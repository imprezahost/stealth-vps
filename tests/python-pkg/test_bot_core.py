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
    """
    return bot_core.BotConfig(
        users_index_path=users_index_path,
        panel_state_path=str(tmp_path / "panel.state.yml"),
        reloader_args_path=str(tmp_path / "reloader-args.json"),
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
