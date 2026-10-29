"""경험 은행 me/experiences.yaml 읽기·검사.

이 모듈은 스크립트만 쓴다 — AI·에이전트는 이 파일을 직접 읽지 않고, 프롬프트에 골라 넣은 것만 본다.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from jobfit.clean.keywords import KeywordDict
from jobfit.numbers import extract, same_value

# 문항 유형. 카드의 usable_for와 문항 분류(match.py)가 같은 이름을 쓴다
QUESTION_TYPES = ("지원동기", "직무역량", "협업", "문제해결", "성장", "기타")
_TYPE_ALIASES = {
    "기술역량": "직무역량", "역량": "직무역량", "전문성": "직무역량",
    "팀워크": "협업", "커뮤니케이션": "협업", "소통": "협업",
    "성장과정": "성장", "학습": "성장",
    "입사후포부": "지원동기", "포부": "지원동기",
}
SUMMARY_MAX = 60
_REQUIRED = ("id", "title", "tags", "summary", "usable_for")
_STAR_KEYS = ("situation", "task", "action", "result")
_PERIOD = re.compile(r"^\d{4}[-.]\d{1,2}(\s*~\s*(\d{4}[-.]\d{1,2}|현재)?)?$")

CARD_TEMPLATE = """
- id: {id}          # 영문·숫자·하이픈. 카드마다 다르게
  title: ""
  period: ""                # 2025-03 ~ 2025-06
  org: ""
  role: ""
  tags: []                  # data/synonyms.yaml 의 대표 표기 (예: Java, Spring Boot, 협업)
  summary: ""               # {summary_max}자 이내
  star:
    situation: ""
    task: ""
    action: ""
    result: ""
  facts: []                 # 확정한 수치·사실만. 초안에 쓰이는 숫자는 전부 여기 있어야 한다
  usable_for: []            # {types}
  verified: false
"""

FILE_HEADER = """# 경험 은행 — 자소서·이력서에 쓸 경험을 카드 하나씩. `jobfit exp check` 로 검사한다.
"""


@dataclass
class CardIssue:
    level: str  # "오류" | "경고"
    card: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.card}: {self.message}"


def load_cards(path: Path) -> tuple[list[dict], list[str]]:
    """(카드 목록, 문제). 파일이 없으면 빈 목록. 깨진 yaml이면 빈 목록과 문제 한 줄."""
    if not path.exists():
        return [], []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" ({mark.line + 1}행)" if mark else ""
        return [], [f"experiences.yaml을 읽을 수 없습니다{where}"]
    if not isinstance(data, list):
        return [], ["experiences.yaml은 카드 목록(- id: …)이어야 합니다"]
    return [c for c in data if isinstance(c, dict)], []


def file_sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else ""


def tag_list(cards: list[dict], kd: KeywordDict) -> list[str]:
    """카드 태그를 대표 표기로 정규화해 많이 쓰인 순으로."""
    counts: Counter[str] = Counter()
    for card in cards:
        for t in card_tags(card, kd):
            counts[t] += 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def card_tags(card: dict, kd: KeywordDict) -> list[str]:
    tags = card.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return list(dict.fromkeys(kd.normalize(str(t).strip()) for t in tags if str(t).strip()))


def normalize_type(value) -> str | None:
    s = re.sub(r"\s+", "", str(value))
    if s in QUESTION_TYPES:
        return s
    return _TYPE_ALIASES.get(s)


def card_types(card: dict) -> list[str]:
    raw = card.get("usable_for") or []
    if isinstance(raw, str):
        raw = [raw]
    return [t for t in (normalize_type(x) for x in raw) if t]


def is_verified(card: dict) -> bool:
    return card.get("verified") is True


def check_cards(cards: list[dict], kd: KeywordDict) -> list[CardIssue]:
    issues: list[CardIssue] = []
    seen: dict[str, int] = {}
    for i, card in enumerate(cards, start=1):
        name = str(card.get("id") or f"{i}번째 카드")
        add = lambda level, msg: issues.append(CardIssue(level, name, msg))  # noqa: E731
        for f in _REQUIRED:
            if card.get(f) in (None, "", []):
                add("오류", f"{f}가 비어 있습니다")
        cid = str(card.get("id") or "")
        if cid:
            if cid in seen:
                add("오류", f"id가 {seen[cid]}번째 카드와 같습니다")
            seen.setdefault(cid, i)
        summary = str(card.get("summary") or "")
        if len(summary) > SUMMARY_MAX:
            add("경고", f"summary가 {len(summary)}자입니다({SUMMARY_MAX}자 이내)")
        tags = card.get("tags") or []
        for t in tags if isinstance(tags, list) else [tags]:
            s = str(t).strip()
            canon = kd.normalize(s)
            if s and canon != s:
                add("경고", f"태그 '{s}'는 대표 표기 '{canon}'로 써 주세요")
        usable = card.get("usable_for") or []
        for u in usable if isinstance(usable, list) else [usable]:
            norm = normalize_type(u)
            if norm is None:
                add("오류", f"usable_for '{u}'는 {' · '.join(QUESTION_TYPES)} 중 하나여야 합니다")
            elif norm != str(u).strip():
                add("경고", f"usable_for '{u}'는 '{norm}'(으)로 읽습니다")
        facts = card.get("facts")
        if not facts:
            add("경고", "facts가 없습니다 — 이 카드로 쓴 초안에는 수치를 쓸 수 없습니다")
        elif not isinstance(facts, list) or not all(isinstance(x, (str, int, float)) for x in facts):
            add("오류", "facts는 문장 목록이어야 합니다")
        star = card.get("star")
        if not isinstance(star, dict):
            add("경고", "star(situation·task·action·result)가 없습니다 — 초안의 재료가 됩니다")
        else:
            empty = [k for k in _STAR_KEYS if not str(star.get(k) or "").strip()]
            if empty:
                add("경고", f"star의 {', '.join(empty)}가 비어 있습니다")
            # star 에만 있는 숫자는 초안에 쓰이면 검사에 걸린다 — facts 로 옮기거나 지우게 알린다
            fact_nums = [n for f in (facts if isinstance(facts, list) else []) for n in extract(str(f)).numbers]
            loose = [n.raw for k in _STAR_KEYS for n in extract(str(star.get(k) or "")).numbers if not same_value(n, fact_nums)]
            if loose:
                add("경고", f"star의 숫자 {', '.join(dict.fromkeys(loose))}가 facts에 없습니다 — 초안에 쓰면 검사에 걸립니다")
        if "verified" not in card:
            add("경고", "verified가 없어 '확인 필요'로 표시됩니다")
        elif card.get("verified") is not True:
            add("경고", "verified가 true가 아닙니다 — 이 카드의 숫자는 초안에서 경고로 표시됩니다. 사실을 확인한 뒤 true로")
        period = str(card.get("period") or "").strip()
        if period and not _PERIOD.match(period):
            add("경고", f"period '{period}'를 읽을 수 없습니다 (예: 2025-03 ~ 2025-06)")
    return issues


def new_card_text(card_id: str) -> str:
    return CARD_TEMPLATE.format(id=card_id, summary_max=SUMMARY_MAX, types=" · ".join(QUESTION_TYPES))
