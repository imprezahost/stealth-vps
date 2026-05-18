"""s-vps operator CLI — Python-side subcommands.

Dispatched to from the bash wrapper at `files/s-vps`. The bash wrapper
keeps owning the legacy verbs (`update`, `diagnose`, `status`, `version`)
because those just shell out to ansible-pull / health-check helpers and
don't need state-aware Python. The verbs that mutate users.index.json
+ trigger the reloader live here:

    s-vps user add LABEL [--hysteria-password PW] [--label-allow-reserved]
    s-vps user revoke LABEL
    s-vps user list [--include-disabled] [--json]
    s-vps user show LABEL [--qr]              (qr is best-effort; off when
                                               python3-qrcode isn't on PATH)
    s-vps reload [--dry-run]                  Re-render configs + SIGHUP.
    s-vps migrate from-3xui [--rollback]      Panel → headless cutover
                                              (renames panel.state.yml so
                                              select_backend() picks
                                              HeadlessBackend on next start).

Selection rule (same as the bot): if /etc/stealth-vps/panel.state.yml
exists → panel mode → ThreeXUIBackend (double-write to panel API).
Otherwise → headless → HeadlessBackend (index-as-source-of-truth + reload).

Output style:
  - Mutating commands print a one-line summary on success, then the
    affected user's URIs / sub URL when relevant.
  - `list` is a fixed-width table; `--json` switches to NDJSON for shell
    pipelines.
  - Errors go to stderr with a non-zero exit. argparse usage text covers
    --help; we don't ship a separate man page.

The CLI is pure stdlib (matches the rest of the package) so it works on
any host the role has touched without an extra venv.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping

from . import state
from .backends import UserBackend
from .reloader import Reloader, ReloadError, load_state_file
from .threex_client import ThreeXUIClient
from .urivider import build_vless_uri, build_hysteria2_uri

# --- paths the role pins ----------------------------------------------------
# Same constants the role's templates + reloader hard-code. Keep them in
# sync with defaults/main.yml — the CLI can't tolerate drift here.

PANEL_STATE_PATH = "/etc/stealth-vps/panel.state.yml"
RELOADER_ARGS_PATH = "/etc/stealth-vps/reloader-args.json"
INSTALLER_ENV_PATH = "/etc/stealth-vps/installer.env"
REALITY_STATE_PATH = "/etc/stealth-vps/reality.state.yml"
HYSTERIA_STATE_PATH = "/etc/stealth-vps/hysteria.state.yml"
SUBSCRIPTION_BASE_URL_KEY = "STEALTH_VPS_SUB_BASE_URL"


# ---------------------------------------------------------------------------
# Backend bootstrapping
# ---------------------------------------------------------------------------


def _load_installer_env(path: str | None = None) -> dict[str, str]:
    """Parse the `KEY="value"` shell-fragment at `path`. Same format the
    bash wrapper sources. Tolerant of quoted/unquoted values. Returns an
    empty dict when the file's missing (fresh install, role not yet
    applied, etc.) — callers fall back to package defaults.

    Defaulting `path=None` and resolving `INSTALLER_ENV_PATH` inside the
    function lets tests monkeypatch the module attribute and have it
    actually take effect; a literal default arg is captured at function-
    definition time and ignores later patches.
    """
    if path is None:
        path = INSTALLER_ENV_PATH
    out: dict[str, str] = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        out[key] = value
    return out


def _load_reloader_args(path: str | None = None) -> dict[str, Any] | None:
    """Read the JSON kwargs blob ansible's headless_reload.yml writes.

    Returns the dict on success, None when the file is absent (e.g. on
    a panel-mode host where headless_reload.yml never runs). Caller
    decides how to surface that to the user — typically: refuse to
    construct a Reloader and tell them to re-run `s-vps update`.

    `path=None` resolves to `RELOADER_ARGS_PATH` at call time so
    monkeypatched test paths take effect.
    """
    if path is None:
        path = RELOADER_ARGS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"s-vps: {path} is not valid JSON ({exc}). Re-run `s-vps update` "
            f"to have ansible regenerate it, or fix manually if you know what "
            f"you're doing."
        )
    if not isinstance(data, dict):
        raise SystemExit(
            f"s-vps: {path} should contain a JSON object of Reloader kwargs."
        )
    return data


def _build_reloader(*, dry_run: bool = False) -> Reloader:
    """Construct a `Reloader` from the role-written args file. Falls
    back to package defaults when a field is missing (lets old converges
    still get a working CLI as long as the index exists).
    """
    args = _load_reloader_args() or {}
    args = dict(args)  # shallow copy so we can mutate
    args.setdefault("reality_servernames", ["www.microsoft.com"])
    if isinstance(args["reality_servernames"], str):
        # The role might serialise the list as a comma-string for the
        # CLI flag; accept that form too.
        args["reality_servernames"] = [
            s.strip() for s in args["reality_servernames"].split(",") if s.strip()
        ]
    args["dry_run"] = dry_run
    return Reloader(**args)


def _build_panel_client_from_state(panel_state_path: str | None = None) -> ThreeXUIClient:
    """Reconstruct the 3X-UI HTTP client from panel.state.yml. Same
    fields the bot loads (host, port, base_path, admin user/password)
    rolled into the `base_url` shape `ThreeXUIClient.__init__` expects.

    Raises SystemExit with an operator-readable error if anything's
    missing — the CLI fails the whole command and prints the fix-up
    instructions rather than trying to limp along.
    """
    if panel_state_path is None:
        panel_state_path = PANEL_STATE_PATH
    try:
        panel_state = load_state_file(panel_state_path)
    except ReloadError as exc:
        raise SystemExit(
            f"s-vps: can't read panel.state.yml ({exc}). Re-run "
            f"`s-vps update` to regenerate, or migrate to headless mode."
        )

    required = ["web_listen_host", "web_port", "web_base_path", "admin_user", "admin_password"]
    missing = [k for k in required if not panel_state.get(k)]
    if missing:
        raise SystemExit(
            f"s-vps: panel.state.yml missing fields {missing}. "
            f"Re-run `s-vps update --tags panel` to regenerate."
        )

    host = str(panel_state["web_listen_host"])
    port = int(panel_state["web_port"])
    base_path = str(panel_state["web_base_path"]).strip("/") or "panel"
    # 3X-UI listens HTTP on loopback behind Caddy; the bot uses the same
    # scheme (see stealth_vps_bot.py PANEL_URL). verify_tls=False because
    # the loopback hostname doesn't match the public cert CN anyway.
    base_url = f"http://{host}:{port}/{base_path}"

    return ThreeXUIClient(
        base_url=base_url,
        username=str(panel_state["admin_user"]),
        password=str(panel_state["admin_password"]),
        verify_tls=False,
    )


def _select_backend_for_cli(*, dry_run: bool = False) -> UserBackend:
    """The CLI's backend factory.

    Panel mode (panel.state.yml exists):
      ThreeXUIBackend wrapping a freshly constructed ThreeXUIClient.
      Mutations go to the panel API + double-write the index.

    Headless mode (panel.state.yml absent):
      HeadlessBackend wrapping a Reloader built from reloader-args.json.
      Mutations write the index + SIGHUP xray (+ hysteria-server if
      enabled in the args file).
    """
    from .backends import ThreeXUIBackend
    from .backends_headless import HeadlessBackend

    if os.path.exists(PANEL_STATE_PATH):
        client = _build_panel_client_from_state()
        # The remark + flow constants match the role's defaults; the bot
        # uses the same fields. Operators that override the role's
        # reality_remark also need to retag the inbound in the panel.
        # Pass users_index_path explicitly so test monkeypatches on
        # state.USERS_INDEX_PATH take effect — the kwarg default is
        # captured at class-definition time and doesn't follow the
        # patched module attribute.
        return ThreeXUIBackend(
            client,
            reality_remark="stealth-vps-reality",
            reality_flow="xtls-rprx-vision",
            users_index_path=state.USERS_INDEX_PATH,
        )
    reloader = _build_reloader(dry_run=dry_run)
    return HeadlessBackend(
        reloader=reloader,
        users_index_path=state.USERS_INDEX_PATH,
    )


# ---------------------------------------------------------------------------
# Helpers shared by user verbs
# ---------------------------------------------------------------------------


def _short(value: str, head: int = 6, tail: int = 4) -> str:
    """`'a1b2c3d4e5f6'` → `'a1b2c3…e5f6'`. Used to print sub_token /
    UUID hints in the list table without giving away the whole secret.
    A re-show with `s-vps user show LABEL` prints the full value.
    """
    if len(value) <= head + tail + 1:
        return value
    return f"{value[:head]}…{value[-tail:]}"


def _render_user_uris(
    label: str,
    rec: Mapping[str, Any],
    *,
    reality_state: Mapping[str, Any] | None = None,
    hysteria_state: Mapping[str, Any] | None = None,
    installer_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the URIs (vless + hysteria2 + sub URL) for one user. Each
    field is omitted from the result when the role's state file for it
    isn't present. The CLI's `user show` + `user add` reuse this.
    """
    out: dict[str, str] = {}
    env = dict(installer_env or _load_installer_env())
    host = env.get("STEALTH_DOMAIN") or env.get("STEALTH_VPS_PUBLIC_HOST") or "your.vps.example"

    if reality_state and rec.get("reality_uuid"):
        try:
            out["vless"] = build_vless_uri(
                uuid=rec["reality_uuid"],
                host=host,
                port=int(reality_state["port"]),
                sni=str(reality_state.get("client_servername", host)),
                public_key=str(reality_state["public_key"]),
                short_id=str(reality_state["short_id"]),
                remark=f"stealth-vps-reality-{label}",
            )
        except (KeyError, TypeError, ValueError):
            pass

    if hysteria_state and rec.get("hysteria_password"):
        try:
            out["hysteria2"] = build_hysteria2_uri(
                password=str(rec["hysteria_password"]),
                host=host,
                port=int(hysteria_state["port"]),
                sni=host,
                obfs_password=str(hysteria_state.get("obfs_password", "")),
                insecure=(env.get("STEALTH_DOMAIN", "") == ""),
                remark=f"stealth-vps-hysteria2-{label}",
            )
        except (KeyError, TypeError, ValueError):
            pass

    sub_token = rec.get("sub_token")
    sub_base = env.get(SUBSCRIPTION_BASE_URL_KEY)
    if sub_token and sub_base:
        out["sub"] = f"{sub_base.rstrip('/')}/{sub_token}"

    return out


