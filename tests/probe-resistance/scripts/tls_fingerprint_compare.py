#!/usr/bin/env python3
"""Scenario 02: TLS shape + JA3/JA3S fingerprint comparison.

Compares a set of TLS handshake-visible features between the public
dest and our VPS (probed with SNI=dest). If Reality's reverse-proxy
fallback is working, every feature should collide. Divergence pinpoints
which layer leaks.

What we compare:

  Stdlib-readable handshake features:
    - Negotiated TLS protocol version  (e.g. TLSv1.3)
    - Chosen cipher suite              (e.g. TLS_AES_256_GCM_SHA384)
    - ALPN protocol selected           (e.g. h2)
  Peer cert (parsed via `openssl x509`):
    - Subject CN + SAN list
    - Issuer CN
    - Public-key algorithm
    - Signature algorithm
  Byte-level fingerprints (parsed from raw handshake bytes captured
  via stdlib ssl.MemoryBIO):
    - JA3      (md5 of ClientHello: version,ciphers,extensions,curves,formats)
    - JA3S     (md5 of ServerHello: version,cipher,extensions)
    - JA3 raw  (the unhashed comma-separated string — handy for diffs)
    - JA3S raw (same for server)

JA3/JA3S follow the canonical Salesforce 2017 spec: GREASE values
(RFC 8701) are excluded from cipher / extension / curve lists. The
md5 hash is what tooling (Suricata, Zeek, Falco) compares against
known-bad lists.

JA4 / JA4S land in v0.5.2 — the FoxIO spec evolved post-2023 and we
want to validate against the reference Python impl before claiming
compatibility. Adding them to TlsShape is a one-line change; the
script contract (env vars + exit codes) stays stable.

Env vars:
    PROBE_TARGET            VPS hostname or IP (required)
    PROBE_REALITY_DEST      dest hostname configured on the panel (required)
    PROBE_REALITY_PORT      dest port (default: 443)
    PROBE_TIMEOUT           per-handshake timeout in seconds (default: 15)
    PROBE_ALPN              comma-separated ALPN to offer (default: "h2,http/1.1")
    PROBE_VERBOSE           any value → dump full baseline + probe on diff

Exit codes:
    0 — every feature matches
    1 — at least one feature diverges (printed as WHY: lines)
    2 — inconclusive (target unreachable, openssl missing, etc.)
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional


# RFC 8701: GREASE values that browsers inject into ClientHello to
# detect intolerant servers. JA3 / JA4 specs both exclude these from
# the fingerprint inputs because they vary per-connection.
def _is_grease(v: int) -> bool:
    """A GREASE value has both bytes equal and the low nibble == 0xa."""
    return (v & 0x0f) == 0x0a and (v >> 8) == (v & 0xff)


# Common TLS extension type numbers we care about (RFC 8446 + IANA).
EXT_SERVER_NAME = 0x0000           # type 0
EXT_SUPPORTED_GROUPS = 0x000a      # type 10 — JA3 "EllipticCurves"
EXT_EC_POINT_FORMATS = 0x000b      # type 11 — JA3 "EllipticCurvePointFormats"
EXT_SIGNATURE_ALGORITHMS = 0x000d  # type 13 — used by JA4_c
EXT_ALPN = 0x0010                  # type 16
EXT_SUPPORTED_VERSIONS = 0x002b    # type 43 — TLS 1.3 real version


def fail_inconclusive(reason: str) -> None:
    print(f"WHY: {reason}", file=sys.stderr)
    sys.exit(2)


def resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        fail_inconclusive(f"could not resolve {host}: {exc}")
        raise  # unreachable


# ---------------------------------------------------------------------------
# Peer cert details — via openssl x509 (carried over from v0.4.1)
# ---------------------------------------------------------------------------

def cert_details(cert_der: bytes) -> dict[str, Optional[str]]:
    """Parse a DER cert via the system openssl binary."""
    if not cert_der:
        return {"subject_cn": None, "san": None, "issuer_cn": None,
                "sig_algo": None, "pubkey_algo": None, "parsed": "no-cert"}

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
            if out["sig_algo"] is None:
                out["sig_algo"] = s.split(":", 1)[1].strip()
        elif s.startswith("Public Key Algorithm:"):
            if out["pubkey_algo"] is None:
                out["pubkey_algo"] = s.split(":", 1)[1].strip()
        elif s.startswith("X509v3 Subject Alternative Name:"):
            out["san"] = "__pending__"
        elif out["san"] == "__pending__" and s and not s.startswith("X509v3"):
            entries = sorted(e.strip() for e in s.replace("DNS:", "").split(","))
            out["san"] = ", ".join(entries)
    if out["san"] == "__pending__":
        out["san"] = None
    return out


# ---------------------------------------------------------------------------
# Byte-level handshake capture via ssl.MemoryBIO
# ---------------------------------------------------------------------------

def capture_tls_handshake(
    target_ip: str, sni: str, port: int, timeout: int,
    alpn_offer: list[str],
) -> tuple[bytes, bytes, Optional[tuple], Optional[str], Optional[str], Optional[bytes]]:
    """Run a real TLS handshake while capturing every byte that crosses
    the socket in either direction.

    Returns (client_bytes, server_bytes, cipher_tuple, version, alpn,
    cert_der). client_bytes is what we sent (contains ClientHello at
    offset 0); server_bytes is what we received (contains ServerHello
    after the 5-byte record header, plus the rest of the handshake).
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if alpn_offer:
        ctx.set_alpn_protocols(alpn_offer)

    in_bio = ssl.MemoryBIO()
    out_bio = ssl.MemoryBIO()
    wrapped = ctx.wrap_bio(in_bio, out_bio, server_hostname=sni)

    sock = socket.create_connection((target_ip, port), timeout=timeout)
    sock.settimeout(timeout)

    client_bytes = b""
    server_bytes = b""

    try:
        while True:
            try:
                wrapped.do_handshake()
                # Drain anything left in out_bio after handshake completion.
                chunk = out_bio.read()
                if chunk:
                    client_bytes += chunk
                    sock.sendall(chunk)
                break
            except ssl.SSLWantReadError:
                # Send anything pending before blocking on recv.
                chunk = out_bio.read()
                if chunk:
                    client_bytes += chunk
                    sock.sendall(chunk)
                data = sock.recv(65535)
                if not data:
                    raise ssl.SSLError("server closed during handshake")
                server_bytes += data
                in_bio.write(data)
            except ssl.SSLWantWriteError:
                chunk = out_bio.read()
                if chunk:
                    client_bytes += chunk
                    sock.sendall(chunk)

        cipher_tuple = wrapped.cipher()
        version = wrapped.version()
        alpn = wrapped.selected_alpn_protocol()
        cert_der = wrapped.getpeercert(binary_form=True)
    finally:
        sock.close()

    return client_bytes, server_bytes, cipher_tuple, version, alpn, cert_der


