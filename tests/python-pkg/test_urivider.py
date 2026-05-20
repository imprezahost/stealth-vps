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


# ---------------------------------------------------------------------------
# v0.11.0+ — XHTTP
# ---------------------------------------------------------------------------


def test_build_xhttp_uri_basic_shape() -> None:
    uri = urivider.build_xhttp_uri(
        uuid="00000000-0000-0000-0000-000000000001",
        host="vpn.example.com",
        port=443,
        path="/.well-known/xhttp-stream",
        sni="vpn.example.com",
        remark="stealth-vps-xhttp-alice",
    )
    parsed = urllib.parse.urlparse(uri)
    assert parsed.scheme == "vless"
    assert parsed.netloc == "00000000-0000-0000-0000-000000000001@vpn.example.com:443"
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q["type"] == "xhttp"
    assert q["security"] == "tls"
    assert q["path"] == "/.well-known/xhttp-stream"
    assert q["sni"] == "vpn.example.com"
    assert "flow" not in q     # XHTTP doesn't use XTLS Vision flow
    assert urllib.parse.unquote(parsed.fragment) == "stealth-vps-xhttp-alice"


def test_build_xhttp_uri_optional_host_header() -> None:
    uri = urivider.build_xhttp_uri(
        uuid="u", host="vpn.example.com", port=443, path="/x",
        host_header="cf-fronted.example.com",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["host"] == "cf-fronted.example.com"


def test_build_xhttp_uri_sni_falls_back_to_host() -> None:
    uri = urivider.build_xhttp_uri(
        uuid="u", host="vpn.example.com", port=443, path="/x",
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["sni"] == "vpn.example.com"


# ---------------------------------------------------------------------------
# v0.11.0+ — VMess+WebSocket+TLS
# ---------------------------------------------------------------------------


def test_build_vmess_ws_uri_base64_json_round_trip() -> None:
    import base64
    import json
    uri = urivider.build_vmess_ws_uri(
        uuid="00000000-0000-0000-0000-000000000001",
        host="vpn.example.com",
        port=443,
        ws_path="/.well-known/vmess-ws",
        host_header="vpn.example.com",
        sni="vpn.example.com",
        remark="stealth-vps-vmess-alice",
    )
    assert uri.startswith("vmess://")
    body_b64 = uri[len("vmess://"):]
    body = json.loads(base64.b64decode(body_b64))
    assert body["v"] == "2"
    assert body["ps"] == "stealth-vps-vmess-alice"
    assert body["add"] == "vpn.example.com"
    assert body["port"] == "443"
    assert body["id"] == "00000000-0000-0000-0000-000000000001"
    assert body["aid"] == "0"
    assert body["net"] == "ws"
    assert body["path"] == "/.well-known/vmess-ws"
    assert body["tls"] == "tls"


def test_build_vmess_ws_uri_host_header_defaults_to_host() -> None:
    import base64
    import json
    uri = urivider.build_vmess_ws_uri(
        uuid="u", host="vpn.example.com", port=443, ws_path="/x",
    )
    body = json.loads(base64.b64decode(uri[len("vmess://"):]))
    assert body["host"] == "vpn.example.com"
    assert body["sni"] == "vpn.example.com"


# ---------------------------------------------------------------------------
# v0.11.0+ — Shadowsocks-2022 (SIP022)
# ---------------------------------------------------------------------------


def test_build_ss2022_uri_pre_concatenates_psks() -> None:
    """Open Question A2 default: server_psk + user_psk pre-concatenated
    so clients get one paste-and-go credential."""
    import base64
    uri = urivider.build_ss2022_uri(
        server_psk="SERVER_PSK_BASE64",
        user_psk="USER_PSK_BASE64",
        host="vpn.example.com",
        port=8388,
        method="2022-blake3-aes-128-gcm",
        remark="stealth-vps-ss2022-alice",
    )
    parsed = urllib.parse.urlparse(uri)
    assert parsed.scheme == "ss"
    assert parsed.hostname == "vpn.example.com"
    assert parsed.port == 8388
    encoded = parsed.netloc.split("@")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    auth = base64.urlsafe_b64decode(padded).decode("utf-8")
    assert auth == "2022-blake3-aes-128-gcm:SERVER_PSK_BASE64:USER_PSK_BASE64"
    assert urllib.parse.unquote(parsed.fragment) == "stealth-vps-ss2022-alice"


def test_build_ss2022_uri_without_server_psk_uses_user_only() -> None:
    """Single-user SS-2022 (no server PSK) emits `method:user-psk` only."""
    import base64
    uri = urivider.build_ss2022_uri(
        server_psk="", user_psk="USER_PSK",
        host="vpn.example.com", port=8388,
    )
    encoded = urllib.parse.urlparse(uri).netloc.split("@")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    auth = base64.urlsafe_b64decode(padded).decode("utf-8")
    assert auth == "2022-blake3-aes-128-gcm:USER_PSK"


def test_build_ss2022_uri_custom_method() -> None:
    import base64
    uri = urivider.build_ss2022_uri(
        server_psk="S", user_psk="U",
        host="h", port=1, method="2022-blake3-aes-256-gcm",
    )
    encoded = urllib.parse.urlparse(uri).netloc.split("@")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    auth = base64.urlsafe_b64decode(padded).decode("utf-8")
    assert auth.startswith("2022-blake3-aes-256-gcm:")


# ---------------------------------------------------------------------------
# v0.11.0+ — Trojan(-Go)
# ---------------------------------------------------------------------------


def test_build_trojan_uri_basic_shape() -> None:
    uri = urivider.build_trojan_uri(
        password="my-trojan-pw",
        host="vpn.example.com",
        port=443,
        sni="vpn.example.com",
        remark="stealth-vps-trojan-alice",
    )
    parsed = urllib.parse.urlparse(uri)
    assert parsed.scheme == "trojan"
    assert parsed.hostname == "vpn.example.com"
    assert parsed.port == 443
    assert parsed.username == "my-trojan-pw"
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q["sni"] == "vpn.example.com"
    assert q["fp"] == "chrome"
    assert "allowInsecure" not in q
    assert urllib.parse.unquote(parsed.fragment) == "stealth-vps-trojan-alice"


def test_build_trojan_uri_quotes_special_chars_in_password() -> None:
    """`:`, `@`, `&` in operator-supplied passwords must be URL-encoded
    so they don't get parsed as URI delimiters. urllib.parse.urlparse
    returns the raw (encoded) userinfo; clients decode it on import.
    Round-tripping through unquote should yield the original."""
    uri = urivider.build_trojan_uri(
        password="pass@with:colons&amps",
        host="h", port=443,
    )
    parsed = urllib.parse.urlparse(uri)
    # parsed.username is URL-encoded; decode it to get the original.
    assert urllib.parse.unquote(parsed.username) == "pass@with:colons&amps"
    # The `@`/`:`/`&` in the raw form ARE percent-encoded so the URI
    # parses cleanly — defending the property the test exists for.
    assert "%40" in parsed.username
    assert "%3A" in parsed.username
    assert "%26" in parsed.username


def test_build_trojan_uri_insecure_flag() -> None:
    uri = urivider.build_trojan_uri(
        password="p", host="h", port=443, allow_insecure=True,
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["allowInsecure"] == "1"


def test_build_trojan_uri_sni_falls_back_to_host() -> None:
    uri = urivider.build_trojan_uri(
        password="p", host="vpn.example.com", port=443,
    )
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(uri).query))
    assert q["sni"] == "vpn.example.com"
