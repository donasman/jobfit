import shutil
from pathlib import Path

import yaml

from jobfit import cli
from jobfit.clean.keywords import KeywordDict
from jobfit.experiences import card_types, check_cards, example_ids, load_cards, tag_list
from jobfit.paths import Project, package_data_dir

KD = KeywordDict.load(package_data_dir())

GOOD = {
    "id": "exp-a", "title": "주문 API 개선", "tags": ["Java", "Spring Boot"], "summary": "N+1 제거와 캐시",
    "star": {"situation": "s", "task": "t", "action": "a", "result": "r"}, "facts": ["p95 1.8초 → 0.4초"],
    "usable_for": ["직무역량"], "verified": True, "period": "2025-03 ~ 2025-05",
}


def test_good_card_has_no_issues():
    assert check_cards([GOOD], KD) == []


def test_issues_are_reported_per_card():
    bad = {**GOOD, "id": "exp-a", "summary": "가" * 61, "tags": ["스프링부트"], "usable_for": ["기술역량", "리더십"], "facts": [], "period": "작년 봄"}
    del bad["verified"]
    issues = check_cards([GOOD, bad, {"title": "제목만"}], KD)
    text = [str(i) for i in issues]
    assert any("id가 1번째 카드와 같습니다" in t for t in text)
    assert any("summary가 61자" in t for t in text)
    assert any("'스프링부트' 대신 대표 표기 'Spring Boot'" in t for t in text)
    assert any("'기술역량'은(는) '직무역량'(으)로 읽습니다" in t for t in text)
    assert any("[오류]" in t and "'리더십'" in t for t in text)
    assert any("facts가 없습니다" in t for t in text)
    assert any("verified가 없어" in t for t in text)
    assert any("period 값 '작년 봄'" in t for t in text)
    assert any(t.startswith("[오류] 3번째 카드: id 칸이 비어") for t in text)


def test_types_and_tags_are_normalized():
    card = {**GOOD, "usable_for": ["기술역량", "팀워크"], "tags": ["스프링부트", "파이썬", "Python"]}
    assert card_types(card) == ["직무역량", "협업"]
    assert tag_list([card], KD) == ["Python", "Spring Boot"]


def test_exp_new_and_check_commands(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    project = Project(tmp_path)
    assert cli.main(["--root", str(tmp_path), "exp", "check"]) == 1  # 파일 없음
    assert cli.main(["--root", str(tmp_path), "exp", "new", "--id", "exp-x"]) == 0
    assert cli.main(["--root", str(tmp_path), "exp", "new", "--id", "exp-x"]) == 2  # 같은 id
    assert cli.main(["--root", str(tmp_path), "exp", "new"]) == 0
    cards, problems = load_cards(project.experiences_file)
    assert problems == [] and [c["id"] for c in cards] == ["exp-x", "exp-2"]
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "check"]) == 1  # 빈 카드라 오류
    assert "카드 2개" in capsys.readouterr().out


def test_me_example_cards_pass_check(tmp_path):
    example = Path(__file__).resolve().parents[1] / "me.example" / "experiences.yaml"
    cards, problems = load_cards(example)
    assert problems == [] and cards
    assert [i for i in check_cards(cards, KD) if i.level == "오류"] == []


ME_EXAMPLE = Path(__file__).resolve().parents[1] / "me.example"


def _with_examples(tmp_path) -> Project:
    shutil.copytree(ME_EXAMPLE, tmp_path / "me.example")
    assert cli.main(["--root", str(tmp_path), "init"]) == 0  # me.example 이 me/ 로 복사된다
    return Project(tmp_path)


def test_copied_example_cards_are_errors(tmp_path, capsys):
    project = _with_examples(tmp_path)
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "check"]) == 1
    out = capsys.readouterr().out
    assert out.count("me.example 에서 복사된 예시 카드입니다") == 2
    assert "예시 카드 2개를 지우세요: exp-order-api-latency, exp-team-onboarding-doc" in out
    # 예시를 고쳐 쓰면(제목이 다르면) 내 카드로 본다
    text = project.experiences_file.read_text(encoding="utf-8").replace("주문 조회 API 응답 시간 개선", "내 주문 API 개선")
    project.experiences_file.write_text(text, encoding="utf-8")
    cards, _ = load_cards(project.experiences_file)
    assert example_ids(cards, ME_EXAMPLE / "experiences.yaml") == ["exp-team-onboarding-doc"]


