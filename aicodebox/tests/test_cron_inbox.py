"""telegram bot's cron-message inbox loader."""

from __future__ import annotations

import json

from aicodebox.modes.telegram import cron_inbox


def test_load_cron_message_missing(tmp_path):
    assert cron_inbox.load_cron_message(1, path=tmp_path / "absent.json") is None


def test_load_cron_message_returns_entry(tmp_path):
    p = tmp_path / "tm.json"
    p.write_text(json.dumps({"42": {"job_name": "x"}}))
    assert cron_inbox.load_cron_message(42, path=p) == {"job_name": "x"}


def test_load_cron_message_int_key_string_value(tmp_path):
    p = tmp_path / "tm.json"
    p.write_text(json.dumps({"7": {"job_name": "ping"}}))
    assert cron_inbox.load_cron_message(7, path=p) is not None


def test_load_cron_message_ignores_garbage(tmp_path):
    p = tmp_path / "tm.json"
    p.write_text("not json")
    assert cron_inbox.load_cron_message(1, path=p) is None


def test_load_cron_message_honours_env(monkeypatch, tmp_path):
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "telegram_messages.json").write_text(json.dumps({"100": {"job_name": "rota"}}))
    monkeypatch.setenv("AICODEBOX_CRON_MODE_HISTORY_DIR", str(cron_dir))
    assert cron_inbox.load_cron_message(100) == {"job_name": "rota"}
