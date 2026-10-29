"""지원서 정보(profile.yaml)·지원서 정보 뽑기·지원서 입력표·블라인드 검사 — 네트워크·AI 없이."""

import shutil
from pathlib import Path

import yaml
from test_report_dashboard import KEY, _job, _project

from jobfit import cli, handoff, pipeline
from jobfit.ai import profile_extract
from jobfit.apply import count_line
from jobfit.lint import lint_answer
from jobfit.paths import Project
from jobfit.profile import append_items, check_profile, example_note, load_profile, school_names, split_examples
from jobfit.state import State

RESUME = """이력서

학력
2019.03 ~ 2025.02  한빛대학교 컴퓨터공학과 졸업 (학점 3.8/4.5)

자격증
정보처리기사 — 한국산업인력공단, 2024.12.06 취득
TOEIC 860 (2025.01.12)

교육
2025.03 ~ 2025.08 백엔드 개발자 과정 (960시간) 수료
"""
PORTFOLIO = "프로젝트 소개\n\n" + ("주문 시스템을 Spring Boot 로 만들고 배포 자동화를 붙였다. " * 120)
ANSWER = """```yaml
- section: 학력
  school: 한빛대학교
  major: 컴퓨터공학과
  degree: 학사
  period: 2019-03 ~ 2025-02
  status: 졸업
  gpa: 3.8/4.5
  birthday: 1999-01-01
- section: 자격증
  name: 정보처리기사
  issuer: 한국산업인력공단
  date: 2024-12-06
  number: "24201234"
- section: 어학
  test: TOEIC
  score: "860"
  grade: ""
  date: 2025-01-12
- section: 경력
  company: ""
  department: ""
- section: 교육
  name: 백엔드 개발자 과정
  institution: ""
  period: 2025-03 ~ 2025-08
  hours: "960"
  content: ""
```
위와 같이 뽑았습니다."""


def _sources_project(tmp_path) -> Project:
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    project = Project(tmp_path)
    (project.sources_dir / "이력서.txt").write_text(RESUME, encoding="utf-8")
    (project.sources_dir / "포트폴리오.txt").write_text(PORTFOLIO, encoding="utf-8")
    return project


def test_profile_extract_only_prompts_chunks_with_application_info(tmp_path):
    project = _sources_project(tmp_path)
    report = profile_extract.scan(project)
    assert [i.source for i in report.pending] == ["sources/이력서.txt"]
    assert report.nothing == ["sources/포트폴리오.txt"]  # 학교·자격·어학 같은 낱말이 없는 자료는 돌지 않는다
    prompt = report.pending[0].prompt.read_text(encoding="utf-8")
    assert "uv run jobfit accept profile-extract" in prompt
    assert "이름·생년월일·나이·연락처·이메일·주소·주민번호·가족 같은 신원 정보는 절대 쓰지 않습니다" in prompt
    assert len(prompt) <= profile_extract.limit()


def test_profile_extract_accept_checks_numbers_and_drops_identity(tmp_path, capsys):
    project = _sources_project(tmp_path)
    profile_extract.scan(project)
    item = profile_extract.pending_items(project)[0]
    item.response.write_text(ANSWER, encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "accept", "profile-extract"]) == 0
    out = capsys.readouterr().out
    assert "지원서 정보 4개를 me/profile.yaml 에 추가했습니다" in out
    assert "자격증 '정보처리기사' 번호 '24201234': 자료 원문에 없는 숫자라 비웠습니다" in out
    items, problems = load_profile(project.profile_file)
    assert not problems
    edu = next(i for i in items if i["section"] == "학력")
    assert edu["school"] == "한빛대학교" and edu["gpa"] == "3.8/4.5" and edu["verified"] is False
    assert "birthday" not in edu  # 형식에 없는 칸(신원 정보 포함)은 버린다
    assert next(i for i in items if i["section"] == "자격증")["number"] == ""
    assert not any(i["section"] == "경력" for i in items)  # 빈 틀은 넣지 않는다
    assert profile_extract.source_files(project)["sources/이력서.txt"]["status"] == "done"
    # 같은 답을 다시 받아도 같은 항목은 건너뛴다
    profile_extract.scan(project, force=True)
    profile_extract.pending_items(project)[0].response.write_text(ANSWER, encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "accept", "profile-extract"]) == 0
    assert "학력 '한빛대학교': 이미 있는 항목이라 건너뜁니다" in capsys.readouterr().out
    assert len(load_profile(project.profile_file)[0]) == len(items)


def test_next_and_handoff_point_to_profile_extract_after_jobs(tmp_path):
    project = _sources_project(tmp_path)
    profile_extract.scan(project)
    state = State.load(project.state_file)
    action = pipeline.next_action(project, state)
    assert action.kind == "ai" and "지원서 정보 뽑기: sources/이력서.txt" in action.message
    h = handoff.build(project, state, "이력서로 지원서 정보를 만들 준비를 해 뒀어.")
    assert h.agent.endswith("(지금 다음 할 일: 지원서 정보 만들기)")


