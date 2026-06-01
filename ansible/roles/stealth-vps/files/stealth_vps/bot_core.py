"""Bot-side dispatch primitives — extracted from `bot/stealth_vps_bot.py`
so they can be pytest'd without a python-telegram-bot install.

The Telegram entrypoint imports `from . import bot_core` (and its
constants are env-fed at module-load time of stealth_vps_bot). Tests
import the same functions directly and pass paths via the
`BotConfig` dataclass — so monkeypatching env vars isn't required.

Why this module exists separately:
  - `stealth_vps_bot.py` imports `telegram` at the top level. Test
    runners that don't have python-telegram-bot installed can't even
    `import stealth_vps_bot`. Pulling the pure backend/reloader/URI
    logic out lets us test the dispatch rule (panel.state.yml present?
    → panel; else headless) without that dep.
  - The CLI's equivalent (`stealth_vps.cli`) already has its own
    dispatch helper `_select_backend_for_cli`. Both paths SHOULD
    behave identically — moving the bot's version into a tested
    module lets us assert that contract.

Coverage in `tests/python-pkg/test_bot_core.py` (added in v0.8.1):
  - backend dispatch on panel.state.yml presence
  - Reloader construction from reloader-args.json (missing file,
    malformed JSON, CSV servernames form, use_sudo passthrough)
  - URI rendering for both backends (panel vs headless hysteria pw
    sourcing, port-hop range, insecure flag toggling on no-domain)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from typing import Any, Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    # `fleet.FleetNode` is only needed for the v0.10.0+ multi-node URI
    # helpers (`uri_config_from_node`, `build_uris_for_user_multinode`).
    # Pulled in under TYPE_CHECKING so importing `bot_core` on a v0.9
    # box (where the operator might not have updated yet, or where the
    # fleet module is missing from a partial install) doesn't fail at
    # import time. Annotations on the helpers stay as string forms.
    from .fleet import FleetNode

from . import state
from .backends import ThreeXUIBackend, ThreeXUIClient, UserBackend
from .backends_headless import HeadlessBackend
from .reloader import Reloader
from .urivider import (
    build_hysteria2_uri,
    build_vless_uri,
    build_ss2022_uri,
    build_trojan_uri,
    build_vmess_ws_uri,
    build_xhttp_uri,
)


log = logging.getLogger("stealth_vps.bot_core")


# Default paths the role pins. The bot's env-vars override these
# (STEALTH_VPS_BOT_PANEL_STATE_PATH, …) but defaults exist so tests
# don't need to monkeypatch absolute paths from the start.

DEFAULT_PANEL_STATE_PATH = "/etc/stealth-vps/panel.state.yml"
DEFAULT_RELOADER_ARGS_PATH = "/etc/stealth-vps/reloader-args.json"
DEFAULT_REALITY_STATE_PATH = "/etc/stealth-vps/reality.state.yml"


@dataclasses.dataclass
class BotConfig:
    """All paths + flags the bot dispatch helpers need. The bot's
    module-level constants get packed into one of these at startup;
    tests construct a BotConfig pointing at tmp_path fixtures.
    """

    users_index_path: str
    panel_state_path: str = DEFAULT_PANEL_STATE_PATH
    reloader_args_path: str = DEFAULT_RELOADER_ARGS_PATH
    # v0.10.0+: absence signals control mode (no local Xray to reload).
    reality_state_path: str = DEFAULT_REALITY_STATE_PATH

    # Panel-mode credentials. None when panel mode is unavailable.
    panel_url: str = ""
    panel_username: str = ""
    panel_password: str = ""

    # Reality remark + flow are needed to construct ThreeXUIBackend.
    reality_remark: str = "stealth-vps-reality"
    reality_flow: str = "xtls-rprx-vision"

    # Whether the reloader needs to prefix systemctl with `sudo -n`.
    use_sudo: bool = False


def build_headless_reloader(config: BotConfig) -> Reloader:
    """Reconstruct a Reloader from the kwargs the role's
    `headless_reload.yml` writes to /etc/stealth-vps/reloader-args.json
    on every converge.

    Behaviour matches `stealth_vps.cli._build_reloader` so a bot-
    triggered `/user add` produces byte-identical xray + hysteria
    configs to what the CLI would write.

    `use_sudo=config.use_sudo` because the bot runs as the
    `stealth-vps-bot` system user and needs the sudoers drop-in's
    NOPASSWD rule to `systemctl restart` the services.

    Failure modes:
      - reloader-args.json missing → log a warning, fall back to
        Reloader's package defaults. Operators run `s-vps update` to
        regenerate the file. Reloader can still work (best-effort
        defaults match the role) but won't match operator overrides.
      - malformed JSON → ValueError propagates so the bot handler
        can show the operator a clear error in the chat. We don't
        silently fall back here — silent fallback could hide a real
        misconfiguration.
    """
    try:
        with open(config.reloader_args_path, "r", encoding="utf-8") as f:
            args: dict[str, Any] = json.load(f)
    except FileNotFoundError:
        log.warning(
            "no reloader-args.json at %s — falling back to Reloader defaults. "
            "Run `s-vps update` so ansible regenerates the file.",
            config.reloader_args_path,
        )
        args = {}
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"reloader-args.json at {config.reloader_args_path} is not valid JSON ({exc}). "
            f"Re-run `s-vps update` to regenerate it."
        )

    # The role serialises servernames as a list; Reloader's kwarg is
    # iterable so either form works, but accept the CSV string form
    # too in case an operator hand-edits the JSON.
    if isinstance(args.get("reality_servernames"), str):
        args["reality_servernames"] = [
            s.strip() for s in args["reality_servernames"].split(",") if s.strip()
        ]
    args["use_sudo"] = config.use_sudo
    return Reloader(**args)


def is_control_mode(config: BotConfig) -> bool:
    """True when this host runs as a stealth-vps CONTROL plane (v0.10.0+).
    On a control box the role explicitly skips xray.yml + hysteria.yml,
    so `reality.state.yml` never gets created. Single-node + data-node
    hosts always have it (the role generates it inside reality_state.yml).
    """
    return not os.path.exists(config.reality_state_path)


def make_backend(config: BotConfig) -> UserBackend:
    """Return the right `UserBackend` for the bot to talk through.

    Selection rule (same as `stealth_vps.select_backend` + the CLI's
    `_select_backend_for_cli`):
      * panel.state.yml on disk → panel mode → ThreeXUIBackend.
      * reality.state.yml absent → control mode (v0.10.0+) →
        HeadlessBackend with NO reloader. Propagation to data nodes is
        done by the bot's caller via `fleet.sync_all`, not here.
      * else → headless mode → HeadlessBackend + Reloader.

    The `STEALTH_VPS_BOT_PANEL_ENABLED` env var is the OPERATOR'S
    INTENT (set by installer.env); `panel.state.yml`'s presence is
    the ON-DISK FACT. We dispatch on the fact, not the intent, so a
    half-finished migration (panel disabled in the env but state file
    still around) still talks to the running panel until ansible
    converges away from it.

    Raises RuntimeError when panel.state.yml exists but the bot's
    panel credentials (URL/user/password) are missing from bot.env —
    typically a v0.6→v0.7 migration that didn't re-render bot.env.
    """
    if os.path.exists(config.panel_state_path):
        if not config.panel_url or not config.panel_username or not config.panel_password:
            raise RuntimeError(
                "panel.state.yml present but bot.env is missing panel credentials. "
                "Re-run `s-vps update` to regenerate /etc/stealth-vps/bot.env."
            )
        client = ThreeXUIClient(
            base_url=config.panel_url,
            username=config.panel_username,
            password=config.panel_password,
            verify_tls=False,  # 127.0.0.1 loopback, self-signed
        )
        return ThreeXUIBackend(
            client,
            reality_remark=config.reality_remark,
            reality_flow=config.reality_flow,
            users_index_path=config.users_index_path,
        )
    if is_control_mode(config):
        # Control box: no local Xray/Hysteria → no Reloader. The bot's
        # caller fires `fleet.sync_all` after the backend method
        # returns. HeadlessBackend's `reloader=None` falls back to its
        # internal _noop_reloader.
        return HeadlessBackend(
            users_index_path=config.users_index_path,
            reloader=None,
        )
    # Headless mode: every add/revoke goes through HeadlessBackend +
    # Reloader. add() generates a fresh per-user Hysteria2 password
    # so revoking one user doesn't break the others.
    return HeadlessBackend(
        users_index_path=config.users_index_path,
        reloader=build_headless_reloader(config),
    )


def backend_is_headless(backend: UserBackend) -> bool:
    """True when the active backend is HeadlessBackend. Used by the
    /user add handler to skip the panel-mode shared-password copy.
    """
    return isinstance(backend, HeadlessBackend)


# ---------------------------------------------------------------------------
# URI / subscription rendering
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class UriRenderConfig:
    """Per-protocol settings needed to render per-user URIs. The bot
    fills these from its env vars at startup; tests pass them directly
    in fixtures.

    v0.11.0+ adds XHTTP / VMess+WS / SS-2022 / Trojan fields. Each is
    OFF by default — the URI builder skips that protocol's URI unless
    its `*_enabled` flag is true AND the per-user record has the
    required credential (UUID for XHTTP/VMess from `reality_uuid`,
    `ss2022_psk` for SS-2022, `trojan_password` for Trojan).
    """

    public_host: str
    reality_port: int
    reality_sni: str
    reality_pubkey: str
    reality_short_id: str
    reality_fingerprint: str = "chrome"
    reality_flow: str = "xtls-rprx-vision"
    reality_remark: str = "stealth-vps-reality"
    # Defaults True so single-node callers (which always run Reality)
    # keep emitting the Reality URI without setting this. Multi-node
    # sets it False for a Reality-less node (e.g. an XHTTP-only CDN
    # front) so the bundle doesn't carry a bogus vless://host:0 entry.
    reality_enabled: bool = True
    hysteria_enabled: bool = False
    hysteria_port: int = 0
    hysteria_sni: str = ""
    hysteria_obfs_type: str = "salamander"
    hysteria_obfs_password: str = ""
    hysteria_remark: str = "stealth-vps-hysteria2"
    hysteria_insecure: bool = False
    hysteria_port_hop_min: int | None = None
    hysteria_port_hop_max: int | None = None

    # v0.11.0+ — XHTTP (VLESS-over-XHTTP behind CDN front).
    xhttp_enabled: bool = False
    xhttp_port: int = 0
    xhttp_path: str = ""
    xhttp_host_header: str = ""
    xhttp_sni: str = ""
    xhttp_remark: str = "stealth-vps-xhttp"

    # v0.11.0+ — VMess+WebSocket+TLS (legacy-compat path behind Caddy).
    vmess_ws_enabled: bool = False
    vmess_ws_port: int = 0
    vmess_ws_path: str = ""
    vmess_ws_host_header: str = ""
    vmess_ws_sni: str = ""
    vmess_ws_remark: str = "stealth-vps-vmess-ws"

    # v0.11.0+ — Shadowsocks-2022 (SIP022).
    ss2022_enabled: bool = False
    ss2022_port: int = 0
    ss2022_method: str = "2022-blake3-aes-128-gcm"
    ss2022_server_psk: str = ""
    ss2022_remark: str = "stealth-vps-ss2022"

    # v0.11.0+ — Trojan-Go (separate daemon; URI shape lives here for
    # parity with the others — wiring lands in Block B).
    trojan_enabled: bool = False
    trojan_port: int = 0
    trojan_sni: str = ""
    trojan_insecure: bool = False
    trojan_remark: str = "stealth-vps-trojan"


def build_uris_for_user(
    rec: Mapping[str, Any], uri_config: UriRenderConfig
) -> list[str]:
    """Return the list of connection URIs for a user record.

    Output (each entry present only when its protocol is enabled +
    the user has the matching credential):
      - VLESS-Reality URI (when reality_enabled — True by default for
        single-node; False for a Reality-less multi-node data node)
      - Hysteria2 URI (hysteria_enabled AND hysteria_password)
      - XHTTP / VMess+WS / SS-2022 / Trojan URIs (v0.11.0+)

    Headless-mode callers pass per-user hysteria_password (from
    HeadlessBackend.add's random gen). Panel-mode callers pass the
    shared seed password — same shape from this function's POV.
    """
    uris: list[str] = []
    if uri_config.reality_enabled and rec.get("reality_uuid"):
        uris.append(
            build_vless_uri(
                uuid=rec["reality_uuid"],
                host=uri_config.public_host,
                port=uri_config.reality_port,
                sni=uri_config.reality_sni,
                public_key=uri_config.reality_pubkey,
                short_id=uri_config.reality_short_id,
                fingerprint=uri_config.reality_fingerprint,
                flow=uri_config.reality_flow,
                remark=uri_config.reality_remark,
            )
        )
    if uri_config.hysteria_enabled and rec.get("hysteria_password"):
        port_hop: tuple[int, int] | None = None
        if uri_config.hysteria_port_hop_min and uri_config.hysteria_port_hop_max:
            port_hop = (
                int(uri_config.hysteria_port_hop_min),
                int(uri_config.hysteria_port_hop_max),
            )
        uris.append(
            build_hysteria2_uri(
                password=rec["hysteria_password"],
                host=uri_config.public_host,
                port=uri_config.hysteria_port,
                sni=uri_config.hysteria_sni,
                obfs_type=uri_config.hysteria_obfs_type,
                obfs_password=uri_config.hysteria_obfs_password,
                port_hop_range=port_hop,
                insecure=uri_config.hysteria_insecure,
                remark=uri_config.hysteria_remark,
            )
        )

    # v0.11.0+ — XHTTP (uses Reality UUID per Open Question A1 default).
    if uri_config.xhttp_enabled and rec.get("reality_uuid"):
        uris.append(
            build_xhttp_uri(
                uuid=rec["reality_uuid"],
                host=uri_config.public_host,
                port=uri_config.xhttp_port,
                path=uri_config.xhttp_path,
                host_header=uri_config.xhttp_host_header,
                sni=uri_config.xhttp_sni,
                fingerprint=uri_config.reality_fingerprint,
                remark=uri_config.xhttp_remark,
            )
        )

    # v0.11.0+ — VMess+WS (uses Reality UUID per Open Question A1 default).
    if uri_config.vmess_ws_enabled and rec.get("reality_uuid"):
        uris.append(
            build_vmess_ws_uri(
                uuid=rec["reality_uuid"],
                host=uri_config.public_host,
                port=uri_config.vmess_ws_port,
                ws_path=uri_config.vmess_ws_path,
                host_header=uri_config.vmess_ws_host_header,
                sni=uri_config.vmess_ws_sni,
                remark=uri_config.vmess_ws_remark,
            )
        )

    # v0.11.0+ — SS-2022. Per-user PSK; pre-concatenated with server PSK
    # at render time per Open Question A2.
    if uri_config.ss2022_enabled and rec.get("ss2022_psk"):
        uris.append(
            build_ss2022_uri(
                server_psk=uri_config.ss2022_server_psk,
                user_psk=rec["ss2022_psk"],
                host=uri_config.public_host,
                port=uri_config.ss2022_port,
                method=uri_config.ss2022_method,
                remark=uri_config.ss2022_remark,
            )
        )

    # v0.11.0+ — Trojan-Go (separate daemon; URI shape lives here for
    # parity. The CONFIG wiring for the daemon lands in Block B).
    if uri_config.trojan_enabled and rec.get("trojan_password"):
        uris.append(
            build_trojan_uri(
                password=rec["trojan_password"],
                host=uri_config.public_host,
                port=uri_config.trojan_port,
                sni=uri_config.trojan_sni,
                fingerprint=uri_config.reality_fingerprint,
                allow_insecure=uri_config.trojan_insecure,
                remark=uri_config.trojan_remark,
            )
        )

    return uris


# ---------------------------------------------------------------------------
# Multi-node URI rendering (v0.10.0+)
# ---------------------------------------------------------------------------
#
# When the control box has registered data nodes via `s-vps fleet add`,
# each user's subscription bundle becomes N × P entries (N nodes × P
# protocols). The cleanest API:
#
#   nodes = stealth_vps.load_fleet()   # empty on single-node hosts
#   if nodes:
#       uris = build_uris_for_user_multinode(rec, nodes, label="alice")
#   else:
#       uris = build_uris_for_user(rec, single_node_config)
#   write_subscription_file(rec["sub_token"], uris)
#
# Single-node hosts hit the `else` branch and behave identically to
# v0.9. Multi-node hosts hit the multinode branch and emit per-node
# remarks so the client can label/sort by region.


def uri_config_from_node(node: "FleetNode") -> UriRenderConfig:  # noqa: F821
    """Materialise a `UriRenderConfig` from a `FleetNode`. The node's
    own Reality keys + ports go into the config; the operator's host
    naming (`public_host` if set, else `ssh_host`) drives the URI host.

    SNI defaults to the first `reality_servernames` entry (whatever
    real-internet site Reality is masquerading as on THIS node). If
    the node hasn't been discovered yet (empty list — shouldn't happen
    after `fleet add` completes), we fall back to the public endpoint
    so the URI is still well-formed.

    `hysteria_enabled` is set to True only when the node has a hysteria
    port — single-protocol data nodes (Reality-only) emit one URI per
    user, not two.

    v0.11.0+ per-protocol blocks (XHTTP / VMess+WS / SS-2022 / Trojan)
    are each enabled only when the node terminates them (port > 0,
    discovered by `fleet add`). A heterogeneous fleet — say tokyo-1
    runs Reality+Hysteria+SS-2022 while cdn-1 runs only XHTTP — yields
    a bundle with exactly the URIs each node actually serves.
    """
    sni = (
        node.reality_servernames[0]
        if node.reality_servernames
        else node.public_endpoint
    )
    endpoint = node.public_endpoint
    no_domain = not node.domain
    return UriRenderConfig(
        public_host=endpoint,
        reality_enabled=node.reality_port > 0,
        reality_port=node.reality_port,
        reality_sni=sni,
        reality_pubkey=node.reality_public_key,
        reality_short_id=node.reality_short_id,
        hysteria_enabled=node.hysteria_port > 0,
        hysteria_port=node.hysteria_port,
        hysteria_sni=endpoint,
        hysteria_obfs_password=node.hysteria_obfs_password,
        hysteria_insecure=no_domain,
        # v0.11.0+ — each protocol on iff the node has a port for it.
        xhttp_enabled=node.xhttp_port > 0,
        xhttp_port=node.xhttp_port,
        xhttp_path=node.xhttp_path,
        xhttp_host_header=endpoint,
        xhttp_sni=(node.domain or endpoint),
        vmess_ws_enabled=node.vmess_ws_port > 0,
        vmess_ws_port=node.vmess_ws_port,
        vmess_ws_path=node.vmess_ws_path,
        vmess_ws_host_header=endpoint,
        vmess_ws_sni=(node.domain or endpoint),
        ss2022_enabled=node.ss2022_port > 0,
        ss2022_port=node.ss2022_port,
        ss2022_method=(node.ss2022_method or "2022-blake3-aes-128-gcm"),
        ss2022_server_psk=node.ss2022_server_psk,
        trojan_enabled=node.trojan_port > 0,
        trojan_port=node.trojan_port,
        trojan_sni=(node.domain or endpoint),
        trojan_insecure=no_domain,
    )


def build_uris_for_user_multinode(
    rec: Mapping[str, Any],
    nodes: "Iterable[FleetNode]",  # noqa: F821
    *,
    label: str = "",
) -> list[str]:
    """N × P URIs across the fleet, in load_fleet() order (alphabetic
    by node_id). Each URI's remark is suffixed:

        stealth-vps-reality-<label>-<node_id>
        stealth-vps-hysteria2-<label>-<node_id>

    The label is optional — when empty, the remark is just
    `stealth-vps-reality-<node_id>` (matches v0.9's "single-user
    install" feel for fleet-of-one cases).

    Hysteria URIs are skipped on nodes that don't terminate Hysteria
    (hysteria_port == 0). Single-protocol nodes are first-class — a
    Reality-only fleet is fine.
    """
    uris: list[str] = []
    for node in nodes:
        cfg = uri_config_from_node(node)
        # Per-node remark suffix so a client importing the bundle sees
        # one labelled profile per (protocol, node). Suffix is
        # `-<label>-<node_id>` when a label is given, else `-<node_id>`.
        suffix = f"-{label}-{node.node_id}" if label else f"-{node.node_id}"
        cfg.reality_remark = f"stealth-vps-reality{suffix}"
        cfg.hysteria_remark = f"stealth-vps-hysteria2{suffix}"
        cfg.xhttp_remark = f"stealth-vps-xhttp{suffix}"
        cfg.vmess_ws_remark = f"stealth-vps-vmess-ws{suffix}"
        cfg.ss2022_remark = f"stealth-vps-ss2022{suffix}"
        cfg.trojan_remark = f"stealth-vps-trojan{suffix}"
        uris.extend(build_uris_for_user(rec, cfg))
    return uris


def sub_url_for(token: str, subscription_public_url: str) -> str:
    """Return the publicly-served subscription URL for a sub_token,
    or empty string if the subscription endpoint isn't configured.
    """
    if not subscription_public_url:
        return ""
    return subscription_public_url.rstrip("/") + "/" + token


def collect_seed_hysteria_password(
    users_index_path: str = state.USERS_INDEX_PATH,
) -> str:
    """Walk users.index.json and return the first non-empty
    hysteria_password we find — used by the panel-mode `/user add`
    path to seed new users with the shared Hysteria2 password (3X-UI's
    data model has one password per inbound).

    Empty string when no users have a Hysteria password yet (fresh
    panel install before the role's seed step completed, or after a
    panel→headless migration that left the index empty).

    Headless-mode callers should NOT use this — they want fresh
    per-user passwords from `HeadlessBackend.add`.
    """
    try:
        idx = state.load_users_index(users_index_path)
    except state.StateError:
        return ""
    for _label, rec in idx["users"].items():
        if rec.get("hysteria_password"):
            return rec["hysteria_password"]
    return ""
