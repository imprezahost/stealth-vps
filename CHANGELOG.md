# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Probe-resistance scripts 02 + 03 are now runnable** (filled in from the v0.4.0 scaffold):
  - `tls_fingerprint_compare.py` — 7-feature TLS shape comparison using stdlib `ssl` + a single `openssl x509 -inform DER -text` shell-out: protocol version, chosen cipher, selected ALPN, peer cert subject CN, SAN list (sorted), issuer CN, signature + public-key algorithms. Cert is captured to a temp file (avoids Windows openssl-stdin quirks). One `WHY:` line per diverging feature on fail.
  - `active_probe.py` — HTTP/1.1 response-shape comparison via stdlib `http.client`: status code, lower-cased header keys (minus a VARIABLE_HEADERS set for `date` / `set-cookie` / `cf-ray` / `x-amz-cf-id` / etc.), and a body-size bucket. Forces ALPN `http/1.1` so the response parses with stdlib; HTTP/2 frame check stays v1.0.
  - Both scripts smoke-tested positively (target=`www.microsoft.com`, dest=`www.microsoft.com` → exit 0 with one-line OK) and negatively (target=`www.apple.com`, dest=`www.microsoft.com` → exit 1, every divergent feature reported).
  - `PROBE_VERBOSE=1` dumps full shapes for triage.
- Honest naming: the script does **not** claim to compute JA3/JA4. Those are byte-level fingerprints over raw `ClientHello` / `ServerHello`; Python's stdlib abstracts those bytes away. Scenario doc 02 now documents this explicitly and points to v1.0 plug-in territory (scapy / tlslite-ng + golden snapshots).
- Scenario docs `02-tls-fingerprint.md` and `03-active-probe-no-key.md` rewritten — the "v0.5 (planned)" sections become "v0.4.1 (runnable)" with the actual implementation details, and v1.0 picks up only what stays deferred.

### Planned (v0.4.1 remaining)
- Pen-tested iOS + macOS validation pass against the v0.3.0 walkthroughs (deferred from v0.4.0 — needs iOS + macOS hardware in the QA rotation)
- zh-CN README rewrite by a native speaker (deferred from v0.4.0 — needs reviewer)

## [0.4.0] - 2026-05-13

Fourth tagged release. Three new pieces of infrastructure for the project itself: external contributors get a real CI path via the **reverse-mirror automation**, the **probe-resistance test suite** lands in scaffolded form so the threat model is auditable and the script contracts are locked, and **arm64 hosts are first-class** — Oracle Ampere free tier, AWS Graviton, and Hetzner CAX all run the same playbook now. Two roadmap items (pen-tested iOS/macOS validation, zh-CN README rewrite) are deferred to v0.4.1 because they depend on externals (hardware in the QA rotation, native-speaker reviewer) that aren't ready.

### Added
- **arm64 support** (`ansible_facts.architecture == 'aarch64'` is now accepted):
  - New `stealth_vps_arch_map` in `defaults/main.yml` maps `uname -m` → GOARCH-style suffix (`x86_64 → amd64`, `aarch64 → arm64`). Every upstream binary URL (`Xray-core` bundled in 3X-UI, Hysteria2, the in-panel xray binary path) now derives from this map via the new `stealth_vps_arch` fact set in `tasks/main.yml`.
  - The three per-component `x86_64`-only `assert`s in `panel.yml` / `hysteria.yml` are replaced by a single central assert against `stealth_vps_arch_map.keys()` in `tasks/main.yml`. To try an unvalidated architecture (armv7, 386, riscv64), extend the map and rerun — no other role changes required.
  - `docs/operations.md` gains a "Running on arm64" section with the validated provider matrix (Oracle Ampere free tier, AWS Graviton, Hetzner CAX) and per-component arm64 caveats.
