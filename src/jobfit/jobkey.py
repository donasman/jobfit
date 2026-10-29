"""공고키 — 입력(링크, 없으면 회사명·직무·마감일)에서 유도하는 공고의 고유 키.

스크립트는 jobs.xlsx에 쓰지 않으므로 키는 언제나 입력에서 다시 계산할 수 있어야 한다.
링크를 고치면 새 공고가 된다(안내 시트에 적혀 있다).

형식
  <사이트>:<공고번호>   사람인·잡코리아·원티드·점핏·리멤버·프로그래머스·토스
  url:<호스트/경로?남은 값>  모르는 사이트. 추적값과 #뒤를 뺀 정규화 URL
  local:<해시8>         링크가 없을 때. 회사명|직무|마감일 해시
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

# (사이트, 호스트 정규식, 공고번호 정규식 목록) — 먼저 맞는 것을 쓴다
_SITE_RULES: list[tuple[str, str, list[str]]] = [
    # 점핏은 jumpit.saramin.co.kr 로도 열린다 — 사람인 규칙보다 먼저 봐야 한다
    ("jumpit", r"jumpit\.(co\.kr|saramin\.co\.kr)$", [r"/position/(\d+)"]),
    ("jobkorea", r"jobkorea\.co\.kr$", [r"/Recruit/GI_Read/(\d+)", r"[?&]Gno=(\d+)"]),
    ("saramin", r"saramin\.co\.kr$", [r"[?&]rec_idx=(\d+)"]),
    ("wanted", r"wanted\.co\.kr$", [r"/wd/(\d+)"]),
    ("remember", r"(remember|rememberapp)\.co\.kr$", [r"/posting/(\d+)"]),
    ("programmers", r"programmers\.co\.kr$", [r"/job_positions/(\d+)"]),
]

# 사이트 호스트에서 떼는 접두어
_HOST_PREFIX = re.compile(r"^(www|m)\.")

# 추적용 쿼리 값 — 같은 공고를 다른 주소로 보이게 만드는 것들
_TRACKING_EXACT = {"ref", "t_content", "isMypage", "gclid", "fbclid", "yclid", "igshid"}
_TRACKING_PREFIX = ("utm_", "t_ref")


@dataclass(frozen=True)
class JobKey:
    key: str
    site: str  # saramin · jobkorea · wanted · jumpit · remember · programmers · toss · url · local
    number: str = ""  # 사이트 공고번호(있을 때)

    @property
    def has_url(self) -> bool:
        return self.site != "local"


def site_of(url: str) -> str:
    """URL의 사이트 이름. 모르면 'url'."""
    host = _host(url)
    if not host:
        return "url"
    for site, host_re, _ in _SITE_RULES:
        if re.search(host_re, host):
            return site
    if host.endswith("toss.im") or host.endswith("toss.com"):
        return "toss"
    return "url"


def derive(url: str = "", company: str = "", title: str = "", deadline: str = "") -> JobKey:
    url = (url or "").strip()
    if url:
        return _from_url(url)
    seed = "|".join(x.strip() for x in (company, title, deadline))
    return JobKey(key="local:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], site="local")


def normalize_url(url: str) -> str:
    """추적값·#뒤를 빼고 호스트의 www./m.을 뗀 `호스트/경로?남은 값`."""
    parts = urlsplit(url.strip())
    host = _HOST_PREFIX.sub("", parts.netloc.lower())
    path = parts.path.rstrip("/")
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _TRACKING_EXACT and not k.startswith(_TRACKING_PREFIX)
    ]
    query = urlencode(kept)
    return f"{host}{path}" + (f"?{query}" if query else "")


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _from_url(url: str) -> JobKey:
    host = _host(url)
    for site, host_re, number_res in _SITE_RULES:
        if not re.search(host_re, host):
            continue
        for number_re in number_res:
            m = re.search(number_re, url)
            if m:
                return JobKey(key=f"{site}:{m.group(1)}", site=site, number=m.group(1))
    if host.endswith("toss.im") or host.endswith("toss.com"):
        # 토스는 한 job_id에 포지션이 여럿 — 둘을 합쳐야 공고 하나다
        job = re.search(r"[?&]job_id=([^&#]+)", url)
        sub = re.search(r"[?&]sub_position_id=([^&#]+)", url)
        if job and sub:
            number = f"{job.group(1)}-{sub.group(1)}"
            return JobKey(key=f"toss:{number}", site="toss", number=number)
    return JobKey(key="url:" + normalize_url(url), site="url")
