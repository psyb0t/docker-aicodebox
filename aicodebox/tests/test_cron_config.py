"""Cron config parser."""

from __future__ import annotations

import pytest

from aicodebox.modes.cron import config


def _write(tmp_path, body):
    p = tmp_path / "cron.yml"
    p.write_text(body)
    return p


def test_load_minimal(tmp_path):
    p = _write(
        tmp_path,
        """
jobs:
  - name: ping
    schedule: "*/30 * * * * *"
    instruction: say hi
""",
    )
    cfg = config.load(p)
    assert len(cfg.jobs) == 1
    assert cfg.jobs[0].name == "ping"
    assert cfg.jobs[0].instruction == "say hi"


def test_load_inherits_defaults(tmp_path):
    p = _write(
        tmp_path,
        """
model: global-m
telegram_chat_id: 100
jobs:
  - name: a
    schedule: "*/1 * * * * *"
    instruction: a
  - name: b
    schedule: "*/2 * * * * *"
    instruction: b
    model: override-m
""",
    )
    cfg = config.load(p)
    a, b = cfg.jobs
    assert a.model == "global-m"
    assert b.model == "override-m"
    assert a.telegram_chat_id == 100
    assert b.telegram_chat_id == 100


def test_load_rejects_duplicate_name(tmp_path):
    p = _write(
        tmp_path,
        """
jobs:
  - name: ping
    schedule: "*/1 * * * * *"
    instruction: a
  - name: ping
    schedule: "*/1 * * * * *"
    instruction: b
""",
    )
    with pytest.raises(config.CronConfigError, match="duplicate"):
        config.load(p)


def test_load_rejects_invalid_schedule(tmp_path):
    p = _write(
        tmp_path,
        """
jobs:
  - name: bad
    schedule: not a cron
    instruction: hi
""",
    )
    with pytest.raises(config.CronConfigError, match="invalid cron"):
        config.load(p)


def test_load_rejects_bad_name_chars(tmp_path):
    p = _write(
        tmp_path,
        """
jobs:
  - name: "bad name with spaces"
    schedule: "*/1 * * * * *"
    instruction: hi
""",
    )
    with pytest.raises(config.CronConfigError, match="name must match"):
        config.load(p)


def test_load_rejects_empty_instruction(tmp_path):
    p = _write(
        tmp_path,
        """
jobs:
  - name: ping
    schedule: "*/1 * * * * *"
    instruction: "   "
""",
    )
    with pytest.raises(config.CronConfigError, match="instruction"):
        config.load(p)


def test_load_rejects_missing_jobs(tmp_path):
    p = _write(tmp_path, "model: x")
    with pytest.raises(config.CronConfigError):
        config.load(p)


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(config.CronConfigError, match="not found"):
        config.load(tmp_path / "nope.yml")


def test_load_rejects_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text(":\n  - [unclosed")
    with pytest.raises(config.CronConfigError):
        config.load(p)


def test_load_int_or_none_string_chat_id(tmp_path):
    p = _write(
        tmp_path,
        """
telegram_chat_id: "-100123"
jobs:
  - name: ping
    schedule: "*/1 * * * * *"
    instruction: hi
""",
    )
    cfg = config.load(p)
    assert cfg.jobs[0].telegram_chat_id == -100123


def test_load_rejects_non_int_chat_id(tmp_path):
    p = _write(
        tmp_path,
        """
telegram_chat_id: bogus
jobs:
  - name: ping
    schedule: "*/1 * * * * *"
    instruction: hi
""",
    )
    with pytest.raises(config.CronConfigError):
        config.load(p)
