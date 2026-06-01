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
import dataclasses
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
# v0.11.0+ — per-protocol state files. Presence on disk = "this host
# terminates this protocol"; absence = "skip auto-credential-gen for
# new users." The actual Xray inbound rendering for these protocols
# lands in Block A.2 (a follow-up MR in the v0.11 sprint).
SS2022_STATE_PATH = "/etc/stealth-vps/ss2022.state.yml"
XHTTP_STATE_PATH = "/etc/stealth-vps/xhttp.state.yml"
VMESS_WS_STATE_PATH = "/etc/stealth-vps/vmess_ws.state.yml"
# v0.11.0+ Block B — separate-daemon protocols.
TROJAN_GO_STATE_PATH = "/etc/stealth-vps/trojan_go.state.yml"
WIREGUARD_STATE_PATH = "/etc/stealth-vps/wireguard.state.yml"
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


def _is_control_box() -> bool:
    """Best-effort detection of "this host is a stealth-vps control".
    The role asserts mutex at converge time so reality.state.yml is
    never present on a control. Falling back on the state file rather
    than re-parsing the role's flag means the detection works without
    pulling in PyYAML.
    """
    return not os.path.exists(REALITY_STATE_PATH)


def _select_backend_for_cli(*, dry_run: bool = False) -> UserBackend:
    """The CLI's backend factory.

    Panel mode (panel.state.yml exists):
      ThreeXUIBackend wrapping a freshly constructed ThreeXUIClient.
      Mutations go to the panel API + double-write the index.

    Headless mode (panel.state.yml absent, reality.state.yml present):
      HeadlessBackend wrapping a Reloader built from reloader-args.json.
      Mutations write the index + SIGHUP xray (+ hysteria-server if
      enabled in the args file).

    Control mode (reality.state.yml absent — v0.10.0+):
      HeadlessBackend with NO reloader. Mutations write the index;
      propagation to data nodes is done by the caller via
      `_post_mutation_sync` (Step 6), which invokes `fleet.sync_all`.
      Control boxes don't terminate proxy traffic so there's nothing
      local to reload.
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
    if _is_control_box():
        # Control box: no local Xray/Hysteria → no Reloader. Sync to
        # data nodes is fired by `_post_mutation_sync` after the
        # backend method returns. HeadlessBackend with reloader=None
        # falls back to its internal _noop_reloader.
        return HeadlessBackend(
            reloader=None,
            users_index_path=state.USERS_INDEX_PATH,
        )
    reloader = _build_reloader(dry_run=dry_run)
    return HeadlessBackend(
        reloader=reloader,
        users_index_path=state.USERS_INDEX_PATH,
    )


# ---------------------------------------------------------------------------
# Post-mutation sync (v0.10.0+ control mode)
# ---------------------------------------------------------------------------
#
# After every `s-vps user *` mutation on a control box, propagate the
# new users.index.json to every registered data node and refresh the
# affected user's subscription file with multi-node URIs.
#
# Single-node hosts and data nodes have `fleet/` empty (or absent), so
# `load_fleet` returns [] and this becomes a no-op. The bot uses the
# same helper via `bot_core` so the two surfaces stay aligned.


def _post_mutation_sync(
    args: argparse.Namespace,
    *,
    affected_user: dict[str, Any] | None = None,
    affected_label: str | None = None,
) -> None:
    """If we're on a control box with registered nodes, sync the
    index + refresh the affected user's subscription bundle. Prints
    per-node ✓/✗ on stdout; failures are reported to stderr but don't
    propagate (the mutation already succeeded locally — partial sync
    is operationally a retry-on-next-mutation, matching Open Question
    #5).

    `args.no_sync` (default False) skips both the SSH push and the
    subscription file refresh. Operators batching mutations should
    follow up with a manual `s-vps fleet sync` + `s-vps fleet
    refresh-subscriptions` (the latter lands as a CLI verb later).
    """
    if getattr(args, "no_sync", False):
        return
    from . import fleet as _fleet
    nodes = _fleet.load_fleet()
    if not nodes:
        return
    print()
    print(f"Propagating to {len(nodes)} data node(s)...")
    results = _fleet.sync_all(nodes, state.USERS_INDEX_PATH)
    all_ok = True
    for r in results:
        status = "✓" if r.ok else "✗"
        if not r.ok:
            all_ok = False
            detail = (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]
            print(f"  {status} {r.node_id} ({r.duration_ms}ms): {detail}")
        else:
            print(f"  {status} {r.node_id} ({r.duration_ms}ms)")
        # Persist per-node sync status.
        try:
            _fleet.update_sync_status(
                r.node_id, status="ok" if r.ok else "failed",
            )
        except _fleet.FleetError:
            pass
    if not all_ok:
        print("  (partial sync — re-run `s-vps fleet sync` to retry failed nodes)",
              file=sys.stderr)

    # Refresh the affected user's subscription file with multi-node URIs.
    # Skipped when the affected user has no sub_token (legacy rows from
    # pre-v0.6 panel installs that double-write didn't yet touch).
    if affected_user and affected_user.get("sub_token"):
        try:
            from . import bot_core
            from .subscription import write_subscription_file
            uris = bot_core.build_uris_for_user_multinode(
                affected_user, nodes, label=affected_label or "",
            )
            if uris:
                write_subscription_file(
                    affected_user["sub_token"], uris,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort
            print(f"  (subscription file refresh skipped: {exc})", file=sys.stderr)


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


def _autogen_ss2022_psk_for_method(method: str) -> str:
    """Generate a per-user SS-2022 PSK matching the cipher's required
    key length. Returns base64-encoded bytes; Xray's shadowsocks
    inbound accepts this shape directly.

    `2022-blake3-aes-128-gcm` → 16 bytes
    `2022-blake3-aes-256-gcm` + `2022-blake3-chacha20-poly1305` → 32 bytes
    Unknown methods → 32 bytes (longest valid; safe for forward compat
    with future ciphers that adopt 32-byte keys).
    """
    import base64
    import secrets
    n_bytes = 16 if method == "2022-blake3-aes-128-gcm" else 32
    return base64.b64encode(secrets.token_bytes(n_bytes)).decode("ascii")


def _maybe_autogen_ss2022_psk(
    ss2022_state_path: str | None = None,
) -> str | None:
    """If ss2022.state.yml is on disk, the host terminates SS-2022 and
    every new user should get a per-user PSK. Returns None when SS-2022
    isn't enabled on this host."""
    p = ss2022_state_path or SS2022_STATE_PATH
    if not os.path.exists(p):
        return None
    try:
        ss_state = load_state_file(p)
    except ReloadError:
        return None
    method = str(ss_state.get("method", "2022-blake3-aes-128-gcm"))
    return _autogen_ss2022_psk_for_method(method)


def _maybe_autogen_trojan_password(
    trojan_state_path: str | None = None,
) -> str | None:
    """If trojan_go.state.yml is on disk, the host runs Trojan-Go and
    every new user gets a per-user password. Returns None when Trojan-Go
    isn't enabled. Trojan passwords have no length constraint (unlike
    SS-2022 PSKs) — a 32-char URL-safe token matches the Hysteria2
    password style used elsewhere in the project."""
    import secrets
    p = trojan_state_path or TROJAN_GO_STATE_PATH
    if not os.path.exists(p):
        return None
    return secrets.token_urlsafe(24).rstrip("=")


def cmd_user_add(args: argparse.Namespace) -> int:
    backend = _select_backend_for_cli()
    try:
        rec = backend.add(args.label, hysteria_password=args.hysteria_password or "")
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1

    # `--ttl` is a second-step patch: backend.add already wrote the
    # row, now we set sub_expires_at on it. Two writes total per add-
    # with-ttl, but state.update_user re-uses the atomic-replace pattern
    # so concurrent readers never see a half-applied row.
    if args.ttl:
        try:
            expires_at = state.compute_expiry(args.ttl)
            state.update_user(args.label, sub_expires_at=expires_at, path=state.USERS_INDEX_PATH)
            rec["sub_expires_at"] = expires_at
        except state.StateError as exc:
            print(f"s-vps: ttl `{args.ttl}` invalid: {exc}", file=sys.stderr)
            return 1

    # v0.11.0+: SS-2022 per-user PSK. Operator override via `--ss2022-psk`
    # takes precedence; otherwise auto-gen when SS-2022 is enabled on
    # this host (detected via state file presence). When neither
    # condition holds, the user's `ss2022_psk` stays None — they have
    # no SS-2022 URI in their bundle, no exposure.
    ss2022_psk: str | None = None
    if getattr(args, "ss2022_psk", ""):
        ss2022_psk = args.ss2022_psk
    else:
        ss2022_psk = _maybe_autogen_ss2022_psk()
    if ss2022_psk is not None:
        state.update_user(
            args.label, ss2022_psk=ss2022_psk, path=state.USERS_INDEX_PATH,
        )
        rec["ss2022_psk"] = ss2022_psk

    # v0.11.0+ Block B: Trojan-Go per-user password. Same opt-in shape
    # as SS-2022 — explicit `--trojan-password` wins; else auto-gen when
    # trojan_go.state.yml exists; else stays None (no trojan:// URI).
    trojan_password: str | None = None
    if getattr(args, "trojan_password", ""):
        trojan_password = args.trojan_password
    else:
        trojan_password = _maybe_autogen_trojan_password()
    if trojan_password is not None:
        state.update_user(
            args.label, trojan_password=trojan_password, path=state.USERS_INDEX_PATH,
        )
        rec["trojan_password"] = trojan_password

    print(f"✓ added user {args.label!r}")
    print(f"  reality_uuid     : {rec['reality_uuid']}")
    print(f"  hysteria_password: {rec['hysteria_password']}")
    print(f"  sub_token        : {rec['sub_token']}")
    if rec.get("sub_expires_at"):
        print(f"  sub_expires_at   : {rec['sub_expires_at']} (TTL {args.ttl})")

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

    _post_mutation_sync(args, affected_user=rec, affected_label=args.label)
    return 0


def cmd_sub_renew(args: argparse.Namespace) -> int:
    """Bump (or set) a user's subscription expiry. Operator workflow:
    user comes back from vacation and reports their sub URL is 404'ing →
    `s-vps sub renew alice --ttl 30d` extends them by another 30 days.

    Pass --clear instead of --ttl to remove the expiry entirely
    (never-expires). Pass neither to inspect the current expiry.
    """
    rec = state.get_user(args.label, state.USERS_INDEX_PATH)
    if rec is None:
        print(f"s-vps: no user labelled {args.label!r} in the index", file=sys.stderr)
        return 1

    if args.clear:
        state.update_user(args.label, sub_expires_at=None, path=state.USERS_INDEX_PATH)
        print(f"✓ cleared expiry on {args.label!r} (never expires)")
        return 0

    if not args.ttl:
        cur = rec.get("sub_expires_at")
        if cur:
            print(f"  current sub_expires_at: {cur}")
        else:
            print(f"  {args.label!r} has no expiry (never expires)")
        print("Pass --ttl <duration> to (re)set the expiry, or --clear to remove it.")
        return 0

    try:
        expires_at = state.compute_expiry(args.ttl)
    except state.StateError as exc:
        print(f"s-vps: ttl `{args.ttl}` invalid: {exc}", file=sys.stderr)
        return 1
    state.update_user(args.label, sub_expires_at=expires_at, path=state.USERS_INDEX_PATH)
    print(f"✓ renewed {args.label!r}: sub_expires_at = {expires_at} (+{args.ttl})")
    return 0


def cmd_sub_prune(args: argparse.Namespace) -> int:
    """Delete the subscription file for every user whose `sub_expires_at`
    is in the past. Caddy then returns 404 on the URL — operationally
    equivalent to "the sub link is dead". The user record itself stays
    in the index (auditable), the operator can `sub renew` later to
    re-issue the sub file via the next `s-vps reload`.

    Idempotent: re-running over an already-pruned token deletes nothing
    and reports zero removals.

    Designed for a daily systemd timer — see tasks/sub_prune.yml.
    """
    from .subscription import SUBSCRIPTION_DIR, remove_subscription_file

    try:
        expired = state.expired_sub_tokens(state.USERS_INDEX_PATH)
    except state.StateError as exc:
        print(f"s-vps: could not read users.index.json: {exc}", file=sys.stderr)
        return 1

    removed = 0
    for label, token in expired:
        if remove_subscription_file(token, dir=SUBSCRIPTION_DIR):
            removed += 1
            if args.verbose:
                print(f"  removed sub file for {label!r} (token {token})")
    if args.verbose or removed > 0:
        print(f"✓ pruned {removed} expired subscription file(s) "
              f"({len(expired)} expired user(s) in the index)")
    return 0


def cmd_user_revoke(args: argparse.Namespace) -> int:
    backend = _select_backend_for_cli()
    try:
        backend.revoke(args.label)
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1
    print(f"✓ revoked user {args.label!r}")
    # Refresh sub file too — revoke flips enabled=false but doesn't
    # delete the sub_token, so the file would still serve URIs for a
    # disabled user. Operators wanting the URL to 404 should `purge`
    # (which deletes the row + the sub file). Revoke just stops the
    # data nodes from honouring the credentials.
    rec = backend.get(args.label) or {}
    _post_mutation_sync(args, affected_user=rec, affected_label=args.label)
    return 0


def cmd_user_purge(args: argparse.Namespace) -> int:
    """Hard-delete a user. Unlike `revoke` (which keeps the row with
    enabled=false), purge wipes the record outright. Idempotent — a
    purge of a non-existent label is treated as success.

    Also cleans up the per-user subscription file (best-effort: a
    missing sub file is fine, an unreadable one logs but doesn't fail
    the command).
    """
    backend = _select_backend_for_cli()
    # Capture the sub_token BEFORE purging so we can clean up the
    # subscription file even though `purge` will wipe the row.
    rec = backend.get(args.label)
    sub_token = rec.get("sub_token") if rec else None
    try:
        backend.purge(args.label)
    except state.StateError as exc:
        # Should be rare — purge is meant to be idempotent. Surface
        # whatever the backend complained about.
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1
    if sub_token:
        try:
            from .subscription import remove_subscription_file
            remove_subscription_file(sub_token)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            print(f"  (subscription file cleanup skipped: {exc})")
    if rec is None:
        print(f"✓ user {args.label!r} was not in the index (no-op)")
    else:
        print(f"✓ purged user {args.label!r}")
    # On a control box, push the now-shrunk index to data nodes so they
    # forget the user too. `affected_user=None` skips the sub file
    # refresh — purge deleted the sub file already.
    _post_mutation_sync(args, affected_user=None, affected_label=args.label)
    return 0


def cmd_user_rotate(args: argparse.Namespace) -> int:
    """Re-issue credentials for an existing user. Generates fresh UUID +
    Hysteria password + sub_token; preserves the label + created_at. If
    the user was revoked, this re-enables them — operators wanting a
    permanent revoke should use `revoke` or `purge`.
    """
    backend = _select_backend_for_cli()
    try:
        rec = backend.rotate(args.label, hysteria_password=args.hysteria_password or "")
    except state.StateError as exc:
        print(f"s-vps: {exc}", file=sys.stderr)
        return 1

    print(f"✓ rotated credentials for {args.label!r}")
    print(f"  reality_uuid     : {rec['reality_uuid']}")
    print(f"  hysteria_password: {rec['hysteria_password']}")
    print(f"  sub_token        : {rec['sub_token']}")
    print(f"  created_at       : {rec.get('created_at', '-')} (preserved)")

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
    print()
    print("⚠ The OLD credentials are now invalid — share the new URIs / sub URL")
    print("  with the user. Existing client connections will be dropped on next reload.")
    _post_mutation_sync(args, affected_user=rec, affected_label=args.label)
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
# fleet subcommands (v0.10.0+)
# ---------------------------------------------------------------------------
#
# Fleet management runs on a CONTROL box (`stealth_vps_control_enabled=true`
# in the role). Data nodes only see the `fleet-receive` HIDDEN top-level
# verb — invoked via a restricted-key authorized_keys command= clause,
# never by an operator directly.
#
# All fleet verbs delegate to `stealth_vps.fleet`; this file is a thin
# presenter that does argv parsing + table rendering + operator prompts.


SSH_OPTIONS_BASE = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
)


