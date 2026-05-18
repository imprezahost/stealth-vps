# stealth-vps

> **Status: v0.7.0 (alpha).** Full stack: VLESS-Reality + Hysteria2 (port hopping), 3X-UI panel **OR headless mode** (v0.7+), Let's Encrypt automation, SSH/UFW/fail2ban/unattended-upgrades hardening, Spamhaus DROP via ipset, kernel tuning. **amd64 + arm64** (Oracle Ampere, AWS Graviton, Hetzner CAX). Observability: per-protocol Prometheus metrics on `:9100`, drop-in Grafana dashboard, Prometheus alert rules. Client walkthroughs for Android, Windows, iOS, macOS. Multi-platform Molecule (Debian 12 + Ubuntu 22.04 + 24.04), both default + headless scenarios green. External GitHub PRs auto-mirror to internal GitLab CI. **IaC ready**: Terraform module with five worked examples (Hetzner, AWS, DigitalOcean, Vultr, Proxmox VE) + a TypeScript Pulumi reference. Probe-resistance test suite: 5/5 scenarios runnable; v0.5.3's first end-to-end pen-test validated Reality reverse-proxy fallback under all four comparators against a real deploy. **v0.7 ships headless mode**: 3X-UI optional via `panel_enabled=false`, standalone Xray-core systemd unit, Hysteria2 per-user `auth.userpass` so revoking one user doesn't break the others, `stealth_vps.reloader` Python module re-renders configs from `users.index.json` and SIGHUPs the services on every mutation, new `s-vps user add/revoke/list/show`, `s-vps reload`, `s-vps migrate from-3xui` CLI verbs. Telegram bot's HeadlessBackend wiring lands in v0.7.1; the CLI is the v0.7.0 path. See [CHANGELOG.md](CHANGELOG.md) + [docs/headless-mode.md](docs/headless-mode.md).

A reproducible toolkit to set up a privacy-focused VPS for restrictive networks. Installs VLESS-Reality + Hysteria2 behind the 3X-UI panel, with sane hardening, working fail2ban, and built-in observability. Comes with an interactive installer that you can press Enter through and an operator CLI (`s-vps`) for everything afterwards.

Designed for people who want a setup they can audit, version-pin, redeploy, and trust — not another opaque `bash <(curl ...)` script.

---

## Why another one?

The space already has good shell installers (`mack-a/v2ray-agent`, `3x-ui`, `Hiddify-Manager`). This project is **not** trying to replace them. It exists for a smaller, specific audience:

- You want **idempotent Ansible** so you can redeploy or recover predictably.
- You want **cloud-init** that works in any hypervisor without manual interaction.
- You want **fail2ban that actually works** with 3X-UI (a recurring pain point upstream).
- You want a **permissive MIT license** (not AGPL viral) so providers and operators can adopt without legal friction.
- You want **semver releases** and a changelog you can read.
- You want **first-class observability** out of the box — node\_exporter + per-protocol Prometheus metrics + a drop-in Grafana dashboard + alert rules, all on a single `:9100` scrape target (shipped in v0.3.0).
- You want an **operator CLI** (`s-vps update`, `s-vps diagnose`, `s-vps status`) so day-2 ops aren't another shell ritual.

If you'd rather paste a one-liner and move on, those other projects serve you better.

---

## What it installs

Always-on:

- **Xray-core** with VLESS-Reality (steals TLS handshake from a real site; resists active probing)
- **Hysteria2** (QUIC-based, masquerades as HTTP/3 traffic to a real site; **port-hopping over a configurable UDP range**)
- **3X-UI** panel (multi-user, traffic limits, expiry, subscription links) — in v0.7 this becomes optional via `stealth_vps_panel_enabled=false`
- **TLS**: optional **Let's Encrypt** issuance via `acme.sh` (HTTP-01 standalone) when you set `stealth_vps_domain` — Hysteria2 + 3X-UI panel both use the real cert; falls back to self-signed if unset
- **Kernel tuning**: BBR + fq qdisc, larger socket buffers, TCP Fast Open
- **Hardening**: SSH on non-default port, key-only auth, fail2ban with a *working* 3X-UI filter, UFW (deny-incoming default)
- **Reputation drop**: **Spamhaus DROP** loaded into an ipset, dropped at the top of UFW's INPUT chain, refreshed daily
- **Patching**: `unattended-upgrades` with security-origin filter and Package-Blacklist hook
- **Observability**: `prometheus-node-exporter` + per-protocol stealth-vps metrics on `:9100` (loopback by default; expose / tunnel as needed), drop-in Grafana dashboard, Prometheus alert rules
- **IPv6 dual-stack** by default

