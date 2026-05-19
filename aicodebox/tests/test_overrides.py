"""Persistent override store: atomic writes + apply_choice semantics."""

from __future__ import annotations

import json

import pytest

from aicodebox.modes.telegram import overrides


def test_load_returns_empty_when_missing(tmp_path):
    assert overrides.load(tmp_path / "missing.json") == {}


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "ov.json"
    state = {123: {"model": "a"}, 456: {"effort": "high"}}
    overrides.save(state, path=p)
    assert overrides.load(p) == state


def test_save_is_atomic(tmp_path):
    p = tmp_path / "ov.json"
    overrides.save({1: {"x": "y"}}, path=p)
    assert p.exists()
    # No leftover .tmp file
    assert not (tmp_path / "ov.json.tmp").exists()


def test_load_ignores_non_dict_file(tmp_path):
    p = tmp_path / "ov.json"
    p.write_text(json.dumps(["nope"]))
    assert overrides.load(p) == {}


def test_load_ignores_invalid_json(tmp_path):
    p = tmp_path / "ov.json"
    p.write_text("not json")
    assert overrides.load(p) == {}


def test_set_value_persists(tmp_path):
    p = tmp_path / "ov.json"
    state: dict[int, dict] = {}
    overrides.set_value(state, 42, "model", "alpha", path=p)
    assert state == {42: {"model": "alpha"}}
    assert json.loads(p.read_text()) == {"42": {"model": "alpha"}}


def test_clear_value_removes_empty_bucket(tmp_path):
    p = tmp_path / "ov.json"
    state = {42: {"model": "alpha"}}
    overrides.save(state, path=p)
    overrides.clear_value(state, 42, "model", path=p)
    assert state == {}
    assert json.loads(p.read_text()) == {}


def test_clear_value_keeps_other_keys(tmp_path):
    p = tmp_path / "ov.json"
    state = {42: {"model": "alpha", "effort": "high"}}
    overrides.save(state, path=p)
    overrides.clear_value(state, 42, "model", path=p)
    assert state == {42: {"effort": "high"}}


def test_apply_choice_with_reset_token_clears(tmp_path):
    p = tmp_path / "ov.json"
    state = {1: {"model": "a"}}
    overrides.save(state, path=p)
    overrides.apply_choice(state, 1, "model", "reset", ["a", "b"], path=p)
    assert state == {}


def test_apply_choice_rejects_unknown(tmp_path):
    p = tmp_path / "ov.json"
    state: dict[int, dict] = {}
    with pytest.raises(ValueError):
        overrides.apply_choice(state, 1, "model", "nope", ["a", "b"], path=p)
    assert state == {}


def test_apply_choice_empty_allowlist_accepts_anything(tmp_path):
    p = tmp_path / "ov.json"
    state: dict[int, dict] = {}
    overrides.apply_choice(state, 1, "system_prompt", "anything goes", [], path=p)
    assert state == {1: {"system_prompt": "anything goes"}}


def test_get_chat_overrides_returns_copy():
    state = {1: {"k": "v"}}
    got = overrides.get_chat_overrides(state, 1)
    got["k"] = "mutated"
    assert state[1]["k"] == "v"
