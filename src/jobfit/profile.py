"""지원서 정보 me/profile.yaml — 지원서의 학력·경력·자격증·어학·병역·교육·수상·활동 칸을 채울 때 쓴다(jobfit apply sheet).

이름·연락처·주소·생년월일 같은 신원 정보는 이 파일에 두지 않는다 — 지원서 화면에서 사람이 직접 입력한다(사용자 결정).
경험 카드(experiences.yaml)처럼 항목 하나가 '- section: …' 한 덩어리인 목록이라, AI가 뽑은 항목을 맨 아래에 덧붙이기 쉽다.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

# 칸 순서가 지원서 화면과 입력표의 열 순서다. 첫 칸이 그 항목의 이름(같은 항목인지 가르는 칸)
SECTIONS: dict[str, tuple[str, ...]] = {
    "학력": ("school", "major", "degree", "period", "status", "gpa"),
    "경력": ("company", "department", "position", "employment", "period", "duties"),
    "자격증": ("name", "issuer", "date", "number"),
    "어학": ("test", "score", "grade", "date"),
    "병역": ("status", "branch", "rank", "period"),
    "교육": ("name", "institution", "period", "hours", "content"),
    "수상": ("name", "issuer", "date", "content"),
    "활동": ("name", "org", "role", "period", "content"),
}
LABELS = {
    "school": "학교", "major": "전공", "degree": "학위", "period": "기간", "status": "상태", "gpa": "학점",
    "company": "회사", "department": "부서", "position": "직위", "employment": "고용형태", "duties": "담당 업무",
    "name": "이름", "issuer": "발급·수여 기관", "date": "일자", "number": "번호",
    "test": "시험", "score": "점수", "grade": "등급",
    "branch": "군별", "rank": "계급", "institution": "기관", "hours": "시간", "content": "내용", "org": "기관", "role": "역할",
}
SECTION_LABELS = {("자격증", "name"): "자격증명", ("교육", "name"): "교육명", ("수상", "name"): "수상명", ("활동", "name"): "활동명", ("병역", "status"): "구분"}
_ALIASES = {
    "학력사항": "학력", "학교": "학력", "경력사항": "경력", "직장": "경력", "자격": "자격증", "자격사항": "자격증",
    "어학성적": "어학", "외국어": "어학", "어학능력": "어학", "병역사항": "병역", "군": "병역",
    "교육이수": "교육", "교육사항": "교육", "수상경력": "수상", "수상내역": "수상", "대외활동": "활동", "활동사항": "활동",
}
BACKUP_KEEP = 10
FILE_HEADER = """# 지원서 정보 — 지원서의 학력·경력·자격증·어학·병역·교육·수상·활동 칸을 채울 때 쓴다 (jobfit apply sheet).
# 이름·연락처·주소·생년월일 같은 신원 정보는 넣지 않는다 — 지원서 화면에서 직접 입력한다.
# 항목 하나 = '- section: …' 한 덩어리. section 은 학력·경력·자격증·어학·병역·교육·수상·활동 중 하나.
# 날짜는 2025-06-13 또는 2025-06, 기간은 2018-03 ~ 2024-02 꼴. 사실을 확인한 항목은 verified: true.
"""


@dataclass
class ProfileIssue:
    level: str  # "오류" | "경고"
    where: str
    message: str


def normalize_section(value) -> str | None:
    s = re.sub(r"\s+", "", str(value or ""))
    if s in SECTIONS:
        return s
    return _ALIASES.get(s)


def key_field(section: str) -> str:
    return SECTIONS[section][0]


def label(section: str, field: str) -> str:
    return SECTION_LABELS.get((section, field), LABELS.get(field, field))


def item_name(item: dict) -> str:
    section = normalize_section(item.get("section")) or ""
    return str(item.get(key_field(section), "") if section else "").strip()


def load_profile(path: Path) -> tuple[list[dict], list[str]]:
    """(항목들, 읽기 문제). 파일이 없으면 빈 목록."""
    if not path.exists():
        return [], []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [], ["profile.yaml 을 읽을 수 없습니다" + (f"({mark.line + 1}번째 줄 근처)" if mark else "")]
    if data is None:
        return [], []
    if not isinstance(data, list):
        return [], ["profile.yaml 은 '- section: …' 항목의 목록이어야 합니다"]
    return [d for d in data if isinstance(d, dict)], []


def check_profile(items: list[dict]) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    for i, item in enumerate(items, start=1):
        section = normalize_section(item.get("section"))
        where = f"{i}번째 항목"
        if section is None:
            issues.append(ProfileIssue("오류", where, f"section '{item.get('section')}' 은(는) {' · '.join(SECTIONS)} 중 하나여야 합니다"))
            continue
        name = item_name(item)
        where = f"{section} '{name}'" if name else f"{i}번째 항목({section})"
        if not name:
            issues.append(ProfileIssue("오류", where, f"{label(section, key_field(section))} 칸이 비어 있습니다"))
        unknown = [k for k in item if k not in (*SECTIONS[section], "section", "verified", "source")]
        if unknown:
            issues.append(ProfileIssue("경고", where, f"{section}에 없는 칸이라 쓰지 않습니다: {', '.join(map(str, unknown))}"))
        if item.get("verified") is not True:
            issues.append(ProfileIssue("경고", where, "verified 가 true 가 아닙니다 — 입력표에 [확인 전]으로 나옵니다"))
    return issues


def split_examples(items: list[dict], example_file: Path) -> tuple[list[dict], list[str]]:
    """(내 항목, me.example 에서 그대로 복사된 예시 항목 이름). section 과 이름이 같으면 예시로 본다."""
    examples, _ = load_profile(example_file)
    pairs = {_pair(i) for i in examples}
    mine = [i for i in items if _pair(i) not in pairs]
    return mine, [item_name(i) for i in items if _pair(i) in pairs]


def example_note(items: list[dict], example_names: list[str]) -> str:
    if not example_names:
        return ""
    if len(example_names) == len(items):
        return "me/profile.yaml 이 예시 그대로입니다 — 지원서 입력표에 쓰지 않습니다. 내 정보로 바꾸거나(지원서 정보 만들기) 파일을 지우세요"
    shown = ", ".join(example_names[:5]) + (f" 외 {len(example_names) - 5}개" if len(example_names) > 5 else "")
    return f"me/profile.yaml 에 예시 항목 {len(example_names)}개가 남아 있습니다({shown}) — 입력표에서 뺐습니다. 직접 지우세요"


def same_item(a: dict, b: dict) -> bool:
    return _pair(a) == _pair(b)


def append_items(path: Path, backup_dir: Path, items: list[dict], source: str) -> None:
    """맨 아래에 덧붙인다. 쓰기 전 백업, 덧붙인 뒤에도 목록으로 읽히는지 확인하고 아니면 쓰지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _backup(path, backup_dir)
        current = path.read_text(encoding="utf-8")
    else:
        current = ""
    loaded = yaml.safe_load(current) if current.strip() else None
    if not loaded:
        current = "\n".join(ln for ln in current.splitlines() if ln.strip() != "[]")
        if not current.strip():
            current = FILE_HEADER
    stamp = datetime.now().strftime("%Y-%m-%d")
    block = f"\n# ── {source} 에서 AI가 뽑은 지원서 정보 ({stamp}) — 사실을 확인한 뒤 verified: true 로\n"
    block += "\n".join(item_yaml(i) for i in items)
    new = current.rstrip("\n") + "\n" + block
    check = yaml.safe_load(new)
    if not isinstance(check, list) or len(check) != len(loaded or []) + len(items):
        raise ValueError("덧붙인 결과가 yaml 목록으로 읽히지 않아 쓰지 않았습니다 — profile.yaml 형식을 확인하세요")
    path.write_text(new, encoding="utf-8")


