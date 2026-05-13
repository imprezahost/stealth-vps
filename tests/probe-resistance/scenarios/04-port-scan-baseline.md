# 04 — Port-scan baseline

## What we're testing

A port scan of our VPS from outside must show:

- TCP open: `22550` (SSH, non-default), `443` (Reality + panel via different SNIs), maybe `80` if you enabled HTTP-01 LE on demand.
- UDP open: nothing recognisable as a "VPN port". The Hysteria2 listener uses port hopping over `stealth_vps_hysteria_port_hopping_min..max`; nmap should see *some* random ports respond as part of the hop range, which looks like a UDP service of unknown shape (good).
- Closed everywhere else.

A failure here means:

- An extra port is open (forgot to close a panel port, exposed node_exporter, mistake in UFW rule).
- An expected port is *closed* (UFW rule didn't apply, service didn't start).
- A service banner leaks the underlying software (`Server: Xray/1.8.13` on the wrong port — happens if you accidentally bind the panel on a non-Reality port).

## Why it matters

Port scans are the cheapest probe class. A scanner spends one SYN per port and gets a "what services run here" map. If the map doesn't look like the dest IP's map, we're easy to bucket. The fix is "only the ports you need, no informative banners on any of them" — this test enforces that.

## Threat model anchor

| Scan technique | Caught here? |
|---|---|
| TCP SYN scan top-1000 | yes |
| UDP top-100 scan | yes (specifically: confirms no `openvpn` / `wireguard` / `ipsec` banner) |
| Service-version detection (`nmap -sV`) | yes (no service must leak a version) |
| OS fingerprint (`nmap -O`) | partial — outside scope; we accept dest-OS-leakage as background noise |

## Test recipe

```bash
# 1. TCP scan top-1000
nmap -Pn -sT --top-ports 1000 "$PROBE_TARGET" > /tmp/tcp.txt

# 2. UDP scan top-100 (slower; we accept this)
nmap -Pn -sU --top-ports 100 "$PROBE_TARGET" > /tmp/udp.txt

# 3. Compare against allow-list from inventory
#    Allow-list comes from ansible/inventory/example.yml:
#      - stealth_vps_ssh_port (default 22550)
#      - 443 (Reality + panel)
#      - 80 if stealth_vps_letsencrypt_http_challenge: true
#      - hysteria hop range
EXPECTED_TCP="22550 443"
EXPECTED_UDP_RANGE="20000-50000"  # default hop range

# 4. Anything outside the allow-list = fail.
```

### Implementation

`scripts/port_scan_baseline.sh`:

- Reads `EXPECTED_TCP_PORTS` (space-separated) and `EXPECTED_UDP_RANGE` (e.g. `20000-50000`) from env, with defaults matching the role defaults.
- Runs nmap if installed; falls back to a `nc -zv` loop if not (slower, narrower).
- Parses nmap output; flags any port shown as `open` that isn't in the allow-list.
- Flags any expected port that's `filtered` or `closed`.
- Exits 0 on match, 1 on mismatch (with a `WHY:` line per offending port), 2 if nmap isn't available.

## Failure modes seen in the wild

| Symptom | Likely cause |
|---|---|
| Port 9100 open from outside | `stealth_vps_observability_listen` wasn't reset to loopback after testing — or the UFW source-IP filter was applied but a `0.0.0.0/0` slipped into `stealth_vps_observability_allow_from`. |
| Port 80 open when LE not in use | `acme.sh --standalone` left a listener up after issuance; should auto-stop. |
| Port 22 open (in addition to 22550) | First-deploy SSH transition didn't complete; re-run `--tags ssh` after confirming key-based 22550 access. |
| TCP/443 closed | Xray or panel didn't start. Check `systemctl status x-ui xray`. |
| UDP hop range invisible | Hysteria2 didn't start, or the `nat PREROUTING REDIRECT` rule from `ufw before.rules` didn't apply. Re-run `--tags hysteria,ufw`. |

## Known gaps

- A stateful UDP probe (sending a real Hysteria2 ClientHello to a port in the hop range) would distinguish "UDP hop range" from "UDP service of any kind". We don't do that here because that's what scenario 03 does at the protocol level.
- `nmap` from one source IP is not the same as `nmap` from a coordinated scanner pool. Ports filtered by fail2ban on the runner's IP will look open from elsewhere. Run from a fresh IP.
- The dest-OS-leakage gap (TCP option ordering, ICMP behaviour) is real but defensible — we're not pretending to be a CDN PoP, just to be benign.
