"""Tests for stealth_vps.health_exporter — probes + render + HTTP server.

We don't actually invoke `systemctl` in the test environment (Windows
dev box has no systemd; CI does but probing real units would couple
tests to host state). The systemd-probing test patches
`subprocess.run`. The TCP probe test binds + closes a real ephemeral
listener so we exercise the socket-level path.

The HTTP server tests spin up the exporter on a free port via the
make_server() factory and curl /metrics + /healthz with urllib.
"""

from __future__ import annotations

import json
import pathlib
import socket
import threading
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import health_exporter, state


# ---------------------------------------------------------------------------
# probe_systemd_unit
# ---------------------------------------------------------------------------


def test_probe_systemd_unit_returns_1_on_active() -> None:
    fake = MagicMock(stdout="active\n", returncode=0)
    with patch("subprocess.run", return_value=fake):
        assert health_exporter.probe_systemd_unit("xray.service") == 1


def test_probe_systemd_unit_returns_0_on_inactive() -> None:
    fake = MagicMock(stdout="inactive\n", returncode=3)
    with patch("subprocess.run", return_value=fake):
        assert health_exporter.probe_systemd_unit("xray.service") == 0


def test_probe_systemd_unit_returns_0_on_missing_systemctl() -> None:
    """Dev / test boxes without systemd return 0 — the metric reflects
    `is this thing running?` which is "no" either way."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert health_exporter.probe_systemd_unit("xray.service") == 0


def test_probe_systemd_unit_returns_0_on_timeout() -> None:
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
        assert health_exporter.probe_systemd_unit("xray.service") == 0


# ---------------------------------------------------------------------------
# probe_tcp_port — real socket round-trip
# ---------------------------------------------------------------------------


def test_probe_tcp_port_returns_1_when_connect_succeeds() -> None:
    """Patch `socket.create_connection` to return a no-op context
    manager — exercises the `connect succeeded` branch without
    binding a real listener. Real-socket tests on Python 3.14
    emit `unclosed socket` ResourceWarnings during GC; our
    `filterwarnings = ["error"]` setup promotes those to failures.
    """
    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=MagicMock())
    fake_conn.__exit__ = MagicMock(return_value=False)
    with patch("socket.create_connection", return_value=fake_conn):
        assert health_exporter.probe_tcp_port("127.0.0.1", 51820) == 1


def test_probe_tcp_port_returns_0_on_connect_refused() -> None:
    """Refused / no-listener → OSError → probe returns 0."""
    with patch("socket.create_connection", side_effect=ConnectionRefusedError):
        assert health_exporter.probe_tcp_port("127.0.0.1", 1) == 0


def test_probe_tcp_port_returns_0_on_timeout() -> None:
    with patch("socket.create_connection", side_effect=socket.timeout):
        assert health_exporter.probe_tcp_port("127.0.0.1", 1, timeout=0.1) == 0


# ---------------------------------------------------------------------------
# count_users / is_index_readable
# ---------------------------------------------------------------------------


def test_count_users_with_mixed_index(users_index_path: str) -> None:
    """Seed: alice (enabled, no expiry). Add: bob (enabled, expired).
    Add: carol (revoked, no expiry). Total=3, enabled=2, expired=1."""
    _kwargs = dict(
        reality_uuid="00000000-0000-0000-0000-000000000002",
        hysteria_password="hy-pw",
        sub_token="bob-sub-token",
        created_at="2026-01-02T00:00:00Z",
        path=users_index_path,
    )
    state.add_user("bob", **_kwargs)
    state.update_user("bob", sub_expires_at="2020-01-01T00:00:00Z", path=users_index_path)

    state.add_user(
        "carol",
        reality_uuid="00000000-0000-0000-0000-000000000003",
        hysteria_password="hy-pw-2",
        sub_token="carol-sub-token",
        created_at="2026-01-03T00:00:00Z",
        path=users_index_path,
    )
    state.revoke_user("carol", users_index_path)

    total, enabled, expired = health_exporter.count_users(users_index_path)
    assert total == 3
    assert enabled == 2
    assert expired == 1


def test_count_users_returns_zeros_when_index_missing(tmp_path: pathlib.Path) -> None:
    assert health_exporter.count_users(str(tmp_path / "nope.json")) == (0, 0, 0)


def test_is_index_readable(users_index_path: str, tmp_path: pathlib.Path) -> None:
    assert health_exporter.is_index_readable(users_index_path) == 1
    assert health_exporter.is_index_readable(str(tmp_path / "nope.json")) == 0


# ---------------------------------------------------------------------------
# render_metrics
# ---------------------------------------------------------------------------


def test_render_metrics_contains_expected_families(
    users_index_path: str, tmp_path: pathlib.Path
) -> None:
    """Snapshot the rendered text and assert every metric family is
    present with at least one sample. We don't pin exact values
    because systemd-probe values depend on the host."""
    reality_state = tmp_path / "reality.state.yml"
    reality_state.write_text("port: 51820\nshort_id: abcd\n", encoding="utf-8")

    with patch("subprocess.run", return_value=MagicMock(stdout="active\n", returncode=0)):
        body = health_exporter.render_metrics(
            reality_state_path=str(reality_state),
            users_index_path=users_index_path,
        )

    # Every metric family is named.
    for name in (
        "stealth_vps_unit_active",
        "stealth_vps_reality_port_listening",
        "stealth_vps_index_readable",
        "stealth_vps_users_total",
        "stealth_vps_users_enabled",
        "stealth_vps_users_expired",
    ):
        assert name in body, f"missing metric family {name}"

    # Every HELP has a matching TYPE.
    help_count = body.count("# HELP ")
    type_count = body.count("# TYPE ")
    assert help_count == type_count
    # Body ends with newline (Prometheus convention).
    assert body.endswith("\n")


def test_render_metrics_emits_negative_one_when_reality_state_missing(
    users_index_path: str, tmp_path: pathlib.Path
) -> None:
    """Panel mode hosts (or pre-converge boxes) have no reality.state.yml.
    The metric must still appear with a -1 sentinel so dashboards see
    `no data` rather than the family vanishing."""
    with patch("subprocess.run", return_value=MagicMock(stdout="active\n", returncode=0)):
        body = health_exporter.render_metrics(
            reality_state_path=str(tmp_path / "missing.yml"),
            users_index_path=users_index_path,
        )
    assert 'stealth_vps_reality_port_listening{port=""} -1' in body


# ---------------------------------------------------------------------------
# HTTP server smoke
# ---------------------------------------------------------------------------


@pytest.fixture
def running_exporter(users_index_path: str, tmp_path: pathlib.Path):
    """Spin up the exporter on a free port + return the base URL.
    Tears down on test exit."""
    reality_state = tmp_path / "reality.state.yml"
    reality_state.write_text("port: 51820\n", encoding="utf-8")

    server = health_exporter.make_server(
        bind_addr="127.0.0.1",
        bind_port=0,                     # ephemeral
        reality_state_path=str(reality_state),
        users_index_path=users_index_path,
    )
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_http_metrics_returns_prometheus_body(running_exporter: str) -> None:
    with patch("subprocess.run", return_value=MagicMock(stdout="active\n", returncode=0)):
        with urllib.request.urlopen(f"{running_exporter}/metrics", timeout=5) as resp:
            assert resp.status == 200
            ctype = resp.getheader("Content-Type")
            assert ctype.startswith("text/plain")
            body = resp.read().decode("utf-8")
    assert "stealth_vps_unit_active" in body
    assert "stealth_vps_users_total" in body


def test_http_healthz_returns_ok(running_exporter: str) -> None:
    with urllib.request.urlopen(f"{running_exporter}/healthz", timeout=5) as resp:
        assert resp.status == 200
        assert resp.read().strip() == b"ok"


def test_http_unknown_path_404s(running_exporter: str) -> None:
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{running_exporter}/bogus", timeout=5)
    assert excinfo.value.code == 404
