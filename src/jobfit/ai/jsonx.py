"""AI 답에서 JSON 꺼내기 — 웹 채팅 답에는 앞뒤 설명, ```json 표시, 끝 쉼표가 섞여 온다."""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n?(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_MAX_SPANS = 8


def extract_json(text: str) -> dict:
    """답 안의 JSON 객체 하나. 못 찾으면 ValueError(사용자에게 보일 문장)."""
    text = (text or "").lstrip("﻿").strip()
    if not text:
        raise ValueError("답이 비어 있습니다")
    candidates = [text]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(text)]
    candidates += _brace_spans(text)
    for c in candidates:
        for variant in (c, _TRAILING_COMMA.sub(r"\1", c)):
            try:
                value = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    if "{" not in text:
        raise ValueError("답에 JSON({ … })이 없습니다. 답 전체를 복사했는지 확인하세요")
    raise ValueError("답의 JSON 형식이 깨져 있습니다(괄호·따옴표가 맞지 않음). 답이 중간에 잘렸는지 확인하세요")


def _brace_spans(text: str) -> list[str]:
    """맨 바깥 { … } 덩어리들. 문자열 안의 괄호는 센다에서 뺀다."""
    spans: list[str] = []
    i, n = 0, len(text)
    while i < n and len(spans) < _MAX_SPANS:
        start = text.find("{", i)
        if start < 0:
            break
        depth, in_str, esc = 0, False, False
        end = -1
        for j in range(start, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            spans.append(text[start:])  # 닫히지 않음 — 끝 쉼표만 문제일 수도 있어 한 번은 시도
            break
        spans.append(text[start : end + 1])
        i = end + 1
    return spans
