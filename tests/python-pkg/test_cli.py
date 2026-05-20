"""Tests for stealth_vps.cli — the `s-vps` operator CLI subcommands.

We test through the argparse entry point (`cli.main(argv)`) so the
real wiring (subparser registration, defaults, exit-code mapping) is
exercised on every run. Backend selection is forced into headless
mode by monkeypatching `PANEL_STATE_PATH` to a non-existent file.

A spy Reloader replaces the real one for mutation tests — we want to
confirm `user add` writes the index then triggers a reload, not that
systemctl actually runs (which would need a docker container).
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import cli, state


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cli_paths_into_tmp(tmp_path: pathlib.Path, users_index_path: str, monkeypatch):
    """Redirect every absolute path the CLI hard-codes into the per-test
    tmp directory. Leaves the rest of the host filesystem untouched.

    Also seeds an empty `reality.state.yml` so `_is_control_box()`
    (v0.10.0+) returns False — legacy tests built around the single-node
    flow assume a Reloader will be invoked, and the role guarantees
    reality.state.yml exists on every data-node / single-node host.
    Control-mode tests delete this file before invoking the CLI.
    """
    monkeypatch.setattr(cli, "PANEL_STATE_PATH", str(tmp_path / "panel.state.yml"))
    monkeypatch.setattr(cli, "RELOADER_ARGS_PATH", str(tmp_path / "reloader-args.json"))
    monkeypatch.setattr(cli, "INSTALLER_ENV_PATH", str(tmp_path / "installer.env"))
    monkeypatch.setattr(cli, "REALITY_STATE_PATH", str(tmp_path / "reality.state.yml"))
    monkeypatch.setattr(cli, "HYSTERIA_STATE_PATH", str(tmp_path / "hysteria.state.yml"))
    monkeypatch.setattr(state, "USERS_INDEX_PATH", users_index_path)
    # Seed a placeholder reality.state.yml so the default test mode is
    # "data node / single-node" (Reloader path active). Control-mode
    # tests `os.unlink()` this file as part of their setup.
    (tmp_path / "reality.state.yml").write_text(
        "port: 51820\n", encoding="utf-8",
    )


@pytest.fixture
def reloader_args_json(tmp_path: pathlib.Path) -> str:
    """Pre-populate the reloader-args.json file the CLI loads to build
    a Reloader. Points everything at tmp paths so the actual Reloader
    constructor doesn't try to touch /etc.
    """
    p = tmp_path / "reloader-args.json"
    p.write_text(
        json.dumps(
            {
                "users_index_path": str(tmp_path / "users.index.json"),
                "reality_enabled": False,
                "hysteria_enabled": False,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def installer_env_panel_domain(tmp_path: pathlib.Path) -> None:
    """Write a minimal installer.env with the STEALTH_DOMAIN set so URI
    rendering picks something other than the placeholder hostname.
    """
    (tmp_path / "installer.env").write_text(
        'STEALTH_DOMAIN="vpn.example.com"\n',
        encoding="utf-8",
    )


@pytest.fixture
def reality_state_yml(tmp_path: pathlib.Path) -> None:
    (tmp_path / "reality.state.yml").write_text(
        "port: 51820\n"
        "public_key: PUBKEYBASE64\n"
        "short_id: ab12cd34\n"
        'client_uuid: "00000000-0000-0000-0000-000000000001"\n',
        encoding="utf-8",
    )


@pytest.fixture
def hysteria_state_yml(tmp_path: pathlib.Path) -> None:
    (tmp_path / "hysteria.state.yml").write_text(
        "port: 36000\nobfs_password: OBFSPW\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _load_installer_env / _load_reloader_args
# ---------------------------------------------------------------------------


def test_load_installer_env_strips_quotes(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "installer.env"
    p.write_text(
        '# leading comment\n'
        'STEALTH_DOMAIN="vpn.example.com"\n'
        "STEALTH_PANEL_ENABLED=false\n"
        "BARE_VALUE=plain\n",
        encoding="utf-8",
    )
    out = cli._load_installer_env(str(p))
    assert out["STEALTH_DOMAIN"] == "vpn.example.com"
    assert out["STEALTH_PANEL_ENABLED"] == "false"
    assert out["BARE_VALUE"] == "plain"


def test_load_installer_env_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    assert cli._load_installer_env(str(tmp_path / "does-not-exist.env")) == {}


def test_load_reloader_args_returns_none_when_missing(tmp_path: pathlib.Path) -> None:
    assert cli._load_reloader_args(str(tmp_path / "nope.json")) is None


def test_load_reloader_args_raises_systemexit_on_invalid_json(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "reloader-args.json"
    p.write_text("not valid json", encoding="utf-8")
    with pytest.raises(SystemExit, match="not valid JSON"):
        cli._load_reloader_args(str(p))


# ---------------------------------------------------------------------------
# user add — headless path
# ---------------------------------------------------------------------------


def test_user_add_writes_index_and_calls_reloader(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    # Patch the Reloader so we don't try to write actual config files.
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0
    idx = state.load_users_index(users_index_path)
    assert "bob" in idx["users"]
    assert idx["users"]["bob"]["enabled"] is True
    fake_reloader.assert_called_once_with()

    out = capsys.readouterr().out
    assert "added user 'bob'" in out
    assert "reality_uuid" in out
    assert "hysteria_password" in out


def test_user_add_rejects_invalid_label(reloader_args_json: str, capsys) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "stealth-vps-default"])
    # Reserved prefix → rejected before mutation.
    assert rc == 1
    fake_reloader.assert_not_called()
    err = capsys.readouterr().err
    assert "invalid" in err


def test_user_add_with_explicit_hysteria_password(
    users_index_path: str,
    reloader_args_json: str,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "bob", "--hysteria-password", "FIXEDPW"])
    assert rc == 0
    idx = state.load_users_index(users_index_path)
    assert idx["users"]["bob"]["hysteria_password"] == "FIXEDPW"


# ---------------------------------------------------------------------------
# user revoke
# ---------------------------------------------------------------------------


def test_user_revoke_flips_enabled_flag(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "revoke", "alice"])

    assert rc == 0
    idx = state.load_users_index(users_index_path)
    assert idx["users"]["alice"]["enabled"] is False
    fake_reloader.assert_called_once_with()
    assert "revoked user 'alice'" in capsys.readouterr().out


def test_user_revoke_unknown_label_errors(reloader_args_json: str, capsys) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "revoke", "never-existed"])
    assert rc == 1
    fake_reloader.assert_not_called()
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# user purge
# ---------------------------------------------------------------------------


def test_user_purge_removes_row_and_calls_reloader(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "purge", "alice"])

    assert rc == 0
    idx = state.load_users_index(users_index_path)
    assert "alice" not in idx["users"]
    fake_reloader.assert_called_once_with()
    assert "purged user 'alice'" in capsys.readouterr().out


def test_user_purge_idempotent_for_missing_label(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "purge", "never-existed"])
    # Exit 0 (no-op success) + helpful message on stdout.
    assert rc == 0
    out = capsys.readouterr().out
    assert "was not in the index" in out
    # Reload still fires — see purge docstring on why.
    fake_reloader.assert_called_once_with()


# ---------------------------------------------------------------------------
# user rotate
# ---------------------------------------------------------------------------


def test_user_rotate_replaces_creds_preserves_created_at(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    before = state.load_users_index(users_index_path)["users"]["alice"]

    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "rotate", "alice"])

    assert rc == 0
    after = state.load_users_index(users_index_path)["users"]["alice"]
    # Creds rolled.
    assert after["reality_uuid"] != before["reality_uuid"]
    assert after["sub_token"] != before["sub_token"]
    assert after["hysteria_password"] != before["hysteria_password"]
    # Anchors preserved.
    assert after["created_at"] == before["created_at"]
    assert after["enabled"] is True

    fake_reloader.assert_called_once_with()
    out = capsys.readouterr().out
    assert "rotated credentials for 'alice'" in out
    assert "preserved" in out
    assert "OLD credentials are now invalid" in out


def test_user_rotate_explicit_hysteria_password(
    users_index_path: str,
    reloader_args_json: str,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main([
            "user", "rotate", "alice",
            "--hysteria-password", "EMERGENCY-FIXED-PW",
        ])
    assert rc == 0
    rec = state.load_users_index(users_index_path)["users"]["alice"]
    assert rec["hysteria_password"] == "EMERGENCY-FIXED-PW"


def test_user_rotate_re_enables_revoked_user(
    users_index_path: str,
    reloader_args_json: str,
) -> None:
    state.revoke_user("alice", users_index_path)
    assert state.get_user("alice", users_index_path)["enabled"] is False

    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "rotate", "alice"])

    assert rc == 0
    assert state.get_user("alice", users_index_path)["enabled"] is True


def test_user_rotate_unknown_label_errors(
    reloader_args_json: str,
    capsys,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "rotate", "never-existed"])
    assert rc == 1
    fake_reloader.assert_not_called()
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# user list
# ---------------------------------------------------------------------------


def test_user_list_table_format(users_index_path: str, capsys) -> None:
    rc = cli.main(["user", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "LABEL" in out  # header row
    assert "STATUS" in out


def test_user_list_json_format_is_ndjson(users_index_path: str, capsys) -> None:
    rc = cli.main(["user", "list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
    assert lines
    assert lines[0]["label"] == "alice"
    assert lines[0]["enabled"] is True


def test_user_list_hides_disabled_by_default(users_index_path: str, capsys) -> None:
    # Revoke alice manually so the table should be empty without --include-disabled
    idx = state.load_users_index(users_index_path)
    idx["users"]["alice"]["enabled"] = False
    state.save_users_index(idx, users_index_path)

    rc = cli.main(["user", "list"])
    assert rc == 0
    assert "no users in the index" in capsys.readouterr().out

    rc = cli.main(["user", "list", "--include-disabled"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "REVOKED" in out


# ---------------------------------------------------------------------------
# user show
# ---------------------------------------------------------------------------


def test_user_show_prints_fields_and_uris(
    users_index_path: str,
    reality_state_yml,
    hysteria_state_yml,
    installer_env_panel_domain,
    capsys,
) -> None:
    rc = cli.main(["user", "show", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "label" in out
    assert "alice" in out
    assert "enabled" in out
    # URI rendering kicked in (host pulled from installer.env).
    assert "vpn.example.com" in out
    assert out.count("vless://") == 1
    assert out.count("hysteria2://") == 1


def test_user_show_unknown_label_errors(users_index_path: str, capsys) -> None:
    rc = cli.main(["user", "show", "ghost"])
    assert rc == 1
    assert "no user labelled 'ghost'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


def test_reload_refuses_in_panel_mode(tmp_path: pathlib.Path, capsys) -> None:
    (tmp_path / "panel.state.yml").write_text("dummy", encoding="utf-8")
    rc = cli.main(["reload"])
    assert rc == 2
    assert "panel mode detected" in capsys.readouterr().err


def test_reload_headless_dry_run_calls_reloader(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["reload", "--dry-run"])
    assert rc == 0
    fake_reloader.assert_called_once_with()
    assert "reload complete (dry-run)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# migrate from-3xui
# ---------------------------------------------------------------------------


def test_migrate_from_3xui_renames_panel_state(
    tmp_path: pathlib.Path,
    users_index_path: str,
    capsys,
) -> None:
    panel_state = tmp_path / "panel.state.yml"
    panel_state.write_text("admin_user: admin\n", encoding="utf-8")

    rc = cli.main(["migrate", "from-3xui"])
    assert rc == 0
    assert not panel_state.exists()
    backups = list(tmp_path.glob("panel.state.yml.before-migrate-*"))
    assert len(backups) == 1
    out = capsys.readouterr().out
    assert "panel mode disabled" in out
    assert "Next steps" in out


def test_migrate_from_3xui_refuses_when_panel_already_absent(capsys) -> None:
    rc = cli.main(["migrate", "from-3xui"])
    assert rc == 1
    assert "not in panel mode" in capsys.readouterr().err


def test_migrate_from_3xui_refuses_when_index_empty(
    tmp_path: pathlib.Path,
    users_index_path: str,
    capsys,
) -> None:
    # Empty the index — migrate should refuse to clobber a panel that
    # has at least the seeded default mirrored, vs. proceeding to a
    # headless config that can't even start.
    (tmp_path / "panel.state.yml").write_text("admin_user: admin\n", encoding="utf-8")
    idx = state.load_users_index(users_index_path)
    idx["users"] = {}
    state.save_users_index(idx, users_index_path)

    rc = cli.main(["migrate", "from-3xui"])
    assert rc == 1
    assert "zero users" in capsys.readouterr().err


def test_migrate_rollback_restores_latest_backup(
    tmp_path: pathlib.Path,
    users_index_path: str,
    capsys,
) -> None:
    panel_state = tmp_path / "panel.state.yml"
    panel_state.write_text("admin_user: admin\n", encoding="utf-8")
    cli.main(["migrate", "from-3xui"])  # creates the backup
    assert not panel_state.exists()

    rc = cli.main(["migrate", "from-3xui", "--rollback"])
    assert rc == 0
    assert panel_state.exists()
    assert panel_state.read_text() == "admin_user: admin\n"
    assert not list(tmp_path.glob("panel.state.yml.before-migrate-*"))
    assert "rolled back" in capsys.readouterr().out


def test_migrate_rollback_with_no_backups_errors(capsys) -> None:
    rc = cli.main(["migrate", "from-3xui", "--rollback"])
    assert rc == 1
    assert "no panel.state.yml.before-migrate" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# user add --ttl  (subscription TTL, schema v2)
# ---------------------------------------------------------------------------


def test_user_add_with_ttl_sets_expiry(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    """`s-vps user add bob --ttl 30d` should write sub_expires_at to the
    index. We don't assert the exact ISO string (it's relative to now);
    we verify the field is non-empty, parseable, and lands ~30d ahead.
    """
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "bob", "--ttl", "30d"])
    assert rc == 0
    rec = state.load_users_index(users_index_path)["users"]["bob"]
    assert rec["sub_expires_at"]
    # Round-trip through the same parser to confirm the value is well-formed
    # and lands in the future (compute_expiry produces "now + ttl" so a 30d
    # TTL must NOT be expired right after creation).
    assert state.is_expired(rec) is False

    out = capsys.readouterr().out
    assert "sub_expires_at" in out
    assert "TTL 30d" in out


def test_user_add_without_ttl_leaves_expiry_none(
    users_index_path: str,
    reloader_args_json: str,
) -> None:
    """The TTL flag is opt-in. Existing operator workflows (no --ttl) must
    keep producing never-expires users — backwards compatibility.
    """
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "bob"])
    assert rc == 0
    rec = state.load_users_index(users_index_path)["users"]["bob"]
    assert rec.get("sub_expires_at") is None


def test_user_add_rejects_garbled_ttl(
    users_index_path: str,
    reloader_args_json: str,
    capsys,
) -> None:
    """`--ttl 30days` is invalid — only the canonical units (s/m/h/d/w/mo/y)
    are accepted. The user record was still created by backend.add (we
    don't unwind that), but a clear stderr message + non-zero exit
    tells the operator the TTL didn't take effect."""
    fake_reloader = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader):
        rc = cli.main(["user", "add", "bob", "--ttl", "30days"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ttl `30days` invalid" in err
    # Record exists but with no expiry set — operator can sub renew it.
    rec = state.load_users_index(users_index_path)["users"]["bob"]
    assert rec.get("sub_expires_at") is None


# ---------------------------------------------------------------------------
# sub renew
# ---------------------------------------------------------------------------


def test_sub_renew_sets_expiry(users_index_path: str, capsys) -> None:
    rc = cli.main(["sub", "renew", "alice", "--ttl", "7d"])
    assert rc == 0
    rec = state.get_user("alice", users_index_path)
    assert rec["sub_expires_at"]
    assert state.is_expired(rec) is False
    out = capsys.readouterr().out
    assert "renewed 'alice'" in out
    assert "+7d" in out


def test_sub_renew_clear_removes_expiry(users_index_path: str, capsys) -> None:
    # Seed an expiry first, then clear it.
    state.update_user("alice", sub_expires_at="2099-01-01T00:00:00Z", path=users_index_path)
    rc = cli.main(["sub", "renew", "alice", "--clear"])
    assert rc == 0
    rec = state.get_user("alice", users_index_path)
    assert rec.get("sub_expires_at") is None
    assert "cleared expiry on 'alice'" in capsys.readouterr().out


def test_sub_renew_inspection_mode(users_index_path: str, capsys) -> None:
    """No --ttl + no --clear → read-only. Prints the current value and
    a hint about how to mutate it. Exit 0 (queries succeed)."""
    state.update_user("alice", sub_expires_at="2099-01-01T00:00:00Z", path=users_index_path)
    rc = cli.main(["sub", "renew", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2099-01-01T00:00:00Z" in out
    assert "Pass --ttl" in out


def test_sub_renew_inspection_says_never_expires_when_unset(
    users_index_path: str,
    capsys,
) -> None:
    rc = cli.main(["sub", "renew", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no expiry" in out
    assert "Pass --ttl" in out


def test_sub_renew_unknown_label_errors(users_index_path: str, capsys) -> None:
    rc = cli.main(["sub", "renew", "ghost", "--ttl", "30d"])
    assert rc == 1
    assert "no user labelled 'ghost'" in capsys.readouterr().err


def test_sub_renew_rejects_garbled_ttl(users_index_path: str, capsys) -> None:
    rc = cli.main(["sub", "renew", "alice", "--ttl", "lots"])
    assert rc == 1
    assert "ttl `lots` invalid" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# sub prune
# ---------------------------------------------------------------------------


def test_sub_prune_deletes_expired_subscription_files(
    users_index_path: str,
    subscriptions_dir: str,
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """Set up: alice's expiry is in the past + her sub file exists on disk.
    Prune should unlink the file (operationally a 404 from Caddy) and
    leave the index row alone (operator can sub renew later).
    """
    # Force the SUBSCRIPTION_DIR import in cmd_sub_prune to point at our
    # tmp dir. We patch at the module the CLI re-imports from.
    from stealth_vps import subscription
    monkeypatch.setattr(subscription, "SUBSCRIPTION_DIR", subscriptions_dir)

    # Set alice's expiry to the past and place a sub file on disk.
    state.update_user("alice", sub_expires_at="2020-01-01T00:00:00Z", path=users_index_path)
    alice = state.get_user("alice", users_index_path)
    sub_file = pathlib.Path(subscriptions_dir) / f"{alice['sub_token']}.txt"
    sub_file.write_text("vless://placeholder", encoding="utf-8")
    assert sub_file.exists()

    rc = cli.main(["sub", "prune", "--verbose"])
    assert rc == 0
    assert not sub_file.exists()
    # Index row preserved — auditable. Only the file is gone.
    assert state.get_user("alice", users_index_path) is not None
    out = capsys.readouterr().out
    assert "pruned 1 expired subscription file" in out


def test_sub_prune_skips_non_expired(
    users_index_path: str,
    subscriptions_dir: str,
    monkeypatch,
    capsys,
) -> None:
    from stealth_vps import subscription
    monkeypatch.setattr(subscription, "SUBSCRIPTION_DIR", subscriptions_dir)

    # alice has no expiry, so prune should leave her sub file alone.
    alice = state.get_user("alice", users_index_path)
    sub_file = pathlib.Path(subscriptions_dir) / f"{alice['sub_token']}.txt"
    sub_file.write_text("vless://placeholder", encoding="utf-8")

    rc = cli.main(["sub", "prune"])
    assert rc == 0
    assert sub_file.exists()


def test_sub_prune_idempotent_when_file_already_missing(
    users_index_path: str,
    subscriptions_dir: str,
    monkeypatch,
    capsys,
) -> None:
    """Re-running prune over an already-pruned token reports zero
    removals. The index row still claims expired, but no file = nothing
    to do."""
    from stealth_vps import subscription
    monkeypatch.setattr(subscription, "SUBSCRIPTION_DIR", subscriptions_dir)
    state.update_user("alice", sub_expires_at="2020-01-01T00:00:00Z", path=users_index_path)

    rc = cli.main(["sub", "prune", "--verbose"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pruned 0 expired" in out


# ---------------------------------------------------------------------------
# argparse plumbing — fail-fast on unknown verbs
# ---------------------------------------------------------------------------


def test_main_unknown_verb_exits_two() -> None:
    # argparse uses exit code 2 for usage errors.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus"])
    assert excinfo.value.code == 2


def test_main_user_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["user"])
    assert excinfo.value.code == 2


def test_main_sub_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["sub"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Control-mode auto-sync (v0.10.0+)
# ---------------------------------------------------------------------------


@pytest.fixture
def control_mode(tmp_path: pathlib.Path):
    """Make `_is_control_box()` return True by removing the placeholder
    reality.state.yml. Returns the fleet_dir path for tests to populate."""
    (tmp_path / "reality.state.yml").unlink()
    fleet_dir = tmp_path / "fleet"
    fleet_dir.mkdir()
    return fleet_dir


def test_control_mode_user_add_skips_reloader_invokes_fleet_sync(
    users_index_path: str, control_mode: pathlib.Path, capsys
) -> None:
    """On a control box, `s-vps user add bob` writes the index, does NOT
    call Reloader (no local Xray), and DOES call fleet.sync_all when
    nodes are registered."""
    from stealth_vps import fleet as _fleet
    _fleet.save_node(
        _fleet.FleetNode(node_id="tokyo-1", ssh_host="h", ssh_key_path="/k"),
        fleet_dir=str(control_mode),
    )

    sync_spy = MagicMock(return_value=[
        _fleet.PushResult("tokyo-1", True, '{"ok":true}', "", 100),
    ])
    # Patch ALL fleet module entry points the helper uses:
    #   load_fleet → discover nodes
    #   sync_all → execute pushes
    with patch("stealth_vps.fleet.load_fleet",
               return_value=[_fleet.FleetNode(node_id="tokyo-1", ssh_host="h", ssh_key_path="/k")]), \
         patch("stealth_vps.fleet.sync_all", sync_spy), \
         patch("stealth_vps.fleet.update_sync_status"):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0
    sync_spy.assert_called_once()
    out = capsys.readouterr().out
    assert "Propagating to 1 data node" in out
    assert "✓ tokyo-1" in out


def test_control_mode_no_sync_flag_skips_propagation(
    users_index_path: str, control_mode: pathlib.Path, capsys
) -> None:
    """`s-vps user add bob --no-sync` writes the index but does NOT
    invoke fleet.sync_all."""
    from stealth_vps import fleet as _fleet
    _fleet.save_node(
        _fleet.FleetNode(node_id="tokyo-1", ssh_host="h", ssh_key_path="/k"),
        fleet_dir=str(control_mode),
    )

    sync_spy = MagicMock()
    with patch("stealth_vps.fleet.sync_all", sync_spy):
        rc = cli.main(["user", "add", "bob", "--no-sync"])

    assert rc == 0
    sync_spy.assert_not_called()
    out = capsys.readouterr().out
    assert "Propagating" not in out


def test_control_mode_no_nodes_skips_propagation(
    users_index_path: str, control_mode: pathlib.Path, capsys
) -> None:
    """Control box with empty fleet/ → no sync (fleet not yet bootstrapped)."""
    sync_spy = MagicMock()
    with patch("stealth_vps.fleet.sync_all", sync_spy):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0
    sync_spy.assert_not_called()


def test_control_mode_partial_sync_failure_does_not_fail_command(
    users_index_path: str, control_mode: pathlib.Path, capsys
) -> None:
    """User add succeeds locally even when 1 of 2 nodes fails to sync.
    Open Question #5 contract: exit 0 (mutation worked), warning on
    stderr, retry on next sync."""
    from stealth_vps import fleet as _fleet
    nodes = [
        _fleet.FleetNode(node_id="ok", ssh_host="h", ssh_key_path="/k"),
        _fleet.FleetNode(node_id="bad", ssh_host="h", ssh_key_path="/k"),
    ]
    for n in nodes:
        _fleet.save_node(n, fleet_dir=str(control_mode))

    def mixed_sync(nodes, *a, **kw):
        return [
            _fleet.PushResult("bad", False, "", "ssh: connect refused", 100),
            _fleet.PushResult("ok", True, '{"ok":true}', "", 50),
        ]

    with patch("stealth_vps.fleet.load_fleet", return_value=nodes), \
         patch("stealth_vps.fleet.sync_all", side_effect=mixed_sync), \
         patch("stealth_vps.fleet.update_sync_status"):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0   # exit 0 — mutation succeeded
    captured = capsys.readouterr()
    assert "✓ ok" in captured.out
    assert "✗ bad" in captured.out
    assert "partial sync" in captured.err


def test_control_mode_user_add_writes_multinode_subscription_file(
    users_index_path: str, control_mode: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """End-to-end: control mode add → multi-node URIs land in the
    subscription file. Asserts the bot_core builder is actually
    called with the fleet nodes (not the single-node URI render)."""
    from stealth_vps import fleet as _fleet
    node = _fleet.FleetNode(
        node_id="tokyo-1",
        ssh_host="h", ssh_key_path="/k",
        reality_public_key="PUBKEY-TOKYO",
        reality_short_id="abc",
        reality_port=43338,
        reality_servernames=["www.example.com"],
        hysteria_port=49440,
        hysteria_obfs_password="OBFS",
        domain="tokyo.example.com",
    )
    _fleet.save_node(node, fleet_dir=str(control_mode))

    write_spy = MagicMock()
    with patch("stealth_vps.fleet.load_fleet", return_value=[node]), \
         patch("stealth_vps.fleet.sync_all",
               return_value=[_fleet.PushResult("tokyo-1", True, "{}", "", 1)]), \
         patch("stealth_vps.fleet.update_sync_status"), \
         patch("stealth_vps.subscription.write_subscription_file", write_spy):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0
    write_spy.assert_called_once()
    args, kwargs = write_spy.call_args
    # args[0] = sub_token; args[1] = uris list
    uris = args[1]
    assert any("PUBKEY-TOKYO" in u for u in uris)
    assert any("tokyo-1" in u for u in uris)


# ---------------------------------------------------------------------------
# fleet rotate-key (v0.10.0 Step 7)
# ---------------------------------------------------------------------------


def test_fleet_rotate_key_unknown_node_errors(tmp_path: pathlib.Path, capsys) -> None:
    rc = cli.main([
        "fleet", "rotate-key", "ghost",
        "--fleet-dir", str(tmp_path), "--keys-dir", str(tmp_path),
    ])
    assert rc == 1
    assert "not registered" in capsys.readouterr().err


def test_fleet_rotate_key_invalid_label(capsys) -> None:
    rc = cli.main(["fleet", "rotate-key", "UPPER"])
    assert rc == 1
    assert "invalid" in capsys.readouterr().err


def test_fleet_rotate_key_refuses_when_old_key_broken(
    tmp_path: pathlib.Path, capsys
) -> None:
    """If `s-vps version` fails over the current key, rotation can't
    safely proceed (no rollback path). Operator gets a clear message
    + the hint to re-bootstrap via `fleet add`."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    keys_dir = tmp_path / "keys"
    for d in (fleet_dir, keys_dir):
        d.mkdir()
    key_path = keys_dir / "control_to_n1.ed25519"
    key_path.write_text("FAKE PRIV", encoding="utf-8")
    (keys_dir / "control_to_n1.ed25519.pub").write_text(
        "ssh-ed25519 AAAA-OLD control_to_n1", encoding="utf-8")

    _fleet.save_node(
        _fleet.FleetNode(
            node_id="n1", ssh_host="h", ssh_key_path=str(key_path),
        ),
        fleet_dir=str(fleet_dir),
    )

    # Probe returns None → version check fails.
    with patch.object(cli, "_probe_remote_version", return_value=None):
        rc = cli.main([
            "fleet", "rotate-key", "n1",
            "--fleet-dir", str(fleet_dir),
            "--keys-dir", str(keys_dir),
        ])
    assert rc == 1
    assert "current key" in capsys.readouterr().err
    # Old key files preserved.
    assert key_path.exists()


def test_fleet_rotate_key_happy_path(
    tmp_path: pathlib.Path, capsys
) -> None:
    """End-to-end with subprocess mocks. Sequence:
        1. _probe_remote_version (OLD key) → success
        2. _generate_ed25519_keypair → writes .new files
        3. _install_restricted_authorized_keys (OLD key) → success
        4. _probe_remote_version (NEW key) → success
        5. _remove_pubkey_from_authorized_keys (NEW key, OLD body) → success
        6. local atomic-replace
    """
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    keys_dir = tmp_path / "keys"
    for d in (fleet_dir, keys_dir):
        d.mkdir()
    key_path = keys_dir / "control_to_n1.ed25519"
    pub_path = keys_dir / "control_to_n1.ed25519.pub"
    key_path.write_text("OLD-PRIV", encoding="utf-8")
    pub_path.write_text("ssh-ed25519 AAAA-OLD control_to_n1", encoding="utf-8")
    _fleet.save_node(
        _fleet.FleetNode(
            node_id="n1", ssh_host="h", ssh_key_path=str(key_path),
        ),
        fleet_dir=str(fleet_dir),
    )

    def fake_keygen(path: str) -> None:
        pathlib.Path(path).write_text("NEW-PRIV", encoding="utf-8")
        pathlib.Path(f"{path}.pub").write_text(
            "ssh-ed25519 AAAA-NEW control_to_n1", encoding="utf-8")

    with patch.object(cli, "_probe_remote_version", return_value="v0.10.0"), \
         patch.object(cli, "_generate_ed25519_keypair", side_effect=fake_keygen), \
         patch.object(cli, "_install_restricted_authorized_keys"), \
         patch.object(cli, "_remove_pubkey_from_authorized_keys"):
        rc = cli.main([
            "fleet", "rotate-key", "n1",
            "--fleet-dir", str(fleet_dir),
            "--keys-dir", str(keys_dir),
        ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rotated key" in out
    # Local files were atomic-replaced.
    assert key_path.read_text() == "NEW-PRIV"
    assert "NEW" in pub_path.read_text()
    # .new files cleaned up.
    assert not (keys_dir / "control_to_n1.ed25519.new").exists()


def test_fleet_rotate_key_rolls_back_on_new_key_probe_failure(
    tmp_path: pathlib.Path, capsys
) -> None:
    """New key didn't take (probe returns None after install). Old key
    files MUST stay intact, .new files cleaned up, and the new pubkey
    must be removed from authorized_keys via the OLD key."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    keys_dir = tmp_path / "keys"
    for d in (fleet_dir, keys_dir):
        d.mkdir()
    key_path = keys_dir / "control_to_n1.ed25519"
    pub_path = keys_dir / "control_to_n1.ed25519.pub"
    key_path.write_text("OLD-PRIV", encoding="utf-8")
    pub_path.write_text("ssh-ed25519 AAAA-OLD control_to_n1", encoding="utf-8")
    _fleet.save_node(
        _fleet.FleetNode(
            node_id="n1", ssh_host="h", ssh_key_path=str(key_path),
        ),
        fleet_dir=str(fleet_dir),
    )

    probe_results = ["v0.10.0", None]   # old works, new doesn't
    def fake_probe(node):
        return probe_results.pop(0)

    def fake_keygen(path: str) -> None:
        pathlib.Path(path).write_text("NEW-PRIV", encoding="utf-8")
        pathlib.Path(f"{path}.pub").write_text(
            "ssh-ed25519 AAAA-NEW control_to_n1", encoding="utf-8")

    rollback_spy = MagicMock()
    with patch.object(cli, "_probe_remote_version", side_effect=fake_probe), \
         patch.object(cli, "_generate_ed25519_keypair", side_effect=fake_keygen), \
         patch.object(cli, "_install_restricted_authorized_keys"), \
         patch.object(cli, "_remove_pubkey_from_authorized_keys",
                       side_effect=rollback_spy):
        rc = cli.main([
            "fleet", "rotate-key", "n1",
            "--fleet-dir", str(fleet_dir),
            "--keys-dir", str(keys_dir),
        ])
    assert rc == 1
    # Old key still in place.
    assert key_path.read_text() == "OLD-PRIV"
    # .new files cleaned up.
    assert not (keys_dir / "control_to_n1.ed25519.new").exists()
    # Rollback called once to remove the new pubkey via the OLD key.
    rollback_spy.assert_called_once()


def test_data_node_mode_unchanged_by_step6(
    users_index_path: str, reloader_args_json: str, capsys
) -> None:
    """Sanity: with reality.state.yml present (data-node / single-node),
    user add still calls Reloader and does NOT touch the fleet module
    even if fleet/ happens to exist (which it shouldn't on a data node,
    but defend against operator misconfig)."""
    fake_reloader = MagicMock()
    sync_spy = MagicMock()
    with patch.object(cli, "_build_reloader", return_value=fake_reloader), \
         patch("stealth_vps.fleet.sync_all", sync_spy):
        rc = cli.main(["user", "add", "bob"])

    assert rc == 0
    fake_reloader.assert_called_once()
    sync_spy.assert_not_called()      # data node never sync's outward


# ---------------------------------------------------------------------------
# fleet list / remove (v0.10.0+)
# ---------------------------------------------------------------------------


def test_fleet_list_empty(tmp_path: pathlib.Path, capsys) -> None:
    rc = cli.main(["fleet", "list", "--fleet-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no nodes registered" in out


def test_fleet_list_table_format(tmp_path: pathlib.Path, capsys) -> None:
    from stealth_vps import fleet as _fleet
    _fleet.save_node(
        _fleet.FleetNode(
            node_id="tokyo-1",
            ssh_host="10.0.0.1",
            ssh_port=22,
            last_sync_status="ok",
            last_sync_at="2026-05-21T10:00:00Z",
        ),
        fleet_dir=str(tmp_path),
    )
    _fleet.save_node(
        _fleet.FleetNode(node_id="amsterdam-1", ssh_host="10.0.0.2"),
        fleet_dir=str(tmp_path),
    )
    rc = cli.main(["fleet", "list", "--fleet-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NODE_ID" in out                      # header
    assert "tokyo-1" in out and "amsterdam-1" in out
    assert "(never)" in out                      # amsterdam never synced
    # Alphabetic sort puts amsterdam first.
    assert out.index("amsterdam-1") < out.index("tokyo-1")


def test_fleet_list_json_emits_ndjson(tmp_path: pathlib.Path, capsys) -> None:
    from stealth_vps import fleet as _fleet
    _fleet.save_node(
        _fleet.FleetNode(node_id="n1", ssh_host="h"),
        fleet_dir=str(tmp_path),
    )
    rc = cli.main(["fleet", "list", "--json", "--fleet-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
    assert lines and lines[0]["node_id"] == "n1"


def test_fleet_remove_deletes_yaml_and_key(tmp_path: pathlib.Path, capsys) -> None:
    """Default behavior: yaml + ssh key both removed."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    keys_dir = tmp_path / "keys"
    fleet_dir.mkdir()
    keys_dir.mkdir()
    _fleet.save_node(
        _fleet.FleetNode(node_id="n1", ssh_host="h"),
        fleet_dir=str(fleet_dir),
    )
    key_path = keys_dir / "control_to_n1.ed25519"
    key_path.write_text("FAKE PRIV", encoding="utf-8")
    (keys_dir / "control_to_n1.ed25519.pub").write_text("FAKE PUB", encoding="utf-8")

    rc = cli.main([
        "fleet", "remove", "n1",
        "--fleet-dir", str(fleet_dir),
        "--keys-dir", str(keys_dir),
    ])
    assert rc == 0
    assert not (fleet_dir / "n1.yml").exists()
    assert not key_path.exists()
    assert "unregistered" in capsys.readouterr().out


def test_fleet_remove_keep_key_preserves_ssh_files(tmp_path: pathlib.Path) -> None:
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    keys_dir = tmp_path / "keys"
    fleet_dir.mkdir()
    keys_dir.mkdir()
    _fleet.save_node(
        _fleet.FleetNode(node_id="n1", ssh_host="h"),
        fleet_dir=str(fleet_dir),
    )
    key_path = keys_dir / "control_to_n1.ed25519"
    key_path.write_text("FAKE PRIV", encoding="utf-8")

    rc = cli.main([
        "fleet", "remove", "n1", "--keep-key",
        "--fleet-dir", str(fleet_dir),
        "--keys-dir", str(keys_dir),
    ])
    assert rc == 0
    assert not (fleet_dir / "n1.yml").exists()
    assert key_path.exists()


def test_fleet_remove_idempotent_for_missing_node(tmp_path: pathlib.Path, capsys) -> None:
    rc = cli.main([
        "fleet", "remove", "ghost",
        "--fleet-dir", str(tmp_path),
        "--keys-dir", str(tmp_path),
    ])
    assert rc == 0
    assert "no-op" in capsys.readouterr().out


def test_fleet_add_rejects_invalid_label(capsys) -> None:
    rc = cli.main([
        "fleet", "add", "UPPER-CASE",
        "--ssh-host", "10.0.0.1",
    ])
    assert rc == 1
    assert "invalid" in capsys.readouterr().err


def test_fleet_add_refuses_when_already_registered(
    tmp_path: pathlib.Path, capsys
) -> None:
    from stealth_vps import fleet as _fleet
    _fleet.save_node(
        _fleet.FleetNode(node_id="dup", ssh_host="10.0.0.1"),
        fleet_dir=str(tmp_path),
    )
    rc = cli.main([
        "fleet", "add", "dup",
        "--ssh-host", "10.0.0.2",
        "--fleet-dir", str(tmp_path),
        "--keys-dir", str(tmp_path),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already registered" in err


# ---------------------------------------------------------------------------
# fleet sync — mocked push_to_node
# ---------------------------------------------------------------------------


def test_fleet_sync_no_nodes_succeeds(tmp_path: pathlib.Path, capsys) -> None:
    rc = cli.main(["fleet", "sync", "--fleet-dir", str(tmp_path)])
    assert rc == 0
    assert "no nodes to sync" in capsys.readouterr().out


def test_fleet_sync_dry_run_invokes_no_ssh(
    users_index_path: str, tmp_path: pathlib.Path, capsys
) -> None:
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    fleet_dir.mkdir()
    _fleet.save_node(
        _fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k"),
        fleet_dir=str(fleet_dir),
    )
    with patch("subprocess.run") as spy:
        rc = cli.main([
            "fleet", "sync", "--dry-run", "--fleet-dir", str(fleet_dir),
        ])
    assert rc == 0
    spy.assert_not_called()
    out = capsys.readouterr().out
    assert "n1" in out


def test_fleet_sync_partial_failure_exits_1(
    users_index_path: str, tmp_path: pathlib.Path, capsys
) -> None:
    """One node ok, one failing → exit 1 so cron / CI notices."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    fleet_dir.mkdir()
    for nid in ("ok-node", "bad-node"):
        _fleet.save_node(
            _fleet.FleetNode(node_id=nid, ssh_host="h", ssh_key_path="/k"),
            fleet_dir=str(fleet_dir),
        )

    def mixed(node, *a, **kw):
        if node.node_id == "bad-node":
            return _fleet.PushResult(node.node_id, False, "", "ssh: connect refused", 100)
        return _fleet.PushResult(node.node_id, True, '{"ok":true}', "", 200)

    with patch("stealth_vps.fleet.push_to_node", side_effect=mixed):
        rc = cli.main(["fleet", "sync", "--fleet-dir", str(fleet_dir)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "✓ ok" in out and "✗ FAIL" in out
    # The status was also persisted back into the per-node yaml.
    bad = _fleet.load_node("bad-node", fleet_dir=str(fleet_dir))
    assert bad.last_sync_status == "failed"


def test_fleet_sync_node_filter(
    users_index_path: str, tmp_path: pathlib.Path
) -> None:
    """--node X only pushes to that one node."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    fleet_dir.mkdir()
    for nid in ("a", "b", "c"):
        _fleet.save_node(
            _fleet.FleetNode(node_id=nid, ssh_host="h", ssh_key_path="/k"),
            fleet_dir=str(fleet_dir),
        )

    seen: list[str] = []
    def spy(node, *a, **kw):
        seen.append(node.node_id)
        return _fleet.PushResult(node.node_id, True, "", "", 1)

    with patch("stealth_vps.fleet.push_to_node", side_effect=spy):
        rc = cli.main([
            "fleet", "sync", "--node", "b", "--fleet-dir", str(fleet_dir),
        ])
    assert rc == 0
    assert seen == ["b"]


def test_fleet_sync_node_filter_unknown_errors(
    tmp_path: pathlib.Path, capsys
) -> None:
    """--node X where X isn't registered should error, not silently succeed."""
    from stealth_vps import fleet as _fleet
    fleet_dir = tmp_path / "fleet"
    fleet_dir.mkdir()
    _fleet.save_node(
        _fleet.FleetNode(node_id="a", ssh_host="h"),
        fleet_dir=str(fleet_dir),
    )
    rc = cli.main([
        "fleet", "sync", "--node", "ghost", "--fleet-dir", str(fleet_dir),
    ])
    assert rc == 1
    assert "no node 'ghost'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# fleet-receive (data-node side, called via SSH stdin)
# ---------------------------------------------------------------------------


class _FakeStdin:
    """sys.stdin replacement for fleet-receive tests. Pytest replaces
    the real sys.stdin with a `DontReadFromInput` that has `buffer`
    as a read-only property — can't monkeypatch that attribute.
    Workaround: swap the whole `sys.stdin` for an object that has
    its own `buffer` (a BytesIO)."""
    def __init__(self, payload: bytes) -> None:
        self.buffer = io.BytesIO(payload)


def _stdin_with(payload: bytes, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))


def test_fleet_receive_installs_index(
    users_index_path: str, tmp_path: pathlib.Path, capsys, monkeypatch
) -> None:
    """Happy path: valid v2 payload on stdin, no reloader-args.json
    (so reload step is skipped — control-box-style invocation). Index
    should land on disk + 1-line JSON status on stdout."""
    payload = json.dumps({
        "version": 2,
        "users": {
            "alice": {
                "reality_uuid": "00000000-0000-0000-0000-000000000001",
                "hysteria_password": "pw",
                "sub_token": "tok",
                "created_at": "2026-05-21T10:00:00Z",
                "enabled": True,
                "sub_expires_at": None,
            }
        },
    }).encode("utf-8")
    _stdin_with(payload, monkeypatch)

    rc = cli.main(["fleet-receive"])
    assert rc == 0
    # Index now contains alice.
    idx = state.load_users_index(users_index_path)
    assert "alice" in idx["users"]
    # Stdout has a 1-line JSON status with user_count=1.
    line = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["ok"] is True
    assert parsed["user_count"] == 1
    assert parsed["reload"] == "skipped"   # no reloader-args.json in test


def test_fleet_receive_rejects_empty_stdin(
    users_index_path: str, capsys, monkeypatch
) -> None:
    _stdin_with(b"", monkeypatch)
    rc = cli.main(["fleet-receive"])
    assert rc == 1
    assert "empty stdin" in capsys.readouterr().err


def test_fleet_receive_rejects_non_json(
    users_index_path: str, capsys, monkeypatch
) -> None:
    _stdin_with(b"this is not json", monkeypatch)
    rc = cli.main(["fleet-receive"])
    assert rc == 1
    assert "not JSON" in capsys.readouterr().err


def test_fleet_receive_rejects_wrong_schema_version(
    users_index_path: str, capsys, monkeypatch
) -> None:
    _stdin_with(json.dumps({"version": 99, "users": {}}).encode("utf-8"), monkeypatch)
    rc = cli.main(["fleet-receive"])
    assert rc == 1
    assert "unsupported schema" in capsys.readouterr().err


def test_fleet_receive_rejects_non_object_payload(
    users_index_path: str, capsys, monkeypatch
) -> None:
    _stdin_with(json.dumps(["a", "list"]).encode("utf-8"), monkeypatch)
    rc = cli.main(["fleet-receive"])
    assert rc == 1
    assert "must be an object" in capsys.readouterr().err


def test_fleet_receive_rejects_non_mapping_users(
    users_index_path: str, capsys, monkeypatch
) -> None:
    _stdin_with(
        json.dumps({"version": 2, "users": ["alice"]}).encode("utf-8"),
        monkeypatch,
    )
    rc = cli.main(["fleet-receive"])
    assert rc == 1
    assert "users" in capsys.readouterr().err.lower()
