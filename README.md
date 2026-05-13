# stealth-vps

> **Status: v0.4.1 (alpha).** Full stack: VLESS-Reality + Hysteria2 (port hopping), 3X-UI panel, Let's Encrypt automation, SSH/UFW/fail2ban/unattended-upgrades hardening, Spamhaus DROP via ipset, kernel tuning. **amd64 + arm64** (Oracle Ampere, AWS Graviton, Hetzner CAX). Observability: per-protocol Prometheus metrics on `:9100` (single scrape target), drop-in Grafana dashboard, Prometheus alert rules. Client walkthroughs for Android, Windows, iOS, macOS. Multi-platform Molecule scenario (Debian 12 + Ubuntu 22.04 + 24.04). External contributor PRs on GitHub auto-mirror to the internal GitLab CI; `stealth-vps/gitlab-ci` status reports back. Probe-resistance test suite: **4 of 5 scenarios runnable** (HTTPS direct probe, TLS shape comparison, active probe, port-scan baseline) under `tests/probe-resistance/`. See [CHANGELOG.md](CHANGELOG.md).

A reproducible toolkit to set up a privacy-focused VPS for restrictive networks. Installs VLESS-Reality + Hysteria2 behind the 3X-UI panel, with sane hardening, working fail2ban, and built-in observability.

Designed for people who want a setup they can audit, version-pin, redeploy, and trust — not another opaque `bash <(curl ...)` script.

---

## Why another one?

The space already has good shell installers (`mack-a/v2ray-agent`, `3x-ui`, `Hiddify-Manager`). This project is **not** trying to replace them. It exists for a smaller, specific audience:

- You want **idempotent Ansible** so you can redeploy or recover predictably.
- You want **cloud-init** that works in any hypervisor without manual interaction.
- You want **fail2ban that actually works** with 3X-UI (a recurring pain point upstream).
- You want a **permissive MIT license** (not AGPL viral) so providers and operators can adopt without legal friction.
- You want **semver releases** and a changelog you can read.
- A **Prometheus + Grafana** observability bundle is on the v0.2.0 roadmap.

If you'd rather paste a one-liner and move on, those other projects serve you better.

---

## What it installs

- **Xray-core** with VLESS-Reality (steals TLS handshake from a real site; resists active probing)
- **Hysteria2** (QUIC-based, masquerades as HTTP/3 traffic to a real site; **port-hopping over a configurable UDP range**)
- **3X-UI** panel (multi-user, traffic limits, expiry, subscription links)
- **TLS**: optional **Let's Encrypt** issuance via `acme.sh` (HTTP-01 standalone) when you set `stealth_vps_domain` — Hysteria2 + 3X-UI panel both use the real cert; falls back to self-signed if unset
- **Kernel tuning**: BBR + fq qdisc, larger socket buffers, TCP Fast Open
- **Hardening**: SSH on non-default port, key-only auth, fail2ban with a *working* 3X-UI filter, UFW (deny-incoming default)
- **Reputation drop**: **Spamhaus DROP** loaded into an ipset, dropped at the top of UFW's INPUT chain, refreshed daily
- **Patching**: `unattended-upgrades` with security-origin filter and Package-Blacklist hook
- **Observability**: `prometheus-node-exporter` baseline (loopback only by default; expose / tunnel as needed)
- **IPv6 dual-stack** by default

---

## Three ways to use it

Pick the one that matches your workflow. All three apply the same configuration.

### 1. One-shot install (`install.sh`)

For a fresh VPS where you just want it done:

```bash
curl -sSL https://get.imprezahost.com/stealth | bash
```

This is a thin wrapper that bootstraps Ansible and runs `ansible-pull` against this repo. Pinned to a release version.

### 2. Ansible (recommended for repeatable use)

