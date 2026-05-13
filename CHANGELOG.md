# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `stealth-vps` role: TLS / ACME task (`tasks/tls.yml`) — when `stealth_vps_domain` is set, issues a Let's Encrypt cert via `acme.sh --standalone --httpport 80`, persists it in `/etc/stealth-vps/tls/`, registers an auto-renewal `--reloadcmd` that restarts hysteria-server + x-ui. When the domain is unset, the role keeps the v0.1.0 self-signed cert path.
- `stealth-vps` role: panel task now binds the Let's Encrypt cert to the 3X-UI panel via `x-ui cert -webCert -webCertKey` when a domain is configured (panel serves HTTPS).
- `stealth-vps` role: hysteria task picks the right cert + SNI at apply time (Let's Encrypt fullchain when `stealth_vps_domain` is set; bing.com self-signed otherwise).
- `stealth-hardening` role: ufw task gains the `stealth_hardening_ufw_acme_http_challenge` toggle that opens port 80/tcp for HTTP-01 challenges and renewals.
- Operator credentials file now emits `https://` panel URLs and drops `insecure=1` from the Hysteria2 URI when TLS is real.

### Added
- `stealth-hardening` role: Spamhaus DROP ipset task (`tasks/spamhaus.yml`) — installs ipset, drops a `stealth-vps-update-spamhaus.sh` script, a oneshot systemd service (runs `Before=ufw.service` so the set exists before UFW reloads), and a daily systemd timer with jitter. Injects a single `-A ufw-before-input -m set --match-set spamhaus-drop src -j DROP` rule into `/etc/ufw/before.rules` via blockinfile. Atomic swap pattern (build new ipset, swap, destroy old) keeps the firewall from ever observing an empty set. Spamhaus consolidated EDROP into DROP in early 2026, so we only consume the one URL.

### Added
- `stealth-vps` role: Hysteria2 port hopping (`tasks/hysteria.yml`) — opt-in via `stealth_vps_hysteria_port_hopping=true`. Injects a `*nat` block into `/etc/ufw/before.rules` with a `PREROUTING REDIRECT` rule that bounces UDP traffic in `[stealth_vps_hysteria_port_hopping_min, _max]` (defaults 20000-50000) to the actual Hysteria2 listener port. The client URI in `credentials.txt` gets the `,min-max` suffix the apernet/hysteria client understands.

### Changed
- `stealth_hardening_spamhaus_drop` + `stealth_hardening_spamhaus_edrop` split is replaced by a single `stealth_hardening_spamhaus_enabled` toggle (default `true`).

### Added
- `docs/client-setup/android.md` — full walkthrough for v2rayNG (Reality) and NekoBox for Android (Reality + Hysteria2), including verification steps, port-hopping URI handling, and a troubleshooting section for the common breakages (Xiaomi VPN permission, DNS leaks, Hysteria2 on mobile data).
- `docs/client-setup/windows.md` — full walkthrough for NekoBox (nekoray) and v2rayN, both proxy and TUN modes, with wintun handling and common error fixes.
- `docs/client-setup/ios.md` and `docs/client-setup/macos.md` — promoted from "placeholder" to working quick-start tables of the recommended clients (Shadowrocket, Streisand, Hiddify on iOS; Hiddify, V2Box, NekoBox, Shadowrocket on macOS), with notes on TUN behaviour and App Store regional availability. Full pen-tested walkthrough lands in v0.3.0.

### Added
- `tests/molecule/default/` — working Molecule scenario. Boots a Debian 12 + systemd container, converges `site.yml` with the service-bound tasks toggled off (panel/xray/hysteria/UFW/fail2ban/spamhaus/unattended-upgrades — those need a real network stack), and `verify.yml` asserts the kernel sysctl drop-in + SSH hardening drop-in are in place. The `idempotence` step of `molecule test` gates regressions where a 2nd converge would mark anything changed.
- `.gitlab-ci.yml` `molecule` job runs the scenario on every MR / `main` push. `allow_failure: true` for now because docker-in-docker self-hosted runners are flaky; the manual VPS validation in `docs/development.md` is still the authoritative gate.

### Changed
- `ansible-lint` CI job now installs the role's collection requirements first (so the role tree resolves) and lowers the pinned ansible-core to `>=2.14,<2.18` to match the project's actual support window.

### Planned (still in v0.2.0)
- Basic observability bundle (Prometheus exporter + Grafana JSON)
- zh-CN README rewrite by a native speaker

## [0.1.0] - 2026-05-13

First tagged release. Working stealth-VPS stack with hardened SSH, firewall, automatic security patches, and fail2ban; installable via `install.sh`, `ansible-playbook`, or `cloud-init`. Validated end-to-end on a Tokyo Debian 12.9 KVM VPS.

### Added
- Project scaffolding (directory layout, license, contribution workflow, CI lint matrix)
- `stealth-vps` role:
  - `tasks/kernel.yml` — loads `tcp_bbr`, renders `/etc/sysctl.d/99-stealth.conf` (BBR + fq + TCP buffers + Fast Open + MTU probing + notsent_lowat + file-max), asserts BBR + fq active post-apply.
  - `tasks/panel.yml` — 3X-UI v2.9.4 install. Per-host random port/username/password/webBasePath generated once and persisted in `/etc/stealth-vps/panel.state.yml` (chmod 600). Applies via `x-ui setting`, smoke-tests the HTTP endpoint.
  - `tasks/xray.yml` — Reality inbound (VLESS + XTLS Vision) created via 3X-UI REST API. Generates X25519 keypair / client UUID / shortId / port once and persists in `reality.state.yml`. Smoke-tests via `openssl s_client` that the served TLS cert matches the configured dest (default `www.microsoft.com:443`).
  - `tasks/hysteria.yml` — apernet/hysteria `app/v2.8.2` as a standalone systemd service (Xray bundled in 3X-UI v2.9.4 doesn't support hysteria2 — running it as an inbound makes Xray crash-loop). Salamander obfs + Brutal congestion control + masquerade to `https://news.ycombinator.com/`, 10-year self-signed TLS cert (CN=bing.com).
- `stealth-hardening` role:
  - `tasks/ssh.yml` — drop-in to `/etc/ssh/sshd_config.d/` that adds port 22550, kills password auth, restricts root to key-only, pins modern KEX / ciphers / MACs / host-key algorithms, AllowUsers-restricts logins. Legacy port 22 toggled off via `stealth_hardening_ssh_legacy_port_enabled=false`.
  - `tasks/ufw.yml` — default deny-incoming + allow-outgoing. Surgical opens for SSH + Reality + Hysteria2 (ports read from `/etc/stealth-vps/{reality,hysteria}.state.yml`). Panel port closed unless `stealth_hardening_ufw_expose_panel=true`.
  - `tasks/fail2ban.yml` — `sshd` and `3xui` jails with `banaction=ufw`. Ships a *working* 3X-UI filter (correct datepattern + quote handling; the canonical upstream example is broken).
  - `tasks/unattended-upgrades.yml` — security-origin-only patches, no auto-reboot, Package-Blacklist hook, `unattended-upgrade --dry-run` smoke test.
- Operator credentials file (`/root/stealth-vps-credentials.txt`, chmod 600) with `vless://` + `hysteria2://` URIs ready to paste into v2rayNG / NekoBox / sing-box / Hiddify.
- Tooling: `ansible.cfg`, `.yamllint`, `.ansible-lint`, `.gitattributes` (force-LF), `ansible/requirements.yml` (pinned `ansible.posix < 2.0` + `community.general < 8.0` for ansible-core 2.14 compatibility).
- `docs/development.md` — controller-on-laptop (Path A) and controller-on-VPS (Path B) iteration loops.
- `install.sh` (one-shot via `ansible-pull`) and `cloud-init/stealth-vps.yaml` (hypervisor bootstrap).
- GitLab CI lint matrix (shellcheck, yamllint, ansible-lint, markdownlint) + tag-only mirror to GitHub.

### Known limitations
- TLS for Hysteria2 + 3X-UI panel is self-signed; clients connect to Hysteria2 with `insecure=1`. Let's Encrypt automation lands in v0.2.0.
- `stealth-hardening` has no Spamhaus / IP-blocklist task; the legacy `hosts.deny` approach is bypassed by modern `sshd` and isn't worth implementing. Modern ipset+UFW replacement lands in v0.2.0.
- Hysteria2 port hopping not yet wired (needs UFW/nftables DNAT rules — v0.2.0).
- amd64 only. arm64/armv7/armv6/386 fail-fast on the architecture assert.
- Client setup docs are placeholders; full walkthroughs land in v0.2.0.
- `observability/` directory is scaffolded but empty; Prometheus/Grafana bundle lands in v0.2.0.

[Unreleased]: https://github.com/imprezahost/stealth-vps/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.1.0
