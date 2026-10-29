#!/usr/bin/env bash
# jobfit 메뉴 (macOS/Linux). 화면은 jobfit menu(파이썬)가 그린다 — start.bat 과 같은 메뉴.
cd "$(dirname "$0")" || exit 1

if ! command -v uv >/dev/null 2>&1; then
  echo "uv 가 없습니다. 아래 한 줄로 설치한 뒤 다시 실행하세요."
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

exec uv run jobfit menu
