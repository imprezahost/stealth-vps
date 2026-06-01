"""Health-check Prometheus exporter (v0.9.0+).

A tiny HTTP server that probes the running stealth-vps components and
exposes the result as Prometheus metrics on :9102/metrics. Operators
who run Prometheus elsewhere (or use Uptime Kuma, Healthchecks.io, etc)
scrape this endpoint to alert on:

  - Xray / Hysteria2 / x-ui / Caddy / bot systemd-unit health
  - Reality TCP port reachability (from the box's own loopback view)
  - users.index.json schema integrity + user count
  - sub_expires_at counts (how many expire in 7 days?)

Why an HTTP exporter vs the existing textfile-collector flow:
The existing `stealth-vps-metrics-update.py` writes a .prom under
/var/lib/stealth-vps/metrics/ for node_exporter's textfile collector
to pick up. That works great when you already run node_exporter — but
operators who don't (e.g. they push to a SaaS) need a self-contained
endpoint. This file is that endpoint: pure stdlib, no node_exporter
dep, default-binds loopback so a forgotten firewall rule doesn't leak
anything.

Stdlib only (http.server, socket). Same model as the auto-update +
backup modules — no new system-level deps."""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import state

log = logging.getLogger("stealth_vps.health_exporter")

# Bind defaults — loopback by default so a forgotten firewall rule never
# leaks our metrics endpoint. Operators wanting external scraping go
# through Caddy reverse-proxy + auth (see docs/operations.md).
DEFAULT_BIND_ADDR = "127.0.0.1"
DEFAULT_BIND_PORT = 9102

# Units we probe. Each entry: (systemd name, optional reality_state.yml
# key holding the listen port for liveness check).
#
# v0.11.0+: trojan-go + wg-quick@stealth are probed too. They report 0
# (inactive/missing) on hosts that haven't enabled those protocols —
# same self-describing behaviour as x-ui on a headless box. No harm in
# probing a unit that doesn't exist.
_PROBE_UNITS = (
    ("xray.service", "port"),
    ("hysteria-server.service", None),       # UDP — separate probe path
    ("x-ui.service", None),                  # Panel mode only
    ("caddy.service", None),                 # Subscription endpoint
    ("stealth-vps-bot.service", None),       # Bot
    ("trojan-go.service", None),             # v0.11.0+ Trojan-Go
    ("wg-quick@stealth.service", None),      # v0.11.0+ WireGuard
)

# Path defaults — same constants the rest of the package pins.
_DEFAULT_REALITY_STATE_PATH = "/etc/stealth-vps/reality.state.yml"

# v0.11.0+ — per-protocol state files. Each carries a `port: NNNNN`
# line the generic port reader extracts. When the file is absent (the
# protocol isn't enabled on this host) the gauge is emitted with -1
# ("no signal") rather than dropped, so dashboards can tell "disabled"
# from "down". Tag → state file path.
_PROTOCOL_STATE_PATHS = {
    "ss2022": "/etc/stealth-vps/ss2022.state.yml",
    "xhttp": "/etc/stealth-vps/xhttp.state.yml",
    "vmess_ws": "/etc/stealth-vps/vmess_ws.state.yml",
    # Trojan-Go is TCP — probeable. WireGuard is UDP (no TCP-connect
    # probe), so it gets no port gauge; the wg-quick@stealth unit-active
    # gauge covers "is it up".
    "trojan_go": "/etc/stealth-vps/trojan_go.state.yml",
}


# ---------------------------------------------------------------------------
# Probes — pure functions, no HTTP
# ---------------------------------------------------------------------------


def probe_systemd_unit(unit: str) -> int:
    """Return 1 if `systemctl is-active <unit>` says `active`, 0 otherwise.
    Missing units (not installed) return 0 too — operators monitoring a
    headless deployment shouldn't see panel-mode unit alerts firing.

    The exporter doesn't distinguish "missing" from "failed"; metric
    consumers care about "is this thing serving traffic right now?" and
    both states answer "no"."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    return 1 if result.stdout.strip() == "active" else 0


def probe_tcp_port(host: str, port: int, *, timeout: float = 2.0) -> int:
    """Return 1 if a TCP connect to (host, port) succeeds, 0 otherwise.
    Used for Reality's listening port — the unit being `active` doesn't
    prove Xray actually managed to bind."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 1
    except (OSError, socket.timeout):
        return 0