# ---------------------------------------------------------------------------
# Minimal TLS record + handshake parser (pure stdlib)
# ---------------------------------------------------------------------------

class _ParseError(Exception):
    pass


def _extract_handshake_message(raw: bytes, msg_type: int) -> Optional[bytes]:
    """Walk TLS records in `raw`, return the body of the first Handshake
    record whose inner message type matches `msg_type` (1=ClientHello,
    2=ServerHello). Returns None if no such message is found.

    TLS record layer: ContentType(1) ProtocolVersion(2) Length(2) Body.
    Inside a Handshake record: HandshakeType(1) Length(3) MessageBody.
    """
    i = 0
    while i + 5 <= len(raw):
        content_type = raw[i]
        record_len = struct.unpack_from(">H", raw, i + 3)[0]
        record_body = raw[i + 5 : i + 5 + record_len]
        i += 5 + record_len
        if content_type != 22:  # 22 = handshake
            continue
        j = 0
        while j + 4 <= len(record_body):
            hs_type = record_body[j]
            # 3-byte length
            hs_len = int.from_bytes(record_body[j + 1 : j + 4], "big")
            hs_body = record_body[j + 4 : j + 4 + hs_len]
            j += 4 + hs_len
            if hs_type == msg_type:
                return hs_body
    return None


def _parse_extensions(buf: bytes, offset: int) -> tuple[list[tuple[int, bytes]], int]:
    """Parse a TLS extensions block starting at `offset` of `buf`. The
    block is uint16 length-prefixed. Returns (list of (type, data), bytes_consumed)."""
    if offset + 2 > len(buf):
        return [], 0
    total_len = struct.unpack_from(">H", buf, offset)[0]
    end = offset + 2 + total_len
    out: list[tuple[int, bytes]] = []
    pos = offset + 2
    while pos + 4 <= end:
        ext_type = struct.unpack_from(">H", buf, pos)[0]
        ext_len = struct.unpack_from(">H", buf, pos + 2)[0]
        data = buf[pos + 4 : pos + 4 + ext_len]
        out.append((ext_type, data))
        pos += 4 + ext_len
    return out, 2 + total_len


