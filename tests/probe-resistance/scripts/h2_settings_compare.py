#!/usr/bin/env python3
"""Scenario 03 (companion): HTTP/2 SETTINGS-frame comparison.

Sibling to active_probe.py — that script forces ALPN `http/1.1` so the
response parses with stdlib `http.client`. This one is the opposite:
forces ALPN `h2`, completes the HTTP/2 connection preface, and parses
the server's first SETTINGS frame inline. Returns the set of
`(identifier, value)` pairs and compares between dest and VPS.

Why a separate script: stdlib has no HTTP/2 implementation. We don't
need one — only the SETTINGS frame parser, which is ~20 lines of
straightforward byte unpacking. Keeping it in a focused script lets
`active_probe.py` stay h1-only and lets this one stay h2-only.

The HTTP/2 connection preface ([RFC 7540 §3.5]) is exactly 24 bytes:

    "PRI * HTTP/2.0\\r\\n\\r\\nSM\\r\\n\\r\\n"

After the preface, both peers send a SETTINGS frame (type 0x4) as the
first frame. The server's SETTINGS frame is what we capture. Frame
header layout ([RFC 7540 §4.1]):

    Length(24) | Type(8) | Flags(8) | R(1)+StreamID(31)

then payload: zero or more (Identifier:uint16, Value:uint32) pairs.

If Reality's reverse-proxy fallback is working, the SETTINGS we receive
should match the dest's exactly (modulo the rare case where the dest
load-balancer rotates which backend serves us). Divergence is a strong
signal that Xray is terminating h2 itself and presenting its own
HTTP/2 stack.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname (required)
    PROBE_REALITY_PORT      VPS port where Reality listens (default: 443).
                            Set to e.g. 43338 when Reality runs on a non-443 port.
    PROBE_DEST_PORT         port on the public dest (default: 443; rarely changed).
    PROBE_TIMEOUT           per-handshake timeout in seconds (default: 15)
    PROBE_VERBOSE           any value → dump full settings on diff

Exit codes:
    0 — SETTINGS frames match
    1 — divergence (printed as WHY: lines, one per differing setting)
    2 — inconclusive (target unreachable, dest doesn't speak h2,
        h2 preface rejected, etc.)
"""

from __future__ import annotations

import os
import socket
import ssl
import sys
from typing import Optional

# HTTP/2 connection preface (RFC 7540 §3.5) — exactly 24 bytes.
H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

# Frame type IDs (RFC 7540 §11.2)
FRAME_TYPE_SETTINGS = 0x4
SETTINGS_FLAG_ACK = 0x1

# An empty SETTINGS frame from us (length=0, type=4, flags=0, stream=0)
EMPTY_SETTINGS_FRAME = b"\x00\x00\x00" + b"\x04" + b"\x00" + b"\x00\x00\x00\x00"

# Known SETTINGS identifiers (RFC 7540 §6.5.2 + RFC 8441 + RFC 9218).
SETTING_NAMES: dict[int, str] = {
    0x1: "HEADER_TABLE_SIZE",
    0x2: "ENABLE_PUSH",
    0x3: "MAX_CONCURRENT_STREAMS",
    0x4: "INITIAL_WINDOW_SIZE",
    0x5: "MAX_FRAME_SIZE",
    0x6: "MAX_HEADER_LIST_SIZE",
    0x8: "ENABLE_CONNECT_PROTOCOL",
    0x9: "NO_RFC7540_PRIORITIES",
}


def fail_inconclusive(reason: str) -> None:
    print(f"WHY: {reason}", file=sys.stderr)
    sys.exit(2)


def resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        fail_inconclusive(f"could not resolve {host}: {exc}")
        raise  # unreachable


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    """Read exactly n bytes from sock, or raise on early close."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ssl.SSLError(f"connection closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def capture_h2_settings(
    target_ip: str, sni: str, port: int, timeout: int,
) -> tuple[Optional[dict[int, int]], str]:
    """Open TLS+h2 to target_ip, send the H/2 preface, parse the server's
    first SETTINGS frame. Returns (settings_dict, status_string).

    status_string:
      "ok"             — got server's SETTINGS, returned in dict
      "alpn-not-h2"    — server didn't pick h2; can't continue
      "no-settings"    — server didn't send SETTINGS in first 20 frames
      "protocol-error" — server replied with GOAWAY or invalid frame
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(["h2"])

    sock = socket.create_connection((target_ip, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        wrapped = ctx.wrap_socket(sock, server_hostname=sni)
        try:
            if wrapped.selected_alpn_protocol() != "h2":
                return None, "alpn-not-h2"

            wrapped.sendall(H2_PREFACE)
            wrapped.sendall(EMPTY_SETTINGS_FRAME)

            # Read frames until we see SETTINGS (non-ACK). Spec allows
            # the server to interleave WINDOW_UPDATE etc. before
            # SETTINGS, but in practice SETTINGS is first.
            for _ in range(20):
                header = _recv_exact(wrapped, 9)
                length = int.from_bytes(header[0:3], "big")
                frame_type = header[3]
                flags = header[4]
                # stream_id is bytes 5..9 with high bit reserved
                payload = _recv_exact(wrapped, length) if length else b""

                if frame_type == FRAME_TYPE_SETTINGS:
                    if flags & SETTINGS_FLAG_ACK:
                        # Server's SETTINGS frame is the one without
                        # the ACK flag; this one is acknowledging
                        # our (empty) SETTINGS — skip.
                        continue
                    settings: dict[int, int] = {}
                    pos = 0
                    while pos + 6 <= len(payload):
                        sid = int.from_bytes(payload[pos : pos + 2], "big")
                        sval = int.from_bytes(payload[pos + 2 : pos + 6], "big")
                        settings[sid] = sval
                        pos += 6
                    return settings, "ok"
                elif frame_type == 0x7:  # GOAWAY
                    return None, "protocol-error: server sent GOAWAY"
                # else: WINDOW_UPDATE / PING / etc. — ignore, keep reading
            return None, "no-settings"
        finally:
            try:
                wrapped.unwrap()
            except (OSError, ssl.SSLError):
                pass
    finally:
        sock.close()


def fmt_settings(s: dict[int, int]) -> str:
    """Human-readable single-line render of a SETTINGS dict.

    Always sorted by identifier so two equivalent dicts render the same
    string regardless of insertion order.
    """
    parts: list[str] = []
    for sid in sorted(s):
        name = SETTING_NAMES.get(sid, f"0x{sid:02x}")
        parts.append(f"{name}={s[sid]}")
    return ", ".join(parts)


def main() -> int:
    target = os.environ.get("PROBE_TARGET")
    dest = os.environ.get("PROBE_REALITY_DEST")
    # v0.5.3: split dest port from probe port. Both default to 443.
    reality_port = int(os.environ.get("PROBE_REALITY_PORT", "443"))
    dest_port = int(os.environ.get("PROBE_DEST_PORT", "443"))
    timeout = int(os.environ.get("PROBE_TIMEOUT", "15"))

    if not target:
        fail_inconclusive("env PROBE_TARGET is required")
    if not dest:
        fail_inconclusive("env PROBE_REALITY_DEST is required")

    target_ip = resolve(target)
    dest_ip = resolve(dest)

    try:
        baseline, baseline_state = capture_h2_settings(
            dest_ip, sni=dest, port=dest_port, timeout=timeout
        )
    except (OSError, ssl.SSLError) as exc:
        fail_inconclusive(
            f"baseline h2 capture from dest {dest_ip}:{dest_port} failed: {exc}"
        )
        return 2  # for type checker

    if baseline_state != "ok":
        fail_inconclusive(
            f"baseline state={baseline_state} — dest {dest}({dest_ip}):{dest_port} "
            f"is not h2-capable or rejected our preface; cannot compare"
        )
        return 2

    try:
        probe, probe_state = capture_h2_settings(
            target_ip, sni=dest, port=reality_port, timeout=timeout
        )
    except (OSError, ssl.SSLError) as exc:
        fail_inconclusive(
            f"probe h2 capture from vps {target_ip}:{reality_port} failed: {exc}"
        )
        return 2

    if probe_state != "ok":
        # Probe-side failure is interesting on its own — if dest speaks
        # h2 but our VPS doesn't, that's a Reality-fallback gap.
        print(
            f"FAIL: probe state={probe_state} "
            f"(dest {dest}({dest_ip}) speaks h2; vps {target}({target_ip}) does not)"
        )
        print(
            f"WHY: dest baseline SETTINGS = {fmt_settings(baseline)} "
            f"— vps could not produce a comparable SETTINGS frame"
        )
        return 1

    assert baseline is not None and probe is not None

    why: list[str] = []
    for sid in sorted(set(baseline) | set(probe)):
        bv = baseline.get(sid)
        pv = probe.get(sid)
        if bv != pv:
            name = SETTING_NAMES.get(sid, f"0x{sid:02x}")
            why.append(f"setting.{name} baseline={bv!r} probe={pv!r}")

    if why:
        print(
            f"FAIL: h2 SETTINGS frame diverges "
            f"[dest={dest}({dest_ip}) vps={target}({target_ip})]"
        )
        for line in why:
            print(f"WHY: {line}")
        if os.environ.get("PROBE_VERBOSE"):
            print(f"--- baseline ---\n  {fmt_settings(baseline)}")
            print(f"--- probe ---\n  {fmt_settings(probe)}")
        return 1

    print(
        f"OK [h2_settings={fmt_settings(probe)}] "
        f"dest={dest}({dest_ip}) vps={target}({target_ip})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
