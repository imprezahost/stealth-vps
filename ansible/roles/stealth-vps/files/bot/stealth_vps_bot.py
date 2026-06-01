#!/usr/bin/env python3
"""stealth-vps Telegram bot — operator interface.

Commands (all admin-only after pairing):
  /start            Pair on first run, otherwise welcome screen.
  /help             Show command list.
  /status           systemd state for xray / hysteria / panel / bot.
  /diagnose         Re-run the health checks (panel + ports + cert).
  /creds            DM the contents of stealth-vps-credentials.txt.
  /user add <label>     Create a new client + DM the per-user URIs.
  /user list            List enabled clients.
  /user revoke <label>  Disable a client (idempotent).
  /sub <label>          DM the subscription URL for <label>.
  /sub revoke <label>   Rotate the sub token (new URL).

Pairing: if STEALTH_VPS_BOT_ADMIN_CHAT_IDS is empty AND state.json is
empty, the first chat that sends /start becomes the sole admin. Persisted
to /var/lib/stealth-vps-bot/state.json so subsequent boots don't re-pair.

Config: read from environment variables — systemd EnvironmentFile=
populates them from /etc/stealth-vps/bot.env (rendered by tasks/bot.yml).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Callable

# Make the shared `stealth_vps` pkg importable. tasks/python_pkg.yml
# drops a .pth file at /usr/lib/python3/dist-packages/stealth-vps.pth
# pointing at /usr/local/lib, but venv'd Python doesn't load system
# .pth files by default — we add the path explicitly.
sys.path.insert(0, "/usr/local/lib")

from stealth_vps import (  # noqa: E402
    UserBackend,
    state,
    write_subscription_file,
)
from stealth_vps.bot_core import (  # noqa: E402
    BotConfig,
    UriRenderConfig,
    backend_is_headless,
    build_uris_for_user,
    collect_seed_hysteria_password,
    make_backend,
    sub_url_for,
)
from stealth_vps.subscription import remove_subscription_file  # noqa: E402

from telegram import Update  # noqa: E402
from telegram.constants import ParseMode  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application,
    CommandHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Logging — to stderr, journald captures it.
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx is chatty at INFO; quiet it down to WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("stealth-vps-bot")


# ---------------------------------------------------------------------------
# Config (env vars)
# ---------------------------------------------------------------------------
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str) -> bool:
    return _env(key).lower() == "true"


TOKEN = _env("STEALTH_VPS_BOT_TOKEN")
if not TOKEN:
    log.error("STEALTH_VPS_BOT_TOKEN missing — refusing to start.")
    sys.exit(1)

INITIAL_ADMIN_IDS = {
    int(x) for x in _env("STEALTH_VPS_BOT_ADMIN_CHAT_IDS").split(",") if x.strip().lstrip("-").isdigit()
}
STATE_FILE = Path(_env("STEALTH_VPS_BOT_STATE_FILE", "/var/lib/stealth-vps-bot/state.json"))
USERS_INDEX = _env("STEALTH_VPS_BOT_USERS_INDEX", "/etc/stealth-vps/users.index.json")
CREDS_FILE = Path(_env("STEALTH_VPS_BOT_CREDENTIALS_FILE", "/root/stealth-vps-credentials.txt"))
SUBSCRIPTIONS_DIR = _env("STEALTH_VPS_BOT_SUBSCRIPTIONS_DIR", "/var/lib/stealth-vps/subscriptions")

PANEL_ENABLED = _env_bool("STEALTH_VPS_BOT_PANEL_ENABLED")
HYSTERIA_ENABLED = _env_bool("STEALTH_VPS_BOT_HYSTERIA_ENABLED")
SUBSCRIPTION_ENABLED = _env_bool("STEALTH_VPS_BOT_SUBSCRIPTION_ENABLED")

PANEL_URL = _env("STEALTH_VPS_BOT_PANEL_URL")
PANEL_USERNAME = _env("STEALTH_VPS_BOT_PANEL_USERNAME")
PANEL_PASSWORD = _env("STEALTH_VPS_BOT_PANEL_PASSWORD")

PUBLIC_HOST = _env("STEALTH_VPS_BOT_PUBLIC_HOST")
REALITY_PORT = int(_env("STEALTH_VPS_BOT_REALITY_PORT", "0") or "0")
REALITY_SNI = _env("STEALTH_VPS_BOT_REALITY_SNI")
REALITY_PUBKEY = _env("STEALTH_VPS_BOT_REALITY_PUBLIC_KEY")
REALITY_SHORTID = _env("STEALTH_VPS_BOT_REALITY_SHORT_ID")
REALITY_FINGERPRINT = _env("STEALTH_VPS_BOT_REALITY_FINGERPRINT", "chrome")
REALITY_FLOW = _env("STEALTH_VPS_BOT_REALITY_FLOW", "xtls-rprx-vision")
REALITY_REMARK = _env("STEALTH_VPS_BOT_REALITY_REMARK", "stealth-vps-reality")

HYSTERIA_PORT = int(_env("STEALTH_VPS_BOT_HYSTERIA_PORT", "0") or "0")
HYSTERIA_SNI = _env("STEALTH_VPS_BOT_HYSTERIA_SNI")
HYSTERIA_OBFS_TYPE = _env("STEALTH_VPS_BOT_HYSTERIA_OBFS_TYPE", "salamander")
HYSTERIA_OBFS_PASSWORD = _env("STEALTH_VPS_BOT_HYSTERIA_OBFS_PASSWORD")
HYSTERIA_REMARK = _env("STEALTH_VPS_BOT_HYSTERIA_REMARK", "stealth-vps-hysteria2")
HYSTERIA_INSECURE = _env_bool("STEALTH_VPS_BOT_HYSTERIA_INSECURE")
HYSTERIA_HOP_MIN = _env("STEALTH_VPS_BOT_HYSTERIA_PORT_HOP_MIN")
HYSTERIA_HOP_MAX = _env("STEALTH_VPS_BOT_HYSTERIA_PORT_HOP_MAX")

# v0.11.0+ — XHTTP / VMess+WS / SS-2022 URI parameters. Each protocol's
# block is rendered into bot.env only when the protocol is enabled;
# the *_ENABLED flag gates whether build_uris_for_user emits its URI.
XHTTP_ENABLED = _env_bool("STEALTH_VPS_BOT_XHTTP_ENABLED")
XHTTP_PORT = int(_env("STEALTH_VPS_BOT_XHTTP_PORT", "0") or "0")
XHTTP_PATH = _env("STEALTH_VPS_BOT_XHTTP_PATH")
XHTTP_HOST_HEADER = _env("STEALTH_VPS_BOT_XHTTP_HOST_HEADER")
XHTTP_SNI = _env("STEALTH_VPS_BOT_XHTTP_SNI")

VMESS_WS_ENABLED = _env_bool("STEALTH_VPS_BOT_VMESS_WS_ENABLED")
VMESS_WS_PORT = int(_env("STEALTH_VPS_BOT_VMESS_WS_PORT", "0") or "0")
VMESS_WS_PATH = _env("STEALTH_VPS_BOT_VMESS_WS_PATH")
VMESS_WS_HOST_HEADER = _env("STEALTH_VPS_BOT_VMESS_WS_HOST_HEADER")
VMESS_WS_SNI = _env("STEALTH_VPS_BOT_VMESS_WS_SNI")

SS2022_ENABLED = _env_bool("STEALTH_VPS_BOT_SS2022_ENABLED")
SS2022_PORT = int(_env("STEALTH_VPS_BOT_SS2022_PORT", "0") or "0")
SS2022_METHOD = _env("STEALTH_VPS_BOT_SS2022_METHOD", "2022-blake3-aes-128-gcm")
SS2022_SERVER_PSK = _env("STEALTH_VPS_BOT_SS2022_SERVER_PSK")

# v0.11.0+ Block B — Trojan-Go.
TROJAN_ENABLED = _env_bool("STEALTH_VPS_BOT_TROJAN_ENABLED")
TROJAN_PORT = int(_env("STEALTH_VPS_BOT_TROJAN_PORT", "0") or "0")
TROJAN_SNI = _env("STEALTH_VPS_BOT_TROJAN_SNI")
TROJAN_INSECURE = _env_bool("STEALTH_VPS_BOT_TROJAN_INSECURE")

SUBSCRIPTION_PUBLIC_URL = _env("STEALTH_VPS_BOT_SUBSCRIPTION_PUBLIC_URL")
# v0.12.0+ — onboarding bridge public URL prefix. Empty when the
# onboard bridge isn't enabled.
ONBOARD_PUBLIC_URL = _env("STEALTH_VPS_BOT_ONBOARD_PUBLIC_URL")

# --- Headless-mode config -------------------------------------------------
# In v0.7+ panel-less mode the bot constructs a HeadlessBackend instead of
# a ThreeXUIBackend. The role's headless_reload.yml task writes a JSON
# kwargs blob to /etc/stealth-vps/reloader-args.json on every converge —
# we load it to rebuild the same Reloader the CLI uses. `STEALTH_VPS_BOT_
# USE_SUDO=true` flips the systemctl calls to `sudo -n systemctl restart`
# because the bot runs as `stealth-vps-bot`, not root; tasks/bot.yml drops
# a matching /etc/sudoers.d/ rule.
PANEL_STATE_PATH = _env("STEALTH_VPS_BOT_PANEL_STATE_PATH", "/etc/stealth-vps/panel.state.yml")
RELOADER_ARGS_PATH = _env("STEALTH_VPS_BOT_RELOADER_ARGS_PATH", "/etc/stealth-vps/reloader-args.json")
USE_SUDO = _env_bool("STEALTH_VPS_BOT_USE_SUDO")


# ---------------------------------------------------------------------------
# Persisted state — admin chat IDs captured at pairing
# ---------------------------------------------------------------------------
def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"version": 1, "admin_chat_ids": []}
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception as exc:
        log.warning("could not parse %s, starting fresh: %s", STATE_FILE, exc)
        return {"version": 1, "admin_chat_ids": []}


def _save_state(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.chmod(0o640)
    tmp.replace(STATE_FILE)


_state = _load_state()
# Merge initial env-supplied IDs into the runtime set so a re-render of
# bot.env that adds admins propagates without operator action.
_admin_ids: set[int] = set(_state.get("admin_chat_ids", [])) | INITIAL_ADMIN_IDS


def _persist_admin_set() -> None:
    _state["admin_chat_ids"] = sorted(_admin_ids)
    _state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save_state(_state)


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------
def admin_only(handler: Callable) -> Callable:
    @wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            return
        if chat_id not in _admin_ids:
            log.info("rejecting command from non-admin chat_id=%s", chat_id)
            await update.message.reply_text(
                "⛔ Not authorised. This bot only accepts commands from its admin chats."
            )
            return
        await handler(update, ctx)
    return wrapper


# ---------------------------------------------------------------------------
# Helpers — backend dispatch + URI rendering moved to stealth_vps.bot_core
# in v0.8.1 so they can be pytest'd without a python-telegram-bot install.
# The bot module is now a thin async dispatcher; all the testable logic
# lives in bot_core.
# ---------------------------------------------------------------------------


def _bot_config() -> BotConfig:
    """Pack the env-fed module constants into a BotConfig the
    `stealth_vps.bot_core` dispatchers consume."""
    return BotConfig(
        users_index_path=USERS_INDEX,
        panel_state_path=PANEL_STATE_PATH,
        reloader_args_path=RELOADER_ARGS_PATH,
        panel_url=PANEL_URL,
        panel_username=PANEL_USERNAME,
        panel_password=PANEL_PASSWORD,
        reality_remark=REALITY_REMARK,
        reality_flow=REALITY_FLOW,
        use_sudo=USE_SUDO,
    )


def _uri_render_config() -> UriRenderConfig:
    """Pack URI-builder inputs from module env vars."""
    return UriRenderConfig(
        public_host=PUBLIC_HOST,
        reality_port=REALITY_PORT,
        reality_sni=REALITY_SNI,
        reality_pubkey=REALITY_PUBKEY,
        reality_short_id=REALITY_SHORTID,
        reality_fingerprint=REALITY_FINGERPRINT,
        reality_flow=REALITY_FLOW,
        reality_remark=REALITY_REMARK,
        hysteria_enabled=HYSTERIA_ENABLED,
        hysteria_port=HYSTERIA_PORT,
        hysteria_sni=HYSTERIA_SNI,
        hysteria_obfs_type=HYSTERIA_OBFS_TYPE,
        hysteria_obfs_password=HYSTERIA_OBFS_PASSWORD,
        hysteria_remark=HYSTERIA_REMARK,
        hysteria_insecure=HYSTERIA_INSECURE,
        hysteria_port_hop_min=int(HYSTERIA_HOP_MIN) if HYSTERIA_HOP_MIN else None,
        hysteria_port_hop_max=int(HYSTERIA_HOP_MAX) if HYSTERIA_HOP_MAX else None,
        # v0.11.0+ protocol params — each gated by its *_enabled flag.
        xhttp_enabled=XHTTP_ENABLED,
        xhttp_port=XHTTP_PORT,
        xhttp_path=XHTTP_PATH,
        xhttp_host_header=XHTTP_HOST_HEADER,
        xhttp_sni=XHTTP_SNI,
        vmess_ws_enabled=VMESS_WS_ENABLED,
        vmess_ws_port=VMESS_WS_PORT,
        vmess_ws_path=VMESS_WS_PATH,
        vmess_ws_host_header=VMESS_WS_HOST_HEADER,
        vmess_ws_sni=VMESS_WS_SNI,
        ss2022_enabled=SS2022_ENABLED,
        ss2022_port=SS2022_PORT,
        ss2022_method=SS2022_METHOD,
        ss2022_server_psk=SS2022_SERVER_PSK,
        trojan_enabled=TROJAN_ENABLED,
        trojan_port=TROJAN_PORT,
        trojan_sni=TROJAN_SNI,
        trojan_insecure=TROJAN_INSECURE,
    )


def _make_backend() -> UserBackend:
    """Thin wrapper around `bot_core.make_backend` — kept for the
    legacy call sites in the async handlers below."""
    return make_backend(_bot_config())


def _backend_is_headless(backend: UserBackend) -> bool:
    return backend_is_headless(backend)


def _systemctl_is_active(unit: str) -> str:
    """Return 'active' / 'inactive' / 'failed' / 'unknown' for a unit."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _systemctl_unit_exists(unit: str) -> bool:
    out = subprocess.run(
        ["systemctl", "list-unit-files", unit],
        capture_output=True, text=True, timeout=3,
    )
    return unit in out.stdout


