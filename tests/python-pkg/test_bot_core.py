"""Tests for stealth_vps.bot_core — the testable half of the Telegram
bot module.

The bot's entry point at `files/bot/stealth_vps_bot.py` imports
`telegram` at module level, which makes it untestable without the
python-telegram-bot package installed on the test runner. `bot_core`
exists to host the pure dispatch + URI-rendering logic so pytest can
exercise it standalone.

Coverage:
  - make_backend dispatches on panel.state.yml presence
  - make_backend raises clean error when panel state exists but
    credentials missing
  - build_headless_reloader handles missing/malformed reloader-args.json
    + CSV servernames + use_sudo passthrough
  - build_uris_for_user covers headless (per-user hy pw) + panel
    (shared hy pw) + port-hop range + insecure flag
  - sub_url_for handles empty subscription_public_url
  - collect_seed_hysteria_password picks the first non-empty password
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from stealth_vps import bot_core, state
from stealth_vps.backends import ThreeXUIBackend
from stealth_vps.backends_headless import HeadlessBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: pathlib.Path, users_index_path: str) -> bot_core.BotConfig:
    """A BotConfig where every path points into tmp_path. Panel-mode
    fields are EMPTY by default so make_backend takes the headless
    branch; tests that want panel mode write a panel.state.yml + set
    PANEL_* and re-construct the config.

    reality_state_path is seeded with a placeholder file so
    `is_control_mode` returns False — these legacy tests assume the
    headless-with-Reloader path. Control-mode tests `unlink()` the
    file as part of setup.
    """
    reality_state = tmp_path / "reality.state.yml"
    reality_state.write_text("port: 51820\n", encoding="utf-8")
    return bot_core.BotConfig(
        users_index_path=users_index_path,
        panel_state_path=str(tmp_path / "panel.state.yml"),
        reloader_args_path=str(tmp_path / "reloader-args.json"),
        reality_state_path=str(reality_state),
    )


@pytest.fixture
def reloader_args_file(tmp_path: pathlib.Path, users_index_path: str) -> pathlib.Path:
    """A reloader-args.json with everything disabled — the Reloader can
    still be constructed but won't try to touch real files."""
    p = tmp_path / "reloader-args.json"
    p.write_text(
        json.dumps(
            {
                "users_index_path": users_index_path,
                "reality_enabled": False,
                "hysteria_enabled": False,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def uri_cfg() -> bot_core.UriRenderConfig:
    return bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=51820,
        reality_sni="www.microsoft.com",
        reality_pubkey="PUBKEYBASE64",
        reality_short_id="ab12cd34",
        hysteria_enabled=True,
        hysteria_port=36000,
        hysteria_sni="vpn.example.com",
        hysteria_obfs_password="OBFSPW",
        hysteria_insecure=False,
    )


# ---------------------------------------------------------------------------
# make_backend dispatch rule
# ---------------------------------------------------------------------------


def test_make_backend_returns_headless_when_no_panel_state(
    cfg: bot_core.BotConfig,
    reloader_args_file: pathlib.Path,
) -> None:
    backend = bot_core.make_backend(cfg)
    assert isinstance(backend, HeadlessBackend)
    assert bot_core.backend_is_headless(backend) is True


def test_make_backend_returns_panel_when_panel_state_exists(
    cfg: bot_core.BotConfig,
    tmp_path: pathlib.Path,
) -> None:
    # Write a panel.state.yml. Need PANEL_* credentials set in cfg too.
    pathlib.Path(cfg.panel_state_path).write_text(
        "admin_user: admin\n", encoding="utf-8"
    )
    cfg.panel_url = "http://127.0.0.1:32999/abc"
    cfg.panel_username = "admin"
    cfg.panel_password = "topsecret"
    # ThreeXUIBackend's constructor calls client._login on the client.
    # We don't want a real HTTP call — patch the login to short-circuit.
    with patch("stealth_vps.threex_client.ThreeXUIClient._login"):
        backend = bot_core.make_backend(cfg)
    assert isinstance(backend, ThreeXUIBackend)
    assert bot_core.backend_is_headless(backend) is False


def test_make_backend_panel_state_present_but_no_creds_raises(
    cfg: bot_core.BotConfig,
) -> None:
    """v0.6→v0.7 migration partial-state: panel.state.yml exists but
    bot.env wasn't re-rendered → credentials missing. Should fail
    clean with an actionable message, NOT silently fall back to
    headless (which would write the index with no panel sync)."""
    pathlib.Path(cfg.panel_state_path).write_text("dummy", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing panel credentials"):
        bot_core.make_backend(cfg)


# ---------------------------------------------------------------------------
# build_headless_reloader
# ---------------------------------------------------------------------------


def test_build_headless_reloader_loads_args_json(
    cfg: bot_core.BotConfig,
    reloader_args_file: pathlib.Path,
) -> None:
    reloader = bot_core.build_headless_reloader(cfg)
    # use_sudo from cfg flows through to the Reloader instance.
    assert reloader.use_sudo is False
    assert reloader.reality_enabled is False
    assert reloader.hysteria_enabled is False


def test_build_headless_reloader_uses_use_sudo_from_cfg(
    cfg: bot_core.BotConfig,
    reloader_args_file: pathlib.Path,
) -> None:
    cfg.use_sudo = True
    reloader = bot_core.build_headless_reloader(cfg)
    assert reloader.use_sudo is True


def test_build_headless_reloader_missing_file_falls_back_to_defaults(
    cfg: bot_core.BotConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing reloader-args.json is non-fatal — log a warning, use
    Reloader's package defaults. Operators run `s-vps update` to fix."""
    # cfg.reloader_args_path points at tmp_path/reloader-args.json which
    # we haven't created — so the read fails.
    reloader = bot_core.build_headless_reloader(cfg)
    # Reloader's defaults are reality_enabled=True, hysteria_enabled=False.
    assert reloader.reality_enabled is True
    assert reloader.hysteria_enabled is False
    # Warning emitted.
    assert any("no reloader-args.json" in r.message for r in caplog.records)


def test_build_headless_reloader_malformed_json_raises(
    cfg: bot_core.BotConfig,
) -> None:
    """Malformed JSON is fail-loud — silent fallback could hide a
    misconfiguration that would surface later as a service crash."""
    pathlib.Path(cfg.reloader_args_path).write_text(
        "not valid json", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not valid JSON"):
        bot_core.build_headless_reloader(cfg)


def test_build_headless_reloader_csv_servernames_coerced(
    cfg: bot_core.BotConfig,
    tmp_path: pathlib.Path,
) -> None:
    """Operator hand-editing of reloader-args.json may leave servernames
    as a CSV string instead of a list. bot_core accepts either form."""
    pathlib.Path(cfg.reloader_args_path).write_text(
        json.dumps(
            {
                "reality_enabled": False,
                "hysteria_enabled": False,
                "reality_servernames": "alpha.com, beta.com ,gamma.com",
            }
        ),
        encoding="utf-8",
    )
    reloader = bot_core.build_headless_reloader(cfg)
    assert list(reloader.reality_servernames) == ["alpha.com", "beta.com", "gamma.com"]


# ---------------------------------------------------------------------------
# build_uris_for_user
# ---------------------------------------------------------------------------


def test_build_uris_for_user_emits_vless_always(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123"}, uri_cfg
    )
    assert len(uris) == 1
    assert uris[0].startswith("vless://u-123@vpn.example.com:51820")
    assert "sni=www.microsoft.com" in uris[0]


def test_build_uris_for_user_adds_hysteria_when_enabled(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123", "hysteria_password": "user-specific-pw"},
        uri_cfg,
    )
    assert len(uris) == 2
    hy = uris[1]
    assert hy.startswith("hysteria2://user-specific-pw@vpn.example.com:36000")
    assert "obfs-password=OBFSPW" in hy


def test_build_uris_for_user_skips_hysteria_when_disabled(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    uri_cfg.hysteria_enabled = False
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123", "hysteria_password": "would-not-use"},
        uri_cfg,
    )
    assert len(uris) == 1


def test_build_uris_for_user_skips_hysteria_when_password_empty(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    """Headless-mode-imported-from-panel users may have empty
    hysteria_password until they're rotated. Don't emit a broken URI."""
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123", "hysteria_password": ""},
        uri_cfg,
    )
    assert len(uris) == 1


def test_build_uris_for_user_port_hop_range(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    uri_cfg.hysteria_port_hop_min = 50000
    uri_cfg.hysteria_port_hop_max = 60000
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123", "hysteria_password": "pw"},
        uri_cfg,
    )
    # Port-hop renders as `host:port,min-max` in the Hysteria2 URI.
    assert "vpn.example.com:36000,50000-60000" in uris[1]


def test_build_uris_for_user_insecure_flag(
    uri_cfg: bot_core.UriRenderConfig,
) -> None:
    uri_cfg.hysteria_insecure = True
    uris = bot_core.build_uris_for_user(
        {"reality_uuid": "u-123", "hysteria_password": "pw"},
        uri_cfg,
    )
    assert "insecure=1" in uris[1]


# ---------------------------------------------------------------------------
# sub_url_for + collect_seed_hysteria_password
# ---------------------------------------------------------------------------


def test_sub_url_for_strips_trailing_slash() -> None:
    assert bot_core.sub_url_for("abc123", "https://x.example.com/.well-known/sub/") == \
        "https://x.example.com/.well-known/sub/abc123"


def test_sub_url_for_empty_base_returns_empty() -> None:
    """No subscription endpoint configured → no sub URL to emit."""
    assert bot_core.sub_url_for("abc123", "") == ""


def test_collect_seed_hysteria_password_returns_first_non_empty(
    users_index_path: str,
) -> None:
    # The fixture seeds alice with hysteria_password = "alice-hy2-pw".
    assert bot_core.collect_seed_hysteria_password(users_index_path) == "alice-hy2-pw"


def test_collect_seed_hysteria_password_skips_empty(
    users_index_path: str,
) -> None:
    # Add bob with no hy pw, then revoke alice. collect should still
    # find alice's password (revoked or not, we iterate every row).
    state.add_user(
        "bob",
        reality_uuid="bob-uuid",
        hysteria_password="",
        sub_token="bob-sub-token",
        created_at="2026-01-01T00:00:00Z",
        path=users_index_path,
    )
    # alice still has a password — pick whichever the dict iteration
    # finds first. With alice + bob in the index, both are valid
    # answers depending on insertion order. Assert the result is a
    # known non-empty password.
    pw = bot_core.collect_seed_hysteria_password(users_index_path)
    assert pw in ("alice-hy2-pw",)


def test_collect_seed_hysteria_password_empty_when_no_users(
    tmp_path: pathlib.Path,
) -> None:
    """Fresh install before users_index.yml seeds the default — no
    users at all, no Hysteria password to seed from."""
    empty_idx = tmp_path / "users.index.json"
    empty_idx.write_text(
        json.dumps({"version": 1, "users": {}}), encoding="utf-8"
    )
    assert bot_core.collect_seed_hysteria_password(str(empty_idx)) == ""


def test_collect_seed_hysteria_password_missing_index_returns_empty(
    tmp_path: pathlib.Path,
) -> None:
    """No users.index.json at all → empty (caller decides whether to
    proceed without). Doesn't raise — defensive against very-fresh
    installs."""
    assert bot_core.collect_seed_hysteria_password(str(tmp_path / "missing.json")) == ""


# ---------------------------------------------------------------------------
# Multi-node URI rendering (v0.10.0+)
# ---------------------------------------------------------------------------


def _make_node(node_id: str, **overrides) -> "stealth_vps.fleet.FleetNode":  # type: ignore[name-defined]
    """Helper: build a FleetNode with sane defaults; overrides take
    precedence so each test can be precise about what it's exercising."""
    from stealth_vps.fleet import FleetNode
    defaults = dict(
        node_id=node_id,
        ssh_host=f"{node_id}.example.com",
        ssh_port=22,
        ssh_user="root",
        ssh_key_path=f"/keys/control_to_{node_id}.ed25519",
        reality_public_key=f"PUBKEY-{node_id}",
        reality_short_id=f"sid{node_id[-1]}",
        reality_port=43338,
        reality_servernames=["www.microsoft.com"],
        hysteria_port=49440,
        hysteria_obfs_password="OBFSPW",
        public_host=None,
        domain="example.com",
        added_at="2026-05-21T10:00:00Z",
    )
    defaults.update(overrides)
    return FleetNode(**defaults)


def test_uri_config_from_node_basic_fields() -> None:
    from stealth_vps import bot_core
    node = _make_node("tokyo-1")
    cfg = bot_core.uri_config_from_node(node)
    # Public endpoint defaults to ssh_host when public_host is None.
    assert cfg.public_host == "tokyo-1.example.com"
    assert cfg.reality_port == 43338
    assert cfg.reality_sni == "www.microsoft.com"     # first servername
    assert cfg.reality_pubkey == "PUBKEY-tokyo-1"
    assert cfg.reality_short_id == "sid1"
    assert cfg.hysteria_enabled is True
    assert cfg.hysteria_port == 49440
    # Domain is set → insecure should be False.
    assert cfg.hysteria_insecure is False


def test_uri_config_from_node_uses_public_host_when_set() -> None:
    from stealth_vps import bot_core
    node = _make_node("tokyo-1", public_host="tokyo.cdn.example.com")
    cfg = bot_core.uri_config_from_node(node)
    assert cfg.public_host == "tokyo.cdn.example.com"


def test_uri_config_from_node_no_domain_flips_insecure() -> None:
    """IP-only data nodes (no LE domain) get insecure=1 in the
    Hysteria URI so clients accept the self-signed cert."""
    from stealth_vps import bot_core
    node = _make_node("ip-only", domain="")
    cfg = bot_core.uri_config_from_node(node)
    assert cfg.hysteria_insecure is True


def test_uri_config_from_node_no_hysteria_port_disables_hysteria() -> None:
    """Single-protocol Reality-only nodes (hysteria disabled) don't
    emit Hysteria URIs."""
    from stealth_vps import bot_core
    node = _make_node("reality-only", hysteria_port=0)
    cfg = bot_core.uri_config_from_node(node)
    assert cfg.hysteria_enabled is False


def test_uri_config_from_node_falls_back_to_endpoint_for_sni() -> None:
    """When `reality_servernames` is empty (shouldn't happen in
    practice, but defend against partial files), the SNI falls back
    to the public endpoint so URIs are at least well-formed."""
    from stealth_vps import bot_core
    node = _make_node("partial", reality_servernames=[])
    cfg = bot_core.uri_config_from_node(node)
    assert cfg.reality_sni == node.public_endpoint


# ---------------------------------------------------------------------------
# v0.11.0 Block C — per-protocol multi-node discovery
# ---------------------------------------------------------------------------


def test_uri_config_from_node_enables_protocols_with_ports() -> None:
    """A node carrying v0.11 protocol ports → the config enables each
    one. Ports at 0 (default) stay disabled."""
    from stealth_vps import bot_core
    node = _make_node(
        "full",
        ss2022_port=8543, ss2022_method="2022-blake3-aes-128-gcm",
        ss2022_server_psk="SP",
        xhttp_port=18543, xhttp_path="/x",
        vmess_ws_port=19543, vmess_ws_path="/v",
        trojan_port=4443,
    )
    cfg = bot_core.uri_config_from_node(node)
    assert cfg.ss2022_enabled and cfg.ss2022_port == 8543
    assert cfg.ss2022_server_psk == "SP"
    assert cfg.xhttp_enabled and cfg.xhttp_path == "/x"
    assert cfg.vmess_ws_enabled and cfg.vmess_ws_path == "/v"
    assert cfg.trojan_enabled and cfg.trojan_port == 4443


def test_uri_config_from_node_disables_protocols_without_ports() -> None:
    """A Reality-only node (no v0.11 protocol ports) → all new
    protocols disabled in the config."""
    from stealth_vps import bot_core
    node = _make_node("reality-only")   # no ss2022/xhttp/vmess/trojan ports
    cfg = bot_core.uri_config_from_node(node)
    assert not cfg.ss2022_enabled
    assert not cfg.xhttp_enabled
    assert not cfg.vmess_ws_enabled
    assert not cfg.trojan_enabled


def test_build_uris_multinode_heterogeneous_fleet() -> None:
    """The v0.11 design's headline scenario: tokyo-1 runs
    Reality+Hysteria+SS-2022, cdn-1 runs only XHTTP. A user's bundle
    has exactly the URIs each node actually serves — 3 from tokyo
    (reality+hysteria+ss) + 1 from cdn (xhttp) = 4."""
    from stealth_vps import bot_core
    tokyo = _make_node(
        "tokyo-1",
        ss2022_port=8543, ss2022_method="2022-blake3-aes-128-gcm",
        ss2022_server_psk="SP",
    )
    # cdn-1: XHTTP only — no Reality, no Hysteria, no SS.
    cdn = _make_node(
        "cdn-1",
        reality_port=0, reality_public_key="", reality_short_id="",
        hysteria_port=0,
        xhttp_port=18543, xhttp_path="/x",
    )
    rec = {
        "reality_uuid": "00000000-0000-0000-0000-000000000001",
        "hysteria_password": "hy2-pw",
        "ss2022_psk": "USER_PSK",
        "trojan_password": "tpw",
    }
    uris = bot_core.build_uris_for_user_multinode(rec, [cdn, tokyo], label="alice")
    schemes = sorted(u.split("://", 1)[0] for u in uris)
    # cdn: 1 vless(xhttp). tokyo: vless(reality) + hysteria2 + ss.
    # Two vless entries (xhttp on cdn, reality on tokyo).
    assert schemes == ["hysteria2", "ss", "vless", "vless"]
    # Per-node remarks present.
    joined = "\n".join(uris)
    assert "stealth-vps-xhttp-alice-cdn-1" in joined
    assert "stealth-vps-ss2022-alice-tokyo-1" in joined
    assert "stealth-vps-reality-alice-tokyo-1" in joined


def test_build_uris_multinode_ss2022_uses_per_node_server_psk() -> None:
    """Each node has its OWN ss2022 server PSK (per-node keys, ADR).
    The bundle's ss:// for a node must pre-concatenate THAT node's
    server PSK with the user's PSK — not some other node's."""
    from stealth_vps import bot_core
    import base64
    n1 = _make_node("n1", ss2022_port=8543, ss2022_server_psk="SERVER_PSK_N1")
    rec = {"reality_uuid": "u", "hysteria_password": "h", "ss2022_psk": "USER_PSK"}
    uris = bot_core.build_uris_for_user_multinode(rec, [n1])
    ss = [u for u in uris if u.startswith("ss://")][0]
    # Decode the userinfo, confirm n1's server PSK is in the creds.
    import urllib.parse
    encoded = urllib.parse.urlparse(ss).netloc.split("@")[0]
    auth = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    assert "SERVER_PSK_N1:USER_PSK" in auth


def test_build_uris_for_user_multinode_emits_per_node_uris() -> None:
    """2 nodes × 2 protocols = 4 URIs, per-node Reality keys baked in."""
    from stealth_vps import bot_core
    nodes = [
        _make_node("amsterdam-1"),
        _make_node("tokyo-1"),
    ]
    rec = {
        "reality_uuid": "00000000-0000-0000-0000-000000000001",
        "hysteria_password": "hy2-pw",
    }
    uris = bot_core.build_uris_for_user_multinode(rec, nodes, label="alice")
    assert len(uris) == 4
    # Order is iteration order — alphabetic since `load_fleet` sorts.
    # Reality URIs come before Hysteria for each node.
    assert uris[0].startswith("vless://")
    assert "amsterdam-1.example.com" in uris[0]
    assert "PUBKEY-amsterdam-1" in uris[0]
    assert "stealth-vps-reality-alice-amsterdam-1" in uris[0]

    assert uris[1].startswith("hysteria2://")
    assert "amsterdam-1.example.com" in uris[1]
    assert "stealth-vps-hysteria2-alice-amsterdam-1" in uris[1]

    assert uris[2].startswith("vless://")
    assert "tokyo-1.example.com" in uris[2]
    assert "PUBKEY-tokyo-1" in uris[2]
    assert "stealth-vps-reality-alice-tokyo-1" in uris[2]

    assert uris[3].startswith("hysteria2://")
    assert "tokyo-1.example.com" in uris[3]


def test_build_uris_for_user_multinode_without_label() -> None:
    """When no label is passed, remark uses just `-<node_id>` suffix."""
    from stealth_vps import bot_core
    nodes = [_make_node("solo")]
    rec = {"reality_uuid": "u", "hysteria_password": "p"}
    uris = bot_core.build_uris_for_user_multinode(rec, nodes)
    assert "stealth-vps-reality-solo" in uris[0]
    assert "-alice-solo" not in uris[0]


def test_build_uris_for_user_multinode_skips_hysteria_on_reality_only_nodes() -> None:
    """Mix of full (Reality+Hysteria) and Reality-only nodes — bundle
    has 3 URIs (full=2, reality-only=1), not 4."""
    from stealth_vps import bot_core
    nodes = [
        _make_node("a", hysteria_port=0),    # Reality-only
        _make_node("b"),                       # full
    ]
    rec = {"reality_uuid": "u", "hysteria_password": "p"}
    uris = bot_core.build_uris_for_user_multinode(rec, nodes)
    assert len(uris) == 3
    # First URI is from `a` (sorted), Reality only.
    assert uris[0].startswith("vless://")
    assert "stealth-vps-reality-a" in uris[0]
    # Next two are from `b` (Reality + Hysteria).
    assert uris[1].startswith("vless://")
    assert "stealth-vps-reality-b" in uris[1]
    assert uris[2].startswith("hysteria2://")
    assert "stealth-vps-hysteria2-b" in uris[2]


def test_build_uris_for_user_multinode_empty_fleet_returns_empty() -> None:
    from stealth_vps import bot_core
    assert bot_core.build_uris_for_user_multinode({"reality_uuid": "u"}, []) == []


def test_build_uris_for_user_multinode_skips_hysteria_when_user_has_no_pw() -> None:
    """A user with hysteria_password='' (e.g. created with --hysteria-password
    explicitly cleared, or a panel-mode mirror) shouldn't get Hysteria URIs
    even when the node terminates Hysteria. Matches `build_uris_for_user`'s
    existing single-node behaviour."""
    from stealth_vps import bot_core
    nodes = [_make_node("n1")]
    rec = {"reality_uuid": "u", "hysteria_password": ""}
    uris = bot_core.build_uris_for_user_multinode(rec, nodes)
    assert len(uris) == 1
    assert uris[0].startswith("vless://")


# ---------------------------------------------------------------------------
# v0.11.0+ — protocol additions in build_uris_for_user
# ---------------------------------------------------------------------------


def _rec_full() -> dict:
    """A user record with EVERY v3 credential present, so each protocol
    test can assert on the per-protocol emission rule (`enabled AND
    rec.has(field)`) without per-test setup boilerplate."""
    return {
        "reality_uuid": "00000000-0000-0000-0000-000000000001",
        "hysteria_password": "hy-pw",
        "ss2022_psk": "USER_PSK_BASE64",
        "trojan_password": "trojan-pw",
        "wireguard_pubkey": "WG_PUBKEY",
        "wireguard_client_ip": "10.99.0.5",
    }


def test_build_uris_for_user_emits_xhttp_when_enabled() -> None:
    cfg = bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=43338,
        reality_sni="www.microsoft.com",
        reality_pubkey="PUB",
        reality_short_id="SID",
        xhttp_enabled=True,
        xhttp_port=18543,
        xhttp_path="/.well-known/xhttp-stream",
        xhttp_sni="vpn.example.com",
    )
    uris = bot_core.build_uris_for_user(_rec_full(), cfg)
    assert any(u.startswith("vless://") and "type=xhttp" in u for u in uris)


def test_build_uris_for_user_emits_vmess_ws_when_enabled() -> None:
    cfg = bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=43338, reality_sni="s", reality_pubkey="P", reality_short_id="S",
        vmess_ws_enabled=True,
        vmess_ws_port=19543,
        vmess_ws_path="/.well-known/vmess-ws",
    )
    uris = bot_core.build_uris_for_user(_rec_full(), cfg)
    assert any(u.startswith("vmess://") for u in uris)


def test_build_uris_for_user_emits_ss2022_when_enabled_and_psk_present() -> None:
    cfg = bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=43338, reality_sni="s", reality_pubkey="P", reality_short_id="S",
        ss2022_enabled=True,
        ss2022_port=8543,
        ss2022_server_psk="SERVER_PSK",
        ss2022_method="2022-blake3-aes-128-gcm",
    )
    uris = bot_core.build_uris_for_user(_rec_full(), cfg)
    ss_uris = [u for u in uris if u.startswith("ss://")]
    assert len(ss_uris) == 1


def test_build_uris_for_user_skips_ss2022_when_user_psk_absent() -> None:
    """User added pre-v0.11 has no `ss2022_psk` (migrated to None on
    load). The URI builder must skip SS-2022 for them even when the
    protocol is enabled on the host. Operator can issue a PSK later
    via `update_user(label, ss2022_psk=...)`."""
    cfg = bot_core.UriRenderConfig(
        public_host="h", reality_port=1, reality_sni="s",
        reality_pubkey="P", reality_short_id="S",
        ss2022_enabled=True,
        ss2022_port=8543,
        ss2022_server_psk="SERVER_PSK",
    )
    rec = _rec_full()
    rec["ss2022_psk"] = None
    uris = bot_core.build_uris_for_user(rec, cfg)
    assert not any(u.startswith("ss://") for u in uris)


def test_build_uris_for_user_emits_trojan_when_enabled() -> None:
    cfg = bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=43338, reality_sni="s", reality_pubkey="P", reality_short_id="S",
        trojan_enabled=True,
        trojan_port=4443,
        trojan_sni="vpn.example.com",
    )
    uris = bot_core.build_uris_for_user(_rec_full(), cfg)
    assert any(u.startswith("trojan://") for u in uris)


def test_build_uris_for_user_skips_trojan_when_password_absent() -> None:
    cfg = bot_core.UriRenderConfig(
        public_host="h", reality_port=1, reality_sni="s",
        reality_pubkey="P", reality_short_id="S",
        trojan_enabled=True,
        trojan_port=4443,
    )
    rec = _rec_full()
    rec["trojan_password"] = None
    uris = bot_core.build_uris_for_user(rec, cfg)
    assert not any(u.startswith("trojan://") for u in uris)


def test_build_uris_for_user_full_stack_emits_all_six_protocols() -> None:
    """End-to-end: every protocol flag on + every credential present →
    bundle contains 1 Reality + 1 Hysteria2 + 1 XHTTP + 1 VMess + 1 SS2022
    + 1 Trojan = 6 URIs."""
    cfg = bot_core.UriRenderConfig(
        public_host="vpn.example.com",
        reality_port=43338, reality_sni="s", reality_pubkey="P", reality_short_id="S",
        hysteria_enabled=True, hysteria_port=49440, hysteria_sni="s",
        xhttp_enabled=True, xhttp_port=18543, xhttp_path="/x",
        vmess_ws_enabled=True, vmess_ws_port=19543, vmess_ws_path="/v",
        ss2022_enabled=True, ss2022_port=8543, ss2022_server_psk="SP",
        trojan_enabled=True, trojan_port=4443,
    )
    uris = bot_core.build_uris_for_user(_rec_full(), cfg)
    schemes = [u.split("://", 1)[0] for u in uris]
    assert schemes == ["vless", "hysteria2", "vless", "vmess", "ss", "trojan"]


# ---------------------------------------------------------------------------
# uri_config_from_states (v0.12.1 — single-node config builder)
# ---------------------------------------------------------------------------

_REALITY_ST = {"port": 43338, "public_key": "PUB", "short_id": "SID"}
_HYST_ST = {"port": 49440, "obfs_password": "OBFS"}


def test_uri_config_from_states_reality_hysteria() -> None:
    cfg = bot_core.uri_config_from_states(
        public_host="vpn.example.com",
        reality_state=_REALITY_ST,
        hysteria_state=_HYST_ST,
    )
    assert cfg.public_host == "vpn.example.com"
    assert cfg.reality_enabled is True
    assert cfg.reality_port == 43338
    assert cfg.reality_pubkey == "PUB"
    assert cfg.reality_short_id == "SID"
    # No client_servername in state → SNI falls back to the public host
    # (matches cli._render_user_uris, the proven single-node behaviour).
    assert cfg.reality_sni == "vpn.example.com"
    assert cfg.hysteria_enabled is True
    assert cfg.hysteria_port == 49440
    assert cfg.hysteria_obfs_password == "OBFS"
    assert cfg.hysteria_insecure is False   # has_domain defaults True


def test_uri_config_from_states_no_domain_flips_insecure() -> None:
    cfg = bot_core.uri_config_from_states(
        public_host="203.0.113.5",
        reality_state=_REALITY_ST,
        hysteria_state=_HYST_ST,
        has_domain=False,
    )
    assert cfg.hysteria_insecure is True


def test_uri_config_from_states_no_reality_pubkey_disables_reality() -> None:
    # A control box (no reality.state.yml → empty/portless dict) must not
    # emit a bogus vless://host:0 entry.
    cfg = bot_core.uri_config_from_states(
        public_host="h", reality_state={"port": 51820},
    )
    assert cfg.reality_enabled is False


def test_uri_config_from_states_hysteria_absent_stays_disabled() -> None:
    cfg = bot_core.uri_config_from_states(
        public_host="h", reality_state=_REALITY_ST, hysteria_state=None,
    )
    assert cfg.hysteria_enabled is False


def test_uri_config_from_states_client_servername_wins_for_sni() -> None:
    cfg = bot_core.uri_config_from_states(
        public_host="h",
        reality_state={**_REALITY_ST, "client_servername": "www.microsoft.com"},
    )
    assert cfg.reality_sni == "www.microsoft.com"


def test_uri_config_from_states_v011_protocols_enabled_when_present() -> None:
    cfg = bot_core.uri_config_from_states(
        public_host="vpn.example.com",
        reality_state=_REALITY_ST,
        ss2022_state={"port": 8389, "method": "2022-blake3-aes-256-gcm",
                      "server_psk": "SRV"},
        xhttp_state={"port": 2096, "path": "/xh"},
        vmess_ws_state={"port": 2097, "path": "/vm"},
        trojan_state={"port": 2098},
    )
    assert cfg.ss2022_enabled and cfg.ss2022_port == 8389
    assert cfg.ss2022_method == "2022-blake3-aes-256-gcm"
    assert cfg.ss2022_server_psk == "SRV"
    assert cfg.xhttp_enabled and cfg.xhttp_port == 2096 and cfg.xhttp_path == "/xh"
    assert cfg.xhttp_host_header == "vpn.example.com"
    assert cfg.vmess_ws_enabled and cfg.vmess_ws_path == "/vm"
    assert cfg.trojan_enabled and cfg.trojan_port == 2098


def test_uri_config_from_states_feeds_build_uris_for_user() -> None:
    """The point of the helper: it drives build_uris_for_user to emit a
    working single-node bundle — the path cli._refresh_subscription_file
    takes on a no-fleet host."""
    cfg = bot_core.uri_config_from_states(
        public_host="vpn.example.com",
        reality_state=_REALITY_ST, hysteria_state=_HYST_ST,
    )
    rec = {"reality_uuid": "uuid-1", "hysteria_password": "pw"}
    uris = bot_core.build_uris_for_user(rec, cfg)
    assert any(u.startswith("vless://") and "vpn.example.com" in u for u in uris)
    assert any(u.startswith("hysteria2://") for u in uris)


# ---------------------------------------------------------------------------
# is_control_mode + make_backend control branch (v0.10.0+)
# ---------------------------------------------------------------------------


def test_is_control_mode_true_when_reality_state_absent(
    cfg: bot_core.BotConfig,
) -> None:
    """Default cfg has reality.state.yml present → False. Remove it → True."""
    assert bot_core.is_control_mode(cfg) is False
    pathlib.Path(cfg.reality_state_path).unlink()
    assert bot_core.is_control_mode(cfg) is True


def test_make_backend_control_mode_returns_headless_without_reloader(
    cfg: bot_core.BotConfig, tmp_path: pathlib.Path
) -> None:
    """Control box: reality.state.yml absent → HeadlessBackend with
    reloader=None (its `_noop_reloader` fallback). The presence of
    `reloader_args.json` doesn't matter — control mode skips that path
    entirely so missing reloader-args.json doesn't crash."""
    pathlib.Path(cfg.reality_state_path).unlink()
    backend = bot_core.make_backend(cfg)
    assert isinstance(backend, HeadlessBackend)
    # _noop_reloader is what HeadlessBackend falls back to when reloader=None.
    # We can't compare directly (it's an instance attribute) but we can
    # verify calling .reloader() does nothing.
    backend.reloader()   # should not raise


def test_build_uris_for_user_multinode_round_trip_through_subscription_file(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: build URIs → write subscription file → base64-decode
    → assert each URI appears in the decoded body. Sanity that the
    existing `subscription.write_subscription_file` doesn't need
    multi-node-specific changes."""
    import base64
    from stealth_vps import bot_core, subscription

    nodes = [_make_node("a"), _make_node("b")]
    rec = {"reality_uuid": "u", "hysteria_password": "pw"}
    uris = bot_core.build_uris_for_user_multinode(rec, nodes, label="alice")
    path = subscription.write_subscription_file(
        "abc123", uris, dir=str(tmp_path),
    )
    body_b64 = pathlib.Path(path).read_text().strip()
    decoded = base64.b64decode(body_b64).decode("utf-8")
    for uri in uris:
        assert uri in decoded