def _generate_ed25519_keypair(path: str) -> None:
    """Shell out to `ssh-keygen -t ed25519 -N "" -f <path>`. Open Question
    #1 (locked): we prefer subprocess to avoid pulling cryptography in.
    Idempotent at the OS level — refuses to overwrite an existing file
    so an accidental `fleet add` twice doesn't clobber a live keypair.
    """
    if os.path.exists(path):
        raise SystemExit(
            f"s-vps fleet: key already exists at {path}. "
            f"Use `s-vps fleet rotate-key` to refresh or delete manually first."
        )
    # ssh-keygen writes <path> (private) + <path>.pub. Comment names the
    # key so it's identifiable in ssh-agent listings + authorized_keys.
    comment = f"control_to_{os.path.basename(path).replace('control_to_', '').replace('.ed25519', '')}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = [
        "ssh-keygen", "-t", "ed25519", "-N", "",
        "-f", path, "-C", comment, "-q",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"s-vps fleet: `ssh-keygen` not found on PATH ({exc}). "
            f"Install openssh-client (`apt install openssh-client`)."
        )
    if result.returncode != 0:
        raise SystemExit(
            f"s-vps fleet: ssh-keygen failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '(no stderr)'}"
        )
    # ssh-keygen writes 0600 on the private file by default; pubkey is 0644.
    # No extra chmod needed.


