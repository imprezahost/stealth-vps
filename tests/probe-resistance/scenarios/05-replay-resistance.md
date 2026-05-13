# 05 — Replay resistance

## What we're testing

Capture a legitimate Reality handshake from a real client. Replay the captured packets from a different source IP. The replay must **fail** in a way that doesn't reveal "this server speaks Reality" — i.e. it should look identical to scenario 03 (active probe with no key).

A failure here means: the server accepts a replayed handshake from an unauthorised source, OR the server rejects it with a tell that wouldn't appear when probing dest directly.

## Why it matters

Reality's threat model includes the GFW capturing handshakes as they pass through Chinese ISPs and replaying them shortly after, from inside Chinese networks. The protocol design includes time-keyed nonces in the `shortID` derivation so a replay even seconds later fails authentication.

We don't re-test the protocol — we test that *our deployment* of it inherits that property. Misconfigurations (clock drift > 30s, NTP not running, wrong `shortID`s in the panel) can disable replay resistance without disabling normal client connections.

## Threat model anchor

| Replay technique | Caught here? |
|---|---|
| Capture + replay seconds later, same source IP | yes |
| Capture + replay minutes later, different source IP | yes |
| Capture + selective replay of just ClientHello | partial — server should refuse the application_data phase |
| Capture + replay with packet munging (e.g. update IP/TCP checksums) | manual, hard to automate cleanly |

## Test recipe (manual, v1.0 automation)

```bash
# On a client machine that already connects successfully:
sudo tcpdump -i any -w /tmp/reality-handshake.pcap host "$VPS_IP" and port 443 &
DUMP=$!
# Make a fresh client connection (any traffic — the handshake is at the start).
curl --proxy "vless://...your-vless-link..." https://example.com >/dev/null
kill $DUMP
wait $DUMP

# Now from a *different* machine, replay just the client-side packets:
sudo tcpreplay --intf1=eth0 --topspeed --pktlen \
  --srcip=<NEW_SOURCE_IP> /tmp/reality-handshake.pcap

# Observe with a parallel tcpdump on the VPS:
#   - If the VPS accepts and forwards traffic from the new source, replay-resistance is broken.
#   - If the VPS reverse-proxies to dest (returns dest HTML), replay-resistance is good.
#   - If the VPS resets or behaves oddly compared to dest, that's a tell — fix Reality config.
```

### Implementation

`scripts/` — none in v0.4.0. This stays manual because:

1. Capturing a handshake needs a client-side packet capture during a *real* connection, which can't be scripted from the controller cleanly.
2. `tcpreplay` needs raw-socket privilege and a NIC that matches the captured one.
3. Verifying "behaves like dest" needs scenario 03's logic, applied to the replay flow.

**v1.0 (planned):**
- Combine: capture a handshake during scenario 02's run (since we're already doing TLS to the VPS); save the raw packets to `/tmp/reality-handshake.pcap`.
- Replay via `scapy.sendpfast` from a second IP on the runner (network namespace trick).
- Assert the response shape matches dest, not "accept-and-forward".

## Failure modes seen in the wild

| Symptom | Likely cause |
|---|---|
| Replay is accepted (proxied traffic flows) | Server clock is wildly off (`timedatectl status` shows >30s drift). NTP not running. |
| Replay returns a 502 | Reality is correctly rejecting it but the upstream-dest reverse-proxy is in a degraded state. Re-check dest reachability. |
| Replay returns a Reality-specific reset / error | Reality misconfiguration — `xtls-rprx-vision` flow is off, or a stale `shortID` is in the panel and matches the captured one. |
| Replay works for 30s post-capture, then stops | Approximately correct — Reality uses ~30-second windows. Confirm by repeating with a 60s delay before replay. |

## Known gaps

- This test does not exercise *passive* replay (where the adversary doesn't replay packets but just observes timing/sizing). That's a deeper traffic-analysis question we don't take a position on here.
- A replay from the *same* source IP (rare, mostly a TLS-bug class) is harder to distinguish from a legitimate retransmit — Reality handles it but our verification depends on bytes-on-the-wire diff, not on server-side logs.
- Automation in v1.0 will need raw-socket capability in CI, which means running on a privileged runner. Cost / complexity trade-off is open.
