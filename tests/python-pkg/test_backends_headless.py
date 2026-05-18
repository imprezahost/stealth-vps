"""Tests for stealth_vps.backends_headless.HeadlessBackend.

Headline behaviour: add/revoke mutate users.index.json atomically, then
invoke a reload callback. We verify the index state + the callback
invocation order — that's enough to lock the contract Block 3's real
reloader will plug into.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from stealth_vps import state
from stealth_vps.backends_headless import HeadlessBackend


def _make_backend(users_index_path: str, **kw):
    """Build a backend whose reloader is a MagicMock — tests can assert
    on call order + count + arguments."""
    reloader = kw.pop("reloader", None) or MagicMock()
    backend = HeadlessBackend(
        users_index_path=users_index_path,
        reloader=reloader,
        **kw,
    )
    return backend, reloader


# ---------------------------------------------------------------------------
# HeadlessBackend.add
# ---------------------------------------------------------------------------


def test_add_writes_index_then_calls_reloader(users_index_path: str) -> None:
    backend, reloader = _make_backend(users_index_path)
    rec = backend.add("bob")

    # Index has the new user.
    idx = state.load_users_index(users_index_path)
    assert "bob" in idx["users"]
    assert idx["users"]["bob"]["enabled"] is True
    assert idx["users"]["bob"]["reality_uuid"] == rec["reality_uuid"]
    assert idx["users"]["bob"]["hysteria_password"] == rec["hysteria_password"]
    assert idx["users"]["bob"]["sub_token"] == rec["sub_token"]

    # Reloader was called exactly once, AFTER the index was written.
    reloader.assert_called_once_with()


def test_add_generates_unique_uuids(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    backend.add("bob")
    backend.add("carol")

    idx = state.load_users_index(users_index_path)
    uuids = {idx["users"][u]["reality_uuid"] for u in ("bob", "carol")}
    assert len(uuids) == 2, "two adds should produce two distinct UUIDs"
    # Also distinct from the seed user.
    assert idx["users"]["alice"]["reality_uuid"] not in uuids


def test_add_generates_per_user_hysteria_password_by_default(users_index_path: str) -> None:
    """v0.7 default: per-user Hysteria2 password. Each add mints fresh."""
    backend, _ = _make_backend(users_index_path)
    backend.add("bob")
    backend.add("carol")

    idx = state.load_users_index(users_index_path)
    bob_pw = idx["users"]["bob"]["hysteria_password"]
    carol_pw = idx["users"]["carol"]["hysteria_password"]
    assert bob_pw != ""
    assert carol_pw != ""
    assert bob_pw != carol_pw, "per-user mode: each user gets its own Hysteria2 password"


def test_add_honors_default_hysteria_password_from_constructor(users_index_path: str) -> None:
    """Migration path: the from-3xui migrator constructs a HeadlessBackend
    with the panel-era shared Hysteria2 password preset, so every migrated
    user inherits the same password and existing clients keep working."""
    backend, _ = _make_backend(
        users_index_path,
        default_hysteria_password="shared-panel-era-pw",
    )
    backend.add("bob")
    backend.add("carol")

    idx = state.load_users_index(users_index_path)
    assert idx["users"]["bob"]["hysteria_password"] == "shared-panel-era-pw"
    assert idx["users"]["carol"]["hysteria_password"] == "shared-panel-era-pw"


def test_add_explicit_hysteria_password_overrides_default(users_index_path: str) -> None:
    """Explicit kwarg wins over both constructor default and auto-generated."""
    backend, _ = _make_backend(
        users_index_path,
        default_hysteria_password="should-not-be-used",
    )
    backend.add("bob", hysteria_password="bob-explicit-pw")

    idx = state.load_users_index(users_index_path)
    assert idx["users"]["bob"]["hysteria_password"] == "bob-explicit-pw"


def test_add_invalid_label_does_not_call_reloader(users_index_path: str) -> None:
    """Label validation happens up front — bad labels must NOT trigger a
    reload (which would be wasted work + potential service blip)."""
    backend, reloader = _make_backend(users_index_path)
    with pytest.raises(state.StateError, match="invalid"):
        backend.add("bad/label")
    reloader.assert_not_called()


def test_add_duplicate_label_does_not_call_reloader(users_index_path: str) -> None:
    """state.add_user raises before save_users_index touches disk —
    so reloader shouldn't fire either."""
    backend, reloader = _make_backend(users_index_path)
    with pytest.raises(state.StateError, match="already exists"):
        backend.add("alice")  # alice was seeded by the fixture
    reloader.assert_not_called()


def test_add_reloader_failure_leaves_index_in_place(users_index_path: str) -> None:
    """If the reloader raises (xray/hysteria failed to reload), the index
    is already on disk with the new user. Caller can retry the reload
    via `s-vps reload` without losing the entry."""
    reloader = MagicMock(side_effect=RuntimeError("systemctl exploded"))
    backend, _ = _make_backend(users_index_path, reloader=reloader)

    with pytest.raises(RuntimeError, match="systemctl exploded"):
        backend.add("bob")

    # Bob is still in the index.
    idx = state.load_users_index(users_index_path)
    assert "bob" in idx["users"]


# ---------------------------------------------------------------------------
# HeadlessBackend.revoke
# ---------------------------------------------------------------------------


def test_revoke_flips_enabled_false_then_reloads(users_index_path: str) -> None:
    backend, reloader = _make_backend(users_index_path)
    backend.revoke("alice")

    idx = state.load_users_index(users_index_path)
    assert idx["users"]["alice"]["enabled"] is False
    reloader.assert_called_once_with()


def test_revoke_missing_label_does_not_call_reloader(users_index_path: str) -> None:
    backend, reloader = _make_backend(users_index_path)
    with pytest.raises(state.StateError, match="not found"):
        backend.revoke("nobody")
    reloader.assert_not_called()


# ---------------------------------------------------------------------------
# HeadlessBackend.list / get — thin wrappers around `state`
# ---------------------------------------------------------------------------


def test_list_returns_only_enabled_by_default(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    backend.add("bob")
    backend.revoke("alice")

    labels = [label for label, _ in backend.list()]
    assert labels == ["bob"]


def test_list_include_disabled(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    backend.add("bob")
    backend.revoke("alice")

    labels = [label for label, _ in backend.list(include_disabled=True)]
    assert set(labels) == {"alice", "bob"}


def test_get_returns_record_or_none(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    rec = backend.get("alice")
    assert rec is not None
    assert rec["enabled"] is True
    assert backend.get("nobody") is None
