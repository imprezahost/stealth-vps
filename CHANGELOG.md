# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffolding (directory layout, license, contribution workflow)
- Placeholder structure for Ansible roles, cloud-init, install script, observability, docs, tests
- `stealth-vps` role: kernel tuning task (`tasks/kernel.yml`) — loads `tcp_bbr`, renders `/etc/sysctl.d/99-stealth.conf`, asserts BBR + fq active post-apply
- `stealth-vps` role: 3X-UI panel install task (`tasks/panel.yml`) — pinned to `v2.9.4`, generates per-host random port/username/password/webBasePath, persists them in `/etc/stealth-vps/panel.state.yml`, applies via `x-ui setting`, smoke-tests the HTTP endpoint, and writes `/root/stealth-vps-credentials.txt`
- `stealth-vps` role: Reality inbound task (`tasks/xray.yml`) — generates X25519 keypair / client UUID / shortId / port once, persists in `/etc/stealth-vps/reality.state.yml`, creates the inbound on the 3X-UI panel via REST API, smoke-tests via `openssl s_client` that the served TLS cert matches the configured dest, renders the `vless://` URI into the credentials file
- `stealth-vps` role: Hysteria2 task (`tasks/hysteria.yml`) — installs apernet/hysteria pinned to `app/v2.8.2` as a standalone systemd service (the Xray bundled in 3X-UI v2.9.4 does not support the hysteria2 protocol), generates per-host auth + obfs passwords + UDP port, generates a 10-year self-signed cert (CN=bing.com), renders config with Salamander obfs + Brutal congestion + masquerade to news.ycombinator.com, smoke-tests UDP listening, renders the `hysteria2://` URI into the credentials file
- `stealth-hardening` role: SSH hardening task (`tasks/ssh.yml`) — installs a drop-in to `/etc/ssh/sshd_config.d/` that adds the stealth port (default 22550, additive while we keep port 22 open through one apply cycle), disables password auth, restricts root to key-only, pins modern KEX / ciphers / MACs / host-key algorithms, tightens auth limits + idle timeouts, and AllowUsers-restricts logins. Validates with `sshd -t -f %s` before move + a full `sshd -t` after, then restarts sshd via handler.
- `stealth-hardening` role: UFW firewall task (`tasks/ufw.yml`) — installs ufw, sets default deny-incoming + allow-outgoing, opens SSH 22550 (plus legacy 22 transitionally), opens the Reality TCP port + Hysteria2 UDP port read from `/etc/stealth-vps/{reality,hysteria}.state.yml`, keeps the 3X-UI panel port closed by default (override via `stealth_hardening_ufw_expose_panel`), and enables UFW. Also re-added `community.general` (pinned to 7.x for ansible-core 2.14 compatibility) for `community.general.ufw`.
- `stealth-hardening` role: fail2ban task (`tasks/fail2ban.yml`) — installs fail2ban, drops a working 3X-UI filter (`filter.d/3xui.conf`) that matches the panel's `WARNING - wrong username: "..." ... IP: "..."` log line with the right `datepattern` (a common upstream pitfall), renders `jail.local` with sshd + 3xui jails active and `banaction=ufw` so blocked IPs land in `ufw status`, asserts the configured jails are running.
- `stealth-hardening` role: unattended-upgrades task (`tasks/unattended-upgrades.yml`) — installs unattended-upgrades + apt-listchanges, renders `/etc/apt/apt.conf.d/20auto-upgrades` (enable timer hooks) and `/etc/apt/apt.conf.d/51stealth-vps-unattended` (security-origin filter, no auto-reboot, Package-Blacklist via `stealth_hardening_unattended_blacklist`), validates the apt config and runs `unattended-upgrade --dry-run` as a smoke test.
- `ansible/requirements.yml` declaring `community.general` and `ansible.posix` collection dependencies
- `docs/development.md` documenting the controller-on-laptop (Path A) and controller-on-VPS (Path B) iteration loops

### Changed
- `stealth-vps` role: `min_ansible_version` lowered from `2.16` to `2.14` so Debian 12's stock `ansible-core` works without a PPA or pipx install

### Notes
- This is the initial pre-alpha scaffolding commit. No installable code yet.
- First tagged release will be `v0.1.0` once the Ansible role and cloud-init reach functional parity.

[Unreleased]: https://github.com/imprezahost/stealth-vps/compare/HEAD
