"""stealth_vps.onboard — subscription-bridge URL helpers (v0.12.0+).

The onboarding bridge is a static web page served by Caddy at
`/.well-known/stealth-vps-onboard/<token>` that turns a subscription
token into a one-tap client import (UA detection + deep-links + QR).
The page itself is HTML/CSS/JS under `files/onboard/`; this module is
the small, testable Python core the CLI + bot reuse:

  - onboard_url_for(token, base) → the friendly onboarding URL.
  - deeplink_for(client, sub_url) → a client deep-link (kept in Python
    too so a unit test can assert the JS table in onboard.js matches —
    the JS and Python deep-link templates must stay in lockstep).

Pure stdlib. The page does the real work client-side; Python only
builds the URLs the operator hands out + provides the parity anchor
for the JS deep-link table.

Design + ADRs: docs/internal/roadmap-v0.12-onboard-bridge.md
"""

from __future__ import annotations

import urllib.parse

# Default public path the role's Caddyfile serves the bundle under.
# Parallel to the subscription path. Operators override via
# stealth_vps_onboard_path in inventory; the CLI/bot read the resolved
# value from installer.env so this default only applies as a fallback.
DEFAULT_ONBOARD_PATH = "/.well-known/stealth-vps-onboard"

# Deep-link templates. ONE source of truth, mirrored verbatim in
# files/onboard/onboard.js (a unit test asserts the two agree). Each
# value is a format string with a single `{url}` placeholder for the
# URL-encoded subscription URL.
#
# Scheme strings VERIFIED against client releases (ADR O2):
#   hiddify   — Hiddify Next 2.x   `hiddify://install-sub?url=`
#   v2box     — V2Box 1.8+         `v2box://install-sub?url=`
#   nekobox   — NekoBox 1.3+       `sn://subscription?url=`
#   singbox   — sing-box 1.8+      `sing-box://import-remote-profile?url=`
#   streisand — Streisand (iOS)    `streisand://import/<raw-url>`
# (re-verify on each client major bump; record date in onboard.js.)
DEEPLINK_TEMPLATES = {
    "hiddify": "hiddify://install-sub?url={url}",
    "v2box": "v2box://install-sub?url={url}",
    "nekobox": "sn://subscription?url={url}",
    "singbox": "sing-box://import-remote-profile?url={url}",
    "streisand": "streisand://import/{url}",
}


class OnboardError(Exception):
    """Raised on malformed inputs to the URL builders."""


def onboard_url_for(token: str, base_url: str, *, path: str = DEFAULT_ONBOARD_PATH) -> str:
    """Build the onboarding URL an operator hands to a user.

    `base_url` is the public origin (e.g. `https://vpn.example.com`),
    typically the same host that serves the subscription endpoint.
    `token` is the user's `sub_token` — the onboard URL just wraps it
    in the friendly path; it carries no new secret.

    Returns `<base>/<path>/<token>`. Raises OnboardError on an empty
    token or one unsafe for a URL path segment (the token is operator-
    or server-generated url-safe base64, so this is belt-and-braces).
    """
    if not token or not token.strip():
        raise OnboardError("token must be non-empty")
    if "/" in token or token.startswith("."):
        raise OnboardError(f"token {token!r} unsafe for a URL path segment")
    base = base_url.rstrip("/")
    seg = path.strip("/")
    return f"{base}/{seg}/{token}"


def sub_url_for(token: str, base_url: str, *, sub_path: str = "/.well-known/stealth-vps-sub") -> str:
    """Build the raw subscription URL for a token. Mirrors what the
    onboard page constructs client-side; kept here so the CLI/bot can
    show both URLs without duplicating the path logic."""
    if not token or not token.strip():
        raise OnboardError("token must be non-empty")
    base = base_url.rstrip("/")
    seg = sub_path.strip("/")
    return f"{base}/{seg}/{token}"


def deeplink_for(client: str, sub_url: str) -> str:
    """Return a client deep-link that imports `sub_url`. `client` is one
    of DEEPLINK_TEMPLATES' keys. The sub URL is URL-encoded into the
    scheme's query (or path, for streisand). Raises OnboardError on an
    unknown client.

    This is the Python mirror of onboard.js's DEEPLINKS table — a unit
    test asserts the two produce identical output so the page never
    drifts from the documented schemes."""
    tmpl = DEEPLINK_TEMPLATES.get(client)
    if tmpl is None:
        raise OnboardError(
            f"unknown client {client!r}; known: {', '.join(sorted(DEEPLINK_TEMPLATES))}"
        )
    encoded = urllib.parse.quote(sub_url, safe="")
    return tmpl.format(url=encoded)
