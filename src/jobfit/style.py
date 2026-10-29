"""문체 — 자소서 샘플 읽기, 문체 통계(스크립트), 문체 규칙 파일에서 금지 표현 읽기.

샘플: me/samples/ 의 .txt · .md · .docx · .hwpx · .pdf (내가 쓴 자소서 원문). 글자 꺼내기는 textio.
통계: me/style/style_stats.json — `jobfit style stats`가 계산한다. AI 없음.
규칙: me/style/style_rules.md — AI가 한 번 쓰고 사람이 확정한다(`prep style-rules`).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from jobfit.textio import read_any

SAMPLE_EXTS = (".txt", ".md", ".docx", ".hwpx", ".pdf")
MIN_SAMPLES_FOR_LINT = 5
SPECIAL_CHARS = ("·", "…", '"', "'", "(", "~", "!", "—")
CONNECTIVES = ("그래서", "하지만", "그러나", "또한", "따라서", "이를 통해", "그 결과", "특히", "먼저", "이후", "그리고", "결국", "덕분에", "반면", "이처럼", "즉")
_ENDINGS = (("했습니다", r"했습니다"), ("입니다", r"입니다"), ("였습니다", r"(였|었|았)습니다"), ("합니다", r"합니다"), ("겠습니다", r"겠습니다"), ("습니다(기타)", r"습니다"), ("~다", r"다"))
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def samples_dir(me_dir: Path) -> Path:
    return me_dir / "samples"


def style_dir(me_dir: Path) -> Path:
    return me_dir / "style"


def list_samples(me_dir: Path) -> list[Path]:
    d = samples_dir(me_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SAMPLE_EXTS)


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
    path = style_dir(me_dir) / "style_stats.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


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
