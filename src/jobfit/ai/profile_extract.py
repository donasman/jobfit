"""지원서 정보 뽑기(AI) — sources/ 의 내 자료 → me/profile.yaml 에 학력·자격증·어학 같은 항목 추가.

경험 카드 뽑기(exp_extract)와 같은 꼴이다: 자료마다 글자를 꺼내(textio) 연락처를 가리고, 조각으로 나눠 프롬프트를 만들고,
답(YAML 목록)을 검사해 덧붙인다. 다른 점:
- 지원서 정보가 있을 법한 조각(학교·자격·어학·병역·수상 같은 낱말이 있는 조각)만 프롬프트를 만든다 — 포트폴리오 전체를 돌지 않게.
- 숫자(날짜·점수·학점·시간·번호)가 자료 원문에 없으면 그 칸을 비운다. 이름(학교·자격증 등)이 원문에 없으면 경고.
- 신원 정보 칸은 형식에 없고, 들어와도 버린다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jobfit.ai import exp_extract
from jobfit.ai.promptfile import LIMITS, load_template, log_usage, render
from jobfit.clean.posting import mask_contacts
from jobfit.paths import Project
from jobfit.profile import SECTIONS, append_items, item_name, key_field, label, load_profile, norm_name, normalize_section, same_item
from jobfit.state import now
from jobfit.textio import Extracted, read_any

STEP = "profile-extract"
EMPTIED_MARK = "자료 원문에 없는 숫자라 비웠습니다"
# 지원서 정보가 있을 법한 조각의 표시. 없으면 그 조각은 프롬프트를 만들지 않는다.
# 개발 글에 흔한 뜻이 다른 낱말은 뺐다(실측: 포트폴리오의 '전역 변수'·'개발 과정'·'적용 대상'·'자격 증명'에 걸렸다)
_SIGNALS = re.compile(
    r"학력|대학교|대학원|고등학교|학과|전공|학점|졸업|자격증|자격\s?취득|면허|어학|TOEIC|TOEFL|TEPS|OPIc|토익|토플|오픽|JLPT|HSK|IELTS|"
    r"병역|군필|미필|만기\s?전역|병장|수상|최우수상|우수상|장려상|대상\s?수상|수료|이수|교육과정|부트캠프|"
    r"경력|재직|입사|퇴사|인턴|동아리|봉사",
    re.I,
)
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Accepted:
    ok: bool
    added: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    item: exp_extract.WorkItem | None = None
    remaining: int = 0


# --- 상태 ---


def _state_path(project: Project) -> Path:
    return project.state_dir / "profile_sources.json"


def _load_state(project: Project) -> dict:
    p = _state_path(project)
    if not p.exists():
        return {"files": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": {}}


def _save_state(project: Project, state: dict) -> None:
    project.state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(project).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_cache(project: Project, slug: str) -> Path:
    return project.state_dir / "profile_sources" / f"{slug}.txt"


# --- scan · prep ---


@dataclass
class ScanReport:
    pending: list[exp_extract.WorkItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    nothing: list[str] = field(default_factory=list)  # 지원서 정보가 없어 보여 건너뛴 자료
    done: list[str] = field(default_factory=list)


def scan(project: Project, force: bool = False) -> ScanReport:
    state = _load_state(project)
    files = state.setdefault("files", {})
    report = ScanReport()
    present = set()
    for path in exp_extract.list_sources(project):
        rel = project.rel(path)
        present.add(rel)
        digest = exp_extract._digest(path)
        info = files.get(rel)
        if info and info.get("sha1") == digest and not force:
            if info.get("status") == "skipped":
                report.skipped.append((rel, info.get("note", "")))
            elif info.get("status") == "nothing":
                report.nothing.append(rel)
            elif info.get("status") == "done":
                report.done.append(rel)
            continue
        slug = exp_extract._slug(path, rel)
        _clear_work(project, slug)
        if path.stat().st_size > exp_extract.MAX_SOURCE_BYTES:
            got = Extracted(None, "unsupported", "파일이 너무 큽니다")
        else:
            got = read_any(path)
        if got.text is None and got.kind in ("unsupported", "empty"):
            files[rel] = {"sha1": digest, "slug": slug, "status": "skipped", "note": got.note, "at": now()}
            report.skipped.append((rel, got.note))
            continue
        text = mask_contacts(got.text) if got.text else ""
        parts = exp_extract._split(text) if text else [""]
        relevant = [n for n, chunk in enumerate(parts, start=1) if got.text is None or _SIGNALS.search(chunk)]
        cache = _text_cache(project, slug)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        status = "pending" if relevant else "nothing"
        files[rel] = {
            "sha1": digest, "slug": slug, "direct": got.text is None, "note": got.note, "parts": len(parts),
            "relevant": relevant, "done_parts": [], "items": 0, "status": status, "at": now(),
        }
        if not relevant:
            report.nothing.append(rel)
    for rel in list(files):
        if rel not in present:
            _clear_work(project, files.pop(rel).get("slug", ""))
    _save_state(project, state)
    report.pending = prepare(project)
    return report


def prepare(project: Project) -> list[exp_extract.WorkItem]:
    state = _load_state(project)
    items: list[exp_extract.WorkItem] = []
    existing, _ = load_profile(project.profile_file)
    template = load_template(project, STEP)
    project.profile_dir.mkdir(parents=True, exist_ok=True)
    for rel, info in state.get("files", {}).items():
        if info.get("status") != "pending":
            continue
        text = "" if info.get("direct") else _text_cache(project, info["slug"]).read_text(encoding="utf-8")
        parts = exp_extract._split(text) if text else [""]
        for n in info.get("relevant", []):
            if n in info.get("done_parts", []):
                continue
            item = _item(project, rel, info, n)
            if info.get("direct"):
                body = (
                    f"이 파일은 스크립트가 글자를 읽지 못했습니다({info.get('note', '')}).\n"
                    f"- 에이전트: 원본 파일 `{rel}` 을 직접 열어 읽으세요(이 파일만).\n"
                    f"- 웹 채팅: 이 프롬프트와 함께 그 파일을 첨부하세요."
                )
            else:
                body = parts[n - 1]
            values = {
                "response_path": project.rel(item.response),
                "existing": "",
                "source_name": Path(rel).name,
                "part": f" (조각 {n}/{len(parts)})" if len(parts) > 1 else "",
                "source": body,
            }
            room = limit() - len(render(template, values)) - 20
            values["existing"] = _fit_existing(existing, room)
            prompt = render(template, values)
            if not item.prompt.exists() or item.prompt.read_text(encoding="utf-8") != prompt:
                item.prompt.write_text(prompt, encoding="utf-8")
                log_usage(project, rel, STEP, "prompt", len(prompt))
            items.append(item)
    return items


def pending_items(project: Project) -> list[exp_extract.WorkItem]:
    out = []
    for rel, info in _load_state(project).get("files", {}).items():
        if info.get("status") != "pending":
            continue
        for n in info.get("relevant", []):
            if n not in info.get("done_parts", []):
                item = _item(project, rel, info, n)
                if item.prompt.exists():
                    out.append(item)
    return out


def pick(project: Project, raw_given: bool, prefer: str | None = None) -> exp_extract.WorkItem | None:
    items = pending_items(project)
    if not items:
        return None
    if raw_given:
        return next((i for i in items if project.rel(i.prompt) == prefer), items[0])
    return next((i for i in items if i.response.exists()), items[0])


# --- accept ---


def accept(project: Project, raw: str | None = None, prefer: str | None = None) -> Accepted:
    item = pick(project, raw is not None, prefer)
    if item is None:
        return Accepted(ok=False, errors=["지원서 정보 답을 기다리는 자료가 없습니다. 먼저  jobfit profile extract"])
    if raw is not None:
        item.response.parent.mkdir(parents=True, exist_ok=True)
        item.response.write_text(raw, encoding="utf-8")
    if not item.response.exists():
        return Accepted(ok=False, errors=[f"답 파일이 없습니다: {project.rel(item.response)}"], item=item)
    text = item.response.read_text(encoding="utf-8", errors="replace")
    log_usage(project, item.source, STEP, "response", len(text))
    try:
        data = exp_extract.parse_yaml_list(text, "- section: 학력")
    except ValueError as e:
        return Accepted(ok=False, errors=[str(e)], item=item)
    state = _load_state(project)
    info = state["files"][item.source]
    source_text = None if info.get("direct") else _text_cache(project, info["slug"]).read_text(encoding="utf-8")
    existing, problems = load_profile(project.profile_file)
    if problems:
        return Accepted(ok=False, errors=[f"{problems[0]} — profile.yaml 을 고친 뒤 다시"], item=item)
    items, warnings = normalize_items(data, existing, source_text, item.source)
    if source_text is None and items:
        warnings.append("스크립트가 읽지 못한 파일이라 숫자를 원문과 대조하지 못했습니다 — 직접 확인하세요")
    if items:
        try:
            append_items(project.profile_file, project.backups_dir, items, item.source)
        except ValueError as e:
            return Accepted(ok=False, errors=[str(e)], item=item)
    info.setdefault("done_parts", []).append(item.part)
    info["items"] = int(info.get("items", 0)) + len(items)
    if all(n in info["done_parts"] for n in info.get("relevant", [])):
        info["status"] = "done"
    info["at"] = now()
    _save_state(project, state)
    return Accepted(ok=True, added=items, warnings=warnings, item=item, remaining=len(pending_items(project)))


def normalize_items(data: list, existing: list[dict], source_text: str | None, source: str) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    src_digits = _digit_set(source_text) if source_text is not None else None
    src_norm = norm_name(source_text) if source_text is not None else None
    out: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        section = normalize_section(raw.get("section"))
        values = {k: str(v).strip() for k, v in raw.items() if k != "section" and v is not None and str(v).strip()}
        if section is None:
            if values:
                warnings.append(f"지원서 칸이 아닌 항목을 버렸습니다(section: {raw.get('section')})")
            continue
        item = {"section": section, **{f: values.get(f, "") for f in SECTIONS[section]}}
        name = item[key_field(section)]
        if not name:
            if any(item[f] for f in SECTIONS[section]):
                warnings.append(f"{section}: {label(section, key_field(section))}이(가) 없는 항목을 버렸습니다")
            continue  # 답 형식의 빈 틀을 그대로 돌려준 것
        if any(same_item(item, e) for e in [*existing, *out]):
            warnings.append(f"{section} '{name}': 이미 있는 항목이라 건너뜁니다")
            continue
        if src_digits is not None:
            for f in SECTIONS[section]:
                if item[f] and any(not _digit_ok(d, src_digits) for d in _DIGITS.findall(item[f])):
                    warnings.append(f"{section} '{name}' {label(section, f)} '{item[f]}': {EMPTIED_MARK}")
                    item[f] = ""
        if src_norm is not None and norm_name(name) not in src_norm:
            warnings.append(f"{section} '{name}': 자료에서 이 이름을 찾지 못했습니다 — 맞는지 확인하세요")
        item["verified"] = False
        item["source"] = source
        out.append(item)
    return out, warnings


def source_files(project: Project) -> dict[str, dict]:
    return dict(_load_state(project).get("files", {}))


def limit() -> int:
    return LIMITS[STEP]


# --- 내부 ---


def _fit_existing(items: list[dict], budget: int) -> str:
    lines = [f"- {i.get('section')}: {item_name(i)}" for i in items if item_name(i)]
    if not lines:
        return "(없음)"
    out: list[str] = []
    used = 40
    for line in lines:
        if used + len(line) + 1 > budget:
            out.append(f"- … 외 {len(lines) - len(out)}개 (같은 항목은 스크립트가 건너뜁니다)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _digit_set(text: str) -> set[str]:
    """자료의 숫자 — 소수(3.8)와, 점으로 이어진 날짜(2025.06.13)의 낱낱(2025·6·13)을 모두."""
    return {_norm_digits(d) for d in [*_DIGITS.findall(text), *re.findall(r"\d+", text)]}


def _norm_digits(d: str) -> str:
    """'03'과 '3', '4.50'과 '4.5'를 같게."""
    if "." in d:
        whole, frac = d.split(".", 1)
        frac = frac.rstrip("0")
        return str(int(whole)) + ("." + frac if frac else "")
    return str(int(d))


def _digit_ok(d: str, src: set[str]) -> bool:
    n = _norm_digits(d)
    if n in src:
        return True
    # 2025-06-13 을 자료가 25.06.13 으로 쓴 경우 — 네 자리 연도의 뒤 두 자리
    return len(d) == 4 and str(int(d[2:])) in src


def _clear_work(project: Project, slug: str) -> None:
    if not slug or not project.profile_dir.is_dir():
        return
    for old in project.profile_dir.glob(f"profile-{slug}-*.md"):
        old.unlink(missing_ok=True)


def _item(project: Project, rel: str, info: dict, n: int) -> exp_extract.WorkItem:
    name = f"profile-{info['slug']}-{n}"
    return exp_extract.WorkItem(
        source=rel, part=n, parts=int(info.get("parts", 1)),
        prompt=project.profile_dir / f"{name}.prompt.md", response=project.profile_dir / f"{name}.response.md",
        direct=bool(info.get("direct")),
    )
