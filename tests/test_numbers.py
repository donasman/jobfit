from jobfit.numbers import extract, kor_to_int, same_value

VERSIONS = ["Java", "Spring Boot", "Python", "Node.js"]


def values(text):
    return [(n.value, n.unit) for n in extract(text, VERSIONS).numbers]


def test_same_quantity_in_many_spellings():
    forms = ["약 3천만 건", "삼천만 건", "30,000,000건", "3,000만 건"]
    toks = [extract(f).numbers[0] for f in forms]
    assert {(t.value, t.unit) for t in toks} == {(30_000_000.0, "건")}
    assert same_value(toks[0], toks[1:])


def test_units_and_multipliers():
    assert values("p95 1.8초 → 0.4초, 쿼리 21회 → 1회") == [(1.8, "초"), (0.4, "초"), (21.0, "회"), (1.0, "회")]
    assert values("비용 50만 원 절감, 3배 빨라짐, 40% 감소") == [(500_000.0, "원"), (3.0, "배"), (40.0, "%")]
    assert values("2인 팀, 3번 배포, 6개월") == [(2.0, "인"), (3.0, "회"), (6.0, "개월")]


def test_names_dates_markers_are_not_numbers():
    text = "Java 17과 Spring Boot 3, S3·EC2·OAuth2, HTTP/2, p95. 2025년 3월부터 2025.06까지. [확인 필요: 사용자 수 1만 명]\n1. 첫째"
    ex = extract(text, VERSIONS)
    assert ex.numbers == []
    assert ex.years == [2025, 2025]


def test_korean_numeral_needs_unit_and_word_start():
    assert kor_to_int("삼천만") == 30_000_000 and kor_to_int("천만") == 10_000_000 and kor_to_int("이십") == 20
    assert kor_to_int("만약") == 0
    assert values("천만 원") == [(10_000_000.0, "원")]
    assert values("수천만 건의 데이터") == []  # 어림말은 숫자로 보지 않는다
    assert values("만들었습니다 일주일 만에") == []


def test_severity():
    big, small, mag = extract("100명").numbers[0], extract("3명").numbers[0], extract("3%").numbers[0]
    assert big.severity() == "오류" and small.severity() == "경고" and mag.severity() == "오류"
    assert extract("15").numbers[0].severity() == "오류" and extract("2").numbers[0].severity() == "경고"


def test_same_value_rules():
    facts = extract("p95 1.8초 → 0.4초, 6GB").numbers
    assert same_value(extract("1.8초").numbers[0], facts)
    assert same_value(extract("1.8").numbers[0], facts)  # 단위 없는 쪽은 값만 본다
    assert not same_value(extract("1.8분").numbers[0], facts)  # 단위가 다르면 다른 값
    assert not same_value(extract("78%").numbers[0], facts)  # 계산해서 만든 비율
