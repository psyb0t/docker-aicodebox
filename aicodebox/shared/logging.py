"""Structured logging used by all modes.

When DEBUG env var is truthy, emits one JSON object per record (ts, level,
logger, func, line, file, msg). Otherwise emits plain `LEVEL logger: msg`.
"""

from __future__ import annotations

import json
import logging
import os


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "func": record.funcName,
            "line": record.lineno,
            "file": record.filename,
            "msg": record.getMessage(),
        })


def configure_logging() -> None:
    debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    handler = logging.StreamHandler()
    if debug:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
