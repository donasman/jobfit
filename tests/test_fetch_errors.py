"""받기 실패의 종류별 안내 — 네트워크를 쓰지 않고 requests 를 흉내 낸다."""

import pytest
import requests

from jobfit import cli, pipeline
from jobfit.fetch import fetcher
from jobfit.jobkey import derive
from jobfit.notices import HEADER
from jobfit.paths import Project
from jobfit.state import State

URL = "https://www.jobkorea.co.kr/Recruit/GI_Read/10000002"


@pytest.fixture(autouse=True)
def _no_robots_request(monkeypatch):
    """robots.txt 확인도 인터넷을 쓴다 — 테스트에서는 허용으로 둔다."""
    monkeypatch.setattr(fetcher.robots, "allowed", lambda *a, **k: True)


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
