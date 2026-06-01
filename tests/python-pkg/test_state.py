"""Tests for stealth_vps.state — users.index.json I/O + label validation."""

from __future__ import annotations

import datetime
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
    # v0.9.0+: the load step auto-upgrades v1 fixtures to v2 by adding
    # sub_expires_at=None. v0.11.0+: extends to v3 by adding the four
    # new-protocol fields. Both upgrades are in-memory only — the on-
    # disk file stays at its written version until the next mutation
    # triggers save_users_index.
    assert data["version"] == 3
    assert "alice" in data["users"]
    assert data["users"]["alice"]["enabled"] is True
    assert data["users"]["alice"]["sub_expires_at"] is None
    # v3 fields default to None on auto-upgrade.
    assert data["users"]["alice"]["ss2022_psk"] is None
    assert data["users"]["alice"]["wireguard_pubkey"] is None
    assert data["users"]["alice"]["wireguard_client_ip"] is None
    assert data["users"]["alice"]["trojan_password"] is None


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
    """A v3 payload writes and reads back unchanged. The migration chain
    (v1→v2→v3) is exercised separately; here we keep equality direct.
    """
    payload = {
        "version": 3,
        "users": {
            "bob": {
                "reality_uuid": "uuid-bob",
                "hysteria_password": "pw-bob",
                "sub_token": "tok-bob",
                "created_at": "2026-02-02T02:02:02Z",
                "enabled": False,
                "sub_expires_at": None,
                "ss2022_psk": None,
                "wireguard_pubkey": None,
                "wireguard_client_ip": None,
                "trojan_password": None,
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
# purge_user
# ---------------------------------------------------------------------------


def test_purge_user_removes_row_outright(users_index_path: str) -> None:
    state.purge_user("alice", users_index_path)
    data = state.load_users_index(users_index_path)
    assert "alice" not in data["users"]


def test_purge_user_idempotent_for_missing_label(users_index_path: str) -> None:
    """Purging a label that isn't there is a clean no-op — does NOT raise.
    Operators run `s-vps user purge LABEL` defensively; surfacing an
    error would force them to guard every call.
    """
    before = state.load_users_index(users_index_path)
    state.purge_user("never-existed", users_index_path)
    after = state.load_users_index(users_index_path)
    assert before == after


def test_purge_user_then_add_reuses_label(users_index_path: str) -> None:
    """After a purge the label slot is fully free — subsequent `add_user`
    with the same label works (vs revoke, which would block with
    "already exists").
    """
    state.purge_user("alice", users_index_path)
    state.add_user(
        "alice",
        reality_uuid="11111111-1111-1111-1111-111111111111",
        hysteria_password="new-pw",
        sub_token="new-sub-token",
        created_at="2026-06-01T00:00:00Z",
        path=users_index_path,
    )
    rec = state.get_user("alice", users_index_path)
    assert rec is not None
    assert rec["reality_uuid"] == "11111111-1111-1111-1111-111111111111"
    # created_at is the NEW one — purge erased the old row entirely.
    assert rec["created_at"] == "2026-06-01T00:00:00Z"


# ---------------------------------------------------------------------------
# update_user (rotate primitive)
# ---------------------------------------------------------------------------


def test_update_user_patches_specified_fields_only(users_index_path: str) -> None:
    state.update_user(
        "alice",
        reality_uuid="22222222-2222-2222-2222-222222222222",
        path=users_index_path,
    )
    rec = state.get_user("alice", users_index_path)
    assert rec is not None
    assert rec["reality_uuid"] == "22222222-2222-2222-2222-222222222222"
    # Unspecified fields preserved.
    assert rec["hysteria_password"] == "alice-hy2-pw"
    assert rec["sub_token"] == "alice-sub-token"
    assert rec["created_at"] == "2026-01-01T00:00:00Z"
    assert rec["enabled"] is True


def test_update_user_can_re_enable_revoked(users_index_path: str) -> None:
    state.revoke_user("alice", users_index_path)
    state.update_user("alice", enabled=True, path=users_index_path)
    rec = state.get_user("alice", users_index_path)
    assert rec is not None
    assert rec["enabled"] is True


def test_update_user_missing_label_raises(users_index_path: str) -> None:
    with pytest.raises(state.StateError, match="not found"):
        state.update_user("nobody", reality_uuid="x", path=users_index_path)


# ---------------------------------------------------------------------------
# parse_duration + compute_expiry (v0.9.0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("30s", 30),
        ("5m", 5 * 60),
        ("2h", 2 * 60 * 60),
        ("1d", 24 * 60 * 60),
        ("2w", 14 * 24 * 60 * 60),
        ("3mo", 3 * 30 * 24 * 60 * 60),
        ("1y", 365 * 24 * 60 * 60),
        # Tolerant of leading / trailing whitespace and uppercase.
        ("  30D  ", 30 * 24 * 60 * 60),
    ],
)
def test_parse_duration_accepts(text: str, seconds: int) -> None:
    assert state.parse_duration(text) == seconds


@pytest.mark.parametrize(
    "text",
    [
        "",                  # empty
        "30",                # no unit
        "d30",               # unit first
        "30 d",              # space inside
        "30days",            # multi-char unit
        "thirty days",       # not digits
        "-30d",              # negative not supported (nor needed)
        "30dx",              # trailing junk
    ],
)
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(state.StateError, match="duration"):
        state.parse_duration(text)


def test_compute_expiry_adds_duration_to_now() -> None:
    fixed = datetime.datetime(2026, 5, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    expiry = state.compute_expiry("30d", now=fixed)
    # 30 days later → 2026-06-19T12:00:00Z.
    assert expiry == "2026-06-19T12:00:00Z"


def test_compute_expiry_default_now_is_utc() -> None:
    """Default `now` is utcnow — the returned string is a valid
    ISO 8601 UTC timestamp parseable round-trip."""
    expiry = state.compute_expiry("1d")
    parsed = state._parse_iso_utc(expiry)
    delta = parsed - datetime.datetime.now(datetime.timezone.utc)
    # ~24h ± a few seconds for test runtime.
    assert datetime.timedelta(hours=23, minutes=59) < delta < datetime.timedelta(hours=24, minutes=1)


# ---------------------------------------------------------------------------
# is_expired + expired_sub_tokens
# ---------------------------------------------------------------------------


def test_is_expired_none_returns_false() -> None:
    """Missing or null sub_expires_at means never-expires (the v0.8.x
    default for the whole user table)."""
    assert state.is_expired({}) is False
    assert state.is_expired({"sub_expires_at": None}) is False


def test_is_expired_past_returns_true() -> None:
    rec = {"sub_expires_at": "2020-01-01T00:00:00Z"}
    assert state.is_expired(rec) is True


def test_is_expired_future_returns_false() -> None:
    fixed = datetime.datetime(2026, 5, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    rec = {"sub_expires_at": "2027-01-01T00:00:00Z"}
    assert state.is_expired(rec, now=fixed) is False


def test_is_expired_garbled_raises() -> None:
    rec = {"sub_expires_at": "not a timestamp"}
    with pytest.raises(state.StateError, match="not parseable"):
        state.is_expired(rec)


def test_expired_sub_tokens_filters_correctly(
    tmp_path: pathlib.Path,
) -> None:
    """Mix of: expired (returned), not-yet-expired (skipped), never-
    expires (skipped), garbled timestamp (skipped silently)."""
    idx = tmp_path / "users.index.json"
    idx.write_text(
        json.dumps(
            {
                "version": 2,
                "users": {
                    "expired_alice": {
                        "reality_uuid": "u1",
                        "hysteria_password": "p1",
                        "sub_token": "tok-alice-expired",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                        "sub_expires_at": "2020-01-01T00:00:00Z",
                    },
                    "future_bob": {
                        "reality_uuid": "u2",
                        "hysteria_password": "p2",
                        "sub_token": "tok-bob-future",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                        "sub_expires_at": "2099-01-01T00:00:00Z",
                    },
                    "never_carol": {
                        "reality_uuid": "u3",
                        "hysteria_password": "p3",
                        "sub_token": "tok-carol-never",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                        "sub_expires_at": None,
                    },
                    "garbled_dave": {
                        "reality_uuid": "u4",
                        "hysteria_password": "p4",
                        "sub_token": "tok-dave-garbled",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                        "sub_expires_at": "not-a-timestamp",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    out = state.expired_sub_tokens(str(idx))
    # Only expired_alice. Garbled dave is SKIPPED (not raised) so the
    # prune doesn't blow up on a single bad row.
    assert out == [("expired_alice", "tok-alice-expired")]


# ---------------------------------------------------------------------------
# Schema migration v1 → v2 → v3
# ---------------------------------------------------------------------------


def test_load_users_index_upgrades_v1_to_v3(users_index_path: str) -> None:
    """The fixture seeds a v1 file. `load_users_index` walks the
    migration chain (v1 → v2 → v3) in one pass: every user gets
    sub_expires_at + the four v3 protocol credentials (all None)."""
    idx = state.load_users_index(users_index_path)
    assert idx["version"] == 3
    alice = idx["users"]["alice"]
    assert alice["sub_expires_at"] is None
    assert alice["ss2022_psk"] is None
    assert alice["wireguard_pubkey"] is None
    assert alice["wireguard_client_ip"] is None
    assert alice["trojan_password"] is None


def test_load_users_index_upgrades_v2_to_v3(tmp_path: pathlib.Path) -> None:
    """A file written by a v0.9 / v0.10 box (schema v2) loads cleanly
    on v0.11. v3 fields default to None on the in-memory upgrade."""
    payload = {
        "version": 2,
        "users": {
            "carol": {
                "reality_uuid": "u",
                "hysteria_password": "p",
                "sub_token": "t",
                "created_at": "2026-01-01T00:00:00Z",
                "enabled": True,
                "sub_expires_at": None,
            },
        },
    }
    p = tmp_path / "v2.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    idx = state.load_users_index(str(p))
    assert idx["version"] == 3
    assert idx["users"]["carol"]["ss2022_psk"] is None
    assert idx["users"]["carol"]["trojan_password"] is None


def test_load_users_index_rejects_future_versions(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "future.json"
    p.write_text(
        json.dumps({"version": 99, "users": {}}), encoding="utf-8"
    )
    with pytest.raises(state.StateError, match="unsupported"):
        state.load_users_index(str(p))


def test_add_user_with_sub_expires_at(users_index_path: str) -> None:
    state.add_user(
        "bob",
        reality_uuid="bob-uuid",
        hysteria_password="bob-pw",
        sub_token="bob-sub",
        created_at="2026-01-01T00:00:00Z",
        sub_expires_at="2099-01-01T00:00:00Z",
        path=users_index_path,
    )
    assert state.get_user("bob", users_index_path)["sub_expires_at"] == "2099-01-01T00:00:00Z"


def test_update_user_can_set_expiry_to_none() -> None:
    """Operator wants to revoke an expiry (set it to never-expires).
    Passing sub_expires_at=None explicitly should clear the field;
    NOT setting it (the default _UNSET sentinel) leaves it alone."""
    # Use a tmp idx with an existing expires_at to flip.
    tmp = pathlib.Path(state.USERS_INDEX_PATH)  # any path — we override below
    # Construct a tmp file manually.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "version": 2,
                "users": {
                    "alice": {
                        "reality_uuid": "u",
                        "hysteria_password": "p",
                        "sub_token": "t",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                        "sub_expires_at": "2099-01-01T00:00:00Z",
                    }
                },
            },
            f,
        )
        path = f.name
    try:
        state.update_user("alice", sub_expires_at=None, path=path)
        assert state.get_user("alice", path)["sub_expires_at"] is None
    finally:
        os.unlink(path)


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
