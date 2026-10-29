"""jobs.xlsx 생성 — `jobfit init`이 한 번 만든다. 그 뒤 스크립트가 이 파일에 쓰는 건 `jobfit add`뿐이다.

시트: 공고 · 문항 · 안내. 사용자가 손대는 열만 있고 스크립트 상태 열은 없다.
컬럼은 헤더 이름으로 찾으므로 사용자가 열 순서를 바꾸거나 열을 추가해도 된다.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from jobfit.state import DECISIONS, TASKS

SHEET_JOBS = "공고"
SHEET_QUESTIONS = "문항"
SHEET_GUIDE = "안내"

# 공고 시트. 마지막 "공고표시"는 문항 시트 드롭다운용 수식 열(숨김) — 스크립트 상태가 아니라 수식이다
JOB_HEADERS = ("회사명", "직무", "링크", "마감일", "메모", "결정", "작업")
JOB_DISPLAY_HEADER = "공고표시"
JOB_REQUIRED = ("회사명",)

QUESTION_HEADERS = ("공고", "번호", "문항", "글자수", "공백포함")
QUESTION_REQUIRED = ("공고", "문항")
SPACE_OPTIONS = ("포함", "제외")

MAX_ROWS = 500  # 드롭다운·수식 범위. 넘기면 안내 시트대로 범위를 늘린다

_HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
_HELPER_FILL = PatternFill("solid", fgColor="EEEEEE")
_HEADER_FONT = Font(bold=True)

GUIDE_LINES = (
    "jobfit 사용 안내",
    "",
    "[이 파일] 사용자가 입력하는 파일입니다. 스크립트는 읽기만 합니다.",
    "  예외는 하나 — jobfit add 를 실행하면 공고 시트 맨 아래 빈 줄에 한 줄을 추가합니다(엑셀이 열려 있으면 추가하지 않음, 직전 백업은 .jobfit/backups).",
    "[결과] 처리 상태·매칭 점수·다음 할 일은 dashboard.xlsx(스크립트가 매번 다시 만듦)와 output/<공고>/report.md에 있습니다.",
    "",
    "■ 공고 시트",
    "  회사명(필수) · 직무 · 링크 · 마감일 · 메모 — 공고를 한 줄에 하나씩 적습니다.",
    "  링크가 없으면 스크립트가 공고 폴더를 만든 뒤 output/<공고>/posting.txt에 본문을 붙여 넣으라고 안내합니다.",
    "  마감일은 2026-11-30 · 2026.11.30 · 2026/11/30 · 2026년 11월 30일 형식을 알아봅니다. 상시·채용시 마감이면 비워 두세요.",
    "  결정 — 미정(기본) / 보류 / 지원안함 / 지원완료 / 서류합격 / 탈락 / 최종합격.",
    "         보류·지원안함은 처리하지 않습니다(미정으로 되돌리면 다시 처리). 지원완료 이후 값은 끝난 공고로 봅니다.",
    "  작업 — 빈칸 / 재실행 / 초안만 재작성. 값이 바뀐 시점에 한 번만 실행됩니다.",
    "         실행이 끝나면 dashboard에 '작업 칸을 비워 주세요'가 뜹니다. 같은 작업을 다시 하려면 비웠다가 다시 고르세요.",
    "  공고표시(숨김, 회색) — 문항 시트 드롭다운용 수식입니다. 지우면 드롭다운만 사라지고 직접 입력은 됩니다.",
    "",
    "■ 문항 시트",
    "  자소서 문항을 직접 입력할 때만 씁니다. 공고 원문에서 추출된 문항은 여기 쓰지 않고 분석 결과·대시보드에만 나옵니다.",
    "  같은 공고에 여기 입력한 행이 있으면 추출본 대신 입력한 문항만 씁니다.",
    "  공고 — 드롭다운에서 '회사명 / 직무'를 고르거나 회사명만 적습니다(같은 회사 공고가 둘 이상이면 '회사명 / 직무'로).",
    "  글자수 — 숫자만. 모르면 비웁니다. 공백포함 — 포함(기본) / 제외.",
    "",
    "■ 공고는 어떻게 구분하나",
    "  링크에서 공고키를 만듭니다(예: saramin:55145352). 링크가 없으면 회사명·직무·마감일로 만듭니다.",
    "  그래서 링크를 고치면 새 공고로 처리되고, 링크 없는 공고는 회사명·직무·마감일을 고치면 새 공고가 됩니다.",
    "",
    "■ 하면 안 되는 것",
    "  헤더 이름 바꾸기 · 시트 이름 바꾸기 · 병합 셀 · 매크로. 열 순서 바꾸기와 열 추가는 됩니다.",
    f"  드롭다운·수식 범위는 {MAX_ROWS}행까지입니다. 넘기면 공고표시 열 수식을 아래로 복사하세요.",
    "",
    "■ 막힐 때",
    "  jobfit check   — 이 파일의 문제 행을 알려 줍니다.",
    "  jobfit next    — 다음에 할 일 하나를 알려 줍니다.",
)


def create_jobs_workbook(path: Path) -> None:
    """jobs.xlsx를 만든다. 이미 있으면 덮어쓰지 않고 예외를 낸다."""
    if path.exists():
        raise FileExistsError(path)
    wb = Workbook()
    ws_jobs = wb.active
    ws_jobs.title = SHEET_JOBS
    _build_jobs_sheet(ws_jobs)
    _build_questions_sheet(wb.create_sheet(SHEET_QUESTIONS))
    _build_guide_sheet(wb.create_sheet(SHEET_GUIDE))
    ensure_validations(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def header_letters(ws) -> dict[str, str]:
    """1행 헤더 이름 -> 열 문자. 같은 이름이 둘이면 앞의 것."""
    out: dict[str, str] = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1), ()):
        name = str(cell.value).strip() if cell.value is not None else ""
        if name and name not in out:
            out[name] = cell.column_letter
    return out


def display_formula(company_col: str, title_col: str | None, row: int) -> str:
    """문항 시트 드롭다운에 보일 '회사명 / 직무'."""
    c = f"{company_col}{row}"
    if not title_col:
        return f'=IF({c}="","",{c})'
    t = f"{title_col}{row}"
    return f'=IF({c}="","",{c}&IF({t}="",""," / "&{t}))'


def ensure_validations(wb) -> int:
    """우리 드롭다운·입력 검사가 빠져 있으면 다시 단다. 단 개수를 돌려준다.

    엑셀이 저장하면서 다른 시트를 참조하는 목록 검사를 확장 형식(x14)으로 바꾸면 openpyxl이 읽다가 버린다.
    `jobfit add`가 파일을 다시 저장할 때 이 함수로 메운다. 이미 같은 범위에 검사가 있으면 건드리지 않는다.
    """
    added = 0
    jobs_cols = header_letters(wb[SHEET_JOBS]) if SHEET_JOBS in wb.sheetnames else {}
    if jobs_cols:
        ws = wb[SHEET_JOBS]
        if "결정" in jobs_cols:
            added += _ensure(ws, _list_dv(DECISIONS, "결정"), jobs_cols["결정"])
        if "작업" in jobs_cols:
            added += _ensure(ws, _list_dv([t for t in TASKS if t], "작업"), jobs_cols["작업"])
        if "마감일" in jobs_cols:
            dv = DataValidation(type="date", operator="greaterThan", formula1="DATE(2000,1,1)", allow_blank=True)
            dv.errorStyle = "warning"
            dv.error = "날짜가 아닌 것 같습니다. 2026-11-30 형식으로 적어 주세요."
            dv.errorTitle = "마감일"
            added += _ensure(ws, dv, jobs_cols["마감일"])
    if SHEET_QUESTIONS in wb.sheetnames:
        wq = wb[SHEET_QUESTIONS]
        q_cols = header_letters(wq)
        display = jobs_cols.get(JOB_DISPLAY_HEADER)
        if "공고" in q_cols and display:
            # 목록에 없는 값도 허용(경고만) — 회사명만 적어도 된다
            dv = DataValidation(type="list", formula1=f"='{SHEET_JOBS}'!${display}$2:${display}${MAX_ROWS}", allow_blank=True)
            dv.errorStyle = "warning"
            dv.error = "공고 시트에 없는 이름입니다. 회사명을 그대로 적었는지 확인하세요."
            dv.errorTitle = "공고"
            added += _ensure(wq, dv, q_cols["공고"])
        if "공백포함" in q_cols:
            added += _ensure(wq, _list_dv(SPACE_OPTIONS, "공백포함"), q_cols["공백포함"])
        if "글자수" in q_cols:
            dv = DataValidation(type="whole", operator="greaterThan", formula1="0", allow_blank=True)
            dv.errorStyle = "warning"
            dv.error = "글자수는 숫자만 적습니다."
            added += _ensure(wq, dv, q_cols["글자수"])
    return added


def _list_dv(options, title: str) -> DataValidation:
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True)
    dv.error = "목록에서 고르세요"
    dv.errorTitle = title
    return dv


def _ensure(ws, dv: DataValidation, letter: str) -> int:
    sqref = f"{letter}2:{letter}{MAX_ROWS}"
    for existing in ws.data_validations.dataValidation:
        if str(existing.sqref) == sqref:
            return 0
    ws.add_data_validation(dv)
    dv.add(sqref)
    return 1


def _build_jobs_sheet(ws) -> None:
    headers = (*JOB_HEADERS, JOB_DISPLAY_HEADER)
    ws.append(headers)
    widths = {"회사명": 18, "직무": 28, "링크": 48, "마감일": 12, "메모": 30, "결정": 10, "작업": 14, JOB_DISPLAY_HEADER: 30}
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = widths[name]
    ws.freeze_panes = "A2"

    col_deadline = get_column_letter(headers.index("마감일") + 1)
    for r in range(2, MAX_ROWS + 1):
        ws[f"{col_deadline}{r}"].number_format = "yyyy-mm-dd"

    # 문항 시트 드롭다운용 "회사명 / 직무" 수식. 회색·숨김
    c = get_column_letter(headers.index("회사명") + 1)
    t = get_column_letter(headers.index("직무") + 1)
    display_letter = get_column_letter(headers.index(JOB_DISPLAY_HEADER) + 1)
    for r in range(2, MAX_ROWS + 1):
        cell = ws[f"{display_letter}{r}"]
        cell.value = display_formula(c, t, r)
        cell.fill = _HELPER_FILL
    ws.column_dimensions[display_letter].hidden = True


def _build_questions_sheet(ws) -> None:
    ws.append(QUESTION_HEADERS)
    widths = {"공고": 32, "번호": 6, "문항": 70, "글자수": 8, "공백포함": 10}
    for col, name in enumerate(QUESTION_HEADERS, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = widths[name]
    ws.freeze_panes = "A2"
    col_q = get_column_letter(QUESTION_HEADERS.index("문항") + 1)
    for r in range(2, MAX_ROWS + 1):
        ws[f"{col_q}{r}"].alignment = Alignment(wrap_text=True, vertical="top")


def _build_guide_sheet(ws) -> None:
    ws.column_dimensions["A"].width = 120
    for i, line in enumerate(GUIDE_LINES, start=1):
        cell = ws.cell(row=i, column=1, value=line)
        if i == 1:
            cell.font = Font(bold=True, size=14)
        elif line.startswith("■"):
            cell.font = _HEADER_FONT
    ws.protection.sheet = True  # 읽기 전용 표시. 비밀번호 없음
