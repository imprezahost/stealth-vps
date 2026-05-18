"""Tests for stealth_vps.reloader.

The reloader is the half of HeadlessBackend that B1 left as a no-op
hook. These tests lock the rendered config shape (the molecule headless
verify spot-checks the same JSON keys / YAML mappings) and the
write-then-reload sequence.

Coverage targets:
  * `_parse_simple_yaml`           — narrow YAML reader for state files
  * `_emit_yaml` / `_emit_scalar`  — hand-rolled YAML emitter
  * `render_xray_config(_text)`    — multi-client Reality config.json
  * `render_hysteria_config(_text)`— password vs userpass auth modes
  * `_write_atomic`                — temp-file + os.replace
  * `reload_service`               — subprocess wrapper, dry-run path
  * `Reloader.__call__`            — end-to-end render → write → reload
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import reloader, state


# os.chmod on Windows only flips the read-only bit (not full POSIX
# modes). The CI runner is Linux Alpine, so any mode-checking test
# would pass there but produce 0o666 on local Windows dev runs.
_skip_windows_chmod = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits don't apply on Windows os.chmod",
)


# ---------------------------------------------------------------------------
# _parse_simple_yaml
# ---------------------------------------------------------------------------


def test_parse_simple_yaml_handles_str_int_bool() -> None:
    text = """
    # comment line — should be skipped
    port: 12345
    enabled: true
    disabled: false
    label: stealth-vps-default
    quoted_double: "has: colon"
    quoted_single: 'also: colon'
    """
    out = reloader._parse_simple_yaml(text)
    assert out["port"] == 12345
    assert out["enabled"] is True
    assert out["disabled"] is False
    assert out["label"] == "stealth-vps-default"
    assert out["quoted_double"] == "has: colon"
    assert out["quoted_single"] == "also: colon"


def test_parse_simple_yaml_blank_lines_and_comments_skipped() -> None:
    text = "\n\n# leading comment\nfoo: bar\n# trailing comment\n\n"
    assert reloader._parse_simple_yaml(text) == {"foo": "bar"}


def test_parse_simple_yaml_missing_colon_raises() -> None:
    with pytest.raises(reloader.ReloadError, match="missing `:`"):
        reloader._parse_simple_yaml("not_a_mapping_line")


# ---------------------------------------------------------------------------
# load_state_file
# ---------------------------------------------------------------------------


def test_load_state_file_returns_dict(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "reality.state.yml"
    p.write_text("port: 51820\nshort_id: abcd1234\n", encoding="utf-8")
    assert reloader.load_state_file(str(p)) == {"port": 51820, "short_id": "abcd1234"}


def test_load_state_file_missing_raises_reloaderror(tmp_path: pathlib.Path) -> None:
    with pytest.raises(reloader.ReloadError, match="missing"):
        reloader.load_state_file(str(tmp_path / "nope.yml"))


# ---------------------------------------------------------------------------
# _emit_yaml — sanity for the narrow Hysteria-shape we emit
# ---------------------------------------------------------------------------


def test_emit_yaml_flat_mapping() -> None:
    out = reloader._emit_yaml({"a": 1, "b": "x", "c": True})
    # Both keys and values are double-quoted (json.dumps form) so a
    # YAML 1.1 reader can't accidentally bool-coerce a username
    # called `yes`/`no`/`on`/`off`. Ints + bools stay bare.
    assert out == '"a": 1\n"b": "x"\n"c": true'


def test_emit_yaml_nested_mapping_indents() -> None:
    out = reloader._emit_yaml({"outer": {"inner": 42}})
    assert out == '"outer":\n  "inner": 42'


def test_emit_yaml_rejects_non_mapping_top_level() -> None:
    with pytest.raises(TypeError, match="top-level must be a mapping"):
        reloader._emit_yaml([1, 2, 3])


def test_emit_yaml_rejects_unsupported_scalar() -> None:
    with pytest.raises(TypeError, match="YAML scalar"):
        reloader._emit_yaml({"k": object()})


def test_emit_scalar_quotes_yaml11_traps() -> None:
    # YAML 1.1's "no"/"yes"/"off"/"on" → bool surprise. We quote so the
    # parser keeps them as strings.
    assert reloader._emit_scalar("no") == '"no"'
    assert reloader._emit_scalar("yes") == '"yes"'
    assert reloader._emit_scalar("off") == '"off"'


# ---------------------------------------------------------------------------
# render_xray_config
# ---------------------------------------------------------------------------


_REALITY_STATE = {
    "port": 51820,
    "private_key": "PRIVATEKEYBASE64",
    "short_id": "ab12cd34",
    "client_uuid": "00000000-0000-0000-0000-000000000001",
}


def test_render_xray_config_single_client_shape() -> None:
    users = [("stealth-vps-default", {"reality_uuid": _REALITY_STATE["client_uuid"]})]
    cfg = reloader.render_xray_config(_REALITY_STATE, users)

    assert cfg["log"] == {"loglevel": "warning"}
    inbound = cfg["inbounds"][0]
    assert inbound["protocol"] == "vless"
    assert inbound["port"] == 51820
    assert inbound["streamSettings"]["security"] == "reality"
    assert inbound["streamSettings"]["realitySettings"]["privateKey"] == "PRIVATEKEYBASE64"
    assert inbound["streamSettings"]["realitySettings"]["shortIds"] == ["ab12cd34"]
    assert inbound["settings"]["clients"] == [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "email": "stealth-vps-default",
            "flow": "xtls-rprx-vision",
        }
    ]


def test_render_xray_config_multi_client() -> None:
    users = [
        ("alice", {"reality_uuid": "uuid-alice"}),
        ("bob", {"reality_uuid": "uuid-bob"}),
        ("carol", {"reality_uuid": "uuid-carol"}),
    ]
    cfg = reloader.render_xray_config(_REALITY_STATE, users)
    clients = cfg["inbounds"][0]["settings"]["clients"]
    assert len(clients) == 3
    assert [c["email"] for c in clients] == ["alice", "bob", "carol"]
    assert [c["id"] for c in clients] == ["uuid-alice", "uuid-bob", "uuid-carol"]
    for c in clients:
        assert c["flow"] == "xtls-rprx-vision"


def test_render_xray_config_empty_user_list_raises() -> None:
    with pytest.raises(reloader.ReloadError, match="empty user list"):
        reloader.render_xray_config(_REALITY_STATE, [])


def test_render_xray_config_user_without_uuid_raises() -> None:
    with pytest.raises(reloader.ReloadError, match="missing reality_uuid"):
        reloader.render_xray_config(_REALITY_STATE, [("alice", {})])


def test_render_xray_config_missing_reality_field_raises() -> None:
    with pytest.raises(reloader.ReloadError, match="reality state missing"):
        reloader.render_xray_config(
            {"port": 443},  # no private_key / short_id
            [("alice", {"reality_uuid": "u"})],
        )


def test_render_xray_config_text_is_valid_json_and_deterministic() -> None:
    users = [("alice", {"reality_uuid": "u1"})]
    text1 = reloader.render_xray_config_text(_REALITY_STATE, users)
    text2 = reloader.render_xray_config_text(_REALITY_STATE, users)
    assert text1 == text2  # sort_keys + identical inputs → byte-identical
    json.loads(text1)  # actually parseable


def test_render_xray_config_text_matches_ansible_template_keys() -> None:
    """The jinja template in templates/xray-config.json.j2 hard-codes a
    specific set of top-level + inbound keys; the molecule verify
    `assert (idx.inbounds[0].settings.clients[0].flow == ...)` style
    assertion depends on these. Locking the key set here means breaking
    the parity surfaces as a unit-test failure, not a molecule failure.
    """
    text = reloader.render_xray_config_text(
        _REALITY_STATE,
        [("stealth-vps-default", {"reality_uuid": _REALITY_STATE["client_uuid"]})],
    )
    cfg = json.loads(text)
    assert set(cfg.keys()) == {"log", "inbounds", "outbounds", "routing"}
    inbound = cfg["inbounds"][0]
    assert set(inbound.keys()) == {
        "tag",
        "listen",
        "port",
        "protocol",
        "settings",
        "streamSettings",
        "sniffing",
    }


# ---------------------------------------------------------------------------
# render_hysteria_config
# ---------------------------------------------------------------------------


_HYSTERIA_STATE = {
    "port": 36000,
    "auth_password": "SHAREDPW",
    "obfs_password": "OBFSPW",
}

_HYSTERIA_KW = dict(
    tls_cert="/etc/hysteria/cert.crt",
    tls_key="/etc/hysteria/cert.key",
    masquerade_url="https://www.bing.com",
    bandwidth_up="100 mbps",
    bandwidth_down="100 mbps",
)


def test_render_hysteria_password_mode() -> None:
    cfg = reloader.render_hysteria_config(
        _HYSTERIA_STATE,
        [("alice", {"hysteria_password": "alice-pw"})],
        per_user=False,
        **_HYSTERIA_KW,
    )
    assert cfg["listen"] == ":36000"
    assert cfg["auth"] == {"type": "password", "password": "SHAREDPW"}
    # obfs + masquerade present
    assert cfg["obfs"]["salamander"]["password"] == "OBFSPW"
    assert cfg["masquerade"]["proxy"]["url"] == "https://www.bing.com"


def test_render_hysteria_password_mode_missing_shared_password_raises() -> None:
    state_no_pw = {k: v for k, v in _HYSTERIA_STATE.items() if k != "auth_password"}
    with pytest.raises(reloader.ReloadError, match="no auth_password"):
        reloader.render_hysteria_config(
            state_no_pw, [("a", {})], per_user=False, **_HYSTERIA_KW,
        )


def test_render_hysteria_userpass_mode() -> None:
    cfg = reloader.render_hysteria_config(
        _HYSTERIA_STATE,
        [
            ("alice", {"hysteria_password": "alice-pw"}),
            ("bob", {"hysteria_password": "bob-pw"}),
        ],
        per_user=True,
        **_HYSTERIA_KW,
    )
    assert cfg["auth"]["type"] == "userpass"
    assert cfg["auth"]["userpass"] == {"alice": "alice-pw", "bob": "bob-pw"}


def test_render_hysteria_userpass_skips_empty_passwords() -> None:
    # Users imported from panel mode before per-user landed may have an
    # empty hysteria_password; the render skips them so hysteria-server
    # doesn't get `userpass: { alice: "" }` (which would let anyone in
    # as "alice" with an empty password).
    cfg = reloader.render_hysteria_config(
        _HYSTERIA_STATE,
        [
            ("alice", {"hysteria_password": ""}),
            ("bob", {"hysteria_password": "bob-pw"}),
        ],
        per_user=True,
        **_HYSTERIA_KW,
    )
    assert cfg["auth"]["userpass"] == {"bob": "bob-pw"}


def test_render_hysteria_userpass_all_empty_raises() -> None:
    with pytest.raises(reloader.ReloadError, match="no users with passwords"):
        reloader.render_hysteria_config(
            _HYSTERIA_STATE,
            [("alice", {"hysteria_password": ""})],
            per_user=True,
            **_HYSTERIA_KW,
        )


def test_render_hysteria_traffic_stats_when_metrics_on() -> None:
    cfg = reloader.render_hysteria_config(
        _HYSTERIA_STATE,
        [("alice", {"hysteria_password": "pw"})],
        per_user=True,
        metrics_enabled=True,
        traffic_stats_listen="127.0.0.1:7777",
        **_HYSTERIA_KW,
    )
    assert cfg["trafficStats"] == {"listen": "127.0.0.1:7777"}


def test_render_hysteria_text_emits_string_quoted_passwords() -> None:
    text = reloader.render_hysteria_config_text(
        _HYSTERIA_STATE,
        [("alice", {"hysteria_password": "alice-pw"})],
        per_user=True,
        **_HYSTERIA_KW,
    )
    # YAML 1.1 quirk-proofing: every string is double-quoted — both the
    # `alice` username (as a key) and the `alice-pw` password value.
    assert '"alice-pw"' in text
    assert '"alice":' in text  # quoted key
    assert '"type":' in text  # nested key also quoted
    # Bools / ints stay bare (no surrounding quotes).
    assert ": true" in text


# ---------------------------------------------------------------------------
# _write_atomic
# ---------------------------------------------------------------------------


@_skip_windows_chmod
def test_write_atomic_creates_file_with_mode(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "out.json"
    reloader._write_atomic(str(target), '{"a":1}', mode=0o640)
    assert target.read_text() == '{"a":1}\n'  # trailing newline added
    # The mode bits should be exactly 0640 (mask away any high bits).
    assert (target.stat().st_mode & 0o777) == 0o640


def test_write_atomic_creates_file_contents_only(tmp_path: pathlib.Path) -> None:
    """Mode-agnostic version of the previous test — Windows local dev
    can still run this one even though chmod is a no-op there.
    """
    target = tmp_path / "out.json"
    reloader._write_atomic(str(target), '{"a":1}', mode=0o640)
    assert target.read_text() == '{"a":1}\n'


def test_write_atomic_replaces_existing_file(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old content")
    reloader._write_atomic(str(target), "new content", mode=0o644)
    assert target.read_text() == "new content\n"


def test_write_atomic_no_temp_files_left_behind_on_success(tmp_path: pathlib.Path) -> None:
    reloader._write_atomic(str(tmp_path / "out.txt"), "x", mode=0o644)
    # Only the target should remain — no `.reloader.<random>.tmp` leftovers.
    entries = list(tmp_path.iterdir())
    assert [e.name for e in entries] == ["out.txt"]


def test_write_atomic_unknown_group_is_silent(tmp_path: pathlib.Path) -> None:
    # Test runners typically don't have an `xray` group. The write should
    # still succeed; the group lookup just gets swallowed.
    target = tmp_path / "out.txt"
    reloader._write_atomic(
        str(target), "x", mode=0o644, group="definitely-not-a-real-group"
    )
    assert target.read_text() == "x\n"


# ---------------------------------------------------------------------------
# reload_service
# ---------------------------------------------------------------------------


def test_reload_service_dry_run_skips_subprocess() -> None:
    with patch("subprocess.run") as mock_run:
        reloader.reload_service("xray.service", dry_run=True)
        mock_run.assert_not_called()


def test_reload_service_invokes_systemctl_reload() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        reloader.reload_service("xray.service")
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == ["systemctl", "reload", "xray.service"]


def test_reload_service_restart_mode_for_xray() -> None:
    """xray-core ignores SIGHUP; `mode="restart"` is what the Reloader
    actually uses to make xray pick up a new config. Test the mode
    dispatch directly so a future signature change is caught here.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        reloader.reload_service("xray.service", mode="restart")
        args, _ = mock_run.call_args
        assert args[0] == ["systemctl", "restart", "xray.service"]


