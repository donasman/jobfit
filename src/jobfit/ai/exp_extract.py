"""경험 카드 뽑기(AI) — sources/ 의 내 자료 → me/experiences.yaml 에 카드 추가.

1. scan: sources/ 파일마다 글자를 꺼낸다(textio). 못 읽는 파일(이미지·스캔 PDF)은 "AI가 원본을 직접 읽을 것"으로 둔다.
   해시로 처리한 파일을 기억해, 바뀌지 않은 파일은 다시 하지 않는다. 긴 자료는 여러 조각(part)으로 나눈다.
2. prep: 조각마다 output/_exp/_ai/extract-<이름>-<n>.prompt.md — 카드 형식·규칙·이미 있는 카드·태그 표기(자료에 나온 것)·자료 글자.
   자료가 바뀌면 그 자료의 옛 프롬프트·답 파일을 지운다(옛 답을 새 자료에 받아들이지 않게).
3. accept: 답(YAML 목록)을 검사한다 — 형식 정리, 태그·문항 유형 정규화, 이미 있는 카드와 같은 경험은 건너뜀,
   **facts의 숫자가 자료 원문에 없으면 그 fact를 뺌**. 새 카드는 모두 verified: false, source: <파일>.
   experiences.yaml 맨 아래에 덧붙인다(쓰기 전 .jobfit/backups/ 에 백업, 덧붙인 뒤에도 yaml로 읽히는지 확인).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from jobfit.ai.promptfile import LIMITS, load_template, log_usage, render
from jobfit.clean.keywords import KeywordDict
from jobfit.clean.posting import mask_contacts
from jobfit.experiences import FILE_HEADER, card_tags, load_cards, normalize_type
from jobfit.match import ROLE_WORDS
from jobfit.numbers import extract, same_value
from jobfit.paths import Project
from jobfit.state import now
from jobfit.textio import Extracted, read_any

STEP = "exp-extract"
SOURCE_PART = 5000  # 프롬프트 한 장에 넣는 자료 글자수. 넘으면 조각으로 나눈다
MAX_SOURCE_BYTES = 30_000_000  # 이보다 큰 파일(영상 등)은 읽지 않는다
BACKUP_KEEP = 10
_FENCE_LINE = re.compile(r"^[ \t]*```[\w-]*[ \t]*$", re.M)
_FENCED = re.compile(r"^[ \t]*```[\w-]*[ \t]*\n(.*?)^[ \t]*```", re.S | re.M)
_ID_SAFE = re.compile(r"[^a-z0-9-]+")
_NAME_SAFE = re.compile(r'[\\/:*?"<>|#^\[\]\s]+')


@dataclass
class WorkItem:
    source: str  # sources/ 기준 상대 경로 (프로젝트 기준 "sources/…")
    part: int
    parts: int
    prompt: Path
    response: Path
    direct: bool  # 스크립트가 못 읽어 AI가 원본을 직접 읽어야 함


@dataclass
class ScanReport:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (파일, 이유)
    pending: list[WorkItem] = field(default_factory=list)
    done: list[str] = field(default_factory=list)


@dataclass
class Accepted:
    ok: bool
    added: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    item: WorkItem | None = None
    remaining: int = 0


# --- 상태 ---


def _state_path(project: Project) -> Path:
    return project.state_dir / "sources.json"


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
    return project.state_dir / "sources" / f"{slug}.txt"


# --- scan · prep ---


def list_sources(project: Project) -> list[Path]:
    d = project.sources_dir
    if not d.is_dir():
        return []
    return sorted(p for p in d.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(d).parts))


def scan(project: Project, force: bool = False) -> ScanReport:
    """sources/ 를 훑어 새로 들어오거나 바뀐 파일의 글자를 꺼내고, 남은 조각의 프롬프트를 만든다."""
    state = _load_state(project)
    files = state.setdefault("files", {})
    report = ScanReport()
    present = set()
    for path in list_sources(project):
        rel = project.rel(path)
        present.add(rel)
        digest = _digest(path)
        info = files.get(rel)
        if info and info.get("sha1") == digest and not force:
            if info.get("status") == "done":
                report.done.append(rel)
            elif info.get("status") == "skipped":
                report.skipped.append((rel, info.get("note", "")))
            continue
        (report.changed if info else report.new).append(rel)
        slug = _slug(path, rel)
        _clear_work(project, slug)  # 예전 프롬프트·답이 남아 있으면 새 자료에 옛 답을 받아들이게 된다
        if path.stat().st_size > MAX_SOURCE_BYTES:
            got = Extracted(None, "unsupported", f"파일이 너무 큽니다({MAX_SOURCE_BYTES // 1_000_000}MB 넘음)")
        else:
            got = read_any(path)
        if got.text is None and got.kind in ("unsupported", "empty"):
            files[rel] = {"sha1": digest, "slug": slug, "kind": got.kind, "status": "skipped", "note": got.note, "at": now()}
            report.skipped.append((rel, got.note))
            continue
        text = mask_contacts(got.text) if got.text else ""  # 이력서의 전화·이메일은 카드에 필요 없다 — AI에게 넘기지 않는다
        parts = _split(text) if text else [""]
        cache = _text_cache(project, slug)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        files[rel] = {
            "sha1": digest, "slug": slug, "kind": got.kind, "direct": got.text is None, "note": got.note,
            "parts": len(parts), "done_parts": [], "cards": [], "status": "pending", "at": now(),
        }
    for rel in list(files):
        if rel not in present:
            _clear_work(project, files.pop(rel).get("slug", ""))  # 사용자가 지운 자료는 잊는다(이미 추가한 카드는 그대로)
    _save_state(project, state)
    report.pending = prepare(project)
    return report


def prepare(project: Project) -> list[WorkItem]:
    """끝나지 않은 조각마다 프롬프트를 만든다(이미 같은 내용이면 그대로 둔다)."""
    state = _load_state(project)
    items: list[WorkItem] = []
    kd = KeywordDict.load(project.data_dir)
    cards, _ = load_cards(project.experiences_file)
    existing = "\n".join(f"- {c.get('id')}: {c.get('title', '')}" for c in cards if c.get("id")) or "(없음)"
    card_tag_list = [t for c in cards for t in card_tags(c, kd)]
    project.exp_dir.mkdir(parents=True, exist_ok=True)
    for rel, info in state.get("files", {}).items():
        if info.get("status") != "pending":
            continue
        text = _text_cache(project, info["slug"]).read_text(encoding="utf-8") if not info.get("direct") else ""
        parts = _split(text) if text else [""]
        # 태그 표기: 자료에 나온 기술 낱말 + 이미 쓰는 카드 태그 (사전 전체를 넣으면 프롬프트가 길어진다)
        found = [canon for canon, _ in kd.scan(text)] if text else []
        tags = ", ".join(t for t in dict.fromkeys([*found, *card_tag_list]) if t not in ROLE_WORDS) or "(없음)"
        for n, chunk in enumerate(parts, start=1):
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
                body = chunk
            values = {
                "response_path": project.rel(item.response),
                "existing": existing,
                "tags": tags,
                "source_name": Path(rel).name,
                "part": f" (조각 {n}/{len(parts)})" if len(parts) > 1 else "",
                "source": body,
            }
            prompt = render(load_template(project, STEP), values)
            if not item.prompt.exists() or item.prompt.read_text(encoding="utf-8") != prompt:
                item.prompt.write_text(prompt, encoding="utf-8")
                log_usage(project, rel, STEP, "prompt", len(prompt))
            items.append(item)
    return items


def pending_items(project: Project) -> list[WorkItem]:
    state = _load_state(project)
    out = []
    for rel, info in state.get("files", {}).items():
        if info.get("status") != "pending":
            continue
        for n in range(1, int(info.get("parts", 1)) + 1):
            if n not in info.get("done_parts", []):
                item = _item(project, rel, info, n)
                if item.prompt.exists():
                    out.append(item)
    return out


def new_sources(project: Project) -> list[str]:
    """아직 scan 하지 않았거나 바뀐 자료(next 안내용, 파일은 쓰지 않는다)."""
    files = _load_state(project).get("files", {})
    out = []
    for path in list_sources(project):
        rel = project.rel(path)
        info = files.get(rel)
        if not info or info.get("sha1") != _digest(path):
            out.append(rel)
    return out


# --- accept ---


def pick(project: Project, raw_given: bool, prefer: str | None = None) -> WorkItem | None:
    """받아들일 조각. 답을 붙여넣으면 마지막으로 보여 준 프롬프트의 조각, 답 파일이면 답 파일이 있는 첫 조각."""
    items = pending_items(project)
    if not items:
        return None
    if raw_given:
        return next((i for i in items if project.rel(i.prompt) == prefer), items[0])
    return next((i for i in items if i.response.exists()), items[0])


def accept(project: Project, raw: str | None = None, prefer: str | None = None) -> Accepted:
    item = pick(project, raw is not None, prefer)
    if item is None:
        return Accepted(ok=False, errors=["경험 카드 답을 기다리는 자료가 없습니다. 먼저  jobfit exp extract"])
    if raw is not None:
        item.response.parent.mkdir(parents=True, exist_ok=True)
        item.response.write_text(raw, encoding="utf-8")
    if not item.response.exists():
        return Accepted(ok=False, errors=[f"답 파일이 없습니다: {project.rel(item.response)}"], item=item)
    text = item.response.read_text(encoding="utf-8", errors="replace")
    log_usage(project, item.source, STEP, "response", len(text))
    try:
        data = parse_cards(text)
    except ValueError as e:
        return Accepted(ok=False, errors=[str(e)], item=item)

    state = _load_state(project)
    info = state["files"][item.source]
    source_text = None if info.get("direct") else _text_cache(project, info["slug"]).read_text(encoding="utf-8")
    kd = KeywordDict.load(project.data_dir)
    existing, problems = load_cards(project.experiences_file)
    if problems:
        return Accepted(ok=False, errors=[f"{problems[0]} — experiences.yaml 을 고친 뒤 다시"], item=item)
    cards, warnings = normalize_cards(data, existing, kd, source_text, item.source)
    if source_text is None and cards:
        warnings.append("스크립트가 읽지 못한 파일이라 facts를 원문과 대조하지 못했습니다 — 숫자를 직접 확인하세요")
    if cards:
        try:
            append_cards(project, cards, item.source)
        except ValueError as e:
            return Accepted(ok=False, errors=[str(e)], item=item)
    info.setdefault("done_parts", []).append(item.part)
    info.setdefault("cards", []).extend(c["id"] for c in cards)
    if len(info["done_parts"]) >= int(info.get("parts", 1)):
        info["status"] = "done"
    info["at"] = now()
    _save_state(project, state)
    return Accepted(ok=True, added=cards, warnings=warnings, item=item, remaining=len(pending_items(project)))


def parse_cards(text: str) -> list:
    """답에서 카드 목록을 꺼낸다. 코드 블록이 있으면 그 안만, 없으면 첫 '- ' 줄부터 맨 앞 칸의 설명 문장 직전까지."""
    fenced = _FENCED.search(text)
    body = fenced.group(1) if fenced else _FENCE_LINE.sub("", text)
    lines = body.strip().splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith(("- ", "-\t", "[")) or ln.strip() == "-"), None)
    if start is None:
        raise ValueError("답에서 YAML 목록('- id: …')을 찾지 못했습니다. 답 형식 그대로 다시 요청하세요")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i][0].isspace() and not lines[i].startswith(("-", "#", "]"))),
        len(lines),
    )
    try:
        data = yaml.safe_load("\n".join(lines[start:end]))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" ({start + mark.line + 1}번째 줄 근처)" if mark else ""
        raise ValueError(f"답을 YAML로 읽지 못했습니다{where}. 답이 잘리지 않았는지 보고 다시 요청하세요") from e
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("답이 카드 목록이 아닙니다(맨 앞이 '- '로 시작해야 합니다)")
    return data


def normalize_cards(data: list, existing: list[dict], kd: KeywordDict, source_text: str | None, source: str) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    ids = {str(c.get("id")) for c in existing if c.get("id")}
    titles = {_norm_title(str(c.get("title", ""))) for c in existing}
    src_numbers = extract(source_text).numbers if source_text else None
    out: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            warnings.append("제목 없는 카드를 버렸습니다")
            continue
        if _norm_title(title) in titles:
            warnings.append(f"이미 있는 카드와 같은 경험이라 건너뜀: {title}")
            continue
        titles.add(_norm_title(title))
        cid = _unique_id(str(raw.get("id") or ""), title, ids)
        ids.add(cid)
        tags = _str_list(raw.get("tags"))
        tags = list(dict.fromkeys(kd.normalize(t) for t in tags))
        usable, bad = [], []
        for u in _str_list(raw.get("usable_for")):
            (usable if normalize_type(u) else bad).append(normalize_type(u) or u)
        if bad:
            warnings.append(f"{cid}: 문항 유형이 아닌 '{', '.join(bad)}'는 뺐습니다")
        star_raw = raw.get("star") if isinstance(raw.get("star"), dict) else {}
        star = {k: str(star_raw.get(k) or "").strip() for k in ("situation", "task", "action", "result")}
        star_nums = extract(" ".join(star.values())).numbers
        if src_numbers is not None and any(not same_value(n, src_numbers) for n in star_nums):
            warnings.append(f"{cid}: star에 자료 원문에 없는 숫자가 있습니다 — 고치거나 지우세요")
        facts = []
        for fact in _str_list(raw.get("facts")):
            nums = extract(fact).numbers
            if src_numbers is not None and nums and not all(same_value(n, src_numbers) for n in nums):
                warnings.append(f"{cid}: 자료 원문에 없는 숫자가 있어 이 fact를 뺐습니다 — \"{fact}\"")
                continue
            facts.append(fact)
        out.append({
            "id": cid,
            "title": title,
            "period": str(raw.get("period") or "").strip(),
            "org": str(raw.get("org") or "").strip(),
            "role": str(raw.get("role") or "").strip(),
            "tags": tags,
            "summary": str(raw.get("summary") or "").strip(),
            "star": star,
            "facts": facts,
            "usable_for": list(dict.fromkeys(usable)),
            "verified": False,
            "source": source,
        })
    return out, warnings


def append_cards(project: Project, cards: list[dict], source: str) -> Path:
    path = project.experiences_file
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _backup(path, project.backups_dir)
        current = path.read_text(encoding="utf-8")
    else:
        current = ""
    loaded = yaml.safe_load(current) if current.strip() else None
    if not loaded:
        # 빈 파일이거나 '[]' 만 있으면 — '[]' 줄 뒤에 '- ' 를 붙이면 yaml 이 깨진다
        current = "\n".join(ln for ln in current.splitlines() if ln.strip() != "[]")
        if not current.strip():
            current = FILE_HEADER
    stamp = datetime.now().strftime("%Y-%m-%d")
    block = f"\n# ── {source} 에서 AI가 뽑은 카드 ({stamp}) — 사실을 확인한 뒤 verified: true 로\n"
    block += "\n".join(card_yaml(c) for c in cards)
    new = current.rstrip("\n") + "\n" + block
    check = yaml.safe_load(new)
    if not isinstance(check, list) or len(check) != len(loaded or []) + len(cards):
        raise ValueError("카드를 덧붙인 결과가 yaml 목록으로 읽히지 않아 쓰지 않았습니다 — experiences.yaml 형식을 확인하세요")
    path.write_text(new, encoding="utf-8")
    return path


def card_yaml(card: dict) -> str:
    """me.example 과 같은 꼴 — tags·usable_for 는 한 줄 목록, star·facts 는 줄마다."""
    def scalar(v) -> str:
        return yaml.safe_dump([v], allow_unicode=True, default_flow_style=True, width=10_000).strip()[1:-1]

    def flow(values: list) -> str:
        return yaml.safe_dump(list(values), allow_unicode=True, default_flow_style=True, width=10_000).strip()

    lines = [f"- id: {scalar(card['id'])}"]
    for key in ("title", "period", "org", "role"):
        lines.append(f"  {key}: {scalar(card[key])}")
    lines.append(f"  tags: {flow(card['tags'])}")
    lines.append(f"  summary: {scalar(card['summary'])}")
    lines.append("  star:")
    lines += [f"    {k}: {scalar(v)}" for k, v in card["star"].items()]
    lines.append("  facts:" + ("" if card["facts"] else " []"))
    lines += [f"    - {scalar(f)}" for f in card["facts"]]
    lines.append(f"  usable_for: {flow(card['usable_for'])}")
    lines.append(f"  verified: {'true' if card['verified'] else 'false'}")
    lines.append(f"  source: {scalar(card['source'])}")
    return "\n".join(lines) + "\n"


# --- 내부 ---


def _digest(path: Path) -> str:
    st = path.stat()
    if st.st_size > MAX_SOURCE_BYTES:
        return f"size:{st.st_size}:{int(st.st_mtime)}"
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _clear_work(project: Project, slug: str) -> None:
    if not slug or not project.exp_dir.is_dir():
        return
    for old in project.exp_dir.glob(f"extract-{slug}-*.md"):
        old.unlink(missing_ok=True)


def _item(project: Project, rel: str, info: dict, n: int) -> WorkItem:
    name = f"extract-{info['slug']}-{n}"
    return WorkItem(
        source=rel, part=n, parts=int(info.get("parts", 1)),
        prompt=project.exp_dir / f"{name}.prompt.md", response=project.exp_dir / f"{name}.response.md",
        direct=bool(info.get("direct")),
    )


def _split(text: str) -> list[str]:
    """문단 경계에서 SOURCE_PART 이하 조각으로."""
    text = text.strip()
    if len(text) <= SOURCE_PART:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n|\n", text):
        if len(cur) + len(para) + 1 > SOURCE_PART and cur:
            parts.append(cur.strip())
            cur = ""
        while len(para) > SOURCE_PART:  # 한 문단이 너무 길면 자른다
            parts.append(para[:SOURCE_PART])
            para = para[SOURCE_PART:]
        cur += para + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _slug(path: Path, rel: str) -> str:
    stem = _NAME_SAFE.sub("-", path.stem).strip("-.")[:40] or "file"
    return f"{stem}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:6]}"


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", title)).lower()


def _unique_id(raw_id: str, title: str, taken: set[str]) -> str:
    base = _ID_SAFE.sub("-", raw_id.lower()).strip("-")
    if not base.startswith("exp-") or base == "exp-example" or len(base) <= 4:
        ascii_title = _ID_SAFE.sub("-", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()).strip("-")
        base = f"exp-{ascii_title}" if ascii_title else "exp-card"
    cid, n = base[:60], 2
    while cid in taken:
        cid = f"{base[:56]}-{n}"
        n += 1
    return cid


def _str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if isinstance(x, (str, int, float)) and str(x).strip()]


def _backup(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"experiences_{stamp}.yaml"
    n = 1
    while dst.exists():
        dst = backup_dir / f"experiences_{stamp}_{n}.yaml"
        n += 1
    shutil.copy2(path, dst)
    for old in sorted(backup_dir.glob("experiences_*.yaml"))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def limit() -> int:
    return LIMITS[STEP]
