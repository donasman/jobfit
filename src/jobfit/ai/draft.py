"""자소서 초안(AI 1회, 전 문항 한 번에) → 검사 → 수정(AI 최대 1회).

phase(job.steps["draft"]["phase"]):
  draft  — draft.prompt.md 를 AI에 보내고 draft.response.md 를 받는다
  revise — 검사에 걸린 문항만 revise.prompt.md 로 다시 받는다(revise.response.md). 그 뒤에는 남은 문제를 경고로 두고 끝낸다
결과: output/<공고>/drafts/q1.md …, lint.json, report.md 갱신
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jobfit.ai.analyze import Prepared
from jobfit.ai.promptfile import LIMITS, load_template, log_usage, render, sha1
from jobfit.apply import is_blind
from jobfit.clean.keywords import KeywordDict
from jobfit.drafts import DRAFTS_DIR, FILE_LINT, load_drafts
from jobfit.experiences import file_sha, is_verified, load_cards
from jobfit.lint import QuestionLint, lint_answer
from jobfit.match import load_analysis, load_match
from jobfit.paths import Project
from jobfit.profile import load_profile, school_names
from jobfit.report import write_report
from jobfit.state import Job, now
from jobfit.style import forbidden_expressions, load_rules, load_stats, stats_lines

STEP = "draft"
FILE_PROMPT = "draft.prompt.md"
FILE_RETRY = "draft.retry.prompt.md"
FILE_RESPONSE = "draft.response.md"
FILE_REVISE = "revise.prompt.md"
FILE_REVISE_RESPONSE = "revise.response.md"
NO_LIMIT_TEXT = "제한 없음 — 600~900자"
_STAR_LABELS = (("situation", "상황"), ("task", "과제"), ("action", "행동"), ("result", "결과"))
_SECTION = re.compile(r"^[ \t]*={2,}[ \t]*(q\d+)[ \t]*={2,}[ \t]*$", re.M | re.I)
_FENCE_LINE = re.compile(r"^[ \t]*```[\w-]*[ \t]*$", re.M)


@dataclass
class DraftAccepted:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lints: list[QuestionLint] = field(default_factory=list)
    retry_path: Path | None = None
    revise_path: Path | None = None
    attempt: int = 0
    done: bool = False


def response_name(job: Job) -> str:
    return FILE_REVISE_RESPONSE if job.steps.get(STEP, {}).get("phase") == "revise" else FILE_RESPONSE


# --- prep ---


def prepare(project: Project, job: Job, force: bool = False) -> Prepared:
    ai_dir = project.ai_dir(job.key)
    info = job.steps.get(STEP, {})
    if info.get("phase") == "revise" and (ai_dir / FILE_REVISE).exists():
        text = (ai_dir / FILE_REVISE).read_text(encoding="utf-8")
        return Prepared(status="ready", prompt_path=ai_dir / FILE_REVISE, chars=len(text), limit=LIMITS["revise"])

    text = build_prompt(project, job)
    digest = sha1(text)
    force = force or STEP in job.force_steps
    cache = project.cache_dir / f"{STEP}-{digest}.json"
    if cache.exists() and not force:
        data = json.loads(cache.read_text(encoding="utf-8"))
        finalize(project, job, data.get("answers", {}), digest, revised=bool(data.get("revised")), cached=True)
        log_usage(project, job.key, STEP, "cached", len(text))
        return Prepared(status="cached", prompt_path=ai_dir / FILE_PROMPT, chars=len(text), limit=LIMITS[STEP])

    ai_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = ai_dir / FILE_PROMPT
    unchanged = prompt_path.exists() and prompt_path.read_text(encoding="utf-8") == text
    if not unchanged:
        prompt_path.write_text(text, encoding="utf-8")
        log_usage(project, job.key, STEP, "prompt", len(text))
    if not unchanged or STEP not in job.steps or job.steps[STEP].get("prompt_sha1") != digest:
        for stale in (FILE_RETRY, FILE_RESPONSE, FILE_REVISE, FILE_REVISE_RESPONSE):
            (ai_dir / stale).unlink(missing_ok=True)
        job.mark(STEP, done=False, phase="draft", prompt=FILE_PROMPT, prompt_sha1=digest, attempts=0)
    current = ai_dir / job.steps[STEP].get("prompt", FILE_PROMPT)
    if not current.exists():
        current = prompt_path
    return Prepared(status="ready", prompt_path=current, chars=len(current.read_text(encoding="utf-8")), limit=LIMITS[STEP])


# 상한을 넘으면 줄이는 단계: 0 그대로 · 1 STAR 120자, 보조 카드 STAR 빼기 · 2 STAR 60자. facts는 끝까지 남긴다(숫자의 근거)
STAR_CUT = {0: None, 1: 120, 2: 60}


def build_prompt(project: Project, job: Job, compact: int = 0) -> str:
    match = load_match(project, job)
    analysis = load_analysis(project, job)
    cards = {str(c.get("id")): c for c in load_cards(project.experiences_file)[0]}
    questions = match.get("questions", [])
    summary = [analysis.get("role_summary", "")]
    for label, key in (("필수", "must"), ("주요 업무", "duties"), ("인재상", "values")):
        if analysis.get(key):
            summary.append(f"- {label}: " + "; ".join(analysis[key]))
    rules = load_rules(project.me_dir).strip()
    style = rules or "(문체 규칙 없음 — 자연스러운 경어체 '~습니다'로)"
    lines = stats_lines(load_stats(project.me_dir))
    if lines:
        style += "\n\n스크립트가 잰 내 문체 수치:\n" + "\n".join(lines)
    values = {
        "response_path": project.rel(project.ai_dir(job.key) / FILE_RESPONSE),
        "job": job.key,
        "company": job.company,
        "title": job.title or "",
        "format": _format(q["id"] for q in questions),
        "summary": "\n".join(s for s in summary if s),
        "style": style,
        "questions": "\n\n".join(_question_block(q, cards, compact) for q in questions),
    }
    text = render(load_template(project, STEP), values)
    if len(text) > LIMITS[STEP] and int(compact) < max(STAR_CUT):
        return build_prompt(project, job, compact=int(compact) + 1)
    return text


def _question_block(q: dict, cards: dict[str, dict], compact: int | bool) -> str:
    level = int(compact)
    cut = STAR_CUT[level]
    out = [f"### {q['id']}. {q['text']}", f"- 글자수: {_limit_text(q)}", f"- 문항 유형: {q['type']}"]
    if not q.get("cards"):
        out.append("- 배정된 경험이 없습니다. 경험 내용이 필요하면 [확인 필요: 어떤 경험] 으로 비워 두세요.")
    for pick in q.get("cards", []):
        card = cards.get(pick["id"])
        if not card:
            continue
        label = "주력 경험" if pick["role"] == "주력" else "보조 경험"
        meta = ", ".join(str(card.get(k)) for k in ("period", "org", "role") if card.get(k))
        flag = " (사실 확인 전 카드 — 숫자는 [확인 필요]로)" if not is_verified(card) else ""
        out.append(f"- {label}: {card.get('title', pick['id'])}" + (f" ({meta})" if meta else "") + flag)
        star = card.get("star") if isinstance(card.get("star"), dict) else {}
        if pick["role"] == "주력" or not level:
            for key, name in _STAR_LABELS:
                value = str(star.get(key) or "").strip()
                if value:
                    out.append(f"  - {name}: {value[:cut] + '…' if cut and len(value) > cut else value}")
        facts = [str(f) for f in (card.get("facts") or [])]
        out.append("  - 쓸 수 있는 사실: " + ("; ".join(f'"{f}"' for f in facts) if facts else "(없음 — 숫자를 쓰지 마세요)"))
    return "\n".join(out)


# --- accept ---


def accept(project: Project, job: Job, raw: str | None = None) -> DraftAccepted:
    ai_dir = project.ai_dir(job.key)
    info = job.steps.setdefault(STEP, {"done": False, "phase": "draft"})
    phase = info.get("phase", "draft")
    response_path = ai_dir / (FILE_REVISE_RESPONSE if phase == "revise" else FILE_RESPONSE)
    if raw is not None:
        ai_dir.mkdir(parents=True, exist_ok=True)
        response_path.write_text(raw, encoding="utf-8")
    if not response_path.exists():
        return DraftAccepted(ok=False, errors=[f"답 파일이 없습니다: {project.rel(response_path)}"])
    text = response_path.read_text(encoding="utf-8", errors="replace")
    log_usage(project, job.key, "revise" if phase == "revise" else STEP, "response", len(text))

    questions = load_match(project, job).get("questions", [])
    expected = info.get("revise_qids", []) if phase == "revise" else [q["id"] for q in questions]
    answers, errors, warnings = parse_answers(text, expected)

    if phase == "revise":
        merged = load_drafts(project, job)
        for qid in expected:
            if answers.get(qid):
                merged[qid] = answers[qid]
            else:
                warnings.append(f"{qid} 수정본이 없어 처음 초안을 그대로 둡니다")
        result = finalize(project, job, merged, info.get("prompt_sha1", ""), revised=True)
        result.warnings = warnings + result.warnings
        return result

    if errors:
        info["attempts"] = int(info.get("attempts", 0)) + 1
        retry = _write_retry(project, job, errors, info["attempts"])
        info["prompt"] = FILE_RETRY
        info["at"] = now()
        return DraftAccepted(ok=False, errors=errors, warnings=warnings, retry_path=retry, attempt=info["attempts"])

    _write_drafts(project, job, answers)
    lints = lint_all(project, job, answers)
    failing = [ql.qid for ql in lints if ql.errors]
    if not failing:
        result = finalize(project, job, answers, info.get("prompt_sha1", ""), revised=False, lints=lints)
        result.warnings = warnings + result.warnings
        return result
    revise_path = _write_revise(project, job, answers, lints, failing)
    info.update(phase="revise", prompt=FILE_REVISE, revise_qids=failing, at=now())
    _write_lint(project, job, lints)
    return DraftAccepted(ok=True, warnings=warnings, lints=lints, revise_path=revise_path)


def parse_answers(text: str, expected: list[str]) -> tuple[dict[str, str], list[str], list[str]]:
    body = _FENCE_LINE.sub("", text)
    parts = _SECTION.split(body)
    answers: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        qid = parts[i].lower()
        if qid not in answers:
            answers[qid] = parts[i + 1].strip()
    errors: list[str] = []
    warnings: list[str] = []
    if not answers:
        errors.append("'=== q1 ===' 같은 구분선을 찾지 못했습니다. 답 형식 그대로 써 달라고 다시 요청하세요")
        return answers, errors, warnings
    for qid in expected:
        if not answers.get(qid):
            errors.append(f"{qid} 본문이 없습니다")
    extra = [q for q in answers if q not in expected]
    if extra:
        warnings.append(f"요청하지 않은 문항 {', '.join(extra)}은(는) 버렸습니다")
        for q in extra:
            answers.pop(q)
    return answers, errors, warnings


def lint_all(project: Project, job: Job, answers: dict[str, str]) -> list[QuestionLint]:
    questions = load_match(project, job).get("questions", [])
    cards = {str(c.get("id")): c for c in load_cards(project.experiences_file)[0]}
    rules = load_rules(project.me_dir)
    forbidden = forbidden_expressions(rules)
    stats = load_stats(project.me_dir)
    kd = KeywordDict.load(project.data_dir)
    versions = [v for alts in kd.variants.values() for v in alts if re.fullmatch(r"[A-Za-z][A-Za-z0-9.+# -]*", v)]
    # 블라인드 채용 공고면 학교명·나이·가족 직업을 본다 — 내 학교 이름은 지원서 정보(profile.yaml)에서
    blind = school_names(load_profile(project.profile_file)[0]) if is_blind(project, job) else None
    out = []
    for q in questions:
        if q["id"] not in answers:
            continue
        picked = [cards[c["id"]] for c in q.get("cards", []) if c["id"] in cards]
        facts = [str(f) for card in picked for f in (card.get("facts") or [])]
        unverified = [str(f) for card in picked if not is_verified(card) for f in (card.get("facts") or [])]
        periods = [str(card.get("period")) for card in picked if card.get("period")]
        out.append(lint_answer(q["id"], answers[q["id"]], q.get("limit"), q.get("count_spaces", True), facts, q["text"], periods, forbidden, stats, versions, unverified, blind))
    return out


def finalize(project: Project, job: Job, answers: dict[str, str], digest: str, revised: bool, cached: bool = False, lints: list[QuestionLint] | None = None) -> DraftAccepted:
    """초안을 확정한다 — 파일·검사 결과·캐시·report. 남은 오류는 경고로 report에 남는다."""
    _write_drafts(project, job, answers)
    lints = lints if lints is not None else lint_all(project, job, answers)
    _write_lint(project, job, lints)
    if digest and not cached:
        project.cache_dir.mkdir(parents=True, exist_ok=True)
        (project.cache_dir / f"{STEP}-{digest}.json").write_text(json.dumps({"answers": answers, "revised": revised}, ensure_ascii=False, indent=2), encoding="utf-8")
    job.mark(
        STEP,
        done=True,
        prompt_sha1=digest,
        revised=revised,
        cached=cached,
        errors=sum(len(ql.errors) for ql in lints),
        warnings=sum(len(ql.warnings) for ql in lints),
        confirm=sum(ql.confirm_needed for ql in lints),
    )
    if STEP in job.force_steps:
        job.force_steps.remove(STEP)
    write_report(project, job)
    job.mark("report", done=True, resume_sha=file_sha(project.resume_file))
    return DraftAccepted(ok=True, lints=lints, done=True)


# --- 내부 ---


def _limit_text(q: dict) -> str:
    if not q.get("limit"):
        return NO_LIMIT_TEXT
    return f"{q['limit']}자 ({'공백 포함' if q.get('count_spaces', True) else '공백 제외'})"


def _format(qids) -> str:
    return "\n".join(f"=== {q} ===\n({q} 본문)" for q in qids)


def _write_drafts(project: Project, job: Job, answers: dict[str, str]) -> None:
    d = project.job_dir(job.key) / DRAFTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("q*.md"):
        if old.stem not in answers:
            old.unlink()
    for qid, text in answers.items():
        (d / f"{qid}.md").write_text(text.strip() + "\n", encoding="utf-8")


def _write_lint(project: Project, job: Job, lints: list[QuestionLint]) -> None:
    data = {"made_at": now(), "questions": [ql.to_dict() for ql in lints]}
    (project.job_dir(job.key) / FILE_LINT).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_retry(project: Project, job: Job, errors: list[str], attempt: int) -> Path:
    ai_dir = project.ai_dir(job.key)
    original = (ai_dir / FILE_PROMPT).read_text(encoding="utf-8")
    head, _, body = original.partition("\n")
    agent = head[head.find(" (에이전트"):] if " (에이전트" in head else ""
    problems = "\n".join(f"- {e}" for e in errors)
    text = f"[jobfit · 자소서 초안 다시 요청 {attempt}] 앞 답에 아래 문제가 있었습니다. 답 형식 그대로, 앞뒤 설명 없이 다시 써 주세요.{agent}\n\n## 앞 답의 문제\n{problems}\n{body}"
    path = ai_dir / FILE_RETRY
    path.write_text(text, encoding="utf-8")
    log_usage(project, job.key, STEP, "prompt", len(text))
    return path


def _write_revise(project: Project, job: Job, answers: dict[str, str], lints: list[QuestionLint], failing: list[str]) -> Path:
    questions = {q["id"]: q for q in load_match(project, job).get("questions", [])}
    cards = {str(c.get("id")): c for c in load_cards(project.experiences_file)[0]}
    by_id = {ql.qid: ql for ql in lints}
    items = []
    for qid in failing:
        q = questions[qid]
        facts = [str(f) for c in q.get("cards", []) if c["id"] in cards for f in (cards[c["id"]].get("facts") or [])]
        fixes = "\n".join(f"  - {i.message}" for i in by_id[qid].errors)
        items.append(
            f"## {qid}. {q['text']}\n- 글자수: {_limit_text(q)}\n- 고칠 것:\n{fixes}\n"
            f"- 쓸 수 있는 사실: " + ("; ".join(f'"{f}"' for f in facts) if facts else "(없음 — 숫자를 쓰지 마세요)")
            + f"\n### 지금 초안\n{answers[qid].strip()}"
        )
    values = {
        "response_path": project.rel(project.ai_dir(job.key) / FILE_REVISE_RESPONSE),
        "job": job.key,
        "format": _format(failing),
        "items": "\n\n".join(items),
    }
    text = render(load_template(project, "revise"), values)
    path = project.ai_dir(job.key) / FILE_REVISE
    path.write_text(text, encoding="utf-8")
    log_usage(project, job.key, "revise", "prompt", len(text))
    return path
