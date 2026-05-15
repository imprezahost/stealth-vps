"""Tests for stealth_vps.urivider — vless:// and hysteria2:// URI builders."""

from __future__ import annotations

import urllib.parse

import pytest

from stealth_vps import urivider


# ---------------------------------------------------------------------------
# build_vless_uri
# ---------------------------------------------------------------------------


def test_build_vless_uri_full_round_trip() -> None:
    uri = urivider.build_vless_uri(
        uuid="00000000-0000-0000-0000-000000000001",
        host="vpn.example.com",
        port=443,
        sni="www.microsoft.com",
        public_key="PUBKEY",
        short_id="SHORTID",
        fingerprint="chrome",
        flow="xtls-rprx-vision",
        remark="alice",
    )
    parsed = urllib.parse.urlparse(uri)
    assert parsed.scheme == "vless"
    assert parsed.username == "00000000-0000-0000-0000-000000000001"
    assert parsed.hostname == "vpn.example.com"
    assert parsed.port == 443

    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q["type"] == "tcp"
    assert q["security"] == "reality"
    assert q["sni"] == "www.microsoft.com"
    assert q["fp"] == "chrome"
    assert q["pbk"] == "PUBKEY"
    assert q["sid"] == "SHORTID"
    assert q["flow"] == "xtls-rprx-vision"

    # Fragment is the URL-encoded remark.
    assert urllib.parse.unquote(parsed.fragment) == "alice"


def test_build_vless_uri_default_fingerprint_is_chrome() -> None:
    uri = urivider.build_vless_uri(
        uuid="u", host="h", port=1, sni="s", public_key="p", short_id="i",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["fp"] == "chrome"


def test_build_vless_uri_default_flow_is_xtls_rprx_vision() -> None:
    """Reality protocol pins the flow value — defaulting matters because
    clients reject Reality inbounds without this exact flow."""
    uri = urivider.build_vless_uri(
        uuid="u", host="h", port=1, sni="s", public_key="p", short_id="i",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["flow"] == "xtls-rprx-vision"


def test_build_vless_uri_special_chars_in_remark_get_url_encoded() -> None:
    uri = urivider.build_vless_uri(
        uuid="u", host="h", port=1, sni="s", public_key="p", short_id="i",
        remark="my client / spaces",
    )
    # The fragment should be url-quoted (no spaces, no slashes in raw form).
    fragment = urllib.parse.urlparse(uri).fragment
    assert " " not in fragment
    assert urllib.parse.unquote(fragment) == "my client / spaces"


# ---------------------------------------------------------------------------
# build_hysteria2_uri
# ---------------------------------------------------------------------------


def test_build_hysteria2_uri_full_round_trip() -> None:
    uri = urivider.build_hysteria2_uri(
        password="hy2-pw",
        host="vpn.example.com",
        port=443,
        sni="bing.com",
        obfs_type="salamander",
        obfs_password="obfs-pw",
        remark="alice",
    )
    parsed = urllib.parse.urlparse(uri)
    assert parsed.scheme == "hysteria2"
    assert parsed.hostname == "vpn.example.com"
    assert parsed.port == 443
    # The password is URL-encoded in the userinfo.
    assert urllib.parse.unquote(parsed.username or "") == "hy2-pw"

    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q["sni"] == "bing.com"
    assert q["obfs"] == "salamander"
    assert q["obfs-password"] == "obfs-pw"
    assert urllib.parse.unquote(parsed.fragment) == "alice"


def test_build_hysteria2_uri_with_port_hop_range_appends_range_suffix() -> None:
    """Port hopping is expressed in the URI as `host:base_port,min-max`."""
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=49440, sni="s",
        port_hop_range=(20000, 50000),
    )
    # urlparse won't grok the `,` syntax — assert via raw string contains.
    assert "@h:49440,20000-50000/" in uri


def test_build_hysteria2_uri_without_port_hop_uses_bare_host_port() -> None:
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=443, sni="s",
    )
    assert "@h:443/" in uri
    assert "," not in uri.split("?", 1)[0]   # nothing before the query


def test_build_hysteria2_uri_insecure_flag_appears_when_set() -> None:
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=1, sni="s", insecure=True,
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q.get("insecure") == "1"


def test_build_hysteria2_uri_insecure_flag_absent_by_default() -> None:
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=1, sni="s",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert "insecure" not in q


def test_build_hysteria2_uri_special_chars_in_password_get_url_encoded() -> None:
    uri = urivider.build_hysteria2_uri(
        password="p/w@with:special",
        host="h", port=1, sni="s",
    )
    parsed = urllib.parse.urlparse(uri)
    # Round-trip: the userinfo must decode back to the original.
    assert urllib.parse.unquote(parsed.username or "") == "p/w@with:special"


def test_build_hysteria2_uri_omits_empty_obfs_password() -> None:
    """When obfs_password is empty, the query string should not carry an
    empty `obfs-password=` parameter (clients treat that as a real value
    and authenticate against an empty string)."""
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=1, sni="s",
        obfs_type="salamander", obfs_password="",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert "obfs-password" not in q


@pytest.mark.parametrize(
    "obfs_type,expected_obfs",
    [
        ("salamander", "salamander"),
        ("", None),                     # empty obfs_type → no obfs param at all
    ],
)
def test_build_hysteria2_uri_obfs_type_handling(obfs_type: str, expected_obfs: str | None) -> None:
    uri = urivider.build_hysteria2_uri(
        password="pw", host="h", port=1, sni="s",
        obfs_type=obfs_type,
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    if expected_obfs is None:
        assert "obfs" not in q
    else:
        assert q["obfs"] == expected_obfs
