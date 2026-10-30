"""처리 상태 원장 — .jobfit/state.json.

jobs.xlsx는 사용자 전용이고 dashboard.xlsx는 매 실행 다시 만든다.
그래서 "지금까지 무엇을 했는가"는 전부 이 파일에만 있다. 임시 파일에 쓴 뒤 교체한다.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_VERSION = 1

# 처리 상태(스크립트가 정함) — 사용자 결정(jobs.xlsx 결정 열)과는 별개다
STATUS_WAITING = "대기"
STATUS_RUNNING = "처리중"
STATUS_AI = "AI대기"
STATUS_NEED_QUESTIONS = "문항대기"
STATUS_REVIEW = "검토대기"
STATUS_ERROR = "오류"

DECISIONS = ("미정", "보류", "지원안함", "지원완료", "서류합격", "탈락", "최종합격")
DECISION_DEFAULT = "미정"
DECISIONS_PAUSED = {"보류", "지원안함"}  # 처리 대상에서 빠진다. 미정으로 돌리면 다시 처리
DECISIONS_CLOSED = {"지원완료", "서류합격", "탈락", "최종합격"}  # 끝난 공고. 작업 지시 무시

TASKS = ("", "재실행", "초안만 재작성")

# 지원처 — 어디로 지원하나. jobs.xlsx 지원방법 칸(빈칸이면 페이지에서 읽은 값)과 fetch/channel.py가 같이 쓴다
VIA_BOARD = "플랫폼"  # 사람인 입사지원·잡코리아 즉시지원처럼 플랫폼에 올린 이력서로
VIA_COMPANY = "홈페이지"  # 회사 채용 사이트 양식에 새로 입력
VIA_EMAIL = "이메일"
APPLY_VIAS = (VIA_BOARD, VIA_COMPANY, VIA_EMAIL)

# 파이프라인 단계 순서. 구현되지 않은 단계는 pipeline.IMPLEMENTED에 없다.
# report는 매칭 뒤에 한 번 나오고(문항이 없어도), 초안 단계가 끝나면 다시 만든다
STEPS = ("fetch", "clean", "analyze", "match", "report", "draft")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Job:
    key: str
    site: str
    company: str
    title: str = ""
    url: str = ""
    deadline: str = ""  # YYYY-MM-DD 또는 빈칸
    memo: str = ""
    decision: str = DECISION_DEFAULT
    apply_via: str = ""  # jobs.xlsx 지원방법 칸(사용자 지정). 빈칸이면 페이지에서 읽은 값을 쓴다
    task_seen: str = ""  # 마지막으로 본 작업 열 값. 값이 바뀔 때만 한 번 실행한다
    status: str = STATUS_WAITING
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)  # 단계 -> {done, at, note}
    error: str = ""
    next_hint: str = ""  # 대시보드 "다음 할 일"
    row: int = 0  # jobs.xlsx 공고 시트 행 번호(마지막 sync-in 기준)
    user_questions: list[dict[str, Any]] = field(default_factory=list)  # 문항 시트에서 사용자가 입력한 문항(마지막 sync-in)
    force_steps: list[str] = field(default_factory=list)  # 캐시를 쓰지 않고 AI에 다시 물을 단계(초안만 재작성)
    registered_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    # --- 단계 기록 ---
    def step_done(self, step: str) -> bool:
        return bool(self.steps.get(step, {}).get("done"))

    def mark(self, step: str, done: bool = True, **extra: Any) -> None:
        self.steps[step] = {"done": done, "at": now(), **extra}
        self.updated_at = now()

    def reset_from(self, step: str) -> None:
        """해당 단계부터 뒤의 단계 기록을 지운다(재실행용)."""
        if step not in STEPS:
            return
        for s in STEPS[STEPS.index(step):]:
            self.steps.pop(s, None)
        self.error = ""
        self.updated_at = now()

    # --- 처리 대상 판정 ---
    @property
    def is_paused(self) -> bool:
        return self.decision in DECISIONS_PAUSED

    @property
    def is_closed(self) -> bool:
        return self.decision in DECISIONS_CLOSED

    @property
    def is_active(self) -> bool:
        return not (self.is_paused or self.is_closed)

    def set_error(self, message: str, next_hint: str = "") -> None:
        self.status = STATUS_ERROR
        self.error = message
        self.next_hint = next_hint
        self.updated_at = now()

    def clear_error(self) -> None:
        self.error = ""
        if self.status == STATUS_ERROR:
            self.status = STATUS_RUNNING
        self.updated_at = now()


@dataclass
class State:
    jobs: dict[str, Job] = field(default_factory=dict)
    last_sync: str = ""
    sheet_errors: list[str] = field(default_factory=list)  # 마지막 sync-in에서 읽지 못한 jobs.xlsx 행
    version: int = STATE_VERSION

    # --- 입출력 ---
    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = set(Job.__dataclass_fields__)
        jobs = {k: Job(**{f: x for f, x in v.items() if f in known}) for k, v in raw.get("jobs", {}).items()}
        return cls(jobs=jobs, last_sync=raw.get("last_sync", ""), sheet_errors=raw.get("sheet_errors", []), version=raw.get("version", STATE_VERSION))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "last_sync": self.last_sync,
            "sheet_errors": self.sheet_errors,
            "jobs": {k: asdict(j) for k, j in self.jobs.items()},
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix="state.", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # --- 조회 ---
    def get(self, key: str) -> Job | None:
        return self.jobs.get(key)

    def find(self, token: str) -> list[Job]:
        """키 전체, 키 일부(공고번호), 회사명으로 찾는다."""
        token = token.strip()
        if token in self.jobs:
            return [self.jobs[token]]
        hits = [j for j in self.jobs.values() if j.key.endswith(":" + token) or j.company == token]
        if not hits:
            hits = [j for j in self.jobs.values() if token in j.key or token in j.company]
        return hits

    def active_jobs(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.is_active]
