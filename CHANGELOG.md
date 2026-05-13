# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `stealth-vps` role: Reality (3X-UI panel API) and Hysteria2 (`trafficStats` JSON) traffic counters surfaced as Prometheus metrics. A `/usr/local/sbin/stealth-vps-metrics-update.py` runs every `stealth_vps_metrics_refresh_interval_sec` seconds (systemd `.timer`), pulls both APIs, and writes a `.prom` file into `/var/lib/stealth-vps/metrics/`. node_exporter is restarted with `--collector.textfile.directory` pointing at that dir, so Prometheus has one scrape target (`:9100`) covering host + protocols. `stealth_vps_metrics_enabled` (default true) toggles the whole thing.
- Hysteria2 config now exposes its `trafficStats` JSON API on `stealth_vps_hysteria_traffic_stats_listen` (default `127.0.0.1:9101`, loopback only) so the updater can read it.

### Planned (still in v0.3.0)
- stealth-vps Grafana dashboard JSON consuming the new metrics
- Alert rules: cert expiry, login flood, bandwidth spike, fail2ban ban rate
- Pen-tested iOS + macOS client walkthroughs (Shadowrocket, Hiddify, V2Box)
- Multi-platform Molecule matrix (Ubuntu 22.04 / 24.04 alongside Debian 12)
- Source-IP filter variant for `stealth_vps_observability_listen` exposure
- zh-CN README rewrite by a native speaker

## [0.2.0] - 2026-05-13

Second tagged release. Real TLS, hardening with reputation-based dropping, port hopping, end-user client walkthroughs, baseline observability, and a Molecule scenario gating idempotency regressions in CI.

### Added
- **Let's Encrypt automation** (`stealth-vps` role, `tasks/tls.yml`) — when `stealth_vps_domain` is set, the role issues a cert via `acme.sh --standalone --httpport 80`, persists it under `/etc/stealth-vps/tls/`, and registers a renewal `--reloadcmd` that restarts hysteria-server + x-ui. Hysteria2 + 3X-UI panel both pick up the LE cert; the Hysteria2 URI in `credentials.txt` drops `insecure=1` and the panel URLs become `https://`. With `stealth_vps_domain` unset, v0.1.0 self-signed behaviour is preserved.
- **`stealth-hardening` role: ufw task** gains the `stealth_hardening_ufw_acme_http_challenge` toggle that opens port 80/tcp for HTTP-01 issuance + renewals.
- **Spamhaus DROP via ipset** (`stealth-hardening` role, `tasks/spamhaus.yml`) — installs ipset, ships `/usr/local/sbin/stealth-vps-update-spamhaus.sh` (atomic swap of the named set), a oneshot systemd service (`Before=ufw.service` so the set exists before UFW reloads), and a daily timer with `RandomizedDelaySec=4h`. Injects one `-A ufw-before-input -m set --match-set spamhaus-drop src -j DROP` line into `/etc/ufw/before.rules` via blockinfile. Spamhaus merged EDROP into DROP in early 2026; we only consume the one URL.
- **Hysteria2 port hopping** (`stealth-vps` role, `tasks/hysteria.yml`) — opt-in via `stealth_vps_hysteria_port_hopping=true`. Injects a `*nat` block into `/etc/ufw/before.rules` with a `PREROUTING REDIRECT` rule that bounces UDP traffic in `[port_hopping_min, _max]` (defaults 20000-50000) to the actual Hysteria2 listener. The client URI in `credentials.txt` gains the `,min-max` suffix.
- **Observability bootstrap** (`stealth-vps` role, `tasks/observability.yml`) — installs `prometheus-node-exporter`, overrides its systemd unit to bind to `stealth_vps_observability_listen` (default `127.0.0.1:9100`). Operators pull via SSH tunnel from a central Prometheus, or override the listen + UFW source filter when ready to expose. Smoke-tests `/metrics`.
- **Client setup walkthroughs**: `docs/client-setup/android.md` (v2rayNG + NekoBox), `docs/client-setup/windows.md` (NekoBox + v2rayN, both proxy and TUN modes). `ios.md` and `macos.md` promoted from "placeholder" to working quick-start tables; pen-tested walkthrough for those lands in v0.3.0.
- **Molecule scenario** (`tests/molecule/default/`) — Debian 12 + systemd container, converges `site.yml` with service-bound tasks toggled off, `verify.yml` asserts kernel sysctl + SSH drop-in artifacts, Molecule's built-in `idempotence` step gates regressions. `.gitlab-ci.yml` runs `molecule test` on every MR / `main` push (allow_failure during dind stabilisation).

### Changed
- `stealth_hardening_spamhaus_drop` + `_edrop` split replaced by a single `stealth_hardening_spamhaus_enabled` toggle.
- `ansible-lint` CI job installs the role's collection requirements first; ansible-core pin lowered to `>=2.14,<2.18` to match the project's real support window.
- Permission chain on `/etc/stealth-vps/`: parent dir is now `0711 root:root` (traverse-only) so `hysteria` can reach `tls/`; cert files are `0644 fullchain` + `0640 privkey`, with the private key chgrped to `hysteria`.
- `panel.yml` cert binding now drives the restart explicitly (not via a flush_handlers + notify chain, which behaved unreliably under ansible-core 2.14 inside an `include_tasks`).
- `xray.yml` 3X-UI API calls use `https://` when TLS is on, with `validate_certs: false` on loopback (cert CN won't match `127.0.0.1`).

### Fixed
- Idempotency cleanup (`tls.yml` + `hysteria.yml`): tls.yml owns *mode* on cert files (no owner/group), hysteria.yml owns *group=hysteria* only; install-cert gated on a per-domain marker file. Second apply now reports 0 changed across the full play.

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

[Unreleased]: https://github.com/imprezahost/stealth-vps/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.2.0
[0.1.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.1.0
