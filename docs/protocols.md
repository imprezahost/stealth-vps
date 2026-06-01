# Protocols (v0.11.0+)

stealth-vps ships **VLESS-Reality** and **Hysteria2** by default — they cover ~95% of operator-reported network conditions. v0.11 adds five opt-in protocols for the remaining ~5% (hard-DPI national networks, Cloudflare-fronted-only networks, legacy clients, cipher-family diversity).

Every protocol is **off by default** — flip a flag per host (or per fleet node) to enable it. This doc is the operator runbook; the design rationale (ADRs, scope decisions) lives in [`internal/roadmap-v0.11-protocols.md`](internal/roadmap-v0.11-protocols.md).

## At a glance

| Protocol | Transport | Use case | Fronted? | URI in sub bundle |
|----------|-----------|----------|----------|-------------------|
| VLESS-Reality | TCP (stolen TLS) | default; best probe-resistance | no | `vless://…` |
| Hysteria2 | QUIC/UDP | throughput, different fail-mode | no | `hysteria2://…` |
| **XHTTP** (v0.11) | HTTP/2/3 via CDN | Cloudflare-fronted networks | Caddy + CDN | `vless://…?type=xhttp` |
| **VMess+WS** (v0.11) | WebSocket+TLS | legacy clients (pre-2024 Hiddify/V2RayN) | Caddy | `vmess://…` |
| **Shadowsocks-2022** (v0.11) | TCP+UDP (AEAD) | cipher diversity vs Reality | no | `ss://…` |
| **Trojan-Go** (v0.11) | TCP+TLS | Trojan-only client populations | no | `trojan://…` |
| **WireGuard** (v0.11) | UDP | hard-DPI fallback ("boring VPN") | no | `.conf` (no URI) |

All five are additive: enabling SS-2022 doesn't touch Reality. A user's subscription bundle grows by one entry per enabled protocol (WireGuard excepted — it has no URI; see below).

## Schema

`users.index.json` is on **schema v3** as of v0.11. The bump is auto-applied on load: any v2 file gains four nullable fields (`ss2022_psk`, `wireguard_pubkey`, `wireguard_client_ip`, `trojan_password`) and rewrites to v3 on the next mutation. Operators upgrading v0.10 → v0.11 don't migrate anything by hand.

A per-protocol URI renders for a user **only when both** (a) the protocol is enabled on the host AND (b) the user has the matching credential. `s-vps user add` mints the credential automatically when the protocol is enabled; existing users get `null` until you `s-vps user rotate` them on an enabled host.

---

## XHTTP (VLESS-over-XHTTP)

For networks where direct connections are throttled but Cloudflare-fronted HTTP passes freely.

```yaml
stealth_vps_xhttp_enabled: true
stealth_vps_xhttp_path: /.well-known/xhttp-stream   # default
stealth_vps_subscription_enabled: true              # Caddy must be present
stealth_vps_subscription_expose: true               # public :443 site
stealth_vps_domain: vpn.example.com                 # required for fronting
```

How it works: Xray binds a loopback port; Caddy reverse-proxies the public `https://<domain>/<xhttp_path>` to it. Put Cloudflare (or any CDN) in front of `<domain>` and the censor sees only encrypted CF traffic. XHTTP reuses the user's Reality UUID — no separate per-user credential.

**Requires** `subscription_expose: true` + a domain (the fronting needs the public TLS site). On a loopback-only subscription host, XHTTP fronting isn't rendered.

---

## VMess+WebSocket+TLS

Legacy-compat for clients that predate Reality (older Hiddify, V2RayN, V2Box). Worse fingerprint than Reality (the TLS handshake is real, not stolen) but works out-of-the-box on a wide client base.

```yaml
stealth_vps_vmess_ws_enabled: true
stealth_vps_vmess_ws_path: /.well-known/vmess-ws    # default
stealth_vps_subscription_enabled: true
stealth_vps_subscription_expose: true
stealth_vps_domain: vpn.example.com
```

Same Caddy-fronting model as XHTTP, same Reality-UUID reuse. The `vmess://` URI is a base64-encoded JSON blob (the v2rayN scheme) — clients decode it on import.

---

## Shadowsocks-2022 (SIP022)

A different cipher family from Reality. Useful when a network has learned to fingerprint Reality's TLS shape but hasn't trained against SS-2022's AEAD. Mature client support (sing-box, Shadowrocket, every modern Xray client).

```yaml
stealth_vps_ss2022_enabled: true
# 2022-blake3-aes-128-gcm (default, fastest) | -aes-256-gcm | -chacha20-poly1305
stealth_vps_ss2022_method: "2022-blake3-aes-128-gcm"
```

