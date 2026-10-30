"""진단 정보(jobfit diag) — 개수와 있고 없음만 내고 개인정보는 한 글자도 내지 않는다."""

from pathlib import Path

import pytest

from jobfit import cli, diag
from jobfit.paths import Project
from jobfit.state import STATUS_ERROR, STATUS_REVIEW, Job, State

# 가짜 작업 폴더에 심어 두는 개인정보. 이 중 하나라도 진단 출력에 나오면 안 된다.
SECRETS = (
    "홍길동",
    "010-1234-5678",
    "hong@example.com",
    "서울특별시 강남구 테헤란로 1길 2",
    "1995-03-17",
    "서울대학교 컴퓨터공학과",
    "예시컴퍼니",
    "별빛소프트",
    "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001",
    "주문 조회 API 응답 시간 개선",
    "p95 응답 시간 1.8초 → 0.4초",
    "저는 어릴 적부터 서버 성능에 관심이 많았습니다",
    "백엔드 개발자를 모집합니다",
    "제 강점은 끈기입니다",
)

CARDS = """
- id: exp-order-api
  title: 주문 조회 API 응답 시간 개선
  tags: [Java, Spring Boot]
  summary: "N+1을 없애고 캐시를 붙인 경험"
  facts: ["p95 응답 시간 1.8초 → 0.4초"]
  usable_for: [직무역량]
  verified: true
- id: exp-etl
  title: 별빛소프트 ETL 파이프라인
  tags: [Python]
  summary: "배치 재처리"
  facts: ["3천만 건"]
  usable_for: [문제해결]
  verified: false
"""

RESUME = """
items:
  - section: 프로젝트
    title: 주문 조회 API 응답 시간 개선
    text: 홍길동이 맡아 처리했다
    tags: [Spring Boot]
"""

PROFILE = """
- section: 학력
  school: 서울대학교 컴퓨터공학과
  graduated: 1995-03-17
  verified: true
"""

RULES = "# 문체 규칙\n\n## 도입\n저는 어릴 적부터 서버 성능에 관심이 많았습니다 처럼 시작한다.\n"


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """홈 디렉터리를 가짜로 — 실제 홈 경로(사용자 이름이 들어 있다)가 섞이지 않게."""
    home = tmp_path / "Users" / "홍길동"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _workspace(root: Path) -> Project:
    """개인정보가 가득한 가짜 작업 폴더. 네트워크·AI 없이 파일만 만든다."""
    project = Project(root)
    (root / "me" / "style").mkdir(parents=True)
    project.experiences_file.write_text(CARDS, encoding="utf-8")
    project.resume_file.write_text(RESUME, encoding="utf-8")
    project.profile_file.write_text(PROFILE, encoding="utf-8")
    (root / "me" / "style" / "style_rules.md").write_text(RULES, encoding="utf-8")

    project.samples_dir.mkdir(parents=True)
    (project.sources_dir / "이력서.txt").write_text(
        "홍길동 010-1234-5678 hong@example.com 서울특별시 강남구 테헤란로 1길 2 (1995-03-17)", encoding="utf-8"
    )
    (project.sources_dir / "포트폴리오.md").write_text("# 별빛소프트 프로젝트\n주문 조회 API 응답 시간 개선", encoding="utf-8")
    (project.samples_dir / "합격자소서.txt").write_text("제 강점은 끈기입니다", encoding="utf-8")

    job_dir = project.output_dir / "saramin-10000001"
    (job_dir / "drafts").mkdir(parents=True)
    (job_dir / "posting.txt").write_text("예시컴퍼니에서 백엔드 개발자를 모집합니다", encoding="utf-8")
    (job_dir / "report.md").write_text("# 예시컴퍼니 — 백엔드 개발자\n주문 조회 API 응답 시간 개선", encoding="utf-8")
    (job_dir / "drafts" / "q1.md").write_text("저는 어릴 적부터 서버 성능에 관심이 많았습니다", encoding="utf-8")
    (project.output_dir / "jobkorea-10000002").mkdir(parents=True)

    project.jobs_xlsx.write_text("(엑셀 대신 자리만)", encoding="utf-8")
    state = State(sheet_errors=["[공고 4행] 예시컴퍼니 — 링크를 읽지 못했습니다"])
    done = {"done": True, "at": "2026-10-30 10:00:00"}
    state.jobs["saramin:10000001"] = Job(
        key="saramin:10000001",
        site="saramin",
        company="예시컴퍼니",
        title="백엔드 개발자",
        url="https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001",
        status=STATUS_REVIEW,
        steps={s: dict(done) for s in ("fetch", "clean", "analyze", "match", "report", "draft")},
    )
    state.jobs["jobkorea:10000002"] = Job(
        key="jobkorea:10000002",
        site="jobkorea",
        company="별빛소프트",
        url="https://www.jobkorea.co.kr/Recruit/GI_Read/10000002",
        status=STATUS_ERROR,
        error="별빛소프트 채용 페이지를 읽지 못했습니다: https://www.jobkorea.co.kr/Recruit/GI_Read/10000002",
        steps={"fetch": dict(done)},
    )
    state.save(project.state_file)
    return project


