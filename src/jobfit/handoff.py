"""메뉴 끝에 보여 줄 '에이전트에게 보낼 말' — 사람이 메뉴에서 한 일을 에이전트(데스크톱 앱·Claude Code·Codex)에게 넘긴다.

말에는 늘 "이전 대화에서 본 상태는 믿지 말고 jobfit next부터"를 넣는다. 실측: 사용자가 문항을 넣은 뒤
에이전트가 명령을 실행하지 않고 앞선 대화의 기억으로 "문항이 없다"고 답했다.
다음 할 일이 사람 몫(문항 넣기·공고 다시 받기·본문 붙여넣기·공고 추가)이면 보낼 말 대신 그 안내를 준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobfit import menunum, pipeline
from jobfit.fetch.fetcher import NETWORK_ERROR_MARK
from jobfit.paths import Project
from jobfit.state import STEPS, State

AGENT_RULE = "이전 대화에서 본 상태는 믿지 말고 `uv run jobfit next`부터 다시 실행해서, 안내대로 끝까지 이어서 진행해줘."
STEP_NAMES = {"fetch": "공고 받기", "clean": "공고 정리", "analyze": "공고 분석", "match": "매칭", "report": "report", "draft": "초안 작성"}


@dataclass
class Handoff:
    agent: str = ""  # 에이전트에게 보낼 말
    human: str = ""  # 사람이 할 일(메뉴 번호와 함께)
    web_chat: bool = False  # AI 단계라 웹 채팅 사용자는 프롬프트 복사로 이어 갈 수 있다


def build(project: Project, state: State, did: str = "") -> Handoff:
    """지금 상태의 다음 할 일로 만든다. did 는 방금 메뉴에서 한 일("○○ 문항 2개를 넣었어.")."""
    action = pipeline.next_action(project, state)
    job = action.job
    if action.kind in ("ai", "run"):
        todo = _todo(action)
        return Handoff(agent=f"{did} {AGENT_RULE}".strip() + (f" (지금 다음 할 일: {todo})" if todo else ""), web_chat=action.kind == "ai")
    if action.kind == "paste" and job is not None:
        if NETWORK_ERROR_MARK in job.error:
            return Handoff(human=f"{job.company}: 공고를 받지 못했습니다 — 인터넷 연결을 확인하고 {menunum.at(menunum.RUN, '공고 처리 진행')}을 다시 실행하세요. "
                           f"그래도 안 되면 {menunum.at(menunum.POSTING, '공고 본문 붙여넣기')}")
        return Handoff(human=f"{job.company}: 공고 본문을 붙여 넣어야 합니다 — 브라우저에서 본문을 복사한 뒤 {menunum.at(menunum.POSTING, '공고 본문 붙여넣기')}")
    if action.kind == "edit":
        if job is not None:
            return Handoff(human=f"{job.company}: 공고에 자소서 문항이 없습니다 — 문항이 있으면 {menunum.at(menunum.QUESTIONS, '문항 넣기')}로 넣으세요. "
                           "문항이 없는 공고면 report.md가 결과입니다")
        return Handoff(human=f"처리할 공고가 없습니다 — {menunum.at(menunum.ADD, '공고 추가')}로 공고 링크를 넣으세요")
    return Handoff()


def _todo(action: pipeline.Action) -> str:
    job = action.job
    if job is None:
        return "경험 카드 만들기"
    pending = [s for s in STEPS if not job.step_done(s)]
    if action.kind == "ai" and pending:
        return f"{job.company} {STEP_NAMES.get(pending[0], pending[0])}"
    return f"{job.company} 공고 이어서 처리"
