"""메뉴(start.bat·start.sh)와 문항 넣기, 에이전트에게 보낼 말 — 네트워크·클립보드 없이."""

import io
import shutil
from pathlib import Path

from rich.console import Console
from test_draft import _original
from test_report_dashboard import KEY, _job, _project

from jobfit import cli, handoff, menu, notices, pipeline
from jobfit.emphasis import load_resume
from jobfit.excel.append import append_question_rows, lock_files
from jobfit.excel.reader import read_jobs_workbook
from jobfit.experiences import load_cards
from jobfit.paths import Project
from jobfit.profile import load_profile
from jobfit.state import State


def _run_menu(project, answers, copied=None):
    """answers 를 차례로 입력한 메뉴 화면 글자. 클립보드에 넣은 것은 copied 에 모은다."""
    feed = iter(answers)

    def ask(prompt):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    def copy(text):
        (copied if copied is not None else []).append(text)
        return True

    out = io.StringIO()
    console = Console(file=out, width=140, color_system=None, force_terminal=False)
    assert menu.run_menu(project, console=console, ask=ask, copy=copy) == 0
    return out.getvalue()


def test_question_add_writes_sheet_and_skips_duplicates(tmp_path, capsys):
    project = _project(tmp_path)
    capsys.readouterr()
    argv = ["--root", str(tmp_path), "question", "add", "--job", "예시랩"]
    assert cli.main(argv + ["--text", "지원 동기를 적어주세요", "--limit", "700", "--text", "협업 경험", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "추가: 문항 시트 2~3행 — 예시랩 문항 2개" in out
    assert "1. 지원 동기를 적어주세요 (700자, 공백 포함)" in out
    rows = read_jobs_workbook(project.jobs_xlsx).questions
    assert [(q.job_ref, q.number, q.text, q.limit) for q in rows] == [
        ("예시랩 / 백엔드 개발자", 1, "지원 동기를 적어주세요", 700),
        ("예시랩 / 백엔드 개발자", 2, "협업 경험", None),
    ]
    # 같은 문항은 다시 넣지 않고, 새 문항은 다음 번호로
    assert cli.main(argv + ["--text", "협업  경험", "--text", "입사 후 포부", "--no-spaces"]) == 0
    out = capsys.readouterr().out
    assert "이미 있는 문항이라 건너뜁니다: 협업  경험" in out
    last = read_jobs_workbook(project.jobs_xlsx).questions[-1]
    assert (last.number, last.text, last.count_spaces) == (3, "입사 후 포부", False)
    assert len(list(project.backups_dir.glob("jobs_*.xlsx"))) >= 2  # 쓸 때마다 백업


def test_question_add_refuses_open_workbook(tmp_path, capsys):
    project = _project(tmp_path)
    lock_files(project.jobs_xlsx)[0].write_bytes(b"x")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "question", "add", "--job", KEY, "--text", "지원 동기"]) == 1
    assert "jobs.xlsx가 열려 있습니다" in capsys.readouterr().out


def test_question_sheet_accepts_job_key(tmp_path):
    # 실측: 에이전트가 "공고키로 문항을 입력하라"고 안내했는데 공고 칸은 회사명만 알아들었다
    project = _project(tmp_path)
    append_question_rows(project.jobs_xlsx, project.backups_dir, KEY, [("지원 동기", 500, True)], 1)
    state = State.load(project.state_file)
    report = pipeline.sync_in(project, state)
    assert not report.unmatched_questions
    assert state.get(KEY).user_questions[0]["text"] == "지원 동기"


def test_handoff_for_human_and_agent_steps(tmp_path):
    project = _project(tmp_path)  # 문항대기
    h = handoff.build(project, State.load(project.state_file))
    assert not h.agent and "start.bat 2번(문항 넣기)로 넣으세요" in h.human
    cli.main(["--root", str(tmp_path), "question", "add", "--job", KEY, "--text", "어려운 문제를 해결한 경험", "--limit", "700"])
    cli.main(["--root", str(tmp_path), "run"])
    h = handoff.build(project, State.load(project.state_file), "예시랩 문항 1개를 넣었어.")
    assert not h.agent and "원안이 될 자소서가 필요합니다 — sources/자소서/" in h.human  # 원안 없이는 초안을 쓰지 않는다
    _original(project)
    cli.main(["--root", str(tmp_path), "run"])
    h = handoff.build(project, State.load(project.state_file), "예시랩 문항 1개를 넣었어.")
    assert h.agent.startswith("예시랩 문항 1개를 넣었어. 이전 대화에서 본 상태는 믿지 말고 `uv run jobfit next`부터 다시 실행해서")
    assert h.agent.endswith("(지금 다음 할 일: 예시랩 초안 작성)") and h.web_chat


