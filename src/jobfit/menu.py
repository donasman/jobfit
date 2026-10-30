"""start.bat·start.sh 메뉴 — 사람이 보는 화면. 색·상자·표는 rich, 일은 jobfit 명령(cli.main)이 한다.

번호를 고르면 그 명령을 돌리고, 끝에 '사람이 직접 할 일'(노란 상자)과 '에이전트에게 보낼 말'(초록 상자,
클립보드에 복사)을 보여 준다. 명령의 출력 형식은 바꾸지 않는다 — 에이전트가 직접 실행할 때 보는 글은
그대로이고, 꾸밈은 이 메뉴 안에서만 한다.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from jobfit import handoff, menunum, pipeline
from jobfit.notices import HEADER
from jobfit.paths import Project
from jobfit.state import STATUS_ERROR, State

M = menunum
MAIN_MENU: list[tuple[str, list[tuple[int, str, str]]]] = [
    ("공고", [
        (M.ADD, "공고 추가", "링크 하나를 jobs.xlsx 에"),
        (M.QUESTIONS, "문항 넣기", "자소서 문항을 jobs.xlsx 에"),
        (M.RUN, "공고 처리 진행", "받기·정리·매칭 — AI 단계 직전까지"),
        (M.POSTING, "공고 본문 붙여넣기", "받기에 실패한 공고"),
        (M.NEXT, "다음 할 일 보기", "에이전트에게 보낼 말도 함께"),
        (M.OUTPUT, "결과 폴더 열기", "공고별 report.md"),
        (M.SHEET, "지원서 입력표", "학력·자격증·자소서를 한 장으로"),
    ]),
    ("내 자료", [
        (M.ME, "내 자료 관리  ›", "경험 카드 · 지원서 정보 · 문체"),
        (M.CHECK, "jobs.xlsx 검사", ""),
    ]),
    ("웹 채팅에 직접 붙여넣기 (에이전트 없이)", [
        (M.PROMPT, "AI 프롬프트 복사", "클립보드로"),
        (M.ANSWER, "AI 답 붙여넣기", "저장·검사하고 이어서 진행"),
    ]),
]
ME_MENU: list[tuple[str, list[tuple[int, str, str]]]] = [
    ("경험 카드", [
        (1, "sources 폴더 열기", "이력서·포트폴리오를 넣는 곳"),
        (2, "경험 카드 만들기 준비", "sources 를 읽어 AI 단계 직전까지"),
        (3, "경험 카드 검사", ""),
        (4, "빈 경험 카드 추가", ""),
    ]),
    ("지원서 정보 (학력·경력·자격증·어학 …)", [
        (5, "지원서 정보 만들기 준비", "이력서를 읽어 AI 단계 직전까지"),
        (6, "지원서 정보 검사", ""),
    ]),
    ("원안 자소서 (초안이 따를 문체·흐름)", [
        (7, "원안 자소서 분석 준비", "sources/자소서 의 잘 쓴 자소서"),
        (8, "문체 규칙: 프롬프트 복사", "웹 채팅용"),
        (9, "문체 규칙: 답 붙여넣기", "웹 채팅용"),
    ]),
    ("", [
        (10, "me 폴더 열기", ""),
    ]),
]
ME_NOTE = f"웹 채팅에서는 경험 카드·지원서 정보 프롬프트도\n메인 메뉴 {M.PROMPT}번(복사)·{M.ANSWER}번(붙여넣기)으로"
STYLE_AGENT = (
    "sources/자소서 의 원안 자소서로 문체 수치를 계산했어. `uv run jobfit prep style-rules`로 문체 규칙 프롬프트를 만들고, "
    "그 프롬프트대로 답을 쓴 뒤 `uv run jobfit accept style-rules`까지 해줘. 초안을 기다리던 공고가 있으면 `uv run jobfit run`으로 이어서."
)

Ask = Callable[[str], str]
Copy = Callable[[str], bool]


def run_menu(project: Project, console: Console | None = None, ask: Ask | None = None, copy: Copy | None = None) -> int:
    # 명령 출력을 잠시 가로채는 동안에도 화면은 진짜 표준 출력에 그리도록 지금의 stdout 에 묶어 둔다
    ui = _Menu(project, console or Console(file=sys.stdout, highlight=False), ask, copy or _clipboard)
    try:
        return ui.loop()
    except (EOFError, KeyboardInterrupt):
        ui.console.print()
        return 0


class _Menu:
    def __init__(self, project: Project, console: Console, ask: Ask | None, copy: Copy) -> None:
        self.project = project
        self.console = console
        self.ask_fn = ask or (lambda prompt: console.input(prompt))
        self.copy = copy

    # --- 화면 ---

    def loop(self) -> int:
        if not self.project.is_initialized() and not self._first_setup():
            return 0
        while True:
            self._draw(MAIN_MENU, "jobfit", self._summary(), "끝내기")
            choice = self._ask_number()
            if choice == 0:
                return 0
            self.console.rule(style="dim")
            self._main(choice)

    def _draw(self, menu, title: str, subtitle: str, zero: str, note: str = "") -> None:
        # 구역마다 표를 따로 만들되 열 너비를 같게 — 긴 구역 제목이 이름 열을 넓히지 않게 제목은 표 밖에 둔다
        name_width = max(cell_len(name) for _, items in menu for _, name, _ in items)
        parts: list = []
        for i, (section, items) in enumerate([*menu, ("", [(0, zero, "")])]):
            if section:
                parts.append(Text(section, style="bold magenta"))
            elif i == len(menu):
                parts.append(Text(""))  # 0(끝내기·돌아가기) 앞 빈 줄
            grid = Table.grid(padding=(0, 2))
            grid.add_column(justify="right", style="bold cyan", width=3)
            grid.add_column(style="bold", width=name_width)
            grid.add_column(style="dim")
            for number, name, desc in items:
                grid.add_row(str(number), Text(name, style="dim") if number == 0 else name, desc)
            parts.append(grid)
        if note:
            parts += [Text(""), Text(note, style="dim")]
        self.console.print()
        self.console.print(Panel(Group(*parts), title=f"[bold]{title}[/]", subtitle=subtitle, subtitle_align="left", border_style="cyan", expand=False))

    def _summary(self) -> str:
        state = self._state()
        jobs = state.active_jobs()
        if not jobs:
            return "공고 없음"
        counts = Counter(j.status for j in jobs)
        today = date.today().isoformat()
        due = sum(1 for j in jobs if j.deadline == today)
        parts = [f"공고 {len(jobs)}개"] + [f"{status} {n}" for status, n in counts.items()] + ([f"오늘 마감 {due}"] if due else [])
        return " · ".join(parts)

    def _ask_number(self) -> int | None:
        raw = self._ask("[bold]번호[/] › ")
        try:
            return int(raw)
        except ValueError:
            if raw:
                self.console.print(f"[yellow]'{raw}' 은(는) 메뉴에 없는 번호입니다[/]")
            return None

    def _ask(self, prompt: str) -> str:
        return self.ask_fn(prompt).strip()

    def _yes(self, prompt: str) -> bool:
        return self._ask(f"{prompt} [dim](Enter=예, n=아니오)[/] › ").lower() not in ("n", "no", "아니오", "아니")

    # --- 메뉴 ---

    def _main(self, choice: int | None) -> None:
        actions = {
            M.ADD: self._add,
            M.QUESTIONS: self._questions,
            M.RUN: lambda: self._step(["run"], "공고 처리 중…", "공고 처리를 AI 단계 직전까지 진행해 뒀어."),
            M.POSTING: self._posting,
            M.NEXT: lambda: self._step(["next"], "", "", body=False),
            M.OUTPUT: lambda: _open(self.project.output_dir),
            M.SHEET: self._sheet,
            M.ME: self._me,
            M.CHECK: lambda: self._step(["check"], "", ""),
            M.PROMPT: lambda: self._call(["prep", "--copy"]),
            M.ANSWER: self._answer,
        }
        action = actions.get(choice) if choice is not None else None
        if action is None:
            return
        action()

    def _me(self) -> None:
        while True:
            self._draw(ME_MENU, "내 자료", "", "돌아가기", ME_NOTE)
            choice = self._ask_number()
            if choice == 0:
                return
            self.console.rule(style="dim")
            if choice == 1:
                _open(self.project.sources_dir)
                self._hint("자료를 넣은 뒤 2번(경험 카드 만들기 준비)·5번(지원서 정보 만들기 준비)을 누르면 에이전트에게 보낼 말이 나옵니다. "
                           "잘 쓴 자소서는 sources/자소서 에 넣고 7번 — 초안은 그 문체·흐름을 따릅니다(경험 카드로는 뽑지 않습니다)")
            elif choice == 2:
                self._step(["exp", "extract"], "자료 읽는 중…", "sources 에 새 자료를 넣었어.")
            elif choice == 3:
                self._step(["exp", "check"], "", "")
            elif choice == 4:
                self._call(["exp", "new"])
            elif choice == 5:
                self._step(["profile", "extract"], "자료 읽는 중…", "sources 의 이력서로 지원서 정보(학력·자격증·어학 등)를 만들 준비를 해 뒀어.")
            elif choice == 6:
                self._call(["profile", "check"])
            elif choice == 7:
                rc, _ = self._call(["style", "stats"])
                if rc == 0:
                    self._show_agent(handoff.Handoff(agent=STYLE_AGENT))
            elif choice == 8:
                self._call(["prep", "style-rules", "--copy"])
            elif choice == 9:
                self._call(["accept", "style-rules", "--paste"])
            elif choice == 10:
                _open(self.project.me_dir)

    def _first_setup(self) -> bool:
        self.console.print(Panel("이 폴더는 아직 처음 설정 전입니다. jobs.xlsx, me/, sources/, output/ 을 만듭니다.", title="처음 설정", border_style="cyan", expand=False))
        if not self._yes("지금 만들까요?"):
            return False
        rc, _ = self._call(["init"])
        if rc != 0:
            return False
        self._hint(
            f"1) sources 폴더에 이력서·포트폴리오를 넣으세요 — {M.ME}번 → 1번\n"
            f"2) 공고 링크를 넣으세요 — {M.ADD}번(공고 추가)"
        )
        return True

    def _add(self) -> None:
        url = self._ask("공고 링크 › ")
        if not url:
            return
        rc, out = self._call(["add", url], "공고 페이지 읽는 중…")
        if rc != 0 and "--company" in out:
            company = self._ask("회사명 › ")
            if not company:
                return
            rc, out = self._call(["add", url, "--company", company])
        if rc != 0:
            return
        if self._yes("지금 공고를 받아 AI 단계 직전까지 진행할까요?"):
            self._step(["run"], "공고 처리 중…", "새 공고를 추가하고 AI 단계 직전까지 진행해 뒀어.")
        else:
            self._show_after("새 공고를 jobs.xlsx 에 추가했어.")

    def _questions(self) -> None:
        state = self._state()
        job = self._pick(state.active_jobs(), "문항을 넣을 공고")
        if job is None:
            return
        existing = job.user_questions or []
        if existing:
            self.console.print("[dim]이미 있는 문항[/]")
            for i, q in enumerate(existing, start=1):
                self.console.print(f"  {i}. {q['text']}" + (f" [dim]({q['limit']}자)[/]" if q.get("limit") else ""))
        self.console.print("[dim]공고·지원서에 적힌 원문을 한 줄로 붙여 넣으세요. 빈 줄이면 끝.[/]")
        argv = ["question", "add", "--job", job.key]
        n = 0
        while True:
            text = self._ask(f"문항 {len(existing) + n + 1} › ")
            if not text:
                break
            limit = self._ask("  글자수 [dim](숫자, 없으면 Enter)[/] › ")
            argv += ["--text", text, "--limit", limit if limit.isdigit() else "0"]
            n += 1
        if not n:
            return
        if not self._yes("  공백 포함으로 셀까요?"):
            argv.append("--no-spaces")
        rc, _ = self._call(argv)
        if rc == 0:
            self._step(["run"], "매칭을 다시 하는 중…", f"{job.company} 문항 {n}개를 jobs.xlsx 에 넣고 초안 직전까지 진행해 뒀어.")

    def _posting(self) -> None:
        state = self._state()
        jobs = state.active_jobs()
        failed = [j for j in jobs if j.status == STATUS_ERROR]
        job = self._pick(failed or jobs, "본문을 넣을 공고")
        if job is None:
            return
        self._ask(f"브라우저에서 {job.company} 공고 본문을 복사한 뒤 Enter › ")
        rc, _ = self._call(["posting", "--job", job.key, "--from-clipboard"])
        if rc == 0:
            self._step(["run"], "공고 처리 중…", f"{job.company} 공고 본문을 넣고 AI 단계 직전까지 진행해 뒀어.")

    def _sheet(self) -> None:
        state = self._state()
        job = self._pick(state.active_jobs(), "입력표를 만들 공고")
        if job is None:
            return
        rc, _ = self._call(["apply", "sheet", "--job", job.key])
        if rc == 0:
            path = self.project.apply_dir(job.key) / "fill.md"
            self._hint(f"{self.project.rel(path)} 를 열어 지원서 칸에 옮기세요. 신원 정보는 직접, 제출도 직접 합니다")
            _open(path)

    def _answer(self) -> None:
        rc, _ = self._call(["accept", "--paste"])
        if rc == 0:
            self._call(["run"], "이어서 진행 중…")
            self._hint(f"다음 AI 단계가 있으면 {M.PROMPT}번(AI 프롬프트 복사)으로 이어 가세요")

    def _pick(self, jobs, title: str):
        jobs = sorted(jobs, key=lambda j: j.row)
        if not jobs:
            self._hint(f"공고가 없습니다 — {M.ADD}번(공고 추가)으로 먼저 넣으세요")
            return None
        if len(jobs) == 1:
            self.console.print(f"[dim]{title}:[/] [bold]{_name(jobs[0])}[/]")
            return jobs[0]
        self.console.print(f"[dim]{title}[/]")
        for i, j in enumerate(jobs, start=1):
            self.console.print(f"  [bold cyan]{i}[/]  {_name(j)} [dim]{j.status}[/]")
        raw = self._ask("번호 › ")
        if raw.isdigit() and 1 <= int(raw) <= len(jobs):
            return jobs[int(raw) - 1]
        return None

    # --- 실행과 결과 ---

    def _step(self, argv: list[str], busy: str, did: str, body: bool = True) -> None:
        """명령을 돌리고, 끝에 사람이 할 일과 에이전트에게 보낼 말."""
        rc, _ = self._call(argv, busy, body)
        if rc in (0, 1):  # 1 = 검사에 걸린 것이 있을 뿐 다음 할 일은 있다
            self._show_after(did if rc == 0 else "")

    def _call(self, argv: list[str], busy: str = "", body: bool = True) -> tuple[int, str]:
        """body=False 면 명령 출력은 숨기고 [사용자에게 전할 것]만 상자로 — 'next'처럼 아래 상자가 같은 말을 할 때."""
        from jobfit import cli

        buf = io.StringIO()
        # status(Live)가 먼저 stdout 을 바꾸고, 그 안에서 명령 출력을 가로챈다 — 순서가 반대면 출력이 화면으로 샌다
        spinner = self.console.status(busy) if busy else contextlib.nullcontext()
        try:
            with spinner, contextlib.redirect_stdout(buf):
                rc = cli.main(["--root", str(self.project.root), *argv])
        except SystemExit as e:
            rc = int(e.code or 0) if isinstance(e.code, int) else 2
        except Exception as e:  # noqa: BLE001 — 메뉴는 죽지 않고 다음 번호를 받는다
            rc = 1
            buf.write(f"! 실행 중 오류: {e.__class__.__name__}: {e}\n")
        out = buf.getvalue()
        lines, notices = _split_notices(out)
        for line in _drop_next(lines) if body or rc not in (0, 1) else []:
            self.console.print(_styled(line))
        if notices:
            self.console.print(Panel("\n".join(f"• {n}" for n in notices), title="[bold]사람이 직접 할 일[/]", border_style="yellow", expand=False))
        return rc, out

    def _show_after(self, did: str) -> None:
        h = handoff.build(self.project, self._state(), did)
        if h.human:
            self._hint(h.human)
        if h.agent:
            self._show_agent(h)

    def _show_agent(self, h: handoff.Handoff) -> None:
        copied = self.copy(h.agent)
        title = "[bold]에이전트에게 보낼 말[/] · " + ("클립보드에 복사했습니다" if copied else "복사하지 못했습니다 — 직접 선택해 복사하세요")
        body: list = [Text(h.agent)]
        if h.web_chat:
            body.append(Text(f"\n웹 채팅만 쓴다면 {M.PROMPT}번(AI 프롬프트 복사)으로 이어 가세요", style="dim"))
        self.console.print(Panel(Group(*body), title=title, border_style="green", expand=False))

    def _hint(self, text: str) -> None:
        self.console.print(Panel(text, title="[bold]사람이 할 일[/]", border_style="yellow", expand=False))

    def _state(self) -> State:
        state = State.load(self.project.state_file) if self.project.state_file.exists() else State()
        if self.project.is_initialized():
            pipeline.sync_in(self.project, state)
            state.save(self.project.state_file)
        return state


def _name(job) -> str:
    return job.company + (f" / {job.title}" if job.title else "")


def _split_notices(out: str) -> tuple[list[str], list[str]]:
    """명령 출력에서 [사용자에게 전할 것] 묶음을 떼어 낸다 — 메뉴에서는 노란 상자로 보여 준다."""
    lines = out.splitlines()
    if HEADER not in lines:
        return lines, []
    i = lines.index(HEADER)
    body = lines[:i]
    while body and not body[-1].strip():
        body.pop()
    return body, [ln.strip()[2:] for ln in lines[i + 1:] if ln.strip().startswith("- ")]


def _drop_next(lines: list[str]) -> list[str]:
    """'다음: …' 줄과 그 아래 들여 쓴 이어지는 줄을 뺀다. 명령 이름으로 된 안내라, 메뉴에서는 아래 상자가 다음 할 일을 말한다."""
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("다음:"):
            skipping = True
            continue
        if skipping and (line.startswith(" ") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return out


def _styled(line: str) -> Text:
    s = line.rstrip()
    t = s.lstrip()
    if t.startswith(("!", "⛔")) or "오류" in s or "실패" in s or "못했습니다" in s:
        style = "red"
    elif "⚠" in s or "경고" in s or "[확인 필요" in s:
        style = "yellow"
    elif t.startswith("다음:"):
        style = "dim"  # 명령 이름 안내 — 메뉴에서는 아래 상자가 다음 할 일을 말한다
    elif t.startswith(("추가:", "저장:", "+ ")) or "완료" in s or "복사했습니다" in s:
        style = "green"
    else:
        style = ""
    return Text(s, style=style)


def _open(path: Path) -> None:
    """폴더는 탐색기로, 글 파일(.md)은 메모장·텍스트 편집기로 — .md 에 연결된 프로그램이 없는 PC가 많다."""
    is_file = bool(path.suffix)
    if not is_file:
        path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        if is_file:
            subprocess.Popen(["notepad.exe", str(path)])  # noqa: S603, S607
        else:
            os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", "-t", str(path)] if is_file else ["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:  # noqa: BLE001
        return False
