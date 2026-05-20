# Multi-node mode (v0.10.0+)

Multi-node mode runs stealth-vps as **one control plane + N data nodes**: the control holds `users.index.json`, the bot, and the subscription endpoint; data nodes terminate Reality + Hysteria2. The control SSHs into each data node to push the user index after every mutation; per-node Reality keys ensure that compromising one data node doesn't leak credentials for the rest of the fleet.

This doc is the operator runbook. The architecture rationale lives in [`internal/roadmap-v0.10-multi-node.md`](internal/roadmap-v0.10-multi-node.md).

## When to use it

Three signals tell you you've outgrown single-node:

1. **Region failover.** The region where your VPS lives starts getting blocked. Single-node means redirecting every client to a new URL; multi-node means clients fail over automatically across regions.
2. **Latency-by-geography.** A client in Singapore connecting to a VPS in Frankfurt pays 150ms RTT. Hiddify Next / V2Box / NekoBox auto-pick the lowest-latency URI from a multi-node subscription bundle.
3. **~30+ concurrent clients per VPS.** A single CAX11 (€4/mo) holds about 50 simultaneous proxy sessions comfortably; past that, horizontal scaling beats vertical upgrade — especially when traffic peaks differ across timezones.

If none of those apply, stay single-node. Multi-node adds an extra VPS bill (the control) and more moving parts.

## Topology

```text
┌──────────────────────────────────────────────────────────────────┐
│ Control plane (1 small VPS, ~€4/month, or a laptop with SSH      │
│ reachability to the data nodes)                                  │
│                                                                  │
│ Holds:                                                           │
│   /etc/stealth-vps/users.index.json   (source of truth)          │
│   /etc/stealth-vps/fleet/<id>.yml     (one per data node)        │
│   /etc/stealth-vps/keys/control_to_<id>.ed25519                  │
│   /var/lib/stealth-vps/subscriptions/ (multi-node URI bundles)   │
│                                                                  │
│ Runs:                                                            │
│   stealth-vps-bot.service             (optional)                 │
│   caddy.service                       (subscription endpoint)    │
│   stealth-vps-health-exporter         (optional)                 │
│                                                                  │
│ DOES NOT run:                                                    │
│   xray.service / hysteria-server      (no Reality termination)   │
│   x-ui.service                                                   │
└─────────────┬────────────────────────────────────────────────────┘
              │ SSH push (per-node key, restricted command=...)
              │ scp users.index.json → s-vps fleet-receive
              ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌──────────
│ Data node: tokyo-1      │  │ Data node: amsterdam-1  │  │ data N…
│ /usr/local/bin/s-vps    │  │                         │  │
│ xray.service (Reality)  │  │                         │  │
│ hysteria-server.service │  │                         │  │
│ users.index.json        │  │ users.index.json        │  │
│ reality.state.yml       │  │ reality.state.yml       │  │
│ (own X25519 keys)       │  │ (own X25519 keys)       │  │
└─────────────────────────┘  └─────────────────────────┘  └──────────
```

Each data node is a **regular v0.10+ headless-mode install**. The only thing that changes vs single-node is: its `users.index.json` is now rewritten by the control's `fleet sync`, not by a local operator.

## Setup walkthrough

### 1. Provision the control box

