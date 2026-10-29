"""마감일 읽기 — 엑셀 날짜, 여러 꼴의 문자열, '상시' 같은 날짜 아닌 표기."""

from __future__ import annotations

import re
from datetime import date, datetime

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"),
    re.compile(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?"),
)
_NO_DEADLINE = re.compile(r"상시|채용\s*시|수시|미정|협의")
_WS = re.compile(r"[\s　 ]+")


def parse_deadline(value) -> tuple[str, str, str]:
    """(YYYY-MM-DD, 날짜가 아닌 입력 원문, 오류 메시지). 셋 중 하나만 채워진다(빈 입력이면 셋 다 빈칸)."""
    if value is None or value == "":
        return "", "", ""
    if isinstance(value, datetime):
        return value.date().isoformat(), "", ""
    if isinstance(value, date):
        return value.isoformat(), "", ""
    if isinstance(value, (int, float)):
        # 엑셀 일련번호가 날짜 서식 없이 들어온 경우
        try:
            from openpyxl.utils.datetime import from_excel

            return from_excel(value).date().isoformat(), "", ""
        except Exception:  # noqa: BLE001
            return "", "", f"마감일을 날짜로 읽을 수 없습니다: {value}"
    s = _WS.sub(" ", str(value)).strip()
    for pat in _DATE_PATTERNS:
        m = pat.match(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d).isoformat(), "", ""
            except ValueError:
                return "", "", f"마감일이 존재하지 않는 날짜입니다: {s}"
    if _NO_DEADLINE.search(s):
        return "", s, ""
    return "", "", f"마감일 형식을 알 수 없습니다: {s} (예: 2026-11-30)"