def test_reload_service_rejects_bad_mode() -> None:
    with pytest.raises(ValueError, match="must be 'reload' or 'restart'"):
        reloader.reload_service("xray.service", mode="recycle")


def test_reload_service_raises_on_nonzero_exit() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["systemctl", "reload", "xray.service"], stderr="nope"
        )
        with pytest.raises(reloader.ReloadError, match="rc=1"):
            reloader.reload_service("xray.service")


def test_reload_service_raises_when_systemctl_missing() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("systemctl")):
        with pytest.raises(reloader.ReloadError, match="systemctl not found"):
            reloader.reload_service("xray.service")


# ---------------------------------------------------------------------------
# Reloader — end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def reloader_paths(tmp_path: pathlib.Path, users_index_path: str) -> dict[str, str]:
    """Build a self-contained set of paths under tmp_path so a Reloader
    can be constructed without touching /etc.
    """
    reality_state = tmp_path / "reality.state.yml"
    reality_state.write_text(
        "port: 51820\n"
        "private_key: PRIVATEKEYBASE64\n"
        "short_id: ab12cd34\n"
        'client_uuid: "00000000-0000-0000-0000-000000000001"\n',
        encoding="utf-8",
    )
    hy_state = tmp_path / "hysteria.state.yml"
    hy_state.write_text(
        "port: 36000\nauth_password: SHAREDPW\nobfs_password: OBFSPW\n",
        encoding="utf-8",
    )
    xray_cfg = tmp_path / "xray-config.json"
    hy_cfg = tmp_path / "hysteria-config.yaml"
    return {
        "users_index_path": users_index_path,
        "reality_state_path": str(reality_state),
        "hysteria_state_path": str(hy_state),
        "xray_config_path": str(xray_cfg),
        "hysteria_config_path": str(hy_cfg),
    }


