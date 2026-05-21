"""Parse ~/.aicodebox/telegram.yml (or env-only fallback).

YAML schema:
    allowed_chats: [123456789]
    default:
      model: glm-4.5-air
      continue: true
      workspace: e2e
      system_prompt: ...
      append_system_prompt: ...
    chats:
      123456789:
        workspace: my-project
        model: ...
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("telegram.config")


class TelegramConfigError(ValueError):
    pass


DEFAULT_CONFIG_PATH = Path(os.environ.get("HOME", "/home/aicode")) / ".aicodebox" / "telegram.yml"


def _config_path() -> Path:
    p = os.environ.get("AICODEBOX_TELEGRAM_MODE_CONFIG")
    return Path(p) if p else DEFAULT_CONFIG_PATH


def load() -> dict[str, Any]:
    """Load the telegram config. If the file is absent, build a minimal config
    from env vars (TELEGRAM_CHAT_ID becomes the only allowed chat)."""
    path = _config_path()
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise TelegramConfigError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise TelegramConfigError(f"{path}: top level must be a mapping")
        allowed = raw.get("allowed_chats", []) or []
        if not isinstance(allowed, list):
            raise TelegramConfigError("'allowed_chats' must be a list")
        chats_raw = raw.get("chats", {}) or {}
        if not isinstance(chats_raw, dict):
            raise TelegramConfigError("'chats' must be a mapping")
        chats = {int(k): (v or {}) for k, v in chats_raw.items()}
        default = raw.get("default", {}) or {}
        if not isinstance(default, dict):
            raise TelegramConfigError("'default' must be a mapping")
        return {
            "allowed_chats": [int(x) for x in allowed],
            "default": default,
            "chats": chats,
        }

    # env-only fallback — useful for tests and simple deployments
    env_chat = os.environ.get("TELEGRAM_CHAT_ID")
    allowed = [int(env_chat)] if env_chat and env_chat.lstrip("-").isdigit() else []
    log.info("no telegram.yml at %s, falling back to env-only (allowed=%s)", path, allowed)
    return {"allowed_chats": allowed, "default": {}, "chats": {}}


def is_allowed(cfg: dict[str, Any], chat_id: int, user_id: int) -> bool:
    allowed = cfg.get("allowed_chats", [])
    if allowed and chat_id not in allowed:
        return False
    chat_cfg = cfg.get("chats", {}).get(chat_id, {})
    allowed_users = chat_cfg.get("allowed_users", [])
    if allowed_users and user_id not in allowed_users:
        return False
    return True


def get_chat_config(
    cfg: dict[str, Any],
    chat_id: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge yaml default + per-chat yaml block + runtime overrides.

    Precedence (low→high): default → chats[<id>] → overrides[<id>]. When no
    workspace is set anywhere the default is a per-chat subdir so each chat
    is automatically isolated."""
    defaults = cfg.get("default", {})
    chat_cfg = cfg.get("chats", {}).get(chat_id, {})
    merged: dict[str, Any] = {**defaults, **chat_cfg, **(overrides or {})}
    if "workspace" not in merged:
        merged["workspace"] = f"chat_{abs(chat_id)}"
    return merged
