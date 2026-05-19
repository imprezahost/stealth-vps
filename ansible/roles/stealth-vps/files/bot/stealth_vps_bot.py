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
    Reloader,
    ThreeXUIBackend,
    ThreeXUIClient,
    UserBackend,
    build_hysteria2_uri,
    build_vless_uri,
    state,
    write_subscription_file,
)
from stealth_vps.backends_headless import HeadlessBackend  # noqa: E402
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

SUBSCRIPTION_PUBLIC_URL = _env("STEALTH_VPS_BOT_SUBSCRIPTION_PUBLIC_URL")

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
# Helpers
# ---------------------------------------------------------------------------
def _build_headless_reloader() -> Reloader:
    """Reconstruct a Reloader from the kwargs the role's
    `headless_reload.yml` writes to /etc/stealth-vps/reloader-args.json
    on every converge. Mirrors `stealth_vps.cli._build_reloader` exactly
    — same JSON file, same fallbacks — so a bot-triggered `/user add`
    produces byte-identical xray + hysteria configs to what the CLI
    would write. `use_sudo=USE_SUDO` because the bot runs as the
    `stealth-vps-bot` system user and needs the sudoers drop-in's
    NOPASSWD rule to `systemctl restart` the services.
    """
    try:
        with open(RELOADER_ARGS_PATH, "r", encoding="utf-8") as f:
            args: dict[str, Any] = json.load(f)
    except FileNotFoundError:
        log.warning(
            "no reloader-args.json at %s — falling back to package defaults. "
            "Run `s-vps update` so ansible regenerates the file.",
            RELOADER_ARGS_PATH,
        )
        args = {}
    # The role serialises servernames as a list; Reloader's kwarg is
    # iterable so either form works, but accept the CSV string form too
    # in case an operator hand-edits the JSON.
    if isinstance(args.get("reality_servernames"), str):
        args["reality_servernames"] = [
            s.strip() for s in args["reality_servernames"].split(",") if s.strip()
        ]
    args["use_sudo"] = USE_SUDO
    return Reloader(**args)


def _make_backend() -> UserBackend:
    """Return the right `UserBackend` for the bot to talk through.

    Selection rule (same as `stealth_vps.select_backend`):
      * panel.state.yml on disk → panel mode → ThreeXUIBackend.
      * panel.state.yml absent  → headless mode → HeadlessBackend +
        Reloader (which re-renders Xray/Hysteria configs from
        users.index.json and SIGHUPs / restarts the services on
        every add/revoke).

    The `PANEL_ENABLED` env var is the OPERATOR'S INTENT (set by
    installer.env); `panel.state.yml`'s presence is the ON-DISK FACT.
    We dispatch on the fact, not the intent, so a half-finished
    migration (panel disabled in the env but state file still around)
    still talks to the running panel until ansible converges away
    from it.
    """
    if os.path.exists(PANEL_STATE_PATH):
        if not PANEL_URL or not PANEL_USERNAME or not PANEL_PASSWORD:
            raise RuntimeError(
                "panel.state.yml present but bot.env is missing panel credentials. "
                "Re-run `s-vps update` to regenerate /etc/stealth-vps/bot.env."
            )
        client = ThreeXUIClient(
            base_url=PANEL_URL,
            username=PANEL_USERNAME,
            password=PANEL_PASSWORD,
            verify_tls=False,  # 127.0.0.1 loopback, self-signed
        )
        return ThreeXUIBackend(
            client,
            reality_remark=REALITY_REMARK,
            reality_flow=REALITY_FLOW,
            users_index_path=USERS_INDEX,
        )
    # Headless mode: every add/revoke goes through HeadlessBackend +
    # Reloader. add() generates a fresh per-user Hysteria2 password
    # (so revoking one user doesn't break the others), so unlike
    # the panel-mode path we DON'T copy the shared password from the
    # seed default client.
    return HeadlessBackend(
        users_index_path=USERS_INDEX,
        reloader=_build_headless_reloader(),
    )


def _backend_is_headless(backend: UserBackend) -> bool:
    """True when the active backend is HeadlessBackend. Used by the
    /user add handler to skip the panel-mode shared-password copy.
    """
    return isinstance(backend, HeadlessBackend)


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