def test_profile_check_and_examples(tmp_path, capsys):
    shutil.copytree(Path(__file__).resolve().parents[1] / "me.example", tmp_path / "me.example")
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    project = Project(tmp_path)
    items, _ = load_profile(project.profile_file)
    mine, examples = split_examples(items, project.me_example_dir / "profile.yaml")
    assert not mine and examples == ["예시대학교", "정보처리기사", "TOEIC", "예시 백엔드 개발자 과정"]
    assert "me/profile.yaml 이 예시 그대로입니다" in example_note(items, examples)
    capsys.readouterr()
    cli.main(["--root", str(tmp_path), "next"])
    assert "me/profile.yaml 이 예시 그대로입니다" in capsys.readouterr().out
    issues = check_profile([{"section": "학교", "school": "", "foo": 1}, {"section": "취미", "name": "등산"}])
    assert [i.level for i in issues] == ["오류", "경고", "경고", "오류"]


def test_append_items_keeps_file_a_yaml_list(tmp_path):
    path = tmp_path / "me" / "profile.yaml"
    append_items(path, tmp_path / "bk", [{"section": "어학", "test": "TOEIC", "score": "860", "verified": False, "source": "sources/a.pdf"}], "sources/a.pdf")
    append_items(path, tmp_path / "bk", [{"section": "자격증", "name": "SQLD: 개발자", "verified": False}], "sources/b.pdf")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [d["section"] for d in data] == ["어학", "자격증"]
    assert data[1]["name"] == "SQLD: 개발자"  # 콜론이 든 값도 따옴표로
    assert list((tmp_path / "bk").glob("profile_*.yaml"))


def test_apply_sheet_has_profile_drafts_and_counts_but_no_identity(tmp_path, capsys):
    project = _project(tmp_path)
    cli.main(["--root", str(tmp_path), "question", "add", "--job", KEY, "--text", "지원 동기를 적어주세요", "--limit", "700"])
    cli.main(["--root", str(tmp_path), "run"])
    project.profile_file.write_text(
        "- section: 학력\n  school: 한빛대학교\n  major: 컴퓨터공학과\n  period: 2019-03 ~ 2025-02\n  verified: true\n"
        "- section: 어학\n  test: TOEIC\n  score: '860'\n  verified: false\n",
        encoding="utf-8",
    )
    drafts = project.job_dir(KEY) / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "q1.md").write_text("저는 데이터를 다루는 일을 좋아합니다.", encoding="utf-8")
    full = project.job_dir(KEY) / "posting.full.txt"
    full.write_text(full.read_text(encoding="utf-8") + "\n본 채용은 블라인드 채용으로 진행합니다.", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "apply", "sheet", "--job", KEY]) == 0
    out = capsys.readouterr().out
    assert "확인 전 지원서 정보 1개" in out
    sheet = (project.apply_dir(KEY) / "fill.md").read_text(encoding="utf-8")
    assert "**신원 정보는 이 표에 없습니다**" in sheet
    assert "| 한빛대학교 | 컴퓨터공학과 |  | 2019-03 ~ 2025-02 |" in sheet
    assert "| TOEIC [확인 전] | 860 |" in sheet
    assert "블라인드 채용 공고입니다" in sheet
    assert "### 1. 지원 동기를 적어주세요" in sheet
    assert "> 700자(공백 포함) · 지금 공백 포함 21자 · 공백 제외 17자" in sheet
    assert "저는 데이터를 다루는 일을 좋아합니다." in sheet
    assert "## 보유 기술" in sheet
    assert _job(project).key == KEY


def test_count_line_shows_both_byte_counts():
    assert count_line("가 a") == "공백 포함 3자 · 공백 제외 2자 · 4바이트(한글 2) / 5바이트(한글 3)"


def test_blind_lint_only_when_asked():
    text = "한빛대학교에서 배운 것을 24살에 실무에 썼고, 아버지의 직업 덕분에 일찍 관심을 가졌습니다."
    words = school_names([{"section": "학력", "school": "한빛대학교"}])
    assert words == ["한빛대학교", "한빛대"]
    blind = [i.message for i in lint_answer("q1", text, None, True, [], blind_words=words).issues if i.kind == "blind"]
    assert blind == [
        "블라인드 채용 공고입니다 — 학교명 '한빛대학교'을(를) 쓰지 마세요",
        "블라인드 채용 공고입니다 — 학교명 '한빛대'을(를) 쓰지 마세요",
        "블라인드 채용 공고입니다 — '24살' 같은 표현(나이)은 빼세요",
        "블라인드 채용 공고입니다 — '아버지의 직업' 같은 표현(가족의 직업)은 빼세요",
    ]
    assert not [i for i in lint_answer("q1", text, None, True, []).issues if i.kind == "blind"]
