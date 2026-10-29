"""start.bat·start.sh 메뉴 번호 — 안내 문구가 메뉴 번호를 말할 때는 여기서 가져온다(메뉴를 바꾸면 이 파일만 고친다)."""

ADD = 1  # 공고 추가 (링크)
QUESTIONS = 2  # 문항 넣기
RUN = 3  # 공고 처리 진행
POSTING = 4  # 공고 본문 붙여넣기
NEXT = 5  # 다음 할 일 보기
OUTPUT = 6  # 결과 폴더 열기
ME = 7  # 경험 카드 · 문체
CHECK = 8  # jobs.xlsx 검사
PROMPT = 9  # AI 프롬프트 복사 (웹 채팅)
ANSWER = 10  # AI 답 붙여넣기 (웹 채팅)


def at(number: int, name: str) -> str:
    """'start.bat 3번(공고 처리 진행)' — 번호만 쓰면 메뉴를 바꿨을 때 틀린 안내가 되므로 이름을 함께."""
    return f"start.bat {number}번({name})"