def _load_states_for_render() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Best-effort load of reality + hysteria state. Missing files are
    fine — the URI builder skips that protocol's URI.
    """
    reality = None
    hysteria = None
    try:
        reality = load_state_file(REALITY_STATE_PATH)
    except ReloadError:
        pass
    try:
        hysteria = load_state_file(HYSTERIA_STATE_PATH)
    except ReloadError:
        pass
    return reality, hysteria


# ---------------------------------------------------------------------------
# user subcommands
# ---------------------------------------------------------------------------


def cmd_user_add(args: argparse.Namespace) -> int:
    backend = _select_backend_for_cli()
    try:
        rec = backend.add(args.label, hysteria_password=args.hysteria_password or "")
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1

    print(f"✓ added user {args.label!r}")
    print(f"  reality_uuid     : {rec['reality_uuid']}")
    print(f"  hysteria_password: {rec['hysteria_password']}")
    print(f"  sub_token        : {rec['sub_token']}")

    reality, hysteria = _load_states_for_render()
    uris = _render_user_uris(args.label, rec, reality_state=reality, hysteria_state=hysteria)
    if uris:
        print()
        if "vless" in uris:
            print(f"  vless URI       : {uris['vless']}")
        if "hysteria2" in uris:
            print(f"  hysteria2 URI   : {uris['hysteria2']}")
        if "sub" in uris:
            print(f"  subscription URL: {uris['sub']}")
    return 0


def cmd_user_revoke(args: argparse.Namespace) -> int:
    backend = _select_backend_for_cli()
    try:
        backend.revoke(args.label)
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1
    print(f"✓ revoked user {args.label!r}")
    return 0


def cmd_user_list(args: argparse.Namespace) -> int:
    # Read directly from the index — no need to construct a backend for
    # a read-only op. This also means `user list` works on a half-broken
    # host (panel down, reloader-args.json corrupt) as long as the index
    # file is readable. Reads `state.USERS_INDEX_PATH` at call time
    # (not as a default arg) so tests can monkeypatch the constant.
    try:
        idx = state.load_users_index(state.USERS_INDEX_PATH)
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1

    rows: list[tuple[str, dict[str, Any]]] = sorted(
        ((label, rec) for label, rec in idx["users"].items()
         if args.include_disabled or rec.get("enabled", True)),
        key=lambda kv: kv[0],
    )

    if args.json:
        for label, rec in rows:
            print(json.dumps({"label": label, **rec}, sort_keys=True))
        return 0

    if not rows:
        print("(no users in the index)")
        return 0

    print(f"{'LABEL':<32} {'STATUS':<10} {'REALITY_UUID':<38} {'SUB_TOKEN':<14} CREATED")
    print("-" * 110)
    for label, rec in rows:
        status = "enabled" if rec.get("enabled", True) else "REVOKED"
        print(
            f"{label:<32} {status:<10} "
            f"{rec.get('reality_uuid', '-'):<38} "
            f"{_short(rec.get('sub_token', '-'), 8, 4):<14} "
            f"{rec.get('created_at', '-')}"
        )
    return 0


def cmd_user_show(args: argparse.Namespace) -> int:
    rec = state.get_user(args.label, state.USERS_INDEX_PATH)
    if rec is None:
        print(f"s-vps: no user labelled {args.label!r} in the index", file=sys.stderr)
        return 1

    print(f"label            : {args.label}")
    print(f"status           : {'enabled' if rec.get('enabled', True) else 'REVOKED'}")
    print(f"reality_uuid     : {rec.get('reality_uuid', '-')}")
    print(f"hysteria_password: {rec.get('hysteria_password', '-')}")
    print(f"sub_token        : {rec.get('sub_token', '-')}")
    print(f"created_at       : {rec.get('created_at', '-')}")

    reality, hysteria = _load_states_for_render()
    uris = _render_user_uris(args.label, rec, reality_state=reality, hysteria_state=hysteria)
    if uris:
        print()
        if "vless" in uris:
            print(f"vless URI        : {uris['vless']}")
        if "hysteria2" in uris:
            print(f"hysteria2 URI    : {uris['hysteria2']}")
        if "sub" in uris:
            print(f"subscription URL : {uris['sub']}")

    if args.qr and uris:
        # Best-effort: shell out to `qrencode -t ANSIUTF8` if available.
        # Operators who want QR support `apt install qrencode`; we don't
        # vendor the lib into the stdlib-only package.
        for proto, uri in uris.items():
            if proto == "sub":
                continue
            print(f"\n--- {proto} QR ---")
            if shutil.which("qrencode") is None:
                print("(qrencode not installed — `apt install qrencode` for QR)")
                break
            try:
                subprocess.run(["qrencode", "-t", "ANSIUTF8", uri], check=True)
            except subprocess.CalledProcessError:
                pass

    return 0


# ---------------------------------------------------------------------------
# reload — re-render + SIGHUP
# ---------------------------------------------------------------------------


def cmd_reload(args: argparse.Namespace) -> int:
    if os.path.exists(PANEL_STATE_PATH):
        print(
            "s-vps: panel mode detected (panel.state.yml present). "
            "Reloading the standalone Xray + Hysteria2 configs would conflict "
            "with the 3X-UI panel's reconciliation pass. Run "
            "`s-vps migrate from-3xui` first if you meant to switch to "
            "headless mode.",
            file=sys.stderr,
        )
        return 2
    reloader = _build_reloader(dry_run=args.dry_run)
    try:
        reloader()
    except ReloadError as exc:
        print(f"s-vps reload: {exc}", file=sys.stderr)
        return 1
    print("✓ reload complete" + (" (dry-run)" if args.dry_run else ""))
    return 0


# ---------------------------------------------------------------------------
# migrate from-3xui — panel → headless cutover
# ---------------------------------------------------------------------------


def cmd_migrate_from_3xui(args: argparse.Namespace) -> int:
    """Panel → headless cutover.

    What this command DOES:
      1. Validates we're in panel mode (panel.state.yml present).
      2. Validates users.index.json exists and has at least one user.
         (ThreeXUIBackend double-writes the index on every panel mutation,
         so this is the expected state.)
      3. Renames panel.state.yml → panel.state.yml.before-migrate-<ts>.
         select_backend() now sees no panel file → picks HeadlessBackend.
      4. Prints the next-step instructions.

    What it DOES NOT do (operator's responsibility):
      - Stop / disable x-ui.service (operators may want it running as a
        rollback path until the headless side is verified).
      - Re-run ansible-pull with panel_enabled=false — the operator
        runs `s-vps update` after migrating. The migrate command is
        intentionally a single small atomic step.

    Rollback: `s-vps migrate from-3xui --rollback` renames the backup
    back to panel.state.yml. Only the LATEST backup is restored.
    """
    if args.rollback:
        # Find the most recent backup. Sort by name (backup names include
        # a timestamp so lexicographic sort is also chronological).
        parent = pathlib.Path(os.path.dirname(PANEL_STATE_PATH) or "/")
        candidates = sorted(parent.glob("panel.state.yml.before-migrate-*"))
        if not candidates:
            print("s-vps: no panel.state.yml.before-migrate-* backups found.", file=sys.stderr)
            return 1
        backup = candidates[-1]
        if os.path.exists(PANEL_STATE_PATH):
            print(
                f"s-vps: {PANEL_STATE_PATH} already exists — refusing to "
                f"clobber. Rename or remove it first.",
                file=sys.stderr,
            )
            return 1
        os.rename(backup, PANEL_STATE_PATH)
        print(f"✓ rolled back: restored {backup.name} → panel.state.yml")

        # Restore x-ui — migrate stopped + disabled it; rollback puts
        # it back. Operators who never want x-ui again can disable
        # it manually after the rollback; doing it here matches the
        # principle of least surprise (rollback = full undo).
        if shutil.which("systemctl") is not None:
            for verb in ("enable", "start"):
                try:
                    subprocess.run(
                        ["systemctl", verb, "x-ui.service"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    print(f"✓ x-ui.service {verb}d")
                except subprocess.CalledProcessError as exc:
                    print(f"  (systemctl {verb} x-ui.service: {exc.stderr.strip() or 'not found'})")
        print("  Re-run `s-vps update` to converge with panel_enabled=true.")
        return 0

    if not os.path.exists(PANEL_STATE_PATH):
        print(
            "s-vps: not in panel mode — panel.state.yml is missing. "
            "Already on headless? Run `s-vps user list` to confirm the "
            "index is intact.",
            file=sys.stderr,
        )
        return 1

    try:
        idx = state.load_users_index(state.USERS_INDEX_PATH)
    except state.StateError as exc:
        print(
            f"s-vps: {exc}\nThe index must exist before migrating — if "
            "you're on a v0.5 install that predates the double-write, "
            "upgrade to v0.6.4+ first and let ThreeXUIBackend populate "
            "users.index.json.",
            file=sys.stderr,
        )
        return 1
    if not idx.get("users"):
        print(
            "s-vps: users.index.json has zero users. Add at least one "
            "via the bot or `s-vps user add` before migrating, otherwise "
            "the headless-side Xray will start with empty clients[] and "
            "refuse to listen.",
            file=sys.stderr,
        )
        return 1

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = f"{PANEL_STATE_PATH}.before-migrate-{ts}"
    os.rename(PANEL_STATE_PATH, backup)
    print(f"✓ panel mode disabled: panel.state.yml → {os.path.basename(backup)}")

    # Stop x-ui before the headless converge so the standalone xray
    # service that ansible installs next can bind the Reality port.
    # `systemctl stop` is idempotent; failure is non-fatal (e.g. the
    # unit was already gone after an earlier abort). We always disable
    # too so a reboot doesn't start it again — operators rolling back
    # via `--rollback` need to re-enable explicitly.
    print()
    if shutil.which("systemctl") is not None:
        for verb in ("stop", "disable"):
            try:
                subprocess.run(
                    ["systemctl", verb, "x-ui.service"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                print(f"✓ x-ui.service {verb}ped")
            except subprocess.CalledProcessError as exc:
                # Most common reason: already-stopped / already-disabled.
                # Print stderr for clarity but don't fail the migrate.
                print(f"  (systemctl {verb} x-ui.service: {exc.stderr.strip() or 'not found'})")
    else:
        print("  (skipping x-ui stop — systemctl not on PATH; do it manually)")

    print()
    print("Next steps:")
    print("  1. Re-run `s-vps update` so ansible converges with panel_enabled=false.")
    print("     The role installs the standalone Xray + hysteria-per-user units.")
    print("       sudo STEALTH_PANEL_ENABLED=false s-vps update")
    print("  2. Run `s-vps diagnose` to validate the new path.")
    print()
    print("Rollback (within this session): `s-vps migrate from-3xui --rollback`")
    print("  (also re-enables x-ui.service)")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s-vps",
        description="stealth-vps operator CLI (user/reload/migrate subcommands)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- user ---------------------------------------------------------
    user = sub.add_parser("user", help="user management")
    user_sub = user.add_subparsers(dest="user_cmd", required=True)

    p = user_sub.add_parser("add", help="add a user to the index")
    p.add_argument("label", help="user label ([a-zA-Z0-9_-]{1,32}, no `stealth-vps-` prefix)")
    p.add_argument(
        "--hysteria-password",
        default="",
        help="override the auto-generated Hysteria2 password (useful during migration "
             "when the operator wants to reuse a known token).",
    )
    p.set_defaults(func=cmd_user_add)

    p = user_sub.add_parser("revoke", help="disable a user (does not delete)")
    p.add_argument("label")
    p.set_defaults(func=cmd_user_revoke)

    p = user_sub.add_parser("list", help="list users in the index")
    p.add_argument("--include-disabled", action="store_true",
                   help="show revoked users too (status=REVOKED).")
    p.add_argument("--json", action="store_true",
                   help="emit one JSON record per line (NDJSON).")
    p.set_defaults(func=cmd_user_list)

    p = user_sub.add_parser("show", help="show one user's details + URIs")
    p.add_argument("label")
    p.add_argument("--qr", action="store_true",
                   help="render terminal QR codes for the URIs (needs qrencode).")
    p.set_defaults(func=cmd_user_show)

    # --- reload -------------------------------------------------------
    p = sub.add_parser("reload", help="re-render configs + SIGHUP services (headless only)")
    p.add_argument("--dry-run", action="store_true",
                   help="render configs but skip `systemctl reload`. For debugging.")
    p.set_defaults(func=cmd_reload)

    # --- migrate ------------------------------------------------------
    migrate = sub.add_parser("migrate", help="migration helpers")
    migrate_sub = migrate.add_subparsers(dest="migrate_cmd", required=True)

    p = migrate_sub.add_parser(
        "from-3xui",
        help="cutover from 3X-UI panel mode to headless mode",
        description=cmd_migrate_from_3xui.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--rollback",
        action="store_true",
        help="restore the most recent panel.state.yml.before-migrate-* backup.",
    )
    p.set_defaults(func=cmd_migrate_from_3xui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
