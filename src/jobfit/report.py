"""report.md(스크립트, AI 없음) — 공고 하나의 최종 정리. Notion에 그대로 가져오기 좋은 마크다운.

순서: 공고 요약 → 매칭(점수·카드 순위·문항별 사용 경험) → 이력서 강조 포인트 → 자기소개서 문항과 초안(검사 결과) → 확인 필요.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from jobfit.clean.keywords import KeywordDict
from jobfit.clean.posting import FILE_CLEAN_META, FILE_FULL, FILE_POSTING, mask_contacts
from jobfit.drafts import load_drafts, load_lint
from jobfit.emphasis import build_emphasis, example_note, load_resume, split_examples
from jobfit.experiences import check_cards, example_ids, load_cards
from jobfit.fetch.channel import ApplyChannel, page_htmls, read_channel, resolve
from jobfit.match import cards_in_use, load_analysis, load_match
from jobfit.paths import Project
from jobfit.state import Job
from jobfit.style import draft_blocker, is_draft_rules

FILE_REPORT = "report.md"


def write_report(project: Project, job: Job, today: date | None = None) -> Path:
    path = project.job_dir(job.key) / FILE_REPORT
    path.write_text(build_report(project, job, today), encoding="utf-8")
    return path


def build_report(project: Project, job: Job, today: date | None = None) -> str:
    today = today or date.today()
    kd = KeywordDict.load(project.data_dir)
    analysis = load_analysis(project, job)
    match = load_match(project, job)
    cards, card_problems = load_cards(project.experiences_file)
    all_resume, resume_problems = load_resume(project.resume_file)
    resume_items, resume_examples = split_examples(all_resume, project.me_example_dir / "resume.yaml")
    meta = _load_json(project.job_dir(job.key) / FILE_CLEAN_META)
    posting_text = _read(project.job_dir(job.key) / FILE_FULL) or _read(project.job_dir(job.key) / FILE_POSTING)
    emphasis = build_emphasis(match, cards, resume_items, kd, posting_text)
    titles = {c["id"]: c["title"] or c["id"] for c in match.get("cards", [])}

    out: list[str] = []
    out.append(f"# {job.company}" + (f" — {job.title}" if job.title else ""))
    head = [f"공고키 `{job.key}`"]
    if job.deadline:
        dday = (date.fromisoformat(job.deadline) - today).days
        head.append(f"마감 {job.deadline} ({_dday(dday)}, {today.isoformat()} 기준)")
    if job.url:
        head.append(f"[공고 원문]({job.url})")
    out.append("> " + " · ".join(head))
    if meta.get("summary"):
        out.append("> " + " · ".join(f"{k} {v}" for k, v in meta["summary"].items()))
    channel = apply_channel(project, job)
    if channel.via or job.step_done("clean"):
        out.append("> " + _channel_line(channel, job.url))

    # 공고 요약
    out.append("\n## 공고 요약")
    out.append(analysis.get("role_summary", "(분석 전)"))
    for label, key in (("필수", "must"), ("우대", "nice"), ("주요 업무", "duties"), ("인재상", "values")):
        items = analysis.get(key) or []
        if items:
            out.append(f"\n**{label}**")
            out.extend(f"- {x}" for x in items)
    if analysis.get("keywords"):
        out.append(f"\n**키워드** {', '.join(analysis['keywords'])}")

    # 매칭
    out.append("\n## 매칭")
    if not match:
        out.append("(매칭 전)")
    elif not match.get("cards"):
        out.append("경험 카드가 없어 매칭하지 않았습니다. `me/experiences.yaml`에 카드를 넣고 `jobfit run`.")
    else:
        b = match["breakdown"]
        score = match.get("score")
        out.append(f"**점수 {score}/100**" if score is not None else "**점수 —** (태그로 판단할 수 있는 항목이 없습니다)")
        out.append(
            f"필수 {b['must']['covered']}/{b['must']['judged']} · 우대 {b['nice']['covered']}/{b['nice']['judged']}"
            f" · 키워드 {b['keywords']['covered']}/{b['keywords']['judged']}"
        )
        # 기술 낱말이 없는 조건(경력 연수·자격증 등)은 점수에 못 넣는다 — 몇 개가 아니라 무엇인지 보여 줘야 사람이 판단한다
        unjudged = [r for r in match.get("requirements", []) if not r.get("terms")]
        if unjudged:
            out.append("\n**점수에 넣지 않은 조건** — 기술 낱말이 없어 경험 카드로 판단할 수 없습니다. 직접 판단하세요")
            for kind, label in (("must", "필수"), ("nice", "우대")):
                items = [r["text"] for r in unjudged if r["kind"] == kind]
                if items:
                    out.append(f"- {label}: " + " · ".join(items))
        out.append("\n| 경험 카드 | 점수 | 맞은 필수 | 맞은 우대 | 맞은 키워드 |")
        out.append("|---|---|---|---|---|")
        for c in match["cards"]:
            mark = "" if c["verified"] else " ⚠"
            out.append(
                f"| {_cell(titles[c['id']])}{mark} | {c['score']} | {_cell(', '.join(c['must_hits']) or '—')}"
                f" | {_cell(', '.join(c['nice_hits']) or '—')} | {_cell(', '.join(c['keywords_hits']) or '—')} |"
            )
        if match.get("questions"):
            out.append("\n**문항별 사용 경험**" + (" (직접 고친 배정)" if match.get("edited") else ""))
            out.append("\n| 문항 | 유형 | 주력 | 보조 |")
            out.append("|---|---|---|---|")
            for q in match["questions"]:
                primary = next((c for c in q["cards"] if c["role"] == "주력"), None)
                second = next((c for c in q["cards"] if c["role"] == "보조"), None)
                p = (titles.get(primary["id"], primary["id"]) + ("" if primary["type_match"] else " (유형 불일치)")) if primary else "—"
                s = titles.get(second["id"], second["id"]) if second else "—"
                out.append(f"| {q['id']}. {_cell(_short(q['text']))} | {q['type']} | {_cell(p)} | {_cell(s)} |")

    # 이력서 강조 포인트
    out.append("\n## 이력서 강조 포인트")
    if emphasis["top"]:
        label = "위로 올릴 항목 (resume.yaml)" if emphasis["source"] == "resume" else "앞세울 경험 (내 이력서 항목이 없어 경험 카드 순위로)"
        out.append(f"\n**{label}**")
        for i, t in enumerate(emphasis["top"], start=1):
            why = "; ".join(x for x in (
                ("필수 " + ", ".join(t["must"])) if t["must"] else "",
                ("우대 " + ", ".join(t["nice"])) if t["nice"] else "",
                ("키워드 " + ", ".join(t["keywords"])) if t["keywords"] else "",
            ) if x)
            section = f"[{t['section']}] " if t["section"] and emphasis["source"] == "resume" else ""
            out.append(f"{i}. {section}{t['title']} — {why}")
    else:
        out.append("이 공고와 맞는 항목을 찾지 못했습니다." + ("" if resume_items or cards else " `me/resume.yaml`이나 `me/experiences.yaml`을 채우면 나옵니다."))
    if emphasis["wording"]:
        out.append("\n**공고 표기로 맞출 것**")
        out.extend(f"- {w['item']}: '{w['from']}' → '{w['to']}'" for w in emphasis["wording"])

    # 자기소개서 문항과 초안
    out.append("\n## 자기소개서 문항")
    questions = match.get("questions") or []
    drafts = load_drafts(project, job) if job.step_done("draft") else {}
    lints = {q["qid"]: q for q in load_lint(project, job).get("questions", [])} if drafts else {}
    if not questions:
        out.append("공고에 자소서 문항이 없습니다. 문항이 있으면 `jobs.xlsx` 문항 시트에 입력하고 `jobfit run` — 초안 단계로 이어집니다.")
    elif not drafts:
        for q in questions:
            limit = f" ({q['limit']}자, 공백 {'포함' if q.get('count_spaces', True) else '제외'})" if q.get("limit") else ""
            out.append(f"{q['id'][1:]}. {q['text']}{limit} — {q['source']}")
        blocker = draft_blocker(project)
        if blocker:
            out.append(f"\n초안은 쓰지 않습니다. {blocker}. 초안은 원안 자소서의 문체·흐름에 내 경험 카드를 맞춰 쓰는 것이라, 원안 없이 처음부터 쓰지 않습니다.")
        else:
            out.append("\n(초안 전 — `jobfit run`이 초안 단계로 이어집니다)")
    else:
        if job.steps.get("draft", {}).get("revised"):
            out.append("검사에 걸린 문항을 한 번 고쳐 받았습니다. 남은 문제는 각 문항 아래에 있습니다.")
        for q in questions:
            lint = lints.get(q["id"], {})
            basis = "공백 포함" if q.get("count_spaces", True) else "공백 제외"
            count = f"{lint.get('chars', 0)}" + (f"/{q['limit']}자" if q.get("limit") else "자")
            meta = [f"글자수 {count} ({basis})", q["source"]]
            if lint.get("errors"):
                meta.append(f"오류 {lint['errors']}")
            if lint.get("warnings"):
                meta.append(f"경고 {lint['warnings']}")
            if lint.get("confirm_needed"):
                meta.append(f"확인 필요 {lint['confirm_needed']}")
            out.append(f"\n### {q['id'][1:]}. {q['text']}")
            out.append("> " + " · ".join(meta))
            out.append("")
            out.append(drafts.get(q["id"], "(초안 없음)").strip())
            for issue in lint.get("issues", []):
                out.append(f"- {'⛔' if issue['level'] == '오류' else '⚠'} {issue['message']}")

    # 확인 필요
    notes = []
    used = set(cards_in_use(match))
    for c in match.get("cards", []):
        if c["id"] in used and not c["verified"]:
            notes.append(f"경험 카드 '{titles[c['id']]}'은(는) verified가 true가 아닙니다 — 사실을 확인한 뒤 true로 바꾸세요")
    if analysis.get("dropped_questions"):
        notes.append(f"AI가 낸 문항 중 공고 원문에 없는 {len(analysis['dropped_questions'])}개를 버렸습니다: " + " / ".join(_short(x) for x in analysis["dropped_questions"]))
    examples = example_ids(cards, project.me_example_dir / "experiences.yaml")
    if examples:
        notes.append(f"me.example 에서 복사된 예시 카드 {len(examples)}개가 경험 은행에 남아 있습니다({', '.join(examples)}) — 가짜 경험이 매칭·초안에 쓰이니 me/experiences.yaml 에서 지우세요")
    errors = [i for i in check_cards(cards, kd, examples) if i.level == "오류" and i.kind != "example"]
    if errors or card_problems:
        notes.append(f"경험 카드 검사에서 오류 {len(errors) + len(card_problems)}건 — `jobfit exp check`")
    if lints:
        left = sum(q.get("errors", 0) for q in lints.values())
        confirm = sum(q.get("confirm_needed", 0) for q in lints.values())
        if left:
            notes.append(f"초안에 고치지 못한 오류 {left}건 — 문항 아래 ⛔ 표시를 직접 고치세요")
        if confirm:
            notes.append(f"초안의 [확인 필요] {confirm}곳 — 사실을 채우거나 문장을 빼세요")
        notes.append("초안은 그대로 내지 말고 내 말투로 다시 다듬으세요")
        if is_draft_rules(project.me_dir):
            notes.append("초안이 따른 문체 규칙(me/style/style_rules.md)은 AI가 원안에서 정리한 초안 그대로입니다 — 읽고 고친 뒤 맨 위 안내 줄을 지우면 확정")
    notes.extend(resume_problems)
    if example_note(all_resume, resume_examples):
        notes.append(example_note(all_resume, resume_examples))
    if notes:
        out.append("\n## 확인 필요")
        out.extend(f"- {n}" for n in notes)

    return "\n".join(out).rstrip() + "\n"


def apply_channel(project: Project, job: Job) -> ApplyChannel:
    """지원처 — jobs.xlsx 지원방법 칸 > 정제 때 페이지에서 읽은 값(meta.json). 그 값이 없는 예전 공고는 지금 읽는다."""
    job_dir = project.job_dir(job.key)
    found = _load_json(job_dir / FILE_CLEAN_META).get("apply")
    if found is None and job.step_done("clean"):
        found = asdict(read_channel(job.url, page_htmls(job_dir), _read(job_dir / FILE_FULL)))
    return resolve(job.apply_via, found or {})


def _channel_line(channel: ApplyChannel, posting_url: str) -> str:
    # 근거는 공고 글에서 온 줄이라 담당자 연락처가 섞일 수 있다
    evidence = mask_contacts(channel.evidence)
    if not channel.via:
        why = f" ({evidence})" if evidence else ""
        return f"지원 방법: 페이지에서 읽지 못했습니다{why} — 알면 jobs.xlsx 지원방법 칸에 적으세요"
    line = f"지원 방법 **{channel.via}**"
    if channel.url and channel.url != posting_url:
        line += f" — [지원 페이지]({channel.url})"
    return line + (f" · 근거: {evidence}" if evidence else "")


def _dday(days: int) -> str:
    if days > 0:
        return f"D-{days}"
    if days == 0:
        return "D-day"
    return f"마감 {-days}일 지남"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _short(text: str, n: int = 40) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
