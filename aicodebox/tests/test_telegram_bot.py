"""Unit tests for bot helpers — workspace resolution, file extraction,
choice menus, reply-context builders."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aicodebox.modes.telegram import bot

# ── workspace resolution ──────────────────────────────────────────────────────


def test_resolve_workspace_returns_root_for_dot(monkeypatch, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    assert bot.resolve_workspace({"workspace": "."}) == str(tmp_workspace.resolve())


def test_resolve_workspace_creates_subdir(monkeypatch, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    out = bot.resolve_workspace({"workspace": "chat_42"})
    assert out == str((tmp_workspace / "chat_42").resolve())
    assert (tmp_workspace / "chat_42").is_dir()


def test_resolve_workspace_rejects_escape(monkeypatch, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    with pytest.raises(ValueError):
        bot.resolve_workspace({"workspace": "../../etc"})


def test_resolve_workspace_strips_leading_slash(monkeypatch, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    out = bot.resolve_workspace({"workspace": "/abs"})
    assert out == str((tmp_workspace / "abs").resolve())


# ── SEND_FILE tag extraction ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_and_send_files_removes_tags(monkeypatch, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    (tmp_workspace / "out.txt").write_text("hi")
    bot_obj = MagicMock()
    bot_obj.send_message = AsyncMock()
    bot_obj.send_document = AsyncMock()
    bot_obj.send_photo = AsyncMock()
    bot_obj.send_video = AsyncMock()
    text = "before [SEND_FILE: out.txt] after"
    remaining = await bot._extract_and_send_files(
        bot_obj,
        1,
        text,
        str(tmp_workspace),
    )
    assert "SEND_FILE" not in remaining
    assert "before" in remaining and "after" in remaining
    bot_obj.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_and_send_files_refuses_escape(monkeypatch, tmp_path, tmp_workspace):
    monkeypatch.setattr(bot, "ROOT_WORKSPACE", str(tmp_workspace))
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    bot_obj = MagicMock()
    bot_obj.send_message = AsyncMock()
    bot_obj.send_document = AsyncMock()
    text = f"[SEND_FILE: ../../{outside.name}]"
    remaining = await bot._extract_and_send_files(
        bot_obj,
        1,
        text,
        str(tmp_workspace),
    )
    bot_obj.send_document.assert_not_awaited()
    bot_obj.send_photo.assert_not_called()
    bot_obj.send_video.assert_not_called()
    assert "SEND_FILE" not in remaining


@pytest.mark.asyncio
async def test_send_file_refuses_oversize(monkeypatch, tmp_workspace):
    big = tmp_workspace / "big.bin"
    big.write_bytes(b"x")
    monkeypatch.setattr(bot, "MAX_FILE_BYTES", 0)
    bot_obj = MagicMock()
    bot_obj.send_message = AsyncMock()
    bot_obj.send_document = AsyncMock()
    await bot._send_file(bot_obj, 1, str(big))
    bot_obj.send_document.assert_not_awaited()
    bot_obj.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_file_refuses_missing(monkeypatch, tmp_workspace):
    bot_obj = MagicMock()
    bot_obj.send_message = AsyncMock()
    bot_obj.send_document = AsyncMock()
    await bot._send_file(bot_obj, 1, str(tmp_workspace / "nope.txt"))
    bot_obj.send_message.assert_awaited_once()
    bot_obj.send_document.assert_not_awaited()


# ── available_models / available_efforts plumbing ─────────────────────────────


def test_available_models_uses_env(monkeypatch, stub_adapter):
    monkeypatch.setenv("AICODEBOX_AVAILABLE_MODELS", "a, b ,c")
    assert bot._available_models() == ["a", "b", "c"]


def test_available_models_falls_back_to_adapter(stub_adapter):
    assert bot._available_models() == ["stub-a", "stub-b"]


def test_available_efforts_falls_back_to_adapter(stub_adapter):
    assert bot._available_efforts() == ["off", "low", "high"]


def test_available_models_handles_unconfigured_adapter(monkeypatch):
    from aicodebox.adapters import base as adapter_base

    monkeypatch.delenv("AICODEBOX_ADAPTER", raising=False)
    adapter_base.reset_adapter_cache()
    assert bot._available_models() == []
    assert bot._available_efforts() == []


# ── merged config ─────────────────────────────────────────────────────────────


def test_merged_chat_config_pulls_overrides(monkeypatch):
    monkeypatch.setattr(
        bot,
        "config",
        {
            "default": {"model": "default-m"},
            "chats": {},
        },
    )
    monkeypatch.setattr(bot, "chat_overrides", {1: {"model": "ov-m"}})
    cfg = bot._merged_chat_config(1)
    assert cfg["model"] == "ov-m"


# ── reply prompt builders ─────────────────────────────────────────────────────


def test_build_cron_reply_prompt_includes_history_dir():
    entry = {
        "job_name": "ping",
        "fired_at": "2026-01-01T00:00:00",
        "instruction": "say hi",
        "result": "hello",
        "history_dir": "/var/run/cron/abc",
    }
    out = bot._build_cron_reply_prompt(entry, "follow up?")
    assert "/var/run/cron/abc" in out
    assert "ping" in out
    assert "follow up?" in out


def test_build_cron_reply_prompt_without_history_dir():
    entry = {
        "job_name": "ping",
        "fired_at": "2026-01-01T00:00:00",
        "instruction": "i",
        "result": "r",
    }
    out = bot._build_cron_reply_prompt(entry, "q")
    assert "Full run history" not in out
    assert "ping" in out


def test_describe_reply_kind_photo():
    replied = MagicMock()
    replied.photo = ["p"]
    replied.video = None
    replied.document = None
    replied.sticker = None
    replied.voice = None
    replied.audio = None
    replied.animation = None
    replied.text = None
    replied.caption = None
    assert "photo" in bot._describe_reply_kind(replied)


def test_describe_reply_kind_text_only():
    replied = MagicMock()
    for attr in ("photo", "video", "document", "sticker", "voice", "audio", "animation"):
        setattr(replied, attr, None)
    replied.text = "hello"
    replied.caption = None
    assert bot._describe_reply_kind(replied) == "text"


def test_build_generic_reply_prompt_marks_bot_author():
    replied = MagicMock()
    replied.message_id = 999
    replied.from_user.is_bot = True
    replied.text = "previous bot output"
    replied.caption = None
    for attr in ("photo", "video", "document", "sticker", "voice", "audio", "animation"):
        setattr(replied, attr, None)
    out = bot._build_generic_reply_prompt(replied, "what did you mean?")
    assert "the bot (you)" in out
    assert "999" in out
    assert "what did you mean?" in out


# ── _build_append_system_prompt always includes TG hint ───────────────────────


def test_append_system_prompt_includes_telegram_hint():
    out = bot._build_append_system_prompt({})
    assert "[SEND_FILE:" in out


def test_append_system_prompt_concatenates_user_text():
    out = bot._build_append_system_prompt({"append_system_prompt": "be terse"})
    assert "[SEND_FILE:" in out
    assert "be terse" in out
