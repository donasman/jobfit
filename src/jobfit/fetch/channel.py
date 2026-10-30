"""지원처 — 이 공고에 어디로 지원하나: 플랫폼 · 홈페이지 · 이메일. 못 읽으면 빈칸(모름).

공고를 받은 곳(게시처, JobKey.kind)과 다를 수 있다. 실측(2026-10):
- 사람인 55145352: 사람인 공고인데 지원방법 칸이 '홈페이지 지원', 실제 지원은 회사 그리팅 페이지.
- 잡코리아 50182629: 잡코리아 공고인데 '이메일 지원시 우선 검토'.
그래서 링크 주소만으로 정하지 않고 받은 페이지를 읽는다. 판정은 정규식이고, 여러 방법이 함께 적혀 있으면
고르지 않고 빈칸으로 둔다 — 추측하지 않는다. 사용자가 jobs.xlsx 지원방법 칸에 적은 값이 언제나 먼저다.
플랫폼 공고의 홈페이지 지원 링크는 따라가 받지 않는다(사용자가 적은 링크 한 건만 받는다). 링크만 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from jobfit import jobkey
from jobfit.clean.sections import split_sections
from jobfit.fetch.fetcher import FILE_DETAIL, FILE_MAIN
from jobfit.state import VIA_BOARD, VIA_COMPANY, VIA_EMAIL

# 지원 방법을 말하는 문장. 버튼 글자만 있는 줄(즉시지원·홈페이지 지원)은 정제 때 메뉴로 보고 뺐다.
# 사이에 낱말 하나둘은 둔다("이메일로 이력서 제출") — 같은 문장 안, 8자까지
_GAP = r"[^.。!?\n]{0,8}?"
_TEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (VIA_EMAIL, re.compile(rf"(이메일|e-?mail|메일){_GAP}(지원|접수|제출|보내)", re.I)),
    (VIA_COMPANY, re.compile(rf"(홈페이지|채용\s*(사이트|페이지)){_GAP}(지원|접수|제출)")),
    (VIA_BOARD, re.compile(rf"즉시\s*지원|(사람인|잡코리아|원티드|점핏|리멤버|프로그래머스){_GAP}(지원|접수)")),
)
# 사람인 요약 페이지의 지원방법 칸(dd.method). 실측은 '홈페이지 지원'뿐 — 나머지 낱말은 같은 칸의 다른 값으로 보고 넣었다
_SARAMIN_CELL: tuple[tuple[str, re.Pattern[str]], ...] = (
    (VIA_EMAIL, re.compile(r"이메일|e-?mail", re.I)),
    (VIA_COMPANY, re.compile(r"홈페이지")),
    (VIA_BOARD, re.compile(r"즉시\s*지원|입사\s*지원")),
)
# 지원 방법만 읽을 곳: 이 헤더 아래 줄과 "접수방법 : …" 줄. 페이지 아래 개인정보 안내문의 '홈페이지 지원 공고의 경우'를 피한다
_METHOD_HEADER = re.compile(r"(접수|지원).{0,6}방법|(접수|지원)\s*안내")
_METHOD_LINE = re.compile(r"^[\s\-•·ㆍ▪*※]*(접수|지원)\s*방법\s*[:：|]\s*(.+)$")
_RE_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_RE_LEAD = re.compile(r"^[\s\-•·ㆍ▪*※|]+")
_EVIDENCE_MAX = 50


@dataclass
class ApplyChannel:
    via: str = ""  # 플랫폼 · 홈페이지 · 이메일 · ""(모름)
    url: str = ""  # 홈페이지 지원일 때 지원하러 갈 곳(알 때만)
    evidence: str = ""  # 무엇을 보고 정했나


def read_channel(url: str, htmls: list[str], text: str) -> ApplyChannel:
    """공고 링크·받은 HTML·걷어낸 글자에서 지원처를 읽는다. url이 없으면(붙여 넣은 공고) 글자만 본다."""
    key = jobkey.derive(url) if url else None
    soups = [BeautifulSoup(h, "html.parser") for h in htmls if h]
    page_host = urlsplit(url).netloc.lower() if url else ""

    if key and key.site == "saramin":
        vias, cell, href = _saramin_cell(soups)
        if len(vias) == 1:
            via = vias.pop()
            link = (href or _anchor_url(soups, page_host)) if via == VIA_COMPANY else ""
            return ApplyChannel(via, link, f"사람인 지원방법 칸 '{cell}'")

    found = _vias_in(_method_lines(text))
    if key and key.kind == jobkey.KIND_COMPANY:
        if set(found) == {VIA_EMAIL}:
            return ApplyChannel(VIA_EMAIL, "", f"공고 글 '{_short(found[VIA_EMAIL])}'")
        return ApplyChannel(VIA_COMPANY, url, "회사 채용 사이트의 공고")
    if len(found) == 1:
        via, line = next(iter(found.items()))
        link = (_line_url(line, page_host) or _anchor_url(soups, page_host)) if via == VIA_COMPANY else ""
        return ApplyChannel(via, link, f"공고 글 '{_short(line)}'")
    if len(found) > 1:
        return ApplyChannel("", "", "여러 방법이 적혀 있습니다: " + "·".join(found))
    return ApplyChannel()


def page_htmls(job_dir: Path) -> list[str]:
    """받은 HTML(요약·상세). 없으면 빈 목록."""
    return [p.read_text(encoding="utf-8", errors="replace") for p in (job_dir / FILE_MAIN, job_dir / FILE_DETAIL) if p.exists()]


def resolve(user_via: str, found: dict) -> ApplyChannel:
    """jobs.xlsx 지원방법 칸(user_via)이 있으면 그것, 없으면 페이지에서 읽은 값(meta.json의 apply)."""
    page = ApplyChannel(**{k: str(v) for k, v in (found or {}).items() if k in ApplyChannel.__dataclass_fields__})
    if not user_via:
        return page
    return ApplyChannel(user_via, page.url if user_via == VIA_COMPANY else "", "jobs.xlsx 지원방법 칸")


# --- 내부 ---


def _saramin_cell(soups) -> tuple[set[str], str, str]:
    for soup in soups:
        dd = soup.select_one("dd.method")
        if dd is None:
            continue
        cell = re.sub(r"\s+", " ", dd.get_text(" ", strip=True))
        vias = {via for via, pat in _SARAMIN_CELL if pat.search(cell)}
        href = next((h for h in (_href(a) for a in dd.find_all("a")) if h), "")
        return vias, cell, href
    return set(), "", ""


def _method_lines(text: str) -> list[str]:
    lines = []
    for sec in split_sections(text):
        if sec.name == "접수방법" and _METHOD_HEADER.search(sec.header):
            lines += [ln for ln in sec.lines if ln.strip()]
    for line in text.splitlines():
        m = _METHOD_LINE.match(line.strip())
        if m:
            lines.append(m.group(2))
    return lines


def _vias_in(lines: list[str]) -> dict[str, str]:
    """지원처 -> 그렇게 본 첫 줄. 규칙 순서(이메일·홈페이지·플랫폼)대로."""
    found: dict[str, str] = {}
    for via, pat in _TEXT_RULES:
        for line in lines:
            if pat.search(line):
                found[via] = line.strip()
                break
    return found


def _href(a) -> str:
    """홈페이지 지원 링크. 사람인은 href="#"에 주소를 data-href로 둔다."""
    for attr in ("data-href", "href"):
        value = (a.get(attr) or "").strip()
        if re.match(r"^https?://", value, re.I):
            return value
    return ""


def _anchor_url(soups, page_host: str) -> str:
    """글자가 '홈페이지 접수'·'홈페이지 지원' 같은 링크 중 공고 사이트 밖으로 가는 첫 주소."""
    company = dict(_TEXT_RULES)[VIA_COMPANY]
    for soup in soups:
        for a in soup.find_all("a"):
            href = _href(a)
            if href and urlsplit(href).netloc.lower() != page_host and company.search(a.get_text(" ", strip=True)):
                return href
    return ""


def _line_url(line: str, page_host: str) -> str:
    for m in _RE_URL.finditer(line):
        if urlsplit(m.group()).netloc.lower() != page_host:
            return m.group()
    return ""


def _short(line: str) -> str:
    line = re.sub(r"\s+", " ", _RE_LEAD.sub("", line)).strip()
    return line if len(line) <= _EVIDENCE_MAX else line[:_EVIDENCE_MAX] + "…"
