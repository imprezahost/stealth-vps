"""Tests for stealth_vps.state — users.index.json I/O + label validation."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from stealth_vps import state


# ---------------------------------------------------------------------------
# label_valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "alice",
        "alice123",
        "alice-bob",
        "alice_bob",
        "a",                          # min length 1
        "x" * 32,                     # max length 32
        "ALICE",                      # uppercase allowed
    ],
)
def test_label_valid_accepts(label: str) -> None:
    assert state.label_valid(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "",                            # empty rejected
        "x" * 33,                      # over max length
        "alice@bob",                   # @ disallowed
        "alice bob",                   # space disallowed
        "alice/bob",                   # / disallowed (path-traversal risk)
        "stealth-vps-default",         # reserved prefix
        "stealth-vps-",                # bare reserved prefix
    ],
)
def test_label_valid_rejects(label: str) -> None:
    assert state.label_valid(label) is False


def test_label_valid_allow_reserved_lets_reserved_through() -> None:
    """The role's own seed clients use the reserved prefix — allow_reserved=True
    is the only way to add them."""
    assert state.label_valid("stealth-vps-default", allow_reserved=True) is True


# ---------------------------------------------------------------------------
# load_users_index
# ---------------------------------------------------------------------------


def test_load_users_index_returns_dict(users_index_path: str) -> None:
    data = state.load_users_index(users_index_path)
    assert data["version"] == 1
    assert "alice" in data["users"]
    assert data["users"]["alice"]["enabled"] is True


def test_load_users_index_missing_file_raises_stateerror(tmp_path: pathlib.Path) -> None:
    with pytest.raises(state.StateError, match="missing"):
        state.load_users_index(str(tmp_path / "absent.json"))


def test_load_users_index_corrupt_json_raises_stateerror(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(state.StateError, match="corrupt"):
        state.load_users_index(str(p))


def test_load_users_index_unsupported_version_raises(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "v99.json"
    p.write_text(json.dumps({"version": 99, "users": {}}), encoding="utf-8")
    with pytest.raises(state.StateError, match="schema version"):
        state.load_users_index(str(p))


def test_load_users_index_missing_required_keys_raises(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "noschema.json"
    p.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    with pytest.raises(state.StateError, match="unexpected shape"):
        state.load_users_index(str(p))


# ---------------------------------------------------------------------------
# save_users_index — atomicity + permissions
# ---------------------------------------------------------------------------


def test_save_users_index_writes_via_rename(tmp_path: pathlib.Path) -> None:
    """Verifies the atomic-rename pattern: the final file should exist after
    the call returns, and no .tmp leftover should linger."""
    target = tmp_path / "users.index.json"
    state.save_users_index({"version": 1, "users": {}}, str(target))
    assert target.exists()
    # No tempfile lingering — list dir entries.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "users.index.json"]
    assert leftovers == [], f"unexpected leftovers: {leftovers}"


def test_save_users_index_sets_mode_0600(tmp_path: pathlib.Path) -> None:
    """The save function chmods the tempfile to 0600 before rename. On
    Windows the mode-bits are simulated, so we only assert on POSIX."""
    if os.name != "posix":
        pytest.skip("chmod semantics differ on Windows")
    target = tmp_path / "users.index.json"
    state.save_users_index({"version": 1, "users": {}}, str(target))
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_users_index_round_trip(tmp_path: pathlib.Path) -> None:
    payload = {
        "version": 1,
        "users": {
            "bob": {
                "reality_uuid": "uuid-bob",
                "hysteria_password": "pw-bob",
                "sub_token": "tok-bob",
                "created_at": "2026-02-02T02:02:02Z",
                "enabled": False,
            },
        },
    }
    p = tmp_path / "users.index.json"
    state.save_users_index(payload, str(p))
    loaded = state.load_users_index(str(p))
    assert loaded == payload


# ---------------------------------------------------------------------------
# add_user
# ---------------------------------------------------------------------------


def test_add_user_appends_to_index(users_index_path: str) -> None:
    state.add_user(
        "bob",
        reality_uuid="uuid-bob",
        hysteria_password="pw-bob",
        sub_token="tok-bob",
        created_at="2026-02-02T02:02:02Z",
        path=users_index_path,
    )
    data = state.load_users_index(users_index_path)
    assert "alice" in data["users"]
    assert "bob" in data["users"]
    assert data["users"]["bob"]["reality_uuid"] == "uuid-bob"
    assert data["users"]["bob"]["enabled"] is True


def test_add_user_duplicate_label_raises(users_index_path: str) -> None:
    with pytest.raises(state.StateError, match="already exists"):
        state.add_user(
            "alice",
            reality_uuid="dup",
            hysteria_password="dup",
            sub_token="dup",
            created_at="2026-01-01T00:00:00Z",
            path=users_index_path,
        )


def test_add_user_invalid_label_raises(users_index_path: str) -> None:
    with pytest.raises(state.StateError, match="invalid"):
        state.add_user(
            "bad/label",
            reality_uuid="x",
            hysteria_password="x",
            sub_token="x",
            created_at="2026-01-01T00:00:00Z",
            path=users_index_path,
        )


def test_add_user_reserved_prefix_blocked_by_default(users_index_path: str) -> None:
    with pytest.raises(state.StateError, match="invalid"):
        state.add_user(
            "stealth-vps-extra",
            reality_uuid="x",
            hysteria_password="x",
            sub_token="x",
            created_at="2026-01-01T00:00:00Z",
            path=users_index_path,
        )


def test_add_user_reserved_prefix_allowed_with_flag(users_index_path: str) -> None:
    state.add_user(
        "stealth-vps-extra",
        reality_uuid="x",
        hysteria_password="x",
        sub_token="x",
        created_at="2026-01-01T00:00:00Z",
        path=users_index_path,
        allow_reserved=True,
    )
    assert state.get_user("stealth-vps-extra", users_index_path) is not None


# ---------------------------------------------------------------------------
# revoke_user
# ---------------------------------------------------------------------------


def test_revoke_user_flips_enabled_false(users_index_path: str) -> None:
    state.revoke_user("alice", users_index_path)
    data = state.load_users_index(users_index_path)
    assert data["users"]["alice"]["enabled"] is False
    # Other fields untouched.
    assert data["users"]["alice"]["reality_uuid"] == "00000000-0000-0000-0000-000000000001"


def test_revoke_user_missing_label_raises(users_index_path: str) -> None:
    with pytest.raises(state.StateError, match="not found"):
        state.revoke_user("nobody", users_index_path)


def test_revoke_user_idempotent_via_double_call(users_index_path: str) -> None:
    """Calling revoke twice on the same label is a no-op (still enabled=False)."""
    state.revoke_user("alice", users_index_path)
    state.revoke_user("alice", users_index_path)
    data = state.load_users_index(users_index_path)
    assert data["users"]["alice"]["enabled"] is False


# ---------------------------------------------------------------------------
# get_user / list_users
# ---------------------------------------------------------------------------


def test_get_user_existing_returns_record(users_index_path: str) -> None:
    rec = state.get_user("alice", users_index_path)
    assert rec is not None
    assert rec["reality_uuid"] == "00000000-0000-0000-0000-000000000001"


def test_get_user_missing_returns_none(users_index_path: str) -> None:
    assert state.get_user("nobody", users_index_path) is None


def test_list_users_skips_disabled_by_default(users_index_path: str) -> None:
    # Add a second user, then disable one.
    state.add_user(
        "bob",
        reality_uuid="uuid-bob",
        hysteria_password="pw-bob",
        sub_token="tok-bob",
        created_at="2026-02-02T02:02:02Z",
        path=users_index_path,
    )
    state.revoke_user("alice", users_index_path)
    labels = [label for label, _ in state.list_users(users_index_path)]
    assert labels == ["bob"]


def test_list_users_include_disabled_returns_all(users_index_path: str) -> None:
    state.revoke_user("alice", users_index_path)
    labels = [label for label, _ in state.list_users(users_index_path, include_disabled=True)]
    assert labels == ["alice"]


def test_list_users_returns_sorted_by_label(users_index_path: str) -> None:
    for label in ("carol", "bob", "dave"):
        state.add_user(
            label,
            reality_uuid=f"uuid-{label}",
            hysteria_password=f"pw-{label}",
            sub_token=f"tok-{label}",
            created_at="2026-01-01T00:00:00Z",
            path=users_index_path,
        )
    labels = [label for label, _ in state.list_users(users_index_path)]
    assert labels == sorted(labels)