def _build_uris_for_user(rec: dict[str, Any], *, label: str = "") -> list[str]:
    """URIs for one user. On a control box with registered data nodes,
    emit N × P URIs (one per node, per protocol) using the per-node
    Reality keys. On single-node / data-node hosts, the existing
    single-node URI render (using this host's env-fed config).
    """
    from stealth_vps import fleet as _fleet, bot_core as _bc
    if _bc.is_control_mode(_bot_config()):
        nodes = _fleet.load_fleet()
        if nodes:
            return _bc.build_uris_for_user_multinode(rec, nodes, label=label)
    return build_uris_for_user(rec, _uri_render_config())


def _sub_url_for(token: str) -> str:
    return sub_url_for(token, SUBSCRIPTION_PUBLIC_URL)


def _onboard_url_for(token: str) -> str:
    """The one-tap onboarding URL for a token, or '' when the onboard
    bridge isn't enabled. Just the configured prefix + token."""
    if not ONBOARD_PUBLIC_URL or not token:
        return ""
    return ONBOARD_PUBLIC_URL.rstrip("/") + "/" + token


async def _post_mutation_sync(label: str) -> tuple[bool, str]:
    """If this host is a control box with registered nodes, push the
    new users.index.json to each. Runs the (blocking) sync_all in a
    thread so the bot's event loop doesn't stall.

    Returns (all_ok, human_summary). The bot's `_user_add` /
    `_user_revoke` / `_sub_renew` calls this AFTER the local mutation
    has succeeded; the operator sees a brief "syncing…" then the
    summary inline in the same Telegram message.

    No-op (returns (True, "")) on single-node / data-node hosts.
    """
    from stealth_vps import bot_core as _bc, fleet as _fleet
    if not _bc.is_control_mode(_bot_config()):
        return True, ""
    nodes = _fleet.load_fleet()
    if not nodes:
        return True, ""

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None,
        lambda: _fleet.sync_all(nodes, USERS_INDEX),
    )
    lines: list[str] = []
    all_ok = True
    for r in results:
        if r.ok:
            lines.append(f"  ✓ `{r.node_id}` ({r.duration_ms}ms)")
        else:
            all_ok = False
            tail = (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]
            lines.append(f"  ✗ `{r.node_id}` ({r.duration_ms}ms): {tail}")
        try:
            _fleet.update_sync_status(
                r.node_id, status="ok" if r.ok else "failed",
            )
        except _fleet.FleetError:
            pass
    summary = (
        f"\n\n*Fleet sync ({len(nodes)} node{'s' if len(nodes) > 1 else ''}):*\n"
        + "\n".join(lines)
    )
    if not all_ok:
        summary += (
            "\n\n⚠ partial sync. Re-run `s-vps fleet sync` from a shell "
            "to retry failed nodes."
        )
    return all_ok, summary


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Pairing: empty admin set means we're waiting for the first /start.
    if not _admin_ids:
        _admin_ids.add(chat_id)
        _persist_admin_set()
        log.info("paired with admin chat_id=%s", chat_id)
        await update.message.reply_text(
            "🔐 Pairing complete — this chat is now the bot admin.\n\n"
            "Send /help for the command list. Send /creds for your initial "
            "connection URIs."
        )
        return
    # Already paired: gated by admin_only.
    await admin_only(cmd_welcome)(update, ctx)


