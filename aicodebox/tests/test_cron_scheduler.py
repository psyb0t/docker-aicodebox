"""Cron scheduler helpers — slug, expand, workspace, history hint, telegram."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from aicodebox.modes.cron import scheduler
from aicodebox.modes.cron.config import CronJob


def test_slugify_replaces_punctuation():
    assert scheduler._slugify("/workspace/foo bar") == "workspace_foo_bar"


def test_slugify_empty_fallback():
    assert scheduler._slugify("") == "workspace"
    assert scheduler._slugify("/////") == "workspace"


def test_expand_substitutes_placeholders():
    fired = datetime(2026, 1, 2, 3, 4, 5)
    out = scheduler._expand(
        "now={system_datetime} job={job_name}",
        fired,
        "ping",
    )
    assert out == "now=2026-01-02 03:04:05 UTC job=ping"


def test_expand_passes_through_none():
    assert scheduler._expand(None, datetime.now(), "x") is None


def test_resolve_workspace_creates_dir(tmp_workspace):
    out = scheduler._resolve_workspace("alpha")
    assert Path(out) == (tmp_workspace / "alpha").resolve()
    assert (tmp_workspace / "alpha").is_dir()


def test_resolve_workspace_rejects_absolute(tmp_workspace):
    with pytest.raises(ValueError):
        scheduler._resolve_workspace("/etc")


def test_resolve_workspace_rejects_dot_dot(tmp_workspace):
    with pytest.raises(ValueError):
        scheduler._resolve_workspace("../escape")


def test_resolve_workspace_returns_root_for_dot(tmp_workspace):
    out = scheduler._resolve_workspace(".")
    assert Path(out) == tmp_workspace


def test_previous_run_dir_returns_none_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "h")
    cur = tmp_path / "h" / "ws" / "20260101-000000-ping"
    assert scheduler._previous_run_dir("ws", "ping", cur) is None


def test_previous_run_dir_finds_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "h")
    parent = tmp_path / "h" / "ws"
    parent.mkdir(parents=True)
    older = parent / "20260101-000000-ping"
    newer = parent / "20260102-000000-ping"
    different = parent / "20260102-000000-other"
    for d in (older, newer, different):
        d.mkdir()
    cur = parent / "20260103-000000-ping"
    cur.mkdir()
    assert scheduler._previous_run_dir("ws", "ping", cur) == newer


def test_build_history_hint_returns_none_first_run(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "h")
    cur = tmp_path / "h" / "ws" / "20260101-000000-ping"
    assert scheduler._build_history_hint("ws", "ping", cur) is None


def test_build_history_hint_includes_prior_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "h")
    parent = tmp_path / "h" / "ws"
    parent.mkdir(parents=True)
    prev = parent / "20260101-000000-ping"
    prev.mkdir()
    cur = parent / "20260102-000000-ping"
    hint = scheduler._build_history_hint("ws", "ping", cur)
    assert hint is not None
    assert str(prev) in hint
    assert "ping" in hint


def test_record_summary_appends_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path)
    job = CronJob(
        name="ping",
        schedule="*/1 * * * * *",
        instruction="i",
    )
    fired = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = scheduler._record_summary(job, fired, {"exit_code": 0, "text": "ok"})
    assert path == tmp_path / "ping.jsonl"
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["job"] == "ping"
    assert entry["exit_code"] == 0


def test_record_telegram_message_caps_history(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path)
    monkeypatch.setattr(scheduler, "TELEGRAM_MESSAGES_FILE", tmp_path / "tm.json")
    monkeypatch.setattr(scheduler, "TELEGRAM_MESSAGES_CAP", 3)
    for i in range(5):
        scheduler._record_telegram_message(i, {"job_name": f"j{i}"})
    data = json.loads((tmp_path / "tm.json").read_text())
    # only the most recent 3 should survive
    assert sorted(data.keys()) == ["2", "3", "4"]


def test_record_telegram_message_atomic(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path)
    monkeypatch.setattr(scheduler, "TELEGRAM_MESSAGES_FILE", tmp_path / "tm.json")
    scheduler._record_telegram_message(1, {"job_name": "x"})
    assert (tmp_path / "tm.json").is_file()
    assert not (tmp_path / "tm.json.tmp").exists()


def test_run_job_writes_full_artefacts(monkeypatch, tmp_path, tmp_workspace):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path / "cron")
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "cron" / "history")

    job = CronJob(
        name="ping",
        schedule="*/1 * * * * *",
        instruction="say hi",
    )
    fired = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)

    fake_result = type(
        "FakeResult",
        (),
        {"text": "agent says hi", "raw_stdout": "RAW", "raw_stderr": "ERR", "exit_code": 0},
    )()

    with patch.object(scheduler, "run_agent", return_value=fake_result) as run:
        scheduler._run_job(job, fired)
        run.assert_called_once()

    runs = list((tmp_path / "cron" / "history").rglob("*-ping"))
    assert len(runs) == 1
    job_dir = runs[0]
    assert (job_dir / "stdout.log").read_text() == "RAW"
    assert (job_dir / "stderr.log").read_text() == "ERR"
    assert (job_dir / "result.txt").read_text() == "agent says hi"
    meta = json.loads((job_dir / "meta.json").read_text())
    assert meta["exit_code"] == 0
    assert meta["error"] is None
    assert meta["name"] == "ping"

    # back-compat summary
    summary = (tmp_path / "cron" / "ping.jsonl").read_text().strip()
    entry = json.loads(summary)
    assert entry["history_dir"] == str(job_dir)
    assert entry["exit_code"] == 0


def test_run_job_records_workspace_error(monkeypatch, tmp_path, tmp_workspace):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path / "cron")
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "cron" / "history")
    job = CronJob(
        name="bad",
        schedule="*/1 * * * * *",
        instruction="i",
        workspace="../escape",
    )
    fired = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    scheduler._run_job(job, fired)
    summary = (tmp_path / "cron" / "bad.jsonl").read_text().strip()
    entry = json.loads(summary)
    assert entry["exit_code"] == -1
    assert "escape" in entry["error"]


def test_run_job_history_hint_injected_into_append_system_prompt(
    monkeypatch,
    tmp_path,
    tmp_workspace,
):
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path / "cron")
    monkeypatch.setattr(scheduler, "HISTORY_RUNS_ROOT", tmp_path / "cron" / "history")
    workspace_slug = scheduler._slugify(str(tmp_workspace.resolve()))
    parent = tmp_path / "cron" / "history" / workspace_slug
    parent.mkdir(parents=True)
    (parent / "20260101-000000-ping").mkdir()

    captured: dict = {}

    def fake_run(spec):
        captured["append"] = spec.append_system_prompt
        return type(
            "R",
            (),
            {"text": "ok", "raw_stdout": "", "raw_stderr": "", "exit_code": 0},
        )()

    monkeypatch.setattr(scheduler, "run_agent", fake_run)
    job = CronJob(name="ping", schedule="*/1 * * * * *", instruction="i")
    fired = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    scheduler._run_job(job, fired)
    assert "Prior runs" in (captured["append"] or "")


def test_notify_telegram_no_token_skips(monkeypatch, tmp_path):
    monkeypatch.delenv("AICODEBOX_TELEGRAM_BOT_TOKEN", raising=False)
    job = CronJob(
        name="ping",
        schedule="*/1 * * * * *",
        instruction="i",
        telegram_chat_id=42,
    )
    called: list = []
    monkeypatch.setattr(scheduler, "_post_to_telegram", lambda *a, **k: called.append(a) or 99)
    scheduler._notify_telegram(job, datetime.now(timezone.utc), "hi", 0, tmp_path)
    assert called == []


def test_notify_telegram_records_history_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AICODEBOX_TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setattr(scheduler, "HISTORY_ROOT", tmp_path)
    monkeypatch.setattr(scheduler, "TELEGRAM_MESSAGES_FILE", tmp_path / "tm.json")
    monkeypatch.setattr(scheduler, "_post_to_telegram", lambda *a, **k: 7777)

    job_dir = tmp_path / "history" / "ws" / "20260101-000000-ping"
    job_dir.mkdir(parents=True)
    job = CronJob(
        name="ping",
        schedule="*/1 * * * * *",
        instruction="i",
        telegram_chat_id=42,
    )
    scheduler._notify_telegram(
        job,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        "hello",
        0,
        job_dir,
    )
    tg_meta = json.loads((job_dir / "telegram.json").read_text())
    assert tg_meta["chat_id"] == 42
    assert tg_meta["message_id"] == 7777
    inbox = json.loads((tmp_path / "tm.json").read_text())
    assert inbox["7777"]["history_dir"] == str(job_dir)
    assert inbox["7777"]["chat_id"] == 42
