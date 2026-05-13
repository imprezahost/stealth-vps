#!/usr/bin/env python3
"""Scenario 02: TLS fingerprint comparison (server vs dest).

v0.4.0 status: scaffold. Validates env, resolves targets, prints what it
would compare. Exits 2 (inconclusive). The contract (env vars, exit codes,
output shape) is locked so v0.5 / v1.0 can fill the body without changing
how CI calls it.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname configured on the panel (required)
    PROBE_REALITY_PORT      dest port (default: 443)
    PROBE_TIMEOUT           per-handshake timeout in seconds (default: 15)

Exit codes:
    0 — fingerprints match (JA3 + JA4 collide)
    1 — fingerprints differ (probe shows non-dest handshake)
    2 — inconclusive (target unreachable, scaffold-mode, etc.)
"""

from __future__ import annotations

import os
import socket
import sys


def fail_inconclusive(reason: str) -> None:
    print(f"WHY: {reason}", file=sys.stderr)
    sys.exit(2)


def resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        fail_inconclusive(f"could not resolve {host}: {exc}")
        raise  # unreachable, satisfies type checker


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

    # Validate both endpoints are at least TCP-reachable so a future v0.5
    # implementation knows the scaffold prerequisites were met when it ran.
    for label, host_ip in (("dest", dest_ip), ("target", target_ip)):
        try:
            with socket.create_connection((host_ip, port), timeout=timeout):
                pass
        except OSError as exc:
            fail_inconclusive(f"{label} {host_ip}:{port} unreachable: {exc}")

    # Scaffold-mode contract: print what we would compare, exit 2.
    #
    # v0.5 plan:
    #   1. Capture ClientHello → ServerHello round-trip from `dest_ip` with
    #      SNI=`dest` using openssl s_client -msg or scapy.layers.tls.
    #   2. Capture the same against `target_ip` with SNI=`dest`.
    #   3. Compute JA3 (md5 of canonical fields) + JA4 (canonical string)
    #      on both. Compare.
    #   4. Exit 0 on collision, 1 on mismatch.
    #
    # v1.0 plan: golden JA4 strings checked into the repo per-dest; quarterly
    # refresh via scripts/update_golden.py.
    print(
        "SCAFFOLD: tls_fingerprint_compare "
        f"dest={dest}({dest_ip}):{port} target={target}({target_ip}):{port}"
    )
    print("SCAFFOLD: full JA3/JA4 comparison lands in v0.5. Exiting 2 (inconclusive).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
