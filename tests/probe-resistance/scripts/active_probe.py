#!/usr/bin/env python3
"""Scenario 03: active probe with no Reality key.

v0.4.0 status: scaffold. Exercises the dest side (so we know the baseline
works from the controller), then exits 2. Contract is locked.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname (required)
    PROBE_REALITY_PORT      dest port (default: 443)
    PROBE_TIMEOUT           per-request timeout in seconds (default: 15)

Exit codes:
    0 — HTTP response shape matches (status + header-set + body-bucket)
    1 — shape mismatch
    2 — inconclusive (target unreachable, scaffold-mode, etc.)
"""

from __future__ import annotations

import bisect
import http.client
import os
import socket
import ssl
import sys
from typing import NamedTuple

BODY_BUCKETS = [1024, 10240, 102400, 1_048_576]  # 1KB, 10KB, 100KB, 1MB

# Headers whose presence is stable but whose values rotate per-request.
# Compared on key, dropped from value comparison.
VARIABLE_HEADERS = frozenset(
    {
        "date",
        "set-cookie",
        "expires",
        "last-modified",
        "etag",
        "x-amz-cf-id",
        "x-amz-cf-pop",
        "cf-ray",
        "cf-cache-status",
        "x-cache",
        "x-served-by",
        "x-timer",
        "x-request-id",
        "x-correlation-id",
    }
)


class ProbeShape(NamedTuple):
    status: int
    headers: frozenset[str]
    body_bucket: int


def fail_inconclusive(reason: str) -> None:
    print(f"WHY: {reason}", file=sys.stderr)
    sys.exit(2)


def resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        fail_inconclusive(f"could not resolve {host}: {exc}")
        raise  # unreachable


def http_probe(host_ip: str, sni: str, port: int, timeout: int) -> ProbeShape:
    """Issue a GET / over TLS, return the (status, header-set, body-bucket).

    `verify=False` because for the VPS probe we won't trust the chain — the
    proxy presents whatever cert chain dest does, but the controller's cert
    store may not match the chain dest uses. We're testing shape, not trust.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    conn = http.client.HTTPSConnection(
        host_ip,
        port=port,
        timeout=timeout,
        context=ctx,
    )
    # server_hostname must be set on the underlying socket for SNI.
    conn.host = sni  # so that the HTTP `Host:` header matches dest
    conn._tunnel_host = None  # avoid CONNECT path
    try:
        # Open the socket manually so we can set SNI properly.
        sock = socket.create_connection((host_ip, port), timeout=timeout)
        wrapped = ctx.wrap_socket(sock, server_hostname=sni)
        conn.sock = wrapped
        conn.request("GET", "/", headers={"Host": sni, "User-Agent": "stealth-probe/0.4"})
        resp = conn.getresponse()
        body = resp.read()
        headers = frozenset(k.lower() for k, _ in resp.getheaders()) - VARIABLE_HEADERS
        bucket = bisect.bisect_right(BODY_BUCKETS, len(body))
        return ProbeShape(status=resp.status, headers=headers, body_bucket=bucket)
    finally:
        conn.close()


def main() -> int:
    target = os.environ.get("PROBE_TARGET")
    dest = os.environ.get("PROBE_REALITY_DEST")
    port = int(os.environ.get("PROBE_REALITY_PORT", "443"))
    timeout = int(os.environ.get("PROBE_TIMEOUT", "15"))

    if not target:
        fail_inconclusive("env PROBE_TARGET is required")
    if not dest:
        fail_inconclusive("env PROBE_REALITY_DEST is required")

    target_ip = resolve(target)
    dest_ip = resolve(dest)

    # Exercise the dest side so we know the baseline shape captures from
    # the controller's network. If this fails the test is inconclusive
    # (probably MITM on the controller's network — see scenario doc).
    try:
        baseline = http_probe(dest_ip, sni=dest, port=port, timeout=timeout)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        fail_inconclusive(f"baseline probe to dest failed: {exc}")
        return 2  # for the type checker

    # Scaffold-mode contract: don't probe the VPS yet — the comparison logic
    # below is implemented but the test treats v0.4.0 as inconclusive until
    # we've validated it against real deployments in v0.5.
    print(
        "SCAFFOLD: active_probe "
        f"baseline=(status={baseline.status} "
        f"headers={len(baseline.headers)} body_bucket={baseline.body_bucket}) "
        f"dest={dest}({dest_ip}) target={target}({target_ip})"
    )
    print(
        "SCAFFOLD: VPS-side probe + comparison ready but unvalidated against "
        "real deploys. Exiting 2 (inconclusive)."
    )

    # The comparison would be (already-written for v0.5):
    #
    #   probe = http_probe(target_ip, sni=dest, port=port, timeout=timeout)
    #   why = []
    #   if probe.status != baseline.status:
    #       why.append(f"status baseline={baseline.status} probe={probe.status}")
    #   if probe.headers != baseline.headers:
    #       extra = sorted(probe.headers - baseline.headers)
    #       missing = sorted(baseline.headers - probe.headers)
    #       if extra:   why.append(f"extra_headers={extra}")
    #       if missing: why.append(f"missing_headers={missing}")
    #   if probe.body_bucket != baseline.body_bucket:
    #       why.append(f"body_bucket baseline={baseline.body_bucket} probe={probe.body_bucket}")
    #   if why:
    #       for line in why: print(f"WHY: {line}")
    #       return 1
    #   return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
