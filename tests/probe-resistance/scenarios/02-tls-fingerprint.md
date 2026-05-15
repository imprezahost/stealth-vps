# 02 — TLS shape + JA3/JA3S fingerprint comparison

## What we're testing

The TLS handshake response shape from our VPS — when responding to a probe that does **not** carry a valid Reality key — must match the shape of the real `dest` site across:

**Stdlib-readable features (7):**

1. Negotiated TLS protocol version (e.g. `TLSv1.3`)
2. Chosen cipher suite (e.g. `TLS_AES_256_GCM_SHA384`)
3. ALPN protocol selected (e.g. `h2`)
4. Peer cert subject CN
5. Peer cert SAN list (sorted)
6. Peer cert issuer CN
7. Peer cert signature + public-key algorithms

**Byte-level fingerprints (2, since v0.5.1) — items 8-9 of the comparison set:**

1. **JA3** — Salesforce 2017 spec. md5 of `version,ciphers,extensions,curves,formats` from ClientHello bytes captured via `ssl.MemoryBIO`. GREASE values (RFC 8701) excluded.
2. **JA3S** — md5 of `version,cipher,extensions` from ServerHello bytes. Same byte-level capture path.

If Reality's reverse-proxy fallback is working, every one of these collides with the real dest, because Reality is literally returning the dest's TLS response. Divergence pinpoints which layer leaks — most commonly a cert-mismatch (we presented our own cert instead of mirroring dest's).

## JA3 versus JA3S — what each catches

**JA3 fingerprints the *client*.** Both our probes use the same Python stdlib `ssl` client, so the dest-side JA3 and the VPS-side JA3 are *expected to be identical* in this scenario. JA3 is exposed in the output for two reasons: (a) it lets you confirm the probe is well-behaved before you start triaging server-side issues, and (b) if the JA3s ever DO differ between probes, something interfered with the controller-side TLS stack (Python upgrade, OpenSSL library swap, MITM CA installed) and the test is invalid.

**JA3S fingerprints the *server*.** This is the value you actually care about for Reality-fallback detection. A working Reality returns the dest's ServerHello bytes verbatim → JA3S collision. A broken Reality returns Xray's own ServerHello → JA3S divergence.

### JA3S in TLS 1.3 — a real limitation

RFC 8446 moved most ServerHello extensions into the `EncryptedExtensions` message, which is encrypted with the key derived from the handshake's earlier `key_share`. JA3S therefore captures only `version`, `cipher`, and the *clear-text* portion of extensions — typically just `supported_versions` and `key_share`.

In practice this means: two completely different TLS 1.3 servers that happen to negotiate the same cipher (which is common — `TLS_AES_256_GCM_SHA384` dominates) and present a similar bare ServerHello can produce *identical* JA3S strings even though everything else about them differs. **JA3S is a much weaker signal in TLS 1.3 than it was in TLS 1.2.** The seven stdlib-readable features above (especially cert subject_cn / SAN / issuer) remain the primary discriminators.

The v0.5.x roadmap picks up JA4 + JA4S (FoxIO 2023+ spec). JA4 captures more pre-encryption bytes (signature_algorithms list ordering, ALPN value, etc.) and JA4S includes a small extension hash that's more discriminating than JA3S's md5 of clear-only extensions.

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

**v0.5.1 (runnable, JA3+JA3S added):**

- Opens two TLS handshakes (dest, then VPS-with-dest-SNI) via stdlib `ssl.SSLContext` wrapped over an `ssl.MemoryBIO` pair. The MemoryBIO setup lets us capture every byte that crosses the socket in either direction without losing the convenience of a normal handshake.
- `_extract_handshake_message()` walks the TLS record layer in the captured byte streams and returns the body of the first ClientHello / ServerHello it finds.
- `parse_client_hello()` and `parse_server_hello()` are pure-stdlib parsers (no `scapy` / `tlslite-ng` dependency at v0.5.1): they decode version, cipher list, extension list, supported_groups, ec_point_formats, signature_algorithms.
- `compute_ja3()` / `compute_ja3s()` apply the Salesforce 2017 spec: GREASE values (RFC 8701) filtered out, fields joined by commas with `-` between list elements, md5 hex digest taken.
- Reads protocol version + chosen cipher + selected ALPN directly from the wrapped socket (stdlib-readable features 1-3).
- Captures the peer cert in DER form, writes it to a temp file, shells out to `openssl x509 -inform DER -text` to parse subject CN, SAN list, issuer CN, signature algorithm, and public-key algorithm (features 4-7).
- Diffs all 9 features. Exits 0 on collision, 1 with one `WHY:` line per diverging feature, 2 on inconclusive (target unreachable, openssl missing, parse failure).
- `PROBE_VERBOSE=1` dumps both shapes (including JA3 raw strings) for triage.
- `parse_state` dict on each shape carries `"ok"` / `"parse-error: ..."` per fingerprint so a parser regression on one side doesn't silently produce a fake match.

**v0.5.2 (planned):** JA4 + JA4S (FoxIO 2023+ spec). The contract is stable, so adding `ja4` / `ja4s` to `TlsShape` + extending `diff_shapes()` is a localized change. Will need a reference implementation (`ja4-python`) for golden-string cross-validation before claiming spec compliance.

**v1.0 (planned):** snapshot tests with golden JA3 / JA3S / JA4 / JA4S checked into the repo per dest; `scripts/update_golden.py` helper for the quarterly cipher-preference rotations upstream sites do.

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
