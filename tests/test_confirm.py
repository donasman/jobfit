"""경험 카드 확인 — 사람은 카드를 보고 Enter, 파일 고치기는 스크립트가(exp show · exp verify · 메뉴 8 → 3)."""

from test_exp_extract import RESUME
from test_exp_extract import _project as _exp_project
from test_menu import _run_menu
from test_report_dashboard import CARDS, _project

from jobfit import cli, notices
from jobfit.cli import VERIFY_RULE
from jobfit.experiences import load_cards, loose_star_numbers

# 공고에 쓰이는 'api' 카드 — 확인 전이고, star 에 facts 밖 숫자(21회·1회)가 있다
STAR_CARDS = CARDS.replace(
    "  facts: [\"p95 1.8초 → 0.4초\"]\n",
    "  star:\n    situation: 주문 조회가 느렸다\n    result: 쿼리를 21회에서 1회로 줄여 p95 1.8초 → 0.4초\n  facts: [\"p95 1.8초 → 0.4초\"]\n",
)


def _cards(project):
    return {c["id"]: c for c in load_cards(project.experiences_file)[0]}


def test_agent_shows_cards_then_verifies_what_the_user_confirmed(tmp_path, capsys):
    project = _project(tmp_path, cards=STAR_CARDS)
    assert loose_star_numbers(_cards(project)["api"]) == ["21회", "1회"]
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "show"]) == 0
    out = capsys.readouterr().out
    assert "확인할 경험 카드 1개 — 공고 리포트·초안에 쓰이는 카드" in out
    assert "「주문 API 개선 | 캐시」  (id: api)" in out and "  상황: 주문 조회가 느렸다" in out
    assert "    - p95 1.8초 → 0.4초" in out and "숫자 21회, 1회는 확인하면 facts에 들어갑니다" in out
    assert "ETL" not in out  # 이미 확인한 카드는 보여 주지 않는다
    assert "맞으면: jobfit exp verify api" in out and VERIFY_RULE in out

    assert cli.main(["--root", str(tmp_path), "exp", "verify", "api"]) == 0
    out = capsys.readouterr().out
    assert "확인: 「주문 API 개선 | 캐시」" in out
    assert 'star에만 있던 숫자(21회)를 facts에 넣었습니다 — "쿼리를 21회에서 1회로 줄여 p95 1.8초 → 0.4초"' in out
    api = _cards(project)["api"]
    assert api["verified"] is True and loose_star_numbers(api) == []
    assert api["facts"] == ["p95 1.8초 → 0.4초", "쿼리를 21회에서 1회로 줄여 p95 1.8초 → 0.4초"]
    assert not any("확인해 주세요" in n for n in notices.collect(project))  # 사람이 할 일이 사라졌다
    assert list(project.backups_dir.glob("experiences_*.yaml"))

    assert cli.main(["--root", str(tmp_path), "run"]) == 0
    report = (project.job_dir("saramin:10000001") / "report.md").read_text(encoding="utf-8")
    assert "확인 전입니다" not in report and "주문 API 개선 \\| 캐시 ⚠" not in report

    assert cli.main(["--root", str(tmp_path), "exp", "verify", "nope"]) == 1
    assert "없는 카드 id 입니다: nope" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "exp", "show"]) == 0
    assert "확인할 경험 카드가 없습니다" in capsys.readouterr().out


def test_old_ai_card_takes_the_source_sentence_when_verified(tmp_path, capsys):
    # 실측: 예전 버전에서 뽑은 카드 3개의 STAR에 facts 밖 숫자가 남아 "facts에 넣거나 STAR에서 빼세요"가 나왔다
    project = _exp_project(tmp_path)
    (project.sources_dir / "r.txt").write_text(RESUME, encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])  # 자료 글자를 읽어 둔다
    project.experiences_file.write_text(
        "- id: exp-order\n  title: 주문 조회 API 개선\n  tags: [Java]\n  summary: s\n  usable_for: [직무역량]\n"
        "  star:\n    result: 응답 시간을 1.8초에서 0.4초로 줄임\n  facts: []\n  verified: false\n  source: sources/r.txt\n"
        "- id: exp-batch\n  title: 결제 알림 배치 개선\n  tags: [Java]\n  summary: s\n  usable_for: [문제해결]\n"
        "  star:\n    result: 처리 시간을 70% 줄임\n  facts: []\n  verified: false\n  source: sources/r.txt\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "show", "exp-order"]) == 0
    out = capsys.readouterr().out
    assert "뽑은 자료: sources/r.txt" in out and "자료 원문에 없는 숫자" not in out
    assert cli.main(["--root", str(tmp_path), "exp", "verify", "exp-order"]) == 0
    card = _cards(project)["exp-order"]
    assert card["facts"] == ["p95 응답 시간 1.8초 → 0.4초"]  # star 문장보다 자료 원문 문장을 먼저
    assert card["verified"] is True and loose_star_numbers(card) == []
    # 예전 카드의 star 에 AI가 지어낸 숫자(원문에 없음)가 남아 있으면 Enter 전에 따로 짚는다 — 맞다고 하면 facts 가 되므로
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "show", "exp-batch"]) == 0
    assert "! 자료 원문에 없는 숫자: 70% — 맞는지 특히 확인하세요" in capsys.readouterr().out


def test_menu_confirms_cards_with_enter(tmp_path):
    project = _project(tmp_path, cards=STAR_CARDS)
    copied = []
    screen = _run_menu(project, ["8", "3", "", "0", "0"], copied)  # 내 자료 → 경험 카드 확인 → Enter(맞음)
    assert "경험 카드 1/1" in screen and "「주문 API 개선 | 캐시」" in screen
    assert "확인: 「주문 API 개선 | 캐시」" in screen
    assert _cards(project)["api"]["verified"] is True
    assert "확인 전입니다" not in (project.job_dir("saramin:10000001") / "report.md").read_text(encoding="utf-8")  # 메뉴가 매칭을 다시 했다


def test_menu_later_keeps_card_unverified(tmp_path):
    project = _project(tmp_path, cards=STAR_CARDS)
    screen = _run_menu(project, ["8", "3", "n", "0", "0"])
    assert "확인한 카드가 없습니다" in screen
    assert _cards(project)["api"]["verified"] is False
