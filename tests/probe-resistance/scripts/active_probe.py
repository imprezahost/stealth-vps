#!/usr/bin/env python3
"""Scenario 03: active probe with no Reality key.

Opens a TLS connection to dest:443 SNI=dest, GETs `/`, captures the
response shape as (status, header_set, body_bucket). Repeats against
the VPS at the same SNI. The two shapes must collide; any divergence
means Reality's reverse-proxy fallback is leaking a tell after the
handshake layer.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname (required)
    PROBE_REALITY_PORT      dest port (default: 443)
    PROBE_TIMEOUT           per-request timeout in seconds (default: 15)
    PROBE_VERBOSE           any value → dump full baseline + probe on diff

Exit codes:
    0 — HTTP response shape matches (status + header-set + body-bucket)
    1 — shape mismatch
    2 — inconclusive (target unreachable, baseline broken, etc.)
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
        "report-to",
        "nel",
        "age",
    }
)


class ProbeShape(NamedTuple):
    status: int
    headers: frozenset[str]
    body_bucket: int
    body_len: int  # kept for verbose output; not part of the equality check


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
    """Issue a GET / over TLS to host_ip with SNI=sni.

    Returns (status, header-set, body-bucket). `verify=False` because for
    the VPS probe we don't trust the chain — Reality presents whatever
    dest does, but the runner's CA store may not match. We're testing
    shape, not trust.

    We open the socket manually so SNI is set explicitly via
    `ctx.wrap_socket(server_hostname=...)`, decouple from the
    HTTPSConnection's own SNI/CONNECT logic that would target host_ip.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(["http/1.1"])  # force h1 so the response is a
    # plain http.client.HTTPResponse — h2 would need hyper/h2 lib.

    sock = socket.create_connection((host_ip, port), timeout=timeout)
    try:
        wrapped = ctx.wrap_socket(sock, server_hostname=sni)
        # Hand the already-wrapped socket to http.client.
        conn = http.client.HTTPSConnection(host_ip, port=port, timeout=timeout)
        conn.sock = wrapped
        try:
            conn.request("GET", "/", headers={
                "Host": sni,
                "User-Agent": "stealth-probe/0.4",
                "Accept": "*/*",
                "Connection": "close",
            })
            resp = conn.getresponse()
            body = resp.read()
            headers_keys = frozenset(k.lower() for k, _ in resp.getheaders())
            stable = headers_keys - VARIABLE_HEADERS
            bucket = bisect.bisect_right(BODY_BUCKETS, len(body))
            return ProbeShape(
                status=resp.status,
                headers=stable,
                body_bucket=bucket,
                body_len=len(body),
            )
        finally:
            conn.close()
    finally:
        # `conn.close()` already closes the underlying socket; this is
        # defensive in case the wrap_socket raised before assignment.
        try:
            sock.close()
        except OSError:
            pass


def diff_shapes(baseline: ProbeShape, probe: ProbeShape) -> list[str]:
    why: list[str] = []
    if baseline.status != probe.status:
        why.append(f"status baseline={baseline.status} probe={probe.status}")
    if baseline.headers != probe.headers:
        extra = sorted(probe.headers - baseline.headers)
        missing = sorted(baseline.headers - probe.headers)
        if extra:
            why.append(f"extra_headers={extra}")
        if missing:
            why.append(f"missing_headers={missing}")
    if baseline.body_bucket != probe.body_bucket:
        why.append(
            f"body_bucket baseline={baseline.body_bucket} "
            f"(len~{baseline.body_len}) probe={probe.body_bucket} "
            f"(len~{probe.body_len})"
        )
    return why


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

    try:
        baseline = http_probe(dest_ip, sni=dest, port=port, timeout=timeout)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        fail_inconclusive(f"baseline probe to dest {dest_ip}:{port} failed: {exc}")
        return 2

    try:
        probe = http_probe(target_ip, sni=dest, port=port, timeout=timeout)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        fail_inconclusive(f"probe to vps {target_ip}:{port} failed: {exc}")
        return 2

    why = diff_shapes(baseline, probe)

    if why:
        print(
            f"FAIL: response shape diverges "
            f"[dest={dest}({dest_ip}) vps={target}({target_ip})]"
        )
        for line in why:
            print(f"WHY: {line}")
        if os.environ.get("PROBE_VERBOSE"):
            print("--- baseline ---")
            print(f"  status={baseline.status} body_len={baseline.body_len}")
            print(f"  headers={sorted(baseline.headers)}")
            print("--- probe ---")
            print(f"  status={probe.status} body_len={probe.body_len}")
            print(f"  headers={sorted(probe.headers)}")
        return 1

    print(
        f"OK [status={probe.status} headers={len(probe.headers)} "
        f"body_bucket={probe.body_bucket}] "
        f"dest={dest}({dest_ip}) vps={target}({target_ip})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