Operator tooling (v0.6+):

- **`s-vps` shell wrapper** at `/usr/local/bin/s-vps` — verbs: `update` (re-run ansible-pull at the pinned tag, preserves your original install choices), `diagnose` (post-deploy ✓/✗/⚠ health check), `status` (`systemctl is-active` summary), `version`, `help`.
- **`users.index.json`** at `/etc/stealth-vps/` — the operator's portable source-of-truth for "who is authorised", schema v1. In v0.6 the bot and CLI double-write panel API + this file; in v0.7 the index becomes authoritative.
- **Shared Python pkg** `stealth_vps` at `/usr/local/lib/stealth_vps/` — pure stdlib, no third-party deps. Bot, metrics updater, and (v0.7+) full Python CLI all import from here.

Opt-in (`STEALTH_BOT_ENABLED=true` / `STEALTH_SUBSCRIPTION_ENABLED=true`):

- **Telegram bot** running `python-telegram-bot` under a hardened systemd unit. Commands: `/start` (auto-pair on first message), `/help`, `/status`, `/diagnose`, `/creds`, `/user add|list|revoke <label>`, `/sub <label>|revoke <label>`.
- **Caddy subscription endpoint** serving `/.well-known/stealth-vps-sub/<token>` from `/var/lib/stealth-vps/subscriptions/`. Two bind modes: loopback `127.0.0.1:8443` (default, fetch via SSH tunnel) or `:443` with Let's Encrypt auto-TLS (when `STEALTH_SUBSCRIPTION_EXPOSE=true` + a domain).

---

## Four ways to use it

Pick the one that matches your workflow. All four apply the same configuration.

### 1. Interactive installer (recommended for first-time deploys)

Download then run with a TTY — you get a whiptail prompt sequence (domain optional, services checklist, bot token if you want it, summary confirm). Pressing Enter through every prompt produces a working install on the VPS's public IP.

```bash
curl -fsSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.7.0/scripts/install.sh -o install.sh
sudo bash install.sh
```

After it finishes you get an ANSI QR code for the default Reality URI, a post-deploy ✓/✗/⚠ health check, and `s-vps` installed at `/usr/local/bin/s-vps` for everything afterwards. Full prompt sequence + env-var contract documented in [`docs/installer-ux.md`](docs/installer-ux.md).

### 2. Headless one-shot (`curl | bash`)

For cloud-init / Terraform user-data / scripted deploys that don't have a TTY. Byte-compatible with v0.5.x — same `STEALTH_*` env vars.

```bash
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.7.0/scripts/install.sh | sudo bash
```

Override defaults with env vars before the pipe:

```bash
STEALTH_DOMAIN=vpn.example.com \
STEALTH_TLS_EMAIL=ops@example.com \
STEALTH_BOT_ENABLED=true \
STEALTH_BOT_TOKEN=12345:abc... \
  curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.7.0/scripts/install.sh | sudo bash
```

Full env-var list in [`docs/installer-ux.md`](docs/installer-ux.md). Re-run any time with `s-vps update` — your original choices stay pinned to `/etc/stealth-vps/installer.env`.

### 3. Ansible directly (when you control inventory)