def item_yaml(item: dict) -> str:
    def scalar(v) -> str:
        return yaml.safe_dump([v], allow_unicode=True, default_flow_style=True, width=10_000).strip()[1:-1]

    section = normalize_section(item.get("section")) or str(item.get("section"))
    lines = [f"- section: {section}"]
    for field in SECTIONS.get(section, ()):
        lines.append(f"  {field}: {scalar(str(item.get(field, '') or ''))}")
    lines.append(f"  verified: {'true' if item.get('verified') is True else 'false'}")
    if item.get("source"):
        lines.append(f"  source: {scalar(item['source'])}")
    return "\n".join(lines) + "\n"


def school_names(items: list[dict]) -> list[str]:
    """블라인드 채용 검사용 — 학교 이름과 줄임말(○○대학교 → ○○대, ○○고등학교 → ○○고)."""
    out: list[str] = []
    for item in items:
        if normalize_section(item.get("section")) != "학력":
            continue
        name = re.sub(r"\s+", "", str(item.get("school") or ""))
        if len(name) < 2:
            continue
        out.append(name)
        if name.endswith("대학교") and len(name) > 3:
            out.append(name[:-2])
        elif name.endswith("고등학교") and len(name) > 4:
            out.append(name[:-4] + "고")
    return list(dict.fromkeys(out))


def _pair(item: dict) -> tuple[str, str]:
    return normalize_section(item.get("section")) or "", norm_name(item_name(item))


def norm_name(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", str(text))).lower()


def _backup(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"profile_{stamp}.yaml"
    n = 1
    while dst.exists():
        dst = backup_dir / f"profile_{stamp}_{n}.yaml"
        n += 1
    shutil.copy2(path, dst)
    for old in sorted(backup_dir.glob("profile_*.yaml"))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
