"""Tests for stealth_vps.wireguard — keygen (mocked), IP allocation,
and server/client config rendering.

`generate_keypair` shells out to `wg`, which isn't on the CI runner.
Those tests patch subprocess.run. The IP-allocation + config-render
functions are pure and tested directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import wireguard


# ---------------------------------------------------------------------------
# generate_keypair
# ---------------------------------------------------------------------------


def test_generate_keypair_shells_out_to_wg() -> None:
    """genkey then pubkey; returns (priv, pub)."""
    def fake_run(cmd, **kwargs):
        if cmd[-1] == "genkey":
            return MagicMock(stdout="PRIVKEY_B64\n", returncode=0)
        if cmd[-1] == "pubkey":
            # pubkey reads the privkey on stdin.
            assert kwargs.get("input") == "PRIVKEY_B64"
            return MagicMock(stdout="PUBKEY_B64\n", returncode=0)
        raise AssertionError(f"unexpected cmd {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        priv, pub = wireguard.generate_keypair()
    assert priv == "PRIVKEY_B64"
    assert pub == "PUBKEY_B64"


def test_generate_keypair_missing_wg_binary_raises() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(wireguard.WireGuardError, match="not found"):
            wireguard.generate_keypair()


def test_generate_keypair_empty_key_raises() -> None:
    with patch("subprocess.run", return_value=MagicMock(stdout="\n", returncode=0)):
        with pytest.raises(wireguard.WireGuardError, match="empty key"):
            wireguard.generate_keypair()


# ---------------------------------------------------------------------------
# server_ip + allocate_client_ip
# ---------------------------------------------------------------------------


def test_server_ip_is_first_host() -> None:
    assert wireguard.server_ip("10.99.0.0/24") == "10.99.0.1"
    assert wireguard.server_ip("10.0.0.0/24") == "10.0.0.1"


def test_allocate_client_ip_starts_at_dot_2() -> None:
    """Server holds .1; first client gets .2."""
    ip = wireguard.allocate_client_ip("10.99.0.0/24", used_ips=[])
    assert ip == "10.99.0.2"


def test_allocate_client_ip_skips_used() -> None:
    ip = wireguard.allocate_client_ip(
        "10.99.0.0/24",
        used_ips=["10.99.0.2", "10.99.0.3"],
    )
    assert ip == "10.99.0.4"


def test_allocate_client_ip_accepts_cidr_in_used() -> None:
    """`used_ips` may carry /32 CIDR (as stored in the index)."""
    ip = wireguard.allocate_client_ip(
        "10.99.0.0/24",
        used_ips=["10.99.0.2/32", "10.99.0.3/32"],
    )
    assert ip == "10.99.0.4"


def test_allocate_client_ip_never_returns_server_ip() -> None:
    """Even if .1 isn't in used_ips, it must not be handed out."""
    ip = wireguard.allocate_client_ip("10.99.0.0/24", used_ips=[])
    assert ip != "10.99.0.1"


def test_allocate_client_ip_fills_gaps() -> None:
    """Sequential = lowest free, so a freed middle IP gets reused."""
    ip = wireguard.allocate_client_ip(
        "10.99.0.0/24",
        used_ips=["10.99.0.2", "10.99.0.4"],   # .3 is free
    )
    assert ip == "10.99.0.3"


def test_allocate_client_ip_exhausted_raises() -> None:
    """A /30 has 2 usable hosts: .1 (server) + .2 (one client). Asking
    for a second client exhausts it."""
    with pytest.raises(wireguard.WireGuardError, match="exhausted"):
        wireguard.allocate_client_ip(
            "10.99.0.0/30",
            used_ips=["10.99.0.2"],
        )


def test_allocate_client_ip_invalid_subnet_raises() -> None:
    with pytest.raises(wireguard.WireGuardError, match="invalid"):
        wireguard.allocate_client_ip("not-a-subnet", used_ips=[])


def test_parse_subnet_rejects_ipv6() -> None:
    with pytest.raises(wireguard.WireGuardError, match="IPv4"):
        wireguard.allocate_client_ip("fd00::/64", used_ips=[])


# ---------------------------------------------------------------------------
# render_server_conf
# ---------------------------------------------------------------------------


def test_render_server_conf_interface_and_peers() -> None:
    conf = wireguard.render_server_conf(
        server_private_key="SERVER_PRIV",
        listen_port=51820,
        subnet="10.99.0.0/24",
        peers=[
            ("alice", "ALICE_PUB", "10.99.0.2"),
            ("bob", "BOB_PUB", "10.99.0.3/32"),
        ],
    )
    assert "[Interface]" in conf
    assert "Address = 10.99.0.1/24" in conf
    assert "ListenPort = 51820" in conf
    assert "PrivateKey = SERVER_PRIV" in conf
    # Both peers present with /32 AllowedIPs.
    assert "# alice" in conf
    assert "PublicKey = ALICE_PUB" in conf
    assert "AllowedIPs = 10.99.0.2/32" in conf
    assert "# bob" in conf
    assert "AllowedIPs = 10.99.0.3/32" in conf   # CIDR input normalised


