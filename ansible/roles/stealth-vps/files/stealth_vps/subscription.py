"""Subscription file rendering.

A "subscription" in Reality/Hysteria2 client land is a single text
file containing base64-encoded URIs (one per line, pre-base64).
Clients fetch it once, parse it, and import every URI as a profile —
makes adding/removing users a server-side operation.

We materialise these files as static text under
`/var/lib/stealth-vps/subscriptions/<token>.txt`. Caddy serves them
at `/.well-known/stealth-vps-sub/<token>`. The bot writes the file
synchronously before returning the URL to the operator (so the next
fetch always sees the current state).
"""

from __future__ import annotations

import base64
import os
import tempfile
from typing import Iterable

SUBSCRIPTION_DIR = "/var/lib/stealth-vps/subscriptions"


def render_subscription_txt(uris: Iterable[str]) -> str:
    """Build the body of a subscription file.

    Format: each URI on its own line, then the whole thing
    base64-encoded once at the file level. Compatible with
    Hiddify / sing-box / v2rayNG.
    """
    joined = "\n".join(uris).strip()
    if not joined:
        return ""
    encoded = base64.b64encode(joined.encode("utf-8")).decode("ascii")
    return encoded + "\n"


def write_subscription_file(
    sub_token: str,
    uris: Iterable[str],
    *,
    dir: str = SUBSCRIPTION_DIR,
) -> str:
    """Atomically write the subscription file for `sub_token`. Returns
    the full filesystem path. Caller is responsible for the URL the
    operator sees (Caddy maps URL → file).
    """
    if not sub_token or "/" in sub_token or sub_token.startswith("."):
        # Defensive: token is used as a filename. Reject path-traversal
        # and anything that would land outside `dir`.
        raise ValueError(f"sub_token {sub_token!r} unsafe for use as a filename")

    os.makedirs(dir, mode=0o755, exist_ok=True)
    target = os.path.join(dir, f"{sub_token}.txt")
    body = render_subscription_txt(uris)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir,
        prefix=f".{sub_token}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        f.write(body)
        tmp_path = f.name
    os.chmod(tmp_path, 0o644)
    # os.replace, not rename — cross-platform atomic move-or-replace.
    # Operator re-issuing a sub for the same token (e.g. after rotation)
    # must overwrite the existing file, which Windows' rename refuses.
    os.replace(tmp_path, target)
    return target


def remove_subscription_file(sub_token: str, *, dir: str = SUBSCRIPTION_DIR) -> bool:
    """Delete the subscription file. Returns True if a file was removed,
    False if it didn't exist. Used by `/sub revoke`.
    """
    target = os.path.join(dir, f"{sub_token}.txt")
    try:
        os.unlink(target)
        return True
    except FileNotFoundError:
        return False
