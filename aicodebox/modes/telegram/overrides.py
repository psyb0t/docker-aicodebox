"""Per-chat config overrides for the telegram bot.

The yaml file is the static baseline. Overrides set by users via /model,
/effort, /system_prompt, /append_system_prompt persist to a JSON file so they
survive container restarts. The file is written atomically (tmp + rename).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("telegram.overrides")

_DEFAULT_PATH = Path(
    os.environ.get("HOME", "/home/aicode")
) / ".aicodebox" / "telegram_overrides.json"

# Words that clear an override instead of setting it.
RESET_TOKENS: frozenset[str] = frozenset(
    {"__reset__", "default", "reset", "clear", "none"}
)


def _path() -> Path:
    override = os.environ.get("AICODEBOX_TELEGRAM_OVERRIDES")
    return Path(override) if override else _DEFAULT_PATH


def load(path: Path | None = None) -> dict[int, dict[str, Any]]:
    p = path or _path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("failed to load overrides from %s: %s", p, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("overrides file %s is not a dict — ignoring", p)
        return {}
    out: dict[int, dict[str, Any]] = {}
    for k, v in raw.items():
        try:
            chat_id = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        out[chat_id] = dict(v)
    return out


def save(state: dict[int, dict[str, Any]], path: Path | None = None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = {str(k): v for k, v in state.items() if v}
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.rename(p)


def set_value(
    state: dict[int, dict[str, Any]],
    chat_id: int,
    key: str,
    value: Any,
    path: Path | None = None,
) -> None:
    state.setdefault(chat_id, {})[key] = value
    save(state, path=path)


def clear_value(
    state: dict[int, dict[str, Any]],
    chat_id: int,
    key: str,
    path: Path | None = None,
) -> None:
    bucket = state.get(chat_id)
    if not bucket:
        return
    bucket.pop(key, None)
    if not bucket:
        state.pop(chat_id, None)
    save(state, path=path)


def apply_choice(
    state: dict[int, dict[str, Any]],
    chat_id: int,
    key: str,
    choice: str,
    allowed: Iterable[str],
    path: Path | None = None,
) -> None:
    """Set or clear an override after validating against the allowlist.

    A choice in RESET_TOKENS clears the override. An empty allowlist means
    "any string accepted" (used when the adapter exposes no choice menu)."""
    if choice in RESET_TOKENS:
        clear_value(state, chat_id, key, path=path)
        return
    allowed_list = list(allowed)
    if allowed_list and choice not in allowed_list:
        raise ValueError(
            f"unknown {key} {choice!r} (allowed: {allowed_list})"
        )
    set_value(state, chat_id, key, choice, path=path)


def get_chat_overrides(
    state: dict[int, dict[str, Any]], chat_id: int
) -> dict[str, Any]:
    return dict(state.get(chat_id, {}))
