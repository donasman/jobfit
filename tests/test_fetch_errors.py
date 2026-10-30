"""받기·정제가 깨졌을 때 사용자가 막히지 않는지 — 네트워크를 쓰지 않고 requests 를 흉내 낸다.

실제 사이트 HTML(tests/fixtures_live/)은 배포본·공개 CI 에 없다. 여기 쓰는 페이지는 가상 회사로 만든
합성본이라 어디서나 돈다 — 사이트가 구조를 바꿨을 때 사용자가 받는 안내를 이 파일이 고정한다.
다섯 곳(사람인·잡코리아·그리팅·나인하이어·리크루터)에 대해 세 가지를 본다:
  1) HTTP 오류(404·403·500·타임아웃)  2) 본문이 들어가던 자리가 사라짐  3) 자리는 있는데 비어 있음
어느 경우에도 ① 왜 못 받았는지 ② 공고 본문 붙여넣기로 이어 가라는 안내가 나와야 한다.
인터넷이 막힌 실패만 안내가 다르다(이 PC에서 공고 처리 진행을 한 번 실행) — 사용자가 할 일이 다르기 때문이다.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from jobfit import cli, pipeline
from jobfit.clean.posting import clean_job
from jobfit.fetch import fetcher
from jobfit.jobkey import derive
from jobfit.notices import HEADER
from jobfit.paths import Project, package_data_dir
from jobfit.state import State
from test_company import BODY, RECRUITER, _greeting, _ninehire

URL = "https://www.jobkorea.co.kr/Recruit/GI_Read/10000002"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """robots.txt 확인도 인터넷을 쓴다 — 테스트에서는 허용으로 두고, 요청 사이 간격도 없앤다."""
    monkeypatch.setattr(fetcher.robots, "allowed", lambda *a, **k: True)
    monkeypatch.setattr(fetcher, "DELAY_SECONDS", 0)


def _raise(exc):
    def get(self, *args, **kwargs):
        raise exc

    return get


def test_proxy_error_points_to_running_on_this_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", _raise(requests.exceptions.ProxyError("blocked")))
    result = fetcher.fetch_job(URL, derive(URL), tmp_path / "job")
    assert not result.ok and result.network
    assert result.error == "인터넷 연결 문제로 받지 못했습니다(ProxyError)"
    assert "start.bat 3번(공고 처리 진행)" in result.next_hint
    assert result.notes == []  # 받지 못했으면 '상세요강을 따로 받았다' 같은 설명을 남기지 않는다


def test_other_failures_still_ask_for_paste(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", _raise(requests.exceptions.ReadTimeout("slow")))
    result = fetcher.fetch_job(URL, derive(URL), tmp_path / "job")
    assert not result.network and result.next_hint == fetcher.PASTE_HINT


def test_network_failure_reaches_the_user(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", _raise(requests.exceptions.ConnectionError("dns")))
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert cli.main(["--root", str(tmp_path), "add", URL, "--company", "예시컴퍼니", "--title", "백엔드", "--deadline", "2026-11-30"]) == 0
    capsys.readouterr()
    cli.main(["--root", str(tmp_path), "run"])
    out = capsys.readouterr().out
    block = out.split(HEADER, 1)[1]
    assert "공고 1개를 이 환경에서 받지 못했습니다(인터넷 연결 문제: 예시컴퍼니)" in block
    assert "이 PC에서 start.bat 3번(공고 처리 진행)을 한 번 실행해 주세요" in block
    assert "에이전트에게 보낼 말" in block
    action = pipeline.next_action(Project(tmp_path), State.load(Project(tmp_path).state_file))
    assert action.command.startswith("start.bat 3번(공고 처리 진행)")


# --- 다섯 사이트의 합성 페이지 ---


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


SARAMIN_MAIN = _read("saramin-sample.html")
SARAMIN_DETAIL = _read("saramin-sample-detail.html")
JOBKOREA_MAIN = _read("jobkorea-sample.html")
JOBKOREA_DETAIL = _read("jobkorea-sample-detail.html")

# 본문이 들어가던 자리가 통째로 사라진 판 — 사이트가 화면 구조를 바꾸면 이렇게 온다
_GONE_BODY = '<body>\n<div class="error">잠시 후 다시 시도해 주세요</div>\n</body>'
SARAMIN_DETAIL_GONE = re.sub(r"(?s)<body>.*</body>", _GONE_BODY, SARAMIN_DETAIL)
JOBKOREA_DETAIL_GONE = re.sub(r"(?s)<body>.*</body>", _GONE_BODY, JOBKOREA_DETAIL)
# 자리는 그대로인데 비어 있는 판
SARAMIN_DETAIL_EMPTY = re.sub(r'(?s)(<div class="user_content">).*(</div>)', r"\1\2", SARAMIN_DETAIL)
JOBKOREA_DETAIL_EMPTY = re.sub(r'(?s)(<div class="view-content-detail">).*(</div>)', r"\1\2", JOBKOREA_DETAIL)

WHY_DETAIL = "상세요강 페이지를 받았지만 본문 자리가 비어 있습니다"
WHY_UNKNOWN = "화면을 스크립트로 그리는 사이트"


@dataclass(frozen=True)
class Site:
    url: str
    ok: tuple[tuple[str, str], ...]  # (주소 조각, 돌려줄 HTML) — 앞에서부터 맞는 것
    gone: tuple[tuple[str, str], ...]
    empty: tuple[tuple[str, str], ...]
    why_gone: str
    why_empty: str


SITES = {
    "사람인": Site(
        url="https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001",
        ok=(("view-detail", SARAMIN_DETAIL), ("jobs/view", SARAMIN_MAIN)),
        gone=(("view-detail", SARAMIN_DETAIL_GONE), ("jobs/view", SARAMIN_MAIN)),
        empty=(("view-detail", SARAMIN_DETAIL_EMPTY), ("jobs/view", SARAMIN_MAIN)),
        why_gone=WHY_DETAIL,
        why_empty=WHY_DETAIL,
    ),
    "잡코리아": Site(
        url="https://www.jobkorea.co.kr/Recruit/GI_Read/10000002",
        ok=(("Ifrm", JOBKOREA_DETAIL), ("GI_Read", JOBKOREA_MAIN)),
        gone=(("Ifrm", JOBKOREA_DETAIL_GONE), ("GI_Read", JOBKOREA_MAIN)),
        empty=(("Ifrm", JOBKOREA_DETAIL_EMPTY), ("GI_Read", JOBKOREA_MAIN)),
        why_gone=WHY_DETAIL,
        why_empty=WHY_DETAIL,
    ),
    # 그리팅·나인하이어는 페이지에 박힌 데이터로 알아본다. 그 데이터 모양이 바뀌면 어느 솔루션인지도 모르게 된다
    "그리팅": Site(
        url="https://example.career.greetinghr.com/ko/o/1",
        ok=(("", _greeting()),),
        gone=(("", _greeting(detail="").replace("careerBaseInfo", "careerBaseInfoV2")),),
        empty=(("", _greeting(detail="")),),
        why_gone=WHY_UNKNOWN,
        why_empty="그리팅 공고의 본문 칸이 비어 있습니다",
    ),
    "나인하이어": Site(
        url="https://example.ninehire.site/job_posting/AbCd1234",
        ok=(("", _ninehire(BODY)),),
        gone=(("", _ninehire("").replace("companyName", "companyNameV2")),),
        empty=(("", _ninehire("")),),
        why_gone=WHY_UNKNOWN,
        why_empty="나인하이어 공고의 본문 칸이 비어 있습니다",
    ),
    # 리크루터는 본문을 화면이 뜬 뒤 불러온다 — 받은 HTML의 본문 자리(div#root)는 늘 비어 있다
    "리크루터": Site(
        url="https://example.recruiter.co.kr/career/jobs/12345",
        ok=(("", RECRUITER),),
        gone=(("", RECRUITER.replace('rel="canonical"', 'rel="canonical-v2"')),),
        empty=(("", RECRUITER),),
        why_gone=WHY_UNKNOWN,
        why_empty="리크루터 채용 페이지는 본문을 화면이 뜬 뒤 따로 불러옵니다",
    ),
}
NAMES = sorted(SITES)


class _Response:
    def __init__(self, text: str = "", status: int = 200):
        self.status_code = status
        self.content = text.encode("utf-8")
        self.headers = requests.structures.CaseInsensitiveDict({"Content-Type": "text/html; charset=utf-8"})


def _serve(monkeypatch, routes):
    def get(self, url, **kwargs):
        for part, page in routes:
            if part in url:
                return page if isinstance(page, _Response) else _Response(page)
        raise AssertionError(f"테스트가 준비하지 않은 주소: {url}")

    monkeypatch.setattr(requests.Session, "get", get)


def _run(tmp_path, monkeypatch, site: Site, routes):
    """받기 → 정제. 사용자가 보는 것은 둘 중 먼저 막히는 쪽의 오류·다음 할 일이다."""
    _serve(monkeypatch, routes)
    job_dir = tmp_path / "job"
    fetched = fetcher.fetch_job(site.url, derive(site.url), job_dir)
    if not fetched.ok:
        return fetched.error, fetched.next_hint, fetched.network
    cleaned = clean_job(job_dir, package_data_dir(), site.url)
    return cleaned.error, cleaned.next_hint, False


# --- 1. HTTP 오류 ---


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    ("status", "why"),
    [(404, "404 — 공고가 내려갔거나 주소가 틀렸습니다"), (403, "403 — 사이트가 접근을 막았습니다"), (500, "500 — 사이트 쪽 오류입니다")],
)
def test_http_error_says_why_and_asks_for_paste(tmp_path, monkeypatch, name, status, why):
    error, hint, network = _run(tmp_path, monkeypatch, SITES[name], [("", _Response("<html>오류</html>", status))])
    assert why in error
    assert hint == fetcher.PASTE_HINT and not network


@pytest.mark.parametrize("name", NAMES)
def test_timeout_asks_for_paste_not_for_a_rerun_on_this_pc(tmp_path, monkeypatch, name):
    site = SITES[name]
    monkeypatch.setattr(requests.Session, "get", _raise(requests.exceptions.ReadTimeout("slow")))
    result = fetcher.fetch_job(site.url, derive(site.url), tmp_path / "job")
    assert not result.ok and not result.network
    assert result.error == "30초 안에 응답이 없었습니다" and result.next_hint == fetcher.PASTE_HINT


@pytest.mark.parametrize("name", NAMES)
def test_blocked_internet_is_told_apart_from_a_site_failure(tmp_path, monkeypatch, name):
    """인터넷이 막힌 실패는 붙여넣기가 아니라 '이 PC에서 한 번 실행'이다 — 사용자가 할 일이 다르다."""
    site = SITES[name]
    monkeypatch.setattr(requests.Session, "get", _raise(requests.exceptions.ProxyError("blocked")))
    result = fetcher.fetch_job(site.url, derive(site.url), tmp_path / "job")
    assert result.network and fetcher.NETWORK_ERROR_MARK in result.error
    assert result.next_hint != fetcher.PASTE_HINT and "공고 처리 진행" in result.next_hint


# --- 2. 본문이 들어가던 자리가 사라짐 ---


@pytest.mark.parametrize("name", NAMES)
def test_body_slot_gone_says_why_and_asks_for_paste(tmp_path, monkeypatch, name):
    site = SITES[name]
    error, hint, network = _run(tmp_path, monkeypatch, site, list(site.gone))
    assert error.startswith("받은 페이지에 공고 본문이 없습니다") and site.why_gone in error
    assert hint == fetcher.PASTE_HINT and not network


# --- 3. 본문 자리는 있는데 비어 있음 ---


@pytest.mark.parametrize("name", NAMES)
def test_body_slot_empty_says_why_and_asks_for_paste(tmp_path, monkeypatch, name):
    site = SITES[name]
    error, hint, network = _run(tmp_path, monkeypatch, site, list(site.empty))
    assert error.startswith("받은 페이지에 공고 본문이 없습니다") and site.why_empty in error
    assert hint == fetcher.PASTE_HINT and not network


# --- 멀쩡할 때는 그대로 간다(위 세 가지가 정상 공고를 막지 않는지) ---


@pytest.mark.parametrize("name", ["사람인", "잡코리아", "그리팅", "나인하이어"])
def test_sound_pages_still_pass(tmp_path, monkeypatch, name):
    site = SITES[name]
    _serve(monkeypatch, list(site.ok))
    job_dir = tmp_path / "job"
    assert fetcher.fetch_job(site.url, derive(site.url), job_dir).ok
    result = clean_job(job_dir, package_data_dir(), site.url)
    assert result.ok, result.error
    assert {"자격요건", "주요업무"} <= set(result.sections)


# --- 상세요강만 못 받았을 때(주소 규칙이 바뀌면 이렇게 된다) ---


@pytest.mark.parametrize("name", ["사람인", "잡코리아"])
def test_detail_page_failure_is_named_not_blamed_on_scripts(tmp_path, monkeypatch, name):
    """요약 페이지는 200인데 상세요강만 404 — 왜 못 읽었는지 그대로 말해야 한다."""
    site = SITES[name]
    routes = [(site.ok[0][0], _Response("", 404)), (site.ok[1][0], site.ok[1][1])]
    error, hint, _ = _run(tmp_path, monkeypatch, site, routes)
    assert "상세요강 페이지를 받지 못했습니다(응답 404)" in error
    assert hint == fetcher.PASTE_HINT


@pytest.mark.parametrize("name", ["사람인", "잡코리아"])
def test_detail_page_blocked_by_robots_is_named(tmp_path, monkeypatch, name):
    site = SITES[name]
    monkeypatch.setattr(fetcher.robots, "allowed", lambda url, *a, **k: site.ok[0][0] not in url)
    error, hint, _ = _run(tmp_path, monkeypatch, site, list(site.ok))
    assert "상세요강 페이지를 robots.txt가 막아 받지 못했습니다" in error
    assert hint == fetcher.PASTE_HINT


# --- 데이터 모양이 바뀌어도 화면 글자에 본문이 있으면 이어 간다 ---


def test_greeting_falls_back_to_the_screen_when_the_data_shape_changes(tmp_path, monkeypatch):
    site = SITES["그리팅"]
    _serve(monkeypatch, [("", _greeting(detail=BODY).replace("careerBaseInfo", "careerBaseInfoV2"))])
    job_dir = tmp_path / "job"
    assert fetcher.fetch_job(site.url, derive(site.url), job_dir).ok
    result = clean_job(job_dir, package_data_dir(), site.url)
    assert result.ok and {"자격요건", "주요업무"} <= set(result.sections)


# --- 사용자에게 닿는가 ---


def test_a_broken_site_reaches_the_user_as_a_paste_notice(tmp_path, capsys, monkeypatch):
    site = SITES["잡코리아"]
    _serve(monkeypatch, list(site.gone))
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert cli.main(["--root", str(tmp_path), "add", site.url, "--company", "예시컴퍼니", "--title", "백엔드", "--deadline", "2026-11-30"]) == 0
    capsys.readouterr()
    cli.main(["--root", str(tmp_path), "run"])
    block = capsys.readouterr().out.split(HEADER, 1)[1]
    assert "공고 본문을 붙여 넣어야 합니다: 예시컴퍼니" in block
    assert WHY_DETAIL in block
    assert "이 PC에서" not in block  # 인터넷이 막힌 실패와 섞이지 않는다
