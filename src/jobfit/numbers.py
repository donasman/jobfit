"""글 속 숫자 뽑기·정규화 — 초안의 숫자가 경험 카드 facts에 있는 값인지 비교하려고. AI 없음.

같은 값으로 본다: 3천만 = 삼천만 = 30,000,000 = 3,000만. '약'·'여' 같은 어림 표현은 값에 영향이 없다.
숫자로 보지 않는다: 기술 이름 속 숫자(S3, EC2, OAuth2, p95, HTTP/2, Java 17, Spring Boot 3),
날짜(2025년 3월, 2025.03), [확인 필요: …] 안, 줄 머리 번호(1. 2)).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 크기를 나타내는 단위 — facts에 없으면 오류
MAGNITUDE_UNITS = {"%", "ms", "초", "분", "시간", "배", "KB", "MB", "GB", "TB", "원", "건"}
# 세는 단위 — 10 이하면 경고, 넘으면 오류
COUNT_UNITS = {"명", "인", "개", "가지", "곳", "차", "위", "팀", "종", "줄", "회", "개월", "주", "일", "년", "대"}
SMALL_COUNT = 10

_MULT = {"백": 100, "천": 1_000, "만": 10_000, "십만": 100_000, "백만": 1_000_000, "천만": 10_000_000, "억": 100_000_000}
_UNIT_ALIASES = {"퍼센트": "%", "프로": "%", "번": "회", "달": "개월", "밀리초": "ms"}
_UNITS = r"%|퍼센트|프로|ms|밀리초|초|분|시간|개월|주|일|년|배|[KMGT]B|원|건|명|인|회|번|개|가지|곳|차|위|팀|종|줄|대|달"

_NUM = re.compile(
    r"(?<![\d.,])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<mult>천만|백만|십만|만|천|억|백)?"
    rf"\s*(?P<unit>{_UNITS})?(?![A-Za-z])"
)
_KOR_DIGITS = dict(zip("일이삼사오육칠팔구", range(1, 10)))
# 한글 숫자 낱말(삼천만). '3천만'의 '천만'처럼 아라비아 숫자 바로 뒤는 _NUM 몫이라 여기서 잡지 않는다
_KOR_NUM = re.compile(rf"(?<![가-힣\d.,])(?P<kor>[일이삼사오육칠팔구십백천만억]{{2,}})\s*(?P<unit>{_UNITS})")

_DATE = re.compile(
    r"(?P<year>(?:19|20)\d{2})\s*(?:년|[.\-/])\s*(?:\d{1,2}\s*(?:월|[.\-/])?\s*(?:\d{1,2}\s*일)?)?"
    r"|\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"
)
_TECH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z.+#/-]*\d[A-Za-z0-9.+#/-]*")
_CONFIRM = re.compile(r"\[확인\s*필요[^\]]*\]")
_LINE_NUMBER = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)


@dataclass(frozen=True)
class NumToken:
    value: float
    unit: str  # 정규화한 단위, 없으면 ""
    raw: str

    @property
    def is_magnitude(self) -> bool:
        return self.unit in MAGNITUDE_UNITS

    def severity(self) -> str:
        """facts에 없을 때의 등급."""
        if self.is_magnitude:
            return "오류"
        return "경고" if self.value <= SMALL_COUNT else "오류"


@dataclass
class Extracted:
    numbers: list[NumToken] = field(default_factory=list)
    years: list[int] = field(default_factory=list)


def extract(text: str, version_names: list[str] | None = None) -> Extracted:
    """version_names: 'Java'·'Spring Boot'처럼 뒤에 버전 숫자가 붙을 수 있는 이름(키워드 사전의 영문 표기)."""
    s = unicodedata.normalize("NFKC", text)
    s = _mask(s, _CONFIRM)
    s = _mask(s, _LINE_NUMBER)
    if version_names:
        s = _mask(s, _version_pattern(tuple(version_names)))
    s = _mask(s, _TECH)
    out = Extracted()
    for m in _DATE.finditer(s):
        if m.group("year"):
            out.years.append(int(m.group("year")))
    s = _mask(s, _DATE)
    for m in _KOR_NUM.finditer(s):
        value = kor_to_int(m.group("kor"))
        if value:
            out.numbers.append(NumToken(float(value), _unit(m.group("unit")), m.group(0).strip()))
    s = _mask(s, _KOR_NUM)
    for m in _NUM.finditer(s):
        value = float(m.group("num").replace(",", ""))
        if m.group("mult"):
            value *= _MULT[m.group("mult")]
        out.numbers.append(NumToken(value, _unit(m.group("unit")), m.group(0).strip()))
    return out


def same_value(a: NumToken, facts: list[NumToken]) -> bool:
    """facts에 같은 값이 있나. 단위가 같거나 한쪽에 단위가 없으면 값만 본다."""
    for f in facts:
        if abs(f.value - a.value) <= 1e-9 * max(1.0, abs(a.value)) and (f.unit == a.unit or not f.unit or not a.unit):
            return True
    return False


def kor_to_int(s: str) -> int:
    """삼천만 → 30000000. 숫자 낱말이 아니면 0."""
    small = {"십": 10, "백": 100, "천": 1000}
    big = {"만": 10_000, "억": 100_000_000}
    if not any(ch in small or ch in big for ch in s):
        return 0
    total = section = num = 0
    for ch in s:
        if ch in _KOR_DIGITS:
            num = _KOR_DIGITS[ch]
        elif ch in small:
            section += (num or 1) * small[ch]
            num = 0
        elif ch in big:
            section += num
            total += (section or 1) * big[ch]
            section = num = 0
        else:
            return 0
    return total + section + num


def _unit(raw: str | None) -> str:
    if not raw:
        return ""
    u = raw.replace(" ", "")
    return _UNIT_ALIASES.get(u, u)


def _mask(s: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), s)


_version_cache: dict[tuple[str, ...], re.Pattern[str]] = {}


def _version_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    if names not in _version_cache:
        alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        _version_cache[names] = re.compile(rf"(?<![A-Za-z])(?:{alts})\s*v?\d+(?:\.\d+)*", re.I)
    return _version_cache[names]
