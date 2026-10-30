"""회사 채용 사이트(그리팅·나인하이어·리크루터) — 가상 회사로 만든 페이지. 구조는 2026-10-30 실측 페이지를 따랐다."""

import json
import shutil
from pathlib import Path

from jobfit import cli
from jobfit.clean.posting import clean_job
from jobfit.clean.sections import has_posting_body
from jobfit.fetch.company import CLOSED_MARK, read_company_page
from jobfit.fetch.meta import read_posting_meta
from jobfit.paths import Project, package_data_dir
from jobfit.state import State

FIXTURES = Path(__file__).parent / "fixtures"
NAV = "<nav><a>채용 공고</a><a>회사 소개</a><a>다른 공고 목록</a></nav>"
BODY = "<h3>주요업무</h3><ul><li>주문 API 개발</li></ul><h3>자격요건</h3><ul><li>Python 3년 이상</li></ul><h3>우대사항</h3><ul><li>Docker</li></ul>"


def _next_page(data: dict, visible: str = "") -> str:
    return (
        "<html><head><title>채용</title></head><body>" + NAV + visible
        + f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></body></html>'
    )


def _greeting(detail: str = BODY, due: str | None = "2026-11-29T15:00:00Z") -> str:
    opening = {"openingId": 1, "status": "OPEN", "title": "백엔드 개발자", "detail": detail, "dueDate": due}
    props = {
        "careerBaseInfo": {"workspace": {"id": 1, "name": "(주)예시랩"}},
        "dehydratedState": {"queries": [{"state": {"data": {"workspace": {}}}}, {"state": {"data": {"data": {"openingsInfo": opening}}}}]},
    }
    return _next_page({"page": "/[locale]/o/[openingId]", "props": {"pageProps": props}}, visible=detail)


def _ninehire(content: str, closed: bool = False) -> str:
    rec = {"companyName": "예시컴퍼니", "externalTitle": "[예시그룹] 2027년 신입사원 공개 채용", "endAt": "2026-10-28T14:59:59.429Z", "deadlineType": "custom_deadline"}
    props = {"recruitment": rec} if closed else {"recruitment": rec, "jobPosting": {"content": content}}
    page = "/job_posting/[addressKey]/invalid" if closed else "/job_posting/[addressKey]"
    return _next_page({"page": page, "props": {"pageProps": props}}, visible="" if closed else content)


RECRUITER = (
    '<html><head><title>예시 Careers | [예시물산] 2026년 하반기 신입사원 채용</title>'
    '<link rel="canonical" href="https://example.recruiter.co.kr/career/jobs/12345"/>'
    '<meta property="og:title" content="[예시물산] 2026년 하반기 신입사원 채용"/>'
    '<meta name="author" content="예시 Careers"/></head><body><div id="root"></div>'
    '<script>self.__next_f.push([1,"0:{}"])</script></body></html>'
)


def _clean(tmp_path, html: str, url: str):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "source.html").write_text(html, encoding="utf-8")
    (job_dir / "fetch.json").write_text(json.dumps({"ok": True, "url": url, "title": ""}), encoding="utf-8")
    return job_dir, clean_job(job_dir, package_data_dir(), url)


def test_greeting_meta_from_embedded_data():
    meta = read_posting_meta(_greeting())
    assert (meta.company, meta.title, meta.source) == ("예시랩", "백엔드 개발자", "embedded")
    assert meta.deadline == "2026-11-30"  # UTC 11-29 15:00 = 한국 11-30 00:00


def test_greeting_body_comes_from_the_data_not_the_site_menu(tmp_path):
    job_dir, r = _clean(tmp_path, _greeting(), "https://example.career.greetinghr.com/ko/o/1")
    assert r.ok and r.title == "백엔드 개발자"
    assert {"주요업무", "자격요건", "우대사항"} <= set(r.sections)
    full = (job_dir / "posting.full.txt").read_text(encoding="utf-8")
    assert "주문 API 개발" in full and "다른 공고 목록" not in full
    assert r.apply["via"] == "홈페이지"


def test_greeting_image_only_posting(tmp_path):
    _, r = _clean(tmp_path, _greeting(detail='<p><img src="https://example.com/posting.png"></p>'), "https://example.career.greetinghr.com/ko/o/2")
    assert not r.ok and "공고 본문이 그림입니다" in r.error


