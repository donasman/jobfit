"""실제 채용 사이트 페이지로 하는 회귀 테스트 — tests/fixtures_live/ 가 있을 때만 돈다(배포본에는 없다).

사이트 HTML 구조가 바뀌어 파서가 깨졌는지 확인하는 용도. 페이지를 새로 받아 이 폴더에 두고 돌린다.
파일 이름: <사이트>-<공고번호>.html (요약), <사이트>-<공고번호>-detail.html (상세요강)
회사 채용 사이트는 company/<솔루션·회사>-<번호>.html 과 company/urls.json(파일 -> 링크·받은 시각·특징)
"""

import json
from pathlib import Path

import pytest

from jobfit.clean.html2text import html_to_text
from jobfit.clean.sections import drop_noise, find_questions, has_posting_body, split_sections, summary_fields
from jobfit.fetch.channel import read_channel
from jobfit.fetch.meta import read_posting_meta

LIVE = Path(__file__).parent / "fixtures_live"
PAIRS = sorted(p for p in LIVE.glob("*.html") if not p.stem.endswith("-detail")) if LIVE.is_dir() else []

pytestmark = pytest.mark.skipif(not PAIRS, reason="tests/fixtures_live/ 에 실제 페이지가 없다")


def _text(path: Path) -> str:
    return drop_noise(html_to_text(path.read_text(encoding="utf-8", errors="replace")))


@pytest.mark.parametrize("main", PAIRS, ids=[p.stem for p in PAIRS])
def test_live_page_still_parses(main: Path):
    meta = read_posting_meta(main.read_text(encoding="utf-8", errors="replace"))
    assert meta.company and meta.title, f"회사명·직무를 읽지 못함: {meta}"
    detail = main.with_name(main.stem + "-detail.html")
    body = _text(detail) if detail.exists() else _text(main)
    assert has_posting_body(body), "공고 본문으로 판정되지 않음"
    names = {s.name for s in split_sections(body)}
    assert names & {"자격요건", "주요업무"}, f"섹션: {names}"


@pytest.mark.parametrize("main", [p for p in PAIRS if p.stem.startswith("saramin")], ids=lambda p: p.stem)
def test_live_saramin_summary(main: Path):
    fields = summary_fields(_text(main))
    assert {"경력", "마감일"} <= set(fields), fields


# 공고를 본 곳과 지원하는 곳이 다른 실측 공고(2026-10) — 받아 둔 페이지가 있을 때만
LIVE_CHANNELS = {
    "saramin-55145352": ("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55145352", "홈페이지", "https://interxlab.career.greetinghr.com/ko/o/138437"),
    "jobkorea-50182629": ("https://www.jobkorea.co.kr/Recruit/GI_Read/50182629", "이메일", ""),
}


@pytest.mark.parametrize("main", [p for p in PAIRS if p.stem in LIVE_CHANNELS], ids=lambda p: p.stem)
def test_live_apply_channel(main: Path):
    url, via, link = LIVE_CHANNELS[main.stem]
    pages = [p for p in (main, main.with_name(main.stem + "-detail.html")) if p.exists()]
    htmls = [p.read_text(encoding="utf-8", errors="replace") for p in pages]
    ch = read_channel(url, htmls, "\n\n".join(_text(p) for p in pages))
    assert (ch.via, ch.url) == (via, link), ch


# 회사 채용 사이트(tests/fixtures_live/company/, 2026-10-30) — 페이지 이름 -> (읽을 회사명, 정제 결과)
# 정제 결과: True 본문 있음 · "closed" 마감돼 내려감 · 그 밖의 글자는 오류에 들어갈 이유
COMPANY = LIVE / "company"
COMPANY_EXPECT = {
    "greetinghr-interxlab-138437": ("인터엑스", True),
    "ninehire-tym-8mlf85bI": ("TYM", True),
    "ninehire-day1company-qqsxM2hR": ("데이원컴퍼니", True),
    "ninehire-akis-2WLBusEG": ("AK아이에스", "closed"),
    "recruiter-lx-125420": ("", "리크루터"),
    "recruiter-dbgroup-125713": ("", "리크루터"),
    "recruiter-s-oil-129856": ("", "리크루터"),
    "skcareers-R261762": ("", True),
    "cj-recruit-8767": ("", "화면을 스크립트로"),  # 개인정보 동의문 낱말로 본문 판정되던 페이지
    "lgcareers-1002196": ("", "화면을 스크립트로"),
}
COMPANY_PAGES = sorted(p for p in COMPANY.glob("*.html") if p.stem in COMPANY_EXPECT) if COMPANY.is_dir() else []


@pytest.mark.parametrize("page", COMPANY_PAGES, ids=lambda p: p.stem)
def test_live_company_site(page: Path, tmp_path):
    from jobfit.clean.posting import clean_job
    from jobfit.fetch.company import CLOSED_MARK
    from jobfit.paths import package_data_dir

    company, expect = COMPANY_EXPECT[page.stem]
    assert read_posting_meta(page.read_text(encoding="utf-8")).company == company
    url = json.loads((COMPANY / "urls.json").read_text(encoding="utf-8"))[page.name]["url"]
    (tmp_path / "source.html").write_bytes(page.read_bytes())
    (tmp_path / "fetch.json").write_text(json.dumps({"ok": True, "url": url, "title": ""}), encoding="utf-8")
    r = clean_job(tmp_path, package_data_dir(), url)
    if expect is True:
        assert r.ok, r.error
    elif expect == "closed":
        assert not r.ok and r.error.startswith(CLOSED_MARK)
    else:
        assert not r.ok and expect in r.error, r.error


@pytest.mark.parametrize("main", [p for p in PAIRS if p.stem.startswith("jobkorea")], ids=lambda p: p.stem)
def test_live_jobkorea_questions(main: Path):
    detail = main.with_name(main.stem + "-detail.html")
    if not detail.exists():
        pytest.skip("상세요강 없음")
    assert find_questions(_text(detail)) is not None
