"""경험 은행 me/experiences.yaml 읽기·검사.

이 모듈은 스크립트만 쓴다 — AI·에이전트는 이 파일을 직접 읽지 않고, 프롬프트에 골라 넣은 것만 본다.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
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
# 기간: 2025-03 ~ 2025-06 · 2025.03 · 2026 ~ 현재(자료에 월이 없을 때) · 2024.09 – 2025.08
_PERIOD_PART = r"\d{4}(?:[-.]\d{1,2})?"
_PERIOD = re.compile(rf"^{_PERIOD_PART}(?:\s*(?:~|–|—|\s-\s)\s*(?:{_PERIOD_PART}|현재)?)?$")

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
    kind: str = ""  # "unverified" 는 화면에서 한 줄로 묶는다

    def __str__(self) -> str:
        return f"[{self.level}] {self.card}: {self.message}"


CONFIRM_HOW = "메뉴 8 → 3번(경험 카드 확인)에서 카드를 보고 Enter"
UNVERIFIED_NOTE = f"이 카드들의 숫자는 초안에서 [확인 필요]로 비워 둡니다. 공고에 쓰일 때 알려 드리니 그때 {CONFIRM_HOW}"
BACKUP_KEEP = 10
EXAMPLE_NOTE = "me.example 에서 복사된 예시 카드입니다 — 지우세요. 그대로 두면 가짜 경험과 숫자가 매칭·초안에 쓰입니다"


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


def match_tags(card: dict, kd: KeywordDict) -> list[str]:
    """매칭에 쓰는 태그 — 카드 태그에 data/implies.yaml 의 포함 관계를 더한다(Spring Boot → Spring → Java)."""
    return kd.expand(card_tags(card, kd))


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


def card_title(card: dict) -> str:
    """사람에게 보여 줄 이름 — id(exp-baekjoon-study)는 스크립트용이라 제목을 쓴다."""
    return str(card.get("title") or "").strip() or str(card.get("id") or "")


def loose_star_numbers(card: dict) -> list[str]:
    """star 에만 있고 facts 에 없는 숫자(표기 그대로, 중복 없이). 초안에 쓰면 검사에 걸린다."""
    star = card.get("star")
    if not isinstance(star, dict):
        return []
    facts = card.get("facts")
    fact_nums = [n for f in (facts if isinstance(facts, list) else []) for n in extract(str(f)).numbers]
    loose = [n.raw for k in _STAR_KEYS for n in extract(str(star.get(k) or "")).numbers if not same_value(n, fact_nums)]
    return list(dict.fromkeys(loose))


def example_ids(cards: list[dict], example_file: Path) -> list[str]:
    """me.example 에서 그대로 복사된 예시 카드의 id. id와 제목이 둘 다 같아야 예시로 본다(고쳐 쓴 카드는 내 카드)."""
    examples, _ = load_cards(example_file)
    pairs = {(str(c.get("id")), str(c.get("title", "")).strip()) for c in examples if c.get("id")}
    return [str(c.get("id")) for c in cards if (str(c.get("id")), str(c.get("title", "")).strip()) in pairs]


def check_cards(cards: list[dict], kd: KeywordDict, examples: list[str] | tuple = ()) -> list[CardIssue]:
    issues: list[CardIssue] = []
    seen: dict[str, int] = {}
    for i, card in enumerate(cards, start=1):
        name = str(card.get("id") or f"{i}번째 카드")
        add = lambda level, msg, kind="": issues.append(CardIssue(level, name, msg, kind))  # noqa: E731
        if card.get("id") and str(card.get("id")) in examples:
            add("오류", EXAMPLE_NOTE, "example")
        for f in _REQUIRED:
            if card.get(f) in (None, "", []):
                add("오류", f"{f} 칸이 비어 있습니다")
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
                add("경고", f"태그 '{s}' 대신 대표 표기 '{canon}'(으)로 써 주세요")
        usable = card.get("usable_for") or []
        for u in usable if isinstance(usable, list) else [usable]:
            norm = normalize_type(u)
            if norm is None:
                add("오류", f"usable_for 의 '{u}'은(는) {' · '.join(QUESTION_TYPES)} 중 하나여야 합니다")
            elif norm != str(u).strip():
                add("경고", f"usable_for 의 '{u}'은(는) '{norm}'(으)로 읽습니다")
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
                add("경고", f"star에서 비어 있는 칸: {', '.join(empty)}")
            # star 에만 있는 숫자는 초안에 쓰이면 검사에 걸린다 — 카드를 확인할 때 스크립트가 facts 에 넣는다
            loose = loose_star_numbers(card)
            if loose:
                add("경고", f"star에 facts에 없는 숫자가 있습니다: {', '.join(loose)} — 카드를 확인하면(jobfit exp verify) facts에 넣습니다", "loose-star")
        if "verified" not in card:
            add("경고", "verified가 없어 '확인 필요'로 표시됩니다", "unverified")
        elif card.get("verified") is not True:
            add("경고", "verified가 true가 아닙니다 — 이 카드의 숫자는 초안에서 경고로 표시됩니다. 사실을 확인한 뒤 true로", "unverified")
        period = str(card.get("period") or "").strip()
        if period and not _PERIOD.match(period):
            add("경고", f"period 값 '{period}'을(를) 읽을 수 없습니다 (예: 2025-03 ~ 2025-06)")
    return issues


def new_card_text(card_id: str) -> str:
    return CARD_TEMPLATE.format(id=card_id, summary_max=SUMMARY_MAX, types=" · ".join(QUESTION_TYPES))


# --- 카드 고치기: 사람이 손댄 파일이라 다시 쓰지 않고 그 값 자리만 바꾼다 ---


@dataclass
class CardChange:
    verified: bool | None = None
    facts_add: tuple[str, ...] = ()
    star: dict[str, str] | None = None  # 바꿀 star 칸만

    def apply(self, card: dict) -> None:
        if self.verified is not None:
            card["verified"] = self.verified
        if self.facts_add:
            facts = card.get("facts") if isinstance(card.get("facts"), list) else []
            card["facts"] = [*facts, *self.facts_add]
        if self.star:
            card["star"] = {**card["star"], **self.star}


def update_cards(path: Path, changes: dict[str, CardChange], backup_dir: Path) -> None:
    """카드 id → 바꿀 것. 주석·순서·다른 카드의 글자는 그대로 두고 바꿀 값의 자리만 고친다.
    고친 글을 다시 읽어 바꾸려던 값만 달라졌는지 확인하고, 아니면 쓰지 않는다(ValueError)."""
    text = path.read_text(encoding="utf-8")
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        before = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError("experiences.yaml 을 읽을 수 없습니다") from e
    if not isinstance(root, yaml.SequenceNode) or not isinstance(before, list):
        raise ValueError("experiences.yaml 이 카드 목록이 아닙니다")  # noqa: TRY004 — 사람이 고칠 파일 형식 문제. 부르는 쪽이 ValueError 를 잡아 안내한다
    edits: list[tuple[int, int, str]] = []
    for node in root.value:
        if not isinstance(node, yaml.MappingNode):
            continue
        keys = {k.value: (k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode)}
        cid = keys.get("id", (None, None))[1]
        change = changes.get(cid.value) if isinstance(cid, yaml.ScalarNode) else None
        if change is None:
            continue
        if node.flow_style:
            raise ValueError(f"카드 '{cid.value}'가 한 줄 꼴({{…}})이라 고치지 못했습니다")
        indent = node.value[0][0].start_mark.column
        if change.verified is not None:
            edits.append(_set_scalar(text, node, keys, "verified", "true" if change.verified else "false", indent))
        if change.facts_add:
            edits.append(_add_items(text, node, keys, "facts", change.facts_add, indent))
        for sub, value in (change.star or {}).items():
            star = keys.get("star", (None, None))[1]
            sub_node = next((v for k, v in star.value if k.value == sub), None) if isinstance(star, yaml.MappingNode) else None
            if not isinstance(sub_node, yaml.ScalarNode):
                raise ValueError(f"카드 '{cid.value}'의 star.{sub}를 찾지 못했습니다")  # noqa: TRY004
            edits.append((sub_node.start_mark.index, _end(text, sub_node), _scalar(value)))
    new = text
    for start, end, repl in sorted(edits, reverse=True):
        new = new[:start] + repl + new[end:]
    expected = copy.deepcopy(before)
    for card in expected:
        if isinstance(card, dict) and str(card.get("id")) in changes:
            changes[str(card.get("id"))].apply(card)
    try:
        after = yaml.safe_load(new)
    except yaml.YAMLError:
        after = None
    if after != expected:
        raise ValueError("고친 결과가 예상과 달라 experiences.yaml 을 그대로 두었습니다 — 직접 고쳐 주세요")
    backup(path, backup_dir)
    path.write_text(new, encoding="utf-8")


def backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"experiences_{stamp}.yaml"
    n = 1
    while dst.exists():
        dst = backup_dir / f"experiences_{stamp}_{n}.yaml"
        n += 1
    shutil.copy2(path, dst)
    for old in sorted(backup_dir.glob("experiences_*.yaml"))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return dst


def _scalar(v) -> str:
    """한 줄 yaml 값 — 따옴표가 필요하면 붙인다."""
    return yaml.safe_dump([v], allow_unicode=True, default_flow_style=True, width=10_000).strip()[1:-1]


def _end(text: str, node: yaml.Node) -> int:
    """값이 실제로 끝나는 자리. 블록 목록·표의 end_mark 는 뒤따르는 빈 줄·주석까지 먹으므로 마지막 값의 끝으로."""
    if isinstance(node, (yaml.SequenceNode, yaml.MappingNode)) and not node.flow_style and node.value:
        last = node.value[-1]
        return _end(text, last[1] if isinstance(node, yaml.MappingNode) else last)
    end = node.end_mark.index
    while end > node.start_mark.index and text[end - 1] in "\r\n":  # 블록 글(| >)은 줄바꿈까지 먹는다
        end -= 1
    return end


def _set_scalar(text: str, node: yaml.MappingNode, keys: dict, key: str, value: str, indent: int) -> tuple[int, int, str]:
    if key not in keys:
        at = _end(text, node)
        return at, at, f"\n{' ' * indent}{key}: {value}"
    v = keys[key][1]
    if not isinstance(v, yaml.ScalarNode):
        raise ValueError(f"{key} 값이 한 단어가 아닙니다")  # noqa: TRY004
    if v.start_mark.index == v.end_mark.index:  # 'verified:' 처럼 값이 빈 칸 — 콜론 바로 뒤
        return v.start_mark.index, v.start_mark.index, f" {value}"
    return v.start_mark.index, _end(text, v), value


def _add_items(text: str, node: yaml.MappingNode, keys: dict, key: str, items: tuple[str, ...], indent: int) -> tuple[int, int, str]:
    lines = [f"- {_scalar(x)}" for x in items]
    if key not in keys:
        at = _end(text, node)
        pad = " " * (indent + 2)
        return at, at, f"\n{' ' * indent}{key}:" + "".join(f"\n{pad}{ln}" for ln in lines)
    v = keys[key][1]
    if isinstance(v, yaml.SequenceNode) and not v.flow_style and v.value:
        at = _end(text, v)  # 블록 목록 — 마지막 항목 뒤에 같은 들여쓰기로 덧붙인다(앞 항목·주석은 그대로)
        return at, at, "".join(f"\n{' ' * v.start_mark.column}{ln}" for ln in lines)
    pad = " " * (indent + 2)
    old = [text[x.start_mark.index:_end(text, x)] for x in v.value] if isinstance(v, yaml.SequenceNode) else []  # 원래 표기 그대로
    if isinstance(v, yaml.ScalarNode) and v.value:
        raise ValueError(f"{key} 가 목록이 아닙니다")
    body = "".join(f"\n{pad}- {x}" for x in old) + "".join(f"\n{pad}{ln}" for ln in lines)
    if v.start_mark.index == v.end_mark.index:  # 'facts:' 처럼 값이 빈 칸
        return v.start_mark.index, v.start_mark.index, body
    return v.start_mark.index - _space_before(text, v.start_mark.index), _end(text, v), body


def _space_before(text: str, i: int) -> int:
    n = 0
    while i - n - 1 >= 0 and text[i - n - 1] == " ":
        n += 1
    return n
