"""Tests for stealth_vps.select_backend — the panel-vs-headless dispatcher.

Selection rule (v0.7.0):
  - panel.state.yml exists  → ThreeXUIBackend (panel API + index double-write)
  - panel.state.yml absent  → HeadlessBackend (index-only, with reloader)

This test file mocks the ThreeXUIClient — we don't exercise its actual
HTTP path; that's covered in test_threex_client.py. Here we only verify
the *dispatch* is right.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest

import stealth_vps
from stealth_vps import HeadlessBackend, ThreeXUIBackend, ThreeXUIClient


@pytest.fixture
def fake_threex_client():
    """A ThreeXUIClient stand-in. We patch login so construction doesn't
    try to hit a real URL."""
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        yield ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )


# ---------------------------------------------------------------------------
# Headless path (no panel.state.yml)
# ---------------------------------------------------------------------------


def test_select_backend_returns_headless_when_panel_state_absent(
    tmp_path: pathlib.Path, users_index_path: str,
) -> None:
    missing = tmp_path / "panel.state.yml"  # never created
    backend = stealth_vps.select_backend(
        panel_state_path=str(missing),
        users_index_path=users_index_path,
    )
    assert isinstance(backend, HeadlessBackend)


def test_select_backend_passes_reloader_to_headless(
    tmp_path: pathlib.Path, users_index_path: str,
) -> None:
    """The reloader the caller hands in should reach the constructed backend."""
    sentinel_called: list[bool] = []

    def my_reloader() -> None:
        sentinel_called.append(True)

    missing = tmp_path / "panel.state.yml"
    backend = stealth_vps.select_backend(
        panel_state_path=str(missing),
        users_index_path=users_index_path,
        reloader=my_reloader,
    )
    assert isinstance(backend, HeadlessBackend)
    # Calling add triggers the reloader — verifies wiring without
    # needing to introspect private attrs.
    backend.add("bob")
    assert sentinel_called == [True]


def test_select_backend_passes_default_hysteria_password_to_headless(
    tmp_path: pathlib.Path, users_index_path: str,
) -> None:
    missing = tmp_path / "panel.state.yml"
    backend = stealth_vps.select_backend(
        panel_state_path=str(missing),
        users_index_path=users_index_path,
        default_hysteria_password="migration-shared-pw",
    )
    backend.add("bob")

    from stealth_vps import load_users_index
    idx = load_users_index(users_index_path)
    assert idx["users"]["bob"]["hysteria_password"] == "migration-shared-pw"


# ---------------------------------------------------------------------------
# Panel path (panel.state.yml exists)
# ---------------------------------------------------------------------------


def test_select_backend_returns_threex_when_panel_state_present(
    tmp_path: pathlib.Path, fake_threex_client, users_index_path: str,
) -> None:
    panel_yml = tmp_path / "panel.state.yml"
    panel_yml.write_text("port: 32999\nusername: admin\n", encoding="utf-8")

    backend = stealth_vps.select_backend(
        panel_state_path=str(panel_yml),
        threex_client=fake_threex_client,
        users_index_path=users_index_path,
    )
    assert isinstance(backend, ThreeXUIBackend)


def test_select_backend_panel_without_client_raises(tmp_path: pathlib.Path) -> None:
    """Panel mode detected but caller forgot to supply a ThreeXUIClient —
    we error loudly. The bot/CLI startup code is supposed to construct
    one from panel.state.yml's credentials before dispatching."""
    panel_yml = tmp_path / "panel.state.yml"
    panel_yml.write_text("port: 32999\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ThreeXUIClient"):
        stealth_vps.select_backend(panel_state_path=str(panel_yml))
