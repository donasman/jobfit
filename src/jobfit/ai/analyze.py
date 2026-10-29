"""공고 분석(AI 1회) — prep: analyze.prompt.md 만들기 / accept: 답 검사 → analysis.json.

보장은 여기서 한다:
- 목록 개수 제한(넘치면 앞에서 자르고 경고), 키워드 동의어 정규화
- 문항은 공고 원문(posting.full.txt)에 실제로 있는 것만. 글자수는 원문에 그 숫자가 있을 때만
- 형식이 틀리면 이유를 붙인 재요청 프롬프트를 만든다
- 같은 프롬프트(같은 공고·같은 태그·같은 템플릿)의 결과는 캐시에서 다시 쓴다
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from jobfit.ai.jsonx import extract_json
from jobfit.ai.promptfile import LIMITS, load_template, log_usage, render, sha1
from jobfit.clean.keywords import KeywordDict
from jobfit.clean.posting import FILE_CLEAN_META, FILE_FULL, FILE_POSTING, POSTING_LIMIT, mask_contacts
from jobfit.experiences import load_cards, tag_list
from jobfit.paths import Project
from jobfit.state import Job, now

STEP = "analyze"
FILE_PROMPT = "analyze.prompt.md"
FILE_RETRY = "analyze.retry.prompt.md"
FILE_RESPONSE = "analyze.response.json"
FILE_RESULT = "analysis.json"

LIST_LIMITS = {"must": 6, "nice": 6, "duties": 5, "values": 3, "keywords": 15}
_KEYWORDS_IN_PROMPT = 20
_CANDIDATES_IN_PROMPT = 10


@dataclass
class Prepared:
    status: str  # "cached" | "ready"
    prompt_path: Path
    chars: int = 0
    limit: int = LIMITS[STEP]

    @property
    def over_limit(self) -> bool:
        return self.chars > self.limit


@dataclass
class Accepted:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    analysis: dict | None = None
    retry_path: Path | None = None
    attempt: int = 0


# --- prep ---


def prepare(project: Project, job: Job, force: bool = False) -> Prepared:
    """프롬프트를 만든다. 같은 입력의 결과가 캐시에 있으면 AI 단계를 건너뛰고 analysis.json을 바로 쓴다."""
    ai_dir = project.ai_dir(job.key)
    text = build_prompt(project, job)
    digest = sha1(text)
    cache = project.cache_dir / f"{STEP}-{digest}.json"
    if cache.exists() and not force:
        analysis = json.loads(cache.read_text(encoding="utf-8"))
        _write_result(project, job, analysis)
        job.mark(STEP, done=True, cached=True, prompt_sha1=digest)
        log_usage(project, job.key, STEP, "cached", len(text))
        return Prepared(status="cached", prompt_path=ai_dir / FILE_PROMPT, chars=len(text))

    ai_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = ai_dir / FILE_PROMPT
    unchanged = prompt_path.exists() and prompt_path.read_text(encoding="utf-8") == text
    if not unchanged:
        prompt_path.write_text(text, encoding="utf-8")
        # 입력이 바뀌었으면 앞 프롬프트에 대한 답·재요청은 버린다
        for stale in (ai_dir / FILE_RETRY, ai_dir / FILE_RESPONSE):
            stale.unlink(missing_ok=True)
        log_usage(project, job.key, STEP, "prompt", len(text))
    if not unchanged or STEP not in job.steps or job.steps[STEP].get("prompt_sha1") != digest:
        job.mark(STEP, done=False, prompt=FILE_PROMPT, prompt_sha1=digest, attempts=0)
    current = ai_dir / job.steps[STEP].get("prompt", FILE_PROMPT)
    if not current.exists():
        current = prompt_path
    return Prepared(status="ready", prompt_path=current, chars=len(current.read_text(encoding="utf-8")))


def build_prompt(project: Project, job: Job) -> str:
    job_dir = project.job_dir(job.key)
    posting = mask_contacts((job_dir / FILE_POSTING).read_text(encoding="utf-8", errors="replace")).strip()
    if len(posting) > POSTING_LIMIT:
        posting = posting[:POSTING_LIMIT].rstrip() + " …(잘림)"
    meta = _load_json(job_dir / FILE_CLEAN_META)
    kd = KeywordDict.load(project.data_dir)
    cards, _ = load_cards(project.experiences_file)
    tags = tag_list(cards, kd)
    keywords = list(meta.get("keywords", {}))[:_KEYWORDS_IN_PROMPT]
    candidates = [q.get("text", "") for q in meta.get("questions", [])][:_CANDIDATES_IN_PROMPT]
    values = {
        "response_path": project.rel(project.ai_dir(job.key) / FILE_RESPONSE),
        "job": job.key,
        "company": job.company,
        "title": job.title or "(직무 미기재)",
        "tags": ", ".join(tags) if tags else "(경험 카드 없음 — keywords는 공고 표기 그대로 씁니다)",
        "keywords": ", ".join(keywords) if keywords else "(없음)",
        "candidates": "\n".join(f"- {c}" for c in candidates if c) or "(없음)",
        "posting": posting,
    }
    return render(load_template(project, STEP), values)


# --- accept ---


def accept(project: Project, job: Job, raw: str | None = None) -> Accepted:
    """raw가 있으면(클립보드·파일) 답 파일로 저장한 뒤 검사한다. 없으면 답 파일(에이전트가 쓴 것)을 읽는다."""
    ai_dir = project.ai_dir(job.key)
    response_path = ai_dir / FILE_RESPONSE
    if raw is not None:
        ai_dir.mkdir(parents=True, exist_ok=True)
        response_path.write_text(raw, encoding="utf-8")
    if not response_path.exists():
        return Accepted(ok=False, errors=[f"답 파일이 없습니다: {project.rel(response_path)}"])
    text = response_path.read_text(encoding="utf-8", errors="replace")
    log_usage(project, job.key, STEP, "response", len(text))

    info = job.steps.setdefault(STEP, {"done": False})
    try:
        data = extract_json(text)
        result = validate(data, _full_text(project, job), KeywordDict.load(project.data_dir))
    except ValueError as e:
        result = Accepted(ok=False, errors=[str(e)])
    if not result.ok:
        info["attempts"] = int(info.get("attempts", 0)) + 1
        result.attempt = info["attempts"]
        result.retry_path = _write_retry(project, job, result.errors, result.attempt)
        info["prompt"] = FILE_RETRY
        info["at"] = now()
        return result

    analysis = result.analysis or {}
    analysis["accepted_at"] = now()
    analysis["prompt_sha1"] = info.get("prompt_sha1", "")
    _write_result(project, job, analysis)
    if analysis["prompt_sha1"]:
        project.cache_dir.mkdir(parents=True, exist_ok=True)
        (project.cache_dir / f"{STEP}-{analysis['prompt_sha1']}.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    job.mark(
        STEP,
        done=True,
        prompt_sha1=analysis["prompt_sha1"],
        attempts=int(info.get("attempts", 0)),
        questions=len(analysis.get("questions_found", [])),
        dropped=len(analysis.get("dropped_questions", [])),
    )
    return result


def validate(data, full_text: str, kd: KeywordDict) -> Accepted:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return Accepted(ok=False, errors=["답이 JSON 객체({ … })가 아닙니다"])
    out: dict = {}
    summary = data.get("role_summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("role_summary(한 줄 요약)가 없습니다")
    else:
        out["role_summary"] = summary.strip()

    for key, limit in LIST_LIMITS.items():
        value = data.get(key)
        if value is None:
            value = []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            errors.append(f"{key}는 목록([ … ])이어야 합니다")
            continue
        items = [str(x).strip() for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
        if key == "keywords":
            items = [kd.normalize(x) for x in items]
        items = list(dict.fromkeys(items))
        if len(items) > limit:
            warnings.append(f"{key} {len(items)}개 중 앞 {limit}개만 씁니다")
            items = items[:limit]
        out[key] = items
    if "must" in out and "duties" in out and not out["must"] and not out["duties"]:
        errors.append("must(필수 역량)와 duties(주요 업무)가 둘 다 비어 있습니다")

    kept, dropped = [], []
    questions = data.get("questions_found")
    if questions is None:
        questions = []
    if not isinstance(questions, list):
        errors.append("questions_found는 목록([ … ])이어야 합니다")
        questions = []
    for q in questions:
        if isinstance(q, str):
            q = {"text": q, "limit": None}
        if not isinstance(q, dict) or not str(q.get("text") or "").strip():
            continue
        text = str(q["text"]).strip()
        limit = _int_or_none(q.get("limit"))
        found, limit_found = verify_question(text, limit, full_text)
        if not found:
            dropped.append(text)
            continue
        if limit is not None and not limit_found:
            warnings.append(f"글자수 {limit}자는 공고 원문에 없어 뺐습니다: {text[:30]}")
            limit = None
        kept.append({"text": text, "limit": limit})
    if dropped:
        warnings.append(f"공고 원문에 없는 문항 {len(dropped)}개를 버렸습니다")
    out["questions_found"] = kept
    out["dropped_questions"] = dropped

    if errors:
        return Accepted(ok=False, errors=errors, warnings=warnings)
    return Accepted(ok=True, warnings=warnings, analysis=out)


# --- 문항 원문 대조 ---

_LEADING_MARK = re.compile(r"^\s*(?:(?:Q|문항)?\s*\d{1,2}\s*[.)．:]|\(\d{1,2}\)|[-•·ㆍ▪*■□●○※])\s*")
_QUOTES = re.compile(r"[\"'“”‘’「」『』]")
_SPACES = re.compile(r"\s+")


def verify_question(text: str, limit: int | None, full_text: str) -> tuple[bool, bool]:
    """(원문에 있는가, 글자수 숫자가 그 문항 근처에 있는가). 공백·따옴표·앞 번호는 무시하고 비교한다."""
    core = _LEADING_MARK.sub("", text)
    q, full = _norm(core), _norm(full_text)
    if len(q) < 6:
        return False, False
    idx = full.find(q)
    if idx < 0:
        return False, False
    if limit is None:
        return True, True
    window = full[max(0, idx - 30) : idx + len(q) + 60]
    return True, any(d in window for d in (str(limit), f"{limit:,}"))


def _norm(s: str) -> str:
    return _SPACES.sub("", _QUOTES.sub("", unicodedata.normalize("NFKC", s)))


# --- 내부 ---


def _write_retry(project: Project, job: Job, errors: list[str], attempt: int) -> Path:
    ai_dir = project.ai_dir(job.key)
    original = (ai_dir / FILE_PROMPT).read_text(encoding="utf-8") if (ai_dir / FILE_PROMPT).exists() else build_prompt(project, job)
    head, _, body = original.partition("\n")
    problems = "\n".join(f"- {e}" for e in errors)
    text = (
        f"[jobfit · 공고 분석 다시 요청 {attempt}] 앞 답에 아래 문제가 있었습니다. 고친 JSON 하나만, 앞뒤 설명 없이 답하세요."
        f"{head[head.find(' (에이전트') :] if ' (에이전트' in head else ''}\n\n"
        f"## 앞 답의 문제\n{problems}\n{body}"
    )
    path = ai_dir / FILE_RETRY
    path.write_text(text, encoding="utf-8")
    log_usage(project, job.key, STEP, "prompt", len(text))
    return path


def _write_result(project: Project, job: Job, analysis: dict) -> None:
    path = project.job_dir(job.key) / FILE_RESULT
    path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")


def _full_text(project: Project, job: Job) -> str:
    job_dir = project.job_dir(job.key)
    for name in (FILE_FULL, FILE_POSTING):
        p = job_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _int_or_none(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    m = re.search(r"\d[\d,]*", str(value))
    if not m:
        return None
    n = int(m.group().replace(",", ""))
    return n if n > 0 else None


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