```bash
git clone https://github.com/imprezahost/stealth-vps.git
cd stealth-vps
cp ansible/inventory/example.yml ansible/inventory/hosts.yml
# edit ansible/inventory/hosts.yml with your VPS details
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

### 3. Cloud-init (for hypervisors)

Drop `cloud-init/stealth-vps.yaml` as user-data when creating the VPS. Works with Proxmox, any cloud that supports cloud-init, and any modern hypervisor.

---

## Where to run this

Any Debian 12 or Ubuntu 22.04+ VPS will work. For best performance from mainland China, choose a provider with direct peering to China Telecom (ideally CN2 GIA), China Unicom, and ChinaNet.

### Sponsored infrastructure

This project is built and maintained by **[Impreza Host](https://imprezahost.com)**. If you want a VPS that runs this template well, we run a Los Angeles fleet with direct peering to:

- **CN routes**: China Telecom (CN2), China Unicom, ChinaNet
- **Tier-1 backbone**: CenturyLink, Cogent, GTT, NTT

We also offer offshore privacy-focused locations in Iceland, Switzerland, Netherlands, Russia, and Romania for clients who need jurisdictions outside the usual surveillance alliances.

- Pay in USDT-TRC20 (no card required)
- No KYC for standard plans
- Open-source ethos: this repo is one of the ways we contribute back

Sponsorship doesn't change the code — the same template runs on any provider's VPS. We just happen to operate infrastructure that fits the use case.

---

## Project status & roadmap

| Version | Scope | Status |
|---|---|---|
| v0.1.0 | Ansible role (kernel + panel + Reality + Hysteria2), hardening role, cloud-init, `install.sh` | shipped 2026-05-13 |
| v0.2.0 | Let's Encrypt automation, Spamhaus DROP, Hysteria2 port hopping, Android + Windows walkthroughs, `node_exporter` baseline, Molecule scenario | shipped 2026-05-13 |
| v0.3.0 | Per-protocol Prometheus metrics + Grafana dashboard + alert rules, multi-platform Molecule matrix (Debian 12 + Ubuntu 22.04/24.04), source-IP filter for `:9100`, iOS + macOS full walkthroughs | shipped 2026-05-13 |
| v0.4.0 | arm64 packaging (Oracle Ampere / Graviton / Hetzner CAX), reverse-mirror automation (GitHub PR → GitLab CI → GitHub commit status), probe-resistance test suite scaffolding (5 scenarios, 2 runnable scripts) | shipped 2026-05-13 |
| **v0.4.1** | Probe-resistance scripts 02 + 03 filled in (7-feature TLS shape comparison; HTTP response-shape comparison); 4 of 5 scenarios now runnable end-to-end | **shipped 2026-05-13** |
| v0.4.2 | Pen-tested iOS + macOS validation pass, zh-CN README rewrite, GitLab shell-executor runner fix | planned |
| v0.5.0 | True byte-level JA3/JA4 (scapy/tlslite-ng + golden snapshots), HTTP/2 frame comparison, Terraform module (provider-agnostic), Pulumi reference | planned |
| v1.0.0 | Probe-resistance CI suite (full), signed releases, security audit | roadmap |

Track the [CHANGELOG](CHANGELOG.md) for what's actually shipped.

---

## Documentation

- [Architecture](docs/architecture.md) — what gets installed, how the pieces fit
- [Operations](docs/operations.md) — day-to-day: rotate creds, add users, upgrade
- Client setup guides: [Android](docs/client-setup/android.md) · [iOS](docs/client-setup/ios.md) · [Windows](docs/client-setup/windows.md) · [macOS](docs/client-setup/macos.md)

中文文档: [README.zh-CN.md](README.zh-CN.md)

---

## Contributing

PRs and issues are welcome on GitHub. Development happens in a private GitLab; the GitHub repository is a release mirror. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

For security disclosures, see [SECURITY.md](SECURITY.md).

---

## License

MIT — see [LICENSE](LICENSE).

---

## Credits

This project depends on excellent upstream work:

- [XTLS/Xray-core](https://github.com/XTLS/Xray-core) — Reality protocol
- [apernet/hysteria](https://github.com/apernet/hysteria) — Hysteria2
- [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui) — panel
- The privacy/anti-censorship community on `nodeseek`, `v2ex`, `linux.do`, and `gfw.report`
