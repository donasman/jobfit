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


def test_dates_times_relations_are_not_numbers():
    # 실제 포트폴리오 테스트에서 숫자로 잘못 읽혔던 꼴들 (예문은 지어낸 것)
    text = "2026.09.17 실측, 2026-10-28 기준, 09-15 측정, 2026 – 현재, 매일 04:00(KST) 백업, 1:N 상세 · N:M 관계 · 1:1 멘토링"
    ex = extract(text)
    assert ex.numbers == []
    assert ex.years == [2026, 2026, 2026]
    assert values("2025년 3개월 동안") == [(3.0, "개월")]  # 연도 뒤의 기간은 남긴다
    assert values("2025년 3월 5일") == []


def test_http_status_codes_are_not_numbers():
    for text in ("429 오류가 났다", "응답이 200이어도 실패로 처리", "HTTP 500", "네트워크 오류 · 429 · 5xx만 재시도", "429는 원인이 달랐다", "동시 저장 500을 막음"):
        assert values(text) == [], text
    assert values("약 500 백엔드 테스트") == [(500.0, "")]  # 오류 문맥이 아니면 수량
    assert values("429 원인 분석, 04 단계에서 멈춤, 01~07 단계") == []  # 429와 앞자리 0은 이름
    assert values("429건, 05분") == [(429.0, "건"), (5.0, "분")]  # 단위가 붙으면 수량
    assert values("오류 300건 감소") == [(300.0, "건")]


def test_versions_without_project_dictionary():
    # 사전 없이도 흔한 기술 이름 뒤 버전은 가린다(경험 카드 검사·카드 뽑기는 사전 이름을 넘기지 않는다)
    assert [n.raw for n in extract("PostgreSQL 17, React 18, Next.js 16, Spring Boot 2.6, Gemini 3.5 Flash, Prisma 7, JDK 11").numbers] == []
    assert [(n.value, n.unit) for n in extract("Java 3년 경력, React 18개 컴포넌트").numbers] == [(3.0, "년"), (18.0, "개")]


def test_suffixes_ranges_and_mixed_korean():
    assert values("~700k 토큰, 41k, 1.2k") == [(700_000.0, ""), (41_000.0, ""), (1_200.0, "")]
    assert values("60t 동시 입고, 8t 중 5t이") == [(60.0, "t"), (8.0, "t"), (5.0, "t")]
    assert values("약 4만 3천 콜, 1억 2천만 원, 3만 5000원") == [(43_000.0, ""), (120_000_000.0, "원"), (35_000.0, "원")]
    assert values("응답 압축 45~78%, 3~6회") == [(45.0, "%"), (78.0, "%"), (3.0, "회"), (6.0, "회")]
    assert same_value(extract("43,000 콜").numbers[0], extract("약 4만 3천 콜").numbers)


def test_words_that_start_with_a_unit_are_not_units():
    assert values("약 500 백엔드 테스트") == [(500.0, "")]  # 백엔드의 '백'은 100이 아니다
    assert values("3만큼") == [(3.0, "")]
    assert values("순위표 6대회, OAuth 2 인증") == [(6.0, "")]
    assert values("3년간, 5명의, 12개월간, 3주치, 0.4초로, 3년차") == [(3.0, "년"), (5.0, "명"), (12.0, "개월"), (3.0, "주"), (0.4, "초"), (3.0, "년")]


def test_same_value_rules():
    facts = extract("p95 1.8초 → 0.4초, 6GB").numbers
    assert same_value(extract("1.8초").numbers[0], facts)
    assert same_value(extract("1.8").numbers[0], facts)  # 단위 없는 쪽은 값만 본다
    assert not same_value(extract("1.8분").numbers[0], facts)  # 단위가 다르면 다른 값
    assert not same_value(extract("78%").numbers[0], facts)  # 계산해서 만든 비율
