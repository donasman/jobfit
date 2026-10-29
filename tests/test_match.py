import json

from jobfit.clean.keywords import KeywordDict
from jobfit.experiences import match_tags
from jobfit.match import assign, build_match, cards_in_use, classify_question, resolve_questions, rules_sha
from jobfit.paths import package_data_dir
from jobfit.state import Job

KD = KeywordDict.load(package_data_dir())

ANALYSIS = {
    "role_summary": "백엔드",
    "must": ["Python 기반 API 개발", "SQL 성능 최적화", "Docker 경험", "피드백을 받아들이는 태도"],
    "nice": ["Kotlin·Spring Boot 개발", "배치 처리 경험"],
    "keywords": ["Python", "Docker", "Kotlin", "Redis"],
    "questions_found": [{"text": "지원 동기를 적어주세요", "limit": 800}],
}
CARDS = [
    {"id": "etl", "title": "ETL 파이프라인", "tags": ["파이썬", "Airflow", "배치"], "usable_for": ["직무역량", "문제해결"], "verified": True},
    {"id": "api", "title": "주문 API 개선", "tags": ["Java", "스프링부트", "성능 최적화", "Docker"], "usable_for": ["문제해결"], "verified": False},
    {"id": "doc", "title": "온보딩 문서", "tags": ["협업", "문서화"], "usable_for": ["협업", "성장"], "verified": True},
]


def _q(text, qid="q1"):
    return {"id": qid, "text": text, "limit": None, "count_spaces": True, "source": "직접입력", "type": classify_question(text)}


def test_requirements_score_and_card_ranking():
    m = build_match(ANALYSIS, CARDS, [], KD)
    req = {r["text"]: r for r in m["requirements"]}
    assert req["Python 기반 API 개발"]["cards"] == ["etl"]
    assert req["SQL 성능 최적화"]["cards"] == ["api"]  # '성능 최적화' 사전 낱말
    assert req["피드백을 받아들이는 태도"]["terms"] == []  # 태그로 판단할 수 없는 항목
    assert req["배치 처리 경험"]["cards"] == ["etl"]  # 사전에 없는 카드 태그도 글에 있으면 낱말
    b = m["breakdown"]
    assert b["must"] == {"covered": 3, "judged": 3, "total": 4}
    assert b["nice"] == {"covered": 2, "judged": 2, "total": 2}  # Kotlin은 없지만 Spring Boot는 api 카드
    assert b["keywords"] == {"covered": 2, "judged": 4, "total": 4}  # Python, Docker
    assert m["score"] == round(100 * (0.6 * 1 + 0.25 * 1 + 0.15 * 0.5))
    assert [c["id"] for c in m["cards"]] == ["api", "etl", "doc"]
    assert m["cards"][-1]["score"] == 0


def test_score_none_when_nothing_judgeable():
    m = build_match({"must": ["성실함"], "nice": [], "keywords": []}, CARDS, [], KD)
    assert m["score"] is None


def test_question_types():
    assert classify_question("지원 동기와 입사 후 목표를 적어주세요") == "지원동기"
    assert classify_question("동료와 의견이 달랐던 경험") == "협업"
    assert classify_question("예상과 다른 결과를 마주했을 때 접근을 바꾼 경험") == "문제해결"
    assert classify_question("본인의 강점을 기술하시오") == "직무역량"
    assert classify_question("좋아하는 책은?") == "기타"


def test_assignment_type_match_and_unique_primary():
    m = build_match(ANALYSIS, CARDS, [], KD)
    qs = assign([_q("어려운 문제를 해결한 경험", "q1"), _q("또 다른 어려움을 극복한 경험", "q2"), _q("동료와 협업한 경험", "q3")], m["cards"])
    primaries = [next(c["id"] for c in q["cards"] if c["role"] == "주력") for q in qs]
    assert primaries == ["api", "etl", "doc"]  # 같은 카드가 두 문항의 주력이 되지 않는다
    assert qs[0]["cards"][1] == {"id": "etl", "role": "보조", "type_match": True}
    assert all(c["type_match"] for c in qs[2]["cards"])


def test_assignment_falls_back_when_no_card_has_the_type():
    m = build_match(ANALYSIS, CARDS, [], KD)
    q = assign([_q("지원 동기를 적어주세요")], m["cards"])[0]
    assert q["cards"] == [{"id": "api", "role": "주력", "type_match": False}]


def test_typed_questions_get_their_cards_before_untyped_fallback():
    # 지원동기가 먼저 나와도 문제해결 문항의 카드를 빼앗지 않는다 (예시컴퍼니 공고 실측)
    m = build_match(ANALYSIS, CARDS, [], KD)
    qs = assign([_q("지원 동기를 적어주세요", "q1"), _q("어려운 목표를 달성한 과정", "q2"), _q("동료와 협업한 경험", "q3")], m["cards"])
    primary = {q["id"]: next((c for c in q["cards"] if c["role"] == "주력"), None) for q in qs}
    assert primary["q2"]["id"] == "api" and primary["q2"]["type_match"]
    assert primary["q3"]["id"] == "doc" and primary["q3"]["type_match"]
    assert primary["q1"] == {"id": "etl", "role": "주력", "type_match": False}  # 남은 카드로


def test_dictionary_tags_keep_word_boundaries_and_role_words_are_ignored():
    m = build_match(
        {"must": ["JavaScript 또는 TypeScript 코드 작성", "백엔드 개발 경험"], "nice": [], "keywords": ["백엔드", "Java"]},
        [{"id": "j", "title": "자바", "tags": ["Java", "백엔드"], "usable_for": ["직무역량"]}],
        [],
        KD,
    )
    req = {r["text"]: r for r in m["requirements"]}
    assert req["JavaScript 또는 TypeScript 코드 작성"]["cards"] == []  # Java 태그가 JavaScript에 걸리지 않는다
    assert req["백엔드 개발 경험"]["terms"] == []  # 직무 이름은 낱말로 치지 않는다
    assert m["keywords"] == ["Java"]


