import pytest

from jobfit.ai.jsonx import extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_with_prose_around():
    text = '네, 분석했습니다.\n```json\n{"role_summary": "백엔드", "must": ["Java"]}\n```\n도움이 되길 바랍니다.'
    assert extract_json(text) == {"role_summary": "백엔드", "must": ["Java"]}


def test_braces_without_fence_and_braces_inside_strings():
    text = '분석 결과입니다: {"role_summary": "API {설계}와 개발", "must": []} 끝.'
    assert extract_json(text)["role_summary"] == "API {설계}와 개발"


def test_trailing_commas_are_tolerated():
    assert extract_json('{"must": ["Java", "Spring",], "nice": [],}') == {"must": ["Java", "Spring"], "nice": []}


def test_bom_and_whitespace():
    assert extract_json('﻿  {"a": "b"}  ') == {"a": "b"}


@pytest.mark.parametrize(
    ("text", "message"),
    [("", "비어"), ("JSON은 못 드리겠어요", "JSON"), ('{"role_summary": "잘린 답', "깨져")],
)
def test_failures_have_reasons(text, message):
    with pytest.raises(ValueError, match=message):
        extract_json(text)