def test_unverified_cards_are_one_line(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    project = Project(tmp_path)
    cards = [{**GOOD, "id": f"exp-{n}", "title": f"카드 {n}", "verified": False} for n in range(7)]
    project.experiences_file.write_text(yaml.safe_dump(cards, allow_unicode=True), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "check"]) == 0
    out = capsys.readouterr().out
    assert "verified가 true가 아닙니다" not in out  # 카드마다 반복하지 않는다
    assert "[확인 전] 카드 7개" in out and "exp-0, exp-1, exp-2, exp-3, exp-4 외 2개" in out
    assert "카드 7개 · 오류 0 · 경고 0 · 확인 전 7" in out


HAND_EDITED = """# 내 경험 은행
- id: exp-a
  title: 주문 API
  star:
    situation: 느렸다
    result: |
      p95를 1.8초에서 0.4초로
  facts:
    - 쿼리 21회 → 1회
    # 아래는 나중에
    - 캐시 적중률 80%

  # 손으로 남긴 메모
  verified: false  # 아직
- id: exp-b
  title: 배치
  facts: []
- id: exp-c
  title: 동아리
  facts:
  verified:
"""


def test_update_cards_changes_only_the_values(tmp_path):
    from jobfit.experiences import CardChange, update_cards

    path = tmp_path / "experiences.yaml"
    path.write_text(HAND_EDITED, encoding="utf-8")
    update_cards(path, {
        "exp-a": CardChange(verified=True, facts_add=("p95 1.8초 → 0.4초",)),
        "exp-b": CardChange(verified=True, facts_add=("하루 3만 건",)),
        "exp-c": CardChange(verified=True, facts_add=("회원 40명",)),
    }, tmp_path / "backups")
    text = path.read_text(encoding="utf-8")
    # 주석·블록 글·빈 줄은 그대로, 바꾼 값만 달라진다
    assert text.startswith("# 내 경험 은행\n") and "    # 아래는 나중에\n" in text and "  # 손으로 남긴 메모\n" in text
    assert "    - 캐시 적중률 80%\n    - p95 1.8초 → 0.4초\n" in text  # 마지막 항목 뒤, 주석 앞
    assert "  verified: true  # 아직\n" in text
    assert "  facts:\n    - 하루 3만 건\n" in text and "  facts:\n    - 회원 40명\n  verified: true\n" in text
    cards, problems = load_cards(path)
    assert problems == []
    assert [c["verified"] for c in cards] == [True, True, True]
    assert cards[0]["facts"] == ["쿼리 21회 → 1회", "캐시 적중률 80%", "p95 1.8초 → 0.4초"]
    assert cards[0]["star"]["result"] == "p95를 1.8초에서 0.4초로\n"
    assert len(list((tmp_path / "backups").glob("experiences_*.yaml"))) == 1


def test_update_cards_adds_missing_verified_and_refuses_odd_shapes(tmp_path):
    from jobfit.experiences import CardChange, update_cards

    path = tmp_path / "experiences.yaml"
    path.write_text("- id: exp-a\n  title: A\n  tags: [Java]\n", encoding="utf-8")
    update_cards(path, {"exp-a": CardChange(verified=True)}, tmp_path / "b")
    assert path.read_text(encoding="utf-8") == "- id: exp-a\n  title: A\n  tags: [Java]\n  verified: true\n"
    odd = "- {id: exp-a, title: A, verified: false}\n"
    path.write_text(odd, encoding="utf-8")
    try:
        update_cards(path, {"exp-a": CardChange(verified=True)}, tmp_path / "b")
        raise AssertionError("ValueError 를 기대")
    except ValueError:
        pass
    assert path.read_text(encoding="utf-8") == odd  # 못 고치면 손대지 않는다


def test_period_formats():
    ok = ("2025-03 ~ 2025-06", "2025.03", "2026 ~ 현재", "2024.09 – 2025.08", "2024-09 - 2025-08", "2025-07", "2026-08 ~ 현재")
    for period in ok:
        assert not [i for i in check_cards([{**GOOD, "period": period}], KD) if "period" in i.message], period
    assert [i for i in check_cards([{**GOOD, "period": "작년 봄"}], KD) if "period" in i.message]