def _ssh_run(
    node: "state.FleetNode",      # type: ignore[name-defined]  # forward ref via stealth_vps.fleet
    *remote_argv: str,
    stdin: bytes | None = None,
    timeout: float = 30.0,
    extra_ssh_opts: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    """Run `remote_argv` on `node` via SSH. Returns the CompletedProcess
    so callers can inspect stdout/stderr/returncode.

    Note: when the data node's authorized_keys has a restricted command=
    clause (after lockdown), `remote_argv` is IGNORED on the remote side
    — the forced command runs instead. We pass it anyway for the
    pre-lockdown bootstrap (where the key is unrestricted) and to be
    a useful diagnostic when SSH is bypassed manually."""
    from . import fleet as _fleet
    cmd = [
        "ssh",
        "-i", node.ssh_key_path,
        "-p", str(node.ssh_port),
        *SSH_OPTIONS_BASE,
        *extra_ssh_opts,
        f"{node.ssh_user}@{node.ssh_host}",
        *remote_argv,
    ]
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _probe_remote_version(node) -> str | None:
    """SSH into `node` and run `s-vps version`. Parses the first line
    (`stealth-vps:      v0.9.0`) and returns the tag string. None if
    something went wrong — caller decides whether to fail or proceed."""
    try:
        result = _ssh_run(node, "/usr/local/bin/s-vps", "version", timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    for raw in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("stealth-vps:"):
            return line.split(":", 1)[1].strip()
    return None


def _slurp_remote_yaml(node, remote_path: str) -> dict:
    """Read `remote_path` from the data node via `cat` over SSH and
    parse it as the project's narrow YAML dialect. Used by `fleet add`
    to discover reality.state.yml + hysteria.state.yml on the remote
    without needing a separate tooling install."""
    from .fleet import _parse_node_yaml  # YAML grammar matches state files
    result = _ssh_run(node, "cat", remote_path, timeout=10)
    if result.returncode != 0:
        raise SystemExit(
            f"s-vps fleet: could not read {remote_path} on {node.node_id} "
            f"(exit {result.returncode}): {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return _parse_node_yaml(result.stdout.decode("utf-8", errors="replace"))


def _install_restricted_authorized_keys(node, pubkey_text: str) -> None:
    """SSH into `node` and replace any line containing our pubkey body
    with a restricted entry that forces `s-vps fleet-receive` as the
    only thing this key can do. Other authorized_keys lines are
    preserved (operator's other keys keep working).

    The restricted form:

        command="/usr/local/bin/s-vps fleet-receive",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... control_to_X

    The match is on the pubkey body (the AAAA... blob), not the comment
    or the algorithm prefix — that way the operator's "I pasted it as
    `ssh-ed25519 AAAA... control_to_X`" works whether or not they kept
    the comment.
    """
    from . import fleet as _fleet
    pubkey_body = _extract_pubkey_body(pubkey_text)
    restricted_line = (
        f'command="/usr/local/bin/s-vps {_fleet.HIDDEN_RECEIVE_SUBCMD}",'
        f'no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding '
        f'{pubkey_text.strip()}'
    )
    # The rewrite is done in a single shell command on the remote so
    # there's no window where the file is empty (and root locks itself
    # out). awk: pass through every line that DOESN'T contain our
    # pubkey body, then append our restricted line.
    rewrite = (
        f"set -e; "
        f"AK=/root/.ssh/authorized_keys; touch \"$AK\"; chmod 0600 \"$AK\"; "
        f"awk -v PUB={shlex_quote(pubkey_body)} -v NEW={shlex_quote(restricted_line)} "
        f"'BEGIN {{ added=0 }} index($0, PUB) > 0 {{ if (!added) {{ print NEW; added=1 }}; next }} "
        f"{{ print }} END {{ if (!added) print NEW }}' \"$AK\" > \"$AK.new\" && "
        f"mv \"$AK.new\" \"$AK\""
    )
    result = _ssh_run(node, "sh", "-c", rewrite, timeout=15)
    if result.returncode != 0:
        raise SystemExit(
            f"s-vps fleet: could not install restricted authorized_keys "
            f"on {node.node_id}: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )


def _extract_pubkey_body(pubkey_text: str) -> str:
    """Pull the base64 body out of `ssh-ed25519 AAAA... comment`. We
    match on the body alone (not the comment) so the operator pasting
    with or without our suggested comment still works."""
    parts = pubkey_text.strip().split()
    if len(parts) < 2 or not parts[0].startswith("ssh-"):
        raise SystemExit(
            f"s-vps fleet: pubkey doesn't look like an OpenSSH public key "
            f"(`ssh-ed25519 AAAA... comment`): {pubkey_text!r}"
        )
    return parts[1]


def shlex_quote(s: str) -> str:
    """POSIX-shell-safe single-quoting. Stdlib has shlex.quote but
    importing here keeps the function used in `_install_restricted_*`
    obvious in code review."""
    import shlex as _shlex
    return _shlex.quote(s)


# ---------------------------------------------------------------------------
# Public fleet verbs
# ---------------------------------------------------------------------------


def cmd_fleet_add(args: argparse.Namespace) -> int:
    """Register a previously-installed data node with this control box.

    Workflow:
      1. Validate node_id + check this node isn't already registered.
      2. Generate a dedicated ed25519 keypair (`/etc/stealth-vps/keys/
         control_to_<node_id>.ed25519`).
      3. Print the pubkey + ask the operator to install it on the
         data node's `/root/.ssh/authorized_keys` (any form — restricted
         or bare — we tighten it later).
      4. Probe the data node: `s-vps version` over SSH should respond
         with v0.9.0+ (multi-node requires v0.9 schema v2).
      5. Slurp `reality.state.yml` + `hysteria.state.yml` over SSH to
         discover the per-node Reality pubkey / short_id / ports.
      6. Atomic-write `/etc/stealth-vps/fleet/<node_id>.yml`.
      7. Install the restricted authorized_keys entry on the remote
         (locks the key to running `s-vps fleet-receive` only).
      8. Done. Operator can now `s-vps fleet sync` to push the index.
    """
    from . import fleet as _fleet

    try:
        _fleet.validate_node_id(args.label)
    except _fleet.FleetError as exc:
        print(f"s-vps fleet add: {exc}", file=sys.stderr)
        return 1

    # Refuse if already registered — prevents accidental key clobber.
    try:
        existing = _fleet.load_node(args.label, fleet_dir=args.fleet_dir)
    except _fleet.FleetError:
        existing = None
    if existing is not None:
        print(
            f"s-vps fleet add: {args.label!r} already registered at "
            f"{existing.ssh_user}@{existing.ssh_host}:{existing.ssh_port}. "
            f"Use `s-vps fleet remove {args.label}` first if you mean to re-register.",
            file=sys.stderr,
        )
        return 1

    key_path = os.path.join(args.keys_dir, f"control_to_{args.label}.ed25519")
    pubkey_path = f"{key_path}.pub"

    print(f"Generating ed25519 keypair → {key_path}")
    _generate_ed25519_keypair(key_path)

    pubkey_text = pathlib.Path(pubkey_path).read_text(encoding="utf-8").strip()

    print()
    print("=" * 72)
    print(f"Step 1/2 — Install this pubkey on {args.ssh_user}@{args.ssh_host}:")
    print()
    print(f"    {pubkey_text}")
    print()
    print(f"Suggested command (run ON {args.ssh_host} as root):")
    print()
    print(f"    echo '{pubkey_text}' >> /root/.ssh/authorized_keys")
    print(f"    chmod 0600 /root/.ssh/authorized_keys")
    print()
    print("(Add any form — bare or restricted — we tighten it after the probe.)")
    print("=" * 72)
    print()
    if not args.yes:
        # `input()` is the right primitive here. The CLI is interactive
        # by design — there's a `--yes` flag for non-interactive runs.
        try:
            input("Press Enter when the pubkey is installed (or Ctrl-C to abort)... ")
        except (EOFError, KeyboardInterrupt):
            print("\ns-vps fleet add: aborted", file=sys.stderr)
            # Roll back the keypair so the operator can retry cleanly.
            for p in (key_path, pubkey_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            return 1

    # Build a transient FleetNode so we can reuse _ssh_run.
    probe_node = _fleet.FleetNode(
        node_id=args.label,
        ssh_host=args.ssh_host,
        ssh_port=args.ssh_port,
        ssh_user=args.ssh_user,
        ssh_key_path=key_path,
    )

    print(f"Step 2/2 — Probing {args.ssh_user}@{args.ssh_host}:{args.ssh_port}...")
    version = _probe_remote_version(probe_node)
    if version is None:
        print(
            f"s-vps fleet add: probe failed — could not run `s-vps version` "
            f"over SSH. Common causes:\n"
            f"  - pubkey not installed on the remote\n"
            f"  - SSH port/host wrong\n"
            f"  - `s-vps` not on PATH on the remote (re-run install.sh there)\n"
            f"Key kept at {key_path} so you can retry — re-run `s-vps fleet add`.",
            file=sys.stderr,
        )
        return 1
    print(f"  remote reports: stealth-vps {version}")

    # v0.9+ is the minimum because schema v2 (sub_expires_at) was
    # introduced there. Older boxes need to `s-vps update v0.9.0` first.
    if not version.startswith(("v0.9.", "v0.10.", "v0.11.", "v0.12.")):
        print(
            f"s-vps fleet add: remote runs {version}, which predates v0.9.0. "
            f"Multi-node requires schema v2 on the data node — run "
            f"`s-vps update v0.9.0` (or newer) on {args.ssh_host} first.",
            file=sys.stderr,
        )
        return 1

    print("  slurping reality.state.yml + hysteria.state.yml...")
    try:
        reality = _slurp_remote_yaml(probe_node, "/etc/stealth-vps/reality.state.yml")
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        hysteria = _slurp_remote_yaml(probe_node, "/etc/stealth-vps/hysteria.state.yml")
    except SystemExit:
        # Hysteria might be disabled on this node — soft-fail.
        hysteria = {}

    # Compose the FleetNode with discovered fields.
    node = _fleet.FleetNode(
        node_id=args.label,
        ssh_host=args.ssh_host,
        ssh_port=args.ssh_port,
        ssh_user=args.ssh_user,
        ssh_key_path=key_path,
        reality_public_key=str(reality.get("public_key", "")),
        reality_short_id=str(reality.get("short_id", "")),
        reality_port=int(reality.get("port", 0) or 0),
        reality_servernames=_servernames_from_state(reality),
        hysteria_port=int(hysteria.get("port", 0) or 0),
        hysteria_obfs_password=str(hysteria.get("obfs_password", "")),
        public_host=args.public_host or None,
        domain=args.domain or "",
        added_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        last_sync_at=None,
        last_sync_status="never",
    )

    print(f"  installing restricted authorized_keys on {args.ssh_host}...")
    try:
        _install_restricted_authorized_keys(probe_node, pubkey_text)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        print(
            f"  (the bare-form pubkey is still installed — `fleet sync` will "
            f"still work, but the key isn't yet restricted to fleet-receive)",
            file=sys.stderr,
        )

    _fleet.save_node(node, fleet_dir=args.fleet_dir)
    print()
    print(f"✓ registered {args.label!r}")
    print(f"  fleet file: {os.path.join(args.fleet_dir, args.label + '.yml')}")
    print(f"  ssh key   : {key_path}")
    print(f"  next      : `s-vps fleet sync` to push the current users.index.json")
    return 0


def _servernames_from_state(reality_state: dict) -> list[str]:
    """reality.state.yml stores `servernames:` as a list. Defensive
    accessor — returns [] when the field is missing or a non-list."""
    val = reality_state.get("servernames")
    if isinstance(val, list):
        return [str(x) for x in val if x]
    # The role's older versions wrote `servernames: ["a", "b"]` inline
    # vs current `servernames:\n  - a\n  - b`. We only handle the list form.
    if isinstance(val, str) and val:
        return [s.strip() for s in val.split(",") if s.strip()]
    return []


def cmd_fleet_rotate_key(args: argparse.Namespace) -> int:
    """Roll the SSH key for `<node>` without disconnecting it.

    Workflow:
      1. Load the existing FleetNode + verify its key still works
         (probe `s-vps version` over the OLD key).
      2. Generate a new ed25519 keypair at `<keys_dir>/control_to_<node>
         .ed25519.new`.
      3. Append the new pubkey (restricted form) to the data node's
         authorized_keys via the OLD key — file now holds BOTH entries.
      4. Probe `s-vps version` over the NEW key — must succeed.
      5. Remove the OLD entry from authorized_keys (over the new key).
      6. Locally: replace the old key files with the new ones
         (`mv .new → real`). The node's fleet/<id>.yml `ssh_key_path`
         is unchanged.
      7. Done. The next `fleet sync` uses the new key.

    Failure modes:
      - Step 1 fails → operator's old key already lost. Refuse to
        proceed (run `s-vps fleet add <node>` to re-bootstrap instead).
      - Step 4 fails → new key didn't take. Roll back by removing the
        new entry from authorized_keys (via the OLD key, which still
        works because we haven't removed it yet) + delete the local
        `.new` files. The node ends up exactly as it was.
      - Step 5 fails → new key works but old removal failed. Operator
        has two valid keys for one node. Print a warning + leave the
        files in place; operator can clean up manually.
    """
    from . import fleet as _fleet

    try:
        _fleet.validate_node_id(args.label)
    except _fleet.FleetError as exc:
        print(f"s-vps fleet rotate-key: {exc}", file=sys.stderr)
        return 1

    try:
        node = _fleet.load_node(args.label, fleet_dir=args.fleet_dir)
    except _fleet.FleetError as exc:
        print(f"s-vps fleet rotate-key: {exc}", file=sys.stderr)
        return 1

    # Step 1: verify the OLD key still works. Without this we have no
    # safe rollback path.
    print(f"Probing {args.label!r} via current key...")
    version = _probe_remote_version(node)
    if version is None:
        print(
            f"s-vps fleet rotate-key: current key for {args.label!r} doesn't "
            f"work — can't safely rotate. Re-bootstrap with "
            f"`s-vps fleet remove {args.label} && s-vps fleet add {args.label} "
            f"--ssh-host {node.ssh_host}` instead.",
            file=sys.stderr,
        )
        return 1
    print(f"  ✓ {version}")

    # Step 2: generate the new key in a `.new` sibling so the old key
    # stays usable until we're confident the new one works.
    new_key_path = f"{node.ssh_key_path}.new"
    new_pubkey_path = f"{new_key_path}.pub"
    # If a previous rotation crashed, the .new files may linger. Wipe them
    # so ssh-keygen doesn't refuse to overwrite.
    for p in (new_key_path, new_pubkey_path):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
    print(f"Generating new ed25519 keypair → {new_key_path}")
    _generate_ed25519_keypair(new_key_path)
    new_pubkey_text = pathlib.Path(new_pubkey_path).read_text(encoding="utf-8").strip()

    # Step 3: append the new pubkey to authorized_keys via the OLD key.
    # `_install_restricted_authorized_keys` finds-and-replaces by pubkey
    # body, so appending a NEW pubkey (different body from the old)
    # leaves the OLD entry untouched. Result: both keys valid.
    print(f"Installing new pubkey on {node.ssh_host} (via old key)...")
    try:
        _install_restricted_authorized_keys(node, new_pubkey_text)
    except SystemExit as exc:
        # Rollback: nothing to undo on the remote (the install failed
        # before adding anything). Just clean up local files.
        print(str(exc), file=sys.stderr)
        for p in (new_key_path, new_pubkey_path):
            try:
                os.unlink(p)
            except OSError:
                pass
        return 1

    # Step 4: probe with the NEW key. Build a transient FleetNode that
    # points at the .new key so _probe_remote_version uses it.
    probe_with_new = dataclasses.replace(node, ssh_key_path=new_key_path)
    print("Probing with new key...")
    version = _probe_remote_version(probe_with_new)
    if version is None:
        # Rollback: remove the new pubkey from authorized_keys via the
        # OLD key, then delete the .new files locally. State is back to
        # exactly where we started.
        print(
            f"s-vps fleet rotate-key: new key probe failed. Rolling back...",
            file=sys.stderr,
        )
        try:
            _remove_pubkey_from_authorized_keys(node, new_pubkey_text)
            print("  rollback done — old key still valid, .new files deleted.")
        except SystemExit as exc:
            print(
                f"  ⚠ rollback FAILED ({exc}). The data node may have "
                f"both pubkeys in authorized_keys. Investigate manually.",
                file=sys.stderr,
            )
        for p in (new_key_path, new_pubkey_path):
            try:
                os.unlink(p)
            except OSError:
                pass
        return 1
    print(f"  ✓ {version}")

    # Step 5: remove the OLD entry from authorized_keys via the NEW key.
    # If this fails we end up with both keys present — annoying but
    # safe; operator sees a warning and can clean up.
    old_pubkey_path = f"{node.ssh_key_path}.pub"
    try:
        old_pubkey_text = pathlib.Path(old_pubkey_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(
            f"  ⚠ could not read old pubkey at {old_pubkey_path} ({exc}). "
            f"Old key will remain in authorized_keys.",
            file=sys.stderr,
        )
        old_pubkey_text = ""

    if old_pubkey_text:
        print("Removing old pubkey from authorized_keys (via new key)...")
        try:
            _remove_pubkey_from_authorized_keys(probe_with_new, old_pubkey_text)
        except SystemExit as exc:
            print(
                f"  ⚠ old-key removal failed ({exc}). Both keys remain on "
                f"the data node — fix manually with `ssh root@{node.ssh_host} "
                f"vi /root/.ssh/authorized_keys`.",
                file=sys.stderr,
            )

    # Step 6: atomic-replace local files. From this point onward the
    # node's recorded ssh_key_path resolves to the new key material.
    os.replace(new_key_path, node.ssh_key_path)
    os.replace(new_pubkey_path, old_pubkey_path)
    print(f"✓ rotated key for {args.label!r}")
    print(f"  next `s-vps fleet sync` will use the new key automatically.")
    return 0


def _remove_pubkey_from_authorized_keys(node, pubkey_text: str) -> None:
    """SSH into `node` and delete any line containing `pubkey_text`'s
    body from /root/.ssh/authorized_keys. Single-pass awk filter.

    Used both for rotate's "remove the old key" step + as the rollback
    path when a rotation's probe fails (remove the failed new key)."""
    pubkey_body = _extract_pubkey_body(pubkey_text)
    rewrite = (
        f"set -e; "
        f"AK=/root/.ssh/authorized_keys; touch \"$AK\"; chmod 0600 \"$AK\"; "
        f"awk -v PUB={shlex_quote(pubkey_body)} "
        f"'index($0, PUB) > 0 {{ next }} {{ print }}' \"$AK\" > \"$AK.new\" && "
        f"mv \"$AK.new\" \"$AK\""
    )
    result = _ssh_run(node, "sh", "-c", rewrite, timeout=15)
    if result.returncode != 0:
        raise SystemExit(
            f"s-vps fleet: could not remove pubkey from authorized_keys "
            f"on {node.node_id}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )


def cmd_fleet_remove(args: argparse.Namespace) -> int:
    """Drop a node from the fleet. Does NOT decommission the box.
    Removes the local YAML + (by default) the SSH key. The data node
    keeps running; the operator can `s-vps user list` on it to confirm
    the last-pushed index is still there.

    Pass `--keep-key` to retain the SSH key — useful when you're going
    to immediately re-register the same node with new metadata."""
    from . import fleet as _fleet

    try:
        _fleet.validate_node_id(args.label)
    except _fleet.FleetError as exc:
        print(f"s-vps fleet remove: {exc}", file=sys.stderr)
        return 1

    deleted = _fleet.remove_node(args.label, fleet_dir=args.fleet_dir)
    if not deleted:
        print(f"s-vps fleet remove: {args.label!r} not registered (no-op)")
        return 0

    key_path = os.path.join(args.keys_dir, f"control_to_{args.label}.ed25519")
    pubkey_path = f"{key_path}.pub"
    if not args.keep_key:
        for p in (key_path, pubkey_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        print(f"✓ unregistered {args.label!r} (yaml + ssh key removed)")
    else:
        print(f"✓ unregistered {args.label!r} (yaml removed, ssh key kept at {key_path})")
    return 0


def cmd_fleet_list(args: argparse.Namespace) -> int:
    """Print the fleet as a table (or NDJSON with --json)."""
    from . import fleet as _fleet

    nodes = _fleet.load_fleet(fleet_dir=args.fleet_dir)

    if args.json:
        for node in nodes:
            print(json.dumps(node.to_dict(), sort_keys=True))
        return 0

    if not nodes:
        print("(no nodes registered — `s-vps fleet add <label> --ssh-host X` to start)")
        return 0

    print(f"{'NODE_ID':<24} {'SSH_HOST':<22} {'PORT':<5} {'STATUS':<7} LAST_SYNC")
    print("-" * 90)
    for node in nodes:
        print(
            f"{node.node_id:<24} "
            f"{node.ssh_host:<22} "
            f"{node.ssh_port:<5} "
            f"{node.last_sync_status:<7} "
            f"{node.last_sync_at or '(never)'}"
        )
    return 0


def cmd_fleet_sync(args: argparse.Namespace) -> int:
    """Push users.index.json to every node (or one node with --node).
    Tabular output: per-node ✓/✗ + duration. Exit code is 0 only when
    every push succeeds; partial failures exit 1 so CI / cron noticesy.
    """
    from . import fleet as _fleet

    nodes = _fleet.load_fleet(fleet_dir=args.fleet_dir)
    if args.node:
        nodes = [n for n in nodes if n.node_id == args.node]
        if not nodes:
            print(
                f"s-vps fleet sync: no node {args.node!r} in the fleet. "
                f"`s-vps fleet list` to see what's registered.",
                file=sys.stderr,
            )
            return 1

    if not nodes:
        print("(no nodes to sync)")
        return 0

    print(f"Syncing users.index.json to {len(nodes)} node(s)...")
    results = _fleet.sync_all(
        nodes,
        state.USERS_INDEX_PATH,
        parallel=args.parallel,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    print()
    print(f"{'NODE_ID':<24} {'STATUS':<8} {'DURATION':<10} DETAIL")
    print("-" * 90)
    all_ok = True
    for r in results:
        status = "✓ ok" if r.ok else "✗ FAIL"
        if not r.ok:
            all_ok = False
        detail = (r.stderr.strip().splitlines() or [""])[-1] if not r.ok else ""
        if not r.ok and not detail:
            detail = "(no stderr)"
        if r.ok and r.stdout.strip():
            # fleet-receive emits a JSON line on success — show that.
            detail = r.stdout.strip().splitlines()[-1]
        print(
            f"{r.node_id:<24} "
            f"{status:<8} "
            f"{r.duration_ms} ms".ljust(35) + detail
        )

        # Update sync status on the local fleet file (only if not dry-run).
        if not args.dry_run:
            try:
                _fleet.update_sync_status(
                    r.node_id,
                    status="ok" if r.ok else "failed",
                    fleet_dir=args.fleet_dir,
                )
            except _fleet.FleetError as exc:
                # Defensive: a sync that succeeded but couldn't update the
                # status file (e.g. disk full) shouldn't fail the whole
                # command, but should print a warning.
                print(f"  (warning: couldn't update {r.node_id}.yml: {exc})",
                      file=sys.stderr)

    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# fleet-receive — HIDDEN top-level subcommand
# ---------------------------------------------------------------------------
#
# Invoked by the data node's restricted-key authorized_keys command=.
# Reads stdin (users.index.json), validates schema v2, atomic-replaces
# /etc/stealth-vps/users.index.json, fires Reloader. Prints a 1-line
# JSON status on stdout for the control to consume.
#
# Not exposed in `s-vps --help` (operators shouldn't invoke it directly)
# but reachable via the bash wrapper's dispatcher for safety / testing.


def cmd_fleet_receive(args: argparse.Namespace) -> int:
    """Data-node-side: receive a users.index.json over stdin, validate,
    install, reload. Designed to be called via SSH from the control,
    never interactively.

    Output: 1 JSON line on stdout. Exit 0 on success, non-zero on
    failure. Stderr carries human-readable error text for the control's
    `PushResult.stderr`.
    """
    raw = sys.stdin.buffer.read()
    if not raw:
        print("fleet-receive: empty stdin (no index payload)", file=sys.stderr)
        return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"fleet-receive: stdin is not JSON ({exc})", file=sys.stderr)
        return 1

    # Schema validation: v2 with `version` + `users` keys.
    if not isinstance(data, dict):
        print("fleet-receive: payload root must be an object", file=sys.stderr)
        return 1
    if data.get("version") not in (1, 2):
        print(
            f"fleet-receive: unsupported schema version {data.get('version')!r}",
            file=sys.stderr,
        )
        return 1
    if not isinstance(data.get("users"), dict):
        print("fleet-receive: `users` must be a mapping", file=sys.stderr)
        return 1

    # Atomic-replace via state.save_users_index, which uses the same
    # tempfile + os.replace pattern as the rest of the project.
    try:
        state.save_users_index(data, path=state.USERS_INDEX_PATH)
    except state.StateError as exc:
        print(f"fleet-receive: save_users_index failed: {exc}", file=sys.stderr)
        return 1

    # Fire the reloader so Xray + Hysteria2 pick up the new index. If
    # we're on a control box (no reloader-args.json), skip the reload
    # and just acknowledge the index update — fleet-receive on a
    # control is operationally undefined but shouldn't blow up.
    reload_status = "skipped"
    if os.path.exists(RELOADER_ARGS_PATH):
        try:
            reloader = _build_reloader()
            reloader()
            reload_status = "ok"
        except ReloadError as exc:
            print(f"fleet-receive: reload failed: {exc}", file=sys.stderr)
            print(json.dumps({
                "ok": False,
                "user_count": len(data["users"]),
                "reload": "failed",
                "error": str(exc),
            }))
            return 2   # partial: index installed, reload failed

    print(json.dumps({
        "ok": True,
        "user_count": len(data["users"]),
        "reload": reload_status,
    }))
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
    p.add_argument(
        "--ttl",
        default="",
        help="set a subscription expiry, e.g. `30d`, `12h`, `1y`. Accepted units: "
             "s/m/h/d/w/mo/y. When the TTL elapses, `s-vps sub prune` will delete the "
             "user's subscription file (operationally a 404). The user record itself "
             "stays in the index; the operator can `sub renew` to re-issue. Omit to "
             "create a never-expires user (current default).",
    )
    p.add_argument(
        "--ss2022-psk",
        default="",
        dest="ss2022_psk",
        help="(v0.11.0+) operator-supplied Shadowsocks-2022 per-user PSK "
             "(base64). When omitted, auto-generated to the right length "
             "for the host's configured cipher (read from ss2022.state.yml). "
             "On hosts without SS-2022 enabled, ignored — the user's "
             "ss2022_psk stays null and the URI builder skips the ss:// entry.",
    )
    p.add_argument(
        "--trojan-password",
        default="",
        dest="trojan_password",
        help="(v0.11.0+) operator-supplied Trojan-Go per-user password. "
             "When omitted, auto-generated when Trojan-Go is enabled on the "
             "host (trojan_go.state.yml present). Ignored on hosts without "
             "Trojan-Go — the user's trojan_password stays null.",
    )
    p.add_argument(
        "--no-sync",
        action="store_true",
        help="(control mode only) skip the post-mutation `fleet sync`. "
             "Use when batching many adds, then run `s-vps fleet sync` once at the end.",
    )
    p.set_defaults(func=cmd_user_add)

    p = user_sub.add_parser("revoke", help="disable a user (keeps the row with enabled=false)")
    p.add_argument("label")
    p.add_argument("--no-sync", action="store_true",
                   help="(control mode only) skip the post-mutation fleet sync.")
    p.set_defaults(func=cmd_user_revoke)

    p = user_sub.add_parser(
        "purge",
        help="hard-delete a user (removes the row outright; idempotent)",
    )
    p.add_argument("label")
    p.add_argument("--no-sync", action="store_true",
                   help="(control mode only) skip the post-mutation fleet sync.")
    p.set_defaults(func=cmd_user_purge)

    p = user_sub.add_parser(
        "rotate",
        help="re-issue an existing user's credentials (new UUID + hy pw + sub_token)",
    )
    p.add_argument("label")
    p.add_argument(
        "--hysteria-password",
        default="",
        help="override the auto-generated Hysteria2 password (rare; usually "
             "you want a fresh random one — the default).",
    )
    p.add_argument("--no-sync", action="store_true",
                   help="(control mode only) skip the post-mutation fleet sync.")
    p.set_defaults(func=cmd_user_rotate)

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

    # --- backup / restore --------------------------------------------
    # `backup` + `restore` delegate to stealth_vps.backup.main(). Wiring
    # them as proper sub.add_parser entries (rather than passthrough to
    # `python3 -m stealth_vps.backup`) lets `s-vps --help` advertise
    # them in the same place operators look for everything else.
    from . import backup as _backup_mod
    p = sub.add_parser(
        "backup",
        help="snapshot operator state to an age-encrypted .tar.age",
    )
    p.add_argument("--recipient", default="",
                   help="operator's age recipient (defaults to env "
                        "STEALTH_VPS_BACKUP_RECIPIENT).")
    p.add_argument("--output-dir", default=_backup_mod.DEFAULT_BACKUP_DIR,
                   help="directory for the .tar.age (default %(default)s).")
    p.add_argument("--source", dest="sources", action="append", default=None,
                   help="extra path to include (repeat for multiple).")
    p.set_defaults(func=_backup_mod.cmd_backup)

    p = sub.add_parser(
        "restore",
        help="decrypt + untar a .tar.age back over the live filesystem",
    )
    p.add_argument("archive", help="path to the .tar.age to restore")
    p.add_argument("--identity", required=True,
                   help="path to the operator's age identity file.")
    p.add_argument("--target-root", default="/",
                   help="prefix to extract under (tests pass tmp; default %(default)s).")
    p.set_defaults(func=_backup_mod.cmd_restore)

    # --- sub ----------------------------------------------------------
    # `sub` (subscription) verbs operate on the per-user expiry that ships
    # with schema v2. They don't talk to a backend — they mutate the index
    # directly + (for prune) delete files under /var/lib/stealth-vps/
    # subscriptions/. Headless reload doesn't need to fire because Caddy
    # serves whatever's in that dir; deleting the file is the 404.
    sub_grp = sub.add_parser("sub", help="subscription TTL / renew / prune helpers")
    sub_sub = sub_grp.add_subparsers(dest="sub_cmd", required=True)

    p = sub_sub.add_parser(
        "renew",
        help="set/bump/clear a user's subscription expiry",
        description=cmd_sub_renew.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("label")
    p.add_argument(
        "--ttl",
        default="",
        help="new TTL relative to now (e.g. `30d`). Same grammar as `user add --ttl`.",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="remove the expiry entirely — the user never expires again.",
    )
    p.set_defaults(func=cmd_sub_renew)

    p = sub_sub.add_parser(
        "prune",
        help="delete subscription files for users whose TTL has elapsed",
        description=cmd_sub_prune.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="log each removed sub_token (default: print summary only when >0 removed).",
    )
    p.set_defaults(func=cmd_sub_prune)

    # --- fleet (v0.10.0+) --------------------------------------------
    # Multi-node control plane verbs. Surfaced on every install, but
    # `fleet add` requires `stealth_vps_control_enabled=true` in the
    # role (which provisions /etc/stealth-vps/fleet/ + keys/).
    fleet_grp = sub.add_parser(
        "fleet",
        help="multi-node fleet management (v0.10+; requires control_enabled in role)",
    )
    fleet_sub = fleet_grp.add_subparsers(dest="fleet_cmd", required=True)

    p = fleet_sub.add_parser(
        "add",
        help="register a previously-installed data node",
        description=cmd_fleet_add.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("label", help="node label ([a-z0-9-]{1,32}, e.g. tokyo-1)")
    p.add_argument("--ssh-host", required=True,
                   help="data node's SSH host (IP or DNS).")
    p.add_argument("--ssh-port", type=int, default=22, help="default %(default)s")
    p.add_argument("--ssh-user", default="root", help="default %(default)s")
    p.add_argument("--public-host", default="",
                   help="hostname clients connect to (defaults to --ssh-host).")
    p.add_argument("--domain", default="",
                   help="LE cert CN on this node (for client URI rendering).")
    p.add_argument("--yes", "-y", action="store_true",
                   help="skip the interactive 'press Enter when pubkey is installed' prompt.")
    p.add_argument("--fleet-dir", default="/etc/stealth-vps/fleet")
    p.add_argument("--keys-dir", default="/etc/stealth-vps/keys")
    p.set_defaults(func=cmd_fleet_add)

    p = fleet_sub.add_parser(
        "remove",
        help="unregister a node (does NOT decommission the data node itself)",
        description=cmd_fleet_remove.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("label")
    p.add_argument("--keep-key", action="store_true",
                   help="keep the per-node SSH key instead of deleting it.")
    p.add_argument("--fleet-dir", default="/etc/stealth-vps/fleet")
    p.add_argument("--keys-dir", default="/etc/stealth-vps/keys")
    p.set_defaults(func=cmd_fleet_remove)

    p = fleet_sub.add_parser("list", help="list registered nodes")
    p.add_argument("--json", action="store_true",
                   help="emit one JSON record per line (NDJSON).")
    p.add_argument("--fleet-dir", default="/etc/stealth-vps/fleet")
    p.set_defaults(func=cmd_fleet_list)

    p = fleet_sub.add_parser(
        "rotate-key",
        help="roll the SSH key for one node (zero-downtime)",
        description=cmd_fleet_rotate_key.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("label")
    p.add_argument("--fleet-dir", default="/etc/stealth-vps/fleet")
    p.add_argument("--keys-dir", default="/etc/stealth-vps/keys")
    p.set_defaults(func=cmd_fleet_rotate_key)

    p = fleet_sub.add_parser(
        "sync",
        help="push users.index.json to all nodes in parallel",
        description=cmd_fleet_sync.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--node", default=None,
                   help="sync only this node (default: every registered node).")
    p.add_argument("--parallel", type=int, default=4,
                   help="concurrent pushes (default %(default)s; locked per Open Question #3).")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="per-node SSH timeout in seconds (default %(default)s).")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be pushed without actually pushing.")
    p.add_argument("--fleet-dir", default="/etc/stealth-vps/fleet")
    p.set_defaults(func=cmd_fleet_sync)

    # --- fleet-receive (data-node-side, called via SSH) ---------------
    # argparse's `help=SUPPRESS` doesn't fully hide subparsers — it
    # displays the literal `==SUPPRESS==` token. Compromise: a short
    # "(internal)" help string so operators reading `--help` know
    # they're not meant to invoke it directly. The data node's
    # restricted authorized_keys forces this command regardless of
    # what the SSH caller passes.
    p = sub.add_parser(
        "fleet-receive",
        help="(internal) receive users.index.json over stdin (data-node side, via SSH)",
        description="Data-node side: read users.index.json from stdin, "
                    "validate schema, atomic-replace, fire Reloader. "
                    "Called by the control over SSH; operators shouldn't "
                    "run this directly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_fleet_receive)

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