def test_reloader_call_renders_xray_and_reloads_service(reloader_paths: dict[str, str]) -> None:
    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_state_path=reloader_paths["reality_state_path"],
        xray_config_path=reloader_paths["xray_config_path"],
        xray_group=None,  # no `xray` group on the test runner
        reality_enabled=True,
        hysteria_enabled=False,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        r()

    # Config file written + valid JSON with our seeded user.
    cfg = json.loads(pathlib.Path(reloader_paths["xray_config_path"]).read_text())
    assert cfg["inbounds"][0]["settings"]["clients"][0]["email"] == "alice"

    # systemctl restart xray was called exactly once. Xray-core has no
    # SIGHUP handler, so the Reloader uses `restart` (not `reload`) —
    # otherwise SIGHUP from the legacy ExecReload= just terminates the
    # process and leaves it inactive.
    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    assert args[0] == ["systemctl", "restart", "xray.service"]


def test_reloader_call_renders_hysteria_userpass(reloader_paths: dict[str, str]) -> None:
    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_enabled=False,
        hysteria_enabled=True,
        hysteria_state_path=reloader_paths["hysteria_state_path"],
        hysteria_config_path=reloader_paths["hysteria_config_path"],
        hysteria_group=None,
        hysteria_per_user=True,
        hysteria_tls_cert="/x/cert.crt",
        hysteria_tls_key="/x/cert.key",
        hysteria_masquerade_url="https://bing.com",
        hysteria_bandwidth_up="100 mbps",
        hysteria_bandwidth_down="100 mbps",
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        r()

    text = pathlib.Path(reloader_paths["hysteria_config_path"]).read_text()
    # alice was seeded with hysteria_password "alice-hy2-pw"; userpass
    # mode should list her.
    assert "alice" in text
    assert "alice-hy2-pw" in text
    assert "userpass" in text

    # Hysteria 2 also doesn't support hot reload — its SIGHUP handler
    # routes into the same graceful-shutdown path as SIGTERM. So the
    # Reloader uses `restart` for both services. Test asserts the
    # exact call so a future flip to real-reload (when Hysteria's
    # #717 lands) trips this and forces a conscious update.
    mock_run.assert_called_once_with(
        ["systemctl", "restart", "hysteria-server.service"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_reloader_dry_run_writes_files_but_skips_reload(
    reloader_paths: dict[str, str],
) -> None:
    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_state_path=reloader_paths["reality_state_path"],
        xray_config_path=reloader_paths["xray_config_path"],
        xray_group=None,
        reality_enabled=True,
        hysteria_enabled=False,
        dry_run=True,
    )
    with patch("subprocess.run") as mock_run:
        r()
    # File rendered.
    assert pathlib.Path(reloader_paths["xray_config_path"]).exists()
    # systemctl skipped.
    mock_run.assert_not_called()


def test_reloader_both_disabled_is_noop(reloader_paths: dict[str, str]) -> None:
    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_enabled=False,
        hysteria_enabled=False,
    )
    with patch("subprocess.run") as mock_run:
        r()
    mock_run.assert_not_called()
    # No config files were created.
    assert not pathlib.Path(reloader_paths["xray_config_path"]).exists()
    assert not pathlib.Path(reloader_paths["hysteria_config_path"]).exists()


