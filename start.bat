@echo off
rem jobfit 메뉴 (Windows). 화면은 jobfit menu(파이썬)가 그린다 — start.sh 와 같은 메뉴.
chcp 65001 >nul
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv 가 없습니다. PowerShell 에서 아래 한 줄을 실행해 설치한 뒤 다시 여세요.
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)

uv run jobfit menu
if errorlevel 1 pause
