"""지원서 입력표(스크립트, AI 없음) — output/<공고>/apply/fill.md.

지원서 화면에 옮길 값을 한 장으로 모은다: 지원서 정보(me/profile.yaml), 자소서 문항별 초안(글자수·바이트), 보유 기술(경험 카드 태그).
신원 정보(이름·연락처·주소·생년월일)는 담지 않는다 — 사람이 지원서에서 직접 입력한다(사용자 결정).
나중에 에이전트가 크롬으로 지원서를 채울 때도 이 파일 하나만 읽게 하려고, 값은 칸 이름과 함께 표로 둔다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jobfit.clean.keywords import KeywordDict
from jobfit.clean.posting import FILE_FULL, FILE_POSTING
from jobfit.drafts import load_drafts, load_lint
from jobfit.experiences import load_cards, tag_list
from jobfit.match import ROLE_WORDS, load_match
from jobfit.paths import Project
from jobfit.profile import SECTIONS, example_note, label, load_profile, normalize_section, split_examples
from jobfit.state import Job

FILE_SHEET = "fill.md"
BLIND_MARK = "블라인드"
TOP_TAGS = 20
UNVERIFIED = "[확인 전]"


def write_sheet(project: Project, job: Job, today: date | None = None) -> tuple[Path, list[str]]:
    text, notes = build_sheet(project, job, today)
    path = project.apply_dir(job.key) / FILE_SHEET
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, notes


def is_blind(project: Project, job: Job) -> bool:
    """공고 본문에 '블라인드'가 있으면 블라인드 채용으로 본다(공기업 공고는 거의 적어 둔다)."""
    job_dir = project.job_dir(job.key)
    for name in (FILE_FULL, FILE_POSTING):
        path = job_dir / name
        if path.exists() and BLIND_MARK in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def build_sheet(project: Project, job: Job, today: date | None = None) -> tuple[str, list[str]]:
    today = today or date.today()
    notes: list[str] = []
    all_items, problems = load_profile(project.profile_file)
    items, examples = split_examples(all_items, project.me_example_dir / "profile.yaml")
    if problems:
        notes.append(problems[0])
    elif not all_items:
        notes.append("me/profile.yaml 이 없거나 비어 있습니다 — 지원서 정보 만들기(jobfit profile extract)로 sources/ 의 이력서에서 뽑으세요")
    if example_note(all_items, examples):
        notes.append(example_note(all_items, examples))
    unverified = [i for i in items if i.get("verified") is not True]
    if unverified:
        notes.append(f"확인 전 지원서 정보 {len(unverified)}개 — 입력표에 {UNVERIFIED}로 표시했습니다. 옮기기 전에 확인하고 me/profile.yaml 에서 verified: true 로")

    out: list[str] = [f"# 지원서 입력표 — {job.company}" + (f" / {job.title}" if job.title else "")]
    head = [f"공고키 `{job.key}`"]
    if job.deadline:
        head.append(f"마감 {job.deadline} (D-{(date.fromisoformat(job.deadline) - today).days})" if date.fromisoformat(job.deadline) >= today else f"마감 {job.deadline} (지남)")
    if job.url:
        head.append(f"[공고 원문]({job.url})")
    out.append("> " + " · ".join(head))
    out.append("> 이름·연락처·주소·생년월일 같은 **신원 정보는 이 표에 없습니다** — 지원서에서 직접 입력하세요. **제출은 직접** 합니다.")
    out.append(f"> {UNVERIFIED}는 AI가 자료에서 뽑고 아직 확인하지 않은 값입니다. 옮기기 전에 확인하세요.")
    if is_blind(project, job):
        out.append("> ⚠ **블라인드 채용 공고입니다** — 자소서·경력기술서에 학교명·출신지·가족·나이를 쓰지 마세요. 학력 칸이 없으면 학력은 넣지 않습니다.")

    # 지원서 정보 — 지원서 화면의 칸 순서대로
    for section, fields in SECTIONS.items():
        rows = [i for i in items if normalize_section(i.get("section")) == section]
        if not rows:
            continue
        out.append(f"\n## {section}")
        out.append("| " + " | ".join(label(section, f) for f in fields) + " |")
        out.append("|" + "---|" * len(fields))
        for item in rows:
            cells = [_cell(str(item.get(f, "") or "")) for f in fields]
            if item.get("verified") is not True:
                cells[0] += f" {UNVERIFIED}"
            out.append("| " + " | ".join(cells) + " |")
    if items:
        missing = [s for s in SECTIONS if not any(normalize_section(i.get("section")) == s for i in items)]
        if missing:
            out.append(f"\n_지원서 정보에 없는 칸: {', '.join(missing)} — 해당이 있으면 직접 넣으세요._")

    # 보유 기술
    cards, _ = load_cards(project.experiences_file)
    tags = [t for t in tag_list(cards, KeywordDict.load(project.data_dir)) if t not in ROLE_WORDS][:TOP_TAGS]
    if tags:
        out.append("\n## 보유 기술 (경험 카드 태그, 많이 쓴 순)")
        out.append(", ".join(tags))

    # 자기소개서
    out.append("\n## 자기소개서")
    questions = load_match(project, job).get("questions", [])
    drafts = load_drafts(project, job)
    lints = {q.get("qid"): q for q in load_lint(project, job).get("questions", [])}
    if not questions:
        out.append("문항이 없습니다. 지원서에 문항이 있으면 문항 넣기(메뉴)나 jobs.xlsx 문항 시트에 넣고 초안까지 진행한 뒤 이 표를 다시 만드세요.")
    missing_drafts = 0
    for q in questions:
        n = q["id"][1:]
        limit = f"{q['limit']}자({'공백 포함' if q.get('count_spaces', True) else '공백 제외'})" if q.get("limit") else "제한 없음"
        out.append(f"\n### {n}. {q['text']}")
        body = drafts.get(q["id"], "").strip()
        if not body:
            missing_drafts += 1
            out.append(f"> {limit} · (초안 없음)")
            continue
        lint = lints.get(q["id"], {})
        check = f"검사 오류 {lint.get('errors', 0)} · 경고 {lint.get('warnings', 0)} · 확인 필요 {lint.get('confirm_needed', 0)}" if lint else "검사 결과 없음"
        out.append(f"> {limit} · 지금 " + count_line(body) + f" · {check}")
        out.append("")
        out.append(body)
    if missing_drafts:
        notes.append(f"초안이 없는 문항 {missing_drafts}개 — 초안 단계를 마친 뒤 입력표를 다시 만드세요")
    if any(lint.get("errors") for lint in lints.values()):
        notes.append("검사 오류가 남은 초안이 있습니다 — report.md 의 ⛔ 를 먼저 고치세요")
    out.append("\n---\n_글자수는 사이트마다 세는 법이 다릅니다. 바이트로 세는 사이트는 한글을 2바이트(EUC-KR) 또는 3바이트(UTF-8)로 셉니다 — 사이트의 글자수 표시와 비교하세요._")
    return "\n".join(out).rstrip() + "\n", notes


def count_line(text: str) -> str:
    """공백 포함·제외 글자수와 두 가지 바이트 — 사이트가 무엇으로 세든 맞춰 볼 수 있게."""
    no_space = sum(1 for ch in text if not ch.isspace())
    utf8 = len(text.encode("utf-8"))
    euckr = len(text.encode("cp949", errors="replace"))
    return f"공백 포함 {len(text):,}자 · 공백 제외 {no_space:,}자 · {euckr:,}바이트(한글 2) / {utf8:,}바이트(한글 3)"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
