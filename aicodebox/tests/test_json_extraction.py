"""Tests for the JSON candidate-extraction logic in adapters.base.

Covers the new tolerant ``parse_json_response``: bare JSON, edge fences,
mid-prose ``` blocks, brace-balanced extraction from chatter, schema
validation interplay, and the "many candidates, last one validates"
case that exercises the schema-aware selection loop.
"""

from __future__ import annotations

from aicodebox.adapters.base import (
    _balanced_extract,
    _json_candidates,
    parse_json_response,
)


def test_parse_clean_json():
    value, err = parse_json_response('{"a": 1}')
    assert value == {"a": 1}
    assert err is None


def test_parse_strips_edge_fences():
    raw = '```json\n{"a": 1}\n```'
    value, err = parse_json_response(raw)
    assert value == {"a": 1}
    assert err is None


def test_parse_extracts_from_prose_with_fenced_block():
    raw = (
        "Sure! Here's the answer:\n\n"
        "```json\n"
        '{"a": 1, "b": [2, 3]}\n'
        "```\n\n"
        "Let me know if you need anything else."
    )
    value, err = parse_json_response(raw)
    assert value == {"a": 1, "b": [2, 3]}
    assert err is None


def test_parse_extracts_last_fenced_block_when_multiple():
    raw = (
        "First attempt:\n"
        "```json\n"
        '{"draft": true}\n'
        "```\n\n"
        "Final answer:\n"
        "```json\n"
        '{"final": true}\n'
        "```"
    )
    value, err = parse_json_response(raw)
    assert value == {"final": True}
    assert err is None


def test_parse_extracts_braces_from_chatter():
    raw = 'OK, the answer is {"a": 1, "b": 2} — see above.'
    value, err = parse_json_response(raw)
    assert value == {"a": 1, "b": 2}
    assert err is None


def test_parse_handles_braces_in_strings():
    raw = '{"text": "this has } in it and {also"}'
    value, err = parse_json_response(raw)
    assert value == {"text": "this has } in it and {also"}
    assert err is None


def test_parse_extracts_array_when_object_missing():
    raw = "Sure: [1, 2, 3]"
    value, err = parse_json_response(raw)
    assert value == [1, 2, 3]
    assert err is None


def test_parse_fails_on_total_garbage():
    raw = "I cannot do this task, sorry."
    value, err = parse_json_response(raw)
    assert value is None
    assert err is not None
    assert "not valid JSON" in err


def test_parse_schema_validation_success():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    value, err = parse_json_response('{"n": 42}', schema=schema)
    assert value == {"n": 42}
    assert err is None


def test_parse_schema_validation_failure():
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    value, err = parse_json_response('{"n": "not an int"}', schema=schema)
    assert value is None
    assert err is not None
    assert "does not match schema" in err


def test_parse_prefers_schema_valid_candidate():
    """When multiple candidates parse, the one that ALSO schema-validates
    wins — even if it appears later in the extraction order."""
    raw = (
        "First thought: {\"n\": \"wrong type\"}\n\n"
        "Actually, the answer is:\n"
        "```json\n"
        '{"n": 7}\n'
        "```"
    )
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    value, err = parse_json_response(raw, schema=schema)
    assert value == {"n": 7}
    assert err is None


def test_candidates_dedupe():
    """Identical candidates from different extraction strategies should
    not appear twice."""
    raw = '{"a": 1}'
    cands = _json_candidates(raw)
    assert cands.count('{"a": 1}') == 1


def test_balanced_extract_handles_escapes():
    raw = r'noise {"escaped": "quote \" here", "n": 1} trailing'
    extracted = _balanced_extract(raw, "{", "}")
    assert extracted == r'{"escaped": "quote \" here", "n": 1}'


def test_balanced_extract_nested():
    raw = 'before {"outer": {"inner": [1, 2]}} after'
    extracted = _balanced_extract(raw, "{", "}")
    assert extracted == '{"outer": {"inner": [1, 2]}}'


def test_balanced_extract_no_match():
    assert _balanced_extract("nothing here", "{", "}") is None
    assert _balanced_extract("unterminated {open", "{", "}") is None
