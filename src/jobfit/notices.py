"""사용자에게 꼭 닿아야 하는 안내 — 사람만 할 수 있는 일(me/·jobs.xlsx·sources/ 고치기)을 한 묶음으로.

에이전트는 me/·jobs.xlsx를 고치지 않으므로, 이 안내가 요약 중에 빠지면 아무도 처리하지 않는다.
그래서 경고 줄 사이에 섞지 않고 next·run 끝과 경험 카드 뽑기가 끝났을 때 따로 낸다.
머리줄에 지시를 적어 AGENTS.md를 읽지 않은 에이전트(예: 데스크톱 앱의 가상 머신 작업 모드)도 보게 한다.
"""

from __future__ import annotations

from jobfit import menunum
from jobfit.clean.keywords import KeywordDict
from jobfit.emphasis import example_note, load_resume, split_examples
from jobfit.experiences import check_cards, example_ids, load_cards
from jobfit.fetch.company import CLOSED_MARK
from jobfit.fetch.fetcher import NETWORK_ERROR_MARK
from jobfit.match import cards_in_use, load_match
from jobfit.paths import Project
from jobfit.profile import example_note as profile_example_note
from jobfit.profile import load_profile
from jobfit.profile import split_examples as profile_split_examples
from jobfit.state import STATUS_ERROR, State

HEADER = "[사용자에게 전할 것] 에이전트는 아래 줄을 요약하지 말고 빠짐없이 그대로 사용자에게 전하세요 — 사람이 직접 해야 하는 일입니다"
LOOSE_STAR = "star에 facts에 없는 숫자가 있습니다"
MAX_LISTED = 5
HANDOFF_TIP = "메뉴 끝에 나오는 '에이전트에게 보낼 말'을 에이전트에게 붙여 넣으면 이어서 진행합니다"


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
    resume, _ = load_resume(project.resume_file)
    resume_note = example_note(resume, split_examples(resume, project.me_example_dir / "resume.yaml")[1])
    if resume_note:
        out.append(resume_note)
    prof, _ = load_profile(project.profile_file)
    prof_note = profile_example_note(prof, profile_split_examples(prof, project.me_example_dir / "profile.yaml")[1])
    if prof_note:
        out.append(prof_note)

    files = exp_extract.source_files(project)
    ai_unverified = [str(c.get("id")) for c in cards if c.get("source") and c.get("verified") is not True]
    if ai_unverified:
        # 31개를 한꺼번에 확인하라고 하면 당장 다 해야 하는 일로 읽힌다 — 리포트·초안에 쓰이는 카드만 짚는다
        in_use = _in_use(project, state)
        used = [cid for cid in ai_unverified if cid in in_use]
        rest = len(ai_unverified) - len(used)
        if used:
            out.append(
                f"공고 리포트·초안에 쓰이는 경험 카드 {len(used)}개가 아직 확인 전입니다({_ids(used)}) — me/experiences.yaml 에서 "
                "사실을 확인하고 verified: true 로 바꾸세요(확인 전 카드의 숫자는 초안에서 [확인 필요]가 됩니다)"
                + (f". 나머지 {rest}개는 쓰일 때 알려 드립니다" if rest else "")
            )
        elif not in_use:
            out.append(
                f"AI가 뽑은 경험 카드 {len(ai_unverified)}개는 아직 확인 전입니다 — 지금 다 확인하지 않아도 됩니다. "
                "공고 리포트·초안에 쓰일 카드가 생기면 그 카드부터 알려 드립니다"
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

    if state is not None:
        failed = [j for j in sorted(state.active_jobs(), key=lambda j: j.row) if j.status == STATUS_ERROR]
        net = [j for j in failed if NETWORK_ERROR_MARK in j.error]
        closed = [j for j in failed if CLOSED_MARK in j.error]
        paste = [j for j in failed if j not in net and j not in closed]
        if closed:
            out.append(
                f"마감돼 내려간 공고가 있습니다: {_ids([j.company for j in closed])} — 지원하지 않을 공고면 jobs.xlsx 결정 열을 '지원안함'으로 바꾸세요. "
                f"본문을 갖고 있으면 {menunum.at(menunum.POSTING, '공고 본문 붙여넣기')}로 넣으면 이어서 처리합니다"
            )
        if net:
            out.append(
                f"공고 {len(net)}개를 이 환경에서 받지 못했습니다(인터넷 연결 문제: {_ids([j.company for j in net])}) — "
                f"이 PC에서 {menunum.at(menunum.RUN, '공고 처리 진행')}을 한 번 실행해 주세요. {HANDOFF_TIP}"
            )
        if paste:
            out.append(
                "공고 본문을 붙여 넣어야 합니다: " + ", ".join(f"{j.company}({j.error})" for j in paste[:MAX_LISTED])
                + (f" 외 {len(paste) - MAX_LISTED}개" if len(paste) > MAX_LISTED else "")
                + f" — 브라우저에서 본문을 복사해 {menunum.at(menunum.POSTING, '공고 본문 붙여넣기')}로 넣으세요. {HANDOFF_TIP}"
            )

    if state is not None and state.sheet_errors:
        errs = state.sheet_errors
        out.append("jobs.xlsx 에서 읽지 못한 곳이 있습니다 — 직접 고쳐 주세요: " + " / ".join(errs[:MAX_LISTED]) + (f" 외 {len(errs) - MAX_LISTED}건" if len(errs) > MAX_LISTED else ""))
    return out


def _in_use(project: Project, state: State | None) -> set[str]:
    """진행 중인 공고의 리포트·초안에 쓰이는 카드 id. 매칭한 공고가 없으면 빈 집합."""
    if state is None and project.state_file.exists():
        state = State.load(project.state_file)
    if state is None:
        return set()
    return {cid for job in state.active_jobs() for cid in cards_in_use(load_match(project, job))}


def render(notices: list[str]) -> list[str]:
    if not notices:
        return []
    return ["", HEADER, *(f"  - {n}" for n in notices)]


def _ids(ids: list[str]) -> str:
    shown = ", ".join(ids[:MAX_LISTED])
    return shown + (f" 외 {len(ids) - MAX_LISTED}개" if len(ids) > MAX_LISTED else "")
