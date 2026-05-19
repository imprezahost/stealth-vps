"""State file I/O — atomic, schema-aware reads + writes for
/etc/stealth-vps/users.index.json (and related state files).

Atomicity: every write goes to a sibling temp file, then `os.replace()`
in place. POSIX `rename(2)` and Windows `MoveFileEx` with REPLACE_EXISTING
are both atomic for in-same-directory moves; `os.replace` wraps the
right syscall on each platform. Concurrent readers either see the old
file or the new file, never a partial.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

USERS_INDEX_PATH = "/etc/stealth-vps/users.index.json"

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
    if data["version"] != 1:
        raise StateError(
            f"users.index.json schema version {data['version']} unsupported by this code"
        )
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
    path: str = USERS_INDEX_PATH,
    allow_reserved: bool = False,
) -> dict[str, Any]:
    """Append a user to the index and persist atomically. Returns the
    full updated index. Raises StateError on duplicate label or invalid
    label.
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


def update_user(
    label: str,
    *,
    reality_uuid: str | None = None,
    hysteria_password: str | None = None,
    sub_token: str | None = None,
    enabled: bool | None = None,
    path: str = USERS_INDEX_PATH,
) -> dict[str, Any]:
    """Patch one or more fields of an existing user. Returns the updated
    index. Raises StateError on unknown label.

    Used by `rotate_user`: the rotate operation needs to set three
    fields atomically (new uuid + new hy pw + new sub_token) while
    preserving `created_at` and `label`. A single load → mutate → save
    keeps the atomic rename pattern intact.
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
    save_users_index(data, path)
    return data


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