def test_ninehire_free_form_body_is_trusted(tmp_path):
    # 헤더가 '💼[아래와 같은 역할을 담당하게 됩니다]' 같은 자유 형식이라 본문 판정 규칙에는 안 걸리는 실측 공고가 있었다
    content = "<p>💼[아래와 같은 역할을 맡게 됩니다]</p><ul><li>강의 운영</li></ul><p>🔍[이런 분을 기다립니다]</p><ul><li>Python 지식</li></ul>"
    assert not has_posting_body("💼[아래와 같은 역할을 맡게 됩니다]\n- 강의 운영\n🔍[이런 분을 기다립니다]\n- Python 지식")
    meta = read_posting_meta(_ninehire(content))
    assert (meta.company, meta.title, meta.deadline) == ("예시컴퍼니", "2027년 신입사원 공개 채용", "2026-10-28")
    _, r = _clean(tmp_path, _ninehire(content), "https://example.ninehire.site/job_posting/AbCd1234")
    assert r.ok and "Python" in r.keywords


def test_ninehire_closed_posting(tmp_path):
    page = read_company_page(_ninehire("", closed=True))
    assert page.closed and page.company == "예시컴퍼니" and page.deadline == "2026-10-28"
    _, r = _clean(tmp_path, _ninehire("", closed=True), "https://example.ninehire.site/job_posting/AbCd1234")
    assert not r.ok and r.error.startswith(CLOSED_MARK) and "마감 2026-10-28" in r.error
    assert "지원안함" in r.next_hint


def test_recruiter_title_only_and_why_no_body(tmp_path):
    meta = read_posting_meta(RECRUITER)
    assert meta.company == "" and meta.title == "2026년 하반기 신입사원 채용"  # author 는 사이트 이름이라 쓰지 않는다
    _, r = _clean(tmp_path, RECRUITER, "https://example.recruiter.co.kr/career/jobs/12345")
    assert not r.ok and "리크루터 채용 페이지는 본문을 화면이 뜬 뒤 따로 불러옵니다" in r.error


def test_board_page_with_a_recruiter_apply_link_is_still_read_as_board():
    html = (FIXTURES / "saramin-sample.html").read_text(encoding="utf-8")
    html = html.replace("</body>", '<a href="#" data-href="https://example.recruiter.co.kr/career/jobs/1">홈페이지 지원</a></body>')
    assert read_company_page(html) is None
    meta = read_posting_meta(html)
    assert meta.company == "예시랩" and meta.source == "title"


def test_body_groups_count_only_on_short_lines():
    # 실측(CJ 채용) 동의문과 같은 꼴·길이 — 항목을 한 줄에 길게 늘어놓는다
    consent = (
        "예시그룹(이하 회사)은 개인정보 보호법 등 관련 법령에 따라 채용 과정 진행 및 채용 후 인사관리 등의 목적으로 입사지원자의 개인정보를 수집하고 있습니다.\n"
        "- 성명(한글/영문), 성별, 생년월일, 국적, 주소, 휴대전화번호, 지원경로, 이메일, 학력사항(학력, 졸업학교명, 학교소재지, 전공, 성적), "
        "경력사항(회사명, 근무기간, 고용형태, 직급, 담당업무, 퇴직사유)"
    )
    assert not has_posting_body(consent)
    assert has_posting_body("## 담당업무\n- 주문 API 개발\n## 자격요건\n- Python 3년\n" + consent)


def test_closed_posting_is_told_apart_from_paste(tmp_path, capsys):
    url = "https://example.ninehire.site/job_posting/AbCd1234"
    project = Project(tmp_path)
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert cli.main(["--root", str(tmp_path), "add", url, "--company", "예시컴퍼니", "--title", "신입", "--deadline", "2020-01-01"]) == 0
    assert "마감일(2020-01-01)이 지난 공고입니다" in capsys.readouterr().out
    job_dir = project.job_dir("url:example.ninehire.site/job_posting/AbCd1234")
    job_dir.mkdir(parents=True)
    (job_dir / "source.html").write_text(_ninehire("", closed=True), encoding="utf-8")
    (job_dir / "fetch.json").write_text(json.dumps({"ok": True, "url": url, "title": ""}), encoding="utf-8")
    cli.main(["--root", str(tmp_path), "run"])
    out = capsys.readouterr().out
    assert "마감돼 내려간 공고가 있습니다: 예시컴퍼니" in out
    assert "공고 본문을 붙여 넣어야 합니다" not in out
    job = next(iter(State.load(project.state_file).jobs.values()))
    assert CLOSED_MARK in job.error


def test_saramin_fixture_is_unchanged(tmp_path):
    # 회사 사이트 규칙이 채용 플랫폼 공고 정제를 바꾸지 않는다
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    shutil.copy(FIXTURES / "saramin-sample.html", job_dir / "source.html")
    shutil.copy(FIXTURES / "saramin-sample-detail.html", job_dir / "source-detail.html")
    url = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001"
    (job_dir / "fetch.json").write_text(json.dumps({"ok": True, "url": url, "title": "t"}), encoding="utf-8")
    r = clean_job(job_dir, package_data_dir(), url)
    assert r.ok and {"자격요건", "주요업무"} <= set(r.sections)
