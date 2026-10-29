"""글 속 숫자 뽑기·정규화 — 초안의 숫자가 경험 카드 facts에 있는 값인지 비교하려고. AI 없음.

같은 값으로 본다: 3천만 = 삼천만 = 30,000,000 = 3,000만, 4만 3천 = 43,000, 41k = 41,000. '약'·'여' 같은 어림 표현은 값에 영향이 없다.
범위는 단위를 나눠 갖는다: 45~78% → 45%, 78%.
숫자로 보지 않는다(값이 아니라 이름·표기인 것):
- 기술 이름 속 숫자(S3, EC2, OAuth2, p95, HTTP/2), 버전(Java 17, PostgreSQL 16, Spring Boot 2.6, Gemini 3.5)
- 날짜(2025년 3월, 2025.03, 2026.09.17, 2026-10-28, 09-15), 연도만(2026 – 현재), 시각(04:00)
- 관계 표기(1:N, N:M, 1:1), HTTP 상태 코드(429 오류, 응답이 200, HTTP 500)
- [확인 필요: …] 안, 줄 머리 번호(1. 2))
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# 크기를 나타내는 단위 — facts에 없으면 오류
MAGNITUDE_UNITS = {"%", "ms", "초", "분", "시간", "배", "KB", "MB", "GB", "TB", "원", "건", "t", "kg"}
# 세는 단위 — 10 이하면 경고, 넘으면 오류
COUNT_UNITS = {"명", "인", "개", "가지", "곳", "차", "위", "팀", "종", "줄", "회", "개월", "주", "일", "년", "대"}
SMALL_COUNT = 10

_MULT = {"백": 100, "천": 1_000, "만": 10_000, "십만": 100_000, "백만": 1_000_000, "천만": 10_000_000, "억": 100_000_000, "k": 1_000}
_UNIT_ALIASES = {"퍼센트": "%", "프로": "%", "번": "회", "달": "개월", "밀리초": "ms", "톤": "t"}
_UNITS = r"%|퍼센트|프로|ms|밀리초|초|분|시간|개월|주|일|년|배|[KMGT]B|원|건|명|인|회|번|개|가지|곳|차|위|팀|종|줄|대|달|톤|kg|t"
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MULTS = r"천만|백만|십만|만|천|억|백"
# 단위 낱말 바로 뒤에 다른 한글이 오면 단위가 아니다: '500 백엔드'의 백, '3만큼'의 만. 단위·'여'(5천여 명)는 괜찮다
_MULT_OK = rf"(?:{_MULTS})(?!(?!(?:{_UNITS}|여))[가-힣])"
# 단위 뒤에 한글이 이어지면 조사·접미사일 때만 단위다: '3년간' '5명의' '60t를'은 단위, '429 원인' '6대회' '2 인증'은 아니다
_PARTICLE_CHARS = "은는이가을를의에로으과와도만간째치당씩여쯤부까동며고나후전내량정보짜차께랑"
_UNIT = rf"(?P<unit>{_UNITS})(?![가-힣](?<![{_PARTICLE_CHARS}]))"
# 뒤에 단위가 오는지(버전·연도·상태 코드가 아니라 수량인지) 볼 때
_UNIT_AHEAD = rf"\s*(?:(?:{_UNITS})(?![가-힣](?<![{_PARTICLE_CHARS}]))|{_MULT_OK})"

_NUM = re.compile(
    rf"(?<![\d.,])(?P<num>{_NUMBER})(?![\d])"  # (?![\d]) — '141k'를 '14'로 잘라 읽지 않게
    rf"(?:\s*(?P<mult>{_MULT_OK})|(?P<k>[kK]))?"
    rf"(?:\s*{_UNIT})?(?![A-Za-z])"
)
# 범위: 45~78%, 3~6회 — 뒤 숫자의 단위를 앞 숫자도 갖는다
_RANGE = re.compile(
    rf"(?<![\d.,])(?P<a>{_NUMBER})\s*[~–]\s*(?P<b>{_NUMBER})(?![\d])"
    rf"(?:\s*(?P<mult>{_MULT_OK})|(?P<k>[kK]))?(?:\s*{_UNIT})?(?![A-Za-z])"
)
# 섞인 한글 숫자: 4만 3천, 1억 2천만, 3만 5000 원
_MIXED = re.compile(
    rf"(?<![\d.,])(?P<parts>(?:\d+\s*{_MULT_OK}\s*){{2,}}(?:\d+(?![\d.,]))?|(?:\d+\s*(?:억|만)(?![가-힣])\s*)\d+(?![\d.,]))"
    rf"(?:\s*{_UNIT})?(?![A-Za-z])"
)
_KOR_DIGITS = dict(zip("일이삼사오육칠팔구", range(1, 10)))
# 한글 숫자 낱말(삼천만). '3천만'의 '천만'처럼 아라비아 숫자 바로 뒤는 _NUM 몫이라 여기서 잡지 않는다
_KOR_NUM = re.compile(rf"(?<![가-힣\d.,])(?P<kor>[일이삼사오육칠팔구십백천만억]{{2,}})\s*{_UNIT}")

_YEAR = r"(?:19|20)\d{2}"
_DATE = re.compile(
    rf"(?<![\d.,])(?P<y1>{_YEAR})\s*[.\-/]\s*\d{{1,2}}(?:\s*[.\-/]\s*\d{{1,2}})?(?![\d])"  # 2025.03 · 2026.09.17 · 2026-10-28
    rf"|(?<![\d.,])(?P<y2>{_YEAR})\s*년(?:\s*\d{{1,2}}\s*월(?:\s*\d{{1,2}}\s*일)?)?"  # 2025년 · 2025년 3월 5일 ('2025년 3개월'의 3은 남긴다)
    r"|(?<![\d.,])\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"  # 3월 · 3월 5일
    r"|(?<![\d.\-/])(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?![\d\-])"  # 09-15 (앞자리 0이 있는 월-일만)
    rf"|(?<![\d.,])(?P<y3>19[89]\d|20[0-3]\d)(?![\d.,]|{_UNIT_AHEAD})"  # 단위 없이 홀로 쓴 연도: 2026 – 현재
)
_TIME = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?(?![\d:])")
_RELATION = re.compile(r"(?<![\w])(?:1|N|M|n|m):(?:1|N|M|n|m)(?![\w])")
_HTTP_CODES = "200|201|202|204|301|302|304|400|401|403|404|405|408|409|410|413|415|422|429|500|501|502|503|504"
_HTTP_WORDS = re.compile(r"HTTP|status|상태\s*코드|응답|오류|에러|error|[1-5]xx|Too Many|Rate Limit", re.I)
_HTTP_CODE = re.compile(rf"(?<![\d.,])(?:{_HTTP_CODES})(?![\d.,]|{_UNIT_AHEAD})")
# 문장에 오류·응답 같은 말이 없어도 조사가 바로 붙으면 코드다: '429는', '429의', '500을 막음'
_HTTP_CODE_PARTICLE = re.compile(rf"(?<![\d.,])(?:{_HTTP_CODES})(?=(?:은|는|이|가|의|에|에는|에서|을|를|로|도|만|이어도|일 때)(?![가-힣]{{2}}))")
# 문맥 없이도 이름인 것: 429(요청 과다 코드), 앞자리 0인 두 자리(단계 번호 01 · 04)
_ALWAYS_LABEL = re.compile(rf"(?<![\d.,])(?:429|0\d)(?![\d.,]|{_UNIT_AHEAD})")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_TECH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z.+#/-]*\d[A-Za-z0-9.+#/-]*")
_CONFIRM = re.compile(r"\[확인\s*필요[^\]]*\]")
_LINE_NUMBER = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)

# 뒤에 버전 숫자가 붙는 이름. 키워드 사전(data/keywords.txt·synonyms.yaml)의 영문 표기에 더해 쓴다
EXTRA_VERSION_NAMES = (
    "JDK", "OpenJDK", "JRE", "Prisma", "Gemini", "GPT", "Claude", "Llama", "Gemma", "Mistral", "Qwen", "DeepSeek",
    "Ubuntu", "Debian", "CentOS", "Rocky Linux", "Amazon Linux", "Windows", "Windows Server", "macOS", "iOS", "Android",
    "Vue", "Angular", "Svelte", "Nuxt", "Next.js", "React", "React Native", "Node", "Node.js", "Deno", "Bun",
    "Python", "Java", "Kotlin", "Swift", "Go", "Rust", "PHP", "Ruby", "Rails", "Scala", "Dart", "Flutter", "C#", ".NET",
    "Spring", "Spring Boot", "Spring Batch", "Spring Security", "Hibernate", "MyBatis", "Django", "Flask", "FastAPI", "Express", "NestJS",
    "PostgreSQL", "MySQL", "MariaDB", "Oracle", "MongoDB", "Redis", "Elasticsearch", "Kafka", "SQLite", "SQL Server",
    "Tomcat", "Nginx", "Apache", "Jenkins", "Docker", "Kubernetes", "Terraform", "Gradle", "Maven", "Webpack", "Vite",
    "TypeScript", "JavaScript", "ECMAScript", "ES", "HTML", "CSS", "Bootstrap", "Tailwind", "Jest", "Vitest", "JUnit",
    "TensorFlow", "PyTorch", "CUDA", "Unity", "Unreal", "Electron", "Excel", "Office", "TLS", "SSL", "OAuth", "HTTP", "IPv",
)


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
    """version_names: 'Java'·'Spring Boot'처럼 뒤에 버전 숫자가 붙을 수 있는 이름. 기본 목록에 더해 쓴다."""
    s = unicodedata.normalize("NFKC", text)
    s = _mask(s, _CONFIRM)
    s = _mask(s, _LINE_NUMBER)
    s = _mask(s, _version_pattern(tuple(sorted({*default_version_names(), *(version_names or [])}))))
    s = _mask(s, _TECH)
    out = Extracted()
    for m in _DATE.finditer(s):
        year = m.group("y1") or m.group("y2") or m.group("y3")
        if year:
            out.years.append(int(year))
    s = _mask(s, _DATE)
    s = _mask(s, _TIME)
    s = _mask(s, _RELATION)
    s = _mask_http_codes(s)
    for m in _MIXED.finditer(s):
        value = _mixed_value(m.group("parts"))
        if value:
            out.numbers.append(NumToken(float(value), _unit(m.group("unit")), m.group(0).strip()))
    s = _mask(s, _MIXED)
    for m in _KOR_NUM.finditer(s):
        value = kor_to_int(m.group("kor"))
        if value:
            out.numbers.append(NumToken(float(value), _unit(m.group("unit")), m.group(0).strip()))
    s = _mask(s, _KOR_NUM)
    for m in _RANGE.finditer(s):
        mult = _mult(m)
        unit = _unit(m.group("unit"))
        for part in ("a", "b"):
            raw = m.group(part)
            out.numbers.append(NumToken(float(raw.replace(",", "")) * mult, unit, f"{raw}{m.group('unit') or ''}"))
    s = _mask(s, _RANGE)
    for m in _NUM.finditer(s):
        value = float(m.group("num").replace(",", "")) * _mult(m)
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


@lru_cache(maxsize=1)
def default_version_names() -> tuple[str, ...]:
    """키워드 사전의 영문 표기 + EXTRA_VERSION_NAMES. 사전을 못 읽으면 EXTRA만."""
    names = set(EXTRA_VERSION_NAMES)
    try:
        from jobfit.clean.keywords import KeywordDict
        from jobfit.paths import package_data_dir

        kd = KeywordDict.load(package_data_dir())
        names |= {v for alts in kd.variants.values() for v in alts if re.fullmatch(r"[A-Za-z.][A-Za-z0-9.+# -]*", v)}
    except Exception:  # 사전이 깨져도 숫자 읽기는 돌아야 한다
        pass
    return tuple(sorted(names))


def _mixed_value(parts: str) -> int:
    """'4만 3천' → 43000, '1억 2천만' → 120000000, '3만 5000' → 35000. 큰 단위가 앞에 와야 한다."""
    total = section = 0
    last_big = None
    for num, mult in re.findall(r"(\d+)\s*(억|천만|백만|십만|만|천|백)?", parts):
        n = int(num)
        if mult in ("억", "만"):  # 앞에 모인 천·백 자리까지 이 단위로 묶는다
            big = _MULT[mult]
            if last_big is not None and big >= last_big:
                return 0  # '3만 2억'처럼 거꾸로면 한 수가 아니다
            total += (section + n) * big
            section, last_big = 0, big
        elif mult:  # 천·백, 그리고 천만·백만·십만(그 자체로 완결된 값)
            if mult in ("천만", "백만", "십만"):
                total += n * _MULT[mult]
            else:
                section += n * _MULT[mult]
        else:
            section += n
    return total + section


def _mult(m: re.Match[str]) -> int:
    if m.group("k"):
        return 1_000
    return _MULT[m.group("mult")] if m.group("mult") else 1


def _mask_http_codes(s: str) -> str:
    """HTTP 상태 코드 — 오류·응답·HTTP 같은 말이 있는 문장 안의 단위 없는 200·429·500 등만 가린다."""
    def one(sentence: re.Match[str]) -> str:
        text = sentence.group(0)
        return _mask(text, _HTTP_CODE) if _HTTP_WORDS.search(text) else text

    return _mask(_mask(_SENTENCE.sub(one, s), _HTTP_CODE_PARTICLE), _ALWAYS_LABEL)


def _unit(raw: str | None) -> str:
    if not raw:
        return ""
    u = raw.replace(" ", "")
    return _UNIT_ALIASES.get(u, u)


def _mask(s: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), s)


@lru_cache(maxsize=64)
def _version_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    """이름 + 버전 숫자. 뒤에 단위가 붙으면 버전이 아니다('Java 3년' 의 3은 남긴다)."""
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{alts})\s*v?\d+(?:\.\d+)*(?![\d.]|{_UNIT_AHEAD})", re.I)
