"""사용자에게 꼭 닿아야 하는 안내 — 사람만 할 수 있는 일(me/·jobs.xlsx·sources/ 고치기)을 한 묶음으로.

에이전트는 me/·jobs.xlsx를 고치지 않으므로, 이 안내가 요약 중에 빠지면 아무도 처리하지 않는다.
그래서 경고 줄 사이에 섞지 않고 next·run 끝과 경험 카드 뽑기가 끝났을 때 따로 낸다.
머리줄에 지시를 적어 AGENTS.md를 읽지 않은 에이전트(예: 데스크톱 앱의 가상 머신 작업 모드)도 보게 한다.
"""

from __future__ import annotations

from jobfit.clean.keywords import KeywordDict
from jobfit.experiences import check_cards, example_ids, load_cards
from jobfit.paths import Project
from jobfit.state import State

HEADER = "[사용자에게 전할 것] 에이전트는 아래 줄을 요약하지 말고 빠짐없이 그대로 사용자에게 전하세요 — 사람이 직접 해야 하는 일입니다"
LOOSE_STAR = "star에 facts에 없는 숫자가 있습니다"
MAX_LISTED = 5


def collect(project: Project, state: State | None = None) -> list[str]:
    """지금 사람이 해야 할 일. 없으면 빈 목록."""
    from jobfit.ai import exp_extract  # pipeline ↔ exp_extract 순환을 피하려고 여기서

    out: list[str] = []
    cards, problems = load_cards(project.experiences_file)
    if problems:
        out.append(f"me/experiences.yaml 을 읽을 수 없습니다({problems[0]}) — 직접 고쳐 주세요")

    examples = example_ids(cards, project.me_example_dir / "experiences.yaml")
    if examples and len(examples) == len(cards):
        out.append(
            f"경험 카드가 예시 카드뿐입니다({_ids(examples)}) — 매칭·초안이 가짜 경험 기준입니다. "
            "sources/ 에 이력서·포트폴리오를 넣고 경험 카드를 만든 뒤 예시 카드는 지우세요"
        )
    elif examples:
        out.append(
            f"me/experiences.yaml 에 예시 카드 {len(examples)}개가 남아 있습니다({_ids(examples)}) — 직접 지우세요. "
            "그대로 두면 가짜 경험과 숫자가 매칭·초안에 쓰입니다"
        )

    files = exp_extract.source_files(project)
    ai_unverified = [str(c.get("id")) for c in cards if c.get("source") and c.get("verified") is not True]
    if ai_unverified:
        out.append(
            f"AI가 뽑은 경험 카드 {len(ai_unverified)}개가 아직 확인 전입니다 — me/experiences.yaml 에서 사실을 확인하고 "
            "verified: true 로 바꾸세요(확인 전 카드의 숫자는 초안에서 [확인 필요]가 됩니다)"
        )
    direct_ids = {cid for info in files.values() if info.get("direct") for cid in info.get("cards", [])}
    unchecked = [cid for cid in ai_unverified if cid in direct_ids]
    if unchecked:
        out.append(f"그림·스캔 PDF에서 뽑은 카드는 숫자를 원문과 대조하지 못했습니다({_ids(unchecked)}) — 숫자를 직접 확인하세요")
    if cards and not problems:
        loose = [i.card for i in check_cards(cards, KeywordDict.load(project.data_dir)) if i.message.startswith(LOOSE_STAR)]
        if loose:
            out.append(
                f"카드 {len(loose)}개의 STAR에 facts에 없는 숫자가 있습니다({_ids(loose)}) — 그 숫자를 facts에 넣거나 STAR에서 빼세요"
                "(그대로 두면 초안 검사에 걸립니다)"
            )

    for rel, info in files.items():
        if info.get("status") == "skipped":
            out.append(f"{rel} 은(는) 읽지 못해 건너뛰었습니다 — {info.get('note', '')}")

    if (project.me_dir / "style" / "style_rules.new.md").exists():
        out.append("me/style/style_rules.new.md 가 있습니다 — style_rules.md 와 비교해 쓸 쪽만 style_rules.md 로 남기세요")

    if state is not None and state.sheet_errors:
        errs = state.sheet_errors
        out.append("jobs.xlsx 에서 읽지 못한 곳이 있습니다 — 직접 고쳐 주세요: " + " / ".join(errs[:MAX_LISTED]) + (f" 외 {len(errs) - MAX_LISTED}건" if len(errs) > MAX_LISTED else ""))
    return out


def render(notices: list[str]) -> list[str]:
    if not notices:
        return []
    return ["", HEADER, *(f"  - {n}" for n in notices)]


def _ids(ids: list[str]) -> str:
    shown = ", ".join(ids[:MAX_LISTED])
    return shown + (f" 외 {len(ids) - MAX_LISTED}개" if len(ids) > MAX_LISTED else "")