- **Probe-resistance test suite scaffolding** (`tests/probe-resistance/`):
  - `README.md` with explicit threat model — which probe classes we test against, which we deliberately leave out of scope.
  - 5 numbered scenario docs under `scenarios/` covering HTTPS direct probe (01), TLS JA3/JA4 fingerprint (02), active probe with no Reality key (03), port-scan baseline (04), and replay-resistance (05). Each doc states what we test, why it matters, the threat-model anchor, and the failure modes we've seen in the wild.
  - 2 runnable scripts: `https_direct_probe.sh` (compares HTTP shape between dest and our VPS) and `port_scan_baseline.sh` (nmap against an allow-list driven by the role's defaults).
  - 2 scaffolded scripts: `tls_fingerprint_compare.py` and `active_probe.py` — full env-var and exit-code contract locked, dest-side baseline already exercised; body lands in v0.5.
  - 1 manual scenario (replay-resistance), documented with a recipe; automation in v1.0.
  - `requirements.txt` (stdlib-only at v0.4.0; TLS-fingerprint lib shortlist in comments).
  - GitLab CI `probe-resistance` job — manual-trigger only, requires `PROBE_TARGET` + `PROBE_REALITY_DEST` set per-pipeline. Does not gate normal pipelines (probe-resistance needs a real VPS, not a Docker runner).
- **Reverse-mirror automation** (`.github/workflows/reverse-mirror.yml` + `.gitlab-ci.yml` `report` stage):
  - External PRs on the GitHub mirror are auto-pushed to internal GitLab as `ext/pr-<N>` and a tracking Merge Request is opened via the GitLab API. The workflow runs under `pull_request_target` but never checks out the untrusted PR code — only `git fetch` of `pull/N/head` + `git push` to GitLab, so secrets stay safe.
  - Force-pushes to the PR refresh the same MR (idempotent on `MR_IID`).
  - The GitLab MR pipeline reports `pending` / `success` / `failure` back to the upstream PR commit as a status check named `stealth-vps/gitlab-ci` (using the GitHub Commit Statuses API).
  - `workflow.rules` in `.gitlab-ci.yml` suppresses the empty push-pipeline that would otherwise fire on `ext/pr-*` branches — only the MR-pipeline runs CI.
  - Secrets / variables required documented in `docs/development.md § External contributor flow`.
- **`CONTRIBUTING.md`** updated to describe the mechanized flow (replaces the v0.3 "we re-apply manually internally" wording).

### Fixed
- Two pre-existing `yamllint` line-length errors on `ansible/roles/stealth-vps/tasks/{hysteria,tls}.yml` that snuck in during v0.2/v0.3 — wrapped in `# yamllint disable rule:line-length` because the offending lines are single-token shell or iptables-restore directives that can't be split.

### Deferred to v0.4.1
- Pen-tested iOS + macOS validation pass against the v0.3.0 walkthroughs (needs hardware in the QA rotation; walkthroughs themselves remain accurate to published app behaviour).
- zh-CN README rewrite by a native speaker (English version is current; zh-CN landed as a placeholder in v0.2 and still needs a native review pass).

## [0.3.0] - 2026-05-13

Third tagged release. Observability stack made useful: per-protocol traffic counters, ready-to-import Grafana dashboard, and a small but practical set of Prometheus alert rules. Multi-platform Molecule matrix. Source-IP filter for the exporter port. Real iOS + macOS walkthroughs replace the v0.2.0 quick-start tables.

### Added
- **Protocol metrics → Prometheus textfile** (`stealth-vps-metrics-update.py`):
  - `stealth_vps_inbound_{up,down}_bytes{inbound_id, remark, protocol, port}` and `stealth_vps_inbound_enabled` from the 3X-UI panel REST API.
  - `stealth_vps_client_{up,down}_bytes{... + client_email}` from the panel's per-client counters.
  - `stealth_vps_hysteria_{tx,rx}_bytes{client_id}` + `stealth_vps_hysteria_online_clients` from Hysteria2's `trafficStats` JSON API (the role enables `trafficStats.listen: 127.0.0.1:9101` automatically).
  - `stealth_vps_cert_expiry_seconds{cert="le-fullchain"}` parsed from `openssl x509 -enddate` (returns `-1` when no LE cert is configured).
  - `stealth_vps_fail2ban_{currently,total}_{banned,failed}{jail}` parsed from `fail2ban-client status`.
  - Health gauges: `stealth_vps_{panel,hysteria,cert,fail2ban}_scrape_error` + `stealth_vps_last_scrape_timestamp`.
  Updater is a systemd `.timer` + oneshot service (every `stealth_vps_metrics_refresh_interval_sec`, default 30s), node_exporter exposes the textfile on the same `:9100`. Single scrape target covers host + protocols.
- **Grafana dashboard** `observability/grafana/dashboards/stealth-vps-overview.json` — importable, paired with Grafana.com 1860 (Node Exporter Full). Panels: health stats, Reality inbound up/down per port, top-N per-client traffic (Reality + Hysteria2), online clients. Datasource picker + multi-select Host templating.
- **Prometheus alert rules** `observability/prometheus/alerts/stealth-vps.rules.yml` — cert expiry (warning 7d / critical 24h), scrape errors per upstream, scrape staleness, fail2ban ban-rate spike, currently-banned threshold, inbound traffic spike (3× 1h baseline AND > 1 MB/s by inbound). Drop into central Prometheus `rule_files:` and reload.
- **Multi-platform Molecule** — `tests/molecule/default/molecule.yml` now converges Debian 12 + Ubuntu 22.04 + Ubuntu 24.04 in one scenario. Same converge + idempotence + verify gates run against all three. `group_vars: all` replaces per-platform `host_vars` to keep opt-outs consistent.
- **Source-IP filter** for the node_exporter port — `stealth_vps_observability_allow_from` (list of CIDRs). When non-empty AND the listen address is non-loopback, `tasks/ufw.yml` adds one `ufw allow from <cidr> to any port <port> proto tcp` per entry. Empty (default) keeps the port loopback-only.
- **iOS walkthrough** `docs/client-setup/ios.md` — full guides for Hiddify (recommended free), Shadowrocket (paid de-facto), Streisand (paid sing-box). Add-profile flows, Reality field-check, Hysteria2 port-hopping + insecure handling, troubleshooting (VPN permission, DNS bypass, cellular UDP, TLS-after-LE, battery), multi-user sharing via subscription URLs.
- **macOS walkthrough** `docs/client-setup/macos.md` — full guides for Hiddify Desktop, V2Box, NekoBox (nekoray), Shadowrocket on Apple Silicon. Install / import / connect, TUN vs system proxy, the Apple-private-DNS leak pitfall, wintun/utun gotcha, Reality-vs-Hysteria2 battery guidance.

### Known limitations
- iOS and macOS walkthroughs are written from app docs + server-side validation; **per-screen pen-tested validation lands in v0.4.0** when hardware enters the QA rotation.
- Updater script's panel-API path needs the panel running with a known `panel.state.yml`; if you regenerate the panel password by hand outside Ansible, re-apply `--tags panel` first.

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

[Unreleased]: https://github.com/imprezahost/stealth-vps/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.3.0
[0.2.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.2.0
[0.1.0]: https://github.com/imprezahost/stealth-vps/releases/tag/v0.1.0
