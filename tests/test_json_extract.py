import pytest

from modules.json_extract import clean_json_from_llm


def test_clean_json_strips_markdown_fence():
    raw = '```json\n{"chapters": []}\n```'
    assert clean_json_from_llm(raw) == '{"chapters": []}'


def test_clean_json_extracts_outer_braces():
    raw = 'Preamble {"a": 1, "b": 2} trailing'
    assert clean_json_from_llm(raw) == '{"a": 1, "b": 2}'


def test_clean_json_no_braces_returns_stripped():
    raw = "no json here"
    assert clean_json_from_llm(raw) == "no json here"
