from pathlib import Path

from jobfit.fetch.meta import clean_company, clean_job_title, read_posting_meta

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def test_jobkorea_json_ld():
    meta = read_posting_meta(_html("jobkorea-sample.html"))
    assert meta.source == "json-ld"
    assert meta.company == "예시컴퍼니"
    assert meta.title == "Node.js개발자(신입)"
    assert meta.deadline == "2026-11-15"


def test_saramin_title_and_summary_deadline():
    meta = read_posting_meta(_html("saramin-sample.html"))
    assert meta.source == "title"
    assert meta.company == "예시랩"
    assert meta.title == "백엔드 (Backend) 개발자"  # 폭 없는 공백·(D-7)·사이트 이름이 빠진다
    assert meta.deadline == "2026-10-27"


def test_unknown_site_title_is_not_guessed():
    meta = read_posting_meta("<html><head><title>Backend Engineer - Acme Careers</title></head><body>hi</body></html>")
    assert meta.company == "" and meta.title == "" and meta.source == ""


def test_name_cleaning():
    assert clean_company("㈜예시컴퍼니(example)") == "예시컴퍼니"
    assert clean_company("(주)예시랩") == "예시랩"
    assert clean_company("주식회사 카카오") == "카카오"
    assert clean_job_title("[시리즈A투자유치] Node.js개발자(신입)") == "Node.js개발자(신입)"
    assert clean_job_title("백엔드 ​개발자(D-7) - 사람인") == "백엔드 개발자"
