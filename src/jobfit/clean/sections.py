"""공고 글자에서 구조를 읽는다 — 본문 유무, 섹션, 요약 칸, 문항 후보.

판단은 전부 정규식이다. 글자 수로 본문 유무를 판단하지 않는다(메뉴만 1,510자인 페이지가 있었다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 섹션 이름(정규화) -> 헤더 정규식. 헤더로 보려면 줄이 짧아야 한다(_HEADER_MAX_LEN)
SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("모집부문", re.compile(r"모집\s*(부문|분야|직무|포지션)|채용\s*(부문|분야|포지션)|포지션\s*소개")),
    # 스타트업 공고는 "이런 업무를 해요 / 이런 분을 찾아요 / 이런 분이면 더 좋아요 / 함께하면 이런 점들이 좋아요" 식으로 쓴다
    ("주요업무", re.compile(r"(주요|담당)\s*업무|업무\s*(내용|소개)|이런\s*(일|업무)(을|를)?\s*(해요|합니다|하게|맡)|하게\s*될\s*일|What you will do|Responsibilities", re.I)),
    ("자격요건", re.compile(r"자격\s*(요건|조건)|지원\s*(자격|요건)|필수\s*(요건|자격|역량|조건|경험|스택|기술)|필요\s*(역량|스택|기술|경험)|필요한\s*(역량|경험)|기술\s*스택|이런\s*분(들)?(을|과|이)\s*(찾|기다|함께|필요)|이런\s*(기술|경험)(이|을)\s*(필요|있|가진)|Requirements|Qualifications", re.I)),
    ("우대사항", re.compile(r"우대\s*(사항|조건|요건)|이런\s*분이면\s*더|이런\s*경험이\s*있다면|있으면\s*(더\s*)?좋아요|더\s*좋아요|Preferred|Nice to have", re.I)),
    ("인재상", re.compile(r"인재상|핵심\s*가치|우리가\s*찾는\s*사람|함께\s*하고\s*싶은")),
    ("자기소개서", re.compile(r"자기\s*소개서|자소서\s*(문항|항목)|서류\s*작성\s*(요령|가이드|팁)|지원서\s*(문항|작성)")),
    ("전형절차", re.compile(r"(전형|채용|선발)\s*(절차|과정|프로세스|단계)|진행\s*절차|채용\s*절차|(합류|채용)\s*여정|이렇게\s*진행|Process", re.I)),
    ("근무조건", re.compile(r"근무\s*(조건|환경|지역|형태|시간|요일|장소)|급여|연봉|고용\s*형태|처우\s*조건|근무할\s*예정|이런\s*조건에서")),
    ("복리후생", re.compile(r"복리\s*후생|복지|혜택|이런\s*점(들)?이\s*좋아요|함께하면\s*이런|Benefits", re.I)),
    ("회사소개", re.compile(r"회사\s*소개|기업\s*소개|팀\s*소개|우리\s*팀은|(을|를)\s*소개(해요|합니다)|소개해요|이런\s*팀이에요|About", re.I)),
    ("접수방법", re.compile(r"접수\s*(방법|기간|안내|마감)|지원\s*(방법|기간)|제출\s*서류|유의\s*사항|유의해\s*주세요|지원\s*시\s*유의|기타\s*(사항|안내)|참고\s*사항")),
]
_HEADER_MAX_LEN = 30
_HEADER_EXTRA_MAX = 14  # 헤더 줄에서 키워드를 뺀 나머지 글자 수 한도. "필수 경험과 역량"은 헤더, "경력 3년 이상인 분"은 내용
_BULLETS = "#-•·ㆍ▪■□●○◆◇▶▷※*> "
_RE_KEY_VALUE = re.compile(r"[:：]\s*\S")  # "근무요일 : 주 5일" 같은 값 줄은 헤더가 아니다
_RE_LI_MARK = re.compile(r"^-\s+")  # html2text 가 <li> 에 붙인 "- ". 구조 표시일 뿐이라 헤더 판정 전에 뗀다
_RE_BRACKETS = re.compile(r"^[\[\(【<]\s*|\s*[\]\)】>]$")  # "[근무 조건]" 같은 괄호 헤더
_RE_NAME_DECOR = re.compile(r"\([^)]{1,30}\)|㈜|\(주\)")

# 본문 유무 판정에 쓰는 묶음. 둘 이상 + (자격요건 또는 주요업무) 있어야 본문이다
_BODY_GROUPS = {
    "자격요건": re.compile(r"자격\s*요건|지원\s*자격|필수\s*(요건|자격|역량|경험|스택)|필요\s*(스택|역량)|필요한\s*(역량|경험)|이런\s*분(들)?(을|과|이)\s*(찾|기다|함께)|이런\s*기술이\s*필요|Requirements|Qualifications", re.I),
    "주요업무": re.compile(r"(주요|담당)\s*업무|업무\s*내용|이런\s*(일|업무)(을|를)?\s*(해요|합니다|하게)|하게\s*될\s*일", re.I),
    "우대사항": re.compile(r"우대\s*(사항|조건)|Preferred", re.I),
    "모집부문": re.compile(r"모집\s*(부문|분야|직무)|채용\s*(부문|분야)"),
    "전형절차": re.compile(r"(전형|채용)\s*(절차|과정|프로세스)|진행\s*절차"),
    "근무조건": re.compile(r"근무\s*(조건|지역|형태|시간|요일)|급여|연봉|고용\s*형태"),
}

# 요약 칸(경력·학력·급여…). 한 줄 "경력 | 신입"·"경력: 신입" 과, 사람인처럼 키 줄 다음에 값 줄이 오는 꼴 둘 다 읽는다
SUMMARY_KEYS = ("경력", "학력", "급여", "연봉", "근무형태", "고용형태", "근무지역", "근무지", "근무요일", "근무시간", "마감일", "접수기간", "접수마감", "직급", "직책", "모집인원", "스킬", "핵심역량", "우대조건")
_RE_SUMMARY = re.compile(r"^(" + "|".join(SUMMARY_KEYS) + r")\s*(?:\||:|：)\s*(.+)$")
_SUMMARY_VALUE_MAX = 60

# 메뉴·푸터로 보이는 짧은 줄
_NOISE_WORDS = re.compile(
    r"^(로그인|회원가입|고객센터|이용약관|개인정보\s*처리방침|개인정보처리방침|즐겨찾기|스크랩|인쇄|공유|신고|닫기|홈|메뉴|검색|전체\s*메뉴|TOP|더보기|바로가기|관심기업|지원하기|즉시지원|홈페이지\s*지원|입사지원|기업정보|채용정보|공채속보|취업톡톡|헤드헌팅|HR\s*매거진|아웃소싱|이력서|자소서|커리어|사람인|잡코리아|원티드)\s*$",
    re.I,
)
_NOISE_MAX_LEN = 14

# 자소서 문항처럼 보이는 문장. 글자수는 "800자" · "1,000자" 둘 다 읽는다
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{2,5})"
_RE_QUESTION = re.compile(
    r"(기술|서술|작성|설명|소개|말씀|적어|써|알려|들려|이야기해|공유해)\s*(하시오|해\s*주십시오|해\s*주세요|하세요|해주세요|하십시오|주십시오|주세요|해\s*주시기\s*바랍니다|하여\s*주시기|주시기\s*바랍니다)|"
    rf"\(\s*(?:최대|공백\s*(?:포함|제외)\s*,?\s*)?\s*{_NUM}\s*자\s*(?:이내|내외|이하)?\s*\)|{_NUM}\s*자\s*(?:이내|내외|이하)"
)
_RE_LIMIT = re.compile(rf"({_NUM})\s*자")
_RE_LEAD_BULLETS = re.compile(r"^[\s\-•·ㆍ▪■□●○※*]+")  # "- 　- 지원동기" 처럼 겹친 글머리도


@dataclass
class Section:
    name: str  # 정규화 이름 또는 "머리"
    header: str  # 원문 헤더 줄
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


@dataclass
class QuestionCandidate:
    text: str
    limit: int | None = None


def has_posting_body(text: str) -> bool:
    found = {name for name, pat in _BODY_GROUPS.items() if pat.search(text)}
    return len(found) >= 2 and bool(found & {"자격요건", "주요업무"})


def drop_noise(text: str) -> str:
    """메뉴·푸터로 보이는 짧은 줄과 연속 중복 줄을 뺀다. 본문 줄은 건드리지 않는다."""
    out: list[str] = []
    prev = None
    for line in text.splitlines():
        s = line.strip()
        if s and len(s) <= _NOISE_MAX_LEN and _NOISE_WORDS.match(s):
            continue
        if s and s == prev:
            continue
        out.append(line)
        prev = s or prev
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = [Section(name="머리", header="")]
    for line in text.splitlines():
        raw = _RE_LI_MARK.sub("", line.strip().strip("| ").strip())
        core = _RE_BRACKETS.sub("", raw.lstrip(_BULLETS)).strip()
        name = _header_name(raw, core)
        if name:
            sections.append(Section(name=name, header=core))
        else:
            sections[-1].lines.append(line.strip())
    return [sec for sec in sections if sec.text or sec.name != "머리"]


def _header_name(raw: str, core: str) -> str | None:
    """짧고, 글머리로 시작하지 않고, 값(:)이 붙지 않은 줄이 섹션 키워드와 맞으면 헤더다."""
    if not core or len(core) > _HEADER_MAX_LEN:
        return None
    if raw[:1] in "ㆍ•·-▪*":
        return None
    if _RE_KEY_VALUE.search(core):
        return None
    # 회사 이름의 괄호·㈜는 길이에서 뺀다 — "㈜예시컴퍼니(example)를 소개해요"가 이름 길이 때문에 헤더에서 빠지지 않게
    bare = _RE_NAME_DECOR.sub("", core)
    for name, pat in SECTION_PATTERNS:
        m = pat.search(bare)
        if m and len(bare) - (m.end() - m.start()) <= _HEADER_EXTRA_MAX:
            return name
    return None


def summary_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = [ln.strip().strip("| ").strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        m = _RE_SUMMARY.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip(" |")
            if key not in fields and value and len(value) <= _SUMMARY_VALUE_MAX:
                fields[key] = value
            continue
        if line in SUMMARY_KEYS and line not in fields:
            # 키 줄 → (빈 줄) → 값 줄. 다음 키가 먼저 나오거나 값이 길면 요약 칸이 아니다
            for value in lines[i + 1 : i + 4]:
                if not value:
                    continue
                if value in SUMMARY_KEYS or len(value) > _SUMMARY_VALUE_MAX:
                    break
                fields[line] = value
                break
    return fields


def find_questions(text: str) -> list[QuestionCandidate]:
    """문항처럼 보이는 줄. 원문 그대로 둔다 — 요약하거나 고치지 않는다."""
    out: list[QuestionCandidate] = []
    seen: set[str] = set()
    for line in text.splitlines():
        s = _RE_LEAD_BULLETS.sub("", line).strip()
        if len(s) < 8 or len(s) > 300 or s in seen or not _RE_QUESTION.search(s):
            continue
        seen.add(s)
        m = _RE_LIMIT.search(s)
        out.append(QuestionCandidate(text=s, limit=int(m.group(1).replace(",", "")) if m else None))
    return out


def clean_title(title: str) -> str:
    """페이지 제목 끝의 사이트 이름을 뗀다. '… - 사람인', '… | 잡코리아'"""
    return re.sub(r"\s*[-|–—:]\s*(사람인|잡코리아|원티드|점핏|리멤버|프로그래머스|Wanted|Saramin|JobKorea)\s*$", "", title, flags=re.I).strip()
