# Operations

Day-to-day operations once the stack is installed. Aimed at someone who's already finished an install (interactive or headless) and now needs to add users, rotate credentials, upgrade, or diagnose a problem.

The operator-facing CLI is `s-vps`. Everything below either uses `s-vps` directly or describes what it does under the hood so you can recover when something goes sideways.

---

## The `s-vps` operator CLI

Installed at `/usr/local/bin/s-vps` from v0.6.0 onward by `tasks/cli_wrapper.yml`. Pure-bash so it works even when the Python pkg is broken (partial-update recovery).

```bash
s-vps help              # full subcommand list
s-vps version           # stealth-vps tag + ansible-core + python3 versions
s-vps status            # quick `systemctl is-active` summary for all managed units
s-vps diagnose          # post-deploy ✓/✗/⚠ checklist (ports, services, panel HTTPS)
s-vps update            # re-run ansible-pull at the pinned tag (read from /etc/stealth-vps/version)
s-vps update v0.7.0     # upgrade to a specific tag
```

`s-vps update` (no args) re-deploys the same tag using the choices stored in `/etc/stealth-vps/installer.env` (domain, optional services). The bot token is read out of `/etc/stealth-vps/bot.env` so re-runs preserve it without you typing it again.

`s-vps diagnose` sources the same `health-check.sh` the installer uses for `[5/5]` — same ✓/✗/⚠ output. It reads actual ports from `/etc/stealth-vps/{reality,hysteria,panel}.state.yml`, so it works even when ports are randomised away from 443 / 8443.

---

## Adding a user

There are three ways, listed in order of "what you should reach for first":

### 1. Telegram bot (if enabled)

Only available if you installed with `STEALTH_BOT_ENABLED=true STEALTH_BOT_TOKEN=...` (or ticked the bot box in the TUI). After pairing (see below), DM the bot:

```text
/user add alice
```