def _build_uris_for_user(rec: dict[str, Any]) -> list[str]:
    """Return the list of connection URIs for a user record."""
    uris = [build_vless_uri(
        uuid=rec["reality_uuid"],
        host=PUBLIC_HOST,
        port=REALITY_PORT,
        sni=REALITY_SNI,
        public_key=REALITY_PUBKEY,
        short_id=REALITY_SHORTID,
        fingerprint=REALITY_FINGERPRINT,
        flow=REALITY_FLOW,
        remark=REALITY_REMARK,
    )]
    if HYSTERIA_ENABLED and rec.get("hysteria_password"):
        port_hop = None
        if HYSTERIA_HOP_MIN and HYSTERIA_HOP_MAX:
            port_hop = (int(HYSTERIA_HOP_MIN), int(HYSTERIA_HOP_MAX))
        uris.append(build_hysteria2_uri(
            password=rec["hysteria_password"],
            host=PUBLIC_HOST,
            port=HYSTERIA_PORT,
            sni=HYSTERIA_SNI,
            obfs_type=HYSTERIA_OBFS_TYPE,
            obfs_password=HYSTERIA_OBFS_PASSWORD,
            port_hop_range=port_hop,
            insecure=HYSTERIA_INSECURE,
            remark=HYSTERIA_REMARK,
        ))
    return uris


def _sub_url_for(token: str) -> str:
    if not SUBSCRIPTION_PUBLIC_URL:
        return ""
    return SUBSCRIPTION_PUBLIC_URL.rstrip("/") + "/" + token


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
        "/sub revoke <label> — rotate sub token\n\n"
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
    #     so we copy it from the existing default-client record.
    #     Without that, the new client's URI would reference a password
    #     the running Hysteria daemon doesn't accept.
    #   - Headless mode (HeadlessBackend): per-user auth.userpass is
    #     the whole point — let `.add()` mint a fresh random password
    #     and write it into both users.index.json and the rendered
    #     /etc/hysteria/config.yaml on the same reload cycle.
    hysteria_pw = ""
    if not _backend_is_headless(backend):
        try:
            idx = state.load_users_index(USERS_INDEX)
            for _label, rec in idx["users"].items():
                if rec.get("hysteria_password"):
                    hysteria_pw = rec["hysteria_password"]
                    break
        except Exception as exc:
            log.warning("could not read users.index.json to seed hysteria_pw: %s", exc)
    try:
        rec = backend.add(label, hysteria_password=hysteria_pw)
    except Exception as exc:
        await update.message.reply_text(f"⛔ could not add `{label}`: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    uris = _build_uris_for_user(rec)
    # Write the sub file too.
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
    await update.message.reply_text(f"✅ Revoked `{label}`.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /sub <label> · /sub revoke <label>"
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
    elif len(args) == 1:
        await _sub_show(update, args[0])
    else:
        await update.message.reply_text(
            "Unknown form. Try: /sub <label> · /sub revoke <label>"
        )


async def _sub_show(update: Update, label: str):
    rec = state.get_user(label, USERS_INDEX)
    if rec is None:
        await update.message.reply_text(f"⛔ user `{label}` not found.",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    uris = _build_uris_for_user(rec)
    # Re-render the sub file so it reflects the current Reality / Hysteria
    # parameters (operator may have rotated keys since /user add).
    try:
        write_subscription_file(rec["sub_token"], uris, dir=SUBSCRIPTIONS_DIR)
    except Exception as exc:
        log.warning("write_subscription_file refreshed failed: %s", exc)
    url = _sub_url_for(rec["sub_token"])
    await update.message.reply_text(
        f"Subscription URL for `{label}`:\n`{url}`",
        parse_mode=ParseMode.MARKDOWN,
    )


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
    uris = _build_uris_for_user(idx["users"][label])
    try:
        write_subscription_file(new_token, uris, dir=SUBSCRIPTIONS_DIR)
    except Exception as exc:
        await update.message.reply_text(f"⛔ rotated token but write failed: `{exc}`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(
        f"✅ Rotated sub token for `{label}`.\nNew URL: `{_sub_url_for(new_token)}`",
        parse_mode=ParseMode.MARKDOWN,
    )


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
    log.info("polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("shutting down (SIGINT)")
