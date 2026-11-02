"""경험 카드 확인 — 사람이 카드를 보고 '맞다'고 하면 스크립트가 verified: true 로 바꾼다.

전에는 me/experiences.yaml 을 열어 카드 id 를 찾고 verified 를 고쳐야 했다(실측: 처음 쓰는 사람이 불편하다고 했다).
이제 공고에 쓰이는 카드만 읽기 좋게 보여 주고, 맞다고 하면 `jobfit exp verify` 가 고친다.
확인할 때 star 에만 있는 숫자는 facts 로 옮긴다 — 사람이 카드를 보고 맞다고 했으니 그 숫자도 확인한 것이다.
에이전트는 사용자가 맞다고 한 카드만 verify 한다(AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobfit.experiences import (
    CardChange,
    card_title,
    example_ids,
    is_verified,
    load_cards,
    loose_star_numbers,
    update_cards,
)
from jobfit.match import cards_in_use, load_match
from jobfit.numbers import extract, same_value
from jobfit.paths import Project
from jobfit.state import State

_STAR_NAMES = (("situation", "상황"), ("task", "과제"), ("action", "행동"), ("result", "결과"))


@dataclass
class Verified:
    ok: bool
    done: list[tuple[dict, list[str]]] = field(default_factory=list)  # (카드, 안내 줄)
    errors: list[str] = field(default_factory=list)


def needs_confirm(card: dict) -> bool:
    """확인 전이거나, 확인했는데 star 에 facts 밖 숫자가 남은 카드(예전에 손으로 verified 를 바꾼 카드)."""
    return not is_verified(card) or bool(loose_star_numbers(card))


def to_confirm(project: Project, state: State | None = None, everything: bool = False) -> list[dict]:
    """확인할 카드 — 기본은 진행 중인 공고의 리포트·초안에 쓰이는 것만(카드 파일 순서). 예시 카드는 빼고(지울 카드다)."""
    cards, problems = load_cards(project.experiences_file)
    if problems:
        return []
    examples = set(example_ids(cards, project.me_example_dir / "experiences.yaml"))
    pending = [c for c in cards if c.get("id") and str(c.get("id")) not in examples and needs_confirm(c)]
    if everything:
        return pending
    used = in_use(project, state)
    return [c for c in pending if str(c.get("id")) in used]


def in_use(project: Project, state: State | None = None) -> set[str]:
    """진행 중인 공고의 리포트·초안에 쓰이는 카드 id. 매칭한 공고가 없으면 빈 집합."""
    if state is None and project.state_file.exists():
        state = State.load(project.state_file)
    if state is None:
        return set()
    return {cid for job in state.active_jobs() for cid in cards_in_use(load_match(project, job))}


def show_lines(project: Project, card: dict) -> list[str]:
    """사람이 읽는 카드 한 장 — yaml 칸 이름 대신 우리말로."""
    from jobfit.ai import exp_extract  # pipeline ↔ exp_extract 순환을 피하려고 여기서

    lines = [f"「{card_title(card)}」  (id: {card.get('id')})"]
    meta = " · ".join(str(card.get(k)).strip() for k in ("period", "org", "role") if str(card.get(k) or "").strip())
    if meta:
        lines.append(f"  {meta}")
    star = card.get("star") if isinstance(card.get("star"), dict) else {}
    for key, name in _STAR_NAMES:
        value = str(star.get(key) or "").strip()
        if value:
            lines.append(f"  {name}: {value}")
    facts = [str(f) for f in card.get("facts") or []] if isinstance(card.get("facts"), list) else []
    lines.append("  쓸 수 있는 사실(facts):" + ("" if facts else " 없음 — 초안에 숫자를 쓰지 않습니다"))
    lines += [f"    - {f}" for f in facts]
    source = str(card.get("source") or "")
    if source:
        lines.append(f"  뽑은 자료: {source}")
        text = exp_extract.read_source_text(project, source)
        if text is None:
            lines.append("  ! 그림·스캔 자료라 숫자를 원문과 대조하지 못했습니다 — 숫자를 특히 확인하세요")
        else:
            # 예전 버전에서 뽑은 카드는 star 에 원문에 없는 숫자가 남아 있을 수 있다(지금은 받을 때 [확인 필요]로 바꾼다).
            # 맞다고 하면 facts 로 들어가니, Enter 를 누르기 전에 눈에 띄게 따로 짚는다.
            src_nums = extract(text).numbers
            unsourced = [n.raw for k, _ in _STAR_NAMES for n in extract(str(star.get(k) or "")).numbers if not same_value(n, src_nums)]
            if unsourced:
                lines.append(
                    f"  ! 자료 원문에 없는 숫자: {', '.join(dict.fromkeys(unsourced))} — 맞는지 특히 확인하세요(맞다고 하면 facts에 들어갑니다)"
                )
    loose = loose_star_numbers(card)
    if loose:
        lines.append(f"  · 위 내용의 숫자 {', '.join(loose)}는 확인하면 facts에 들어갑니다")
    if is_verified(card) and loose:
        lines.append("  · 이미 확인한 카드입니다 — 다시 확인하면 위 숫자만 facts에 넣습니다")
    return lines


def verify(project: Project, ids: list[str]) -> Verified:
    """카드를 verified: true 로. star 에만 있는 숫자는 facts 로(원문에 있으면 원문 문장, 없으면 그 star 문장)."""
    from jobfit.ai import exp_extract

    cards, problems = load_cards(project.experiences_file)
    if problems:
        return Verified(ok=False, errors=[f"{problems[0]} — me/experiences.yaml 을 고친 뒤 다시"])
    by_id = {str(c.get("id")): c for c in cards if c.get("id")}
    missing = [i for i in ids if i not in by_id]
    if missing:
        return Verified(ok=False, errors=[f"없는 카드 id 입니다: {', '.join(missing)} — jobfit exp show 로 id 를 보세요"])
    changes: dict[str, CardChange] = {}
    done: list[tuple[dict, list[str]]] = []
    for cid in dict.fromkeys(ids):
        card = by_id[cid]
        star = card.get("star") if isinstance(card.get("star"), dict) else {}
        facts = [str(f) for f in card.get("facts") or []] if isinstance(card.get("facts"), list) else []
        source = str(card.get("source") or "")
        g = exp_extract.ground_star(star, facts, exp_extract.read_source_text(project, source) if source else None, vouched=True)
        change = CardChange(verified=None if is_verified(card) else True, facts_add=tuple(g.facts_add))
        if change.verified is None and not change.facts_add:
            done.append((card, ["이미 확인한 카드입니다"]))
            continue
        changes[cid] = change
        done.append((card, g.notes))
    if changes:
        try:
            update_cards(project.experiences_file, changes, project.backups_dir)
        except ValueError as e:
            return Verified(ok=False, errors=[str(e)])
    return Verified(ok=True, done=done)
