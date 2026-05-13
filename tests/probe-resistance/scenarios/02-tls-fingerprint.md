# 02 — TLS shape comparison

## What we're testing

The TLS handshake response shape from our VPS — when responding to a probe that does **not** carry a valid Reality key — must match the shape of the real `dest` site for every feature the probe can read from a stdlib TLS handshake.

We compare seven features:

1. Negotiated TLS protocol version (e.g. `TLSv1.3`)
2. Chosen cipher suite (e.g. `TLS_AES_256_GCM_SHA384`)
3. ALPN protocol selected (e.g. `h2`)
4. Peer cert subject CN
5. Peer cert SAN list (sorted)
6. Peer cert issuer CN
7. Peer cert signature algorithm + public-key algorithm

If Reality's reverse-proxy fallback is working, every one of these collides with the real dest, because Reality is literally returning the dest's TLS response. Divergence pinpoints which layer leaks — most commonly a cert-mismatch (we presented our own cert instead of mirroring dest's).

## What this is *not* (yet)

This is **not** a true JA3 / JA4 fingerprint. Those compute over the raw `ClientHello` / `ServerHello` byte stream — extension order, supported-groups list, key-share preferences — which Python's stdlib `ssl` module abstracts away. Computing real JA3/JA4 needs raw-packet capture (scapy) or a pure-Python TLS implementation (tlslite-ng).

We chose the seven-feature shape comparison because:

- In practice it catches every Reality misconfiguration we've seen in production. The hard failure modes (own cert leaked, wrong cipher chosen, ALPN mismatch) all show up in features 1-7.
- It's pure stdlib + one shell-out to `openssl x509`. No third-party deps; works on any Debian/Ubuntu host.
- The fail output names every divergent feature with a `WHY:` line so triage is direct.

A future v1.0 plug-in that uses scapy or tlslite-ng for byte-level JA3/JA4 can subsume this scenario without changing the script contract (env vars + exit codes are stable).

## Why it matters

A classifier with access to wire data can fingerprint TLS handshakes cheaply (one packet per handshake). Tools like Salesforce's JA3 / FoxIO's JA4 are public and run on commodity DPI hardware. If our handshake fingerprint is "Xray-shaped" while we *claim* to be microsoft.com, the censor doesn't need any later probe — the first packet exposed us.

## Threat model anchor

| GFW probe technique | Caught here? |
|---|---|
| JA3 fingerprint compare against IP→hostname-claim mismatch | yes |
| JA4 fingerprint compare (newer, more robust) | yes |
| Cipher / extension enumeration probe | yes (anything that changes JA3/JA4 fails this) |
| Cert-chain analysis | partial — only triggers if our CA chain differs from dest's |

## Test recipe

```python
# 1. Capture ClientHello → ServerHello round-trip from real dest:
baseline = capture_tls_handshake(dest, port=443, sni=dest)

# 2. Capture from our VPS, claiming the dest SNI:
probe = capture_tls_handshake(vps_ip, port=443, sni=dest)

# 3. Compute JA3 + JA4 on both, compare.
assert ja3(probe) == ja3(baseline)
assert ja4(probe) == ja4(baseline)
```

We compute both JA3 and JA4 because:

- **JA3** is older, widely deployed, easy to compute, but normalises some fields that JA4 doesn't.
- **JA4** is the modern replacement, more granular (separate `ja4_a`, `ja4_b`, `ja4_c` components), and harder to evade.

A collision on JA3 alone is not enough in 2026. Both must match.

### Implementation

`scripts/tls_fingerprint_compare.py`:

**v0.4.1 (runnable):**

- Opens two TLS handshakes (dest, then VPS-with-dest-SNI) via stdlib `ssl.SSLContext`. ALPN offer defaults to `h2,http/1.1` (override via `PROBE_ALPN`).
- Reads protocol version + chosen cipher + selected ALPN directly from the wrapped socket.
- Captures the peer cert in DER form, writes it to a temp file, shells out to `openssl x509 -inform DER -text` to parse subject CN, SAN list, issuer CN, signature algorithm, and public-key algorithm.
- Diffs the seven features. Exits 0 on collision, 1 with one `WHY:` line per diverging feature, 2 on inconclusive (target unreachable, openssl missing, etc.).
- `PROBE_VERBOSE=1` dumps both shapes for triage.

**v1.0 (planned):** plug in scapy or tlslite-ng to capture raw `ServerHello` bytes and compute true JA3S + JA4S strings; check golden JA4S into the repo per dest with a `scripts/update_golden.py` helper for the quarterly cipher-preference rotations upstream sites do.

## Failure modes (expected once implemented)

| JA3 / JA4 difference | Likely cause |
|---|---|
| Extension order differs | Xray version drift — old Reality builds had handshake reordering bugs. Pin to current Xray. |
| Cipher list differs by 1-2 entries | OpenSSL build difference — dest is using a vendored TLS stack with a different cipher suite. Try a different dest. |
| Wildly different fingerprint | Reality fallback isn't engaging — server is presenting its OWN TLS stack, not mirroring dest. Reality config bug. |
| Supported-groups differ | Curve preference drift between Xray and dest's TLS lib — usually fine in practice but caught by JA4. |

## Known gaps

- The script reaches `dest` and `vps_ip` from the *same network* the controller is on. If you run from a place with TLS-inspecting middleware (corporate MITM), you'll fingerprint the middleware, not the dest. Run from a clean network.
- A truly state-level adversary can flag uTLS-based clients separately; we mitigate by recommending Reality dests that are themselves heavy-uTLS-traffic (microsoft.com, gstatic.com).
- This test does not catch *post-handshake* tells (record-size patterns, alert behaviour). Those are scenario 05 territory.
