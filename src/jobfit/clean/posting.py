"""clean 단계 — source*.html(또는 사용자가 붙여 넣은 posting.txt) → posting.txt, posting.full.txt, meta.json.

posting.txt는 AI 프롬프트에 들어가는 압축본이다(섹션 우선순위대로 최대 POSTING_LIMIT자).
posting.full.txt는 걷어낸 글자 전체다.

posting.txt를 누가 썼는지는 meta.json의 해시로 안다. 스크립트가 쓴 것과 해시가 다르면 사용자가
붙여 넣거나 고친 것이고, 그때부터는 받은 HTML보다 그 글을 우선하며 고치지 않는다.
받은 원문으로 돌아가려면 posting.txt를 지운다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jobfit.clean.html2text import html_to_text
from jobfit.clean.keywords import KeywordDict
from jobfit.clean.sections import Section, clean_title, drop_noise, find_questions, has_posting_body, split_sections, summary_fields
from jobfit.fetch.fetcher import FILE_DETAIL, FILE_MAIN, FILE_META

FILE_POSTING = "posting.txt"
FILE_FULL = "posting.full.txt"
FILE_CLEAN_META = "meta.json"

POSTING_LIMIT = 2500

# 프롬프트에 넣을 섹션 우선순위. 앞에서부터 채우고 한도를 넘기면 뒤를 자른다
SECTION_PRIORITY = ("자격요건", "주요업무", "우대사항", "자기소개서", "인재상", "모집부문", "전형절차", "근무조건", "접수방법", "회사소개", "머리", "복리후생")


@dataclass
class CleanResult:
    ok: bool
    source: str  # "fetched" | "pasted"
    title: str = ""
    has_body: bool = False
    summary: dict[str, str] = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)  # 발견한 섹션 이름(원문 순서)
    keywords: dict[str, int] = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    posting_chars: int = 0
    full_chars: int = 0
    truncated: bool = False
    posting_sha1: str = ""  # 스크립트가 쓴 posting.txt의 해시. 다르면 사용자가 고친 것
    error: str = ""
    next_hint: str = ""


def clean_job(job_dir: Path, data_dir: Path) -> CleanResult:
    fetch_meta = _load_json(job_dir / FILE_META)
    if is_user_posting(job_dir):
        result = _clean_pasted(job_dir, data_dir)
    elif (job_dir / FILE_MAIN).exists() and fetch_meta.get("ok"):
        result = _clean_fetched(job_dir, fetch_meta, data_dir)
    else:
        return CleanResult(ok=False, source="none", error="공고 본문이 없습니다", next_hint="공고 본문 붙여넣기 (output 폴더의 posting.txt)")

    (job_dir / FILE_CLEAN_META).write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def is_user_posting(job_dir: Path) -> bool:
    """posting.txt가 사용자(또는 에이전트의 웹 조회)가 넣은 글인가."""
    path = job_dir / FILE_POSTING
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return False
    meta = _load_json(job_dir / FILE_CLEAN_META)
    if meta.get("source") == "pasted":
        return True
    return meta.get("posting_sha1") != _sha1(text)


def _clean_pasted(job_dir: Path, data_dir: Path) -> CleanResult:
    # 붙여 넣은 글은 사용자가 본문이라고 준 것이다 — 본문 판정이 안 되어도 막지 않고 meta에만 남긴다.
    # 글은 고치지 않는다(연락처 가림은 프롬프트를 만들 때 한다)
    text = (job_dir / FILE_POSTING).read_text(encoding="utf-8", errors="replace")
    (job_dir / FILE_FULL).write_text(text, encoding="utf-8")
    result = CleanResult(ok=True, source="pasted", title=_first_line(text), has_body=has_posting_body(text))
    result.full_chars = result.posting_chars = len(text)
    result.summary = summary_fields(text)
    result.sections = [s.name for s in split_sections(text) if s.name != "머리"]
    result.truncated = len(text) > POSTING_LIMIT
    result.posting_sha1 = _sha1(text)
    _analyze(result, text, text, data_dir)
    return result


def _clean_fetched(job_dir: Path, fetch_meta: dict, data_dir: Path) -> CleanResult:
    main_text = drop_noise(html_to_text((job_dir / FILE_MAIN).read_text(encoding="utf-8", errors="replace")))
    detail_path = job_dir / FILE_DETAIL
    detail_text = drop_noise(html_to_text(detail_path.read_text(encoding="utf-8", errors="replace"))) if detail_path.exists() else ""
    title = clean_title(fetch_meta.get("title", "") or _first_line(detail_text or main_text))

    # 본문은 상세 페이지에 있을 때 그것을, 없으면 요약 페이지를 쓴다. 요약 칸은 요약 페이지에서
    body_source = detail_text if detail_text and has_posting_body(detail_text) else main_text
    full_text = "\n\n".join(t for t in (main_text, detail_text) if t)
    (job_dir / FILE_FULL).write_text(full_text, encoding="utf-8")

    result = CleanResult(ok=True, source="fetched", title=title, has_body=has_posting_body(body_source), full_chars=len(full_text))
    result.summary = summary_fields(main_text) or summary_fields(detail_text)
    if not result.has_body:
        result.ok = False
        result.error = "받은 페이지에 공고 본문이 없습니다 (화면을 스크립트로 그리는 사이트·로그인 필요·그림 공고)"
        result.next_hint = "공고 본문 붙여넣기 (output 폴더의 posting.txt)"
        return result

    sections = split_sections(body_source)
    result.sections = [s.name for s in sections if s.name != "머리"]
    posting = _compose_posting(title, result.summary, sections)
    result.truncated = len(posting) > POSTING_LIMIT
    if result.truncated:
        posting = _truncate(title, result.summary, sections)
    # 프롬프트에 들어가는 사본에서는 담당자 이메일·전화를 가린다. 전체 글자는 posting.full.txt에 그대로 있다
    posting = mask_contacts(posting)
    (job_dir / FILE_POSTING).write_text(posting, encoding="utf-8")
    result.posting_chars = len(posting)
    result.posting_sha1 = _sha1(posting)
    _analyze(result, body_source, full_text, data_dir)
    return result


def _analyze(result: CleanResult, body_text: str, full_text: str, data_dir: Path) -> None:
    result.questions = [asdict(q) for q in find_questions(body_text)]
    if data_dir.exists():
        result.keywords = KeywordDict.load(data_dir).find(full_text)


def _order(sections: list[Section]) -> list[Section]:
    """우선순위대로. 자격요건·주요업무를 못 찾았으면 머리(헤더 없는 본문)가 그 역할이라 맨 앞에 둔다."""
    names = {s.name for s in sections}
    head_first = not (names & {"자격요건", "주요업무"})

    def rank(s: Section) -> int:
        if head_first and s.name == "머리":
            return -1
        return SECTION_PRIORITY.index(s.name) if s.name in SECTION_PRIORITY else len(SECTION_PRIORITY)

    return sorted(sections, key=rank)


def _compose_posting(title: str, summary: dict[str, str], sections: list[Section]) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if summary:
        parts.append("요약: " + " · ".join(f"{k} {v}" for k, v in summary.items()))
    for sec in _order(sections):
        if not sec.text:
            continue
        head = f"## {sec.header}" if sec.header else "## 공고 본문"
        parts.append(f"{head}\n{sec.text}")
    return "\n\n".join(parts).strip()


def _truncate(title: str, summary: dict[str, str], sections: list[Section]) -> str:
    """우선순위 낮은 섹션부터 통째로 빼고, 그래도 넘치면 마지막 섹션을 자른다."""
    kept = _order(sections)
    while kept and len(_compose_posting(title, summary, kept)) > POSTING_LIMIT:
        if len(kept) == 1:
            last = kept[0]
            # 제목·요약 + "\n\n## 헤더\n" + 본문 + " …(잘림)" 이 한도 안에 들도록 여유를 둔다
            budget = POSTING_LIMIT - len(_compose_posting(title, summary, [])) - len(last.header) - 20
            kept = [Section(name=last.name, header=last.header, lines=[last.text[: max(budget, 200)] + " …(잘림)"])]
            break
        kept.pop()
    return _compose_posting(title, summary, kept)


_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")


def mask_contacts(text: str) -> str:
    return _RE_PHONE.sub("[전화]", _RE_EMAIL.sub("[이메일]", text))


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("# ").strip()
        if s:
            return s[:120]
    return ""


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
