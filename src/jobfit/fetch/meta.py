"""공고 페이지에서 회사명·직무·마감일 읽기 — `jobfit add`가 링크만 받았을 때 빈칸을 채운다.

순서: JSON-LD JobPosting(잡코리아 등) → 채용 솔루션 페이지에 박힌 데이터(그리팅·나인하이어·리크루터, fetch/company.py)
→ 사이트별 제목 규칙(사람인·잡코리아 og:title) → 못 읽음.
읽은 값은 사용자가 준 값을 덮어쓰지 않는다. 못 읽으면 빈칸으로 둔다 — 추측하지 않는다.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass

from jobfit.clean.html2text import html_to_text
from jobfit.clean.sections import summary_fields
from jobfit.dates import parse_deadline
from jobfit.fetch.company import read_company_page

_ZW = re.compile(r"[​-‍⁠﻿]")  # 사람인 제목에 폭 없는 공백이 섞여 온다
_CORP = re.compile(r"㈜|\(\s*주\s*\)|\(\s*유\s*\)|주식회사|유한회사")
_PAREN_LATIN = re.compile(r"\s*\([A-Za-z0-9 .&,'’-]+\)\s*")  # ㈜예시컴퍼니(example) 의 (example)
_LEADING_TAGS = re.compile(r"^(\s*\[[^\]]*\]\s*)+")  # [시리즈A투자유치]
_DDAY = re.compile(r"\s*\((D-\d+|D-day|오늘\s*마감|마감\s*임박)\)", re.I)
_SITE_SUFFIX = re.compile(r"\s*[-|–—]\s*(사람인|잡코리아|점핏|원티드|리멤버|프로그래머스|Wanted|JobKorea|Saramin)\s*$", re.I)
_RE_JSON_LD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_RE_OG_TITLE = re.compile(r"<meta[^>]+property=[\"']og:title[\"'][^>]*content=[\"']([^\"']+)[\"']", re.I)
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


@dataclass
class PostingMeta:
    company: str = ""
    title: str = ""
    deadline: str = ""  # YYYY-MM-DD
    source: str = ""  # "json-ld" | "embedded" | "title" | ""


def read_posting_meta(html: str) -> PostingMeta:
    html = _ZW.sub("", html)
    meta = _from_json_ld(html) or _from_company_page(html) or _from_page_title(html) or PostingMeta()
    if not meta.deadline:
        meta.deadline = _deadline_from_summary(html)
    return meta


def clean_company(name: str) -> str:
    s = _CORP.sub("", _ZW.sub("", name))
    s = _PAREN_LATIN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_job_title(title: str) -> str:
    s = _SITE_SUFFIX.sub("", _ZW.sub("", title))
    s = _DDAY.sub("", s)
    s = _LEADING_TAGS.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


# --- 내부 ---


def _from_json_ld(html: str) -> PostingMeta | None:
    for m in _RE_JSON_LD.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for item in _walk(data):
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" not in types:
                continue
            org = item.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else str(org)
            deadline = ""
            valid = str(item.get("validThrough") or "")
            if re.match(r"\d{4}-\d{2}-\d{2}", valid):
                deadline = valid[:10]
            meta = PostingMeta(company=clean_company(_html.unescape(company)), title=clean_job_title(_html.unescape(str(item.get("title") or ""))), deadline=deadline, source="json-ld")
            if meta.company:
                return meta
    return None


def _from_company_page(html: str) -> PostingMeta | None:
    page = read_company_page(html)
    if page is None or not (page.company or page.title):
        return None
    return PostingMeta(company=clean_company(page.company), title=clean_job_title(page.title), deadline=page.deadline, source="embedded")


def _walk(data):
    if isinstance(data, list):
        for x in data:
            yield from _walk(x)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _walk(data["@graph"])


def _from_page_title(html: str) -> PostingMeta | None:
    m = _RE_OG_TITLE.search(html) or _RE_TITLE.search(html)
    if not m:
        return None
    raw = _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    site = _SITE_SUFFIX.search(raw)
    if not site:
        return None  # 모르는 사이트의 제목은 회사·직무 경계를 알 수 없다
    body = _SITE_SUFFIX.sub("", raw).strip()
    name = site.group(1)
    # 사람인·점핏: "[(주)예시랩] 백엔드 (Backend) 개발자(D-7)"
    bm = re.match(r"^\[([^\]]+)\]\s*(.+)$", body)
    if bm and name in ("사람인", "점핏", "Saramin"):
        return PostingMeta(company=clean_company(bm.group(1)), title=clean_job_title(bm.group(2)), source="title")
    # 잡코리아: "㈜예시컴퍼니(example) 채용 - [시리즈A투자유치] Node.js개발자(신입)"
    jm = re.match(r"^(.+?)\s*채용\s*[-–—]\s*(.+)$", body)
    if jm:
        return PostingMeta(company=clean_company(jm.group(1)), title=clean_job_title(jm.group(2)), source="title")
    return None


def _deadline_from_summary(html: str) -> str:
    fields = summary_fields(html_to_text(html))
    for key in ("마감일", "접수마감", "접수기간"):
        if key in fields:
            value = fields[key]
            # "2026.10.01 ~ 2026.10.27" 이면 뒤 날짜
            if "~" in value:
                value = value.split("~")[-1]
            deadline, _, _ = parse_deadline(value.strip())
            if deadline:
                return deadline
    return ""
