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
- `ansible/requirements.yml` declaring `community.general` and `ansible.posix` collection dependencies
- `docs/development.md` documenting the controller-on-laptop (Path A) and controller-on-VPS (Path B) iteration loops

### Changed
- `stealth-vps` role: `min_ansible_version` lowered from `2.16` to `2.14` so Debian 12's stock `ansible-core` works without a PPA or pipx install

### Notes
- This is the initial pre-alpha scaffolding commit. No installable code yet.
- First tagged release will be `v0.1.0` once the Ansible role and cloud-init reach functional parity.

[Unreleased]: https://github.com/imprezahost/stealth-vps/compare/HEAD
