"""사이트별 수집 규칙 — 본문이 공고 주소가 아닌 곳에 있는 사이트를 안다.

실측(2026-10):
- 사람인: relay/view 주소는 메뉴 글자만 온다. 요약은 jobs/view?rec_idx=N,
  상세요강은 jobs/relay/view-detail?rec_idx=N&rec_seq=0 에 있다.
- 잡코리아: 공고 주소에는 메뉴·요약뿐. 상세요강은 GI_Read_Comt_Ifrm?Gno=N (iframe).
- 그 밖: 받은 HTML에서 같은 사이트 iframe을 찾아 하나 더 받는다.
새 사이트는 여기 규칙 하나를 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobfit.jobkey import JobKey


@dataclass(frozen=True)
class FetchPlan:
    main_url: str  # 요약·제목이 있는 페이지
    detail_urls: tuple[str, ...] = ()  # 상세요강 페이지(들)
    referer: str = ""
    follow_iframes: bool = False  # 모르는 사이트만. 같은 호스트 iframe 1개까지
    notes: tuple[str, ...] = field(default_factory=tuple)


def plan_for(url: str, key: JobKey) -> FetchPlan:
    if key.site == "saramin" and key.number:
        n = key.number
        return FetchPlan(
            main_url=f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={n}",
            detail_urls=(f"https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={n}&rec_seq=0",),
            referer=url,
            notes=("사람인: 요약 페이지와 상세요강을 따로 받았다",),
        )
    if key.site == "jobkorea" and key.number:
        n = key.number
        return FetchPlan(
            main_url=f"https://www.jobkorea.co.kr/Recruit/GI_Read/{n}",
            detail_urls=(f"https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno={n}",),
            referer=url,
            notes=("잡코리아: 상세요강 iframe을 따로 받았다",),
        )
    if key.site == "wanted":
        return FetchPlan(
            main_url=url,
            notes=("원티드: 화면을 스크립트로 그리는 사이트라 본문이 비어 올 수 있다",),
        )
    return FetchPlan(main_url=url, follow_iframes=True)
