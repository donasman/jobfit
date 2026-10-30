"""회사 채용 사이트 — 채용 솔루션 페이지에 박힌 데이터에서 회사명·제목·마감일·본문을 읽는다.

실측(2026-10-30, tests/fixtures_live/company/):
- 그리팅(*.career.greetinghr.com): 본문은 화면 글자에도 있지만 회사명은 __NEXT_DATA__ 의 careerBaseInfo.workspace.name 에만 있다.
  공고는 dehydratedState 안의 openingsInfo(title·dueDate·detail). 마감된 공고는 404.
- 나인하이어(*.ninehire.site/job_posting/…): __NEXT_DATA__ 의 recruitment(companyName·externalTitle·endAt)와 jobPosting.content.
  마감되면 같은 주소가 /invalid 페이지가 되어 본문이 없다 — 회사명·제목·마감일만 남는다.
- 리크루터(*.recruiter.co.kr): 본문을 화면이 뜬 뒤 따로 불러와 받은 HTML에 없다. og:title 이 공고 제목.
  회사명으로 쓸 칸이 없다(author·og:site_name 이 'LX Careers', 'DB그룹'처럼 사이트·그룹 이름) — 읽지 않는다.
채용 솔루션은 공고 주소가 아니라 페이지 내용으로 알아본다 — 회사 도메인을 붙여 쓰는 곳이 있다.
그 밖의 회사 사이트(SK·CJ·LG …)는 규칙이 없다. 본문이 화면 글자에 있으면 일반 규칙으로 읽고, 없으면 붙여넣기.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

GREETING = "그리팅"
NINEHIRE = "나인하이어"
RECRUITER = "리크루터"

CLOSED_MARK = "마감돼 내려간 공고입니다"  # 정제 오류 글의 머리. 다음 할 일 안내가 본문 붙여넣기와 갈린다
CLOSED_HINT = "지원하지 않을 공고면 jobs.xlsx 결정 열을 '지원안함'으로. 본문을 갖고 있으면 공고 본문 붙여넣기"

KST = timezone(timedelta(hours=9))  # 마감 시각은 UTC로 온다(2026-10-28T14:59:59Z = 한국 10-28 23:59)
_RE_NEXT_DATA = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_RE_RECRUITER_CANONICAL = re.compile(r'<link[^>]+rel="canonical"[^>]+href="https?://[\w.-]+\.recruiter\.co\.kr/career/jobs/\d+', re.I)
_RE_OG_TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)


@dataclass
class CompanyPage:
    solution: str  # 그리팅 · 나인하이어 · 리크루터
    company: str = ""
    title: str = ""
    deadline: str = ""  # YYYY-MM-DD, 한국 시각
    body_html: str = ""  # 데이터에 든 공고 본문(HTML 조각) — 화면 글자보다 믿는다. 글자는 바꾸지 않는다
    closed: bool = False  # 마감돼 내려간 공고
    no_body: str = ""  # 본문을 못 주는 이유


def read_company_page(html: str) -> CompanyPage | None:
    """채용 솔루션 페이지면 읽은 값, 아니면 None."""
    data = _next_data(html)
    if data:
        page = _greeting(data) or _ninehire(data)
        if page:
            return page
    return _recruiter(html)


# --- 솔루션별 ---


def _greeting(data: dict) -> CompanyPage | None:
    props = _dig(data, "props", "pageProps") or {}
    base = props.get("careerBaseInfo")
    if not isinstance(base, dict):
        return None
    opening: dict = {}
    for query in _dig(props, "dehydratedState", "queries") or []:
        found = _dig(query, "state", "data", "data", "openingsInfo")
        if isinstance(found, dict):
            opening = found
            break
    body = str(opening.get("detail") or "")
    return CompanyPage(
        GREETING,
        company=str(_dig(base, "workspace", "name") or ""),
        title=str(opening.get("title") or ""),
        deadline=_kst_date(opening.get("dueDate")),
        body_html=body,
        no_body=_no_body_reason(GREETING, body),
    )


def _ninehire(data: dict) -> CompanyPage | None:
    props = _dig(data, "props", "pageProps") or {}
    rec = props.get("recruitment")
    if not isinstance(rec, dict) or "companyName" not in rec:
        return None
    closed = str(data.get("page", "")).endswith("/invalid")
    body = "" if closed else str(_dig(props, "jobPosting", "content") or "")
    return CompanyPage(
        NINEHIRE,
        company=str(rec.get("companyName") or ""),
        title=str(rec.get("externalTitle") or rec.get("title") or ""),
        deadline=_kst_date(rec.get("endAt")),
        body_html=body,
        closed=closed,
        no_body=_no_body_reason(NINEHIRE, body),
    )


def _recruiter(html: str) -> CompanyPage | None:
    # 페이지 자기 주소로 알아본다 — 사람인 공고의 '홈페이지 지원' 링크가 리크루터일 수 있다
    if not _RE_RECRUITER_CANONICAL.search(html):
        return None
    m = _RE_OG_TITLE.search(html)
    return CompanyPage(
        RECRUITER,
        title=_html.unescape(m.group(1)).strip() if m else "",
        no_body="리크루터 채용 페이지는 본문을 화면이 뜬 뒤 따로 불러옵니다",
    )


# --- 공통 ---


def _next_data(html: str) -> dict | None:
    m = _RE_NEXT_DATA.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _kst_date(value) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (dt.astimezone(KST) if dt.tzinfo else dt).date().isoformat()


def _no_body_reason(solution: str, body_html: str) -> str:
    """데이터의 본문 칸을 쓸 수 없는 이유. 글자가 있으면 빈 문자열.

    칸이 비어 있는 것(사이트가 데이터 모양을 바꿨거나 공고가 아직 안 채워짐)과 그림 공고를 가른다.
    """
    if not body_html.strip():
        return f"{solution} 공고의 본문 칸이 비어 있습니다"
    soup = BeautifulSoup(body_html, "html.parser")
    if soup.get_text(strip=True) or not soup.find("img"):
        return ""
    return "공고 본문이 그림입니다"
