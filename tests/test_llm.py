import pytest

from llm import _extract_json


def test_direct_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_markdown_fenced_json():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_with_leading_prose():
    assert _extract_json('Sure, here you go:\n{"a": [1, 2]}') == {"a": [1, 2]}


def test_json_array():
    assert _extract_json('[1, 2, 3]') == [1, 2, 3]


def test_empty_response_raises():
    with pytest.raises(ValueError):
        _extract_json("   ")


def test_unparseable_response_raises():
    with pytest.raises(ValueError):
        _extract_json("not json at all")