```bash
git clone https://github.com/imprezahost/stealth-vps.git
cd stealth-vps
cp ansible/inventory/example.yml ansible/inventory/hosts.yml
# edit ansible/inventory/hosts.yml with your VPS details
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

### 4. Cloud-init / Terraform / Pulumi (IaC)

Drop [`cloud-init/stealth-vps.yaml`](cloud-init/stealth-vps.yaml) as user-data when creating the VPS. Works with Proxmox, any cloud that supports cloud-init, and any modern hypervisor.

For provider-agnostic Terraform, [`terraform/modules/stealth-vps/`](terraform/modules/stealth-vps/) generates the cloud-init `user_data` from typed HCL inputs (SSH key, domain, release pin, Reality dest, free-form Ansible vars):

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.7.0"

  stealth_version   = "v0.7.0"
  ssh_public_key    = file("~/.ssh/id_ed25519.pub")
  domain            = "vpn.example.com"
  letsencrypt_email = "ops@example.com"
}

resource "hcloud_server" "vps" {  # or aws_instance, digitalocean_droplet, vultr_instance, ...
  # ...
  user_data = module.stealth_vps_bootstrap.cloud_init
}
```

End-to-end worked examples for [Hetzner Cloud](terraform/examples/hetzner/), [AWS EC2](terraform/examples/aws/), [DigitalOcean](terraform/examples/digitalocean/), [Vultr](terraform/examples/vultr/), and [Proxmox VE](terraform/examples/proxmox/) (self-hosted). Pulumi TypeScript port lives in [`pulumi/`](pulumi/) with a Hetzner example as the canonical reference. See [`docs/terraform.md`](docs/terraform.md) for the full Terraform reference.

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
| v0.5.4 | AWS EC2 Terraform example — second worked example alongside Hetzner; ARM Graviton + AMD via `architecture` input; dynamic Debian 12 AMI lookup; IMDSv2-required, gp3 encrypted root | shipped 2026-05-14 |
| v0.5.5 | DigitalOcean Terraform example — third worked example; `digitalocean_droplet` + `digitalocean_firewall` + `digitalocean_ssh_key`; amd64-only on DO | shipped 2026-05-14 |
| v0.5.6 | Vultr Terraform example — fourth worked example; `vultr_instance` + `vultr_firewall_group` + per-IP-family firewall rules; amd64-only on Vultr | shipped 2026-05-14 |
| v0.5.7 | Proxmox VE Terraform example — fifth worked example; `proxmox_vm_qemu` clones a Debian 12 cloud-init template; `local_file` writes user_data as a snippet | shipped 2026-05-14 |
| v0.5.8 | Pulumi TypeScript reference — pure-TS port of the Terraform module's cloud-init builder; Hetzner example wired through `@pulumi/hcloud` | shipped 2026-05-14 |
| v0.5.9 | **v0.6 prerequisites**: `scripts/release.sh` (one-shot version bumper across 21 self-pinned files), refactor `xray.yml` into `reality_state.yml` (panel-independent) + `reality_push_3xui.yml` (panel-specific) so v0.7 headless mode drops in cleanly, `docs/internal/roadmap-v0.6-v0.7.md` (Caminho C full-UX plan: zero-domain default, terminal QR, DNS pre-flight, health-check, bot DM, `s-vps update`) | shipped 2026-05-15 |
| v0.6.0 | **Caminho C full UX** (11 sub-sprints in one release): interactive whiptail installer with zero-domain fast path, terminal QR for the Reality URI (qrencode), DNS pre-flight before LE, post-deploy ✓/✗/⚠ health check, error-wrap for known failure patterns, `s-vps` operator CLI wrapper (`update`/`diagnose`/`status`), opt-in Telegram bot (`/user` CRUD, `/sub` rotation, pair-on-first-`/start`), opt-in Caddy subscription endpoint, shared `stealth_vps` Python pkg, `users.index.json` schema as the v0.7 migration anchor | shipped 2026-05-15 |
| v0.6.1 | **Bug-fix release** driven by the Tokyo VPS smoke test of v0.6.0: panel-scheme auto-detection (`https` first, `http` fallback) via a shared `stealth_vps_panel_scheme` fact; `installer.env` + `bot.env` now `\| bool \| ternary` so `-e key=false` actually persists as `false`; health-check reads ports from state files instead of hardcoding 443/8443; shellcheck / yamllint regressions cleared. CI back online via a new project-scoped Docker runner on the Tokyo VPS. | shipped 2026-05-15 |
| v0.6.2 | **Docs + tests + CI consolidation**. 85-test pytest suite for the `stealth_vps` Python pkg; `docs/operations.md` rewritten 71→290 lines; `docs/architecture.md` + `ansible/inventory/example.yml` documented for v0.6; ansible-lint bumped `basic → safety` (7 fixes); markdown-lint config + 15 fixes (every gating lint green); Molecule CI green for the first time since v0.6.0 (was Alpine pyexpat-broken). `os.rename → os.replace` for Windows cross-platform `/sub revoke`. Container-tolerant guards on kernel modprobe / SSH wait / users_index seed so Molecule converges in Docker sandboxes. Two old `Co-Authored-By: Claude` trailers stripped from history. | shipped 2026-05-15 |
| v0.6.3 | **README install-URL bump fix** that v0.6.2 forgot: `release.sh` now has a partial-bump pass that auto-rewrites install URLs + Terraform `?ref=` lines + `stealth_version` var in `README.md` / `README.zh-CN.md` on each release. Roadmap-row historical text stays untouched (lines that don't match the partial-bump regexes). Real contact channels in `SECURITY.md` (the previous `security@imprezahost.com` + PGP placeholder + separate-infra-channel were fictional). GitHub repo deleted + recreated to drop dangling commits referencing `@claude` after a `git filter-branch` rewrite. | shipped 2026-05-15 |
| v0.6.4 | **Hysteria port-wait bug fix**, surfaced by the Tokyo VPS smoke test of v0.6.3. The "Wait for hysteria UDP port to be listening" task piped `ss -lunH \| awk '{print $5}' \| grep ':PORT$'` — but `$5` is the *peer* address column on listening UDP sockets (always `*:*`), not the local address. The grep never matched, but for years `head -1` swallowed the empty input and returned rc=0, masking the bug. v0.6.2's safety-profile lint sweep added `set -o pipefail` to the task, which correctly propagated the real grep failure and turned the latent bug into a hard install fail. Fix: use `ss -lunH 'sport = :PORT' \| grep -q .` — kernel-side filter, no column-position dependency. Plus zh-CN README cleanup of two junk lines left by the broken-sed-delimiter bug in v0.6.3's `release.sh` partial-bump pass. | shipped 2026-05-18 |
| **v0.7.0** | **Headless mode** — 3X-UI optional via `panel_enabled=false`. Standalone Xray-core (`/usr/local/bin/xray`, hardened systemd unit). Hysteria2 per-user `auth.userpass` map so revoking one user doesn't break the others. `stealth_vps.reloader` Python module renders `/etc/xray/config.json` + `/etc/hysteria/config.yaml` from `users.index.json` and SIGHUPs the services on every mutation. New `s-vps user add/revoke/list/show`, `s-vps reload`, `s-vps migrate from-3xui` CLI verbs. 67 new pytest cases over four MRs (`!41`/`!42`/`!43`/`!44`); 169 total. Per-user mode defaults to `not panel_enabled` so existing panel installs upgrade with shared-password semantics intact. Operator-facing docs: [docs/headless-mode.md](docs/headless-mode.md) + [docs/migration-3xui-to-headless.md](docs/migration-3xui-to-headless.md). | **shipped 2026-05-18** |
| v1.0.0 | Probe-resistance CI suite (full, with JA4 + JA4S + golden snapshots), signed releases, security audit | roadmap |

Track the [CHANGELOG](CHANGELOG.md) for what's actually shipped.

---

## Documentation

- [Architecture](docs/architecture.md) — what gets installed, how the pieces fit
- [Operations](docs/operations.md) — day-to-day: rotate creds, add users, upgrade
- [Installer UX contract](docs/installer-ux.md) — every prompt, every env var, every exit code (v0.6+)
- [Terraform](docs/terraform.md) — provider-agnostic module + worked examples (v0.5.0+)
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