def test_reloader_render_failure_aborts_before_writes(
    reloader_paths: dict[str, str],
) -> None:
    """Corrupt the reality state file and confirm the existing xray
    config (if any) is not touched — we render first, write after.
    """
    pathlib.Path(reloader_paths["reality_state_path"]).write_text(
        "this is not valid yaml — no colon", encoding="utf-8"
    )
    # Pre-existing config we'd notice if accidentally clobbered.
    pathlib.Path(reloader_paths["xray_config_path"]).write_text(
        '{"sentinel": true}', encoding="utf-8"
    )

    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_state_path=reloader_paths["reality_state_path"],
        xray_config_path=reloader_paths["xray_config_path"],
        xray_group=None,
        reality_enabled=True,
        hysteria_enabled=False,
    )
    with patch("subprocess.run") as mock_run:
        with pytest.raises(reloader.ReloadError):
            r()

    # File untouched.
    assert (
        pathlib.Path(reloader_paths["xray_config_path"]).read_text()
        == '{"sentinel": true}'
    )
    # Reload not attempted.
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# CLI — argparse driver invoked by tasks/headless_reload.yml
# ---------------------------------------------------------------------------


def test_cli_bool_flag_recognises_common_truthy_strings() -> None:
    for s in ("1", "true", "True", "TRUE", "yes", "on"):
        assert reloader._bool_flag(s) is True
    for s in ("0", "false", "False", "no", "off", ""):
        assert reloader._bool_flag(s) is False


