"""진단 정보 — 막혔을 때 이슈에 붙여넣을 짧은 한 덩어리.

내는 것: jobfit · OS · Python · uv 버전, 폴더가 갖춰졌는지, 공고가 어느 단계까지 갔는지, 마지막 오류의 종류.
내지 않는 것: 이름 · 연락처 · 주소 · 생년월일, me/ 와 sources/ 의 내용, 자소서 · 초안 글, 공고 본문 · 회사명 · 공고 링크.

규칙 하나로 줄이면 **개수와 있고 없음만**이다. 파일 안의 값은 한 글자도 옮기지 않는다.
경로도 그대로 쓰지 않는다 — 홈 디렉터리는 `~`, 중간 폴더 이름은 뺀다(사람 이름이 들어 있을 수 있다).
오류도 원문을 쓰지 않고 아래 표의 종류 이름으로만 바꿔 적는다.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from jobfit import __version__
from jobfit.paths import Project
from jobfit.state import STEPS, State

HEADER = "----- jobfit 진단 정보 (여기부터 복사) -----"
FOOTER = "----- 여기까지 -----"
NOTE = "# 개수와 있고 없음만 적습니다. 이름·연락처·회사명·공고 링크·자소서 글은 들어 있지 않습니다."

YES, NO = "있음", "없음"

# 오류 원문 -> 이슈에 적을 종류. 원문에는 회사명·링크가 섞일 수 있어 그대로 쓰지 않는다.
# 짝이 되는 글귀는 fetch/fetcher.py · fetch/company.py · clean/posting.py · pipeline.py 에 있다.
_ERROR_KINDS: tuple[tuple[str, str], ...] = (
    ("인터넷 연결 문제로 받지 못했습니다", "인터넷 연결 문제"),
    ("robots.txt", "robots.txt 가 자동 수집을 막음"),
    ("마감돼 내려간 공고입니다", "마감돼 내려간 공고"),
    ("403", "403 접근 차단"),
    ("404", "404 공고 없음"),
    ("로그인이 필요", "로그인이 필요한 페이지"),
    ("사이트 쪽 오류", "사이트 쪽 오류(5xx)"),
    ("안에 응답이 없었습니다", "응답 시간 초과"),
    ("PDF", "PDF 공고라 읽지 못함"),
    ("공고 본문이 없습니다", "공고 본문 없음"),
    ("링크가 없습니다", "링크 없음"),
    ("받지 못했습니다", "그 밖의 받기 실패"),
)
UNKNOWN_ERROR = "분류되지 않은 오류"


def error_kind(message: str) -> str:
    """오류 메시지를 정해진 종류 이름 하나로. 모르는 메시지는 원문을 내지 않고 '분류되지 않은 오류'."""
    for mark, kind in _ERROR_KINDS:
        if mark in message:
            return kind
    return UNKNOWN_ERROR


def home_dir() -> Path:
    try:
        return Path.home()
    except (OSError, RuntimeError):  # pragma: no cover — 홈이 없는 환경
        return Path("/")


def scrub(text: str) -> str:
    """글에 남은 홈 디렉터리 경로를 ~ 로. 마지막에 한 번 더 거는 안전망."""
    home = home_dir()
    for form in (str(home), home.as_posix()):
        if len(form) > 1:
            text = text.replace(form, "~")
    return text


def folder_label(root: Path) -> str:
    """작업 폴더를 사람 이름 없이. 홈 안이면 ~/…, 밖이면 드라이브와 마지막 폴더만."""
    home = home_dir()
    try:
        rel = root.relative_to(home)
        return "~/" + rel.as_posix() if rel.parts else "~"
    except ValueError:
        pass
    parts = root.parts
    if len(parts) <= 1:
        return "/"
    drive = parts[0].replace("\\", "/").rstrip("/")
    return f"{drive}/…/{parts[-1]}"


def os_line() -> str:
    system = platform.system()
    machine = platform.machine() or "알 수 없음"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()}) {machine}"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()} {machine}"
    return f"{system or '알 수 없음'} {platform.release()} {machine}"


def uv_version() -> str:
    """`uv --version` 한 줄. uv 가 없거나 물어볼 수 없으면 그렇다고만 적는다."""
    exe = shutil.which("uv") or os.environ.get("UV", "")
    if not exe:
        return "없음 (PATH 에 uv 가 없습니다)"
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "알 수 없음 (실행하지 못했습니다)"
    out = (done.stdout or done.stderr or "").strip().splitlines()
    if not out:
        return "알 수 없음"
    return scrub(out[0].strip()).removeprefix("uv ")


def _count(items) -> int:
    return sum(1 for _ in items)


def _mark(path: Path) -> str:
    return YES if path.exists() else NO


def _me_line(project: Project) -> str:
    from jobfit.emphasis import load_resume
    from jobfit.experiences import is_verified, load_cards
    from jobfit.profile import load_profile
    from jobfit.style import is_draft_rules, load_rules

    cards, card_problems = load_cards(project.experiences_file)
    resume, _ = load_resume(project.resume_file)
    prof, _ = load_profile(project.profile_file)
    verified = _count(c for c in cards if is_verified(c))
    rules = load_rules(project.me_dir).strip()
    if not rules:
        rules_state = NO
    elif is_draft_rules(project.me_dir):
        rules_state = "AI 초안 그대로"
    else:
        rules_state = "확정"
    parts = [
        f"경험 카드 {len(cards)}개(확인 {verified}개)",
        f"이력서 항목 {len(resume)}개",
        f"지원서 정보 {len(prof)}개",
        f"문체 규칙 {rules_state}",
    ]
    if card_problems:
        parts.append("experiences.yaml 을 읽지 못함")
    return " · ".join(parts)


def _sources_line(project: Project) -> str:
    from jobfit.ai.exp_extract import list_sources
    from jobfit.style import list_samples

    def how_many(lister) -> str:
        try:
            return f"{len(lister(project))}개"
        except OSError:
            return "알 수 없음"

    return f"자료 {how_many(list_sources)} · 원안 자소서 {how_many(list_samples)}"


def _job_dirs(project: Project) -> list[Path]:
    if not project.output_dir.is_dir():
        return []
    return [d for d in sorted(project.output_dir.iterdir()) if d.is_dir() and not d.name.startswith("_")]


def _jobs_lines(project: Project, state: State) -> list[str]:
    jobs = list(state.jobs.values())
    out = [f"공고: {len(jobs)}건 · 결과 폴더 {len(_job_dirs(project))}개"]
    if jobs:
        status: dict[str, int] = {}
        for job in jobs:
            status[job.status] = status.get(job.status, 0) + 1
        out.append("처리상태: " + " · ".join(f"{k} {v}건" for k, v in sorted(status.items())))
        done = {step: _count(j for j in jobs if j.step_done(step)) for step in STEPS}
        out.append("단계를 끝낸 공고: " + " · ".join(f"{step} {n}" for step, n in done.items()))
    reports = _count(d for d in _job_dirs(project) if (d / "report.md").exists())
    drafts = _count(d for d in _job_dirs(project) if (d / "drafts").is_dir())
    out.append(f"결과물: report.md {reports}개 · 초안 폴더 {drafts}개")
    return out


def _error_lines(state: State) -> list[str]:
    kinds: dict[str, int] = {}
    for job in state.jobs.values():
        if job.error:
            kind = error_kind(job.error)
            kinds[kind] = kinds.get(kind, 0) + 1
    out = []
    if kinds:
        out.append("마지막 오류: " + " · ".join(f"{k} {n}건" for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))))
    else:
        out.append("마지막 오류: 없음")
    if state.sheet_errors:
        out.append(f"jobs.xlsx 에서 읽지 못한 줄: {len(state.sheet_errors)}개")
    return out


def build(project: Project) -> str:
    """이슈에 그대로 붙여넣을 한 덩어리."""
    root = project.root
    try:
        state = State.load(project.state_file)
    except (OSError, ValueError):
        state = State()
        state_note = "읽지 못함"
    else:
        state_note = _mark(project.state_file)

    lines = [
        HEADER,
        NOTE,
        f"jobfit: {__version__}",
        f"OS: {os_line()}",
        f"Python: {platform.python_version()}",
        f"uv: {uv_version()}",
        f"인코딩: 화면 {getattr(sys.stdout, 'encoding', None) or '알 수 없음'} · 기본 {sys.getdefaultencoding()}",
        f"작업 폴더: {folder_label(root)} · 쓰기 {'가능' if os.access(root, os.W_OK) else '불가'}",
        "갖춰짐: "
        + " · ".join(
            [
                f"jobs.xlsx {_mark(project.jobs_xlsx)}",
                f"me/ {_mark(project.me_dir)}",
                f"sources/ {_mark(project.sources_dir)}",
                f"sources/자소서/ {_mark(project.samples_dir)}",
                f"output/ {_mark(project.output_dir)}",
                f".jobfit/ {_mark(project.state_dir)}",
                f"상태 파일 {state_note}",
                f"dashboard.xlsx {_mark(project.dashboard_xlsx)}",
            ]
        ),
        f"me/: {_me_line(project)}",
        f"sources/: {_sources_line(project)}",
        *_jobs_lines(project, state),
        *_error_lines(state),
        FOOTER,
    ]
    return scrub("\n".join(lines))


def cmd_diag(project: Project, args) -> int:
    """`jobfit diag` — 진단 정보를 화면에 내고, --copy 면 클립보드에도."""
    from jobfit.ai.promptfile import copy_to_clipboard

    text = build(project)
    print(text)
    if not getattr(args, "copy", False):
        print("\n위 '여기부터 복사'~'여기까지'를 그대로 이슈에 붙여넣으세요 (클립보드로:  jobfit diag --copy)")
        return 0
    if not copy_to_clipboard(text):
        print("\n클립보드에 복사하지 못했습니다. 위 글을 직접 선택해 복사하세요")
        return 1
    print("\n클립보드에 복사했습니다. GitHub 이슈의 '진단 정보' 칸에 붙여넣으세요")
    return 0
