"""URI builders for vless:// and hysteria2:// (v0.7+), plus v0.11.0+
additions: xhttp:// (vless variant), vmess://, and ss:// (Shadowsocks-2022).

Extracted from the credentials.txt template so the bot, the
subscription endpoint, and any future code that hands URIs to
clients all use the same builder. Tests can assert byte-equivalence
between this output and the Jinja2-rendered template.

Trojan and WireGuard live elsewhere — Trojan in this module (v0.11.0+
adds `build_trojan_uri`), WireGuard in `wireguard.py` because WG's
"URI" is actually a multi-line `.conf` snippet, not a single line.
"""

from __future__ import annotations

import base64
import json as _json
import urllib.parse


def build_vless_uri(
    *,
    uuid: str,
    host: str,
    port: int,
    sni: str,
    public_key: str,
    short_id: str,
    fingerprint: str = "chrome",
    flow: str = "xtls-rprx-vision",
    remark: str = "stealth-vps-reality",
) -> str:
    """Render the VLESS-Reality URI clients import. Same shape as
    `templates/stealth-vps-credentials.txt.j2` produces, but
    parameterised so the bot can render per-user URIs after `/user add`.
    """
    params = {
        "type": "tcp",
        "security": "reality",
        "sni": sni,
        "fp": fingerprint,
        "pbk": public_key,
        "sid": short_id,
        "flow": flow,
    }
    query = urllib.parse.urlencode(params)
    fragment = urllib.parse.quote(remark)
    return f"vless://{uuid}@{host}:{port}?{query}#{fragment}"


def build_hysteria2_uri(
    *,
    password: str,
    host: str,
    port: int,
    sni: str,
    obfs_type: str = "salamander",
    obfs_password: str = "",
    port_hop_range: tuple[int, int] | None = None,
    insecure: bool = False,
    remark: str = "stealth-vps-hysteria2",
) -> str:
    """Render the Hysteria2 URI.

    port_hop_range, when set, becomes the `,min-max` suffix on the
    port — clients that understand port hopping (Hiddify, NekoBox,
    sing-box) pick a random port from the range per connection.

    insecure=True appends `&insecure=1`. Used when the role is in
    self-signed mode (no LE domain set); clients then accept the
    self-signed cert. Should be False in production (domain set).
    """
    if port_hop_range is None:
        host_port = f"{host}:{port}"
    else:
        host_port = f"{host}:{port},{port_hop_range[0]}-{port_hop_range[1]}"

    params: dict[str, str] = {"sni": sni}
    if obfs_type:
        params["obfs"] = obfs_type
    if obfs_password:
        params["obfs-password"] = obfs_password
    if insecure:
        params["insecure"] = "1"

    query = urllib.parse.urlencode(params)
    fragment = urllib.parse.quote(remark)
    # Hysteria2 URI quotes the password as the userinfo of the URL.
    quoted_password = urllib.parse.quote(password, safe="")
    return f"hysteria2://{quoted_password}@{host_port}/?{query}#{fragment}"


# ---------------------------------------------------------------------------
# v0.11.0+ — Xray-side protocol additions
# ---------------------------------------------------------------------------


def build_xhttp_uri(
    *,
    uuid: str,
    host: str,
    port: int,
    path: str,
    host_header: str = "",
    sni: str = "",
    fingerprint: str = "chrome",
    flow: str = "",
    remark: str = "stealth-vps-xhttp",
) -> str:
    """Render the VLESS-over-XHTTP URI clients import. Same UUID auth
    as Reality (per Open Question A1 default), different transport.

    Designed to live behind Cloudflare: the data node listens on
    loopback `:port`; Caddy reverse-proxies the public CF-fronted
    path to it. The client URI's `host` is the CF-fronted hostname,
    not the VPS IP.

    `path` is the URL path Caddy maps to the loopback Xray (e.g.
    `/.well-known/xhttp-stream`). `host_header` lets clients override
    the Host header sent to the CDN edge (useful when the operator
    fronts via a domain alias).

    `flow` is empty by default — XHTTP doesn't use XTLS Vision flow
    (no inner TLS to wrap). Reality's `xtls-rprx-vision` is for
    direct TCP, not for fronted HTTP.
    """
    params = {
        "type": "xhttp",
        "security": "tls",       # XHTTP requires outer TLS; CF terminates it.
        "sni": sni or host,
        "fp": fingerprint,
        "path": path,
    }
    if host_header:
        params["host"] = host_header
    if flow:
        params["flow"] = flow
    query = urllib.parse.urlencode(params)
    fragment = urllib.parse.quote(remark)
    return f"vless://{uuid}@{host}:{port}?{query}#{fragment}"