def test_cli_main_returns_zero_with_everything_disabled(
    reloader_paths: dict[str, str],
) -> None:
    rc = reloader.main(
        [
            "--users-index-path",
            reloader_paths["users_index_path"],
            "--reality-enabled",
            "false",
            "--hysteria-enabled",
            "false",
        ]
    )
    assert rc == 0


def test_cli_main_renders_xray_with_minimal_flags(
    reloader_paths: dict[str, str],
) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        rc = reloader.main(
            [
                "--users-index-path",
                reloader_paths["users_index_path"],
                "--reality-enabled",
                "true",
                "--reality-state-path",
                reloader_paths["reality_state_path"],
                "--xray-config-path",
                reloader_paths["xray_config_path"],
                "--xray-group",
                "",  # empty → falsy → no group ownership change
                "--hysteria-enabled",
                "false",
            ]
        )
    assert rc == 0
    cfg = json.loads(pathlib.Path(reloader_paths["xray_config_path"]).read_text())
    assert cfg["inbounds"][0]["settings"]["clients"][0]["email"] == "alice"
    mock_run.assert_called_once()


def test_cli_main_dry_run_skips_systemctl(reloader_paths: dict[str, str]) -> None:
    with patch("subprocess.run") as mock_run:
        rc = reloader.main(
            [
                "--users-index-path",
                reloader_paths["users_index_path"],
                "--reality-enabled",
                "true",
                "--reality-state-path",
                reloader_paths["reality_state_path"],
                "--xray-config-path",
                reloader_paths["xray_config_path"],
                "--xray-group",
                "",
                "--hysteria-enabled",
                "false",
                "--dry-run",
            ]
        )
    assert rc == 0
    mock_run.assert_not_called()


