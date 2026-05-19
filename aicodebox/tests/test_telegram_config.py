"""Telegram config loading + access control + chat-config merging."""

from __future__ import annotations

import pytest

from aicodebox.modes.telegram import config


def _write(path, yaml_text):
    path.write_text(yaml_text)
    return path


def test_load_missing_file_env_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AICODEBOX_TELEGRAM_CONFIG", str(tmp_path / "absent.yml"))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    cfg = config.load()
    assert cfg["allowed_chats"] == [-100123]
    assert cfg["default"] == {}
    assert cfg["chats"] == {}


def test_load_missing_file_no_env_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("AICODEBOX_TELEGRAM_CONFIG", str(tmp_path / "absent.yml"))
    cfg = config.load()
    assert cfg == {"allowed_chats": [], "default": {}, "chats": {}}


def test_load_yaml_full(monkeypatch, tmp_path):
    yml = _write(
        tmp_path / "tg.yml",
        """
allowed_chats: [1, 2]
default:
  model: glm-4.5-air
  workspace: shared
chats:
  1:
    workspace: alpha
    model: claude-sonnet
""",
    )
    monkeypatch.setenv("AICODEBOX_TELEGRAM_CONFIG", str(yml))
    cfg = config.load()
    assert cfg["allowed_chats"] == [1, 2]
    assert cfg["default"]["model"] == "glm-4.5-air"
    assert cfg["chats"][1]["workspace"] == "alpha"


def test_load_rejects_non_mapping_top(monkeypatch, tmp_path):
    yml = _write(tmp_path / "tg.yml", "- list_not_map")
    monkeypatch.setenv("AICODEBOX_TELEGRAM_CONFIG", str(yml))
    with pytest.raises(config.TelegramConfigError):
        config.load()


def test_load_rejects_bad_allowed_chats(monkeypatch, tmp_path):
    yml = _write(tmp_path / "tg.yml", "allowed_chats: not_a_list")
    monkeypatch.setenv("AICODEBOX_TELEGRAM_CONFIG", str(yml))
    with pytest.raises(config.TelegramConfigError):
        config.load()


def test_is_allowed_empty_allowed_chats_accepts_anyone():
    cfg = {"allowed_chats": [], "chats": {}}
    assert config.is_allowed(cfg, 999, 42) is True


def test_is_allowed_filters_by_chat():
    cfg = {"allowed_chats": [1, 2], "chats": {}}
    assert config.is_allowed(cfg, 1, 0)
    assert not config.is_allowed(cfg, 3, 0)


def test_is_allowed_filters_by_user_within_chat():
    cfg = {
        "allowed_chats": [1],
        "chats": {1: {"allowed_users": [10, 20]}},
    }
    assert config.is_allowed(cfg, 1, 10)
    assert not config.is_allowed(cfg, 1, 99)


def test_get_chat_config_merges_precedence():
    cfg = {
        "default": {"model": "default-m", "system_prompt": "global"},
        "chats": {1: {"model": "chat-m"}},
    }
    merged = config.get_chat_config(cfg, 1, overrides={"model": "override-m"})
    assert merged["model"] == "override-m"
    assert merged["system_prompt"] == "global"


def test_get_chat_config_auto_workspace_when_missing():
    cfg = {"default": {}, "chats": {}}
    merged = config.get_chat_config(cfg, -100123, overrides=None)
    assert merged["workspace"] == "chat_100123"


def test_get_chat_config_respects_explicit_workspace():
    cfg = {"default": {"workspace": "explicit"}, "chats": {}}
    merged = config.get_chat_config(cfg, 1)
    assert merged["workspace"] == "explicit"
