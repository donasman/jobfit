from jobfit import cli
from jobfit.clean.keywords import KeywordDict
from jobfit.experiences import card_types, check_cards, load_cards, tag_list
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
    assert any("'스프링부트'는 대표 표기 'Spring Boot'" in t for t in text)
    assert any("'기술역량'는 '직무역량'(으)로 읽습니다" in t for t in text)
    assert any("[오류]" in t and "'리더십'" in t for t in text)
    assert any("facts가 없습니다" in t for t in text)
    assert any("verified가 없어" in t for t in text)
    assert any("period '작년 봄'" in t for t in text)
    assert any(t.startswith("[오류] 3번째 카드: id가 비어") for t in text)


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
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "me.example" / "experiences.yaml"
    cards, problems = load_cards(example)
    assert problems == [] and cards
    assert [i for i in check_cards(cards, KD) if i.level == "오류"] == []
