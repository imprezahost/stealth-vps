"""Tests for stealth_vps.onboard — onboarding-bridge URL + deep-link
helpers, plus the parity check that onboard.js's DEEPLINKS table
matches the Python DEEPLINK_TEMPLATES (the JS and Python deep-link
schemes must stay in lockstep — a drift would ship a broken button).
"""

from __future__ import annotations

import pathlib
import re
import urllib.parse

import pytest

from stealth_vps import onboard


# ---------------------------------------------------------------------------
# onboard_url_for
# ---------------------------------------------------------------------------


def test_onboard_url_basic() -> None:
    url = onboard.onboard_url_for("TOK123", "https://vpn.example.com")
    assert url == "https://vpn.example.com/.well-known/stealth-vps-onboard/TOK123"


def test_onboard_url_strips_trailing_slash_on_base() -> None:
    url = onboard.onboard_url_for("T", "https://vpn.example.com/")
    assert url == "https://vpn.example.com/.well-known/stealth-vps-onboard/T"


def test_onboard_url_custom_path() -> None:
    url = onboard.onboard_url_for("T", "https://h", path="/x/onboard")
    assert url == "https://h/x/onboard/T"


def test_onboard_url_empty_token_raises() -> None:
    with pytest.raises(onboard.OnboardError, match="non-empty"):
        onboard.onboard_url_for("", "https://h")
    with pytest.raises(onboard.OnboardError, match="non-empty"):
        onboard.onboard_url_for("   ", "https://h")


def test_onboard_url_unsafe_token_raises() -> None:
    with pytest.raises(onboard.OnboardError, match="unsafe"):
        onboard.onboard_url_for("a/b", "https://h")
    with pytest.raises(onboard.OnboardError, match="unsafe"):
        onboard.onboard_url_for(".hidden", "https://h")


# ---------------------------------------------------------------------------
# sub_url_for
# ---------------------------------------------------------------------------


def test_sub_url_basic() -> None:
    url = onboard.sub_url_for("TOK", "https://vpn.example.com")
    assert url == "https://vpn.example.com/.well-known/stealth-vps-sub/TOK"


# ---------------------------------------------------------------------------
# deeplink_for
# ---------------------------------------------------------------------------


def test_deeplink_hiddify_encodes_sub_url() -> None:
    sub = "https://vpn.example.com/.well-known/stealth-vps-sub/TOK"
    dl = onboard.deeplink_for("hiddify", sub)
    assert dl.startswith("hiddify://install-sub?url=")
    # The sub URL is URL-encoded in the query.
    encoded = dl.split("url=", 1)[1]
    assert urllib.parse.unquote(encoded) == sub
    assert "://" in urllib.parse.unquote(encoded)   # decodes back to a URL


@pytest.mark.parametrize(
    "client,scheme_prefix",
    [
        ("hiddify", "hiddify://install-sub?url="),
        ("v2box", "v2box://install-sub?url="),
        ("nekobox", "sn://subscription?url="),
        ("singbox", "sing-box://import-remote-profile?url="),
        ("streisand", "streisand://import/"),
    ],
)
def test_deeplink_scheme_prefixes(client: str, scheme_prefix: str) -> None:
    dl = onboard.deeplink_for(client, "https://h/s/T")
    assert dl.startswith(scheme_prefix)


def test_deeplink_unknown_client_raises() -> None:
    with pytest.raises(onboard.OnboardError, match="unknown client"):
        onboard.deeplink_for("nonsuch", "https://h/s/T")


def test_deeplink_encodes_special_chars() -> None:
    """A sub URL with query chars must be fully encoded so it doesn't
    break the deep-link's own query parsing."""
    sub = "https://h/s/T?a=1&b=2"
    dl = onboard.deeplink_for("v2box", sub)
    # The `&` in the sub URL must be percent-encoded (%26), not literal.
    assert "%26" in dl
    encoded = dl.split("url=", 1)[1]
    assert urllib.parse.unquote(encoded) == sub


# ---------------------------------------------------------------------------
# JS ↔ Python deep-link parity (the contract that keeps the page honest)
# ---------------------------------------------------------------------------


def _onboard_js_path() -> pathlib.Path:
    # tests/python-pkg/ → repo root → ansible/.../files/onboard/onboard.js
    here = pathlib.Path(__file__).resolve()
    root = here.parents[2]
    return root / "ansible" / "roles" / "stealth-vps" / "files" / "onboard" / "onboard.js"


def test_onboard_js_deeplink_table_matches_python() -> None:
    """onboard.js must define a DEEPLINKS entry for every Python
    DEEPLINK_TEMPLATES client, with the same scheme prefix. The JS
    builds `scheme...${encodeURIComponent(u)}...`; we assert the
    literal scheme prefix (everything up to the encoded-URL insertion)
    appears in the JS for each client. A drift here = a broken import
    button shipped to users."""
    js = _onboard_js_path()
    assert js.exists(), f"onboard.js missing at {js}"
    text = js.read_text(encoding="utf-8")
    for client, tmpl in onboard.DEEPLINK_TEMPLATES.items():
        # The scheme prefix is the template up to the `{url}` marker.
        prefix = tmpl.split("{url}", 1)[0]
        assert prefix in text, (
            f"onboard.js is missing the {client!r} deep-link scheme "
            f"prefix {prefix!r} — JS/Python deep-link tables have drifted"
        )


def test_onboard_js_extracts_token_from_role_path() -> None:
    """onboard.js reads the token from location.pathname. Assert its
    extraction handles the role's actual onboard path — a regex/split
    mismatch would mean the page can't find the token + every button
    breaks. We check the JS references the path segment our Caddyfile
    serves under."""
    js = _onboard_js_path()
    text = js.read_text(encoding="utf-8")
    # The JS must reference the onboard path segment so its token
    # extraction lines up with what Caddy serves.
    assert "stealth-vps-onboard" in text
