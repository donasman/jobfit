"""여러 형식의 파일에서 글자 꺼내기 — 경험 카드 자료(sources/)와 자소서 샘플(me/samples/)이 같이 쓴다. AI 없음.

읽는 형식: txt · md · csv · docx · hwpx · pptx · html · xlsx · pdf(글자가 들어 있는 것)
못 읽는 것: 이미지, 스캔한 PDF(글자 층이 거의 없음), hwp(옛 한글 형식), 그 밖 — `read_any`가 None을 돌려준다.
zip 형식(docx·hwpx·pptx)은 XML 파서 없이 문단·글자 태그만 정규식으로 꺼낸다(엔티티를 풀지 않는다).
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jobfit.clean.html2text import html_to_text

TEXT_EXTS = (".txt", ".md", ".csv")
READABLE_EXTS = (*TEXT_EXTS, ".docx", ".hwpx", ".pptx", ".html", ".htm", ".xlsx", ".pdf")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
UNSUPPORTED_EXTS = (".hwp", ".doc", ".ppt", ".xls")  # 옛 바이너리 형식 — 새 형식(hwpx·docx·pptx·xlsx)이나 PDF로 바꿔야 한다
MAX_XML_BYTES = 20_000_000  # 압축 폭탄 방지
PDF_MIN_CHARS_PER_PAGE = 40  # 이보다 적으면 스캔한 PDF로 본다


@dataclass
class Extracted:
    text: str | None  # None이면 스크립트가 읽지 못함
    kind: str  # text · pdf · image · scanned-pdf · unsupported · empty
    note: str = ""


def read_any(path: Path) -> Extracted:
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXTS:
            return _ok(path.read_text(encoding="utf-8", errors="replace"))
        if ext == ".docx":
            return _ok(zip_text(path, ("word/document.xml",)))
        if ext == ".hwpx":
            return _ok(zip_text(path, prefix="Contents/section"))
        if ext == ".pptx":
            return _ok(zip_text(path, prefix="ppt/slides/slide", numeric=True))
        if ext in (".html", ".htm"):
            return _ok(html_to_text(path.read_text(encoding="utf-8", errors="replace")))
        if ext == ".xlsx":
            return _ok(_xlsx_text(path))
        if ext == ".pdf":
            return _pdf(path)
    except Exception as e:  # 손상·암호 파일 등 — 형식 라이브러리마다 예외가 달라 모두 받는다
        return Extracted(None, "unsupported", f"읽다가 실패했습니다({e.__class__.__name__}) — 손상되었거나 암호가 걸린 파일인지 확인하세요")
    if ext in IMAGE_EXTS:
        return Extracted(None, "image", "그림 파일이라 스크립트가 글자를 읽지 못합니다")
    if ext in UNSUPPORTED_EXTS:
        return Extracted(None, "unsupported", f"{ext} 는 옛 형식이라 읽지 않습니다 — 새 형식(hwpx·docx·pptx·xlsx)이나 PDF로 저장해 주세요")
    return Extracted(None, "unsupported", f"{ext or '확장자 없음'} 형식은 읽지 않습니다")


def zip_text(path: Path, names: tuple[str, ...] = (), prefix: str = "", para_tag: str = "p", text_tag: str = "t", numeric: bool = False) -> str:
    """zip 안 XML에서 문단마다 글자를 이어 붙인다."""
    para_end = re.compile(rf"</(?:\w+:)?{para_tag}>")
    text_re = re.compile(rf"<(?:\w+:)?{text_tag}(?:\s[^>]*)?>(.*?)</(?:\w+:)?{text_tag}>", re.S)
    with zipfile.ZipFile(path) as z:
        if names:
            members = [n for n in names if n in z.namelist()]
        else:
            members = [n for n in z.namelist() if n.startswith(prefix) and n.endswith(".xml")]
            key = (lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[-1]) or 0)) if numeric else (lambda n: n)
            members.sort(key=key)
        paras: list[str] = []
        for name in members:
            if z.getinfo(name).file_size > MAX_XML_BYTES:
                continue
            xml = z.read(name).decode("utf-8", errors="replace")
            for chunk in para_end.split(xml):
                text = "".join(text_re.findall(chunk))
                if text or paras:
                    paras.append(html.unescape(re.sub(r"<[^>]+>", "", text)))
        return "\n".join(paras).strip()


def _xlsx_text(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c not in (None, "")]
            if cells:
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def _pdf(path: Path) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        return Extracted(None, "unsupported", "암호가 걸린 PDF입니다 — 암호를 푼 PDF로 다시 저장해 주세요")
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    text = "\n\n".join(p for p in pages if p)
    if len(text) < PDF_MIN_CHARS_PER_PAGE * max(len(pages), 1):
        return Extracted(None, "scanned-pdf", "PDF에 글자 층이 거의 없습니다(스캔·그림 PDF)")
    return Extracted(text, "pdf")


def _ok(text: str) -> Extracted:
    text = text.strip()
    return Extracted(text, "text") if text else Extracted(None, "empty", "글자가 없습니다")