def _parse_uint16_list(data: bytes) -> list[int]:
    """Parse a uint16-length-prefixed list of uint16 values."""
    if len(data) < 2:
        return []
    length = struct.unpack_from(">H", data, 0)[0]
    body = data[2 : 2 + length]
    return list(struct.unpack(f">{length // 2}H", body))


def _parse_uint8_list(data: bytes) -> list[int]:
    """Parse a uint8-length-prefixed list of uint8 values."""
    if len(data) < 1:
        return []
    length = data[0]
    return list(data[1 : 1 + length])


def parse_client_hello(body: bytes) -> dict:
    """Parse a ClientHello handshake body and return the JA3-relevant fields.

    Layout (RFC 8446 §4.1.2):
      ProtocolVersion legacy_version;     (2 bytes — 0x0303 for TLS1.3 compat)
      Random random;                       (32 bytes)
      opaque legacy_session_id<0..32>;     (1 byte length + body)
      CipherSuite cipher_suites<2..2^16-2>; (2 byte length + body)
      opaque legacy_compression_methods<1..2^8-1>; (1 byte length + body)
      Extension extensions<8..2^16-1>;     (2 byte length + body)
    """
    try:
        if len(body) < 38:
            raise _ParseError(f"client_hello too short: {len(body)} bytes")
        version = struct.unpack_from(">H", body, 0)[0]
        i = 2 + 32  # version + random
        sid_len = body[i]
        i += 1 + sid_len
        cipher_suites_len = struct.unpack_from(">H", body, i)[0]
        ciphers = list(
            struct.unpack(
                f">{cipher_suites_len // 2}H",
                body[i + 2 : i + 2 + cipher_suites_len],
            )
        )
        i += 2 + cipher_suites_len
        comp_len = body[i]
        i += 1 + comp_len
        extensions, _ = _parse_extensions(body, i)

        ext_types = [t for t, _ in extensions]
        curves: list[int] = []
        point_formats: list[int] = []
        sig_algos: list[int] = []
        for ext_type, ext_data in extensions:
            if ext_type == EXT_SUPPORTED_GROUPS:
                curves = _parse_uint16_list(ext_data)
            elif ext_type == EXT_EC_POINT_FORMATS:
                point_formats = _parse_uint8_list(ext_data)
            elif ext_type == EXT_SIGNATURE_ALGORITHMS:
                sig_algos = _parse_uint16_list(ext_data)

        return {
            "version": version,
            "ciphers": ciphers,
            "extensions": ext_types,
            "curves": curves,
            "point_formats": point_formats,
            "sig_algos": sig_algos,
        }
    except (_ParseError, struct.error, IndexError) as exc:
        raise _ParseError(f"client_hello parse failed: {exc}") from exc


def parse_server_hello(body: bytes) -> dict:
    """Parse a ServerHello handshake body and return the JA3S-relevant fields.

    Layout (RFC 8446 §4.1.3):
      ProtocolVersion legacy_version;
      Random random;
      opaque legacy_session_id_echo<0..32>;
      CipherSuite cipher_suite;
      uint8 legacy_compression_method;
      Extension extensions<6..2^16-1>;
    """
    try:
        if len(body) < 38:
            raise _ParseError(f"server_hello too short: {len(body)} bytes")
        version = struct.unpack_from(">H", body, 0)[0]
        i = 2 + 32
        sid_len = body[i]
        i += 1 + sid_len
        cipher = struct.unpack_from(">H", body, i)[0]
        i += 2
        i += 1  # compression_method (1 byte)
        extensions, _ = _parse_extensions(body, i)
        ext_types = [t for t, _ in extensions]
        return {
            "version": version,
            "cipher": cipher,
            "extensions": ext_types,
        }
    except (_ParseError, struct.error, IndexError) as exc:
        raise _ParseError(f"server_hello parse failed: {exc}") from exc


# ---------------------------------------------------------------------------
# JA3 / JA3S computation
# ---------------------------------------------------------------------------

def _filter_grease(ints: list[int]) -> list[int]:
    return [v for v in ints if not _is_grease(v)]


