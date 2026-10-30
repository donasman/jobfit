from jobfit import jobkey
from jobfit.paths import key_to_dirname


def test_saramin_relay_and_view_share_key():
    a = jobkey.derive("https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=10000001&utm_source=x")
    b = jobkey.derive("https://m.saramin.co.kr/zf_user/jobs/view?rec_idx=10000001")
    assert a.key == b.key == "saramin:10000001"
    assert a.site == "saramin" and a.number == "10000001"


def test_jobkorea_two_url_shapes():
    assert jobkey.derive("https://www.jobkorea.co.kr/Recruit/GI_Read/10000002?Oem_Code=C1").key == "jobkorea:10000002"
    assert jobkey.derive("https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno=10000002").key == "jobkorea:10000002"


def test_wanted_jumpit_programmers():
    assert jobkey.derive("https://www.wanted.co.kr/wd/123456").key == "wanted:123456"
    assert jobkey.derive("https://jumpit.saramin.co.kr/position/9876").key == "jumpit:9876"
    assert jobkey.derive("https://www.jumpit.co.kr/position/9876").key == "jumpit:9876"
    assert jobkey.derive("https://career.programmers.co.kr/job_positions/555").key == "programmers:555"


def test_toss_needs_both_ids():
    url = "https://toss.im/career/job-detail?job_id=1000000003&sub_position_id=1"
    assert jobkey.derive(url).key == "toss:1000000003-1"
    assert jobkey.derive("https://toss.im/career/job-detail?job_id=1000000003").key.startswith("url:toss.im/career/job-detail")


def test_unknown_site_strips_tracking_and_fragment():
    k = jobkey.derive("https://www.example.com/careers/42/?fbclid=abc&utm_medium=social&page=2#apply")
    assert k.key == "url:example.com/careers/42?page=2"
    assert k.site == "url"


def test_no_url_uses_company_title_deadline():
    a = jobkey.derive("", "예시컴퍼니", "Node.js개발자", "2026-11-15")
    b = jobkey.derive("", "예시컴퍼니", "Node.js개발자", "2026-11-15")
    c = jobkey.derive("", "예시컴퍼니", "Node.js개발자", "2026-12-01")
    assert a.key == b.key and a.key != c.key
    assert a.site == "local" and not a.has_url


def test_kind_board_company_local():
    assert jobkey.derive("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1").kind == "board"
    assert jobkey.derive("https://www.wanted.co.kr/wd/1").kind == "board"
    # 번호 규칙이 없는 플랫폼은 키는 url: 그대로, 게시처만 플랫폼
    k = jobkey.derive("https://job.incruit.com/jobdb_info/jobpost.asp?job=1")
    assert k.key.startswith("url:") and k.kind == "board"
    assert jobkey.derive("https://toss.im/career/job-detail?job_id=1&sub_position_id=2").kind == "company"
    assert jobkey.derive("https://interxlab.career.greetinghr.com/ko/o/138437").kind == "company"
    assert jobkey.derive("https://notincruit.com/x").kind == "company"  # 호스트 끝이 같아도 다른 도메인
    assert jobkey.derive("", "회사", "직무", "").kind == "local"


def test_dirname_is_windows_safe():
    assert key_to_dirname("saramin:10000001") == "saramin-10000001"
    d = key_to_dirname("url:example.com/careers/42?page=2")
    assert ":" not in d and "/" not in d and "?" not in d and d.startswith("url-example.com-")
