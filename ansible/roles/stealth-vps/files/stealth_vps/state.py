"""State file I/O — atomic, schema-aware reads + writes for
/etc/stealth-vps/users.index.json (and related state files).

Atomicity: every write goes to a sibling temp file, then `os.replace()`
in place. POSIX `rename(2)` and Windows `MoveFileEx` with REPLACE_EXISTING
are both atomic for in-same-directory moves; `os.replace` wraps the
right syscall on each platform. Concurrent readers either see the old
file or the new file, never a partial.

Schema versions:
  v1 (v0.6.0+): {label: {reality_uuid, hysteria_password, sub_token,
                         created_at, enabled}}
  v2 (v0.9.0+): v1 + optional sub_expires_at (ISO 8601 UTC, nullable).
                Migration is automatic + silent — load_users_index
                accepts v1 files and writes them back as v2 the next
                time anything mutates the index.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from typing import Any

USERS_INDEX_PATH = "/etc/stealth-vps/users.index.json"

# Current schema version this code emits. Older versions are accepted
# on read for backwards compat — the load path auto-upgrades them.
CURRENT_SCHEMA_VERSION = 3

# Duration parser regex — accepts "30d", "12h", "4w", "6mo", "1y".
# We pick `mo` for months (rather than overloading `m` for minute vs
# month) because `Nm` is unambiguous as minutes elsewhere in the
# ecosystem (Prometheus, hysteria's bandwidth strings).
_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d|w|mo|y)$")
_DURATION_UNITS_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "mo": 30 * 24 * 60 * 60,    # nominal 30-day month — close enough
    "y": 365 * 24 * 60 * 60,    # nominal 365-day year
}

# Labels accepted for user names. The "stealth-vps-*" prefix is
# reserved for the role's own seed clients (default, system, etc.) so
# operator-created users can't accidentally collide.
LABEL_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
RESERVED_LABEL_PREFIX = "stealth-vps-"


class StateError(RuntimeError):
    """Raised on schema / IO problems with state files."""


def label_valid(label: str, allow_reserved: bool = False) -> bool:
    """True if `label` matches the [a-zA-Z0-9_-]{1,32} regex AND
    (unless allow_reserved) does not start with `stealth-vps-`.
    """
    if not LABEL_RE.match(label):
        return False
    if not allow_reserved and label.startswith(RESERVED_LABEL_PREFIX):
        return False
    return True


def load_users_index(path: str = USERS_INDEX_PATH) -> dict[str, Any]:
    """Read users.index.json. Returns the parsed dict.

    Raises StateError if the file is missing or unparseable. Callers
    that want a soft fallback should catch this explicitly.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise StateError(f"users.index.json missing at {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"users.index.json corrupt at {path}: {exc}") from exc

    if not isinstance(data, dict) or "version" not in data or "users" not in data:
        raise StateError(
            f"users.index.json at {path} has unexpected shape (missing 'version' or 'users')"
        )
    if data["version"] not in (1, 2, 3):
        raise StateError(
            f"users.index.json schema version {data['version']} unsupported by this code "
            f"(this code understands v1, v2, and v3)"
        )
    # In-memory upgrade chain — applied lazily on load, persisted on next save.
    #
    # v1 → v2 (v0.9.0): add `sub_expires_at` (subscription TTL).
    # v2 → v3 (v0.11.0): add the four new-protocol fields. `ss2022_psk`,
    #   `wireguard_pubkey`, `wireguard_client_ip`, `trojan_password`. All
    #   default to None — "this user hasn't been issued credentials for
    #   this protocol yet". The URI builder emits a per-protocol URI only
    #   when both the protocol is enabled on the host AND the user has a
    #   non-null value for the matching field.
    if data["version"] <= 1:
        for _label, rec in data["users"].items():
            rec.setdefault("sub_expires_at", None)
    if data["version"] <= 2:
        for _label, rec in data["users"].items():
            rec.setdefault("ss2022_psk", None)
            rec.setdefault("wireguard_pubkey", None)
            rec.setdefault("wireguard_client_ip", None)
            rec.setdefault("trojan_password", None)
    data["version"] = CURRENT_SCHEMA_VERSION
    return data


def save_users_index(data: dict[str, Any], path: str = USERS_INDEX_PATH) -> None:
    """Atomically write `data` to `path`. mode 0600. Owner stays whatever
    the caller's uid is (typically root when invoked from the bot or the
    metrics updater).
    """
    parent = os.path.dirname(path) or "/"
    # NamedTemporaryFile with delete=False lets us close it before
    # rename — Windows / Linux both rename across open handles ok in
    # this pattern, but explicit close is cleaner.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=parent,
        prefix=".users.index.",
        suffix=".tmp",
        delete=False,
    ) as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        tmp_path = f.name
    try:
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError:
        # Best-effort cleanup if the rename fails.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def add_user(
    label: str,
    *,
    reality_uuid: str,
    hysteria_password: str,
    sub_token: str,
    created_at: str,
    enabled: bool = True,
    sub_expires_at: str | None = None,
    ss2022_psk: str | None = None,
    wireguard_pubkey: str | None = None,
    wireguard_client_ip: str | None = None,
    trojan_password: str | None = None,
    path: str = USERS_INDEX_PATH,
    allow_reserved: bool = False,
) -> dict[str, Any]:
    """Append a user to the index and persist atomically. Returns the
    full updated index. Raises StateError on duplicate label or invalid
    label.

    `sub_expires_at` (v0.9.0+): ISO 8601 UTC timestamp after which the
    operator's subscription URL becomes invalid. Use `parse_duration`
    + `compute_expiry` to compute it from a human-readable "30d"-style
    input. None means never-expires (the v0.8.x default).

    v0.11.0+ optional fields (all default None — "not issued for this
    protocol yet"):
      - `ss2022_psk` — Shadowsocks-2022 per-user PSK (base64).
      - `wireguard_pubkey` — WireGuard client public key (base64).
      - `wireguard_client_ip` — assigned WG client IP (e.g. "10.99.0.5").
      - `trojan_password` — Trojan-Go per-user password.

    The URI builder skips a per-protocol URI when its field is None,
    so leaving any of these unset just means "no URI for that protocol
    in this user's subscription bundle." Operators can fill them in
    later via `update_user` without disturbing the existing fields.
    """
    if not label_valid(label, allow_reserved=allow_reserved):
        raise StateError(
            f"label {label!r} invalid (must match [a-zA-Z0-9_-]{{1,32}} and "
            f"not start with {RESERVED_LABEL_PREFIX!r} unless allow_reserved)"
        )
    data = load_users_index(path)
    if label in data["users"]:
        raise StateError(f"user {label!r} already exists in the index")
    data["users"][label] = {
        "reality_uuid": reality_uuid,
        "hysteria_password": hysteria_password,
        "sub_token": sub_token,
        "created_at": created_at,
        "enabled": enabled,
        "sub_expires_at": sub_expires_at,
        "ss2022_psk": ss2022_psk,
        "wireguard_pubkey": wireguard_pubkey,
        "wireguard_client_ip": wireguard_client_ip,
        "trojan_password": trojan_password,
    }
    save_users_index(data, path)
    return data


