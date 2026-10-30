"""문체 — 원안 자소서 읽기, 문체 통계(스크립트), 문체 규칙 파일, 초안을 쓸 수 있는지.

원안: sources/자소서/ 의 .txt · .md · .docx · .hwpx · .pdf — 잘 쓴 자소서(남의 것도 된다). 예전 자리 me/samples/ 도 읽는다.
  글자 꺼내기는 textio. 경험 카드 뽑기에는 쓰지 않는다(exp_extract.list_sources).
통계: me/style/style_stats.json — `run`(원안이 바뀌면)과 `jobfit style stats`가 계산한다. AI 없음.
규칙: me/style/style_rules.md — 원안의 문체·흐름을 AI가 한 번 정리하고 사람이 확정한다(`prep style-rules`).
초안은 원안과 규칙이 있을 때만 쓴다. 원안 없이 처음부터 쓰는 것은 이 도구의 방향이 아니다(사용자 결정, SPEC §14 46).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from jobfit.paths import SAMPLES_DIRNAME, Project
from jobfit.textio import read_any

SAMPLE_EXTS = (".txt", ".md", ".docx", ".hwpx", ".pdf")
RULES_DRAFT_HEADER = "<!-- AI가 쓴 초안입니다. 읽고 고친 뒤 이 줄을 지우면 확정입니다. -->"
NO_SAMPLES = f"원안이 될 자소서가 없습니다 — sources/{SAMPLES_DIRNAME}/ 에 잘 쓴 자소서를 넣으세요(.txt · .md · .docx · .hwpx · .pdf)"
NO_RULES = "원안 자소서의 문체·흐름 분석(문체 규칙)이 아직입니다"
MIN_SAMPLES_FOR_LINT = 5
SPECIAL_CHARS = ("·", "…", '"', "'", "(", "~", "!", "—")
CONNECTIVES = ("그래서", "하지만", "그러나", "또한", "따라서", "이를 통해", "그 결과", "특히", "먼저", "이후", "그리고", "결국", "덕분에", "반면", "이처럼", "즉")
_ENDINGS = (("했습니다", r"했습니다"), ("입니다", r"입니다"), ("였습니다", r"(였|었|았)습니다"), ("합니다", r"합니다"), ("겠습니다", r"겠습니다"), ("습니다(기타)", r"습니다"), ("~다", r"다"))
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def style_dir(me_dir: Path) -> Path:
    return me_dir / "style"


def list_samples(project: Project) -> list[Path]:
    """원안 자소서 — sources/자소서/(하위 폴더 포함)와 예전 자리 me/samples/. 같은 파일이 두 곳에 있으면 한 번만."""
    found: list[Path] = []
    for d in (project.samples_dir, project.legacy_samples_dir):
        if d.is_dir():
            found += sorted(
                p for p in d.rglob("*")
                if p.is_file() and p.suffix.lower() in SAMPLE_EXTS and not any(part.startswith((".", "~$")) for part in p.relative_to(d).parts)
            )
    seen: set[str] = set()
    out = []
    for p in found:
        digest = hashlib.sha1(p.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            out.append(p)
    return out


def draft_blocker(project: Project) -> str:
    """초안을 쓸 수 없는 이유. 원안 자소서와 그 문체 규칙이 있어야 쓴다. 쓸 수 있으면 빈 문자열."""
    if not list_samples(project):
        return NO_SAMPLES
    if not load_rules(project.me_dir).strip():
        return NO_RULES
    return ""


def style_pending(project: Project) -> bool:
    """원안은 있는데 문체 규칙이 아직 — 문체 규칙 AI 단계가 할 일이다."""
    return draft_blocker(project) == NO_RULES


def write_stats(project: Project, samples: list[Path] | None = None) -> tuple[dict, list[str]]:
    """원안에서 문체 수치를 계산해 style_stats.json 에 쓴다. (수치, 글자를 못 읽은 파일 이름)."""
    samples = list_samples(project) if samples is None else samples
    texts, unreadable = [], []
    for p in samples:
        text = read_sample(p)
        if text.strip():
            texts.append(text)
        else:
            unreadable.append(p.name)
    stats = compute_stats(texts)
    stats["samples_sha"] = _samples_sha(samples)
    out = style_dir(project.me_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "style_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats, unreadable


def refresh_stats(project: Project) -> dict | None:
    """원안이 바뀌었으면 수치를 다시 계산한다(run). 다시 계산했으면 그 수치, 아니면 None."""
    samples = list_samples(project)
    if not samples:
        return None
    stats = load_stats(project.me_dir) or {}
    if stats.get("samples_sha") == _samples_sha(samples):
        return None
    return write_stats(project, samples)[0]


def _samples_sha(samples: list[Path]) -> str:
    h = hashlib.sha1()
    for p in samples:
        h.update(p.read_bytes())
    return h.hexdigest()


def is_draft_rules(me_dir: Path) -> bool:
    """문체 규칙이 AI가 쓴 초안 그대로인가(사람이 확정하지 않음)."""
    return load_rules(me_dir).startswith(RULES_DRAFT_HEADER)


def read_sample(path: Path) -> str:
    return read_any(path).text or ""


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= 2]


def compute_stats(texts: list[str]) -> dict:
    sentences = [s for t in texts for s in split_sentences(t)]
    paragraphs = [p for t in texts for p in re.split(r"\n\s*\n", t) if p.strip()]
    lengths = [len(s) for s in sentences] or [0]
    endings: Counter[str] = Counter()
    for s in sentences:
        tail = s.rstrip(".!? ")
        for name, pat in _ENDINGS:
            if re.search(pat + r"$", tail):
                endings[name] += 1
                break
    joined = "\n".join(texts)
    connectives = Counter({c: len(re.findall(re.escape(c), joined)) for c in CONNECTIVES})
    return {
        "samples": len(texts),
        "chars": len(joined),
        "sentences": len(sentences),
        "sentence_len_avg": round(sum(lengths) / max(len(sentences), 1), 1),
        "sentence_len_max": max(lengths),
        "commas_per_sentence": round(sum(s.count(",") for s in sentences) / max(len(sentences), 1), 2),
        "special": {ch: joined.count(ch) for ch in SPECIAL_CHARS},
        "endings": dict(endings.most_common()),
        "paragraphs": len(paragraphs),
        "sentences_per_paragraph": round(len(sentences) / max(len(paragraphs), 1), 1),
        "connectives": [c for c, n in connectives.most_common(10) if n],
    }


def load_stats(me_dir: Path) -> dict | None:
    """예전 버전이 쓴 수치 파일도 읽는다. 읽을 수 없거나 모양이 다르면 없는 것으로 보고 다시 잰다."""
    path = style_dir(me_dir) / "style_stats.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def stats_lines(stats: dict | None) -> list[str]:
    """프롬프트에 넣을 핵심 수치 3~4줄."""
    if not stats or not stats.get("sentences"):
        return []
    lines = [f"- 문장 길이: 평균 {stats['sentence_len_avg']}자, 가장 긴 문장 {stats['sentence_len_max']}자", f"- 문장당 쉼표 {stats['commas_per_sentence']}개"]
    if stats.get("endings"):
        top = ", ".join(list(stats["endings"])[:2])
        lines.append(f"- 주로 쓰는 끝맺음: {top}")
    unused = [ch for ch, n in stats.get("special", {}).items() if n == 0]
    if unused:
        lines.append(f"- 쓰지 않는 기호: {' '.join(unused)}")
    return lines


def load_rules(me_dir: Path) -> str:
    path = style_dir(me_dir) / "style_rules.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


_FORBIDDEN_HEAD = re.compile(r"^#{1,6}\s*피하는\s*표현", re.M)
_QUOTED = re.compile(r"[\"'“”‘’「」`]([^\"'“”‘’「」`]{1,30})[\"'“”‘’「」`]")


def forbidden_expressions(rules: str) -> list[str]:
    """style_rules.md 의 '## 피하는 표현' 절 글머리에서 표현만. 따옴표가 있으면 따옴표 안을 쓴다."""
    m = _FORBIDDEN_HEAD.search(rules)
    if not m:
        return []
    out: list[str] = []
    for line in rules[m.end():].splitlines()[1:]:
        if line.startswith("#"):
            break
        s = line.strip()
        if not s.startswith(("-", "*")):
            continue
        s = s.lstrip("-* ").strip()
        quoted = _QUOTED.findall(s)
        if quoted:
            out.extend(q.strip() for q in quoted if q.strip())
        else:
            head = re.split(r"\s[—–-]\s|\s\(|:", s)[0].strip()
            if 0 < len(head) <= 30:
                out.append(head)
    return list(dict.fromkeys(out))