def _read_state_port(path: str) -> int | None:
    """Extract the `port: NNNNN` line from any stealth-vps state file.
    Returns None when the file is absent (protocol not enabled, panel
    mode, or pre-converge box). We don't pull in a YAML parser — the
    field is one line and a regex is enough."""
    import re
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    m = re.search(r"^\s*port\s*:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    if not m:
        return None
    return int(m.group(1))


def _read_reality_port(path: str = _DEFAULT_REALITY_STATE_PATH) -> int | None:
    """Reality-specific wrapper around `_read_state_port`. Kept as a
    named function because the render path + tests reference it
    directly."""
    return _read_state_port(path)


def count_users(path: str | None = None) -> tuple[int, int, int]:
    """Return (total, enabled, expired). `total` counts every row in
    the index; `enabled` excludes those with enabled=false; `expired`
    counts rows whose sub_expires_at is in the past.

    Returns (0, 0, 0) when the index is missing or unparseable rather
    than failing the scrape — Prometheus would alert via the
    `stealth_vps_index_readable` gauge below."""
    p = path or state.USERS_INDEX_PATH
    try:
        data = state.load_users_index(p)
    except state.StateError:
        return (0, 0, 0)
    rows = data.get("users", {})
    total = len(rows)
    enabled = sum(1 for r in rows.values() if r.get("enabled", True))
    expired = 0
    for r in rows.values():
        try:
            if state.is_expired(r):
                expired += 1
        except state.StateError:
            # garbled timestamp — skip, the operator will see it via
            # the schema metric below
            continue
    return (total, enabled, expired)


def is_index_readable(path: str | None = None) -> int:
    """1 if `state.load_users_index` succeeds, 0 otherwise. A
    yes-or-no health signal independent of how many users are inside.
    """
    p = path or state.USERS_INDEX_PATH
    try:
        state.load_users_index(p)
        return 1
    except state.StateError:
        return 0


# ---------------------------------------------------------------------------
# Metric rendering — Prometheus text exposition format
# ---------------------------------------------------------------------------


def render_metrics(*, reality_state_path: str = _DEFAULT_REALITY_STATE_PATH,
                   users_index_path: str | None = None,
                   protocol_state_paths: dict[str, str] | None = None) -> str:
    """Snapshot every probe and return the Prometheus text body.

    Why we re-probe on every scrape rather than caching: scrapes are
    typically once per 30s, and each probe is a sub-millisecond
    syscall (systemctl is-active reads a tiny file; the TCP port check
    times out at 2s but we only call it for the Reality port). Caching
    would add staleness without a meaningful CPU win.

    `protocol_state_paths` (v0.11.0+) maps a protocol tag (ss2022 /
    xhttp / vmess_ws) to its state-file path. Defaults to the role's
    canonical paths; tests pass tmp paths. Each emits a
    `stealth_vps_<tag>_port_listening` gauge (-1 when the protocol
    isn't enabled, 0 when enabled-but-down, 1 when listening)."""
    if protocol_state_paths is None:
        protocol_state_paths = _PROTOCOL_STATE_PATHS
    lines: list[str] = []

    # --- systemd unit health ----------------------------------------------
    lines.append("# HELP stealth_vps_unit_active 1 when `systemctl is-active <unit>` returns `active`.")
    lines.append("# TYPE stealth_vps_unit_active gauge")
    for unit, _ in _PROBE_UNITS:
        v = probe_systemd_unit(unit)
        lines.append(f'stealth_vps_unit_active{{unit="{unit}"}} {v}')

    # --- Reality TCP port ----------------------------------------------
    reality_port = _read_reality_port(reality_state_path)
    lines.append("# HELP stealth_vps_reality_port_listening 1 when TCP connect to Reality port succeeds.")
    lines.append("# TYPE stealth_vps_reality_port_listening gauge")
    if reality_port is not None:
        v = probe_tcp_port("127.0.0.1", reality_port)
        lines.append(
            f'stealth_vps_reality_port_listening{{port="{reality_port}"}} {v}'
        )
    else:
        # Emit the metric anyway with -1 so dashboards see "no signal"
        # vs "down". Prometheus tolerates negative values for gauges.
        lines.append('stealth_vps_reality_port_listening{port=""} -1')

    # --- v0.11.0+ per-protocol TCP ports -------------------------------
    # One gauge per protocol tag. -1 = state file absent (protocol not
    # enabled on this host); 0 = enabled but the port isn't accepting
    # connections; 1 = listening. XHTTP + VMess+WS bind loopback (Caddy
    # fronts), SS-2022 binds 0.0.0.0 — all reachable via 127.0.0.1 for
    # the local probe.
    for tag in sorted(protocol_state_paths):
        state_path = protocol_state_paths[tag]
        metric = f"stealth_vps_{tag}_port_listening"
        lines.append(
            f"# HELP {metric} 1 when TCP connect to the {tag} port succeeds "
            f"(-1 = protocol not enabled)."
        )
        lines.append(f"# TYPE {metric} gauge")
        port = _read_state_port(state_path)
        if port is not None:
            v = probe_tcp_port("127.0.0.1", port)
            lines.append(f'{metric}{{port="{port}"}} {v}')
        else:
            lines.append(f'{metric}{{port=""}} -1')

    # --- users index health + counts ----------------------------------
    readable = is_index_readable(users_index_path)
    lines.append("# HELP stealth_vps_index_readable 1 when users.index.json parses cleanly.")
    lines.append("# TYPE stealth_vps_index_readable gauge")
    lines.append(f"stealth_vps_index_readable {readable}")

    total, enabled, expired = count_users(users_index_path)
    lines.append("# HELP stealth_vps_users_total Total user rows in the index (including revoked).")
    lines.append("# TYPE stealth_vps_users_total gauge")
    lines.append(f"stealth_vps_users_total {total}")
    lines.append("# HELP stealth_vps_users_enabled Active (enabled=true) user rows.")
    lines.append("# TYPE stealth_vps_users_enabled gauge")
    lines.append(f"stealth_vps_users_enabled {enabled}")
    lines.append("# HELP stealth_vps_users_expired Users whose sub_expires_at is in the past.")
    lines.append("# TYPE stealth_vps_users_expired gauge")
    lines.append(f"stealth_vps_users_expired {expired}")

    # Add a trailing newline (Prometheus parsers tolerate either, but
    # the convention is to terminate with one).
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serves /metrics and /healthz. Everything else 404s.

    /metrics → Prometheus text body
    /healthz → `ok` + 200, used by k8s-style liveness probes / curl
               smoke tests. Doesn't probe components — it's a "is the
               server itself up" check.
    """

    # Subclass attributes the constructor injects (HTTPServer pattern):
    # we attach the paths to the server instance, then read them off
    # `self.server` here. Avoids closing over module-level globals so
    # tests can spin up multiple instances with different paths.
    server_version = "stealth-vps-health-exporter/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — http.server API
        # Quiet by default — every scrape would otherwise spam syslog.
        # Operators wanting access logs can set --log-requests.
        if getattr(self.server, "log_requests", False):
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if self.path == "/metrics":
            try:
                body = render_metrics(
                    reality_state_path=self.server.reality_state_path,
                    users_index_path=self.server.users_index_path,
                    protocol_state_paths=getattr(
                        self.server, "protocol_state_paths", None
                    ),
                ).encode("utf-8")
            except Exception as exc:   # noqa: BLE001 — last-resort barrier
                log.exception("render_metrics failed")
                self.send_error(500, f"metrics render failed: {exc}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404, "use /metrics or /healthz")


def make_server(
    bind_addr: str = DEFAULT_BIND_ADDR,
    bind_port: int = DEFAULT_BIND_PORT,
    *,
    reality_state_path: str = _DEFAULT_REALITY_STATE_PATH,
    users_index_path: str | None = None,
    protocol_state_paths: dict[str, str] | None = None,
    log_requests: bool = False,
) -> HTTPServer:
    """Return a configured HTTPServer. The caller decides whether to
    `serve_forever()` (the systemd flow) or spin it on a thread (tests).

    We attach the path overrides to the server instance because
    BaseHTTPRequestHandler's API gives handlers a self.server attribute;
    that's the cleanest stdlib-only injection point.

    `protocol_state_paths` (v0.11.0+) is None in the systemd flow → the
    handler passes None → render_metrics uses the canonical paths.
    Tests inject tmp paths."""
    server = HTTPServer((bind_addr, bind_port), _MetricsHandler)
    server.reality_state_path = reality_state_path        # type: ignore[attr-defined]
    server.users_index_path = users_index_path            # type: ignore[attr-defined]
    server.protocol_state_paths = protocol_state_paths    # type: ignore[attr-defined]
    server.log_requests = log_requests                    # type: ignore[attr-defined]
    return server


# ---------------------------------------------------------------------------
# Main — `python3 -m stealth_vps.health_exporter`
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealth-vps-health-exporter",
        description="Prometheus metrics endpoint for stealth-vps component health.",
    )
    parser.add_argument(
        "--bind-addr", default=DEFAULT_BIND_ADDR,
        help="bind address (default %(default)s — loopback only).",
    )
    parser.add_argument(
        "--bind-port", default=DEFAULT_BIND_PORT, type=int,
        help="bind port (default %(default)s).",
    )
    parser.add_argument(
        "--reality-state-path", default=_DEFAULT_REALITY_STATE_PATH,
        help="path to reality.state.yml (test override).",
    )
    parser.add_argument(
        "--users-index-path", default=None,
        help="path to users.index.json (defaults to stealth_vps.state.USERS_INDEX_PATH).",
    )
    parser.add_argument(
        "--log-requests", action="store_true",
        help="log each HTTP request (off by default to avoid scrape spam).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = make_server(
        bind_addr=args.bind_addr,
        bind_port=args.bind_port,
        reality_state_path=args.reality_state_path,
        users_index_path=args.users_index_path,
        log_requests=args.log_requests,
    )
    log.info(
        "serving /metrics + /healthz on http://%s:%d",
        args.bind_addr, args.bind_port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutdown via SIGINT")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