def revoke_user(label: str, path: str = USERS_INDEX_PATH) -> dict[str, Any]:
    """Set users[label].enabled = false. Does NOT delete the record —
    keeping the row around lets the operator see "this label was used
    and revoked" rather than a label disappearing. Hard delete is a
    separate, explicit operation (`purge_user`, wired in v0.8.0).
    """
    data = load_users_index(path)
    if label not in data["users"]:
        raise StateError(f"user {label!r} not found in the index")
    data["users"][label]["enabled"] = False
    save_users_index(data, path)
    return data


def purge_user(label: str, path: str = USERS_INDEX_PATH) -> dict[str, Any]:
    """Hard-delete a user from the index. Removes the row outright; the
    audit trail goes away with it. Operators wanting an audit-keeping
    revoke should use `revoke_user`. This exists for the case where a
    user is being completely cleaned up (e.g. ex-employee, leaked creds
    they want untraceable, GDPR-style erasure).

    Idempotent: purging a non-existent label is a no-op, returns the
    unchanged index. Caller distinguishes "user was there" via the
    pre-purge `get_user` call if needed.
    """
    data = load_users_index(path)
    if label in data["users"]:
        del data["users"][label]
        save_users_index(data, path)
    return data


_UNSET = object()  # sentinel — None is a valid value for sub_expires_at


def update_user(
    label: str,
    *,
    reality_uuid: str | None = None,
    hysteria_password: str | None = None,
    sub_token: str | None = None,
    enabled: bool | None = None,
    sub_expires_at: Any = _UNSET,
    ss2022_psk: Any = _UNSET,
    wireguard_pubkey: Any = _UNSET,
    wireguard_client_ip: Any = _UNSET,
    trojan_password: Any = _UNSET,
    path: str = USERS_INDEX_PATH,
) -> dict[str, Any]:
    """Patch one or more fields of an existing user. Returns the updated
    index. Raises StateError on unknown label.

    Used by `rotate_user`: the rotate operation needs to set three
    fields atomically (new uuid + new hy pw + new sub_token) while
    preserving `created_at` and `label`. A single load → mutate → save
    keeps the atomic rename pattern intact.

    Sentinel-defaulted fields (`sub_expires_at` + the four v0.11.0+
    protocol credentials) use `_UNSET` rather than None-as-default so
    callers can EXPLICITLY clear them by passing None — vs the
    str-defaulted fields where None means "don't touch this field".
    """
    data = load_users_index(path)
    if label not in data["users"]:
        raise StateError(f"user {label!r} not found in the index")
    rec = data["users"][label]
    if reality_uuid is not None:
        rec["reality_uuid"] = reality_uuid
    if hysteria_password is not None:
        rec["hysteria_password"] = hysteria_password
    if sub_token is not None:
        rec["sub_token"] = sub_token
    if enabled is not None:
        rec["enabled"] = enabled
    if sub_expires_at is not _UNSET:
        rec["sub_expires_at"] = sub_expires_at
    if ss2022_psk is not _UNSET:
        rec["ss2022_psk"] = ss2022_psk
    if wireguard_pubkey is not _UNSET:
        rec["wireguard_pubkey"] = wireguard_pubkey
    if wireguard_client_ip is not _UNSET:
        rec["wireguard_client_ip"] = wireguard_client_ip
    if trojan_password is not _UNSET:
        rec["trojan_password"] = trojan_password
    save_users_index(data, path)
    return data


