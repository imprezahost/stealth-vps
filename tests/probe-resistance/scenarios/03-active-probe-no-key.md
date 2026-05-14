# 03 — Active probe with no Reality key

## What we're testing

An adversary that completes a TLS handshake with us but has **no valid Reality `shortID` + `publicKey`** must see exactly the same post-handshake behaviour as connecting to `dest` directly. Specifically:

1. HTTP/1.1 GET to `/` returns dest's homepage (status, headers, body).
2. HTTP/2 ALPN negotiates and dest-shaped streams behave normally.
3. TLS keepalive / `application_data` records have dest-shaped sizes.
4. Closing the connection (`close_notify`) matches dest's behaviour.

A failure here means: a probe gets *into* the handshake, but our reverse-proxy fallback is incomplete — somewhere downstream we leak proxy behaviour after the TLS layer.

## Why it matters

JA3/JA4 (scenario 02) catches handshake-layer leaks. This scenario catches *post-handshake* leaks, which is where most Reality implementations have historically broken:

- Reality versions that buffered the first application-data record (response was 50ms slower than dest — a timing tell).
- Server that proxied HTTP but didn't pass through HTTP/2 frames cleanly (h2 ping-pong differs from dest's).
- Server that dropped `Server:` header from dest's response (dest says `Server: Microsoft-IIS/10.0`, we said nothing — header-set diff).

## Threat model anchor

| GFW probe technique | Caught here? |
|---|---|
| Full HTTPS request after handshake, check body / headers | yes |
| Header-set diff against dest | yes |
| HTTP/2 SETTINGS frame fingerprint | yes |
| Response-timing oracle (compare RTT to handshake RTT) | partial — needs statistical sample, scenario 05 territory |

## Test recipe

```python
# 1. Open TLS to dest:443, SNI=dest. Send 'GET / HTTP/1.1\r\nHost: dest\r\n\r\n'.
#    Capture response status line, header set (keys, no values), body length bucket.
baseline = http_probe(dest, port=443, sni=dest)

# 2. Open TLS to vps_ip:443, SNI=dest. Same request, no Reality key.
#    The server must reverse-proxy through to dest, so we should see dest's response.
probe = http_probe(vps_ip, port=443, sni=dest)

# 3. Compare:
assert baseline.status == probe.status
assert set(baseline.headers) == set(probe.headers)
assert baseline.body_bucket == probe.body_bucket  # 1KB / 10KB / 100KB / 1MB / >
```

We compare header *keys* (not values), because legitimate dest responses vary on:
- `Date:` (always)
- `Set-Cookie:` (some dests)
- `CF-Ray:` / `X-Amz-Cf-Id:` (CDN-fronted dests)

But the *set* of headers a CDN returns is extremely stable. A 7-header response from microsoft.com vs an 11-header response from our VPS is a tell, even if the body matches.

### Implementation

`scripts/active_probe.py`:

**v0.4.1 (runnable):**

- Opens a TLS connection via stdlib `ssl.SSLContext`, force-negotiates ALPN `http/1.1` (so `http.client.HTTPResponse` parses the response — h2 would need a third-party h2 lib), then issues `GET /` with `Host: $dest`, `Connection: close`.
- Captures status code, lower-cased header keys (minus the VARIABLE_HEADERS set: `date`, `set-cookie`, CDN trace headers like `cf-ray` / `x-amz-cf-id`, etc.), and a body-size bucket via `bisect_right([1024, 10240, 102400, 1048576], len(body))`.
- Compares status, header-set, and body-bucket. Exits 0 on collision, 1 on divergence with one `WHY:` per field, 2 on inconclusive.
- `PROBE_VERBOSE=1` dumps full status + header set + body length for both sides.

The h1-only forcing is deliberate: we lose the ability to inspect HTTP/2 frame structure here, but get a stable stdlib-only probe. The h2 frame check is v1.0 territory (needs `h2` lib or raw frame parsing).

**v1.0 (planned):**

- HTTP/2 path: open via ALPN `h2`, read first SETTINGS frame, compare the SETTINGS parameter set against dest's via the `h2` lib.
- Connection-close pattern: graceful `close_notify` vs RST vs FIN — captured via raw socket and compared.
- Add a no-SNI variant for the same `GET /` (current probe always sets SNI = dest).

## Failure modes (expected once implemented)

