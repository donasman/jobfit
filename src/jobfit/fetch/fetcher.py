"""공고 한 건 받기 — output/<공고>/source*.html 과 fetch.json.

실패하면 왜 실패했는지 한국어로 남기고, 다음 할 일은 언제나 "공고 본문 붙여넣기"다.
글자는 바꾸지 않는다. 받은 바이트를 문자로 풀어 UTF-8로 저장할 뿐이다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from jobfit import menunum
from jobfit.fetch import robots
from jobfit.fetch.sites import FetchPlan, plan_for
from jobfit.jobkey import JobKey

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 30
DELAY_SECONDS = 1.0  # 요청 사이 간격

FILE_MAIN = "source.html"
FILE_DETAIL = "source-detail.html"
FILE_PDF = "source.pdf"
FILE_META = "fetch.json"

PASTE_HINT = "공고 본문 붙여넣기 (output 폴더의 posting.txt)"
# 프록시·DNS·SSL·연결 실패 — 사이트가 아니라 실행 환경(에이전트의 가상 머신·샌드박스)의 인터넷이 막힌 경우가 많다
NETWORK_HINT = (
    f"이 PC에서 {menunum.at(menunum.RUN, '공고 처리 진행')}을 한 번 실행해 공고를 받아 오세요"
    f" — 에이전트가 도는 가상 머신·샌드박스는 인터넷이 막혀 있을 수 있습니다. 그래도 안 되면 {menunum.at(menunum.POSTING, '공고 본문 붙여넣기')}"
)
NETWORK_ERROR_MARK = "인터넷 연결 문제로 받지 못했습니다"


@dataclass
class FetchResult:
    ok: bool
    url: str
    status_code: int = 0
    content_type: str = ""
    title: str = ""
    files: dict[str, str] = field(default_factory=dict)  # 역할 -> 파일명
    error: str = ""
    next_hint: str = ""
    notes: list[str] = field(default_factory=list)
    fetched_at: str = ""
    network: bool = False  # 실행 환경의 인터넷 문제로 실패


class _Blocked(Exception):
    pass


def fetch_job(url: str, key: JobKey, job_dir: Path, respect_robots: bool = True) -> FetchResult:
    job_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_for(url, key)
    result = FetchResult(ok=False, url=url, fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
    try:
        main = _get(session, plan.main_url, plan.referer or url, respect_robots)
        result.status_code = main.status_code
        result.content_type = main.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if main.status_code != 200:
            result.error = _status_message(main.status_code)
            result.next_hint = PASTE_HINT
            return _finish(result, job_dir)
        if result.content_type == "application/pdf" or main.content[:5] == b"%PDF-":
            (job_dir / FILE_PDF).write_bytes(main.content)
            result.files["pdf"] = FILE_PDF
            result.error = "공고가 PDF입니다. 이 버전은 PDF를 읽지 않습니다"
            result.next_hint = PASTE_HINT
            return _finish(result, job_dir)

        html = _decode(main)
        (job_dir / FILE_MAIN).write_text(html, encoding="utf-8")
        result.files["main"] = FILE_MAIN
        result.title = _title(html)

        detail_urls = list(plan.detail_urls)
        if plan.follow_iframes and not detail_urls:
            detail_urls = _same_host_iframes(html, plan.main_url)[:1]
        for i, durl in enumerate(detail_urls):
            time.sleep(DELAY_SECONDS)
            try:
                detail = _get(session, durl, plan.main_url, respect_robots)
            except _Blocked:
                result.notes.append(f"상세 페이지는 robots.txt가 막아 받지 않았다: {durl}")
                continue
            if detail.status_code == 200 and "html" in detail.headers.get("Content-Type", "").lower():
                name = FILE_DETAIL if i == 0 else FILE_DETAIL.replace(".html", f"-{i + 1}.html")
                (job_dir / name).write_text(_decode(detail), encoding="utf-8")
                result.files["detail" if i == 0 else f"detail{i + 1}"] = name
            else:
                result.notes.append(f"상세 페이지 응답 {detail.status_code}: {durl}")
        result.ok = True
        result.notes[:0] = list(plan.notes)  # '상세요강을 따로 받았다' 같은 설명은 받았을 때만
    except _Blocked:
        result.error = "사이트가 robots.txt로 자동 수집을 막아 두었습니다"
        result.next_hint = PASTE_HINT
    except requests.ConnectionError as e:  # ProxyError·SSLError·ConnectTimeout·DNS 실패 포함
        result.error = f"{NETWORK_ERROR_MARK}({e.__class__.__name__})"
        result.next_hint = NETWORK_HINT
        result.network = True
    except requests.Timeout:
        result.error = f"{TIMEOUT}초 안에 응답이 없었습니다"
        result.next_hint = PASTE_HINT
    except requests.RequestException as e:
        result.error = f"받지 못했습니다: {e.__class__.__name__}"
        result.next_hint = PASTE_HINT
    return _finish(result, job_dir)


# --- 내부 ---


def _get(session: requests.Session, url: str, referer: str, respect_robots: bool) -> requests.Response:
    if respect_robots and not robots.allowed(url, USER_AGENT):
        raise _Blocked(url)
    headers = {"Referer": referer} if referer else {}
    return session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)


def _decode(resp: requests.Response) -> str:
    """헤더 → meta charset → cp949 → utf-8 순으로 푼다. 사람인·잡코리아는 UTF-8이지만 오래된 사이트는 EUC-KR이다."""
    raw = resp.content
    candidates: list[str] = []
    header_cs = requests.utils.get_encoding_from_headers(resp.headers)
    if header_cs and header_cs.lower() != "iso-8859-1":
        candidates.append(header_cs)
    m = re.search(rb'<meta[^>]+charset=["\']?\s*([A-Za-z0-9_-]+)', raw[:4096], re.I)
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp949"]
    for cs in candidates:
        try:
            return raw.decode(cs if cs.lower() != "euc-kr" else "cp949")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(m.group(1), "html.parser").get_text()).strip()


def _same_host_iframes(html: str, base_url: str) -> list[str]:
    host = urlsplit(base_url).netloc.lower()
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for tag in soup.find_all("iframe"):
        src = tag.get("src") or tag.get("data-src")
        if not src or src.startswith("about:"):
            continue
        full = urljoin(base_url, src)
        if urlsplit(full).netloc.lower() == host:
            urls.append(full)
    return urls


def _status_message(code: int) -> str:
    if code == 403:
        return "403 — 사이트가 접근을 막았습니다"
    if code == 404:
        return "404 — 공고가 내려갔거나 주소가 틀렸습니다"
    if code in (401, 407):
        return f"{code} — 로그인이 필요한 페이지입니다"
    if 500 <= code < 600:
        return f"{code} — 사이트 쪽 오류입니다. 잠시 뒤 다시 시도하세요"
    return f"HTTP {code}"


def _finish(result: FetchResult, job_dir: Path) -> FetchResult:
    (job_dir / FILE_META).write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