def parse_duration(text: str) -> int:
    """Convert a human-readable duration like "30d" / "12h" / "6mo"
    into a number of seconds. Used by the CLI / bot's `--ttl` flag.

    Recognised units (all integer prefix):
      s seconds, m minutes, h hours, d days, w weeks, mo months
      (nominal 30d), y years (nominal 365d).

    Raises StateError on a malformed input — caller's job to surface
    that to the operator with the original input value.
    """
    if not isinstance(text, str):
        raise StateError(f"duration must be a string, got {type(text).__name__}")
    m = _DURATION_RE.match(text.strip().lower())
    if not m:
        raise StateError(
            f"duration {text!r} not recognised — expected like '30d', '12h', "
            f"'4w', '6mo', '1y'"
        )
    n = int(m.group(1))
    unit = m.group(2)
    return n * _DURATION_UNITS_SECONDS[unit]


def compute_expiry(
    duration_text: str,
    *,
    now: datetime.datetime | None = None,
) -> str:
    """`parse_duration(duration_text)` seconds from `now` (default: utcnow),
    formatted as the ISO 8601 UTC string the index uses ('...Z').
    `now=` accepted for testability.
    """
    secs = parse_duration(duration_text)
    base = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    target = base + datetime.timedelta(seconds=secs)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(text: str) -> datetime.datetime:
    """Parse the ISO 8601 UTC strings the index uses. The role writes
    `2026-01-01T00:00:00Z` (no fractional seconds, no offset other than
    Z). We accept that exact form plus any `fromisoformat`-parseable
    variant for robustness against operator hand-edits.
    """
    # Python 3.11+ accepts the trailing `Z`; older versions don't.
    # We normalise it to `+00:00` so 3.10 also works (which is what
    # the metrics updater runs on).
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(text)


def is_expired(rec: dict[str, Any], *, now: datetime.datetime | None = None) -> bool:
    """True iff `rec["sub_expires_at"]` is set and `<= now`. None /
    missing field → never expires → False. Operator hand-edited
    nonsense → StateError.
    """
    expires_at = rec.get("sub_expires_at")
    if expires_at is None:
        return False
    if not isinstance(expires_at, str):
        raise StateError(
            f"sub_expires_at expected ISO 8601 string or None, got "
            f"{type(expires_at).__name__}"
        )
    try:
        target = _parse_iso_utc(expires_at)
    except ValueError as exc:
        raise StateError(f"sub_expires_at {expires_at!r} not parseable: {exc}") from exc
    base = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    return target <= base


def expired_sub_tokens(
    path: str = USERS_INDEX_PATH,
    *,
    now: datetime.datetime | None = None,
) -> list[tuple[str, str]]:
    """Return [(label, sub_token), ...] for every user whose
    sub_expires_at is in the past. Used by the prune step that
    removes the operator-visible subscription file once a token has
    aged out (Caddy then returns 404, which clients treat as "this
    subscription is gone, ask the operator for a new one").
    """
    data = load_users_index(path)
    out: list[tuple[str, str]] = []
    for label, rec in data["users"].items():
        try:
            if is_expired(rec, now=now):
                token = rec.get("sub_token")
                if token:
                    out.append((label, token))
        except StateError:
            # Skip rows with garbled timestamps rather than fail the
            # whole prune. The bot's /user show would still flag the
            # offending row to the operator.
            continue
    return out


def get_user(label: str, path: str = USERS_INDEX_PATH) -> dict[str, Any] | None:
    """Return the user record, or None if absent."""
    data = load_users_index(path)
    return data["users"].get(label)


def list_users(path: str = USERS_INDEX_PATH, include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
    """Return [(label, record), ...] for users in the index. By default
    only enabled users; pass include_disabled=True to see revoked ones too.
    """
    data = load_users_index(path)
    return [
        (label, rec)
        for label, rec in sorted(data["users"].items())
        if include_disabled or rec.get("enabled", True)
    ]