def compute_ja3(ch: dict) -> tuple[str, str]:
    """Salesforce JA3 (2017): md5 of '<ver>,<ciphers>,<exts>,<curves>,<formats>'."""
    fields = [
        str(ch["version"]),
        "-".join(str(v) for v in _filter_grease(ch["ciphers"])),
        "-".join(str(v) for v in _filter_grease(ch["extensions"])),
        "-".join(str(v) for v in _filter_grease(ch["curves"])),
        "-".join(str(v) for v in ch["point_formats"]),
    ]
    full = ",".join(fields)
    digest = hashlib.md5(full.encode("ascii")).hexdigest()
    return full, digest


def compute_ja3s(sh: dict) -> tuple[str, str]:
    """JA3S: md5 of '<ver>,<cipher>,<exts>'.

    Note for TLS 1.3: per RFC 8446 the ServerHello carries a legacy
    version of 0x0303 and most extensions migrated to the encrypted
    EncryptedExtensions message. JA3S therefore captures less than it
    did in TLS 1.2 — still useful as a *delta* indicator between dest
    and probe, but the absolute value is less discriminating.
    """
    fields = [
        str(sh["version"]),
        str(sh["cipher"]),
        "-".join(str(v) for v in _filter_grease(sh["extensions"])),
    ]
    full = ",".join(fields)
    digest = hashlib.md5(full.encode("ascii")).hexdigest()
    return full, digest


# ---------------------------------------------------------------------------
# Shape capture + diff
# ---------------------------------------------------------------------------

@dataclass
class TlsShape:
    version: Optional[str] = None
    cipher: Optional[str] = None
    alpn: Optional[str] = None
    cert: dict[str, Optional[str]] = field(default_factory=dict)
    ja3: Optional[str] = None
    ja3_raw: Optional[str] = None
    ja3s: Optional[str] = None
    ja3s_raw: Optional[str] = None
    parse_state: dict[str, str] = field(default_factory=dict)


def capture_shape(target_ip: str, sni: str, port: int, timeout: int,
                  alpn_offer: list[str]) -> TlsShape:
    client_bytes, server_bytes, cipher_tuple, version, alpn, cert_der = (
        capture_tls_handshake(target_ip, sni, port, timeout, alpn_offer)
    )

    shape = TlsShape(
        version=version,
        cipher=cipher_tuple[0] if cipher_tuple else None,
        alpn=alpn,
        cert=cert_details(cert_der or b""),
        parse_state={},
    )

    # Parse + fingerprint ClientHello
    ch_body = _extract_handshake_message(client_bytes, msg_type=1)
    if ch_body is None:
        shape.parse_state["ja3"] = "no-client-hello-in-stream"
    else:
        try:
            ch = parse_client_hello(ch_body)
            shape.ja3_raw, shape.ja3 = compute_ja3(ch)
            shape.parse_state["ja3"] = "ok"
        except _ParseError as exc:
            shape.parse_state["ja3"] = f"parse-error: {exc}"

    # Parse + fingerprint ServerHello
    sh_body = _extract_handshake_message(server_bytes, msg_type=2)
    if sh_body is None:
        shape.parse_state["ja3s"] = "no-server-hello-in-stream"
    else:
        try:
            sh = parse_server_hello(sh_body)
            shape.ja3s_raw, shape.ja3s = compute_ja3s(sh)
            shape.parse_state["ja3s"] = "ok"
        except _ParseError as exc:
            shape.parse_state["ja3s"] = f"parse-error: {exc}"

    return shape


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

    check("ja3", baseline.ja3, probe.ja3)
    check("ja3s", baseline.ja3s, probe.ja3s)

    # Surface parse-state divergence so an "ja3 mismatch" doesn't hide
    # the fact that one side couldn't be parsed at all.
    for key in ("ja3", "ja3s"):
        sb = baseline.parse_state.get(key, "?")
        sp = probe.parse_state.get(key, "?")
        if sb != "ok" or sp != "ok":
            why.append(f"{key}.parse_state baseline={sb!r} probe={sp!r}")

    return why


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
        if os.environ.get("PROBE_VERBOSE"):
            print("--- baseline ---")
            print(json.dumps(baseline.__dict__, indent=2, default=str))
            print("--- probe ---")
            print(json.dumps(probe.__dict__, indent=2, default=str))
        return 1

    print(
        f"OK [tls={probe.version} cipher={probe.cipher} alpn={probe.alpn} "
        f"cert.cn={probe.cert.get('subject_cn')} "
        f"ja3={probe.ja3} ja3s={probe.ja3s}] "
        f"dest={dest}({dest_ip}) vps={target}({target_ip})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