def build_vmess_ws_uri(
    *,
    uuid: str,
    host: str,
    port: int,
    ws_path: str,
    host_header: str = "",
    sni: str = "",
    remark: str = "stealth-vps-vmess-ws",
) -> str:
    """Render the VMess-over-WebSocket-over-TLS URI clients import.

    VMess URIs are base64-encoded JSON (NOT base64url — standard
    base64 with padding, which most clients decode). The shape is
    documented in v2rayN's URI scheme.

    `ws_path` is the WebSocket path Caddy maps to the loopback Xray
    (e.g. `/.well-known/vmess-ws`). `host_header` overrides the WS
    Host header — typically the public CF-fronted hostname.

    aid is fixed at 0 (alterId — the modern default; pre-2022 setups
    used 64). add/host/port/path are the standard fields.
    """
    body = {
        "v": "2",
        "ps": remark,
        "add": host,
        "port": str(port),
        "id": uuid,
        "aid": "0",
        "net": "ws",
        "type": "none",
        "host": host_header or host,
        "path": ws_path,
        "tls": "tls",
        "sni": sni or host,
    }
    payload = _json.dumps(body, separators=(",", ":"), sort_keys=False)
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return f"vmess://{encoded}"


def build_ss2022_uri(
    *,
    server_psk: str,
    user_psk: str,
    host: str,
    port: int,
    method: str = "2022-blake3-aes-128-gcm",
    remark: str = "stealth-vps-ss2022",
) -> str:
    """Render the Shadowsocks-2022 (SIP022) URI.

    Pre-concatenates the server PSK + user PSK per Open Question A2
    default — cleaner client UX (one paste, automatic auth) at the
    cost of needing to re-issue all per-user URIs when the server
    PSK rotates. The 2-PSK form is `<server-psk>:<user-psk>` (colon-
    separated, both base64); v2rayN / Shadowrocket / sing-box all
    accept this.

    URI format (SIP002):
        ss://base64url("<method>:<creds>")@<host>:<port>#<remark>

    Where `<creds>` is the pre-concatenated PSK pair. The userinfo
    portion is encoded with base64URL (no padding), to match the
    SIP002 reference.
    """
    creds = f"{server_psk}:{user_psk}" if server_psk else user_psk
    auth = f"{method}:{creds}"
    encoded = base64.urlsafe_b64encode(auth.encode("utf-8")).decode("ascii").rstrip("=")
    fragment = urllib.parse.quote(remark)
    return f"ss://{encoded}@{host}:{port}#{fragment}"


# ---------------------------------------------------------------------------
# v0.11.0+ — Trojan-Go
# ---------------------------------------------------------------------------


def build_trojan_uri(
    *,
    password: str,
    host: str,
    port: int,
    sni: str = "",
    fingerprint: str = "chrome",
    allow_insecure: bool = False,
    remark: str = "stealth-vps-trojan",
) -> str:
    """Render the Trojan(-Go) URI clients import.

    URI format (de-facto standard since Trojan-go's 2019 release):
        trojan://<password>@<host>:<port>?sni=...&fp=...#<remark>

    `password` is URL-quoted (operator-supplied passwords may include
    `:` / `@` / `&`). `allow_insecure=true` appends `&allowInsecure=1`
    — only safe for IP-only deployments (operator hasn't set a domain).
    """
    quoted_pw = urllib.parse.quote(password, safe="")
    params = {
        "sni": sni or host,
        "fp": fingerprint,
    }
    if allow_insecure:
        params["allowInsecure"] = "1"
    query = urllib.parse.urlencode(params)
    fragment = urllib.parse.quote(remark)
    return f"trojan://{quoted_pw}@{host}:{port}?{query}#{fragment}"
