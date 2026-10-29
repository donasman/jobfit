"""HTML → 보이는 글자. 태그·스크립트를 걷어내고 글자는 바꾸지 않는다.

채용 사이트 실측으로 다듬어진 규칙(사람인·잡코리아·토스 채용 페이지):
- li는 "- " 글머리로, 표 칸은 " | "로, 제목 태그는 "#"으로 남겨 구조를 글자로 보존한다.
- 그 밖의 블록 태그는 줄바꿈이 된다.
"""

from __future__ import annotations

import html as _html
import re

_RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
_RE_DROP_BLOCK = re.compile(
    r"<(script|style|noscript|template|svg|head|button|select|iframe)\b.*?</\1\s*>",
    re.S | re.I,
)
_RE_BR = re.compile(r"<br\b[^>]*>", re.I)
_RE_LI = re.compile(r"<li\b[^>]*>", re.I)
_RE_H = re.compile(r"<h([1-6])\b[^>]*>", re.I)
_RE_BLOCK = re.compile(
    r"</?(p|div|tr|section|article|dt|dd|dl|table|ul|ol|h[1-6]|header|footer|nav|main|aside|form|figure|blockquote|pre|tbody|thead)\b[^>]*>",
    re.I,
)
_RE_CELL = re.compile(r"<t[dh]\b[^>]*>", re.I)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_SPACES = re.compile(r"[ \t\r\f\v 　]+")  # 전각 공백은 들여쓰기로 쓰인다
_RE_LINE_EDGES = re.compile(r" ?\n ?")
_RE_EMPTY_BULLETS = re.compile(r"\n(?:[-|] ?\n)+")
_RE_MANY_NL = re.compile(r"\n{3,}")
_RE_DOUBLE_BULLET = re.compile(r"^- [ㆍ•·▪-] ?", re.M)  # <li>ㆍ글</li>·<li>- 글</li> → "- ㆍ글"·"- - 글" 을 "- 글" 로


def html_to_text(html: str) -> str:
    s = _RE_COMMENT.sub("", html)
    s = _RE_DROP_BLOCK.sub("", s)
    s = _RE_BR.sub("\n", s)
    s = _RE_LI.sub("\n- ", s)
    s = _RE_H.sub(lambda m: "\n" + "#" * int(m.group(1)) + " ", s)
    s = _RE_BLOCK.sub("\n", s)
    s = _RE_CELL.sub(" | ", s)
    s = _RE_TAG.sub("", s)
    s = _html.unescape(s)
    s = _RE_SPACES.sub(" ", s)
    s = _RE_LINE_EDGES.sub("\n", s)
    s = _RE_EMPTY_BULLETS.sub("\n", s)
    s = _RE_MANY_NL.sub("\n\n", s)
    s = _RE_DOUBLE_BULLET.sub("- ", s)
    return s.strip()