def test_render_server_conf_peers_sorted_by_label() -> None:
    """Deterministic output — peers sorted so repeated renders with the
    same input are byte-identical (matters for `wg syncconf` no-op +
    git review)."""
    conf = wireguard.render_server_conf(
        server_private_key="P",
        listen_port=51820,
        subnet="10.99.0.0/24",
        peers=[
            ("zara", "ZP", "10.99.0.9"),
            ("alice", "AP", "10.99.0.2"),
            ("mike", "MP", "10.99.0.5"),
        ],
    )
    # alice's comment appears before mike's before zara's.
    assert conf.index("# alice") < conf.index("# mike") < conf.index("# zara")


def test_render_server_conf_deterministic() -> None:
    kw = dict(
        server_private_key="P", listen_port=51820, subnet="10.99.0.0/24",
        peers=[("alice", "AP", "10.99.0.2"), ("bob", "BP", "10.99.0.3")],
    )
    assert wireguard.render_server_conf(**kw) == wireguard.render_server_conf(**kw)


def test_render_server_conf_empty_peers() -> None:
    """No users yet — just the Interface block, no peers. wg accepts a
    peer-less interface (it just won't route anyone)."""
    conf = wireguard.render_server_conf(
        server_private_key="P", listen_port=51820, subnet="10.99.0.0/24",
        peers=[],
    )
    assert "[Interface]" in conf
    assert "[Peer]" not in conf


# ---------------------------------------------------------------------------
# render_client_conf
# ---------------------------------------------------------------------------


def test_render_client_conf_full_tunnel_default() -> None:
    conf = wireguard.render_client_conf(
        client_private_key="CLIENT_PRIV",
        client_ip="10.99.0.5",
        server_public_key="SERVER_PUB",
        endpoint_host="vpn.example.com",
        endpoint_port=51820,
    )
    assert "[Interface]" in conf
    assert "PrivateKey = CLIENT_PRIV" in conf
    assert "Address = 10.99.0.5/32" in conf
    assert "DNS = 1.1.1.1" in conf
    assert "[Peer]" in conf
    assert "PublicKey = SERVER_PUB" in conf
    assert "Endpoint = vpn.example.com:51820" in conf
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in conf      # full tunnel
    assert "PersistentKeepalive = 25" in conf


def test_render_client_conf_normalises_cidr_address() -> None:
    conf = wireguard.render_client_conf(
        client_private_key="P",
        client_ip="10.99.0.5/32",   # already CIDR
        server_public_key="SP",
        endpoint_host="h",
        endpoint_port=51820,
    )
    assert "Address = 10.99.0.5/32" in conf
    # No double-suffix.
    assert "10.99.0.5/32/32" not in conf


def test_render_client_conf_split_tunnel_override() -> None:
    conf = wireguard.render_client_conf(
        client_private_key="P",
        client_ip="10.99.0.5",
        server_public_key="SP",
        endpoint_host="h",
        endpoint_port=51820,
        allowed_ips="10.0.0.0/8",
        dns="9.9.9.9",
        keepalive=15,
    )
    assert "AllowedIPs = 10.0.0.0/8" in conf
    assert "DNS = 9.9.9.9" in conf
    assert "PersistentKeepalive = 15" in conf


def test_render_client_conf_round_trips_with_server_conf() -> None:
    """A keypair generated for the client should appear (pubkey) in the
    server conf's peer list AND (privkey) in the client conf — the two
    halves of one user's WG identity."""
    # Simulate server-gen: one server keypair + one client keypair.
    with patch("subprocess.run") as run:
        run.side_effect = [
            MagicMock(stdout="SRV_PRIV\n", returncode=0),
            MagicMock(stdout="SRV_PUB\n", returncode=0),
            MagicMock(stdout="CLI_PRIV\n", returncode=0),
            MagicMock(stdout="CLI_PUB\n", returncode=0),
        ]
        srv_priv, srv_pub = wireguard.generate_keypair()
        cli_priv, cli_pub = wireguard.generate_keypair()

    client_ip = wireguard.allocate_client_ip("10.99.0.0/24", used_ips=[])
    server_conf = wireguard.render_server_conf(
        server_private_key=srv_priv, listen_port=51820,
        subnet="10.99.0.0/24",
        peers=[("alice", cli_pub, client_ip)],
    )
    client_conf = wireguard.render_client_conf(
        client_private_key=cli_priv, client_ip=client_ip,
        server_public_key=srv_pub, endpoint_host="vpn.example.com",
        endpoint_port=51820,
    )
    # Server knows the client's pubkey; client holds the privkey.
    assert "CLI_PUB" in server_conf
    assert "CLI_PRIV" in client_conf
    # Both agree on the client IP.
    assert "10.99.0.2/32" in server_conf
    assert "Address = 10.99.0.2/32" in client_conf
    # Client points at the server's pubkey.
    assert "PublicKey = SRV_PUB" in client_conf