def test_user_questions_override_extracted():
    job = Job(key="k", site="x", company="c")
    assert [q["source"] for q in resolve_questions(job, ANALYSIS)] == ["공고추출"]
    job.user_questions = [{"text": "협업 경험", "limit": 500, "count_spaces": False}]
    qs = resolve_questions(job, ANALYSIS)
    assert qs == [{"id": "q1", "text": "협업 경험", "limit": 500, "count_spaces": False, "source": "직접입력", "type": "협업"}]


def test_user_edited_assignment_survives_rerun(tmp_path):
    from jobfit.match import run_match
    from jobfit.paths import Project

    project = Project(tmp_path)
    project.me_dir.mkdir()
    import yaml

    project.experiences_file.write_text(yaml.safe_dump(CARDS, allow_unicode=True), encoding="utf-8")
    job = Job(key="saramin:1", site="saramin", company="c")
    job_dir = project.job_dir(job.key)
    job_dir.mkdir(parents=True)
    (job_dir / "analysis.json").write_text(json.dumps(ANALYSIS, ensure_ascii=False), encoding="utf-8")
    first = run_match(project, job)
    assert first["questions"][0]["cards"][0]["id"] == "api"
    data = json.loads((job_dir / "match.json").read_text(encoding="utf-8"))
    data["questions"][0]["cards"] = [{"id": "doc", "role": "주력", "type_match": False}]
    (job_dir / "match.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    again = run_match(project, job)
    assert again["edited"] is True and again["questions"][0]["cards"][0]["id"] == "doc"
    third = run_match(project, job)  # 한 번 더 돌려도 계속 지킨다
    assert third["edited"] is True and third["questions"][0]["cards"][0]["id"] == "doc"


def test_implied_tags_count_for_the_language():
    # 실측: Spring Boot·MyBatis 카드에 Java 태그가 없어 'Java' 요건에서 빠지고, Java 태그뿐인 알고리즘 스터디가 1위가 됐다
    cards = [
        {"id": "study", "title": "알고리즘 스터디", "tags": ["Java"], "usable_for": ["성장"]},
        {"id": "portal", "title": "공공 포털 유지보수", "tags": ["Spring Boot", "MyBatis", "MySQL"], "usable_for": ["직무역량"]},
    ]
    analysis = {"must": ["Java 기반 웹 개발", "SQL 작성"], "nice": ["Spring 프레임워크"], "keywords": ["Java", "Spring"]}
    m = build_match(analysis, cards, [], KD)
    req = {r["text"]: r["cards"] for r in m["requirements"]}
    assert req["Java 기반 웹 개발"] == ["study", "portal"]
    assert req["SQL 작성"] == ["portal"]  # MySQL을 썼으면 SQL도
    assert req["Spring 프레임워크"] == ["portal"]  # Spring Boot는 Spring
    assert m["cards"][0]["id"] == "portal"


def test_implication_goes_one_way_and_follows_chains():
    assert match_tags({"tags": ["스프링부트"]}, KD) == ["Spring Boot", "Spring", "Java"]
    assert KD.expand(["Next.js"]) == ["Next.js", "React", "JavaScript"]
    assert KD.expand(["Java"]) == ["Java"]  # Java를 썼다고 Spring을 쓴 건 아니다
    m = build_match({"must": ["Spring Boot 개발"], "nice": [], "keywords": []}, [{"id": "j", "tags": ["Java"]}], [], KD)
    assert m["requirements"][0]["cards"] == []


def test_rules_sha_follows_dictionary_files(tmp_path):
    import shutil

    from jobfit.paths import Project

    project = Project(tmp_path)
    before = rules_sha(project)
    (tmp_path / "data").mkdir()
    for name in ("keywords.txt", "synonyms.yaml"):
        shutil.copy(package_data_dir() / name, tmp_path / "data" / name)
    assert rules_sha(project) == before  # 사전만 복사해 둔 data/ 에는 패키지 implies.yaml 을 쓴다
    (tmp_path / "data" / "implies.yaml").write_text("Spring: [Java]\n", encoding="utf-8")
    assert rules_sha(project) != before
    after = rules_sha(project)
    (tmp_path / "data" / "synonyms.yaml").write_text("Java: [자바]\n", encoding="utf-8")
    assert rules_sha(project) != after  # 다른 표기를 고쳐도 매칭을 다시 한다


def test_rdbms_requirement_accepts_other_databases():
    # 실측: 'MSSQL 또는 Oracle 등 RDBMS 활용'에 PostgreSQL·MySQL 카드가 하나도 맞지 않았다
    m = build_match({"must": ["MSSQL 또는 Oracle 등 RDBMS 활용"], "nice": [], "keywords": []}, [{"id": "pg", "tags": ["PostgreSQL"]}], [], KD)
    assert m["requirements"][0]["cards"] == ["pg"]


def test_cards_in_use_are_assigned_or_top_scoring():
    m = build_match(ANALYSIS, CARDS, [], KD)
    assert cards_in_use(m) == ["api", "etl"]  # doc는 점수 0 — 상위 3개 안이어도 쓰이지 않는다
    m["questions"] = assign([_q("동료와 협업한 경험")], m["cards"])
    assert cards_in_use(m) == ["doc", "api", "etl"]
