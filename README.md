# stealth-vps

> **Status: pre-alpha (v0.1.0-dev).** Public structure only. Not yet usable. First tagged release expected soon.

A reproducible toolkit to set up a privacy-focused VPS for restrictive networks. Installs VLESS-Reality + Hysteria2 behind the 3X-UI panel, with sane hardening, working fail2ban, and built-in observability.

Designed for people who want a setup they can audit, version-pin, redeploy, and trust — not another opaque `bash <(curl ...)` script.

---

## Why another one?

The space already has good shell installers (`mack-a/v2ray-agent`, `3x-ui`, `Hiddify-Manager`). This project is **not** trying to replace them. It exists for a smaller, specific audience:

- You want **idempotent Ansible** so you can redeploy or recover predictably.
- You want **cloud-init** that works in any hypervisor without manual interaction.
- You want **fail2ban that actually works** with 3X-UI (a recurring pain point upstream).
- You want **Prometheus + Grafana** dashboards out of the box.
- You want a **permissive MIT license** (not AGPL viral) so providers and operators can adopt without legal friction.
- You want **semver releases** and a changelog you can read.

If you'd rather paste a one-liner and move on, those other projects serve you better.

---

## What it installs

- **Xray-core** with VLESS-Reality (steals TLS handshake from a real site; resists active probing)
- **Hysteria2** (QUIC-based, masquerades as HTTP/3, port-hopping enabled)
- **3X-UI** panel (multi-user, traffic limits, expiry, subscription links)
- **Kernel tuning**: BBR + fq qdisc, larger socket buffers, TCP Fast Open
- **Hardening**: SSH on non-default port, key-only auth, fail2ban with working 3X-UI rules, UFW, unattended-upgrades, Spamhaus DROP/EDROP via hosts.deny
- **Observability**: Prometheus exporter for Xray/Hysteria2/system, Grafana dashboard, optional alert hooks
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
| **v0.1.0** | Ansible role, cloud-init, install.sh, basic observability, EN/zh-CN README, Android/Windows client docs | in development |
| v0.2.0 | iOS/macOS client docs, additional Grafana dashboards, Discord/Telegram alert webhooks | planned |
| v0.3.0 | Terraform module (provider-agnostic), Pulumi reference | planned |
| v1.0.0 | Probe-resistance CI suite, signed releases, security audit | roadmap |

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
