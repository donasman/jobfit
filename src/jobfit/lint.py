"""초안 검사(스크립트) — AI가 지침을 어겨도 결과물이 망가지지 않게 여기서 보장한다.

오류(수정 대상): 글자수 초과, 경험 카드 facts에 없는 크기 숫자·큰 숫자, 연락처, 문체 규칙의 금지 표현, 빈 초안
경고: 글자수 90% 미만, facts에 없는 작은 수, 경험 기간과 안 맞는 연도, 문체 통계와 다른 점(샘플 5개 이상일 때만)
집계: [확인 필요: …] 개수
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from jobfit.numbers import NumToken, extract, same_value
from jobfit.style import MIN_SAMPLES_FOR_LINT, SPECIAL_CHARS, split_sentences

NO_LIMIT_RANGE = (500, 1000)  # 글자수 제한이 없을 때 프롬프트는 600~900자를 요청한다
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
_CONFIRM = re.compile(r"\[확인\s*필요[^\]]*\]")
_YEAR = re.compile(r"(?:19|20)\d{2}")


@dataclass
class Issue:
    level: str  # "오류" | "경고"
    kind: str  # length · number · contact · forbidden · year · style · empty
    message: str


@dataclass
class QuestionLint:
    qid: str
    chars: int  # 문항 기준(공백 포함/제외)으로 센 글자수
    chars_with_spaces: int
    chars_without_spaces: int
    limit: int | None
    count_spaces: bool
    confirm_needed: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "오류"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "경고"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["errors"] = len(self.errors)
        d["warnings"] = len(self.warnings)
        return d


def count_chars(text: str, count_spaces: bool) -> int:
    return len(text) if count_spaces else len(re.sub(r"\s", "", text))


def lint_answer(
    qid: str,
    text: str,
    limit: int | None,
    count_spaces: bool,
    facts: list[str],
    allowed_text: str = "",
    periods: list[str] | None = None,
    forbidden: list[str] | None = None,
    stats: dict | None = None,
    version_names: list[str] | None = None,
    unverified_facts: list[str] | None = None,
) -> QuestionLint:
    """allowed_text: 숫자가 들어 있어도 되는 다른 글(문항 원문 등).
    unverified_facts: facts 중 verified가 아닌 카드의 것 — 그 숫자만 쓰면 경고."""
    body = text.strip()
    result = QuestionLint(
        qid=qid,
        chars=count_chars(body, count_spaces),
        chars_with_spaces=len(body),
        chars_without_spaces=count_chars(body, False),
        limit=limit,
        count_spaces=count_spaces,
        confirm_needed=len(_CONFIRM.findall(body)),
    )
    add = lambda level, kind, msg: result.issues.append(Issue(level, kind, msg))  # noqa: E731
    if not body:
        add("오류", "empty", "초안이 비어 있습니다")
        return result

    # 글자수
    basis = "공백 포함" if count_spaces else "공백 제외"
    if limit:
        if result.chars > limit:
            add("오류", "length", f"글자수 {result.chars}자({basis})가 제한 {limit}자를 넘습니다")
        elif result.chars < int(limit * 0.9):
            add("경고", "length", f"글자수 {result.chars}자({basis})가 제한 {limit}자의 90%보다 적습니다")
    elif not NO_LIMIT_RANGE[0] <= result.chars <= NO_LIMIT_RANGE[1]:
        add("경고", "length", f"글자수 {result.chars}자 — 제한이 없을 때 권장 {NO_LIMIT_RANGE[0]}~{NO_LIMIT_RANGE[1]}자 밖입니다")

    # 숫자 ↔ facts
    fact_tokens: list[NumToken] = []
    for f in [*facts, allowed_text]:
        fact_tokens.extend(extract(f, version_names).numbers)
    found = extract(body, version_names)
    unverified = set(unverified_facts or [])
    verified_tokens = [t for f in [*facts, allowed_text] if f not in unverified for t in extract(f, version_names).numbers]
    seen: set[str] = set()
    for tok in found.numbers:
        if tok.raw in seen:
            continue
        seen.add(tok.raw)
        if not same_value(tok, fact_tokens):
            add(tok.severity(), "number", f"'{tok.raw}'는 경험 카드 facts에 없는 숫자입니다")
        elif unverified and not same_value(tok, verified_tokens):
            add("경고", "number", f"'{tok.raw}'는 사실 확인 전(verified: false) 카드의 숫자입니다 — 확인 후 쓰세요")

    # 연도 — 경험 기간·facts에 있는 연도면 통과
    known_years = {int(y) for p in (periods or []) for y in _YEAR.findall(str(p))} | {int(y) for f in facts for y in _YEAR.findall(f)}
    if known_years:
        lo, hi = min(known_years), max(known_years)
        for y in sorted(set(found.years)):
            if not lo <= y <= hi:
                add("경고", "year", f"연도 {y}가 경험 기간({lo}~{hi})과 맞지 않습니다")

    # 연락처
    if _EMAIL.search(body) or _PHONE.search(body):
        add("오류", "contact", "이메일·전화번호가 들어 있습니다")

    # 금지 표현
    for expr in forbidden or []:
        if expr and expr in body:
            add("오류", "forbidden", f"문체 규칙의 피하는 표현 '{expr}'이(가) 있습니다")

    # 문체 통계 — 샘플이 충분할 때만, 경고로
    if stats and stats.get("samples", 0) >= MIN_SAMPLES_FOR_LINT and stats.get("sentences"):
        sentences = split_sentences(body)
        longest = max((len(s) for s in sentences), default=0)
        cap = max(int(stats["sentence_len_max"] * 1.2), int(stats["sentence_len_avg"] * 2))
        if longest > cap:
            add("경고", "style", f"가장 긴 문장이 {longest}자 — 샘플의 긴 문장({stats['sentence_len_max']}자)보다 깁니다")
        commas = sum(s.count(",") for s in sentences) / max(len(sentences), 1)
        avg = stats["commas_per_sentence"]
        if (avg == 0 and commas >= 1) or (avg > 0 and commas > avg * 2):
            add("경고", "style", f"문장당 쉼표 {commas:.1f}개 — 샘플({avg}개)보다 많습니다")
        for ch in SPECIAL_CHARS:
            if stats.get("special", {}).get(ch, 1) == 0 and ch in body:
                add("경고", "style", f"샘플에서 쓰지 않는 기호 '{ch}'를 썼습니다")
    return result
