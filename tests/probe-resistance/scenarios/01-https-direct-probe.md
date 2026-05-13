# 01 — HTTPS direct probe

## What we're testing

A bare `curl https://<vps-ip>/` (with no SNI matching the Reality `dest`, or with the dest SNI but no Reality client logic) must produce a response indistinguishable from probing the real `dest` (e.g. `www.microsoft.com`) directly.

A failure here means: the server is leaking a "this is a proxy" tell on the very first probe shape — a generic Nginx 403, a stock Xray banner, an unexpected redirect, a self-signed cert with our hostname, anything that a classifier could spot in a single request.

## Why it matters

This is the cheapest probe a censor can run. They aim a `curl` at a million IPs and bucket the responses. If our bucket is "anything that isn't microsoft.com-shaped", we lose, full stop — we don't even need the more sophisticated probes 02 / 03.

## Threat model anchor

| GFW probe technique | Caught here? |
|---|---|
| HTTPS GET to IP with SNI = dest | yes |
| HTTPS GET to IP with arbitrary SNI | yes (Reality falls back to dest reverse-proxy) |
| HTTPS GET to IP with no SNI | yes |
| Plain TCP connect, no TLS | scenario 04 catches this |

## Test recipe

```bash
# 1. Establish baseline: what does the real dest return?
BASELINE=$(curl -s -o /dev/null -w "%{http_code} %{size_download} %{ssl_verify_result}\n" \
  "https://${PROBE_REALITY_DEST}/")

# 2. Probe our VPS with the dest SNI:
PROBE=$(curl -s -o /dev/null -w "%{http_code} %{size_download} %{ssl_verify_result}\n" \
  --resolve "${PROBE_REALITY_DEST}:443:${PROBE_TARGET_IP}" \
  "https://${PROBE_REALITY_DEST}/")

# 3. They should match within tolerance.
[ "$BASELINE" = "$PROBE" ] || fail "shape mismatch"
```

We compare a 3-tuple `(http_code, response_size, ssl_verify_result)` instead of a byte-level diff because legitimate caches and CDN-shaped microsoft.com responses vary on the size dimension. The 3-tuple stays remarkably stable: a Reality fallback that's "almost right but not quite" will fail one of these three.

### Implementation

`scripts/https_direct_probe.sh`:

- Requires env vars: `PROBE_TARGET`, `PROBE_REALITY_DEST`. Optional: `PROBE_TARGET_IP` (defaults to resolving `PROBE_TARGET`).
- Captures the baseline shape from the public dest, then the VPS shape with `--resolve`.
- Compares.
- Exits 0 on match, 1 on mismatch, 2 if either request errored (dest down / VPS unreachable).
- Prints `OK [http=X size=Y verify=Z]` on pass, `WHY: <field> baseline=A probe=B` on mismatch.

## Failure modes seen in the wild

| Symptom | Likely cause |
|---|---|
| `http_code` differs (e.g. 200 vs 403) | Reality `dest` block missing or wrong port — the panel fell back to a default Xray response |
| `ssl_verify_result` differs (0 vs nonzero) | We're presenting our own cert instead of mirroring the dest — usually a panel TLS misconfiguration |
| `size_download` differs by a huge margin | Reverse-proxy fallback isn't reaching dest (firewall outbound, DNS, dest blocked from VPS region) |
| Test exits 2 (inconclusive) | The dest itself is unreachable from where you're running — try a different controller or replace `PROBE_REALITY_DEST` |

## Known gaps

- A CDN-fronted dest (Akamai, Cloudfront) can return slightly different sizes per geo PoP. If you see flapping in the size dimension only, switch the dest to a non-CDN-fronted site (a small but real one).
- This test doesn't fingerprint the *TLS handshake* — that's scenario 02. A response that matches on the three tuple can still differ on JA3 if the handshake is wrong.
