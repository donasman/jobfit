"""문체 규칙(AI 1회, 공고와 무관) — 자소서 샘플 발췌 + 문체 수치 → me/style/style_rules.md 초안.

AI가 쓴 규칙은 사람이 읽고 고쳐서 확정한다. 이미 style_rules.md 가 있으면 덮어쓰지 않고 style_rules.new.md 로 둔다.
프롬프트는 output/_style/ 에 둔다(에이전트는 me/ 를 읽지 못하므로).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jobfit.ai.promptfile import LIMITS, load_template, log_usage, render
from jobfit.paths import Project
from jobfit.style import compute_stats, list_samples, read_sample, stats_lines, style_dir

STEP = "style-rules"
FILE_PROMPT = "style-rules.prompt.md"
FILE_RESPONSE = "style-rules.response.md"
EXCERPT_TOTAL = 3500
EXCERPT_EACH = 1200
_FENCE_LINE = re.compile(r"^[ \t]*```[\w-]*[ \t]*$", re.M)


@dataclass
class StyleResult:
    ok: bool
    path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def work_dir(project: Project) -> Path:
    return project.output_dir / "_style"


def prepare(project: Project) -> tuple[Path | None, str]:
    """(프롬프트 경로, 문제). 샘플이 없으면 경로 없이 문제만."""
    samples = list_samples(project.me_dir)
    if not samples:
        return None, "me/samples/ 에 내가 쓴 자소서(.txt · .md · .docx · .hwpx)를 넣어 주세요"
    texts = [t for t in (read_sample(p) for p in samples) if t.strip()]
    if not texts:
        return None, "샘플에서 글자를 읽지 못했습니다(.hwp는 .hwpx나 .txt로 바꿔 주세요)"
    stats = compute_stats(texts)
    excerpts, total = [], 0
    for path, text in zip(samples, texts):
        piece = text.strip()[:EXCERPT_EACH]
        if total + len(piece) > EXCERPT_TOTAL:
            piece = piece[: max(0, EXCERPT_TOTAL - total)]
        if not piece:
            break
        excerpts.append(f"### 샘플 {len(excerpts) + 1}\n{piece}")
        total += len(piece)
    d = work_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    values = {
        "response_path": project.rel(d / FILE_RESPONSE),
        "stats": "\n".join(stats_lines(stats)) or "(수치 없음)",
        "samples": "\n\n".join(excerpts),
    }
    text = render(load_template(project, STEP), values)
    path = d / FILE_PROMPT
    path.write_text(text, encoding="utf-8")
    log_usage(project, "-", STEP, "prompt", len(text))
    note = f"샘플 {len(texts)}개 · 프롬프트 {len(text)}자 / 상한 {LIMITS[STEP]}자"
    return path, note


def accept(project: Project, raw: str | None = None) -> StyleResult:
    d = work_dir(project)
    response = d / FILE_RESPONSE
    if raw is not None:
        d.mkdir(parents=True, exist_ok=True)
        response.write_text(raw, encoding="utf-8")
    if not response.exists():
        return StyleResult(ok=False, errors=[f"답 파일이 없습니다: {project.rel(response)}"])
    text = _FENCE_LINE.sub("", response.read_text(encoding="utf-8", errors="replace")).strip()
    log_usage(project, "-", STEP, "response", len(text))
    start = text.find("# 문체 규칙")
    if start < 0:
        return StyleResult(ok=False, errors=["'# 문체 규칙' 제목이 없습니다. 답 형식 그대로 다시 요청하세요"])
    text = text[start:]
    warnings = []
    if "## 피하는 표현" not in text:
        warnings.append("'## 피하는 표현' 절이 없어 금지 표현 검사를 하지 않습니다")
    if "## 대표 문단" not in text:
        warnings.append("'## 대표 문단' 절이 없습니다")
    target_dir = style_dir(project.me_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "style_rules.md"
    if target.exists():
        target = target_dir / "style_rules.new.md"
        warnings.append("style_rules.md 가 이미 있어 style_rules.new.md 로 저장했습니다 — 비교해서 고른 쪽을 style_rules.md 로")
    header = "<!-- AI가 쓴 초안입니다. 읽고 고친 뒤 이 줄을 지우면 확정입니다. -->\n"
    target.write_text(header + text + "\n", encoding="utf-8")
    return StyleResult(ok=True, path=target, warnings=warnings)
