# 02 — TLS fingerprint comparison

## What we're testing

The TLS ClientHello and ServerHello shape (cipher list, extension order, supported groups, ALPN, key-share) of our VPS — when responding to a probe that does **not** carry a valid Reality key — must match the shape of the real `dest` site.

In practical terms: capture the JA3/JA4 fingerprint from both `dest` directly and our VPS in fallback mode. They must collide.

Reality's design intends this: when the server can't verify the client's key, it reverse-proxies the full TLS handshake from `dest`. A successful collision proves the proxy is working at the handshake level, not just at the HTTP body level.

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

**v0.4.0 (scaffold):** parses env vars, validates the targets resolve, prints what it would test, exits 2 (inconclusive). Establishes the script contract so v0.5 / v1.0 can fill the body without changing how CI calls it.

**v0.5 (planned):**

- Use `scapy.layers.tls` or `tlslite-ng` for raw ClientHello capture, or shell out to `openssl s_client -msg -tlsextdebug` and parse stderr.
- JA3 computation: open-source `pyJA3` or inline (it's ~30 lines).
- JA4 computation: `ja4-python` (FoxIO reference impl) or inline (~80 lines, well-specified).
- Exit codes per the suite contract.

**v1.0 (planned):** snapshot tests — golden JA4 strings checked into the repo per-dest, with a `update_golden.py` helper for when upstream microsoft.com / cloudflare.com rotate cipher preferences.

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
