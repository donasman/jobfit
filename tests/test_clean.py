from pathlib import Path

from jobfit.clean.html2text import html_to_text
from jobfit.clean.sections import drop_noise, find_questions, has_posting_body, split_sections, summary_fields

FIXTURES = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    return html_to_text((FIXTURES / name).read_text(encoding="utf-8", errors="replace"))


def test_html_to_text_keeps_structure_and_entities():
    html = "<div><h2>자격요건</h2><ul><li>Java &amp; Spring</li><li>3년&nbsp;이상</li></ul><table><tr><td>경력</td><td>신입</td></tr></table><script>x()</script></div>"
    text = html_to_text(html)
    assert "## 자격요건" in text
    assert "- Java & Spring" in text
    assert "- 3년 이상" in text
    assert "| 경력 | 신입" in text
    assert "x()" not in text


def test_saramin_detail_has_body_and_sections():
    text = drop_noise(_text("saramin-sample-detail.html"))
    assert has_posting_body(text)
    names = {s.name for s in split_sections(text)}
    assert "자격요건" in names or "주요업무" in names


def test_saramin_summary_fields_two_line_layout():
    # 사람인 요약 칸은 "경력" 줄, 빈 줄, "신입·경력 3~15년" 줄로 온다
    text = drop_noise(_text("saramin-sample.html"))
    fields = summary_fields(text)
    assert fields.get("경력") == "신입·경력 3~15년"
    assert fields.get("근무형태") == "정규직"
    assert fields.get("마감일", "").startswith("2026.10.27")


def test_jobkorea_summary_box_comes_from_the_meta_description(tmp_path):
    # 잡코리아 요약 칸(경력·학력·급여·마감일)은 화면 글자에 없다 — meta description 에만 있다(실측 2026-10)
    import json

    from jobfit.clean.posting import clean_job
    from jobfit.paths import package_data_dir

    assert summary_fields(drop_noise(_text("jobkorea-sample.html"))) == {}
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    for name, src in (("source.html", "jobkorea-sample.html"), ("source-detail.html", "jobkorea-sample-detail.html")):
        (job_dir / name).write_text((FIXTURES / src).read_text(encoding="utf-8"), encoding="utf-8")
    url = "https://www.jobkorea.co.kr/Recruit/GI_Read/10000002"
    (job_dir / "fetch.json").write_text(json.dumps({"ok": True, "url": url, "title": ""}), encoding="utf-8")
    result = clean_job(job_dir, package_data_dir(), url)
    assert result.summary == {"경력": "신입", "학력": "대졸↑", "급여": "회사내규에 따름", "마감일": "2026.11.15"}
    # 읽은 값은 프롬프트에 들어가는 posting.txt 의 요약 줄로 간다(report.md 도 meta.json 의 같은 칸을 쓴다)
    assert "요약: 경력 신입 · 학력 대졸↑ · 급여 회사내규에 따름 · 마감일 2026.11.15" in (job_dir / "posting.txt").read_text(encoding="utf-8")


def test_joined_summary_line_is_split_only_before_a_key():
    from jobfit.clean.sections import summary_from_joined

    # "대학졸업(2,3년)이상" 의 쉼표에서 자르면 값이 깨진다 — 뒤에 요약 키가 오는 쉼표에서만 자른다
    assert summary_from_joined("경력 : 신입 , 학력 : 대학졸업(2,3년)이상, 급여 : 회사내규에 따름") == {
        "경력": "신입",
        "학력": "대학졸업(2,3년)이상",
        "급여": "회사내규에 따름",
    }
    assert summary_from_joined("회사 소개만 있는 설명문입니다") == {}