| Difference | Likely cause |
|---|---|
| `probe.status` is 502/504 | Outbound to dest is blocked from the VPS (firewall, CN→US route filtering, dest geofencing the VPS region) |
| `probe.status` is 403 | Reality `dest` config doesn't match the SNI we're probing with |
| Header set differs by `Server:` only | Reverse-proxy is rewriting `Server:` to something else — Reality bug or panel post-processing |
| Header set differs by 3+ entries | We're not actually reverse-proxying, just returning a static "fake dest" page (older Xray builds did this) |
| Body bucket differs by 2+ tiers | Probably the dest's response is cached differently per region; switch dest |

## Known gaps

- Some dests rotate their pages on a few-hour cadence (microsoft.com homepage A/B tests). A tiny size diff is normal; the bucketing absorbs that.
- This test runs over IPv4 by default. Reality with IPv6 dest reachability is identical in principle but worth running separately if you've configured a v6 dest.
- A truly motivated adversary will *also* probe with random URLs, malformed HTTP, and weird ALPN values. Those are scenario-05 territory.
- All suite scripts (including this one's HTTP/2 companion below) assume **dest and probe listen on the same port** — `PROBE_REALITY_PORT`, default 443. If Reality is on a non-443 port on your VPS (e.g. 43338), the baseline-to-dest leg fails because nothing on the dest is listening on 43338. Folding `PROBE_REALITY_PORT` apart from `PROBE_DEST_PORT` is a v0.5.3 follow-up; for now, the workaround is to test against a VPS that runs Reality on 443.

## HTTP/2 sub-scenario — `scripts/h2_settings_compare.py` (v0.5.2)

Companion script that captures the server's first `SETTINGS` frame over a real HTTP/2 connection and compares dest vs VPS. Pure stdlib — no `h2` library or `hyper` dependency.

### What it does

1. Opens TLS with `ALPN = ["h2"]` (not the more permissive `h2, http/1.1` of `active_probe.py`).
2. Refuses to proceed if the server didn't select `h2` (exit 2 — most dests do speak h2, but not all).
3. Sends the HTTP/2 connection preface (`PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n` — exactly 24 bytes per RFC 7540 §3.5).
4. Sends an empty `SETTINGS` frame from us (length=0, type=0x4, flags=0).
5. Reads up to 20 frames from the server, looking for the first `SETTINGS` frame **without the ACK flag** — that's the server's actual SETTINGS, not the one acknowledging ours.
6. Parses the SETTINGS payload — list of (uint16 identifier, uint32 value) pairs per RFC 7540 §6.5.1.
7. Returns a dict mapping identifier → value. Compares dest's vs VPS's.

### What divergence catches

Reality's reverse-proxy fallback must hand the dest's `SETTINGS` frame through verbatim. If Xray is terminating h2 itself (a Reality misconfiguration), Xray's Go-based h2 stack (`golang.org/x/net/http2`) picks different defaults from whatever the dest uses:

| Setting | Akamai dest typical | Go x/net/http2 default | Catches? |
|---|---|---|---|
| `HEADER_TABLE_SIZE` | 4096 | not sent (Go uses 4096 implicitly) | yes — missing key |
| `INITIAL_WINDOW_SIZE` | 65535 | 1048576 | yes — value diff |
| `MAX_FRAME_SIZE` | 16384 | not sent | yes |
| `MAX_HEADER_LIST_SIZE` | 32768 | 65536 | yes |

A Reality fallback that returns the *dest's* SETTINGS will match. A "fake fallback" that just lets Xray's h2 server handle the connection will diverge on 3-4 settings.

### Smoke-tested 2026-05-14

| Probe | Result |
|---|---|
| `target=dest=www.microsoft.com` | OK — settings collide (same Akamai backend) |
| `target=www.apple.com`, `dest=www.microsoft.com` | OK — both behind Akamai with the same h2 config |
| `target=www.google.com`, `dest=www.microsoft.com` | FAIL with 4 WHY: lines — Akamai SETTINGS ≠ Google SETTINGS, exactly what would happen if Reality fallback were leaking a non-dest h2 stack |

### Known limitations

- Same single-port limitation as the rest of the suite (see Known gaps above).
- A dest behind a CDN (Akamai, Cloudflare, Fastly) shares h2 SETTINGS with every other site on that CDN — collision tells you "your VPS is acting like *some* host on this CDN", not "specifically this dest". The cert subject_cn check in scenario 02 is still the discriminator that binds the response to *this* dest.
- v0.5.2 doesn't yet parse PRIORITY / WINDOW_UPDATE / GOAWAY for additional signal. Those land in v1.0 alongside golden snapshots.
