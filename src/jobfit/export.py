"""Notion 내보내기 — 공고 목록 CSV(Notion 데이터베이스 가져오기용). report.md 는 Notion에서 마크다운으로 가져온다.

Notion API로 직접 쓰는 방식은 v2. 에이전트 앱에 Notion 커넥터가 있으면 에이전트가 report.md 로 페이지를 만든다(AGENTS.md 8).
Notion에서 고친 내용은 로컬로 가져오지 않는다.
"""

from __future__ import annotations

import csv
from pathlib import Path

from jobfit.drafts import lint_summary, load_lint
from jobfit.match import load_analysis, load_match
from jobfit.paths import Project
from jobfit.report import FILE_REPORT
from jobfit.state import Job, State

FILE_CSV = "notion-jobs.csv"
COLUMNS = ("회사명", "직무", "결정", "처리상태", "마감일", "매칭점수", "문항", "초안", "요약", "링크", "공고키", "report")


def export_csv(project: Project, state: State) -> Path:
    path = project.output_dir / FILE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = sorted(state.jobs.values(), key=lambda j: (0 if j.is_active else 1, j.deadline or "9999-99-99", j.row))
    # utf-8-sig: 엑셀에서 열어도 한글이 깨지지 않게. Notion 가져오기도 그대로 읽는다
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for job in jobs:
            writer.writerow(_row(project, job))
    return path


def _row(project: Project, job: Job) -> list:
    match = load_match(project, job)
    analysis = load_analysis(project, job)
    questions = match.get("questions") or []
    report = project.job_dir(job.key) / FILE_REPORT
    return [
        job.company,
        job.title,
        job.decision,
        job.status,
        job.deadline,
        match.get("score") if match.get("score") is not None else "",
        f"{len(questions)}개 ({questions[0]['source']})" if questions else ("미입력" if match else ""),
        lint_summary(load_lint(project, job)) if job.step_done("draft") else "",
        analysis.get("role_summary", ""),
        job.url,
        job.key,
        project.rel(report) if report.exists() else "",
    ]
