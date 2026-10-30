from pathlib import Path

from jobfit.clean.html2text import html_to_text
from jobfit.clean.sections import drop_noise
from jobfit.fetch.channel import read_channel, resolve

FIXTURES = Path(__file__).parent / "fixtures"
SARAMIN = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001"
JOBKOREA = "https://www.jobkorea.co.kr/Recruit/GI_Read/10000002"


def _pages(*names: str) -> tuple[list[str], str]:
    htmls = [(FIXTURES / n).read_text(encoding="utf-8") for n in names]
    return htmls, "\n\n".join(drop_noise(html_to_text(h)) for h in htmls)


def test_saramin_fixture_says_homepage_in_its_text():
    htmls, text = _pages("saramin-sample.html", "saramin-sample-detail.html")
    ch = read_channel(SARAMIN, htmls, text)
    assert ch.via == "홈페이지" and "홈페이지 접수" in ch.evidence


def test_jobkorea_fixture_does_not_say():
    htmls, text = _pages("jobkorea-sample.html", "jobkorea-sample-detail.html")
    assert read_channel(JOBKOREA, htmls, text).via == ""


def test_saramin_method_cell_wins_and_keeps_data_href():
    # 사람인 요약 페이지의 지원방법 칸 — 주소는 href="#" 대신 data-href 에 있다(실측)
    html = '<dl><dt>지원방법</dt><dd class="method"><a href="#" data-href="https://acme.career.greetinghr.com/ko/o/1">홈페이지 지원</a></dd></dl>'
    ch = read_channel(SARAMIN, [html], "")
    assert ch.via == "홈페이지" and ch.url == "https://acme.career.greetinghr.com/ko/o/1"
    assert ch.evidence == "사람인 지원방법 칸 '홈페이지 지원'"


def test_link_for_homepage_comes_from_the_method_anchor():
    html = '<p>ㆍ접수방법 : <a href="https://acme.example.com/apply/7">자유양식의 이력서를 홈페이지 접수</a></p>'
    ch = read_channel(JOBKOREA, [html], drop_noise(html_to_text(html)))
    assert ch.via == "홈페이지" and ch.url == "https://acme.example.com/apply/7"


def test_board_posting_with_email_apply():
    text = "[지원 방법 및 절차]\n- 이메일 지원시 우선 검토합니다\n- 문의: hr@example.com"
    ch = read_channel(JOBKOREA, [], text)
    assert ch.via == "이메일" and ch.url == ""


def test_several_methods_are_not_guessed():
    text = "■ 접수방법\n- 잡코리아 즉시지원 또는 이메일로 제출"
    ch = read_channel(JOBKOREA, [], text)
    assert ch.via == "" and "여러 방법" in ch.evidence


def test_boilerplate_outside_the_method_section_is_ignored():
    # 사람인 페이지 아래 개인정보 안내문에 '홈페이지 지원 공고의 경우'가 늘 있다(실측)
    text = "## 자격요건\n- Python\n\n개인정보 수집 동의: 입사지원자(홈페이지 지원 공고의 경우 지원 예정자)의 정보"
    assert read_channel(SARAMIN, [], text).via == ""


def test_company_site_is_homepage_unless_it_says_email_only():
    url = "https://acme.career.greetinghr.com/ko/o/1"
    assert read_channel(url, [], "## 자격요건\n- Python").via == "홈페이지"
    assert read_channel(url, [], "## 자격요건\n- Python").url == url
    assert read_channel(url, [], "■ 지원 방법\n- 이메일로 이력서 제출").via == "이메일"


def test_pasted_posting_without_link_reads_text_only():
    assert read_channel("", [], "접수방법: 홈페이지 접수").via == "홈페이지"
    assert read_channel("", [], "## 자격요건\n- Python").via == ""


def test_user_choice_comes_first():
    found = {"via": "홈페이지", "url": "https://acme.example.com/apply", "evidence": "공고 글"}
    assert resolve("", found).via == "홈페이지"
    mine = resolve("홈페이지", found)
    assert mine.url == "https://acme.example.com/apply" and mine.evidence == "jobs.xlsx 지원방법 칸"
    assert resolve("이메일", found).url == ""
    assert resolve("플랫폼", {}).via == "플랫폼"
