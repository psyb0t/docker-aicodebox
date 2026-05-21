"""Shared test fixtures and adapter-cache reset."""

from __future__ import annotations

import sys

import pytest

from aicodebox.adapters import base as adapter_base


class _StubAdapter(adapter_base.AgentAdapter):
    name = "stub"
    binary = "stub-binary"
    available_models = ["stub-a", "stub-b"]
    available_thinking_levels = ["off", "low", "high"]

    def build_argv(self, req):  # noqa: D401
        return [self.binary, req.prompt]


@pytest.fixture
def stub_adapter(monkeypatch):
    """Install a deterministic adapter for the duration of the test."""
    monkeypatch.setitem(sys.modules, "aicodebox.tests._stub", sys.modules[__name__])
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.conftest:_StubAdapter",
    )
    adapter_base.reset_adapter_cache()
    yield _StubAdapter()
    adapter_base.reset_adapter_cache()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Prevent host env from leaking into tests."""
    for var in (
        "AICODEBOX_AVAILABLE_MODELS",
        "AICODEBOX_AVAILABLE_EFFORTS",
        "AICODEBOX_TELEGRAM_MODE_OVERRIDES",
        "AICODEBOX_TELEGRAM_MODE_CONFIG",
        "AICODEBOX_CRON_MODE_HISTORY_DIR",
        "AICODEBOX_WORKSPACE",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("AICODEBOX_WORKSPACE", str(ws))
    return ws
