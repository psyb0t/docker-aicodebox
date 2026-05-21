"""Bearer-token auth for API mode. If AICODEBOX_API_MODE_TOKEN is unset,
no auth is required."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

_TOKEN_ENV = "AICODEBOX_API_MODE_TOKEN"


def api_token() -> str:
    return os.environ.get(_TOKEN_ENV, "") or ""


def check_bearer(authorization: str | None = Header(default=None)) -> None:
    expected = api_token()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len("Bearer "):].strip()
    if presented != expected:
        raise HTTPException(status_code=401, detail="invalid bearer token")