A small VPS (any provider, any region — control doesn't terminate proxy traffic). Install stealth-vps with the control flag:

```yaml
# inventory or extra-vars
stealth_vps_control_enabled: true
stealth_vps_reality_enabled: false
stealth_vps_hysteria_enabled: false
stealth_vps_panel_enabled: false
stealth_vps_bot_enabled: true            # optional but typical
stealth_vps_subscription_enabled: true   # operator + clients fetch from here
stealth_vps_subscription_expose: true    # bind 0.0.0.0:443 with LE cert
stealth_vps_domain: control.example.com  # for the subscription LE cert
```

After `s-vps update v0.10.0`:

- `/etc/stealth-vps/fleet/` (mode 0700) exists, empty
- `/etc/stealth-vps/keys/` (mode 0700) exists, empty
- `/etc/stealth-vps/users.index.json` is an empty v2 schema
- Caddy + the bot are up; no Xray, no Hysteria2 on this box
- `s-vps status` shows only `caddy.service` + `stealth-vps-bot.service` active

### 2. Provision each data node

Standard headless install per [`headless-mode.md`](headless-mode.md). Make sure each is on v0.9.0 or newer (schema v2 is required for multi-node propagation).

Confirm before proceeding:

```bash
# On each data node:
sudo s-vps version    # must report v0.9+ (v0.10+ recommended)
sudo s-vps user list  # one default user; OK
```

### 3. Register the first data node

On the control:

```bash
sudo s-vps fleet add tokyo-1 --ssh-host <data-node-ip>
```

This is interactive — you'll see:

```text
Generating ed25519 keypair → /etc/stealth-vps/keys/control_to_tokyo-1.ed25519

========================================================================
Step 1/2 — Install this pubkey on root@<data-node-ip>:

    ssh-ed25519 AAAA...long-key... control_to_tokyo-1

Suggested command (run ON <data-node-ip> as root):

    echo 'ssh-ed25519 AAAA... control_to_tokyo-1' >> /root/.ssh/authorized_keys
    chmod 0600 /root/.ssh/authorized_keys

(Add any form — bare or restricted — we tighten it after the probe.)
========================================================================

Press Enter when the pubkey is installed (or Ctrl-C to abort)...
```

Open a separate terminal, SSH into the data node, paste the suggested command. Press Enter on the control.

The control then:

1. Probes `s-vps version` over SSH → must respond with v0.9+
2. Slurps `reality.state.yml` + `hysteria.state.yml` to discover the data node's per-node Reality keys
3. Rewrites the data node's `authorized_keys` entry into the restricted form:

   ```text
   command="/usr/local/bin/s-vps fleet-receive",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... control_to_tokyo-1
   ```

   After this, the control's SSH key can only trigger `s-vps fleet-receive` on the data node. No shell, no port-forward, no other commands.
4. Writes `/etc/stealth-vps/fleet/tokyo-1.yml` with the discovered metadata.

```text
✓ registered 'tokyo-1'
  fleet file: /etc/stealth-vps/fleet/tokyo-1.yml
  ssh key   : /etc/stealth-vps/keys/control_to_tokyo-1.ed25519
  next      : `s-vps fleet sync` to push the current users.index.json
```

### 4. Register more data nodes

Same workflow, different label per node:

```bash
sudo s-vps fleet add amsterdam-1 --ssh-host <amsterdam-ip>
sudo s-vps fleet add singapore-1 --ssh-host <singapore-ip>
```

For unattended bulk bootstraps (Terraform / Pulumi running many `s-vps fleet add` in sequence), pre-install the pubkey via cloud-init / user-data and pass `--yes` to skip the interactive prompt.

### 5. Sync + verify

```bash
sudo s-vps fleet list
# NODE_ID                  SSH_HOST               PORT  STATUS  LAST_SYNC
# ------------------------------------------------------------------------
# amsterdam-1              10.0.0.2               22    never   (never)
# singapore-1              10.0.0.3               22    never   (never)
# tokyo-1                  103.106.228.154        22    never   (never)

sudo s-vps fleet sync
# Syncing users.index.json to 3 node(s)...
#
# NODE_ID                  STATUS    DURATION    DETAIL
# ------------------------------------------------------------------------
# amsterdam-1              ✓ ok      342 ms      {"ok":true,"user_count":0,...}
# singapore-1              ✓ ok      512 ms      {"ok":true,"user_count":0,...}
# tokyo-1                  ✓ ok      251 ms      {"ok":true,"user_count":0,...}
```

Each data node's `users.index.json` is now the control's empty schema (the locally-seeded default client from headless setup gets clobbered — that's intentional, single-default clients are scaffolding).

### 6. Add your first multi-node user

```bash
sudo s-vps user add alice --ttl 30d
# ✓ added user 'alice'
#   reality_uuid     : 7c1f...
#   hysteria_password: 32-char-token
#   sub_token        : 43-char-token
#   sub_expires_at   : 2026-06-19T...
#
# Propagating to 3 data node(s)...
#   ✓ tokyo-1 (251ms)
#   ✓ amsterdam-1 (342ms)
#   ✓ singapore-1 (512ms)
```

The auto-sync after `user add` runs in parallel (4 workers default). Each data node receives the updated index + reloads its Xray. Alice now exists on all three nodes with the same UUID + Hysteria2 password.

Her subscription URL serves a base64-encoded text file containing **6 URIs** (2 protocols × 3 nodes), with per-node Reality public keys baked into each entry:

```text
vless://7c1f...@<tokyo-public-host>:43338?pbk=<tokyo-pubkey>&sid=<tokyo-shortid>...#stealth-vps-reality-alice-tokyo-1
vless://7c1f...@<amsterdam-public-host>:43338?pbk=<amsterdam-pubkey>&sid=<amsterdam-shortid>...#stealth-vps-reality-alice-amsterdam-1
vless://7c1f...@<singapore-public-host>:43338?pbk=<singapore-pubkey>&sid=<singapore-shortid>...#stealth-vps-reality-alice-singapore-1
hysteria2://...@<tokyo>:49440/?...#stealth-vps-hysteria2-alice-tokyo-1
hysteria2://...@<amsterdam>:49440/?...#stealth-vps-hysteria2-alice-amsterdam-1
hysteria2://...@<singapore>:49440/?...#stealth-vps-hysteria2-alice-singapore-1
```

Alice imports the subscription URL into Hiddify Next; the client probes all 6 URIs and picks the lowest-latency one. If Tokyo goes down, the client rotates to Amsterdam automatically.

## Day-2 operations

### Adding more users

Same as single-node: `sudo s-vps user add bob --ttl 90d`. Auto-sync propagates bob across the fleet.

For batch additions (10+ users at once), pass `--no-sync` to each call and follow up with one `s-vps fleet sync` at the end:

```bash
for label in bob charlie daniel eve frank; do
  sudo s-vps user add "$label" --ttl 30d --no-sync
done
sudo s-vps fleet sync
```

### Rotating credentials

`s-vps user rotate alice` regenerates alice's UUID + Hysteria pw + sub_token, preserves her created_at, and triggers fleet sync. Her old URIs are rejected by every data node within ~1s (the next reload).

### Removing a data node from the fleet

Need to decommission Singapore (e.g. the provider's pricing changed):

```bash
sudo s-vps fleet remove singapore-1
# ✓ unregistered 'singapore-1' (yaml + ssh key removed)
```

The Singapore data node keeps running with its last-pushed `users.index.json` — clients with the old subscription bundle still connect. New subscription bundles (next `s-vps user add` or `/sub <label>`) won't include Singapore. Operators decommission the box separately (`apt purge`, `shutdown -h`, billing-cancel).

Pass `--keep-key` to retain the local SSH key — useful when you're going to immediately re-register the same node with new metadata.

### Rotating an SSH key

If a per-node SSH key might be compromised:

```bash
sudo s-vps fleet rotate-key tokyo-1
```

Workflow:

1. Probe the data node via the OLD key (must work).
2. Generate a new keypair.
3. Append the new pubkey to the data node's authorized_keys via the OLD key. Both keys now valid on that node.
4. Probe via the NEW key. Must work.
5. Remove the OLD entry from authorized_keys via the NEW key. Only the new key remains.
6. Atomic-replace local `.new` files over the existing key paths.

If step 4 fails, rollback automatically: remove the new entry (via the still-working OLD key) + delete the `.new` files locally. Zero downtime; the data node never sees more than 1 valid key for more than ~1 second.

### Partial sync failures

`s-vps fleet sync` returns exit 1 when any node fails. The CLI / bot prints per-node ✓/✗ status. Failed pushes are NOT queued — re-run `s-vps fleet sync` (manually or via your monitoring's alert response) to retry.

```bash
sudo s-vps fleet sync
# NODE_ID                  STATUS    DURATION    DETAIL
# ----------------------------------------------------------------
# amsterdam-1              ✓ ok      342 ms      {"ok":true...}
# tokyo-1                  ✗ FAIL    10042 ms    ssh: connect timeout
#   (partial sync — re-run `s-vps fleet sync` to retry failed nodes)
```

The local `users.index.json` already reflects the mutation. Failed data nodes serve the previous version until the next successful sync.

### Backups

`sudo s-vps backup` on the control captures `/etc/stealth-vps/` — which now includes `fleet/*.yml` + `keys/*`. One restore = full control re-provisioning. Data nodes are reprovisionable (run install.sh, then `s-vps fleet add` from the new control). The operator's `age` identity stays off-host as always.

Daily timer + `age` recipient setup is identical to single-node:

```yaml
stealth_vps_backup_enabled: true
stealth_vps_backup_recipient: "age1abcdef..."
stealth_vps_backup_timer_enabled: true
```

## Security model

| Asset | Where | If leaked |
|---|---|---|
| `users.index.json` (UUIDs + Hy2 passwords) | Control + every data node | All clients impersonable against any node. Same blast radius as single-node v0.9. |
| Per-node Reality private key (X25519) | Only that data node | Reality handshakes to that node only. Other nodes unaffected. **Strict improvement vs sharing one key.** |
| `control_to_<node>` SSH key (private) | Control only | Attacker can rewrite that one node's `users.index.json` — equivalent to compromising the control. Does NOT grant lateral movement (per-node keys). |
| Bot token | Control only | Same exposure as single-node v0.9. |
| `age` backup identity | Operator's workstation, never on stealth-vps boxes | Same as v0.9. |

The control plane is the central failure point. Use the v0.9 `age` backup + the operator runbook in [`operations.md`](operations.md#multi-node-fleet-v0100) to rebuild a control box from scratch in <10 minutes if needed.

## Limitations at v0.10.0

- **Control HA is not built in.** One control box per fleet. Real Raft / etcd-style HA is on the v0.11+ roadmap if real operator demand surfaces. For now, the backup-restore runbook is the recovery path.
- **No bidirectional metrics aggregation.** Data nodes don't push traffic stats back to the control. Each data node's `:9102` Prometheus endpoint is scrapeable independently; operators federate at the Prometheus level.
- **Hot membership changes still require client subscription refresh.** Adding a new data node to the fleet means existing clients need to re-fetch their subscription URL to pick up the new URIs. Hiddify Next refreshes on a long-press of the profile; V2Box has a manual refresh button.
- **Per-user × per-node access matrix is not modelled.** A user is authorised on the whole fleet or revoked from the whole fleet. v0.11+ if real demand appears.
- **One control plane per operator.** Multi-tenant fleets (multiple operators sharing the same data nodes) is intentionally out of scope — that's Marzban / Hiddify-Manager territory.

See [`internal/roadmap-v0.10-multi-node.md`](internal/roadmap-v0.10-multi-node.md) for the full scope-out list + rationale.
