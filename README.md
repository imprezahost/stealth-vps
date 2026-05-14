# stealth-vps

> **Status: v0.5.4 (alpha).** Full stack: VLESS-Reality + Hysteria2 (port hopping), 3X-UI panel, Let's Encrypt automation, SSH/UFW/fail2ban/unattended-upgrades hardening, Spamhaus DROP via ipset, kernel tuning. **amd64 + arm64** (Oracle Ampere, AWS Graviton, Hetzner CAX). Observability: per-protocol Prometheus metrics on `:9100` (single scrape target), drop-in Grafana dashboard, Prometheus alert rules. Client walkthroughs for Android, Windows, iOS, macOS (Tahoe Hiddify-via-"Designed for iPad" path documented after pen-test). Multi-platform Molecule scenario (Debian 12 + Ubuntu 22.04 + 24.04). External contributor PRs on GitHub auto-mirror to the internal GitLab CI. Provider-agnostic Terraform module with two end-to-end worked examples — **Hetzner Cloud** (ARM cax11 default, flat €3.79/mo) and **AWS EC2** (Graviton t4g.small default, ~$12/mo + data egress). Probe-resistance test suite: 5 of 5 scenarios runnable; v0.5.3's first end-to-end pen-test validated Reality reverse-proxy fallback under all four comparators against a real deploy. See [CHANGELOG.md](CHANGELOG.md).

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

## Four ways to use it

Pick the one that matches your workflow. All four apply the same configuration.

### 1. One-shot install (`install.sh`)

For a fresh VPS where you just want it done:

```bash
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.4/scripts/install.sh | bash
```

This is a thin wrapper that bootstraps Ansible and runs `ansible-pull` against this repo. The URL is pinned to the v0.5.4 release tag, so you get exactly the code that ships in this changelog. To install a different version, swap the tag in the URL **and** pass `STEALTH_VERSION` to match:

```bash
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.4/scripts/install.sh \
  | STEALTH_VERSION=v0.5.4 bash
```

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

### 4. Terraform module (`v0.5.0`+)

Provider-agnostic — generates the cloud-init `user_data` from typed HCL inputs (SSH key, domain, release pin, Reality dest, free-form Ansible vars). Pass the output to any provider's create-server resource.

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.5.4"

  stealth_version = "v0.5.4"
  ssh_public_key  = file("~/.ssh/id_ed25519.pub")
  domain          = "vpn.example.com"
  letsencrypt_email = "ops@example.com"
}

resource "hcloud_server" "vps" {  # or aws_instance, digitalocean_droplet, vultr_instance, ...
  # ...
  user_data = module.stealth_vps_bootstrap.cloud_init
}
```

End-to-end worked examples for [Hetzner Cloud](terraform/examples/hetzner/) and [AWS EC2](terraform/examples/aws/). See [`docs/terraform.md`](docs/terraform.md) for the full reference and adapter snippets for DigitalOcean / Proxmox / Vultr.

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
| v0.4.1 | Probe-resistance scripts 02 + 03 filled in (7-feature TLS shape comparison; HTTP response-shape comparison); 4 of 5 scenarios now runnable end-to-end | shipped 2026-05-13 |
| v0.4.2 | Hotfix: replace dead `get.imprezahost.com/stealth` URL with release-pinned raw GitHub URL; bump `STEALTH_VERSION` default `v0.1.0 → v0.4.2` so one-shot install actually deploys the current stack | shipped 2026-05-13 |
| v0.4.3 | iOS + macOS walkthroughs validated end-to-end against real hardware (iPhone iOS 19 + M2 Pro Tahoe); Hiddify-MacOS.dmg flagged as broken on macOS 15+ (Network Extension not notarized) — "Designed for iPad" path documented as working alternative; troubleshooting blocks for other-VPN conflicts on both iOS and macOS; machine-assisted zh-CN README draft + translator's glossary | shipped 2026-05-14 |
| v0.5.0 | Provider-agnostic Terraform module (`terraform/modules/stealth-vps/`) + Hetzner Cloud worked example; cloud-init drift fix (`v0.1.0 → v0.5.0`); README "Three ways" → "Four" with the Terraform path | shipped 2026-05-13 |
| v0.5.1 | Byte-level **JA3 + JA3S** in `tls_fingerprint_compare.py` via stdlib `ssl.MemoryBIO` (no scapy / tlslite-ng dep); pure-stdlib TLS record + handshake parser; scenario 02 docs spell out JA3-vs-JA3S semantics + the TLS 1.3 `EncryptedExtensions` limitation | shipped 2026-05-14 |
| v0.5.2 | HTTP/2 SETTINGS-frame comparison as scenario-03 companion (`h2_settings_compare.py`); pure-stdlib HTTP/2 preface + SETTINGS parser inline; all 5 probe-resistance scenarios now have at least one runnable script | shipped 2026-05-14 |
| v0.5.3 | Split `PROBE_REALITY_PORT` from `PROBE_DEST_PORT` across all suite scripts; `--resolve → --connect-to` + portable `getent` replacement; first end-to-end pen-test against a real stealth-vps deploy (Tokyo VPS, Reality on port 43338) — TLS shape + JA3 + JA3S + HTTP/1 + HTTP/2 SETTINGS all match dest microsoft.com | shipped 2026-05-14 |
| **v0.5.4** | **AWS EC2 Terraform example** (`terraform/examples/aws/`) — second worked example alongside Hetzner; ARM Graviton + AMD support via `architecture` input; dynamic Debian 12 AMI lookup; IMDSv2-required, gp3 encrypted root; `lifecycle ignore_changes = [user_data]` to keep cloud-init from triggering instance replacement | **shipped 2026-05-14** |
| v0.5.x | DigitalOcean / Vultr / Proxmox Terraform examples, Pulumi reference | planned |
| v1.0.0 | Probe-resistance CI suite (full, with JA4 + JA4S + golden snapshots), signed releases, security audit | roadmap |

Track the [CHANGELOG](CHANGELOG.md) for what's actually shipped.

---

## Documentation

- [Architecture](docs/architecture.md) — what gets installed, how the pieces fit
- [Operations](docs/operations.md) — day-to-day: rotate creds, add users, upgrade
- [Terraform](docs/terraform.md) — provider-agnostic module + Hetzner example (v0.5.0+)
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
