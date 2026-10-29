@echo off
rem jobfit 붙여넣기 모드 메뉴 (Windows). 무료 웹 채팅만 쓰는 사용자용.
chcp 65001 >nul
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv 가 없습니다. PowerShell 에서 아래 한 줄을 실행해 설치한 뒤 다시 여세요.
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)

set empty=0

:menu
rem 입력이 끊기면(파이프·자동 실행) 빈 입력이 이어진다 — 20번 연속이면 끝낸다
if %empty% GEQ 20 exit /b 1
echo.
echo [jobfit]
echo  1. 처음 설정 (jobs.xlsx, 폴더 생성)
echo  2. 공고 처리 진행 (AI 단계 전까지 자동 실행)
echo  3. AI 프롬프트 복사 (클립보드)
echo  4. AI 응답 붙여넣기 (클립보드에서 저장·검증 후 이어서 진행)
echo  5. 공고 본문 붙여넣기 (수집 실패한 공고용)
echo  6. 결과 폴더 열기
echo  7. 문체·경험 카드
echo  8. 다음 할 일 보기
echo  9. jobs.xlsx 검사
echo  0. 종료
rem 빈 Enter 로 앞 번호가 다시 실행되지 않게 비우고 묻는다
set "c="
set /p "c=번호: "
if not defined c (set /a empty+=1) else (set empty=0)
if "%c%"=="1" uv run jobfit init
if "%c%"=="2" uv run jobfit run
if "%c%"=="3" uv run jobfit prep --copy
if "%c%"=="4" (uv run jobfit accept --paste && uv run jobfit run)
if "%c%"=="5" goto paste
if "%c%"=="6" start "" "%~dp0output"
if "%c%"=="7" goto me
if "%c%"=="8" uv run jobfit next
if "%c%"=="9" uv run jobfit check
if "%c%"=="0" exit /b 0
goto menu

:paste
set "j="
set /p "j=공고 (회사명 또는 공고번호): "
echo 공고 본문을 복사한 뒤 Enter 를 누르세요.
pause >nul
uv run jobfit posting --job "%j%" --from-clipboard
goto menu

:me
if %empty% GEQ 20 exit /b 1
echo.
echo [문체 · 경험 카드]
echo  1. 문체 수치 계산 (me\samples 의 내 자소서)
echo  2. 문체 규칙 프롬프트 복사 (클립보드)
echo  3. 문체 규칙 답 붙여넣기 (클립보드)
echo  4. 경험 카드 검사
echo  5. 빈 경험 카드 추가
echo  6. me 폴더 열기
echo  0. 돌아가기
set "m="
set /p "m=번호: "
if not defined m (set /a empty+=1) else (set empty=0)
if "%m%"=="1" uv run jobfit style stats
if "%m%"=="2" uv run jobfit prep style-rules --copy
if "%m%"=="3" uv run jobfit accept style-rules --paste
if "%m%"=="4" uv run jobfit exp check
if "%m%"=="5" uv run jobfit exp new
if "%m%"=="6" start "" "%~dp0me"
if "%m%"=="0" goto menu
goto me
