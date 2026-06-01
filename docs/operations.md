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

> The `.txt` file is materialised when you **add or rotate a user** (`s-vps user add` / `s-vps user rotate` / the bot's `/user add`), not at converge time — so on a fresh box a user's file appears after the first mutation. *(v0.12.1 fixed a bug where single-node hosts — no fleet registered — skipped this write entirely via the CLI, leaving the subscription URL 404'ing and the onboarding deep-links/QR pointing at a dead bundle.)*

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

> **Known limitation — dual ACME on a domain host.** With a public subscription endpoint *and* a domain, the box runs two ACME clients for the same name: acme.sh (HTTP-01 on :80, for the Hysteria2 / panel cert) and Caddy (TLS-ALPN-01 on :443, for the subscription + onboarding cert). They don't clash at first issuance, but once Caddy is up holding :80 + :443, an acme.sh **standalone** renewal can't bind :80. If you front a domain with Caddy, prefer letting Caddy own the cert (or open :80 only during acme.sh's renewal window). A first-class fix — Caddy issues once and Hysteria2 reads Caddy's cert — is tracked for a later release.

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

### Health-check Prometheus exporter (v0.9.0+)

A tiny HTTP server at `:9102` that exposes:

- `GET /metrics` — Prometheus text body
- `GET /healthz` — `ok` 200 (for k8s liveness / curl smoke)

Off by default. Enable in inventory:

```yaml
stealth_vps_health_exporter_enabled: true
# Default bind = 127.0.0.1 (loopback). Flip to 0.0.0.0 to expose
# externally — the role opens UFW for you in that case. Recommended:
# leave it on loopback and front via Caddy reverse-proxy + basic auth.
stealth_vps_health_exporter_bind_addr: "127.0.0.1"
stealth_vps_health_exporter_bind_port: 9102
```

After `s-vps update`:

```bash
$ curl -s http://127.0.0.1:9102/metrics | head -20
# HELP stealth_vps_unit_active 1 when `systemctl is-active <unit>` returns `active`.
# TYPE stealth_vps_unit_active gauge
stealth_vps_unit_active{unit="xray.service"} 1
stealth_vps_unit_active{unit="hysteria-server.service"} 1
stealth_vps_unit_active{unit="x-ui.service"} 0
stealth_vps_unit_active{unit="caddy.service"} 1
stealth_vps_unit_active{unit="stealth-vps-bot.service"} 0
# HELP stealth_vps_reality_port_listening 1 when TCP connect to Reality port succeeds.
# TYPE stealth_vps_reality_port_listening gauge
stealth_vps_reality_port_listening{port="51820"} 1
# HELP stealth_vps_index_readable 1 when users.index.json parses cleanly.
# TYPE stealth_vps_index_readable gauge
stealth_vps_index_readable 1
# HELP stealth_vps_users_total Total user rows in the index (including revoked).
# TYPE stealth_vps_users_total gauge
stealth_vps_users_total 4
```

**vs. the existing node_exporter textfile flow:**

| Endpoint                                            | When to use                                       |
|-----------------------------------------------------|---------------------------------------------------|
| `/metrics` on `:9102` (this feature)                | You don't run node_exporter. Push to a SaaS scraper. |
| Node-exporter's textfile collector (`v0.6.0`)       | You already run node_exporter. Same data, no extra port. |

The two are complementary. Operators who run both get the same metrics from both surfaces.

### Encrypted backup + restore (v0.9.0+)

stealth-vps ships a CLI for snapshotting operator state into an `age`-encrypted tarball. The encryption is **public-key only on the host** — the box holds your `age1...` recipient, never the secret key.

**Setup on the operator workstation:**

```bash
age-keygen -o ~/.config/stealth-vps-backup.key
# Output:
#   Public key: age1abcdef...                  ← copy this into inventory
# Contents of the file are the SECRET KEY. Stash it like an SSH key.
```

**Inventory:**

```yaml
stealth_vps_backup_enabled: true
stealth_vps_backup_recipient: "age1abcdef..."   # from age-keygen above
stealth_vps_backup_timer_enabled: true          # optional daily timer
```

After the next `s-vps update`, the role:

- apt-installs `age`
- creates `/var/backups/stealth-vps/` mode 0700
- drops `/etc/stealth-vps/backup.env` with the recipient
- (optional) installs the daily systemd timer

**Manual backup:**

```bash
$ sudo s-vps backup
✓ backup complete: /var/backups/stealth-vps/stealth-vps-backup-20260520T1530Z-vps-1.tar.age
  size              : 8421 bytes
  included paths    : /etc/stealth-vps, /var/lib/stealth-vps

Copy the file off-host:
  scp root@<host>:/var/backups/stealth-vps/stealth-vps-backup-...tar.age ./
```

Drop the file in S3, B2, Restic — wherever. The `.tar.age` is opaque ciphertext; cloud-storage providers can't see anything inside.

**Restore on a freshly converged host:**

```bash
# Copy the archive + your identity file (from your workstation) into place,
# then:
sudo s-vps restore /tmp/stealth-vps-backup-...tar.age \
  --identity /tmp/identity.txt
✓ restored 42 files from /tmp/stealth-vps-backup-...tar.age

Next step: run `s-vps reload` to re-apply the restored state to
Xray + Hysteria2.
```

The identity file is required at restore time and **only at restore time** — wipe it from the host after you're done if you don't want a long-lived copy lying around.

**What's in the backup:**

- `/etc/stealth-vps/` — `installer.env`, `version`, `*.state.yml`, `reloader-args.json`, `bot.env` (if bot enabled), `panel.state.yml` (if panel mode)
- `/var/lib/stealth-vps/` — `users.index.json`, `subscriptions/*.txt`

**Not in the backup** (recreated by ansible converge / package install):

- `/usr/local/bin/*`, `/usr/local/lib/stealth_vps/` — idempotent reinstall
- `/etc/systemd/system/stealth-vps-*` — rendered by the role
- Xray / Hysteria2 / Caddy binaries — apt / upstream

### Opt-in auto-update (v0.9.0+)

A daily systemd timer that polls GitHub Releases and applies updates within a policy boundary. Off by default; enable per-host in inventory:

```yaml
stealth_vps_auto_update_enabled: true
stealth_vps_auto_update_policy: patch-only   # safe default
```

Policy ladder:

| Policy        | Accepts                          | Refuses                                  |
|---------------|----------------------------------|------------------------------------------|
| `patch-only`  | `v0.9.0 → v0.9.x` (bug fixes)    | Minor or major bumps                     |
| `minor-patch` | `v0.9.0 → v0.10.y` (new features)| Major bumps (`v0.x → v1.0` requires hand)|
| `disabled`    | Nothing                          | All updates                              |

After enabling + running `s-vps update`, the role drops:

- `/etc/systemd/system/stealth-vps-auto-update.{service,timer}`
- `/etc/stealth-vps/auto-update.env` (mode 0600, holds the optional GitHub token)

Inspect what would happen without applying:

```bash
sudo /usr/bin/python3 -m stealth_vps.auto_update --dry-run
```

Watch the journal for what the timer actually did:

```bash
journalctl -u stealth-vps-auto-update.service -n 50
```

**Fleet behind a NAT?** GitHub's unauthenticated API rate-limits to 60 requests/hour per egress IP. Past ~50 hosts on the same daily timer this becomes a real problem. Supply a PAT in inventory:

```yaml
stealth_vps_auto_update_github_token: ghp_xxxxxxxxxxxxxxxxxx
```

The token only needs read access to public repos (`public_repo` scope, or no scopes for a fine-grained read-only token). Stored at `/etc/stealth-vps/auto-update.env` with mode 0600.



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
curl -fsSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.9.0/scripts/install.sh \
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

## Multi-node fleet (v0.10.0+)

A **fleet** in stealth-vps is one CONTROL box + N DATA NODES. The control holds `users.index.json` + the bot + the subscription endpoint; data nodes terminate Reality + Hysteria2. The control SSHs into each data node to push the index after every user mutation. Per-node Reality keys ensure that compromising one data node doesn't leak credentials for the rest of the fleet.

See [`docs/internal/roadmap-v0.10-multi-node.md`](internal/roadmap-v0.10-multi-node.md) for the full design rationale (ADRs, blast-radius model, scope decisions).

### Bootstrapping a control plane

The control is a regular stealth-vps host with one inventory flag flipped:

```yaml
# inventory for the control box
stealth_vps_control_enabled: true
stealth_vps_reality_enabled: false
stealth_vps_hysteria_enabled: false
stealth_vps_panel_enabled: false
# Optional but typical on the control:
stealth_vps_bot_enabled: true
stealth_vps_subscription_enabled: true
stealth_vps_subscription_expose: true   # serve sub URLs publicly
```

The mutex assert in `tasks/main.yml` catches `control_enabled=true` paired with any of the data-plane services — the fix-up message tells you exactly which flags to flip.

On first `s-vps update`, the role creates:

- `/etc/stealth-vps/fleet/` mode 0700 — one YAML file per registered data node
- `/etc/stealth-vps/keys/` mode 0700 — one ed25519 keypair per data node
- `/etc/stealth-vps/users.index.json` seeded as empty schema v2

The control box runs no Xray, no Hysteria2, no x-ui. `s-vps status` shows only `caddy.service` + `stealth-vps-bot.service` active.

### Registering data nodes

Each data node is a regular stealth-vps install (headless mode, v0.9 or later). Once installed and reachable over SSH, register it with the control:

```bash
# On the control:
sudo s-vps fleet add tokyo-1 --ssh-host 103.106.228.154
```

The interactive workflow:

1. Control generates `/etc/stealth-vps/keys/control_to_tokyo-1.ed25519` (ed25519 keypair).
2. Prints the public key + asks you to install it on the data node:

   ```bash
   # Run ON the data node (Tokyo) as root:
   echo 'ssh-ed25519 AAAA... control_to_tokyo-1' >> /root/.ssh/authorized_keys
   chmod 0600 /root/.ssh/authorized_keys
   ```

3. Press Enter on the control.
4. Control probes `s-vps version` on the remote — must respond with v0.9+ (schema v2 required).
5. Control slurps `reality.state.yml` + `hysteria.state.yml` over SSH to capture the data node's per-node keys.
6. Control rewrites the data node's authorized_keys entry into the restricted form:

   ```text
   command="/usr/local/bin/s-vps fleet-receive",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... control_to_tokyo-1
   ```

   After this rewrite, the control's SSH key can ONLY trigger `s-vps fleet-receive` on the data node — no shell, no port-forward, no other commands.
7. Control writes `/etc/stealth-vps/fleet/tokyo-1.yml` with the discovered fields.

For non-interactive bulk bootstraps, pre-install the pubkey via cloud-init / Terraform user-data and pass `--yes` to skip the prompt.

### Listing, syncing, removing

```bash
$ s-vps fleet list
NODE_ID                  SSH_HOST               PORT  STATUS  LAST_SYNC
------------------------------------------------------------------------------
amsterdam-1              10.0.0.2               22    ok      2026-05-21T10:00:00Z
tokyo-1                  103.106.228.154        22    ok      2026-05-21T10:00:05Z

$ s-vps fleet sync                                # push to ALL nodes in parallel
Syncing users.index.json to 2 node(s)...

NODE_ID                  STATUS    DURATION    DETAIL
------------------------------------------------------------------------------
amsterdam-1              ✓ ok      342 ms      {"ok":true,"user_count":4,"reload":"ok"}
tokyo-1                  ✓ ok      512 ms      {"ok":true,"user_count":4,"reload":"ok"}

$ s-vps fleet sync --node tokyo-1                 # push to one node only
$ s-vps fleet sync --dry-run                      # print what would happen
$ s-vps fleet remove amsterdam-1                  # unregister (data node keeps running)
```

`fleet sync` runs automatically after every `s-vps user add / revoke / purge / rotate` and after the bot's `/user add` / `/sub renew`. Operators batching many mutations can pass `--no-sync` and follow up with one `s-vps fleet sync` at the end.

### Promoting a single-node install into a fleet

Suppose you've been running stealth-vps as a single VPS in Tokyo for months. You want to add Amsterdam without dropping any existing clients. Steps:

1. Provision Amsterdam as a fresh stealth-vps (v0.10.0+, headless mode). It comes up with one default client; ignore it (will be overwritten by the next fleet sync).
2. Provision a new control box (a small €4/month VPS, or even a laptop with port-forwarded SSH access to the data nodes). Bootstrap it with `stealth_vps_control_enabled: true` per the snippet above.
3. On the control, `s-vps fleet add tokyo-1 --ssh-host <tokyo-ip>` (the existing single-node box). The control discovers Tokyo's per-node Reality keys.
4. On the control, `s-vps fleet add amsterdam-1 --ssh-host <amsterdam-ip>`.
5. SCP the existing `users.index.json` from Tokyo to the control:

   ```bash
   scp root@<tokyo-ip>:/etc/stealth-vps/users.index.json \
       root@<control-ip>:/etc/stealth-vps/users.index.json
   ```

6. `s-vps fleet sync` on the control — Tokyo and Amsterdam both receive the (real) users.index.json.
7. Existing Tokyo clients **keep working** without any URL refresh. Their old single-node subscription URL still resolves and the URIs in it still match Tokyo's keys.
8. **For the multi-node fallback feature to take effect**, clients need to refresh their subscription URL. The bot's `/sub <label>` (or a new `s-vps user show <label>`) emits the updated multi-node bundle. Once the user pastes the new URL into Hiddify, they get both Tokyo and Amsterdam endpoints with automatic lowest-latency selection.

Zero downtime in the steady state. The only window where any client sees a service blip is when the new control's `fleet sync` arrives at the data node and `s-vps fleet-receive` triggers a Reloader — same as `s-vps user add` does today. Active QUIC connections survive (Hysteria2 is reload-safe); TCP Reality connections drop and reconnect within a second.

### Rotating an SSH key

If you suspect a control's per-node SSH key is compromised, rotate it in place — zero-downtime, no fleet-wide reissue:

```bash
sudo s-vps fleet rotate-key tokyo-1
```

What happens, step by step:

1. Probe the data node via the OLD key. Must work — otherwise we'd have no rollback path.
2. Generate a new ed25519 keypair at `<keys_dir>/control_to_tokyo-1.ed25519.new`.
3. SSH in via the OLD key, append the NEW pubkey (restricted form) to authorized_keys. Both keys now valid.
4. Probe via the NEW key. Must work.
5. SSH in via the NEW key, remove the OLD entry from authorized_keys. Only the new key remains.
6. Atomic-replace the local `.new` files over the existing key paths.

If step 4 fails (new key didn't take), the rotation rolls back: remove the new entry from authorized_keys (via the still-working OLD key), delete the local `.new` files. The node ends up exactly as it was.

The data node never sees both keys for more than ~1 second.

### Blast radius

| Asset | Where | If leaked |
|---|---|---|
| `users.index.json` (UUIDs + Hy2 passwords) | Control + every data node | All clients impersonable against any node. **Same as single-node v0.9.** |
| Per-node Reality private key (X25519) | Only that data node | Reality handshakes to that node only. Other nodes unaffected. **Strict improvement vs. shared keys.** |
| `control_to_<node>` SSH key (private) | Control only | Attacker can rewrite that one node's `users.index.json` — equivalent to compromising the control. Does NOT grant access to other nodes (per-node keys). |
| Restricted `authorized_keys` entry on the data node | Each data node | Attacker reading it gets the control's pubkey for THAT node — not impersonable against the control or other nodes. |
| Bot token | Control only | Same exposure as single-node v0.9. |
| Operator's `age` identity (backup decrypt) | Off-host (operator's workstation) | Same exposure as single-node v0.9. |

The control plane is the central failure point. The v0.9 `age` backup of `/etc/stealth-vps/` captures `fleet/` + `keys/`; a control rebuild from backup onto a fresh VPS takes ~10 minutes and brings the fleet back online without any data node changes. Real HA (Raft / etcd / gossip) is deferred to v0.11+.

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