Unlike XHTTP/VMess, SS-2022 binds a public port directly (no Caddy fronting) — both TCP and UDP. Each user gets a per-user PSK; the URI pre-concatenates the server PSK + user PSK so the client gets one paste-and-go credential.

```bash
$ sudo s-vps user add alice          # auto-mints alice's ss2022_psk
$ sudo s-vps user add bob --ss2022-psk <base64>   # or supply one (migration)
```

The PSK byte-length matches the cipher (16 bytes for aes-128-gcm, 32 for the others) — the role handles this automatically.

---

## Trojan-Go

For client populations locked to the Trojan protocol. **Upstream is in maintenance mode** (last release ~2023) — prefer SS-2022 or XHTTP for new deployments; this exists for operators who can't move their users off Trojan clients yet. **Deprecated target: 2027.**

```yaml
stealth_vps_trojan_go_enabled: true
stealth_vps_trojan_go_version: "v0.10.6"   # pinned upstream release
```

Trojan-Go runs as its own systemd unit (`trojan-go.service`), TLS-terminated. With a domain set it uses the Let's Encrypt cert; without one it reuses the Hysteria2 self-signed cert (clients import with `allowInsecure`). Each user gets a per-user password, auto-minted on `s-vps user add` (or `--trojan-password` to supply one).

---

## WireGuard

The ultimate fallback for hard-DPI networks. WG's UDP shape is well-known and most networks **allow** it (treating it as a corporate VPN) even when they kill probe-resistant transports.

```yaml
stealth_vps_wireguard_enabled: true
stealth_vps_wg_subnet: "10.99.0.0/24"   # default; override if it collides
stealth_vps_wg_client_dns: "1.1.1.1"
```

WireGuard has **no URI** — clients import a `.conf` file. The server mints each user a keypair on `s-vps user add` (the private key is stashed at `/var/lib/stealth-vps/wireguard/<label>.privkey`), allocates the next-free `/24` IP, and adds them as a peer. Hand the user their config:

```bash
$ sudo s-vps user add alice            # mints alice's WG keypair + IP
$ sudo s-vps user wg-config alice       # prints the importable .conf
[Interface]
PrivateKey = <alice-private-key>
Address = 10.99.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = <server-public-key>
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
```

The operator copies that to the user (paste into the WG app, or `qrencode` it). The private key is the user's secret — deliver it once.

**Single-node only.** WireGuard is excluded from the multi-node subscription bundle: each fleet node has its own server, which would require per-node client keypairs. In a fleet, run WG on one node and hand out configs per-node manually if you need it.

---

## Multi-node fleets (v0.10 + v0.11)

On a [multi-node fleet](multi-node.md), each data node independently enables whichever protocols it terminates. `s-vps fleet add` discovers them by reading each node's state files; the subscription bundle then carries per-node URIs for exactly the protocols each node serves.

Example — a heterogeneous fleet:

| Node | Protocols | Bundle contribution per user |
|------|-----------|------------------------------|
| `tokyo-1` | Reality + Hysteria2 + SS-2022 | 3 URIs |
| `amsterdam-1` | Reality + Hysteria2 | 2 URIs |
| `cdn-1` | XHTTP only (behind Cloudflare) | 1 URI |

A user's bundle is the union (6 URIs here). When the control mints a user, it auto-generates the per-user SS-2022 PSK / Trojan password if **any** fleet node runs that protocol — even though the control itself doesn't terminate it. The SS-2022 *server* PSK is per-node; the *user* PSK is shared across nodes (one credential, N nodes).

Clients (Hiddify Next, V2Box, NekoBox) probe all URIs in the bundle and pick the lowest-latency one that negotiates — automatic cross-protocol, cross-region failover.

---

## Health metrics

When the [health exporter](operations.md#health-check-prometheus-exporter-v090) is enabled, each protocol adds gauges:

```text
stealth_vps_unit_active{unit="trojan-go.service"} 1
stealth_vps_unit_active{unit="wg-quick@stealth.service"} 1
stealth_vps_ss2022_port_listening{port="8543"} 1
stealth_vps_xhttp_port_listening{port="18543"} 1
stealth_vps_vmess_ws_port_listening{port="19543"} 1
stealth_vps_trojan_go_port_listening{port="4443"} 1
```

A gauge of `-1` means the protocol isn't enabled on that host; `0` means enabled but the port isn't accepting connections; `1` means listening. WireGuard is UDP (no TCP-connect probe) so it's covered by the `wg-quick@stealth` unit-active gauge only.
