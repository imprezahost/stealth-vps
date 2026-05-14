# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned (v0.5.3 — next mechanical sprint)
- Split `PROBE_REALITY_PORT` (port on the VPS being probed) from `PROBE_DEST_PORT` (port on the real dest) across all four scenario scripts. Mechanical refactor; unblocks testing against VPSes that run Reality on a non-443 port.

### Planned (v0.5.x — later sprints, autonomous)
- AWS / DigitalOcean / Vultr / Proxmox Terraform examples.
- Pulumi reference.

## [0.5.2] - 2026-05-14

Ninth tagged release. Sole new feature: HTTP/2 `SETTINGS`-frame comparison as scenario 03 companion (`h2_settings_compare.py`). Pure-stdlib HTTP/2 preface + SETTINGS frame parser inline — no `h2` / `hyper` dependency added; `requirements.txt` stays stdlib-only. Scenario 03 now covers both HTTP/1 (via `active_probe.py`) and HTTP/2 (via the new script). 5 of 5 probe-resistance scenarios have at least one runnable script.

### Added
- **HTTP/2 SETTINGS-frame comparison** (`tests/probe-resistance/scripts/h2_settings_compare.py`, scenario 03 companion). Pure-stdlib script that opens TLS with `ALPN=h2`, sends the HTTP/2 connection preface (`PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`) and an empty `SETTINGS` frame from us, then parses the server's first SETTINGS frame inline — list of (uint16 identifier, uint32 value) pairs per RFC 7540 §6.5.1. Compares dest vs VPS. **No `h2` or `hyper` dependency added** — `requirements.txt` stays stdlib-only.
  - Smoke-tested positive (target=dest=`www.microsoft.com` → SETTINGS collide on same Akamai backend), negative against same CDN (target=`www.apple.com`, dest=`www.microsoft.com` → SETTINGS still match because both are behind Akamai — a documented limitation), negative across CDNs (target=`www.google.com`, dest=`www.microsoft.com` → 4 `WHY:` lines: `HEADER_TABLE_SIZE` missing, `INITIAL_WINDOW_SIZE` 65535 vs 1048576, `MAX_FRAME_SIZE` missing, `MAX_HEADER_LIST_SIZE` 32768 vs 65536 — exactly the divergence a broken Reality fallback letting Xray's Go-based h2 stack terminate would produce).
- `scenarios/03-active-probe-no-key.md` gains an "HTTP/2 sub-scenario" section explaining what `h2_settings_compare.py` catches, including a table comparing Akamai's typical SETTINGS vs Go `x/net/http2` defaults — the divergence pattern that a misconfigured Reality would show.
- GitLab CI `probe-resistance` job now runs the new script in sequence with 01 + 02 + 03 + 04. 03 + 03h2 are wrapped in `|| [ "$?" -eq 2 ]` so an inconclusive (h2-not-supported dest, etc.) doesn't fail the manual pipeline.

### Fixed
- **Self-pinning bumped to v0.5.2 across all entry points** — `scripts/install.sh` URL + `STEALTH_VERSION` default, `cloud-init/stealth-vps.yaml` `ansible-pull -C` arg, `terraform/modules/stealth-vps` `stealth_version` default, the Hetzner example, and every doc example all now reference `v0.5.2`. Same invariant as v0.4.2 onwards: fetching at the v0.5.2 tag deploys v0.5.2.

### Known limitation surfaced
- All suite scripts (HTTPS direct probe, TLS shape, active probe HTTP/1, HTTP/2 SETTINGS) share a single `PROBE_REALITY_PORT` env var between dest and probe. If your Reality listener runs on a non-443 port (e.g. 43338 in the Tokyo test VPS), the baseline-to-dest leg fails because nothing on the dest is listening on that port. Splitting `PROBE_REALITY_PORT` from `PROBE_DEST_PORT` is queued for v0.5.3; until then, test against a VPS that runs Reality on 443.

### Deferred to v0.5.x (later sprints)
- JA4 + JA4S (now scheduled for v1.0 alongside golden snapshots — needs cross-validation against `ja4-python`).
- AWS / DigitalOcean / Vultr / Proxmox Terraform examples.
- Pulumi reference.

### Planned (still blocked on externals)
- **zh-CN native-speaker review** on the v0.4.3 draft.
- **GitLab shell-executor runner fix** (`apt-get: Permission denied`).
- **Remaining client pen-tests**: Shadowrocket / Streisand on iOS; Shadowrocket / V2Box / NekoBox on macOS.

## [0.4.3] - 2026-05-14

Eighth tagged release. Two of the three v0.4.3 backlog items that had been blocked since v0.4.0 finally land: **iOS + macOS walkthroughs validated end-to-end** against a real Tokyo VPS on real hardware (iPhone + M2 Pro on macOS Tahoe), and a **machine-assisted zh-CN README draft** with a translator's glossary replaces the v0.1.0 "pre-alpha" placeholder. Native-speaker review on the zh-CN draft, GitLab runner fix, and pen-test of the remaining clients (Shadowrocket / Streisand / V2Box / NekoBox) carry forward.

### Added
- **iOS + macOS walkthroughs validated end-to-end** against a real Tokyo VPS (deployed at `v0.5.1`). Hardware: iPhone running iOS 19, M2 Pro MacBook running macOS Tahoe. Real-world findings folded back into the docs:
  - `docs/client-setup/ios.md` — new troubleshooting section on **other-VPN conflict** (NordLayer / NordVPN / Surfshark / WARP / etc. block Hiddify silently with `errno = 61` `127.0.0.1:<port> connection refused` because iOS allows only one active VPN config). Reproduced once with NordLayer installed (even when its app was closed); removing it solved the issue instantly. New note that an IPv6 in `ifconfig.me` is normal output when the VPS is dual-stack — not a leak.
  - `docs/client-setup/macos.md` — **two new sharp edges on macOS Tahoe (15+)** documented:
    1. **`right-click → Open` for unidentified-developer apps was removed** — the dialog now offers only "Move to Trash". Workaround documented: use **Privacy & Security → Open Anyway** OR `xattr -d com.apple.quarantine /Applications/<App>.app`.
    2. **`Hiddify-MacOS.dmg` is broken on macOS Tahoe** — Network Extension is silently rejected because the build isn't notarized with an Apple Developer ID. `systemextensionsctl list` shows 0 extensions; `networksetup -listallnetworkservices` shows no VPN; `route -n get default` keeps pointing at `en0`. No popup, no error in the app. **New recommended path on Apple Silicon**: install **Hiddify iOS via "Designed for iPad"** from the Mac App Store — uses the iOS VPN model, sidesteps the macOS Network Extension issue entirely. Validated end-to-end on M2 Pro / Tahoe. Hiddify Desktop kept in the doc as a "broken / for reference" section.
  - Both walkthroughs' "Validation status" tables now list per-app last-tested date and result.
- **`README.zh-CN.md` machine-assisted draft (v0.4.3)** — full re-translation of the current `README.md` (covering everything from v0.1.0 through v0.5.1). Marked at the top as **"machine-quality draft, pending native review"** with a Chinese-language note pointing readers to the authoritative English version. Includes a translator's glossary table at the end mapping technical terms (VLESS-Reality, Hysteria2, JA3, idempotent, hardening, port hopping, …) to suggested Chinese renderings + notes on when to keep the English term. The placeholder zh-CN page from v0.1.0 (which still said "pre-alpha") is replaced. Native-speaker review still needed before claiming this fully ships; community contributors welcome.

### Carried forward to later releases (still blocked on externals)
- zh-CN native-speaker review pass on the v0.4.3 draft.
- GitLab shell-executor runner fix.
- Pen-test of the remaining clients (Shadowrocket / Streisand / V2Box / NekoBox).

## [0.5.1] - 2026-05-14

Seventh tagged release. Sole new feature: real byte-level JA3 + JA3S fingerprinting in the probe-resistance suite's scenario 02. The v0.4.1 7-feature TLS shape comparator gains two byte-level fingerprints, computed inline via stdlib `ssl.MemoryBIO` — no scapy / tlslite-ng dependency added; `requirements.txt` stays stdlib-only. JA4 + JA4S (FoxIO 2023+ spec) carry forward to v0.5.2.

### Added
- **Byte-level JA3 + JA3S in `tls_fingerprint_compare.py`** (scenario 02). Captures raw handshake bytes via stdlib `ssl.MemoryBIO` (no scapy / tlslite-ng dependency added), parses the TLS record + handshake layers in-process, and computes the Salesforce 2017 JA3 / JA3S md5s with GREASE values (RFC 8701) excluded. Adds `ja3`, `ja3_raw`, `ja3s`, `ja3s_raw`, and per-fingerprint `parse_state` to the `TlsShape` dataclass; `diff_shapes()` picks them up automatically.
  - Smoke-tested positive (target=dest=`www.microsoft.com` → matching JA3 `304734bb1c086c3453b387400cf83f11`, JA3S `15af977ce25de452b96affa2addb1036`) and negative (target=`www.apple.com`, dest=`www.microsoft.com` → cert fields diverge as in v0.4.1; JA3/JA3S match because (a) client TLS stack is the same for both probes and (b) TLS 1.3 ServerHello carries very few clear-text extensions).
  - Honest naming: scenario doc 02 now spells out what JA3 catches (controller-side sanity), what JA3S catches (server-side mirroring), and the TLS 1.3 limitation that most ServerHello extensions migrated to `EncryptedExtensions` (so JA3S is a weaker signal in 2026 than it was in 2017). JA4 / JA4S land in v0.5.2 with cross-validation against `ja4-python`.

### Fixed
- **Self-pinning bumped to v0.5.1 across all entry points** — `scripts/install.sh` URL + `STEALTH_VERSION` default, `cloud-init/stealth-vps.yaml` `ansible-pull -C` arg, `terraform/modules/stealth-vps` `stealth_version` default, the Hetzner example, and every doc example all now reference `v0.5.1`. Same invariant as v0.4.2 onwards: fetching at the v0.5.1 tag deploys v0.5.1.

### Deferred to v0.5.x (later sprints)
- JA4 + JA4S.
- HTTP/2 SETTINGS-frame comparison.
- AWS / DigitalOcean / Vultr / Proxmox Terraform examples.
- Pulumi reference.

## [0.5.0] - 2026-05-13

Sixth tagged release. Introduces a fourth entry-point for stealth-vps: a **provider-agnostic Terraform module** under `terraform/modules/stealth-vps/`. The module generates the cloud-init `user_data` string from typed HCL inputs; the caller hands the string to whatever cloud provider's create-server resource they use. One end-to-end worked example (Hetzner Cloud, ARM `cax11` by default). The other three v0.5.0 roadmap items (byte-level JA3/JA4, HTTP/2 frame comparison, Pulumi reference) move to "later in v0.5.x".

### Added
- **Terraform module** (`terraform/modules/stealth-vps/`) — provider-agnostic. Generates the cloud-init `user_data` string from typed HCL inputs (SSH key, ssh_port, domain, letsencrypt_email, reality_dest, reality_servernames, free-form `extra_role_vars`, repo_url, log_dir). Output is a string; the caller hands it to whatever provider's create-server resource they use. No `required_providers` block in the module — the user picks the cloud.
  - Input validation: `stealth_version` matches SemVer tag pattern, `ssh_public_key` starts with a supported key type, `ssh_port` is non-privileged, `letsencrypt_email` is email-shaped when non-empty.
  - Template: `templates/stealth-vps.cloud-init.tftpl` — same shape as the static `cloud-init/stealth-vps.yaml` but parameterized; merges convenience inputs with `extra_role_vars` (later wins) into a YAML-encoded `/etc/stealth-vps/extra-vars.yml`.
- **Hetzner Cloud worked example** (`terraform/examples/hetzner/`) — end-to-end: `hcloud_ssh_key` + `hcloud_server` calling the module, IPv4/IPv6 enabled by default, labels carry `stealth_version` so `hcloud server list -l project=stealth-vps` works. Defaults to `cax11` (ARM 2v/4GB) to exercise the v0.4.0 arm64 support.
- **`docs/terraform.md`** — architecture diagram, quickstart, adapter snippets for AWS / DigitalOcean / Proxmox / Vultr, versioning guidance (`?ref=...` vs `stealth_version`), CI status.
- **README "Three ways to use it" → "Four ways"** — Terraform path added as the IaC option; the other three (`install.sh`, raw Ansible, static cloud-init) stay unchanged.

### Fixed
- **cloud-init/stealth-vps.yaml version drift** — same shape as the v0.4.2 install.sh fix: the static cloud-init still pinned `ansible-pull -C v0.1.0`, so anyone who pasted that YAML into a hypervisor's user-data field would have deployed the v0.1.0 stack. Bumped to `v0.5.0` (now matches the release tag). Comment also points at the new Terraform module for the dynamic variant.
- **Self-pinning across all entry points** — `scripts/install.sh` URL + `STEALTH_VERSION` default, `cloud-init/stealth-vps.yaml` `ansible-pull -C` arg, `terraform/modules/stealth-vps` `stealth_version` default, and the Hetzner example all now reference `v0.5.0`. Fetching any entry point at the v0.5.0 tag deploys the v0.5.0 release.

### Deferred to v0.5.x (later sprints)
- Byte-level JA3/JA4 + JA3S/JA4S.
- HTTP/2 SETTINGS-frame comparison.
- AWS / DigitalOcean / Vultr / Proxmox Terraform examples.
- Pulumi reference.

## [0.4.2] - 2026-05-13

Hotfix release. Sole change: the one-shot installer path was broken since v0.1.0 — the advertised vanity URL never existed, and even if you fetched the installer some other way, its default `STEALTH_VERSION` would have deployed the v0.1.0 stack regardless of which release tag you got it from. Both fixed; the bootstrapper now self-pins to the v0.4.2 release the URL points at.

The three roadmap items still blocked on externals (iOS/macOS hardware, native zh-CN reviewer, GitLab runner fix) carry over to v0.4.3.

### Fixed
- **One-shot install URL** — `https://get.imprezahost.com/stealth` was advertised in `README.md` and `scripts/install.sh` since v0.1.0 but the domain was never set up (`get.imprezahost.com` DNS does not resolve). Replaced with `https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.4.2/scripts/install.sh` — pinned to a real release tag, works the moment you copy-paste it.
- **Installer default version drift** — `STEALTH_VERSION` default in `scripts/install.sh` was still `v0.1.0`, meaning anyone who ran the bootstrapper without an override got the v0.1.0 stack (no hardening, no LE, no observability, no arm64). Bumped to `v0.4.2`. The installer now ships the same release whose tag is in the URL it was downloaded from — both file references point at the v0.4.2 tag this release defines.

### Deferred to v0.4.3
- Pen-tested iOS + macOS validation pass — still needs hardware in the QA rotation.
- zh-CN README rewrite — still needs native-speaker review.
- GitLab shell-executor runner fix.

### Planned (v0.5.0)
- True byte-level JA3/JA4 + JA3S/JA4S in `tls_fingerprint_compare.py` (scapy or tlslite-ng); golden snapshots per dest + a `scripts/update_golden.py` for quarterly upstream rotations.
- HTTP/2 SETTINGS-frame comparison in `active_probe.py`.
- Terraform module (provider-agnostic) + Pulumi reference.

## [0.4.1] - 2026-05-13

Fifth tagged release. The two probe-resistance scripts that landed scaffolded in v0.4.0 (scenarios 02 + 03) are now runnable: real TLS shape comparison (seven handshake-visible features) and real HTTP/1.1 response-shape comparison (status + header-set + body-bucket). Both smoke-tested positively (target=dest matches) and negatively (target≠dest detects every divergent feature). Two roadmap items (pen-tested iOS/macOS validation, zh-CN README rewrite) carry over to v0.4.2 — still blocked on the same externals.

### Added
- **Probe-resistance scripts 02 + 03 are now runnable** (filled in from the v0.4.0 scaffold):
  - `tls_fingerprint_compare.py` — 7-feature TLS shape comparison using stdlib `ssl` + a single `openssl x509 -inform DER -text` shell-out: protocol version, chosen cipher, selected ALPN, peer cert subject CN, SAN list (sorted), issuer CN, signature + public-key algorithms. Cert is captured to a temp file (avoids Windows openssl-stdin quirks). One `WHY:` line per diverging feature on fail.
  - `active_probe.py` — HTTP/1.1 response-shape comparison via stdlib `http.client`: status code, lower-cased header keys (minus a VARIABLE_HEADERS set for `date` / `set-cookie` / `cf-ray` / `x-amz-cf-id` / etc.), and a body-size bucket. Forces ALPN `http/1.1` so the response parses with stdlib; HTTP/2 frame check stays v1.0.
  - Both scripts smoke-tested positively (target=`www.microsoft.com`, dest=`www.microsoft.com` → exit 0 with one-line OK) and negatively (target=`www.apple.com`, dest=`www.microsoft.com` → exit 1, every divergent feature reported).
  - `PROBE_VERBOSE=1` dumps full shapes for triage.
- Honest naming: the script does **not** claim to compute JA3/JA4. Those are byte-level fingerprints over raw `ClientHello` / `ServerHello`; Python's stdlib abstracts those bytes away. Scenario doc 02 now documents this explicitly and points to v1.0 plug-in territory (scapy / tlslite-ng + golden snapshots).
- Scenario docs `02-tls-fingerprint.md` and `03-active-probe-no-key.md` rewritten — the "v0.5 (planned)" sections become "v0.4.1 (runnable)" with the actual implementation details, and v1.0 picks up only what stays deferred.

### Deferred to v0.4.2
- Pen-tested iOS + macOS validation pass — still needs hardware in the QA rotation.
- zh-CN README rewrite — still needs native-speaker review.

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