The bot creates the client through the 3X-UI panel API, writes the new row into `/etc/stealth-vps/users.index.json` (operator's portable source of truth), writes the subscription file at `/var/lib/stealth-vps/subscriptions/<token>.txt`, and DMs you back the Reality + Hysteria2 URIs plus the subscription URL. One command, four artifacts updated.

Other `/user` verbs:

```text
/user list                # enabled clients only
/user revoke alice        # disable in panel + mark index row enabled=false
```

Labels must match `[a-zA-Z0-9_-]{1,32}`. Names starting with `stealth-vps-` are reserved for the role's own seed clients.

**Bot pairing** — the first time anyone messages the bot with `/start`, that chat_id becomes the sole admin and gets persisted to `/var/lib/stealth-vps-bot/state.json`. If `STEALTH_VPS_BOT_ADMIN_CHAT_IDS` was already set via env, pairing is skipped and the env list is authoritative.

### 2. 3X-UI panel UI (without the bot)

Tunnel the panel through SSH:

```bash
ssh -L 8888:127.0.0.1:<panel-port> root@<vps>
```

The panel port + base path + username + password are in `/root/stealth-vps-credentials.txt` (and `/etc/stealth-vps/panel.state.yml`). Open `http://localhost:8888/<base-path>/`, log in, find the `stealth-vps-reality` inbound, click "Clients" → "+ Add client", give it an `email` (this becomes the label), let the panel generate a UUID.

After adding via the panel, the `users.index.json` is *not* updated automatically — only the bot/CLI does the double-write. To bring the index in sync, re-run `s-vps update` (the index task is idempotent and reconciles).

### 3. Direct `users.index.json` edit (last resort)

For emergencies — bot is broken, panel API is down, you need a working client *now*. Format:

```json
{
  "version": 1,
  "users": {
    "alice": {
      "reality_uuid": "<uuid4>",
      "hysteria_password": "<random 32 chars>",
      "sub_token": "<32-byte urlsafe>",
      "created_at": "2026-05-15T12:00:00Z",
      "enabled": true
    }
  }
}
```

Generate the random values with:

```bash
python3 -c "import uuid, secrets; print(uuid.uuid4()); print(secrets.token_urlsafe(32))"
```

In v0.6 (panel mode) the panel side won't know about a direct edit until you also create the client through the panel UI; the index is only authoritative in v0.7 headless mode.

---

## Subscription endpoints

Only available when you installed with `STEALTH_SUBSCRIPTION_ENABLED=true`. Caddy serves per-user subscription files from `/var/lib/stealth-vps/subscriptions/<sub_token>.txt`.

Get a user's subscription URL via the bot:

```text
/sub alice                # prints the current URL
/sub revoke alice         # rotate the token; old URL stops working, new one issued
```

Without the bot, the URL format is:

```text
<scheme>://<host>/.well-known/stealth-vps-sub/<sub_token>
```

Where `<scheme>` / `<host>` depend on the bind mode:

- **Loopback (default, `STEALTH_SUBSCRIPTION_EXPOSE=false`)** — `http://127.0.0.1:8443/.well-known/stealth-vps-sub/<token>`. Fetch via SSH tunnel: `ssh -L 8443:127.0.0.1:8443 root@<vps>`.
- **Public (`STEALTH_SUBSCRIPTION_EXPOSE=true`, requires a domain)** — `https://<your-domain>/.well-known/stealth-vps-sub/<token>` with a Let's Encrypt cert that Caddy maintains separately from acme.sh.

The `sub_token` for each user is recorded in `users.index.json`. To grab one without the bot:

```bash
jq -r '.users.alice.sub_token' /etc/stealth-vps/users.index.json
```

---

## Rotating credentials

### Panel admin password

```bash
ssh root@<vps>
/usr/local/x-ui/x-ui setting -username <new-user> -password <new-pass>
systemctl restart x-ui
```

Then edit `/etc/stealth-vps/panel.state.yml` to match (the bot + CLI read this file for API auth). Mode stays `0640 root:stealth-vps-bot` after the bot install ran.

### Bot token

Replace in `/etc/stealth-vps/bot.env`:

```bash
sed -i 's|^STEALTH_VPS_BOT_TOKEN=.*|STEALTH_VPS_BOT_TOKEN=<new-token>|' /etc/stealth-vps/bot.env
systemctl restart stealth-vps-bot
```

Pairing state in `/var/lib/stealth-vps-bot/state.json` is preserved — the admin chat IDs stay paired against the new token.

### Reality keys (full reseed)

Nuclear option — invalidates every client. Useful after a key compromise.

```bash
rm /etc/stealth-vps/reality.state.yml
s-vps update                # re-runs the role; reality_state.yml regenerated
```

All existing clients will need fresh URIs. `users.index.json` is preserved (UUIDs unchanged), so use `/user list` to get every label and `/sub <label>` to re-issue.

### Hysteria2 password / obfs

Same pattern — delete `/etc/stealth-vps/hysteria.state.yml` and `s-vps update`.

---

## Upgrading to a new release

Trivial:

```bash
s-vps update v0.7.0
```

What this does, in order:

1. Reads `/etc/stealth-vps/installer.env` for your original choices (domain, optional services).
2. Reads `/etc/stealth-vps/bot.env` for the bot token if the bot is enabled.
3. Runs `ansible-pull -U github.com/imprezahost/stealth-vps -C v0.7.0 -e <choices>` and tees stderr+stdout to `/var/log/stealth-vps/update-<ts>.log`.
4. On success, rewrites `/etc/stealth-vps/version` to the new tag.
5. On failure, runs `error_wrap_explain` against the log and prints a human-readable hint.

If you don't have `s-vps` (pre-v0.6 install), fall back to:

```bash
ansible-pull -U https://github.com/imprezahost/stealth-vps.git \
  -C v0.7.0 -i 'localhost,' -c local \
  ansible/playbooks/site.yml
```

Or, with a local checkout:

```bash
cd stealth-vps
git fetch --tags
git checkout v0.7.0
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

---

## Rolling back

The role is idempotent and supports re-running an older version. To downgrade, re-run `s-vps update v0.X.Y` with the older tag. Generated state files (`reality.state.yml`, `hysteria.state.yml`, `panel.state.yml`, `users.index.json`) are preserved — they are not regenerated unless you explicitly delete them or trigger a rotation task.

**Caveat from v0.5 → v0.6**: `users.index.json` is a new artifact. Downgrading from v0.6 to v0.5 leaves it in place but unused; no harm done.

---

## Monitoring

The role installs `prometheus-node-exporter` bound to `127.0.0.1:9100` plus a stealth-vps-specific metrics updater that writes per-protocol counters into the textfile collector dir. Single scrape target, two metric families.

To consume from outside:

```bash
ssh -L 9100:127.0.0.1:9100 root@<vps>
curl http://localhost:9100/metrics
```

Or expose externally by setting `stealth_vps_observability_listen: "0.0.0.0:9100"` and adding the scraper's CIDR to `stealth_vps_observability_allow_from`. UFW will only allow listed sources.

A drop-in Grafana dashboard JSON ships under [`observability/grafana/dashboards/`](../observability/grafana/dashboards/). Import it into your existing Grafana instance — the dashboard expects the data source variable `${DS_PROMETHEUS}` to point at your Prometheus.

Prometheus alert rules ship under [`observability/prometheus/`](../observability/prometheus/).

---

## Running on arm64

`stealth-vps` runs on amd64 and arm64 hosts from v0.4.0 onward. Same `s-vps update` / `ansible-playbook` invocation; the role detects the architecture and pulls the right binary variants automatically.

Tested concretely on:

| Provider / class | Image | Notes |
|---|---|---|
| Oracle Cloud Free Tier (Ampere A1) | Ubuntu 22.04 / 24.04 arm64 | 4 OCPU + 24 GB free for life; recommended starting point for arm64 evaluations |
| AWS Graviton2/3 (`*g.*`) | Debian 12 / Ubuntu 24.04 arm64 | Production-grade; smoke-tested |
| Hetzner ARM (CAX line) | Debian 12 arm64 | EU-located, BBR works out of the box |
| Raspberry Pi 4 / 5 (Debian 12) | 64-bit | Works but not recommended as a stealth-vps host — uplink + thermal limits |

The architecture map lives in `defaults/main.yml` as `stealth_vps_arch_map`. The role today maps `x86_64 → amd64` and `aarch64 → arm64`. If you want to try an unvalidated arch (armv7, 386, riscv64 once upstream publishes binaries), extend the map and rerun — every binary URL is derived from this fact, so adding a row is the only change needed at the role level.

Caveats specific to arm64 hosts:

- **3X-UI panel arm64 tarball** comes from the same `MHSanaei/3x-ui` release pin (`stealth_vps_panel_version`). Verified to publish `x-ui-linux-arm64.tar.gz` for every release the role currently pins to.
- **Hysteria2 arm64 binary** is published per release at `apernet/hysteria` as `hysteria-linux-arm64`. No source build needed.
- **Xray-core arm64 archive** is `Xray-linux-arm64-v8a.zip` upstream (vs `Xray-linux-64.zip` for amd64). The role's `tasks/reality_xray_binary.yml` maps the arch automatically — operators don't see the naming quirk.
- **Kernel BBR** works the same on arm64 as on amd64 — the `tcp_bbr` module is in the standard Debian/Ubuntu arm64 kernel.
- **Molecule scenario** still runs only on amd64 in CI; arm64 hosts get validated manually until we add an arm64 runner. See `tests/README.md`.

### arm64 smoke runbook

When provisioning a new arm64 host (Hetzner CAX, Oracle Ampere, AWS Graviton, RPi-class), the validation sequence is identical to amd64 — only the host base changes:

```bash
# On a fresh Debian 12 / Ubuntu 24.04 arm64 host:
curl -fsSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.8.1/scripts/install.sh \
    | sudo bash

# Confirm the role picked the right binaries:
sudo file /usr/local/bin/xray | grep -q "aarch64" && echo "✓ xray arm64"
sudo file /usr/local/bin/hysteria | grep -q "aarch64" && echo "✓ hysteria arm64"

# Standard post-deploy health check:
sudo s-vps diagnose
sudo s-vps status

# Test the full mutation cycle (headless mode):
sudo s-vps user add testuser
sudo s-vps user rotate testuser
sudo s-vps user purge testuser
sudo s-vps user list                     # confirm testuser is gone
```

All three CLI verbs (add/rotate/purge) hit the same code paths as on amd64 — no arch-specific logic. The Reloader uses `subprocess.run(["systemctl", ...])`, the index is platform-agnostic JSON, and the Reality/Hysteria binaries are upstream's native arm64 builds. If anything fails differently on arm64 vs amd64, please file an issue with `uname -a` + the bootstrap log.

---

## Troubleshooting

### First step: `s-vps diagnose`

```bash
s-vps diagnose
```

Runs the same health-check the installer ran. ✓ means "OK", ✗ means "operator action required", ⚠ means "not blocking but watch this". Each line names the unit / port / cert it checked; the failure mode is in the parenthetical.

### Second step: read the logs

- `journalctl -u xray` — Reality / Xray logs (in panel mode, Reality runs inside x-ui, so check `x-ui` instead)
- `journalctl -u x-ui` — 3X-UI panel logs (also contains Reality output)
- `journalctl -u hysteria-server` — Hysteria2 logs
- `journalctl -u stealth-vps-bot` — Telegram bot logs (only if enabled)
- `journalctl -u caddy` — Caddy subscription endpoint (only if enabled)
- `journalctl -u fail2ban` — ban events
- `/var/log/stealth-vps/install-*.log` — most recent install / `s-vps update` output

### Third step: the state files

Everything the role remembers between runs lives under `/etc/stealth-vps/`:

```text
/etc/stealth-vps/
├── version              # pinned release tag, read by `s-vps update`
├── installer.env        # operator choices (domain, optional services) — sourced by s-vps
├── panel.state.yml      # 3X-UI port + username + password + base path
├── reality.state.yml    # X25519 keypair + default client UUID + port
├── hysteria.state.yml   # Hysteria2 port + auth password + obfs password
├── users.index.json     # operator's source-of-truth: who is authorised
├── bot.env              # bot token + admin IDs + per-protocol URI params
└── tls/                 # Let's Encrypt symlinks (when stealth_vps_domain is set)
```

If a state file gets corrupted, deleting it and re-running `s-vps update` is the canonical recovery — the role detects the missing file and regenerates the affected component. Connections using the regenerated credentials need to be re-issued.

### Fourth step: common patterns

The installer's `error-wrap.sh` already catches the most common failures and prints a remediation hint. The pattern catalogue lives in [`scripts/lib/error-wrap.sh`](../scripts/lib/error-wrap.sh) (`_EW_KNOWN_PATTERNS`) and includes: GitHub unreachable, TLS validation failure, ACME verify error, panel didn't come up, dpkg lock, no disk space, Xray failed to start. Add new patterns by appending to that array.
