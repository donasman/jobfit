"""robots.txt 확인 — 사이트가 자동 수집을 막아 둔 경로는 받지 않고 붙여넣기를 안내한다."""

from __future__ import annotations

from urllib import robotparser
from urllib.parse import urlsplit

import requests

_cache: dict[str, robotparser.RobotFileParser | None] = {}


def allowed(url: str, user_agent: str, timeout: float = 10.0) -> bool:
    """robots.txt가 없거나 읽을 수 없으면 허용으로 본다."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _cache:
        _cache[origin] = _load(origin, user_agent, timeout)
    rp = _cache[origin]
    if rp is None:
        return True
    return rp.can_fetch(user_agent, url)


def _load(origin: str, user_agent: str, timeout: float) -> robotparser.RobotFileParser | None:
    try:
        resp = requests.get(f"{origin}/robots.txt", headers={"User-Agent": user_agent}, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or not resp.text.strip():
        return None
    rp = robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp
