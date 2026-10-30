"""파이프라인 — sync-in(읽기) → fetch → clean → [analyze → match → draft → report].

`run`은 AI 단계 직전까지 가능한 단계를 전부 실행하고 멈춘다. AI 단계에 닿으면 프롬프트 파일을 만들고
(같은 입력의 결과가 캐시에 있으면 AI 단계를 건너뛰고) 상태를 AI대기로 둔다.
`next`는 현재 상태를 보고 다음에 실행할 명령 하나를 알려 준다.
구현된 단계: fetch, clean, analyze(MVP-2), match, report(MVP-3). 문항이 없는 공고는 report에서 끝나고 문항대기가 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jobfit import jobkey, menunum
from jobfit.ai import analyze, draft, exp_extract, profile_extract, style_rules
from jobfit.clean.posting import FILE_POSTING, clean_job
from jobfit.dashboard import DashboardBusy, build_dashboard
from jobfit.excel.reader import JobRow, QuestionRow, ReadResult, read_jobs_workbook
from jobfit.experiences import file_sha
from jobfit.fetch.fetcher import FILE_MAIN, FILE_META, NETWORK_ERROR_MARK, fetch_job
from jobfit.match import inputs_sha, load_match, run_match
from jobfit.paths import Project
from jobfit.report import FILE_REPORT, write_report
from jobfit.style import draft_blocker, refresh_stats, style_pending
from jobfit.state import (
    STATUS_AI,
    STATUS_ERROR,
    STATUS_NEED_QUESTIONS,
    STATUS_NEED_SAMPLES,
    STATUS_REVIEW,
    STATUS_RUNNING,
    STATUS_WAITING,
    STEPS,
    Job,
    State,
    now,
)

IMPLEMENTED = {"fetch", "clean", "analyze", "match", "report", "draft"}
# AI 단계 -> 프롬프트 준비 함수
AI_PREPARERS = {"analyze": analyze.prepare, "draft": draft.prepare}
AI_HINT = f"AI 프롬프트 복사 (메뉴 {menunum.PROMPT} · jobfit prep --copy) → 웹 채팅 → 답 복사 → 메뉴 {menunum.ANSWER} (jobfit accept --paste)"

PASTE_HINT = "공고 본문 붙여넣기 (output 폴더의 posting.txt)"


@dataclass
class Action:
    kind: str  # "run" | "paste" | "edit" | "ai" | "unimplemented" | "done" | "none"
    message: str
    command: str = ""
    job: Job | None = None


@dataclass
class SyncReport:
    added: list[Job] = field(default_factory=list)
    updated: list[Job] = field(default_factory=list)
    removed: list[Job] = field(default_factory=list)
    tasks: list[tuple[Job, str]] = field(default_factory=list)  # 이번에 새로 걸린 작업 지시
    duplicates: list[tuple[JobRow, Job]] = field(default_factory=list)
    questions: dict[str, list[QuestionRow]] = field(default_factory=dict)  # key -> 사용자 문항
    unmatched_questions: list[QuestionRow] = field(default_factory=list)
    read: ReadResult = field(default_factory=ReadResult)


# --- sync-in ---


def sync_in(project: Project, state: State) -> SyncReport:
    """jobs.xlsx를 읽어 state에 반영한다. 파일은 쓰지 않는다."""
    report = SyncReport(read=read_jobs_workbook(project.jobs_xlsx))
    seen: dict[str, Job] = {}
    for row in report.read.jobs:
        k = jobkey.derive(row.url, row.company, row.title, row.deadline or row.deadline_note)
        if k.key in seen:
            report.duplicates.append((row, seen[k.key]))
            continue
        job = state.get(k.key)
        if job is None:
            job = Job(key=k.key, site=k.site, company=row.company, title=row.title, url=row.url, deadline=row.deadline, memo=row.memo, decision=row.decision, apply_via=row.apply_via, task_seen=row.task, row=row.row)
            state.jobs[k.key] = job
            report.added.append(job)
        else:
            changed = _apply_row(job, row)
            if changed:
                report.updated.append(job)
            if row.task and row.task != job.task_seen:
                report.tasks.append((job, row.task))
                _apply_task(job, row.task, project)
            job.task_seen = row.task
        seen[k.key] = job

    # 사용자가 지운 행 — state에서만 빼고 output 폴더는 둔다
    for key in list(state.jobs):
        if key not in seen:
            report.removed.append(state.jobs.pop(key))

    # 문항 — 사용자 입력만. 공고 참조를 회사명(/직무)로 맞춘다
    for q in report.read.questions:
        job = _match_job_ref(q.job_ref, seen.values())
        if job is None:
            report.unmatched_questions.append(q)
        else:
            report.questions.setdefault(job.key, []).append(q)
    for key, job in seen.items():
        rows = sorted(report.questions.get(key, []), key=lambda q: (q.number if q.number > 0 else 10_000 + q.row))
        job.user_questions = [{"text": q.text, "limit": q.limit, "count_spaces": q.count_spaces, "row": q.row} for q in rows]

    state.sheet_errors = (
        [str(e) for e in report.read.errors]
        + [f"[공고 {row.row}행] {dup.row}행과 같은 공고입니다 ({dup.key}) — 건너뜀" for row, dup in report.duplicates]
        + [f"[문항 {q.row}행] '{q.job_ref}'에 맞는 공고가 없거나 여럿입니다. '회사명 / 직무'로 적어 주세요" for q in report.unmatched_questions]
    )
    state.last_sync = now()
    return report


def _apply_row(job: Job, row: JobRow) -> bool:
    changed = False
    for attr, value in (("company", row.company), ("title", row.title), ("url", row.url), ("deadline", row.deadline), ("memo", row.memo), ("decision", row.decision), ("apply_via", row.apply_via)):
        if getattr(job, attr) != value:
            setattr(job, attr, value)
            changed = True
    if job.row != row.row:
        job.row = row.row
    if changed:
        job.updated_at = now()
    return changed


def _apply_task(job: Job, task: str, project: Project) -> None:
    if job.is_closed:
        job.next_hint = "끝난 공고라 작업 지시를 무시했습니다. 작업 칸을 비워 주세요"
        return
    if task == "재실행":
        # 링크가 있으면 공고를 다시 받는다. 사용자가 붙여 넣은 본문은 그대로 두고 다시 정제한다
        if job.url:
            forget_fetch(project.job_dir(job.key))
        job.reset_from("fetch")
        job.status = STATUS_WAITING
        job.next_hint = ""
    elif task == "초안만 재작성":
        job.reset_from("draft")
        if "draft" not in job.force_steps:
            job.force_steps.append("draft")  # 같은 입력이어도 캐시를 쓰지 않고 새로 받는다
        job.status = STATUS_WAITING
        job.next_hint = ""


def _match_job_ref(ref: str, jobs) -> Job | None:
    ref = ref.strip()
    by_key = [j for j in jobs if j.key == ref]  # 공고키(jobkorea:50073391)로 적어도 알아듣는다
    if by_key:
        return by_key[0]
    company, _, title = (p.strip() for p in ref.partition(" / "))
    exact = [j for j in jobs if j.company == company and (not title or j.title == title)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1 and not title:
        return None  # 같은 회사 공고가 여럿 — '회사명 / 직무'로 적어야 한다
    return exact[0] if exact else None


# --- run / next ---


def run(project: Project, state: State, only: Job | None = None) -> list[str]:
    """AI 단계 직전까지 실행하고 대시보드를 다시 만든다. 공고별 결과 한 줄씩 돌려준다."""
    lines: list[str] = []
    targets = [only] if only else sorted(state.active_jobs(), key=lambda j: j.row)
    if not only and exp_extract.list_sources(project):
        # 경험 카드 자료도 AI 단계 직전까지 — 바뀐 자료의 프롬프트를 만들어 둔다
        report = exp_extract.scan(project)
        if report.pending:
            lines.append(f"경험 카드: sources/ 자료 조각 {len(report.pending)}개가 AI 단계를 기다립니다")
        for rel, why in report.skipped:
            lines.append(f"경험 카드: {rel} 건너뜀 — {why}")
    lines += _prepare_style(project)
    for job in targets:
        if not job.is_active:
            lines.append(f"{job.company}: 결정이 '{job.decision}'이라 건너뜀")
            continue
        lines.append(_run_job(project, job))
    lines.append(refresh_dashboard(project, state))
    return lines


def _prepare_style(project: Project) -> list[str]:
    """원안 자소서가 바뀌었으면 문체 수치를 다시 재고, 문체 규칙이 아직이면 그 프롬프트를 만들어 둔다(AI 단계 직전까지)."""
    lines = []
    stats = refresh_stats(project)
    if stats is not None:
        lines.append(f"문체: 원안 자소서 {stats['samples']}개로 수치를 다시 쟀습니다")
    if style_pending(project):
        prompt = style_rules.work_dir(project) / style_rules.FILE_PROMPT
        if stats is not None or not prompt.exists():
            path, note = style_rules.prepare(project)
            if path is None:
                return lines + [f"문체 분석: {note}"]
        lines.append(f"문체 분석: 원안 자소서의 문체·흐름을 정리하는 AI 단계입니다. 프롬프트 {project.rel(prompt)} — 초안은 이 분석을 따라 씁니다")
    return lines


def refresh_dashboard(project: Project, state: State) -> str:
    try:
        return f"대시보드: {project.rel(build_dashboard(project, state))}"
    except DashboardBusy as e:
        return str(e)


def invalidate_stale(project: Project, job: Job) -> str:
    """매칭·report의 입력(분석·경험 카드·문항·이력서)이 바뀌었으면 그 단계부터 다시 하게 한다. 이유를 돌려준다."""
    reason = _stale_reason(project, job)
    if reason:
        job.reset_from("match" if "매칭" in reason else "report")
        job.status = STATUS_RUNNING
    return reason


def _stale_reason(project: Project, job: Job) -> str:
    if job.step_done("match"):
        stored = job.steps["match"]
        changed = [k for k, v in inputs_sha(project, job).items() if stored.get(k) != v]
        if changed:
            names = {"analysis_sha": "공고 분석", "cards_sha": "경험 카드", "questions_sha": "문항", "rules_sha": "매칭 규칙"}
            return "·".join(names[k] for k in changed) + "이(가) 바뀌어 매칭부터 다시"
    if job.step_done("report"):
        stored = job.steps["report"]
        if stored.get("resume_sha") != file_sha(project.resume_file):
            return "이력서 항목이 바뀌어 report부터 다시"
        if stored.get("apply_via", "") != job.apply_via:
            return "지원방법이 바뀌어 report부터 다시"
    return ""


def _run_job(project: Project, job: Job) -> str:
    job_dir = project.job_dir(job.key)
    why = invalidate_stale(project, job)
    prefix = f"({why}) " if why else ""
    for step in STEPS:
        if job.step_done(step):
            continue
        if step == "draft" and not load_match(project, job).get("questions"):
            # 문항이 없으면 초안을 만들지 않는다. report(매칭·강조 포인트)까지가 이 공고의 결과다
            job.status = STATUS_NEED_QUESTIONS
            job.next_hint = "문항 시트에 문항 입력, 또는 결정 열에서 지원 여부 결정"
            report_path = project.rel(job_dir / FILE_REPORT)
            return f"{job.company}: {prefix}report 완료 — {report_path}. 공고에 자소서 문항이 없어 초안은 만들지 않습니다"
        if step == "draft":
            # 초안은 원안 자소서의 문체·흐름에 내 경험을 맞추는 것이다. 원안이나 그 분석이 없으면 처음부터 쓰지 않는다
            blocker = draft_blocker(project)
            if blocker:
                job.status = STATUS_NEED_SAMPLES
                job.next_hint = blocker
                return f"{job.company}: {prefix}report 완료 — {project.rel(job_dir / FILE_REPORT)}. 초안은 쓰지 않습니다: {blocker}"
        if step not in IMPLEMENTED:
            job.status = STATUS_RUNNING if job.status != STATUS_ERROR else job.status
            job.next_hint = f"{step} 단계는 아직 구현되지 않았습니다"
            return f"{job.company}: {prefix}{step} 앞에서 멈춤 (다음 버전)"
        if step in AI_PREPARERS:
            prepared = AI_PREPARERS[step](project, job)
            if prepared.status == "cached":
                continue  # 같은 입력의 결과를 다시 썼다 — AI 단계 생략
            job.status = STATUS_AI
            job.next_hint = AI_HINT
            note = f" — 상한 {prepared.limit}자를 넘었습니다" if prepared.over_limit else ""
            return f"{job.company}: {prefix}{step} — AI 단계입니다. 프롬프트 {project.rel(prepared.prompt_path)} ({prepared.chars}자{note})"
        ok, message = _STEP_RUNNERS[step](project, job, job_dir)
        if not ok:
            return f"{job.company}: {prefix}{step} 실패 — {message}"
    job.status = STATUS_REVIEW
    job.next_hint = "report.md 확인 후 결정 열에서 지원 여부 결정"
    return f"{job.company}: {prefix}초안까지 완료 — {project.rel(job_dir / FILE_REPORT)}"


def _step_fetch(project: Project, job: Job, job_dir: Path) -> tuple[bool, str]:
    if not job.url:
        if (job_dir / FILE_POSTING).exists():
            job.mark("fetch", done=True, note="링크 없음 — 붙여 넣은 본문 사용")
            job.clear_error()
            return True, ""
        job_dir.mkdir(parents=True, exist_ok=True)
        job.set_error("링크가 없습니다", PASTE_HINT)
        return False, f"링크 없음 → {job_dir / FILE_POSTING} 에 본문을 붙여 넣어 주세요"
    job.status = STATUS_RUNNING
    prev = _load_json(job_dir / FILE_META)
    if prev.get("ok") and prev.get("url") == job.url and (job_dir / FILE_MAIN).exists():
        # `jobfit add`가 회사명을 읽느라 이미 받아 둔 원문. 같은 링크면 다시 받지 않는다
        job.mark("fetch", done=True, title=prev.get("title", ""), files=prev.get("files", {}), note="이미 받은 원문 사용")
        job.clear_error()
        return True, ""
    result = fetch_job(job.url, jobkey.derive(job.url), job_dir)
    if result.ok:
        job.mark("fetch", done=True, title=result.title, files=result.files)
        job.clear_error()
        return True, ""
    if (job_dir / FILE_POSTING).exists():
        job.mark("fetch", done=True, note=f"받기 실패({result.error}) — 붙여 넣은 본문 사용")
        job.clear_error()
        return True, ""
    job.set_error(result.error, result.next_hint or PASTE_HINT)
    return False, f"{result.error} → {job_dir / FILE_POSTING} 에 본문을 붙여 넣어 주세요"


def _step_clean(project: Project, job: Job, job_dir: Path) -> tuple[bool, str]:
    result = clean_job(job_dir, project.data_dir, job.url)
    if not result.ok:
        job.set_error(result.error, result.next_hint or PASTE_HINT)
        return False, result.error
    # 직무는 사용자 입력이 원본이다. 페이지 제목은 meta.json에만 둔다
    job.mark("clean", done=True, source=result.source, chars=result.posting_chars, sections=result.sections, questions=len(result.questions))
    job.clear_error()
    return True, ""


def _step_match(project: Project, job: Job, job_dir: Path) -> tuple[bool, str]:
    if not job.step_done("analyze"):
        return False, "공고 분석이 아직입니다"
    result = run_match(project, job)
    job.mark("match", done=True, score=result.get("score"), questions=len(result.get("questions", [])), edited=bool(result.get("edited")), **inputs_sha(project, job))
    return True, ""


def _step_report(project: Project, job: Job, job_dir: Path) -> tuple[bool, str]:
    if not job.step_done("match"):
        return False, "매칭이 아직입니다"
    write_report(project, job)
    job.mark("report", done=True, resume_sha=file_sha(project.resume_file), apply_via=job.apply_via)
    return True, ""


_STEP_RUNNERS = {"fetch": _step_fetch, "clean": _step_clean, "match": _step_match, "report": _step_report}


def forget_fetch(job_dir: Path) -> None:
    """다음 fetch가 원문을 새로 받게 한다. 받은 HTML은 새로 받을 때 덮어쓴다."""
    (job_dir / FILE_META).unlink(missing_ok=True)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def next_action(project: Project, state: State) -> Action:
    if not project.is_initialized():
        return Action(kind="run", message="처음 설정이 필요합니다", command="jobfit init")
    # 경험 카드가 매칭에 쓰이므로, 같은 급이면 자료 → 공고 순서로 안내한다
    exp, sty, prof = _exp_action(project), _style_action(project), _profile_action(project)
    if not state.jobs:
        mine = [a for a in (exp, sty, prof) if a]
        return min(mine, key=lambda a: _PRIORITY[a.kind]) if mine else Action(kind="edit", message=f'jobs.xlsx 공고 시트에 공고를 적거나 jobfit add "<링크>"(메뉴 {menunum.ADD})로 추가한 뒤 jobfit run(메뉴 {menunum.RUN})', command="jobfit run")
    # 경험 카드는 매칭에, 문체 분석은 초안에 쓰여 공고보다 먼저, 지원서 정보는 입력표에만 쓰여 공고 뒤에
    jobs = [a for a in (_job_action(project, j) for j in sorted(state.active_jobs(), key=lambda j: j.row)) if a]
    actions = ([exp] if exp else []) + ([sty] if sty else []) + jobs + ([prof] if prof else [])
    if actions:
        return min(actions, key=lambda a: _PRIORITY[a.kind])  # 같은 급이면 앞의 것(자료, 그다음 jobs.xlsx 위쪽 공고)
    return Action(kind="done", message="처리할 공고가 없습니다. 새 공고를 jobs.xlsx에 적거나 결정을 미정으로 돌리세요", command="jobfit sync-in")


def _exp_action(project: Project) -> Action | None:
    items = exp_extract.pending_items(project)
    if items:
        item = items[0]
        part = f" 조각 {item.part}/{item.parts}" if item.parts > 1 else ""
        direct = "\n  (스크립트가 못 읽은 파일 — 에이전트는 원본을 직접 열고, 웹 채팅은 파일을 첨부)" if item.direct else ""
        message = (
            f"경험 카드 뽑기: {item.source}{part} — AI 단계입니다.\n"
            f"  붙여넣기 모드: jobfit prep --copy → 웹 채팅에 붙여넣기 → 답 전체 복사 → jobfit accept --paste\n"
            f"  에이전트 모드: {project.rel(item.prompt)} 를 읽고 답을 {project.rel(item.response)} 에 쓴 뒤 jobfit accept exp-extract"
            f"{direct}"
        )
        return Action(kind="ai", message=message, command="jobfit prep exp-extract --copy")
    new = exp_extract.new_sources(project)
    if new:
        return Action(kind="run", message=f"sources/ 에 새 자료 {len(new)}개 — 경험 카드로 만들려면", command="jobfit exp extract")
    return None


def _style_action(project: Project) -> Action | None:
    """원안 자소서는 있는데 문체 규칙이 아직이면 그 AI 단계. 초안은 이 규칙이 있어야 쓴다."""
    if not style_pending(project):
        return None
    work = style_rules.work_dir(project)
    message = (
        "원안 자소서의 문체·흐름 분석(문체 규칙 만들기) — AI 단계입니다. 초안은 이 분석을 따라 씁니다.\n"
        f"  붙여넣기 모드: jobfit prep --copy → 웹 채팅에 붙여넣기 → 답 전체 복사 → jobfit accept --paste\n"
        f"  에이전트 모드: jobfit prep style-rules 로 프롬프트를 만들고 {project.rel(work / style_rules.FILE_PROMPT)} 를 읽고 "
        f"답을 {project.rel(work / style_rules.FILE_RESPONSE)} 에 쓴 뒤 jobfit accept style-rules"
    )
    return Action(kind="ai", message=message, command="jobfit prep style-rules --copy")


def _profile_action(project: Project) -> Action | None:
    """지원서 정보 뽑기는 사용자가 profile extract 를 한 뒤에만 — 프롬프트가 있을 때만 안내한다."""
    items = profile_extract.pending_items(project)
    if not items:
        return None
    item = items[0]
    part = f" 조각 {item.part}/{item.parts}" if item.parts > 1 else ""
    message = (
        f"지원서 정보 뽑기: {item.source}{part} — AI 단계입니다.\n"
        f"  붙여넣기 모드: jobfit prep --copy → 웹 채팅에 붙여넣기 → 답 전체 복사 → jobfit accept --paste\n"
        f"  에이전트 모드: {project.rel(item.prompt)} 를 읽고 답을 {project.rel(item.response)} 에 쓴 뒤 jobfit accept profile-extract"
    )
    return Action(kind="ai", message=message, command="jobfit prep profile-extract --copy")


# 사용자가 손을 써야 하는 것 → AI 단계 → 스크립트로 진행 → 다음 버전 → 문항 입력 대기
_PRIORITY = {"paste": 0, "ai": 1, "run": 2, "unimplemented": 3, "edit": 4}


def response_name(job: Job, step: str) -> str:
    """AI 단계의 답 파일 이름. 초안은 수정 단계면 revise.response.md."""
    if step == "draft":
        return draft.response_name(job)
    return analyze.FILE_RESPONSE


def _job_action(project: Project, job: Job) -> Action | None:
    if job.status == STATUS_ERROR:
        if NETWORK_ERROR_MARK in job.error:  # 붙여넣기보다 이 PC에서 한 번 받는 게 먼저
            return Action(kind="paste", message=f"{job.company}: {job.error}. {job.next_hint}", command=f"{menunum.at(menunum.RUN, '공고 처리 진행')} (이 PC에서) — 또는 jobfit run", job=job)
        return Action(kind="paste", message=f"{job.company}: {job.error}. {job.next_hint}", command=f"jobfit posting --job {job.key} --from-clipboard", job=job)
    stale = _stale_reason(project, job)
    if stale:
        return Action(kind="run", message=f"{job.company}: {stale}", command="jobfit run", job=job)
    pending = [s for s in STEPS if not job.step_done(s)]
    if not pending:
        return None
    step = pending[0]
    if job.status == STATUS_AI and step in AI_PREPARERS:
        prompt = project.ai_dir(job.key) / job.steps.get(step, {}).get("prompt", f"{step}.prompt.md")
        response = project.ai_dir(job.key) / response_name(job, step)
        message = (
            f"{job.company}: AI 단계({step})입니다.\n"
            f"  붙여넣기 모드: jobfit prep --copy → 웹 채팅에 붙여넣기 → 답 전체 복사 → jobfit accept --paste\n"
            f"  에이전트 모드: {project.rel(prompt)} 를 읽고 답을 {project.rel(response)} 에 쓴 뒤 jobfit accept {step} --job {job.key}"
        )
        return Action(kind="ai", message=message, command=f"jobfit prep {step} --job {job.key} --copy", job=job)
    if step == "draft" and job.status == STATUS_NEED_QUESTIONS:
        report = project.rel(project.job_dir(job.key) / FILE_REPORT)
        message = (
            f"{job.company}: 공고에 자소서 문항이 없습니다. {report} 에서 매칭·강조 포인트를 보세요. 문항이 있으면 "
            f"{menunum.at(menunum.QUESTIONS, '문항 넣기')}나 jobs.xlsx 문항 시트(공고 칸에 '{job.company}')에 넣고 jobfit run. "
            f'사용자가 문항 원문을 주면 에이전트가 jobfit question add --job {job.key} --text "<문항>" [--limit 글자수]'
        )
        return Action(kind="edit", message=message, command="jobfit run", job=job)
    if step == "draft" and job.status == STATUS_NEED_SAMPLES:
        blocker = draft_blocker(project)
        if not blocker:
            return Action(kind="run", message=f"{job.company}: 원안 자소서와 문체 분석이 준비됐습니다 — 초안 단계로", command="jobfit run", job=job)
        report = project.rel(project.job_dir(job.key) / FILE_REPORT)
        return Action(kind="edit", message=f"{job.company}: 초안은 쓰지 않습니다. {blocker}. {report} 에서 매칭·강조 포인트를 보세요", command="jobfit run", job=job)
    if step in IMPLEMENTED:
        return Action(kind="run", message=f"{job.company}: {step} 단계부터 실행", command="jobfit run", job=job)
    return Action(kind="unimplemented", message=f"{job.company}: 다음은 {step} 단계인데 이 버전에는 없습니다 (다음 MVP)", job=job)