def test_jobkorea_detail_sections_questions_and_bracket_headers():
    text = drop_noise(_text("jobkorea-sample-detail.html"))
    assert has_posting_body(text)
    names = [s.name for s in split_sections(text)]
    assert "주요업무" in names  # "이런 업무를 해요"
    assert names.count("자격요건") >= 2  # "이런 분들을 찾고 있어요", "이런 기술이 필요해요"
    assert "우대사항" in names  # "이런 분이면 더 좋아요"
    assert "접수방법" in names  # "- [지원 방법 및 절차]" (li 안의 괄호 헤더), "지원 시 유의해주세요"
    assert "근무조건" in names  # "- [근무 조건]", "이런 조건에서 근무할 예정이에요"
    assert "전형절차" in names  # "합류 여정은 이렇게 진행돼요"
    assert "복리후생" in names  # "함께하면 이런 점들이 좋아요"
    assert "회사소개" in names  # "㈜예시컴퍼니(example)를 소개해요"
    qs = [q.text for q in find_questions(text)]
    # 자기소개서 작성 요령 세 주제 — "알려주세요"로 끝나는 것도, 겹친 글머리 없이
    assert any(q.startswith("지원동기: 예시컴퍼니팀을 지원하는 이유") for q in qs)
    assert "목표와 실행: 어려운 목표를 달성하기 위해 내린 판단과 실행 과정을 적어주세요." in qs
    assert any(q.startswith("개선 경험: 예상과 다른 결과") for q in qs)
    assert not any(q.startswith(("-", "　")) for q in qs)


def test_fullwidth_space_and_nested_bullets():
    assert html_to_text("<ul><li>　- 지원동기: 알려주세요</li><li>ㆍ학력 : 대졸</li></ul>") == "- 지원동기: 알려주세요\n- 학력 : 대졸"


def test_summary_single_line_layouts():
    assert summary_fields("| 경력 | 신입 |\n학력: 대졸\n근무지역：서울 강남구") == {"경력": "신입", "학력": "대졸", "근무지역": "서울 강남구"}


def test_header_rules_separate_headers_from_content():
    text = "\n".join([
        "| 담당 업무",
        "ㆍPython 기반 API 개발",
        "| 필수 경험과 역량",
        "ㆍBackend 업무 경험이 3년 이상이신 분",
        "ㆍ근무요일 : 주 5일(월-금)",
        "| 우대 사항",
        "ㆍDocker 경험",
        "처우 협의 (평판조회)",
    ])
    secs = split_sections(text)
    assert [s.name for s in secs] == ["주요업무", "자격요건", "우대사항"]
    assert "근무요일 : 주 5일(월-금)" in secs[1].text  # 값 줄은 헤더가 아니라 내용
    assert "처우 협의 (평판조회)" in secs[2].text


def test_keywords_do_not_overmatch():
    from pathlib import Path as _P

    from jobfit.clean.keywords import KeywordDict
    from jobfit.paths import package_data_dir

    kd = KeywordDict.load(package_data_dir())
    found = kd.find("C# (.Net) 과 C++ 경험. 코드 리뷰와 인터뷰. JavaScript 아님 Java. PostgreSQL 과 SQL 튜닝. Node.js")
    assert "C" not in found
    assert "Vue" not in found
    assert found.get("Java") == 1 and found.get("JavaScript") == 1
    assert found.get("SQL") == 1 and found.get("PostgreSQL") == 1
    assert found.get("C#") == 1 and found.get("C++") == 1
    assert isinstance(_P, type)
    # 긴 표기가 먼저 — 'Spring Boot'·'스프링부트' 안의 'Spring'·'스프링'은 따로 세지 않는다
    assert kd.find("Spring Boot와 스프링부트, 그리고 Spring") == {"Spring Boot": 2, "Spring": 1}
    assert kd.forms("스프링부트와 Spring Boot", "Spring Boot") == {"스프링부트": 1, "Spring Boot": 1}


def test_mask_contacts():
    from jobfit.clean.posting import mask_contacts

    assert mask_contacts("문의 ks.lee@example.com / 010-1234-5678 / 02-555-1234") == "문의 [이메일] / [전화] / [전화]"
    assert mask_contacts("Java 17, 2026년, 3,000만 건") == "Java 17, 2026년, 3,000만 건"


def test_menu_only_text_is_not_body():
    menu = "\n".join(["로그인", "회원가입", "채용정보", "공채속보", "이력서", "고객센터", "이용약관"] * 30)
    assert not has_posting_body(menu)


def test_find_questions_keeps_original_text_and_limit():
    text = "1. 지원 동기를 기술하시오 (최대 800자)\n2. 협업 경험을 서술해 주십시오 (공백 포함 1,000자 이내)\n복리후생: 점심 제공"
    qs = find_questions(text)
    assert [q.text for q in qs] == ["1. 지원 동기를 기술하시오 (최대 800자)", "2. 협업 경험을 서술해 주십시오 (공백 포함 1,000자 이내)"]
    assert qs[0].limit == 800
    assert qs[1].limit == 1000
