"""초안 결과 파일 읽기 — output/<공고>/drafts/q*.md 와 lint.json. report·dashboard·draft 단계가 같이 쓴다."""

from __future__ import annotations

import json

from jobfit.paths import Project
from jobfit.state import Job

DRAFTS_DIR = "drafts"
FILE_LINT = "lint.json"


def load_drafts(project: Project, job: Job) -> dict[str, str]:
    d = project.job_dir(job.key) / DRAFTS_DIR
    if not d.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(d.glob("q*.md"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)}


def load_lint(project: Project, job: Job) -> dict:
    path = project.job_dir(job.key) / FILE_LINT
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def lint_summary(lint: dict) -> str:
    """대시보드 '초안' 칸: 오류 n건 / 경고 n건 / 확인 필요 n건 / 완료."""
    qs = lint.get("questions", [])
    if not qs:
        return ""
    errors = sum(q.get("errors", 0) for q in qs)
    warnings = sum(q.get("warnings", 0) for q in qs)
    confirm = sum(q.get("confirm_needed", 0) for q in qs)
    if errors:
        return f"오류 {errors}건"
    if warnings:
        return f"경고 {warnings}건"
    if confirm:
        return f"확인 필요 {confirm}건"
    return "완료"
