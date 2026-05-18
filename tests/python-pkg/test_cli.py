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

import json
import pathlib
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
    """
    monkeypatch.setattr(cli, "PANEL_STATE_PATH", str(tmp_path / "panel.state.yml"))
    monkeypatch.setattr(cli, "RELOADER_ARGS_PATH", str(tmp_path / "reloader-args.json"))
    monkeypatch.setattr(cli, "INSTALLER_ENV_PATH", str(tmp_path / "installer.env"))
    monkeypatch.setattr(cli, "REALITY_STATE_PATH", str(tmp_path / "reality.state.yml"))
    monkeypatch.setattr(cli, "HYSTERIA_STATE_PATH", str(tmp_path / "hysteria.state.yml"))
    monkeypatch.setattr(state, "USERS_INDEX_PATH", users_index_path)


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