def test_diag_leaks_no_personal_information(tmp_path, fake_home):
    """개인정보가 가득한 작업 폴더에서도 출력에 그 문자열이 하나도 없다."""
    project = _workspace(fake_home / "내 폴더" / "jobfit")
    text = diag.build(project)
    for secret in SECRETS:
        assert secret not in text, f"진단 정보에 개인정보가 섞였습니다: {secret}"
    # 파일 내용이 아니라 개수와 있고 없음만
    assert "경험 카드 2개(확인 1개)" in text
    assert "이력서 항목 1개 · 지원서 정보 1개" in text
    assert "문체 규칙 확정" in text
    assert "자료 2개 · 원안 자소서 1개" in text
    assert "공고: 2건 · 결과 폴더 2개" in text
    assert "report.md 1개 · 초안 폴더 1개" in text
    assert "단계를 끝낸 공고: fetch 2 · clean 1 · analyze 1 · match 1 · report 1 · draft 1" in text
    assert "jobs.xlsx 에서 읽지 못한 줄: 1개" in text


def test_diag_hides_home_directory_and_folder_names(tmp_path, fake_home):
    """홈 디렉터리는 ~ 로, 홈 밖이면 중간 폴더 이름(사람 이름일 수 있다)을 뺀다."""
    inside = diag.build(_workspace(fake_home / "내 폴더" / "jobfit"))
    assert "작업 폴더: ~/내 폴더/jobfit" in inside
    assert str(fake_home) not in inside

    outside = tmp_path / "구직자료" / "정건희" / "jobfit"
    text = diag.build(_workspace(outside))
    assert "정건희" not in text
    assert "구직자료" not in text
    assert "…/jobfit" in text


def test_diag_reports_error_kind_not_the_message(tmp_path, fake_home):
    """오류는 종류 이름으로만. 모르는 메시지는 원문을 내지 않는다."""
    project = _workspace(fake_home / "jobfit")
    text = diag.build(project)
    assert f"마지막 오류: {diag.UNKNOWN_ERROR} 1건" in text
    assert "읽지 못했습니다" not in text

    assert diag.error_kind("인터넷 연결 문제로 받지 못했습니다(ProxyError)") == "인터넷 연결 문제"
    assert diag.error_kind("404 — 공고가 내려갔거나 주소가 틀렸습니다") == "404 공고 없음"
    assert diag.error_kind("사이트가 robots.txt로 자동 수집을 막아 두었습니다") == "robots.txt 가 자동 수집을 막음"
    assert diag.error_kind("링크가 없습니다") == "링크 없음"


def test_diag_runs_in_an_empty_folder(tmp_path, capsys):
    """아직 아무것도 없는 폴더에서도(init 전에도) 돈다 — 막혔을 때 쓰는 명령이라서."""
    assert cli.main(["--root", str(tmp_path), "diag"]) == 0
    out = capsys.readouterr().out
    assert diag.HEADER in out and diag.FOOTER in out
    assert "jobs.xlsx 없음" in out
    assert "공고: 0건" in out


def test_diag_copy_puts_the_same_text_on_the_clipboard(tmp_path, monkeypatch, capsys, fake_home):
    project = _workspace(fake_home / "jobfit")
    copied: list[str] = []
    monkeypatch.setattr("jobfit.ai.promptfile.copy_to_clipboard", lambda text: copied.append(text) or True)
    assert cli.main(["--root", str(project.root), "diag", "--copy"]) == 0
    out = capsys.readouterr().out
    assert copied and copied[0].startswith(diag.HEADER) and copied[0].endswith(diag.FOOTER)
    assert "클립보드에 복사했습니다" in out
    for secret in SECRETS:
        assert secret not in copied[0]
