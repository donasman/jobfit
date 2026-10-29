"""실제 채용 사이트 페이지로 하는 회귀 테스트 — tests/fixtures_live/ 가 있을 때만 돈다(배포본에는 없다).

사이트 HTML 구조가 바뀌어 파서가 깨졌는지 확인하는 용도. 페이지를 새로 받아 이 폴더에 두고 돌린다.
파일 이름: <사이트>-<공고번호>.html (요약), <사이트>-<공고번호>-detail.html (상세요강)
"""

from pathlib import Path

import pytest

from jobfit.clean.html2text import html_to_text
from jobfit.clean.sections import drop_noise, find_questions, has_posting_body, split_sections, summary_fields
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


@pytest.mark.parametrize("main", [p for p in PAIRS if p.stem.startswith("jobkorea")], ids=lambda p: p.stem)
def test_live_jobkorea_questions(main: Path):
    detail = main.with_name(main.stem + "-detail.html")
    if not detail.exists():
        pytest.skip("상세요강 없음")
    assert find_questions(_text(detail)) is not None
