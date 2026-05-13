#!/usr/bin/env python3
"""Scenario 02: TLS shape comparison (server vs dest).

Compares a set of TLS handshake-visible shape features between the public
dest and our VPS (probed with SNI=dest). If Reality's reverse-proxy
fallback is working, every feature should collide. Divergence pinpoints
which layer leaks.

What we compare (all visible from stdlib `ssl` + a single `openssl x509`
call on the captured peer cert):

  - Negotiated TLS protocol version  (e.g. TLSv1.3)
  - Chosen cipher suite              (e.g. TLS_AES_256_GCM_SHA384)
  - ALPN protocol selected           (e.g. h2)
  - Peer cert subject CN + SAN list  (must match dest exactly)
  - Peer cert issuer CN              (must match dest's chain root)
  - Peer cert public-key algorithm   (e.g. ECDSA-P-256)
  - Peer cert signature algorithm    (e.g. ecdsa-with-SHA384)

What we don't claim:

This is *not* a true JA3 / JA4 fingerprint. Those compute over raw
ClientHello / ServerHello bytes (extension order, supported-groups
list, key-share preference) which the Python `ssl` module abstracts
away. A future v0.5+ revision can plug in scapy or tlslite-ng to
capture handshake bytes; the comparator API here stays the same.

In practice, the seven shape features above catch the failure modes
we've actually seen in production:

  - Reality config bug → server presents OWN cert (subject diverges)
  - Xray TLS-lib drift → different cipher chosen for the same offer
  - Reverse-proxy half-broken → ALPN mismatch (server speaks h1, dest h2)
  - Outbound to dest blocked from the VPS → dest unreachable from probe

A real JA3/JA4 mismatch is rare in the same scenarios where the seven
features collide. The opposite (features collide but JA3 differs) is
the v1.0 territory.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname configured on the panel (required)
    PROBE_REALITY_PORT      dest port (default: 443)
    PROBE_TIMEOUT           per-handshake timeout in seconds (default: 15)
    PROBE_ALPN              comma-separated ALPN to offer (default: "h2,http/1.1")

Exit codes:
    0 — every feature matches
    1 — at least one feature diverges (printed as WHY: lines)
    2 — inconclusive (target unreachable, openssl missing, etc.)
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional


def fail_inconclusive(reason: str) -> None:
    print(f"WHY: {reason}", file=sys.stderr)
    sys.exit(2)


def resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        fail_inconclusive(f"could not resolve {host}: {exc}")
        raise  # unreachable


def cert_details(cert_der: bytes) -> dict[str, Optional[str]]:
    """Parse a DER cert via the system openssl binary.

    We shell out instead of pulling in `cryptography` because the suite
    is stdlib-only by v0.4.0 spec, and openssl is on every Debian/Ubuntu
    box that can run stealth-vps. If openssl is missing we degrade
    gracefully — return None for parsed fields, signal partial parse.
    """
    if not cert_der:
        return {"subject_cn": None, "san": None, "issuer_cn": None,
                "sig_algo": None, "pubkey_algo": None, "parsed": "no-cert"}

    # Write to a temp file rather than stdin: some openssl builds on
    # Windows / Git Bash mangle binary stdin (CRLF). A real file avoids
    # the issue and is symmetric on Linux runners.
    with tempfile.NamedTemporaryFile(suffix=".der", delete=False) as f:
        f.write(cert_der)
        cert_path = f.name
    try:
        try:
            result = subprocess.run(
                ["openssl", "x509", "-inform", "DER", "-in", cert_path,
                 "-noout", "-text", "-subject", "-issuer"],
                capture_output=True,
                timeout=10,
                check=True,
            )
        except FileNotFoundError:
            return {"subject_cn": None, "san": None, "issuer_cn": None,
                    "sig_algo": None, "pubkey_algo": None, "parsed": "no-openssl"}
        except subprocess.CalledProcessError as exc:
            return {"subject_cn": None, "san": None, "issuer_cn": None,
                    "sig_algo": None, "pubkey_algo": None,
                    "parsed": f"openssl-error: "
                              f"{exc.stderr.decode(errors='replace')[:200]}"}
    finally:
        try:
            os.unlink(cert_path)
        except OSError:
            pass

    text = result.stdout.decode(errors="replace")
    out: dict[str, Optional[str]] = {
        "subject_cn": None, "san": None, "issuer_cn": None,
        "sig_algo": None, "pubkey_algo": None, "parsed": "ok",
    }

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("subject=") or s.startswith("subject ="):
            # subject=CN=www.microsoft.com,O=Microsoft...
            for kv in s.split("=", 1)[1].split(","):
                k, _, v = kv.strip().partition("=")
                if k.strip().upper() == "CN":
                    out["subject_cn"] = v.strip()
                    break
        elif s.startswith("issuer=") or s.startswith("issuer ="):
            for kv in s.split("=", 1)[1].split(","):
                k, _, v = kv.strip().partition("=")
                if k.strip().upper() == "CN":
                    out["issuer_cn"] = v.strip()
                    break
        elif s.startswith("Signature Algorithm:"):
            # Two lines in -text; the first hit is what was used.
            if out["sig_algo"] is None:
                out["sig_algo"] = s.split(":", 1)[1].strip()
        elif s.startswith("Public Key Algorithm:"):
            if out["pubkey_algo"] is None:
                out["pubkey_algo"] = s.split(":", 1)[1].strip()
        elif s.startswith("X509v3 Subject Alternative Name:"):
            # SAN values are on the next non-indented line; we'll catch
            # them via a sentinel state instead.
            out["san"] = "__pending__"
        elif out["san"] == "__pending__" and s and not s.startswith("X509v3"):
            # Normalise + sort so reorderings don't cause spurious diff.
            entries = sorted(e.strip() for e in s.replace("DNS:", "").split(","))
            out["san"] = ", ".join(entries)
    if out["san"] == "__pending__":
        out["san"] = None
    return out


@dataclass
class TlsShape:
    version: Optional[str] = None
    cipher: Optional[str] = None
    alpn: Optional[str] = None
    cert: dict[str, Optional[str]] = field(default_factory=dict)


def capture_shape(target_ip: str, sni: str, port: int, timeout: int,
                  alpn_offer: list[str]) -> TlsShape:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if alpn_offer:
        ctx.set_alpn_protocols(alpn_offer)

    sock = socket.create_connection((target_ip, port), timeout=timeout)
    try:
        wrap = ctx.wrap_socket(sock, server_hostname=sni)
        try:
            cipher_tuple = wrap.cipher()
            version = wrap.version()
            alpn = wrap.selected_alpn_protocol()
            cert_der = wrap.getpeercert(binary_form=True)
        finally:
            try:
                wrap.unwrap()
            except (OSError, ssl.SSLError):
                pass
    finally:
        sock.close()

    return TlsShape(
        version=version,
        cipher=cipher_tuple[0] if cipher_tuple else None,
        alpn=alpn,
        cert=cert_details(cert_der or b""),
    )


def diff_shapes(baseline: TlsShape, probe: TlsShape) -> list[str]:
    """Return one WHY: line per diverging field."""
    why: list[str] = []

    def check(label: str, a: object, b: object) -> None:
        if a != b:
            why.append(f"{label} baseline={a!r} probe={b!r}")

    check("tls_version", baseline.version, probe.version)
    check("cipher", baseline.cipher, probe.cipher)
    check("alpn", baseline.alpn, probe.alpn)

    for key in ("subject_cn", "san", "issuer_cn", "sig_algo", "pubkey_algo"):
        check(f"cert.{key}", baseline.cert.get(key), probe.cert.get(key))

    parsed_b = baseline.cert.get("parsed")
    parsed_p = probe.cert.get("parsed")
    if parsed_b != "ok" or parsed_p != "ok":
        why.append(f"cert.parsed_state baseline={parsed_b!r} probe={parsed_p!r}")
    return why


def main() -> int:
    target = os.environ.get("PROBE_TARGET")
    dest = os.environ.get("PROBE_REALITY_DEST")
    port = int(os.environ.get("PROBE_REALITY_PORT", "443"))
    timeout = int(os.environ.get("PROBE_TIMEOUT", "15"))
    alpn_csv = os.environ.get("PROBE_ALPN", "h2,http/1.1")
    alpn_offer = [a.strip() for a in alpn_csv.split(",") if a.strip()]

    if not target:
        fail_inconclusive("env PROBE_TARGET is required")
    if not dest:
        fail_inconclusive("env PROBE_REALITY_DEST is required")

    target_ip = resolve(target)
    dest_ip = resolve(dest)

    try:
        baseline = capture_shape(dest_ip, sni=dest, port=port,
                                 timeout=timeout, alpn_offer=alpn_offer)
    except (OSError, ssl.SSLError) as exc:
        fail_inconclusive(f"baseline handshake to dest {dest_ip}:{port} failed: {exc}")
        return 2  # for type checker

    try:
        probe = capture_shape(target_ip, sni=dest, port=port,
                              timeout=timeout, alpn_offer=alpn_offer)
    except (OSError, ssl.SSLError) as exc:
        fail_inconclusive(f"probe handshake to vps {target_ip}:{port} failed: {exc}")
        return 2

    why = diff_shapes(baseline, probe)

    if why:
        print(
            f"FAIL: tls_shape diverges "
            f"[dest={dest}({dest_ip}) vps={target}({target_ip})]"
        )
        for line in why:
            print(f"WHY: {line}")
        # Optional verbose dump for triage:
        if os.environ.get("PROBE_VERBOSE"):
            print("--- baseline ---")
            print(json.dumps(baseline.__dict__, indent=2, default=str))
            print("--- probe ---")
            print(json.dumps(probe.__dict__, indent=2, default=str))
        return 1

    print(
        f"OK [tls={probe.version} cipher={probe.cipher} alpn={probe.alpn} "
        f"cert.cn={probe.cert.get('subject_cn')} "
        f"cert.issuer={probe.cert.get('issuer_cn')}] "
        f"dest={dest}({dest_ip}) vps={target}({target_ip})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