async def cmd_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "stealth-vps bot online.\n\n"
        "Try /help for the command list, /status for service health, "
        "or /creds for your connection URIs."
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*stealth-vps bot — admin commands*\n\n"
        "/status — service health summary\n"
        "/diagnose — re-run post-deploy checks\n"
        "/creds — DM the credentials file\n"
        "/user add <label> — create a client\n"
        "/user list — list enabled clients\n"
        "/user revoke <label> — disable a client\n"
        "/sub <label> — get a client's sub URL\n"
        "/sub revoke <label> — rotate sub token\n"
        "/sub renew <label> <ttl|clear> — set/clear expiry (e.g. 30d)\n"
        "/onboard <label> — one-tap onboarding link + QR (v0.12+)\n\n"
        "Labels must match `[a-zA-Z0-9_-]{1,32}`. "
        "Names starting with `stealth-vps-` are reserved.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    units = [
        ("xray", "Reality (Xray)"),
        ("x-ui", "3X-UI panel"),
        ("hysteria-server", "Hysteria2"),
        ("caddy", "Caddy (subs)"),
        ("stealth-vps-bot", "this bot"),
    ]
    lines = ["*Service status*", ""]
    for unit, label in units:
        if not _systemctl_unit_exists(f"{unit}.service"):
            continue
        st = _systemctl_is_active(f"{unit}.service")
        glyph = "✅" if st == "active" else "❌"
        lines.append(f"{glyph} `{label}` — {st}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_diagnose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Best-effort: shell out to `systemctl is-active` for each service plus
    # `ss -tlnp` to confirm port 443 is bound. We avoid `s-vps diagnose`
    # because that needs root for the TLS cert check.
    parts = ["*Diagnose*", ""]
    for unit, label in [
        ("xray.service", "Xray (Reality)"),
        ("x-ui.service", "Panel"),
        ("hysteria-server.service", "Hysteria2"),
        ("caddy.service", "Caddy"),
    ]:
        if not _systemctl_unit_exists(unit):
            continue
        st = _systemctl_is_active(unit)
        glyph = "✅" if st == "active" else "❌"
        parts.append(f"{glyph} {label}: {st}")
    # Port check.
    try:
        out = subprocess.run(["ss", "-tnlp"], capture_output=True, text=True, timeout=3)
        listening = ":443 " in out.stdout
        parts.append(("✅" if listening else "⚠️") + f" Reality port {REALITY_PORT}: " +
                     ("listening" if listening else "not detected on ss"))
    except Exception as exc:
        parts.append(f"⚠️ ss check failed: {exc}")
    await update.message.reply_text("\n".join(parts), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_creds(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not CREDS_FILE.exists():
        await update.message.reply_text(f"⚠ {CREDS_FILE} not present yet.")
        return
    try:
        body = CREDS_FILE.read_text("utf-8")
    except PermissionError:
        await update.message.reply_text(
            f"⛔ cannot read {CREDS_FILE} — re-run the Ansible role to chgrp it to the bot."
        )
        return
    # Telegram message cap is 4096 chars; send as document if larger.
    if len(body) > 3500:
        with CREDS_FILE.open("rb") as f:
            await update.message.reply_document(document=f, filename="stealth-vps-credentials.txt")
    else:
        await update.message.reply_text(f"```\n{body}\n```", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /user add <label> · /user list · /user revoke <label>"
        )
        return
    sub = args[0].lower()
    if sub == "add" and len(args) == 2:
        await _user_add(update, args[1])
    elif sub == "list":
        await _user_list(update)
    elif sub == "revoke" and len(args) == 2:
        await _user_revoke(update, args[1])
    else:
        await update.message.reply_text(
            "Unknown form. Try: /user add <label> · /user list · /user revoke <label>"
        )


async def _user_add(update: Update, label: str):
    if not state.label_valid(label):
        await update.message.reply_text(
            f"⛔ label `{label}` invalid. Must match `[a-zA-Z0-9_-]{{1,32}}` and "
            "not start with `stealth-vps-`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    backend = _make_backend()
    # Hysteria2 password sourcing differs by backend:
    #   - Panel mode (ThreeXUIBackend): 3X-UI's data model has one
    #     shared Hysteria password across all clients on the inbound,
    #     so we copy it from the existing default-client record via
    #     `collect_seed_hysteria_password`. Without that, the new
    #     client's URI would reference a password the running Hysteria
    #     daemon doesn't accept.
    #   - Headless mode (HeadlessBackend): per-user auth.userpass is
    #     the whole point — let `.add()` mint a fresh random password.
    hysteria_pw = (
        ""
        if _backend_is_headless(backend)
        else collect_seed_hysteria_password(USERS_INDEX)
    )
    try:
        rec = backend.add(label, hysteria_password=hysteria_pw)
    except Exception as exc:
        await update.message.reply_text(f"⛔ could not add `{label}`: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    # Multi-node aware: on a control box with registered data nodes,
    # the URI list is N × P entries (per-node Reality keys baked in).
    uris = _build_uris_for_user(rec, label=label)
    sub_url = ""
    if SUBSCRIPTION_ENABLED:
        try:
            write_subscription_file(rec["sub_token"], uris, dir=SUBSCRIPTIONS_DIR)
            sub_url = _sub_url_for(rec["sub_token"])
        except Exception as exc:
            log.warning("write_subscription_file failed: %s", exc)
    body = [f"✅ Added `{label}`\n"]
    for u in uris:
        body.append(f"```\n{u}\n```")
    if sub_url:
        body.append(f"\nSubscription URL:\n`{sub_url}`")
    # v0.10.0+: if this is a control box, propagate to data nodes.
    # On single-node hosts this is a no-op (returns (True, "")).
    _ok, sync_summary = await _post_mutation_sync(label)
    if sync_summary:
        body.append(sync_summary)
    await update.message.reply_text("\n".join(body), parse_mode=ParseMode.MARKDOWN)


async def _user_list(update: Update):
    try:
        rows = state.list_users(USERS_INDEX, include_disabled=False)
    except Exception as exc:
        await update.message.reply_text(f"⛔ could not read index: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    if not rows:
        await update.message.reply_text("No enabled users.")
        return
    lines = ["*Enabled users:*", ""]
    for label, rec in rows:
        lines.append(f"• `{label}` (created {rec.get('created_at', '?')})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def _user_revoke(update: Update, label: str):
    backend = _make_backend()
    try:
        rec = backend.get(label)
        if rec is None:
            await update.message.reply_text(f"⛔ user `{label}` not found.",
                                             parse_mode=ParseMode.MARKDOWN)
            return
        backend.revoke(label)
        # Best-effort sub cleanup.
        if rec.get("sub_token"):
            remove_subscription_file(rec["sub_token"], dir=SUBSCRIPTIONS_DIR)
    except Exception as exc:
        await update.message.reply_text(f"⛔ revoke failed: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    body = [f"✅ Revoked `{label}`."]
    _ok, sync_summary = await _post_mutation_sync(label)
    if sync_summary:
        body.append(sync_summary)
    await update.message.reply_text("\n".join(body), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  /sub <label>                  — show URL (refreshes the file)\n"
            "  /sub revoke <label>           — rotate the sub token\n"
            "  /sub renew <label> <ttl>      — set/bump expiry (e.g. 30d, 12h)\n"
            "  /sub renew <label> clear      — remove expiry (never expires)\n"
        )
        return
    if not SUBSCRIPTION_ENABLED:
        await update.message.reply_text(
            "⚠ Subscription endpoint disabled. Enable with "
            "`STEALTH_SUBSCRIPTION_ENABLED=true s-vps update`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if args[0].lower() == "revoke" and len(args) == 2:
        await _sub_revoke(update, args[1])
    elif args[0].lower() == "renew" and len(args) == 3:
        # /sub renew <label> <ttl-or-`clear`> — delegates to state via
        # the same compute_expiry / update_user the CLI uses. Parsing
        # lives in state.py; the bot is a thin presenter.
        await _sub_renew(update, args[1], args[2])
    elif len(args) == 1:
        await _sub_show(update, args[0])
    else:
        await update.message.reply_text(
            "Unknown form. Try: /sub <label> · /sub revoke <label> · "
            "/sub renew <label> <ttl|clear>"
        )


@admin_only
async def cmd_onboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """`/onboard <label>` — DM the one-tap onboarding link (+ QR when
    qrencode is present). v0.12.0+."""
    args = ctx.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /onboard <label>")
        return
    await _onboard_show(update, args[0])


async def _sub_show(update: Update, label: str):
    rec = state.get_user(label, USERS_INDEX)
    if rec is None:
        await update.message.reply_text(f"⛔ user `{label}` not found.",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    uris = _build_uris_for_user(rec, label=label)
    # Re-render the sub file so it reflects the current Reality / Hysteria
    # parameters (operator may have rotated keys since /user add). Multi-
    # node aware via _build_uris_for_user.
    try:
        write_subscription_file(rec["sub_token"], uris, dir=SUBSCRIPTIONS_DIR)
    except Exception as exc:
        log.warning("write_subscription_file refreshed failed: %s", exc)
    url = _sub_url_for(rec["sub_token"])
    body = [f"Subscription URL for `{label}`:\n`{url}`"]
    onboard = _onboard_url_for(rec["sub_token"])
    if onboard:
        body.append(f"\nOne-tap onboarding link (send this to the user):\n`{onboard}`")
    await update.message.reply_text(
        "\n".join(body), parse_mode=ParseMode.MARKDOWN,
    )


async def _onboard_show(update: Update, label: str):
    """`/onboard <label>` — DM the one-tap onboarding link, with a QR
    image when `qrencode` is available on the host (link-only fallback
    otherwise; ADR O5)."""
    rec = state.get_user(label, USERS_INDEX)
    if rec is None:
        await update.message.reply_text(f"⛔ user `{label}` not found.",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    onboard = _onboard_url_for(rec["sub_token"])
    if not onboard:
        await update.message.reply_text(
            "⚠ Onboarding bridge not enabled. Set "
            "`STEALTH_ONBOARD_ENABLED=true` (with a domain + exposed "
            "subscription endpoint) and `s-vps update`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        f"One-tap onboarding link for `{label}`:\n`{onboard}`\n\n"
        f"Send this to the user — they tap it and import into their "
        f"client app in one step.",
        parse_mode=ParseMode.MARKDOWN,
    )
    # Best-effort QR image via qrencode (ADR O5). Link-only if absent.
    if shutil.which("qrencode") is None:
        return
    try:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            png_path = f.name
        subprocess.run(
            ["qrencode", "-o", png_path, "-s", "8", onboard],
            check=True, capture_output=True,
        )
        with open(png_path, "rb") as img:
            await update.message.reply_photo(img, caption=f"Scan to onboard `{label}`",
                                             parse_mode=ParseMode.MARKDOWN)
        os.unlink(png_path)
    except Exception as exc:  # noqa: BLE001 — QR is best-effort
        log.warning("onboard QR generation failed: %s", exc)


async def _sub_revoke(update: Update, label: str):
    rec = state.get_user(label, USERS_INDEX)
    if rec is None:
        await update.message.reply_text(f"⛔ user `{label}` not found.",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    old_token = rec.get("sub_token", "")
    # Generate a new token, update the index, remove old file, write new.
    import secrets
    new_token = secrets.token_urlsafe(32).rstrip("=")
    idx = state.load_users_index(USERS_INDEX)
    idx["users"][label]["sub_token"] = new_token
    state.save_users_index(idx, USERS_INDEX)
    if old_token:
        remove_subscription_file(old_token, dir=SUBSCRIPTIONS_DIR)
    uris = _build_uris_for_user(idx["users"][label], label=label)
    try:
        write_subscription_file(new_token, uris, dir=SUBSCRIPTIONS_DIR)
    except Exception as exc:
        await update.message.reply_text(f"⛔ rotated token but write failed: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    body = [
        f"✅ Rotated sub token for `{label}`.",
        f"New URL: `{_sub_url_for(new_token)}`",
    ]
    _ok, sync_summary = await _post_mutation_sync(label)
    if sync_summary:
        body.append(sync_summary)
    await update.message.reply_text("\n".join(body), parse_mode=ParseMode.MARKDOWN)


async def _sub_renew(update: Update, label: str, ttl_or_clear: str):
    """Set or clear a user's `sub_expires_at`. `ttl_or_clear` is either
    the literal string `clear` (remove expiry) or a duration spec the
    state module's `parse_duration` understands (e.g. `30d`, `12h`).

    Bot-side mirror of the CLI's `s-vps sub renew`. We deliberately
    don't refresh the subscription file here — clearing/setting the TTL
    is a metadata change on the index row, not on the served bytes.
    The next `/sub <label>` (or the next ansible converge) will refresh.
    """
    rec = state.get_user(label, USERS_INDEX)
    if rec is None:
        await update.message.reply_text(
            f"⛔ user `{label}` not found.", parse_mode=ParseMode.MARKDOWN
        )
        return

    if ttl_or_clear.lower() == "clear":
        state.update_user(label, sub_expires_at=None, path=USERS_INDEX)
        await update.message.reply_text(
            f"✅ Cleared expiry on `{label}` (never expires).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        expires_at = state.compute_expiry(ttl_or_clear)
    except state.StateError as exc:
        await update.message.reply_text(
            f"⛔ ttl `{ttl_or_clear}` invalid: {exc}\n"
            f"Accepted units: s/m/h/d/w/mo/y (e.g. `30d`, `12h`, `1y`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    state.update_user(label, sub_expires_at=expires_at, path=USERS_INDEX)
    body = [
        f"✅ Renewed `{label}`.",
        f"`sub_expires_at` = `{expires_at}` (+{ttl_or_clear})",
    ]
    _ok, sync_summary = await _post_mutation_sync(label)
    if sync_summary:
        body.append(sync_summary)
    await update.message.reply_text("\n".join(body), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    log.info("stealth-vps-bot starting up — admin_chat_ids=%s (env=%s, persisted=%s)",
             sorted(_admin_ids), sorted(INITIAL_ADMIN_IDS),
             sorted(_state.get("admin_chat_ids", [])))
    if not _admin_ids:
        log.warning("admin set empty — entering pairing mode (next /start becomes admin)")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("diagnose", cmd_diagnose))
    app.add_handler(CommandHandler("creds", cmd_creds))
    app.add_handler(CommandHandler("user", cmd_user))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("onboard", cmd_onboard))
    log.info("polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("shutting down (SIGINT)")