def test_menu_question_flow_ends_with_message_for_agent(tmp_path):
    project = _project(tmp_path)
    _original(project)
    copied = []
    screen = _run_menu(project, ["2", "어려운 문제를 해결한 경험을 적어주세요", "700", "", "", "0"], copied)
    assert "문항을 넣을 공고: 예시랩 / 백엔드 개발자" in screen
    assert "추가: 문항 시트 2행 — 예시랩 문항 1개" in screen
    assert "에이전트에게 보낼 말 · 클립보드에 복사했습니다" in screen
    assert copied == [
        "예시랩 문항 1개를 jobs.xlsx 에 넣고 초안 직전까지 진행해 뒀어. 이전 대화에서 본 상태는 믿지 말고 "
        "`uv run jobfit next`부터 다시 실행해서, 안내대로 끝까지 이어서 진행해줘. (지금 다음 할 일: 예시랩 초안 작성)"
    ]
    assert _job(project).user_questions[0]["limit"] == 700


def test_menu_next_shows_human_step_instead_of_agent_message(tmp_path):
    project = _project(tmp_path)  # 문항대기 — 에이전트가 할 일이 없다
    copied = []
    screen = _run_menu(project, ["5", "0"], copied)
    assert "사람이 할 일" in screen and "start.bat 2번(문항 넣기)" in screen
    assert copied == []


def test_menu_first_setup_and_quit_on_end_of_input(tmp_path):
    project = Project(tmp_path)
    assert "처음 설정" in _run_menu(project, ["n"])
    assert not project.jobs_xlsx.exists()
    screen = _run_menu(project, [""])  # Enter=예, 그다음 입력이 끊기면 조용히 끝낸다
    assert project.is_initialized()
    # 첫 코스는 공고 링크 하나 → 그다음 경험 카드 → 그다음 원안 자소서
    assert "1) 공고 링크 하나를 먼저 넣고 결과를 보세요" in screen
    first, cards, samples = (screen.index(x) for x in ("1) 공고 링크", "2) 그다음 이력서·포트폴리오", "3) 그다음 잘 쓴 자소서"))
    assert first < cards < samples


def _with_example(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1] / "me.example", tmp_path / "me.example")
    return Project(tmp_path)


def test_first_setup_offers_to_drop_the_example_cards(tmp_path):
    # 실측: 예시 카드가 me/ 에 남아 진짜 카드와 섞였다 — 처음 설정에서 지우고 시작할 수 있어야 한다
    project = _with_example(tmp_path)
    screen = _run_menu(project, ["", ""])  # 지금 만들까요? 예 → 예시 카드를 지울까요? 예
    assert "예시를 지우고 빈 상태로 시작합니다: experiences.yaml, resume.yaml, profile.yaml" in screen
    assert load_cards(project.experiences_file) == ([], [])
    assert load_resume(project.resume_file) == ([], [])
    assert load_profile(project.profile_file) == ([], [])
    assert project.experiences_file.read_text(encoding="utf-8").startswith("# 경험 은행")  # 쓰는 법은 남는다
    assert notices.collect(project) == []  # 지웠으니 "예시 카드가 남아 있습니다" 경고도 없다


def test_first_setup_keeps_the_example_cards_when_declined(tmp_path):
    project = _with_example(tmp_path)
    _run_menu(project, ["", "n"])
    cards, _ = load_cards(project.experiences_file)
    assert [c["id"] for c in cards] == [c["id"] for c in load_cards(project.me_example_dir / "experiences.yaml")[0]]
    assert any("예시 카드" in n for n in notices.collect(project))  # 예전 폴더를 쓰는 사람을 위한 경고는 그대로


def test_split_notices_moves_the_block_into_its_own_box():
    from jobfit.notices import HEADER

    body, notices = menu._split_notices(f"다음: x\n\n{HEADER}\n  - 카드를 확인하세요\n  - 자료를 다시 저장하세요\n")
    assert body == ["다음: x"]
    assert notices == ["카드를 확인하세요", "자료를 다시 저장하세요"]
