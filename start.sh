#!/usr/bin/env bash
# jobfit 붙여넣기 모드 메뉴 (macOS/Linux). 무료 웹 채팅만 쓰는 사용자용.
cd "$(dirname "$0")" || exit 1

if ! command -v uv >/dev/null 2>&1; then
  echo "uv 가 없습니다. 아래 한 줄로 설치한 뒤 다시 실행하세요."
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

open_dir() { if command -v open >/dev/null; then open "$1"; else xdg-open "$1"; fi; }

me_menu() {
  while true; do
    cat <<'MENU'

[문체 · 경험 카드]
 1. 문체 수치 계산 (me/samples 의 내 자소서)
 2. 문체 규칙 프롬프트 복사 (클립보드)
 3. 문체 규칙 답 붙여넣기 (클립보드)
 4. 경험 카드 검사
 5. 빈 경험 카드 추가
 6. me 폴더 열기
 0. 돌아가기
MENU
    read -r -p "번호: " m || exit 0  # 입력이 끊기면(파이프·자동 실행) 끝낸다
    case "$m" in
      1) uv run jobfit style stats ;;
      2) uv run jobfit prep style-rules --copy ;;
      3) uv run jobfit accept style-rules --paste ;;
      4) uv run jobfit exp check ;;
      5) uv run jobfit exp new ;;
      6) open_dir me ;;
      0) return ;;
      *) echo "0~6 중에서 고르세요" ;;
    esac
  done
}

while true; do
  cat <<'MENU'

[jobfit]
 1. 처음 설정 (jobs.xlsx, 폴더 생성)
 2. 공고 처리 진행 (AI 단계 전까지 자동 실행)
 3. AI 프롬프트 복사 (클립보드)
 4. AI 응답 붙여넣기 (클립보드에서 저장·검증 후 이어서 진행)
 5. 공고 본문 붙여넣기 (수집 실패한 공고용)
 6. 결과 폴더 열기
 7. 문체·경험 카드
 8. 다음 할 일 보기
 9. jobs.xlsx 검사
 0. 종료
MENU
  read -r -p "번호: " c || exit 0  # 입력이 끊기면(파이프·자동 실행) 끝낸다
  case "$c" in
    1) uv run jobfit init ;;
    2) uv run jobfit run ;;
    3) uv run jobfit prep --copy ;;
    4) uv run jobfit accept --paste && uv run jobfit run ;;
    5) read -r -p "공고 (회사명 또는 공고번호): " j
       read -r -p "공고 본문을 복사한 뒤 Enter 를 누르세요." _
       uv run jobfit posting --job "$j" --from-clipboard ;;
    6) open_dir output ;;
    7) me_menu ;;
    8) uv run jobfit next ;;
    9) uv run jobfit check ;;
    0) exit 0 ;;
    *) echo "0~9 중에서 고르세요" ;;
  esac
done
