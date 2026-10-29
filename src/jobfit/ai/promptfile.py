"""프롬프트 파일 공통 — 템플릿 채우기, 글자수 상한, 사용량 기록, 클립보드."""

from __future__ import annotations

import hashlib
import json
import re

from jobfit.paths import Project
from jobfit.state import now

# 프롬프트 글자수 상한(SPEC §5). 넘으면 경고하고 줄일 대상을 알려 준다
LIMITS = {"analyze": 6000, "draft": 8000, "revise": 6000, "style-rules": 6000, "exp-extract": 8000}
TAGS_MAX_CHARS = 600  # 프롬프트에 넣는 태그 목록 최대 글자수 — 카드가 늘어도 프롬프트가 끝없이 길어지지 않게

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def load_template(project: Project, name: str) -> str:
    return (project.prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def render(template: str, values: dict[str, str]) -> str:
    """{{이름}}을 값으로. 템플릿에 있는데 값이 없으면 KeyError — 빈칸으로 나가지 않게."""
    missing = sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)} - set(values))
    if missing:
        raise KeyError(f"프롬프트 템플릿의 빈칸에 넣을 값이 없습니다: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def fit_list(items: list[str], budget: int, sep: str = ", ") -> str:
    """앞에서부터 budget 글자 안에 들어가는 만큼, 나머지는 '외 N개'."""
    if not items:
        return "(없음)"
    out: list[str] = []
    used = 0
    for item in items:
        tail = len(f"{sep}외 {len(items)}개")
        if used + len(item) + len(sep) + tail > budget and out:
            break
        out.append(item)
        used += len(item) + len(sep)
    rest = len(items) - len(out)
    return sep.join(out) + (f"{sep}외 {rest}개" if rest else "")


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def log_usage(project: Project, job_key: str, step: str, kind: str, chars: int) -> None:
    """kind: prompt | response | cached."""
    project.state_dir.mkdir(parents=True, exist_ok=True)
    record = {"at": now(), "job": job_key, "step": step, "kind": kind, "chars": chars}
    with project.usage_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_usage(project: Project) -> list[dict]:
    if not project.usage_file.exists():
        return []
    out = []
    for line in project.usage_file.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:  # noqa: BLE001 — 클립보드가 없는 환경(서버·일부 리눅스)
        return False


def read_clipboard() -> str | None:
    try:
        import pyperclip

        return pyperclip.paste()
    except Exception:  # noqa: BLE001
        return None
