from jobfit.lint import count_chars, lint_answer

FACTS = ["p95 응답 시간 1.8초 → 0.4초", "주문 1건당 DB 쿼리 21회 → 1회", "부하 테스트 동시 사용자 200명 기준"]
OK_BODY = "주문 조회 API가 느리다는 문의가 반복됐습니다. 쿼리 로그로 N+1을 찾아 21회였던 쿼리를 1회로 줄였고, p95는 1.8초에서 0.4초가 됐습니다. 동시 사용자 200명 부하 테스트로 전후를 비교했습니다."


def kinds(result, level=None):
    return [i.kind for i in result.issues if level is None or i.level == level]


def test_clean_answer_has_no_errors():
    r = lint_answer("q1", OK_BODY, len(OK_BODY), True, FACTS)
    assert r.errors == [] and r.warnings == []
    assert r.chars == len(OK_BODY)


def test_length_rules_and_space_basis():
    assert count_chars("가 나 다", False) == 3
    over = lint_answer("q1", OK_BODY, 50, True, FACTS)
    assert "length" in kinds(over, "오류")
    short = lint_answer("q1", OK_BODY, 1000, True, FACTS)
    assert "length" in kinds(short, "경고")
    no_limit = lint_answer("q1", OK_BODY, None, True, FACTS)
    assert "length" in kinds(no_limit, "경고")  # 제한 없음 권장 500~1000자 밖
    without_spaces = lint_answer("q1", OK_BODY, count_chars(OK_BODY, False), False, FACTS)
    assert "length" not in kinds(without_spaces)


def test_numbers_outside_facts():
    body = OK_BODY + " 그 결과 응답 시간이 78% 줄었고 팀원 3명과 함께했습니다. 매출이 25만 원 늘었습니다."
    r = lint_answer("q1", body, len(body), True, FACTS)
    errors = [i.message for i in r.errors if i.kind == "number"]
    warnings = [i.message for i in r.warnings if i.kind == "number"]
    assert any("'78%'" in m for m in errors) and any("'25만 원'" in m for m in errors)
    assert any("'3명'" in m for m in warnings)


def test_numbers_in_question_text_are_allowed():
    body = OK_BODY + " 3년 이상 경험을 요구하는 이 직무에"
    r = lint_answer("q1", body, len(body), True, FACTS, allowed_text="백엔드 3년 이상 경험을 적어주세요")
    assert "number" not in kinds(r)


def test_year_contact_forbidden_confirm():
    body = OK_BODY + " 2021년에 시작했고 [확인 필요: 사용자 수] 명이 썼습니다. 열심히 노력했습니다. 연락처 a@b.com"
    r = lint_answer("q1", body, len(body), True, FACTS, periods=["2025-03 ~ 2025-05"], forbidden=["열심히 노력"])
    assert "year" in kinds(r, "경고")
    assert "contact" in kinds(r, "오류") and "forbidden" in kinds(r, "오류")
    assert r.confirm_needed == 1


def test_style_checks_only_with_enough_samples():
    stats = {"samples": 5, "sentences": 40, "sentence_len_avg": 30, "sentence_len_max": 45, "commas_per_sentence": 0.2, "special": {"·": 0, "…": 0, '"': 3}}
    body = "이 문장은, 쉼표가, 아주, 많고, 길게, 이어지며, 가운뎃점·도, 쓰는 문장이라서 샘플보다 훨씬 깁니다 정말로 그렇습니다."
    r = lint_answer("q1", body, None, True, [], stats=stats)
    style = [i.message for i in r.issues if i.kind == "style"]
    assert any("가장 긴 문장" in m for m in style) and any("쉼표" in m for m in style) and any("'·'" in m for m in style)
    assert all(i.level == "경고" for i in r.issues if i.kind == "style")
    few = lint_answer("q1", body, None, True, [], stats={**stats, "samples": 4})
    assert "style" not in kinds(few)


def test_numbers_from_unverified_cards_warn():
    body = OK_BODY + " 세팅 소요도 3일에서 1일로 줄였습니다."
    r = lint_answer("q1", body, len(body), True, FACTS + ["세팅 소요 3일 → 1일"], unverified_facts=["세팅 소요 3일 → 1일"])
    msgs = [i.message for i in r.warnings if i.kind == "number"]
    assert any("'3일'" in m and "확인 전" in m for m in msgs)
    assert not any("'21회'" in m for m in msgs)  # 확인된 카드의 숫자는 그대로 통과
    assert r.errors == []


def test_empty_answer():
    assert kinds(lint_answer("q1", "   ", 500, True, FACTS), "오류") == ["empty"]


def test_period_until_now_covers_this_year():
    from datetime import date

    from jobfit.lint import lint_answer

    this_year = date.today().year
    body = f"{this_year}년에도 이 일을 하고 있습니다."
    ongoing = lint_answer("q1", body, None, True, ["2024년 시작"], "문항", periods=["2024-09 ~ 현재"])
    ended = lint_answer("q1", body, None, True, ["2024년 시작"], "문항", periods=["2024-09 ~ 2024-12"])
    assert not [i for i in ongoing.issues if i.kind == "year"]
    assert [i for i in ended.issues if i.kind == "year"] or this_year == 2024
