"""배포용 zip 만들기 — dist/jobfit-v<버전>.zip. 사용자에게 필요한 파일만 넣는다(테스트·개발 파일 제외).

uv run python tools/build_release.py
"""

from __future__ import annotations

import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = [
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/settings.json",
    ".python-version",
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "start.bat",
    "start.sh",
    "docs",
    "me.example",
    "src",
]
EXECUTABLE = {"start.sh"}
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    out = ROOT / "dist" / f"jobfit-v{version}.zip"
    out.parent.mkdir(exist_ok=True)
    files: list[Path] = []
    for item in INCLUDE:
        path = ROOT / item
        if not path.exists():
            print(f"없음: {item}")
            return 1
        files.extend(sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path])
    files = [f for f in files if not SKIP_PARTS & set(f.relative_to(ROOT).parts) and f.suffix not in (".pyc", ".pyo")]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"jobfit/{rel}")
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if rel in EXECUTABLE else 0o644
            info.external_attr = (0o100000 | mode) << 16
            data = f.read_bytes()
            if rel == "start.bat":
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # 배치 파일은 CRLF여야 레이블이 안 깨진다
            z.writestr(info, data)
    print(f"{out.relative_to(ROOT)} — 파일 {len(files)}개, {out.stat().st_size // 1024}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
