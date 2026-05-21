"""Read the cron scheduler's telegram-message inbox.

The cron mode writes a JSON map (telegram_message_id → metadata) every time
a job posts its result to Telegram. The telegram bot uses this to detect
when a user reply quotes a cron-triggered message and inject the original
job context into the follow-up prompt.

Storage path: ``~/.aicodebox/cron/telegram_messages.json``. Override the
parent dir via ``AICODEBOX_CRON_MODE_HISTORY_DIR``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("telegram.cron_inbox")


def _inbox_path() -> Path:
    override = os.environ.get("AICODEBOX_CRON_MODE_HISTORY_DIR")
    if override:
        return Path(override) / "telegram_messages.json"
    home = Path(os.environ.get("HOME", "/home/aicode"))
    return home / ".aicodebox" / "cron" / "telegram_messages.json"


def load_all(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or _inbox_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("failed to read cron inbox %s: %s", p, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("cron inbox %s is not a dict", p)
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def load_cron_message(
    message_id: int,
    path: Path | None = None,
) -> dict[str, Any] | None:
    return load_all(path=path).get(str(message_id))
