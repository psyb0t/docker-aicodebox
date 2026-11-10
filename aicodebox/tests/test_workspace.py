"""Workspace subpath resolution for /run, the OpenAI endpoint and MCP."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicodebox.modes.api import workspace


@pytest.fixture
def root(monkeypatch, tmp_workspace) -> Path:
    monkeypatch.setattr(workspace, "ROOT_WORKSPACE", str(tmp_workspace))
    return tmp_workspace


def test_resolve_creates_subdir(root):
    assert workspace.resolve("alpha") == str((root / "alpha").resolve())
    assert (root / "alpha").is_dir()


def test_resolve_empty_returns_root(root):
    assert workspace.resolve("") == str(root)


@pytest.mark.parametrize("subpath", ["/etc", "../escape", "a/../../escape"])
def test_resolve_rejects_absolute_and_traversal(root, subpath):
    with pytest.raises(workspace.WorkspaceError):
        workspace.resolve(subpath)


def test_resolve_rejects_symlink_to_sibling_with_shared_prefix(root):
    # <root>-other starts with the root's path string but is outside it.
    sibling = root.parent / f"{root.name}-other"
    sibling.mkdir()
    (root / "link").symlink_to(sibling)

    with pytest.raises(workspace.WorkspaceError):
        workspace.resolve("link")


def test_resolve_allows_symlink_inside_root(root):
    (root / "real").mkdir()
    (root / "link").symlink_to(root / "real")

    assert workspace.resolve("link") == str((root / "real").resolve())