def test_cli_main_returns_one_on_reload_error(
    reloader_paths: dict[str, str], tmp_path: pathlib.Path
) -> None:
    # Point reality_state at a missing path so the render aborts.
    rc = reloader.main(
        [
            "--users-index-path",
            reloader_paths["users_index_path"],
            "--reality-enabled",
            "true",
            "--reality-state-path",
            str(tmp_path / "does-not-exist.yml"),
            "--hysteria-enabled",
            "false",
        ]
    )
    assert rc == 1


def test_cli_main_servernames_comma_split(reloader_paths: dict[str, str]) -> None:
    """`--reality-servernames a.com,b.com` → both end up in the rendered
    serverNames list. Operators flipping the SNI cover need this to
    accept the same comma-separated format as the ansible default var.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        reloader.main(
            [
                "--users-index-path",
                reloader_paths["users_index_path"],
                "--reality-state-path",
                reloader_paths["reality_state_path"],
                "--xray-config-path",
                reloader_paths["xray_config_path"],
                "--xray-group",
                "",
                "--reality-servernames",
                "alpha.example.com,beta.example.com",
                "--hysteria-enabled",
                "false",
            ]
        )
    cfg = json.loads(pathlib.Path(reloader_paths["xray_config_path"]).read_text())
    sni = cfg["inbounds"][0]["streamSettings"]["realitySettings"]["serverNames"]
    assert sni == ["alpha.example.com", "beta.example.com"]


def test_reloader_picks_up_new_user_after_index_mutation(
    reloader_paths: dict[str, str],
) -> None:
    """End-to-end through HeadlessBackend: add a user, the reloader
    re-renders, the on-disk xray config has both users."""
    from stealth_vps.backends_headless import HeadlessBackend

    r = reloader.Reloader(
        users_index_path=reloader_paths["users_index_path"],
        reality_state_path=reloader_paths["reality_state_path"],
        xray_config_path=reloader_paths["xray_config_path"],
        xray_group=None,
        reality_enabled=True,
        hysteria_enabled=False,
    )
    backend = HeadlessBackend(
        users_index_path=reloader_paths["users_index_path"],
        reloader=r,
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        backend.add("bob")

    cfg = json.loads(pathlib.Path(reloader_paths["xray_config_path"]).read_text())
    emails = [c["email"] for c in cfg["inbounds"][0]["settings"]["clients"]]
    assert sorted(emails) == ["alice", "bob"]
    mock_run.assert_called_once()
