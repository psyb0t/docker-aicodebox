"""Tests for the remote image fetch behind the SSRF guard in oai.py.

A loopback HTTP server stands in for the remote host. The guard rejects
loopback, so each test allows only the entry URL and leaves every other
URL to the real guard.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import pytest

from aicodebox.modes.api import oai as oai_mod

_SECRET = b"internal-only"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/image.png":
            self.send_response(302)
            self.send_header("Location", "/internal")
            self.end_headers()
            return
        if self.path == "/internal":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_SECRET)))
            self.end_headers()
            self.wfile.write(_SECRET)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


@pytest.fixture
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _allow_only(monkeypatch, allowed_url: str) -> None:
    real_guard = oai_mod._is_safe_remote_url

    def guard(url: str) -> bool:
        return url == allowed_url or real_guard(url)

    monkeypatch.setattr(oai_mod, "_is_safe_remote_url", guard)


def test_fetch_refuses_redirect_to_unsafe_url(monkeypatch, server) -> None:
    entry = f"{server}/image.png"
    _allow_only(monkeypatch, entry)

    assert oai_mod._fetch_remote_sync(entry) is None


def test_fetch_follows_redirect_to_safe_url(monkeypatch, server) -> None:
    entry = f"{server}/image.png"
    target = f"{server}/internal"
    real_guard = oai_mod._is_safe_remote_url
    monkeypatch.setattr(
        oai_mod,
        "_is_safe_remote_url",
        lambda url: url in (entry, target) or real_guard(url),
    )

    assert oai_mod._fetch_remote_sync(entry) == (_SECRET, "image/png")
