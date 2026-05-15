"""URI builders for vless:// and hysteria2://.

Extracted from the credentials.txt template so the bot, the
subscription endpoint, and any future code that hands URIs to
clients all use the same builder. Tests can assert byte-equivalence
between this output and the Jinja2-rendered template.
"""

from __future__ import annotations

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
