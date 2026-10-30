"""jobfit 명령줄. 메시지는 짧은 한국어, 종료 코드는 0 성공 · 1 실패 · 2 사용법."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from jobfit import __version__
from jobfit import jobkey, menunum, pipeline
from jobfit.excel.workbook import create_jobs_workbook
from jobfit.paths import Project
from jobfit.state import APPLY_VIAS, STATUS_AI, STATUS_REVIEW, STATUS_RUNNING, State

UNIMPLEMENTED: dict[str, str] = {}
# 단계별 실행 명령이 먼저 끝나 있어야 하는 단계
_PREREQ = {"clean": "fetch", "match": "analyze", "report": "match"}
# AI 단계 이름 -> 아직이면 들어올 버전, 됐으면 None. revise 는 draft 의 수정 단계
AI_STEPS = {"analyze": None, "draft": None, "revise": None, "style-rules": None, "exp-extract": None, "profile-extract": None}


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    project = Project.at(args.root)
    try:
        return args.func(project, args)
    except FileNotFoundError as e:
        print(f"파일이 없습니다: {e}")
        return 1
    except PermissionError as e:
        print(f"파일에 쓸 수 없습니다(열려 있나요?): {e.filename}")
        return 1


# --- 명령 ---


def cmd_init(project: Project, args) -> int:
    made: list[str] = []
    if project.jobs_xlsx.exists():
        print(f"이미 있어 건너뜀: {project.jobs_xlsx.name}")
    else:
        create_jobs_workbook(project.jobs_xlsx)
        made.append(project.jobs_xlsx.name)
    for d in (project.output_dir, project.state_dir, project.sources_dir):
        if not d.exists():
            d.mkdir(parents=True)
            made.append(d.name + "/")
    if not project.me_dir.exists():
        if project.me_example_dir.is_dir():
            shutil.copytree(project.me_example_dir, project.me_dir)
            made.append("me/ (me.example 복사)")
        else:
            project.me_dir.mkdir()
            made.append("me/")
    if not project.state_file.exists():
        State().save(project.state_file)
    print("만든 것: " + (", ".join(made) if made else "없음(모두 있었음)"))
    print("다음: 내 이력서·포트폴리오·프로젝트 문서를 sources/ 에 넣고  jobfit exp extract  (경험 카드 만들기)")
    print("      공고는 jobs.xlsx 공고 시트에 적고  jobfit run")
    return 0


def cmd_add(project: Project, args) -> int:
    """jobs.xlsx 공고 시트에 한 줄 추가 — 스크립트가 jobs.xlsx에 쓰는 유일한 명령."""
    _require_init(project)
    from jobfit.dates import parse_deadline
    from jobfit.excel.append import ExcelBusy, append_job_row
    from jobfit.excel.reader import read_jobs_workbook
    from jobfit.fetch.fetcher import FILE_MAIN, fetch_job
    from jobfit.fetch.meta import read_posting_meta

    url = (args.url or "").strip()
    company, title, memo = (args.company or "").strip(), (args.title or "").strip(), (args.memo or "").strip()
    apply_via = args.apply_via or ""
    if url and not re.match(r"^https?://", url, re.I):
        print(f"링크는 http(s)://로 시작해야 합니다: {url}")
        return 2
    if not url and not company:
        print('링크나 --company 중 하나는 있어야 합니다.  예: jobfit add "https://…"')
        return 2
    deadline = ""
    if args.deadline:
        deadline, _, err = parse_deadline(args.deadline)
        if err:
            print(err)
            return 2

    existing = read_jobs_workbook(project.jobs_xlsx)
    if url:
        key = jobkey.derive(url)
        for row in existing.jobs:
            if row.url and jobkey.derive(row.url).key == key.key:
                print(f"이미 공고 시트 {row.row}행에 있습니다: {row.display} ({key.key})")
                print("다음: jobfit run")
                return 0

    # 링크만 받았으면 공고를 받아 회사명·직무·마감일을 읽는다. 받은 원문은 run이 다시 쓴다
    filled: list[str] = []
    if url and not (company and title and deadline):
        key = jobkey.derive(url)
        job_dir = project.job_dir(key.key)
        result = fetch_job(url, key, job_dir)
        if result.ok:
            meta = read_posting_meta((job_dir / FILE_MAIN).read_text(encoding="utf-8", errors="replace"))
            for label, current, found in (("회사명", company, meta.company), ("직무", title, meta.title), ("마감일", deadline, meta.deadline)):
                if not current and found:
                    filled.append(f"{label} '{found}'")
            company, title, deadline = company or meta.company, title or meta.title, deadline or meta.deadline
        elif not company:
            print(f"공고를 받지 못해 회사명을 알 수 없습니다 — {result.error}")
            print('회사명을 알려 주세요:  jobfit add "<링크>" --company <회사명>')
            if result.network:
                print(f"  {result.next_hint}")
            return 1
    if not company:
        print('페이지에서 회사명을 읽지 못했습니다. 회사명을 알려 주세요:  jobfit add "<링크>" --company <회사명>')
        return 1

    if not url:
        key = jobkey.derive("", company, title, deadline)
        for row in existing.jobs:
            if not row.url and jobkey.derive("", row.company, row.title, row.deadline or row.deadline_note).key == key.key:
                print(f"이미 공고 시트 {row.row}행에 있습니다: {row.display}")
                return 0

    try:
        row = append_job_row(project.jobs_xlsx, project.backups_dir, company, title, url, deadline, memo, apply_via)
    except ExcelBusy as e:
        print(str(e))
        return 1
    shown = company + (f" / {title}" if title else "") + (f", 마감 {deadline}" if deadline else "")
    print(f"추가: 공고 시트 {row}행 — {shown}" + (f"  ({jobkey.derive(url).key})" if url else ""))
    if filled:
        print("  페이지에서 읽은 값: " + ", ".join(filled) + " — 틀리면 jobs.xlsx에서 고치세요")
    if apply_via and "지원방법" not in existing.job_headers:
        print("  jobs.xlsx 공고 시트에 '지원방법' 열이 없어 지원방법은 넣지 않았습니다 — 1행 빈 칸에 '지원방법'이라고 적으면 그 열을 씁니다")
    print("다음: jobfit run")
    return 0


def cmd_question_add(project: Project, args) -> int:
    """jobs.xlsx 문항 시트에 문항 추가 — 엑셀이 없어도 넣을 수 있게. 문항은 받은 원문 그대로 쓴다."""
    _require_init(project)
    from jobfit.excel.append import ExcelBusy, append_question_rows

    state = State.load(project.state_file)
    report = pipeline.sync_in(project, state)
    job = _pick_job(state, args.job)
    if job is None:
        state.save(project.state_file)
        return 1
    existing = report.questions.get(job.key, [])
    have = {" ".join(q.text.split()) for q in existing}
    limits = [n if n and n > 0 else None for n in (args.limit or [])]
    questions = []
    for i, raw in enumerate(args.text):
        text = raw.strip()
        if not text:
            continue
        if " ".join(text.split()) in have:
            print(f"이미 있는 문항이라 건너뜁니다: {text}")
            continue
        questions.append((text, limits[i] if i < len(limits) else None, not args.no_spaces))
    if not questions:
        print("넣을 문항이 없습니다")
        return 0 if existing else 2
    ref = f"{job.company} / {job.title}" if job.title else job.company
    first = max([q.number for q in existing if q.number > 0], default=len(existing)) + 1
    try:
        rows = append_question_rows(project.jobs_xlsx, project.backups_dir, ref, questions, first)
    except ExcelBusy as e:
        print(str(e))
        return 1
    print(f"추가: 문항 시트 {rows[0]}" + (f"~{rows[-1]}" if len(rows) > 1 else "") + f"행 — {job.company} 문항 {len(rows)}개")
    for n, (text, limit, spaces) in enumerate(questions, start=first):
        print(f"  {n}. {text}" + (f" ({limit}자, 공백 {'포함' if spaces else '제외'})" if limit else ""))
    print("다음: jobfit run")
    return 0


def cmd_menu(project: Project, args) -> int:
    from jobfit.menu import run_menu

    return run_menu(project)


def cmd_check(project: Project, args) -> int:
    _require_init(project)
    from jobfit.excel.reader import read_jobs_workbook

    result = read_jobs_workbook(project.jobs_xlsx)
    print(f"공고 {len(result.jobs)}건, 문항 {len(result.questions)}건 읽음")
    for err in result.errors:
        print(f"  ! {err}")
    return 0 if result.ok else 1


def cmd_sync_in(project: Project, args) -> int:
    _require_init(project)
    state = State.load(project.state_file)
    report = pipeline.sync_in(project, state)
    state.save(project.state_file)
    _print_sync(report)
    return 0 if not report.read.errors else 1


def cmd_run(project: Project, args) -> int:
    _require_init(project)
    state = State.load(project.state_file)
    report = pipeline.sync_in(project, state)
    _print_sync(report, brief=True)
    only = _pick_job(state, args.job) if args.job else None
    if args.job and only is None:
        state.save(project.state_file)
        return 1
    for line in pipeline.run(project, state, only):
        print("  " + line)
    state.save(project.state_file)
    action = pipeline.next_action(project, state)
    print(f"다음: {action.message}" + (f"  →  {action.command}" if action.command else ""))
    _print_notices(project, state)
    return 0


def cmd_fetch(project: Project, args) -> int:
    return _single_step(project, args, "fetch")


def cmd_clean(project: Project, args) -> int:
    return _single_step(project, args, "clean")


def cmd_match(project: Project, args) -> int:
    return _single_step(project, args, "match")


def cmd_report(project: Project, args) -> int:
    return _single_step(project, args, "report")


def cmd_dashboard(project: Project, args) -> int:
    _require_init(project)
    state = State.load(project.state_file)
    pipeline.sync_in(project, state)
    state.save(project.state_file)
    message = pipeline.refresh_dashboard(project, state)
    print(message)
    return 0 if message.startswith("대시보드:") else 1


def cmd_exp(project: Project, args) -> int:
    from jobfit.clean.keywords import KeywordDict
    from jobfit.experiences import UNVERIFIED_NOTE, FILE_HEADER, check_cards, example_ids, load_cards, new_card_text

    if not project.me_dir.exists():
        print("me/ 폴더가 없습니다. 먼저  jobfit init")
        return 2
    if args.exp_command == "extract":
        return _prep_exp(project, args)
    path = project.experiences_file
    cards, problems = load_cards(path)
    if args.exp_command == "new":
        card_id = args.id or f"exp-{len(cards) + 1}"
        if any(str(c.get("id")) == card_id for c in cards):
            print(f"이미 있는 id입니다: '{card_id}'. --id 로 다른 이름을 주세요")
            return 2
        if problems:
            print(f"{problems[0]} — 고친 뒤 다시 실행하세요")
            return 1
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not cards and existing.strip() in ("", "[]"):
            existing = FILE_HEADER
        path.write_text(existing.rstrip("\n") + "\n" + new_card_text(card_id), encoding="utf-8")
        print(f"{project.rel(path)} 맨 아래에 빈 카드를 넣었습니다(id: {card_id}). 채운 뒤  jobfit exp check")
        return 0

    # check
    if not path.exists():
        print(f"{project.rel(path)}이 없습니다. 예시는 me.example/experiences.yaml, 빈 카드는  jobfit exp new")
        return 1
    for p in problems:
        print(f"  [오류] {p}")
    if problems:
        return 1
    examples = example_ids(cards, project.me_example_dir / "experiences.yaml")
    issues = check_cards(cards, KeywordDict.load(project.data_dir), examples)
    shown = [i for i in issues if i.kind != "unverified"]
    unverified = list(dict.fromkeys(i.card for i in issues if i.kind == "unverified"))
    for issue in shown:
        print(f"  {issue}")
    if unverified:
        ids = ", ".join(unverified[:5]) + (f" 외 {len(unverified) - 5}개" if len(unverified) > 5 else "")
        print(f"  [확인 전] 카드 {len(unverified)}개 — {UNVERIFIED_NOTE}: {ids}")
    errors = sum(1 for i in shown if i.level == "오류")
    warnings = len(shown) - errors
    summary = f"카드 {len(cards)}개 · 오류 {errors} · 경고 {warnings}" + (f" · 확인 전 {len(unverified)}" if unverified else "")
    print(summary + ("" if issues else " — 문제없음"))
    if examples:
        print(f"예시 카드 {len(examples)}개를 지우세요: {', '.join(examples)}  ({project.rel(path)})")
    return 1 if errors else 0


def cmd_next(project: Project, args) -> int:
    state = State.load(project.state_file) if project.state_file.exists() else State()
    if project.is_initialized():
        pipeline.sync_in(project, state)  # jobs.xlsx를 방금 고쳤어도 맞는 안내가 나오게
        state.save(project.state_file)
    action = pipeline.next_action(project, state)
    print(action.message)
    if action.command:
        print(action.command)
    if project.is_initialized():
        _print_notices(project, state)
    return 0


def cmd_posting(project: Project, args) -> int:
    """붙여넣기 — 클립보드나 파일의 글을 그 공고의 posting.txt로 저장한다. 글자는 고치지 않는다."""
    _require_init(project)
    state = State.load(project.state_file)
    job = _pick_job(state, args.job)
    if job is None:
        return 1
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        try:
            import pyperclip

            text = pyperclip.paste()
        except Exception as e:  # noqa: BLE001
            print(f"클립보드를 읽을 수 없습니다({e.__class__.__name__}). --file 로 텍스트 파일을 주세요")
            return 1
    if not text or not text.strip():
        print("붙여 넣을 글이 비어 있습니다")
        return 1
    job_dir = project.job_dir(job.key)
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / pipeline.FILE_POSTING
    target.write_text(text, encoding="utf-8")
    # 받기 단계는 이 글로 대신한다. 정제부터 다시
    job.reset_from("clean")
    job.mark("fetch", done=True, note="붙여 넣은 본문 사용")
    job.clear_error()
    job.next_hint = ""
    state.save(project.state_file)
    print(f"저장: {target} ({len(text)}자)")
    print("받은 원문으로 돌아가려면 이 파일을 지우고  jobfit run")
    print("다음: jobfit run")
    return 0


def cmd_prep(project: Project, args) -> int:
    """AI 프롬프트 만들기. --copy 면 클립보드에 복사(메뉴의 AI 프롬프트 복사)."""
    _require_init(project)
    from jobfit.ai.promptfile import copy_to_clipboard
    from jobfit.match import load_match

    step = args.step
    if step and AI_STEPS[step]:
        print(f"'{step}' 프롬프트는 {AI_STEPS[step]}에서 구현됩니다")
        return 2
    if step == "style-rules":
        return _prep_style(project, args)
    if step == "exp-extract" or (step is None and not args.job and _exp_pending(project)):
        return _prep_exp(project, args)  # 경험 카드가 매칭에 쓰이므로 공고보다 먼저
    if step == "profile-extract" or (step is None and not args.job and _profile_pending(project) and _waiting_job(State.load(project.state_file)) is None):
        return _prep_profile(project, args)
    state = State.load(project.state_file)
    job = _pick_job(state, args.job) if args.job else _waiting_job(state)
    if job is None:
        if not args.job:
            print("AI 단계를 기다리는 공고가 없습니다. 먼저  jobfit run")
        return 1
    step = "draft" if step == "revise" else (step or _pending_ai_step(job) or "analyze")
    need = {"analyze": "clean", "draft": "report"}[step]
    if not job.step_done(need):
        print(f"{job.company}: {need} 단계가 아직입니다. 먼저  jobfit run")
        return 1
    if step == "draft" and not load_match(project, job).get("questions"):
        print(f"{job.company}: 문항이 없어 초안을 만들지 않습니다. jobs.xlsx 문항 시트에 입력하고  jobfit run")
        return 1
    if job.step_done(step) and not args.force:
        print(f"{job.company}: 이미 끝난 단계입니다({step}). 다시 하려면 --force")
        return 0
    if args.force:
        job.steps.pop(step, None)
    prepared = pipeline.AI_PREPARERS[step](project, job, force=args.force)
    if prepared.status == "cached":
        job.status, job.next_hint = (STATUS_REVIEW if step == "draft" else STATUS_RUNNING), ""
        state.save(project.state_file)
        print(f"{job.company}: 같은 입력의 결과가 있어 AI 단계를 생략했습니다 ({step})")
        print("다음: jobfit run")
        return 0
    job.status, job.next_hint = STATUS_AI, pipeline.AI_HINT
    state.save(project.state_file)
    print(f"{job.company}: 프롬프트 {project.rel(prepared.prompt_path)} ({prepared.chars}자 / 상한 {prepared.limit}자)")
    _remember_prompt(project, prepared.prompt_path)
    if prepared.over_limit:
        print("  ! 상한을 넘었습니다. 무료 웹 채팅에서 잘릴 수 있습니다 — 그 공고의 posting.txt나 경험 카드 내용을 줄이세요")
    return _copy_prompt(prepared.prompt_path, args.copy, copy_to_clipboard)


def cmd_accept(project: Project, args) -> int:
    """AI 답 받아들이기. --paste 면 클립보드에서(메뉴의 AI 답 붙여넣기), 아니면 답 파일에서(에이전트 모드)."""
    _require_init(project)
    from jobfit.ai import analyze

    step = args.step
    if step and AI_STEPS[step]:
        print(f"'{step}' 답 받기는 {AI_STEPS[step]}에서 구현됩니다")
        return 2
    if step == "style-rules":
        return _accept_style(project, args)
    target = step if step in ("exp-extract", "profile-extract") else (_accept_target(project) if step is None and not args.job else None)
    if target == "exp-extract":
        return _accept_exp(project, args)
    if target == "profile-extract":
        return _accept_profile(project, args)
    state = State.load(project.state_file)
    job = _pick_job(state, args.job) if args.job else _waiting_job(state)
    if job is None:
        if not args.job:
            print("AI 답을 기다리는 공고가 없습니다. 지금 할 일은  jobfit next")
        return 1
    step = "draft" if step == "revise" else (step or _pending_ai_step(job) or "analyze")
    prompt_path = project.ai_dir(job.key) / job.steps.get(step, {}).get("prompt", f"{step}.prompt.md")
    raw, rc = _read_answer(args, prompt_path)
    if rc:
        return rc
    if step == "draft":
        rc = _accept_draft(project, job, raw)
        state.save(project.state_file)
        return rc

    result = analyze.accept(project, job, raw)
    if result.ok:
        a = result.analysis or {}
        job.status, job.next_hint = STATUS_RUNNING, ""
        job.clear_error()
        print(f"{job.company}: 분석을 받아들였습니다")
        print(f"  요약: {a.get('role_summary', '')}")
        print(
            f"  필수 {len(a.get('must', []))} · 우대 {len(a.get('nice', []))} · 업무 {len(a.get('duties', []))}"
            f" · 키워드 {len(a.get('keywords', []))} · 공고에 있는 문항 {len(a.get('questions_found', []))}개"
        )
        for w in result.warnings:
            print(f"  ! {w}")
        print("다음: jobfit run")
        rc = 0
    else:
        job.status, job.next_hint = STATUS_AI, f"재요청 프롬프트 복사 (메뉴 {menunum.PROMPT} · jobfit prep --copy)"
        print(f"{job.company}: 답을 받아들이지 못했습니다" + (f" ({result.attempt}번째)" if result.attempt else ""))
        for e in result.errors:
            print(f"  - {e}")
        if result.retry_path:
            print(f"재요청 프롬프트: {project.rel(result.retry_path)}")
            print("다시: jobfit prep --copy → 웹 채팅(같은 대화에 이어서 붙여도 됨) → 답 전체 복사 → jobfit accept --paste")
        if result.attempt >= 2:
            print("  두 번 넘게 실패했습니다. 다른 AI(ChatGPT·Claude·Gemini)에 처음 프롬프트부터 다시 해 보세요:  jobfit prep --force --copy")
        rc = 1
    state.save(project.state_file)
    return rc


def _accept_draft(project: Project, job, raw: str | None) -> int:
    from jobfit.ai import draft

    phase = job.steps.get("draft", {}).get("phase", "draft")
    result = draft.accept(project, job, raw)
    if not result.ok:
        job.status, job.next_hint = STATUS_AI, f"재요청 프롬프트 복사 (메뉴 {menunum.PROMPT} · jobfit prep --copy)"
        print(f"{job.company}: 초안 답을 받아들이지 못했습니다" + (f" ({result.attempt}번째)" if result.attempt else ""))
        for e in result.errors:
            print(f"  - {e}")
        if result.retry_path:
            print(f"재요청 프롬프트: {project.rel(result.retry_path)}")
            print("다시: jobfit prep --copy → 웹 채팅 → 답 전체 복사 → jobfit accept --paste")
        return 1
    if result.revise_path:
        failing = [ql for ql in result.lints if ql.errors]
        job.status, job.next_hint = STATUS_AI, f"수정 프롬프트 복사 (메뉴 {menunum.PROMPT} · jobfit prep --copy)"
        print(f"{job.company}: 초안을 받았습니다. 검사에 걸린 {len(failing)}개 문항만 한 번 고쳐 받습니다")
        _print_lints(result.lints)
        print(f"수정 프롬프트: {project.rel(result.revise_path)}")
        print("다음: jobfit prep --copy → 웹 채팅(같은 대화도 됨) → 답 전체 복사 → jobfit accept --paste")
        return 0
    job.status, job.next_hint = STATUS_REVIEW, "report.md 확인 후 결정 열에서 지원 여부 결정"
    job.clear_error()
    print(f"{job.company}: {'수정본' if phase == 'revise' else '초안'}을 받아들였습니다")
    _print_lints(result.lints)
    for w in result.warnings:
        print(f"  ! {w}")
    if any(ql.errors for ql in result.lints):
        print("  남은 오류는 report에 ⛔로 표시했습니다 — 직접 고치세요")
    print(f"결과: {project.rel(project.job_dir(job.key) / 'report.md')}")
    print("다음: jobfit run")
    return 0


def _print_lints(lints) -> None:
    for ql in lints:
        limit = f"/{ql.limit}" if ql.limit else ""
        print(f"  {ql.qid}: {ql.chars}{limit}자 · 오류 {len(ql.errors)} · 경고 {len(ql.warnings)} · 확인 필요 {ql.confirm_needed}")
        for issue in ql.errors:
            print(f"    ⛔ {issue.message}")


def _exp_pending(project: Project) -> bool:
    from jobfit.ai import exp_extract

    return bool(exp_extract.pending_items(project))


def _remember_prompt(project: Project, path: Path) -> None:
    """마지막으로 보여 준 프롬프트. 답을 붙여넣을 때(accept --paste) 그 프롬프트의 단계로 보낸다."""
    project.state_dir.mkdir(parents=True, exist_ok=True)
    (project.state_dir / "last_prompt.txt").write_text(project.rel(path), encoding="utf-8")


def _last_prompt(project: Project) -> str | None:
    p = project.state_dir / "last_prompt.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _profile_pending(project: Project) -> bool:
    from jobfit.ai import profile_extract

    return bool(profile_extract.pending_items(project))


def _accept_target(project: Project) -> str | None:
    """단계를 안 주고 답을 붙여넣었을 때 보낼 곳. 마지막으로 보여 준 프롬프트를 따르고, 모르면 경험 카드 → 공고 → 지원서 정보."""
    exp, prof = _exp_pending(project), _profile_pending(project)
    last = _last_prompt(project) or ""
    if exp and last.startswith(project.rel(project.exp_dir)):
        return "exp-extract"
    if prof and last.startswith(project.rel(project.profile_dir)):
        return "profile-extract"
    waiting = _waiting_job(State.load(project.state_file)) is not None
    if "/_ai/" in last and waiting:
        return None
    if exp:
        return "exp-extract"
    if prof and not waiting:
        return "profile-extract"
    return None


def _prep_exp(project: Project, args) -> int:
    """sources/ 를 다시 훑고, 남은 첫 조각의 프롬프트를 보여 준다(--copy 면 클립보드)."""
    from jobfit.ai import exp_extract
    from jobfit.ai.promptfile import copy_to_clipboard

    report = exp_extract.scan(project, force=getattr(args, "force", False))
    _print_scan(project, report)
    if not report.pending:
        return 0 if (report.done or report.skipped) else 1
    item = report.pending[0]
    chars = len(item.prompt.read_text(encoding="utf-8"))
    part = f" 조각 {item.part}/{item.parts}" if item.parts > 1 else ""
    print(f"프롬프트: {project.rel(item.prompt)} ({item.source}{part} · {chars}자 / 상한 {exp_extract.limit()}자)")
    _remember_prompt(project, item.prompt)
    if chars > exp_extract.limit():
        print("  ! 상한을 넘었습니다. 무료 웹 채팅에서 잘릴 수 있습니다 — me/experiences.yaml 카드가 많으면 생길 수 있어요")
    if item.direct:
        print("  ! 스크립트가 읽지 못한 파일입니다 — 에이전트는 원본을 직접 열고, 웹 채팅이라면 이 프롬프트와 함께 그 파일을 첨부하세요")
    return _copy_prompt(item.prompt, args.copy, copy_to_clipboard)


def _accept_exp(project: Project, args) -> int:
    from jobfit.ai import exp_extract

    raw_given = bool(args.paste or args.file)
    prefer = _last_prompt(project)
    item = exp_extract.pick(project, raw_given, prefer)
    if item is None:
        print("경험 카드 답을 기다리는 자료가 없습니다. 먼저  jobfit exp extract")
        return 1
    raw, rc = _read_answer(args, item.prompt)
    if rc:
        return rc
    result = exp_extract.accept(project, raw, prefer=project.rel(item.prompt))
    if not result.ok:
        for e in result.errors:
            print(f"  - {e}")
        if result.item:
            print(f"같은 프롬프트로 다시: {project.rel(result.item.prompt)}  (jobfit prep exp-extract --copy)")
        return 1
    src = result.item.source if result.item else ""
    print(f"{src}: 경험 카드 {len(result.added)}개를 me/experiences.yaml 에 추가했습니다 (모두 verified: false)")
    for card in result.added:
        print(f"  + {card['id']}: {card['title']} · facts {len(card['facts'])}개")
    for w in result.warnings:
        print(f"  ! {w}")
    if result.remaining:
        print(f"남은 자료 조각 {result.remaining}개 — 다음: jobfit prep exp-extract --copy (메뉴 {menunum.PROMPT})")
    else:
        files = exp_extract.source_files(project)
        made = sum(len(i.get("cards", [])) for i in files.values())
        dropped = sum(int(i.get("dropped", 0)) for i in files.values())
        done = sum(1 for i in files.values() if i.get("status") == "done")
        print(f"자료를 모두 카드로 만들었습니다 — 자료 {done}개에서 카드 {made}개" + (f", 원문에 없는 숫자라 뺀 fact {dropped}개" if dropped else ""))
        print("다음: jobfit exp check 로 확인하고, 사실이 맞으면 verified: true 로")
        _print_notices(project, State.load(project.state_file) if project.state_file.exists() else None)
    return 0


def _prep_profile(project: Project, args) -> int:
    """sources/ 를 훑어 지원서 정보가 있을 법한 조각마다 프롬프트를 만들고, 첫 프롬프트를 보여 준다."""
    from jobfit.ai import profile_extract
    from jobfit.ai.promptfile import copy_to_clipboard

    report = profile_extract.scan(project, force=getattr(args, "force", False))
    for rel, note in report.skipped:
        print(f"  건너뜀: {rel} — {note}")
    for rel in report.nothing:
        print(f"  지원서 정보가 없어 보여 건너뜀: {rel}")
    if not report.pending:
        if not _sources(project):
            print("sources/ 에 자료가 없습니다 — 이력서를 넣고 다시")
            return 1
        print("뽑을 지원서 정보가 남아 있지 않습니다" + (" (처리한 자료 그대로)" if report.done else ""))
        return 0
    item = report.pending[0]
    chars = len(item.prompt.read_text(encoding="utf-8"))
    part = f" 조각 {item.part}/{item.parts}" if item.parts > 1 else ""
    print(f"지원서 정보 조각 {len(report.pending)}개 — 프롬프트: {project.rel(item.prompt)} ({item.source}{part} · {chars}자 / 상한 {profile_extract.limit()}자)")
    _remember_prompt(project, item.prompt)
    if item.direct:
        print("  ! 스크립트가 읽지 못한 파일입니다 — 에이전트는 원본을 직접 열고, 웹 채팅이라면 이 프롬프트와 함께 그 파일을 첨부하세요")
    return _copy_prompt(item.prompt, args.copy, copy_to_clipboard)


def _sources(project: Project) -> list:
    from jobfit.ai import exp_extract

    return exp_extract.list_sources(project)


def _accept_profile(project: Project, args) -> int:
    from jobfit.ai import profile_extract

    raw_given = bool(args.paste or args.file)
    item = profile_extract.pick(project, raw_given, _last_prompt(project))
    if item is None:
        print("지원서 정보 답을 기다리는 자료가 없습니다. 먼저  jobfit profile extract")
        return 1
    raw, rc = _read_answer(args, item.prompt)
    if rc:
        return rc
    result = profile_extract.accept(project, raw, prefer=project.rel(item.prompt))
    if not result.ok:
        for e in result.errors:
            print(f"  - {e}")
        if result.item:
            print(f"같은 프롬프트로 다시: {project.rel(result.item.prompt)}  (jobfit prep profile-extract --copy)")
        return 1
    src = result.item.source if result.item else ""
    print(f"{src}: 지원서 정보 {len(result.added)}개를 me/profile.yaml 에 추가했습니다 (모두 verified: false)")
    for it in result.added:
        print(f"  + {it['section']}: {it[_key_field(it['section'])]}")
    for w in result.warnings:
        print(f"  ! {w}")
    if result.remaining:
        print(f"남은 조각 {result.remaining}개 — 다음: jobfit prep profile-extract --copy (메뉴 {menunum.PROMPT})")
    else:
        print("자료에서 지원서 정보를 다 뽑았습니다 — jobfit profile check 로 보고, 사실이 맞으면 verified: true 로")
        _print_notices(project, State.load(project.state_file) if project.state_file.exists() else None)
    return 0


def _key_field(section: str) -> str:
    from jobfit.profile import key_field

    return key_field(section)


def cmd_profile(project: Project, args) -> int:
    from jobfit.profile import SECTIONS, check_profile, load_profile

    _require_init(project)
    if args.profile_command == "extract":
        return _prep_profile(project, args)
    items, problems = load_profile(project.profile_file)
    if problems:
        print(f"  ⛔ {problems[0]}")
        return 1
    if not items:
        print("me/profile.yaml 에 항목이 없습니다 — jobfit profile extract 로 sources/ 의 이력서에서 뽑거나, me.example/profile.yaml 을 보고 직접 쓰세요")
        return 0
    counts = {s: sum(1 for i in items if str(i.get("section", "")).strip() == s) for s in SECTIONS}
    print("지원서 정보: " + " · ".join(f"{s} {n}" for s, n in counts.items() if n))
    issues = check_profile(items)
    unverified = [i for i in issues if "verified" in i.message]
    for issue in issues:
        if issue not in unverified:
            print(f"  {'⛔' if issue.level == '오류' else '⚠'} {issue.where}: {issue.message}")
    if unverified:
        print(f"  [확인 전] {len(unverified)}개 — 입력표에 [확인 전]으로 나옵니다. 사실을 확인한 뒤 verified: true 로")
    return 1 if any(i.level == "오류" for i in issues) else 0


def cmd_apply(project: Project, args) -> int:
    """지원서 입력표 — 지원서 화면에 옮길 값(지원서 정보·자소서 초안·보유 기술)을 한 장으로."""
    from jobfit.apply import write_sheet

    _require_init(project)
    state = State.load(project.state_file)
    pipeline.sync_in(project, state)
    state.save(project.state_file)
    job = _pick_job(state, args.job)
    if job is None:
        return 1
    path, notes = write_sheet(project, job)
    print(f"{job.company}: 지원서 입력표 {project.rel(path)}")
    for n in notes:
        print(f"  ! {n}")
    print("신원 정보(이름·연락처·주소·생년월일)는 입력표에 없습니다 — 지원서에서 직접 입력하고, 제출은 직접 하세요")
    return 0


def _print_notices(project: Project, state) -> None:
    """사람이 해야 할 일 묶음(notices). 에이전트가 요약하다 빠뜨리지 않게 따로 낸다."""
    from jobfit import notices

    for line in notices.render(notices.collect(project, state)):
        print(line)


def _print_scan(project: Project, report) -> None:
    for rel in report.new:
        print(f"  새 자료: {rel}")
    for rel in report.changed:
        print(f"  바뀐 자료: {rel}")
    for rel, why in report.skipped:
        print(f"  ! 건너뜀: {rel} — {why}")
    if not (report.new or report.changed or report.pending or report.done or report.skipped):
        print(f"{project.rel(project.sources_dir)}/ 에 자료가 없습니다. 이력서·포트폴리오·프로젝트 문서를 넣어 주세요(형식 무관)")
    elif report.pending:
        print(f"경험 카드 뽑기를 기다리는 조각 {len(report.pending)}개")
    elif report.done:
        print(f"모든 자료를 카드로 만들었습니다({len(report.done)}개). 다시 뽑으려면 --force")


def _prep_style(project: Project, args) -> int:
    from jobfit.ai import style_rules
    from jobfit.ai.promptfile import copy_to_clipboard

    path, note = style_rules.prepare(project)
    if path is None:
        print(note)
        return 1
    print(f"문체 규칙 프롬프트: {project.rel(path)} ({note})")
    return _copy_prompt(path, args.copy, copy_to_clipboard)


def _accept_style(project: Project, args) -> int:
    from jobfit.ai import style_rules

    raw, rc = _read_answer(args, style_rules.work_dir(project) / style_rules.FILE_PROMPT)
    if rc:
        return rc
    result = style_rules.accept(project, raw)
    if not result.ok:
        for e in result.errors:
            print(f"  - {e}")
        return 1
    print(f"문체 규칙 초안을 저장했습니다: {project.rel(result.path)}")
    for w in result.warnings:
        print(f"  ! {w}")
    print("읽고 고친 뒤 맨 위 안내 줄을 지우면 확정입니다. 다음 초안부터 반영됩니다")
    _print_notices(project, None)
    return 0


def cmd_style(project: Project, args) -> int:
    """문체 통계 — me/samples/ 의 자소서에서 계산해 me/style/style_stats.json 에 쓴다. AI 없음."""
    import json

    from jobfit.style import MIN_SAMPLES_FOR_LINT, compute_stats, list_samples, read_sample, stats_lines, style_dir

    samples = list_samples(project.me_dir)
    if not samples:
        print("me/samples/ 에 내가 쓴 자소서(.txt · .md · .docx · .hwpx)를 넣어 주세요")
        return 1
    texts = []
    for p in samples:
        text = read_sample(p)
        if text.strip():
            texts.append(text)
        else:
            print(f"  ! 글자를 읽지 못해 뺐습니다: {p.name}")
    stats = compute_stats(texts)
    out = style_dir(project.me_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "style_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"샘플 {stats['samples']}개 · 문장 {stats['sentences']}개 → {project.rel(out / 'style_stats.json')}")
    for line in stats_lines(stats):
        print(f"  {line[2:]}")
    if stats["samples"] < MIN_SAMPLES_FOR_LINT:
        print(f"  샘플이 {MIN_SAMPLES_FOR_LINT}개 미만이라 초안의 문체 검사는 하지 않습니다(프롬프트에는 수치가 들어갑니다)")
    return 0


def _pending_ai_step(job) -> str | None:
    """다음에 할 단계가 AI 단계면 그 이름."""
    from jobfit.state import STEPS

    for s in STEPS:
        if not job.step_done(s):
            return s if s in pipeline.AI_PREPARERS else None
    return None


def _read_answer(args, prompt_path: Path) -> tuple[str | None, int]:
    """(답 글, 종료 코드). --paste 면 클립보드, --file 이면 파일, 둘 다 아니면 None(답 파일을 그대로 쓴다)."""
    from jobfit.ai.promptfile import read_clipboard

    if args.paste:
        raw = read_clipboard()
        if raw is None:
            print("클립보드를 읽을 수 없습니다. 답을 텍스트 파일로 저장한 뒤  jobfit accept --file <파일>")
            return None, 1
        if not raw.strip():
            print("클립보드가 비어 있습니다. 웹 채팅의 답을 전체 복사했는지 확인하세요")
            return None, 1
        if prompt_path.exists() and raw.strip() == prompt_path.read_text(encoding="utf-8").strip():
            print("클립보드에 보낸 프롬프트가 그대로 있습니다. 웹 채팅의 '답'을 복사해 주세요")
            return None, 1
        return raw, 0
    if args.file:
        return Path(args.file).read_text(encoding="utf-8", errors="replace"), 0
    return None, 0


def _copy_prompt(path: Path, copy: bool, copier) -> int:
    if not copy:
        return 0
    if not copier(path.read_text(encoding="utf-8")):
        print("클립보드에 복사하지 못했습니다. 위 파일을 메모장으로 열어 전체 복사하세요")
        return 1
    print(f"클립보드에 복사했습니다. 웹 채팅에 붙여넣고, 답이 나오면 전체를 복사한 뒤  jobfit accept --paste (메뉴 {menunum.ANSWER})")
    return 0


def cmd_export(project: Project, args) -> int:
    """Notion 내보내기. 기본은 CSV + report.md 가져오기 안내. --api 는 v2."""
    _require_init(project)
    if args.api:
        print("Notion API 내보내기는 v2에서 제공됩니다. 지금은 CSV 가져오기나 에이전트의 Notion 커넥터를 쓰세요")
        return 2
    from jobfit.export import export_csv

    state = State.load(project.state_file)
    pipeline.sync_in(project, state)
    state.save(project.state_file)
    path = export_csv(project, state)
    reports = [project.rel(project.job_dir(j.key) / "report.md") for j in state.jobs.values() if (project.job_dir(j.key) / "report.md").exists()]
    print(f"공고 목록 CSV: {project.rel(path)} ({len(state.jobs)}건)")
    print("  Notion → 페이지에서 '가져오기' → CSV → 이 파일 — 공고 목록 데이터베이스가 됩니다")
    if reports:
        print("공고별 정리(report.md) — Notion '가져오기' → 텍스트·마크다운으로 하나씩:")
        for r in reports:
            print(f"  {r}")
    return 0


def cmd_usage(project: Project, args) -> int:
    """공고별 프롬프트·답 길이 집계."""
    from jobfit.ai.promptfile import read_usage

    records = read_usage(project)
    if not records:
        print("기록이 없습니다")
        return 0
    state = State.load(project.state_file) if project.state_file.exists() else State()
    agg: dict[tuple[str, str], dict] = {}
    for r in records:
        v = agg.setdefault((r.get("job", ""), r.get("step", "")), {"prompt": [], "response": [], "cached": 0})
        kind = r.get("kind")
        if kind == "cached":
            v["cached"] += 1
        elif kind in ("prompt", "response"):
            v[kind].append(int(r.get("chars", 0)))
    total = 0
    for (key, step), v in agg.items():
        job = state.get(key)
        name = job.company if job else key
        total += sum(v["prompt"]) + sum(v["response"])
        print(
            f"{name}  {step}: 프롬프트 {len(v['prompt'])}회(마지막 {v['prompt'][-1] if v['prompt'] else 0}자)"
            f" · 답 {len(v['response'])}회(마지막 {v['response'][-1] if v['response'] else 0}자)"
            + (f" · 캐시로 생략 {v['cached']}회" if v["cached"] else "")
        )
    print(f"합계 {total:,}자")
    return 0


def cmd_unimplemented(project: Project, args) -> int:
    print(f"'{args.command}' 명령은 {UNIMPLEMENTED[args.command]}에서 구현됩니다")
    return 2


# --- 도우미 ---


def _single_step(project: Project, args, step: str) -> int:
    _require_init(project)
    state = State.load(project.state_file)
    pipeline.sync_in(project, state)
    jobs = [_pick_job(state, args.job)] if args.job else state.active_jobs()
    if args.job and jobs[0] is None:
        return 1
    rc = 0
    for job in jobs:
        need = _PREREQ.get(step)
        if need and not job.step_done(need) and not args.job:
            print(f"  {job.company}: 건너뜀 ({need} 단계가 아직)")
            continue
        job.reset_from(step)
        if step == "fetch":
            pipeline.forget_fetch(project.job_dir(job.key))  # 명시적으로 부르면 새로 받는다
        ok, message = pipeline._STEP_RUNNERS[step](project, job, project.job_dir(job.key))
        print(f"  {job.company}: {'완료' if ok else '실패 — ' + message}")
        rc |= 0 if ok else 1
    if step in ("match", "report"):
        print(pipeline.refresh_dashboard(project, state))
    state.save(project.state_file)
    return rc


def _waiting_job(state: State):
    """AI 답을 기다리는 공고 중 jobs.xlsx에서 가장 위에 있는 것. 한 번에 하나씩 끝까지 처리한다."""
    waiting = sorted((j for j in state.active_jobs() if j.status == STATUS_AI), key=lambda j: j.row)
    return waiting[0] if waiting else None


def _pick_job(state: State, token: str):
    hits = state.find(token)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"공고를 찾지 못했습니다: {token}  (jobfit sync-in 뒤에 다시)")
    else:
        print(f"여럿이 맞습니다: " + ", ".join(f"{j.key}({j.company})" for j in hits))
    return None


def _print_sync(report: pipeline.SyncReport, brief: bool = False) -> None:
    r = report.read
    if not brief or r.errors:
        print(f"jobs.xlsx: 공고 {len(r.jobs)}건, 문항 {len(r.questions)}건" + (f", 문제 행 {len(r.errors)}건" if r.errors else ""))
    for err in r.errors:
        print(f"  ! {err}")
    for row, dup in report.duplicates:
        print(f"  ! [공고 {row.row}행] {dup.row}행과 같은 공고입니다 ({dup.key}) — 건너뜀")
    for q in report.unmatched_questions:
        print(f"  ! [문항 {q.row}행] '{q.job_ref}'에 맞는 공고가 없거나 여럿입니다. '회사명 / 직무'로 적어 주세요")
    if report.added:
        print("  새 공고: " + ", ".join(f"{j.company}({j.key})" for j in report.added))
    if report.removed:
        print("  지운 공고(결과 폴더는 둠): " + ", ".join(j.company for j in report.removed))
    for job, task in report.tasks:
        print(f"  작업 지시: {job.company} — {task}")


def _require_init(project: Project) -> None:
    if not project.is_initialized():
        print(f"이 폴더에 jobs.xlsx가 없습니다. 먼저  jobfit init  을 실행하세요 ({project.root})")
        raise SystemExit(2)


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobfit", description="공고 기반 자소서 파이프라인 — 스크립트가 보장하고 AI는 판단만 한다")
    p.add_argument("--root", help="프로젝트 폴더(기본: 현재 폴더)")
    p.add_argument("--version", action="version", version=f"jobfit {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="jobs.xlsx, me/, output/ 만들기").set_defaults(func=cmd_init)

    add = sub.add_parser("add", help="jobs.xlsx 공고 시트에 한 줄 추가 (링크만 주면 회사명·직무·마감일을 페이지에서 읽음)")
    add.add_argument("url", nargs="?", default="", help="공고 링크")
    add.add_argument("--company", help="회사명")
    add.add_argument("--title", help="직무")
    add.add_argument("--deadline", help="마감일 (2026-11-30)")
    add.add_argument("--memo", help="메모")
    add.add_argument("--apply-via", choices=APPLY_VIAS, help="지원방법 (비우면 공고 페이지에서 읽음)")
    add.set_defaults(func=cmd_add)
    sub.add_parser("check", help="jobs.xlsx 검사만").set_defaults(func=cmd_check)
    sub.add_parser("sync-in", help="jobs.xlsx 읽어 상태에 반영").set_defaults(func=cmd_sync_in)
    sub.add_parser("next", help="다음에 할 일 하나").set_defaults(func=cmd_next)

    run = sub.add_parser("run", help="AI 단계 직전까지 진행")
    run.add_argument("--job", help="공고키·공고번호·회사명")
    run.set_defaults(func=cmd_run)

    for name, fn, help_ in (
        ("fetch", cmd_fetch, "공고 원문 받기"),
        ("clean", cmd_clean, "정제·키워드 추출"),
        ("match", cmd_match, "경험 카드와 매칭 (match.json)"),
        ("report", cmd_report, "공고별 정리 (report.md)"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--job", help="공고키·공고번호·회사명")
        sp.set_defaults(func=fn)

    sub.add_parser("dashboard", help="dashboard.xlsx 다시 만들기").set_defaults(func=cmd_dashboard)

    exp = sub.add_parser("exp", help="경험 카드 (me/experiences.yaml)")
    exp_sub = exp.add_subparsers(dest="exp_command", required=True)
    exp_sub.add_parser("check", help="카드 검사").set_defaults(func=cmd_exp)
    exp_extract = exp_sub.add_parser("extract", help="sources/ 의 내 자료로 경험 카드 뽑기 프롬프트 만들기 (AI 단계)")
    exp_extract.add_argument("--copy", action="store_true", help="첫 프롬프트를 클립보드에 복사")
    exp_extract.add_argument("--force", action="store_true", help="이미 처리한 자료도 다시")
    exp_extract.set_defaults(func=cmd_exp)
    exp_new = exp_sub.add_parser("new", help="빈 카드 하나 추가")
    exp_new.add_argument("--id", help="카드 id (기본 exp-<번호>)")
    exp_new.set_defaults(func=cmd_exp)

    profile = sub.add_parser("profile", help="지원서 정보 (me/profile.yaml — 학력·자격증·어학 …)")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    p_extract = profile_sub.add_parser("extract", help="sources/ 의 내 자료로 지원서 정보 뽑기 프롬프트 만들기 (AI 단계)")
    p_extract.add_argument("--copy", action="store_true", help="첫 프롬프트를 클립보드에 복사")
    p_extract.add_argument("--force", action="store_true", help="이미 처리한 자료도 다시")
    p_extract.set_defaults(func=cmd_profile)
    profile_sub.add_parser("check", help="지원서 정보 검사").set_defaults(func=cmd_profile)

    apply = sub.add_parser("apply", help="지원서 입력표")
    apply_sub = apply.add_subparsers(dest="apply_command", required=True)
    a_sheet = apply_sub.add_parser("sheet", help="공고의 지원서 입력표 만들기 (output/<공고>/apply/fill.md)")
    a_sheet.add_argument("--job", required=True, help="공고키·회사명")
    a_sheet.set_defaults(func=cmd_apply)

    question = sub.add_parser("question", help="자소서 문항 (jobs.xlsx 문항 시트)")
    question_sub = question.add_subparsers(dest="question_command", required=True)
    q_add = question_sub.add_parser("add", help="문항 추가 — 엑셀 없이. 문항은 원문 그대로")
    q_add.add_argument("--job", required=True, help="공고키·회사명")
    q_add.add_argument("--text", action="append", required=True, help="문항 원문 (여러 개면 --text 를 반복)")
    q_add.add_argument("--limit", action="append", type=int, help="글자수 (문항 순서대로, 0이면 제한 없음)")
    q_add.add_argument("--no-spaces", action="store_true", help="글자수를 공백 빼고 센다")
    q_add.set_defaults(func=cmd_question_add)

    sub.add_parser("menu", help="start.bat·start.sh 메뉴 (사람용 화면)").set_defaults(func=cmd_menu)

    posting = sub.add_parser("posting", help="공고 본문 붙여넣기(수집 실패한 공고용)")
    posting.add_argument("--job", required=True, help="공고키·공고번호·회사명")
    src = posting.add_mutually_exclusive_group()
    src.add_argument("--from-clipboard", action="store_true", help="클립보드에서(기본)")
    src.add_argument("--file", help="텍스트 파일에서")
    posting.set_defaults(func=cmd_posting)

    prep = sub.add_parser("prep", help="AI 프롬프트 만들기 (붙여넣기 모드: --copy 로 클립보드에)")
    prep.add_argument("step", nargs="?", choices=list(AI_STEPS), help="기본 analyze")
    prep.add_argument("--job", help="공고키·공고번호·회사명 (기본: AI 답을 기다리는 첫 공고)")
    prep.add_argument("--copy", action="store_true", help="클립보드에 복사")
    prep.add_argument("--force", action="store_true", help="캐시·이전 결과를 무시하고 다시")
    prep.set_defaults(func=cmd_prep)

    accept = sub.add_parser("accept", help="AI 답 받아들이기 (붙여넣기 모드: --paste)")
    accept.add_argument("step", nargs="?", choices=list(AI_STEPS), help="기본 analyze")
    accept.add_argument("--job", help="공고키·공고번호·회사명 (기본: AI 답을 기다리는 첫 공고)")
    src_a = accept.add_mutually_exclusive_group()
    src_a.add_argument("--paste", action="store_true", help="클립보드에서")
    src_a.add_argument("--file", help="텍스트 파일에서")
    accept.set_defaults(func=cmd_accept)

    sub.add_parser("usage", help="공고별 프롬프트·답 길이 집계").set_defaults(func=cmd_usage)

    export = sub.add_parser("export", help="내보내기")
    export_sub = export.add_subparsers(dest="export_target", required=True)
    notion = export_sub.add_parser("notion", help="Notion용 공고 목록 CSV + report.md 가져오기 안내")
    notion_mode = notion.add_mutually_exclusive_group()
    notion_mode.add_argument("--csv", action="store_true", help="CSV (기본)")
    notion_mode.add_argument("--api", action="store_true", help="Notion API로 직접 (v2)")
    notion.set_defaults(func=cmd_export)

    style = sub.add_parser("style", help="문체 (me/samples → me/style)")
    style_sub = style.add_subparsers(dest="style_command", required=True)
    style_sub.add_parser("stats", help="자소서 샘플에서 문체 수치 계산").set_defaults(func=cmd_style)

    for name in UNIMPLEMENTED:
        sp = sub.add_parser(name, help=f"({UNIMPLEMENTED[name]}에서 구현)")
        sp.add_argument("rest", nargs=argparse.REMAINDER)
        sp.set_defaults(func=cmd_unimplemented)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
