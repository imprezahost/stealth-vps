# Roadmap interno — v0.10.0 Multi-node

Design doc para o salto single-node → multi-node em stealth-vps. Documento interno; o público vê apenas a entrada no [`README.md`](../../README.md) e o conteúdo do `CHANGELOG.md` no release. ADRs já travadas com o operador estão marcadas **[ADR]**; perguntas em aberto estão em [Open questions](#open-questions).

## Context

O stealth-vps até v0.9 é estritamente single-host: um VPS, uma instância de Xray, uma instância de Hysteria2, uma `users.index.json`. O cliente conecta no mesmo IP/hostname para Reality e Hysteria2. Isso funciona para single-tenant ou para um cliente que aceita ter uma única região de saída — mas falha em três cenários competitivos:

1. **Region failover.** Quando a região onde a VPS roda começa a ser bloqueada (CN-tier filtering muda a cada semana), o cliente precisa migrar para outra região. Hoje o operador precisa reprovisionar do zero + redistribuir URIs.
2. **Latency-by-geo.** Cliente em Cingapura conecta numa VPS em Frankfurt e paga 150ms RTT desnecessários. Reality não tem mecanismo built-in para escolher o ponto de presença mais próximo.
3. **Tráfego em pico.** 1 VPS CAX11 (4€/mês) aguenta ~50 clientes concorrentes confortáveis em Reality+Hy2. Operadores chegando em ~30 querem horizontal scaling antes de upgrade vertical, especialmente quando alguns clientes só usam à noite.

Marzban / Hiddify-Manager têm respostas para isso (Marzban-Node, Hiddify multi-server) mas com peso de painel SQL + admin web. O caminho que escolhemos para v0.10 é simbiotico ao desenho atual: **`users.index.json` no control plane é fonte da verdade, propagado para N data nodes por SSH push, sem painel central, sem banco de dados, sem agente novo no data node**.

## Strategy at a glance

Peças centrais (ADRs locked):

- **[ADR] Push do control plane.** Reconciliação é feita pelo control box rodando `s-vps fleet sync` (cron diário ou manual). Data nodes nunca abrem conexão de volta. Vantagem: data nodes ficam puramente reativos (qualquer node novo é trivial; bootstrap = single-node install + 1 `fleet add`). Desvantagem: control é SPOF — endereçada via backup `age` do v0.9 (control state cabe num único `.tar.age`).
- **[ADR] Per-node Reality keys.** Cada data node gera seu próprio X25519 (já gera hoje — `reality_state.yml`). O control NÃO compartilha keys entre nós. Comprometer 1 data node ≠ comprometer fleet. Cost: subscription bundle precisa enumerar (pubkey, short_id, port) por nó, não pode reusar.
- **[ADR] Caminho C sem terceiros.** Sem Marzban, sem panel ETCD, sem gossip protocol. SSH + arquivos JSON + 1 binário (`s-vps`) por nó. Mantém o pitch "audit-friendly, IaC-native".

Consequências:

- **`users.index.json` continua o mesmo schema (v2 do v0.9).** Multi-node não muda a representação do usuário — só duplica para onde ela vai. Operador vê 1 `users.index.json` no control, lê com o mesmo `s-vps user *`, e o estado é eventually-consistent across data nodes.
- **Data nodes não conhecem o control.** Não há config "qual control me serve" no data node. Se o control sumir, o data node continua atendendo clientes com o `users.index.json` que tinha; só não recebe updates. Operador troca de control reapontando ele para os data nodes existentes.
- **Reconciliação é stateless do ponto de vista do control.** `fleet sync` lê `users.index.json` + `fleet/<node>.yml` por nó, faz push, e termina. Não há queue persistente, não há "pending sync to node-X". Falha = retry no próximo sync.
- **Subscription bundle agrega URIs.** `/subscriptions/<token>.txt` no control vira N×P linhas: cada usuário, para cada nó, para cada protocolo. Cliente mobile (Hiddify Next, V2Box) já faz fallback automático entre URIs do mesmo bundle.

---

## Topology

```text
┌──────────────────────────────────────────────────────────────────┐
│ Control plane                                                    │
│ /usr/local/bin/s-vps  (CLI)                                      │
│ /etc/stealth-vps/                                                │
│   users.index.json     ← operator's source of truth              │
│   fleet/               ← one file per data node                  │
│     tokyo-1.yml                                                  │
│     amsterdam-1.yml                                              │
│   keys/                ← SSH keys, one per data node             │
│     control_to_tokyo-1.ed25519                                   │
│     control_to_amsterdam-1.ed25519                               │
│   subscriptions/<token>.txt  ← agg URIs across all nodes         │
│                                                                  │
│ Optional services (same as single-node):                         │
│   stealth-vps-bot.service                                        │
│   caddy.service (subscription endpoint)                          │
│   stealth-vps-health-exporter.service                            │
│   stealth-vps-auto-update.timer                                  │
│                                                                  │
│ NOT on the control:                                              │
│   xray.service          (no Reality termination here)            │
│   hysteria-server       (no QUIC listener here)                  │
└─────────────┬────────────────────────────────────────────────────┘
              │ SSH push (parallel, per-node key)
              │ scp users.index.json + ssh s-vps reload
              ▼
┌─────────────────────────┐   ┌─────────────────────────┐   ┌──────────
│ Data node: tokyo-1      │   │ Data node: amsterdam-1  │   │ data N…
│ /usr/local/bin/s-vps    │   │ ...                     │   │
│ xray.service (Reality)  │   │                         │   │
│ hysteria-server.service │   │                         │   │
│ users.index.json        │ ← │ users.index.json        │   │
│ reality.state.yml       │   │ reality.state.yml       │   │
│ (own X25519 keys)       │   │ (own X25519 keys)       │   │
└─────────────────────────┘   └─────────────────────────┘   └──────────
```

Each data node is **exactly a v0.9 headless-mode install**. The role doesn't change: same `tasks/main.yml`, same `xray.service`, same `headless_reload.yml`. The control box runs a NEW role mode (`stealth_vps_control_enabled: true`) that disables Xray + Hysteria2 install and adds fleet-management CLI verbs.

---

## Scope (in)

- **`s-vps fleet add LABEL --ssh-host X --ssh-port Y --ssh-user Z`** — register a previously-installed data node with the control.
- **`s-vps fleet remove LABEL`** — drop a node from the fleet (does NOT touch the node itself; operator decommissions the box separately).
- **`s-vps fleet sync [--node LABEL] [--dry-run]`** — push `users.index.json` to all (or one) data nodes in parallel; trigger `s-vps reload` remotely; report per-node ✓/✗.
- **`s-vps fleet list [--json]`** — table of all registered nodes + last-sync timestamp + connection check.
- **`s-vps fleet ssh LABEL [command]`** — convenience: SSH into a node using the dedicated key.
- **`/etc/stealth-vps/fleet/<node>.yml`** — per-node metadata file (see [Schema](#state-files)).
- **SSH key per node** — `/etc/stealth-vps/keys/control_to_<node>.ed25519`, generated by `fleet add`, pushed to the node's authorized_keys via the first connection.
- **Restricted `authorized_keys` on the data node** — `command="..."` prefix locks the key to running `s-vps reload` + receiving an scp of users.index.json. No shell, no proxy, no port forwarding.
- **Multi-node subscription bundle** — `write_subscription_file` enumerates all fleet nodes; one VLESS + one Hysteria2 URI per node, suffix-tagged with `-<node>` so clients dedupe and label sensibly.
- **`stealth_vps.fleet` Python module** — pure stdlib (paramiko is NOT a dep — uses `subprocess` to call openssh-client, same pattern as `auto_update` / `backup`). Public surface: `FleetNode` dataclass, `load_fleet()`, `push_to_node()`, `sync_all()`.
- **Molecule multi-host scenario** — 1 control + 2 data containers; assert that `fleet sync` propagates a new user to both nodes' `users.index.json` + triggers their reloaders.
- **Migration path doc** in `docs/operations.md` — how to upgrade a single-node v0.9 host into a multi-node fleet without dropping clients.

## Scope (out — explicitly deferred)

- **Control plane HA.** v0.10 keeps a single control box. Operator gets a daily age-encrypted backup (v0.9) and a runbook for "restore control from backup onto a fresh VPS in <10 min." HA via Raft / etcd is v0.11+ if anyone asks.
- **Bidirectional sync / data node → control reporting.** Data nodes don't push traffic stats / connection counts back to the control. The control queries each node's `/metrics` over SSH when asked (`s-vps fleet metrics` is v0.11). For v0.10, observability is per-node-Prometheus + operator-side federation.
- **Geo-DNS / Anycast.** Client picks which fleet node to connect to. Hiddify Next / V2Box / NekoBox already do automatic latency probing across URIs in a subscription bundle — we lean on that instead of building our own DNS infrastructure.
- **Hot membership changes without reload.** Adding a node to the fleet doesn't require reloading existing nodes — they don't know about each other. But adding a node to the subscription bundle means clients need to refresh their subscription URL to pick up the new URIs. Acceptable: subscription is HTTP, refresh is one tap in the client.
- **Per-node user overrides.** A user is either authorized on the whole fleet or revoked from the whole fleet. No "alice can use tokyo-1 but not amsterdam-1." That feature is interesting (regional access control) but adds a per-user × per-node access matrix that explodes the index schema. v0.11+ if a real use case shows up.
- **Hysteria2 per-node passwords.** Hysteria2 password is per-user, not per-node — Hysteria2's `auth.userpass` map at the data node is rendered from `users.index.json` exactly like single-node mode. **A user's Hysteria2 password is the same across all fleet nodes.** This is intentional (cleaner client UX; client doesn't need to know which node it's connecting to). The blast radius of a Hysteria2 password leak is one user (the password is the same across nodes for that user, but distinct from other users).

## State files

### Control plane

| File | Mode | Owner | Written by | Purpose |
|---|---|---|---|---|
| `users.index.json` | 0660 | root:stealth-vps-bot | bot, CLI, `fleet add/remove` (no field added) | Schema v2. Same as single-node. Source of truth. |
| `fleet/<node>.yml` | 0600 | root:root | `fleet add` (per-node) | Per-node metadata (see schema below). |
| `keys/control_to_<node>.ed25519` | 0600 | root:root | `fleet add` | Dedicated SSH key. Operator never reuses their personal SSH key. |
| `keys/control_to_<node>.ed25519.pub` | 0644 | root:root | `fleet add` | Public key, written to the data node's authorized_keys on first connect. |
| `fleet.lock` | 0600 | root:root | `fleet sync` | Flock file so two concurrent `sync` invocations serialise rather than corrupt the per-node state. |
| `subscriptions/<token>.txt` | 0644 | root:caddy | bot, CLI | Now multi-node — N×P lines per user. |

### Per-node `fleet/<node>.yml` schema

```yaml
node_id: tokyo-1                # operator-supplied, [a-z0-9-]{1,32}
ssh_host: 103.106.228.154
ssh_port: 22
ssh_user: root
ssh_key_path: /etc/stealth-vps/keys/control_to_tokyo-1.ed25519
# Discovered on first `fleet add` and refreshed on every `fleet sync`:
reality_public_key: 3ajziTLzJKIN8YNUWnpl2Yli14HBIGdvnhHm6gpbM24
reality_short_id: 04285c7f
reality_port: 43338
reality_servernames:
  - www.microsoft.com
hysteria_port: 49440
hysteria_obfs_password: "..."   # rendered into per-user URIs
# Optional: public hostname different from ssh_host (e.g. when the VPS
# has a public DNS name but SSH goes through a bastion):
public_host: tokyo.example.com  # if unset, ssh_host is used
domain: ""                      # for LE cert on this node (independent per-node)
added_at: 2026-05-21T10:00:00Z
last_sync_at: 2026-05-21T10:05:00Z
last_sync_status: ok            # ok | failed | never
```

Reasoning: keeping each node in its own YAML file (vs one `fleet.yml` with a nodes dict) eliminates write contention. `fleet add tokyo-1` and `fleet add amsterdam-1` can run truly concurrently (rare, but matters for `ansible-pull` initial bootstrap of multiple new nodes).

### Data node (unchanged from v0.9 headless mode)

The data node has no awareness it's part of a fleet. Its `users.index.json` is rewritten by `fleet sync`; its `reality.state.yml` and `hysteria.state.yml` are still locally generated (per-node keys, per-node ports). No new state files on the data node side.

---

## Implementation steps (order matters)

Sequence designed so each step ships in its own MR, each tested in isolation, and a partial roll-back is safe.

### Step 1 — `stealth_vps_control_enabled` mode in the role

A new defaults flag. When `true`, the role:

- Skips `xray.yml` + `hysteria.yml` + `reality_*.yml` (no Reality termination here)
- Still installs `python_pkg.yml` (control needs `stealth_vps.fleet`)
- Still installs `cli_wrapper.yml`, `subscription.yml` (control serves the subscription endpoint)
- Still installs `bot.yml` (bot lives here)
- Creates `/etc/stealth-vps/fleet/` and `/etc/stealth-vps/keys/` (mode 0700, owner root:root)

The two modes are mutually exclusive — the role asserts `not (control_enabled and (reality_enabled or hysteria_enabled))`. A node is either a control or a data node, never both. Operators who want a single-host install for cost reasons just use v0.9-style headless mode unchanged.

### Step 2 — `stealth_vps.fleet` Python module

Pure-stdlib module under `ansible/roles/stealth-vps/files/stealth_vps/fleet.py`. Public surface:

```python
@dataclass
class FleetNode:
    node_id: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_key_path: str
    reality_public_key: str
    reality_short_id: str
    reality_port: int
    reality_servernames: list[str]
    hysteria_port: int
    hysteria_obfs_password: str
    public_host: str | None
    domain: str
    added_at: str
    last_sync_at: str | None
    last_sync_status: str

def load_fleet(fleet_dir: str = "/etc/stealth-vps/fleet") -> list[FleetNode]: ...
def save_node(node: FleetNode, fleet_dir: str = ...) -> None: ...
def remove_node(node_id: str, fleet_dir: str = ...) -> None: ...

def push_to_node(node: FleetNode, users_index_path: str,
                 *, timeout: float = 30.0, dry_run: bool = False) -> PushResult: ...

def sync_all(nodes: list[FleetNode], users_index_path: str,
             *, parallel: int = 4, dry_run: bool = False) -> list[PushResult]: ...
```

Implementation notes:

- YAML I/O without PyYAML: same hand-rolled parser style as `state.py` (one-line `key: value` plus a list section for `reality_servernames`). Keeps stdlib-only.
- `push_to_node`: shells out to `scp` + `ssh` (openssh-client; already a dep of ansible). NO paramiko, NO fabric.
- `sync_all`: uses `concurrent.futures.ThreadPoolExecutor` with `parallel` workers (default 4 — balance between speed and per-node noise budget).
- Each `PushResult` carries `(node_id, ok: bool, stdout: str, stderr: str, duration_ms: int)`.

### Step 3 — `s-vps fleet add` subcommand

Wiring:

1. Operator-facing: `s-vps fleet add tokyo-1 --ssh-host 103.106.228.154 --ssh-port 22 --ssh-user root`
2. CLI generates ed25519 keypair at `/etc/stealth-vps/keys/control_to_tokyo-1.ed25519`.
3. CLI prompts operator: "Add this public key to root@103.106.228.154's authorized_keys, then press Enter."
   ```
   ssh-ed25519 AAAA... control_to_tokyo-1
   ```
   Reason for the manual step: we don't want the control box to ever store the operator's primary SSH key. The bootstrap is one-shot and clearly visible. (Optional `--push-via-password` flag for trust-on-first-use over password auth in scripted bootstraps — explicitly noisier, requires `sshpass`.)
4. CLI does a probe SSH: `s-vps version` on the remote. Expects to see a v0.9+ version pin.
5. CLI reads `/etc/stealth-vps/reality.state.yml` + `/etc/stealth-vps/hysteria.state.yml` over SSH (with `cat`, locked-down to root via existing mode 0640 + ssh-key root login).
6. CLI writes `/etc/stealth-vps/fleet/tokyo-1.yml` with the discovered fields + `added_at: <now>` + `last_sync_status: never`.
7. CLI installs a restricted authorized_keys entry on the remote (replacing the trust-on-first-use entry) — see [Security model](#security-model).

### Step 4 — `s-vps fleet sync`

Algorithm:

```python
def cmd_fleet_sync(args):
    nodes = filter_by_label(load_fleet(), args.node)
    results = sync_all(nodes, USERS_INDEX_PATH,
                       parallel=args.parallel, dry_run=args.dry_run)
    print_table(results)
    return 0 if all(r.ok for r in results) else 1
```

Per-node push:

1. `scp -i <key> -P <port> users.index.json <user>@<host>:/etc/stealth-vps/users.index.json.new`
2. `ssh -i <key> -p <port> <user>@<host> '/usr/local/bin/s-vps fleet-receive'`
   - `s-vps fleet-receive` is a HIDDEN subcommand (not in `--help`, only callable from the locked-down authorized_keys command=). It validates that `.new` exists + parses + is schema v2 + does `os.replace(.new, real)` + invokes `Reloader()`.
3. The HIDDEN command writes its result on stdout in a 1-line JSON for the control to consume.
4. On success: control writes `last_sync_at` + `last_sync_status: ok` back into `fleet/<node>.yml`.
5. On failure: `last_sync_status: failed`. Reported in the table. Next `fleet sync` will retry — no separate queue.

### Step 5 — Multi-node URI builder + subscription bundle

`urivider.build_vless_uri()` and `build_hysteria2_uri()` are per-node already (they take host + port + pubkey args). What changes: the **subscription writer**.

Before (v0.9, single-node):
```text
vless://<uuid>@<host>:<port>?...#stealth-vps-reality-alice
hysteria2://<pw>@<host>:<port>?...#stealth-vps-hysteria2-alice
```

After (v0.10, multi-node — when the fleet has nodes):
```text
vless://<uuid>@<tokyo-host>:<tokyo-port>?pbk=<tokyo-pubkey>...#stealth-vps-reality-alice-tokyo-1
vless://<uuid>@<amsterdam-host>:<amsterdam-port>?pbk=<amsterdam-pubkey>...#stealth-vps-reality-alice-amsterdam-1
hysteria2://<pw>@<tokyo-host>:<tokyo-port>?...#stealth-vps-hysteria2-alice-tokyo-1
hysteria2://<pw>@<amsterdam-host>:<amsterdam-port>?...#stealth-vps-hysteria2-alice-amsterdam-1
```

Same UUID + same Hysteria2 password (per-user, not per-node — see Scope-out). Different `pbk` + `sid` + remark suffix per node.

Hiddify Next / V2Box / NekoBox treat the bundle as N profiles; their "automatic" mode probes all and picks the lowest-latency. Manual select also works. Same UX as a "multi-server subscription" from Marzban / Hiddify-Manager.

### Step 6 — Bot + CLI surface unchanged for user verbs

`s-vps user add alice --ttl 30d` still works on the **control box** — the index is local, then `fleet sync` propagates. Bot's `/user add` same flow.

We add `s-vps user add --no-sync` to skip the post-mutation sync (operator wants to batch 10 user adds then one sync at the end). Default is to sync after every mutation, matching the single-node "every mutation triggers reload" semantics.

### Step 7 — Migration path: single-node → multi-node

Single-host v0.9 install in Tokyo. Operator wants to add Amsterdam as a second node.

1. Provision Amsterdam VPS, install stealth-vps v0.10 in **headless mode** (same as v0.9 today). `s-vps user list` on Amsterdam shows 1 default user.
2. Provision new control box (or reuse a dev box / laptop with `stealth_vps_control_enabled=true`).
3. On the control: `s-vps fleet add tokyo-1 --ssh-host <tokyo-ip>` → control discovers Tokyo's Reality state, adds Tokyo to fleet.
4. On the control: `s-vps fleet add amsterdam-1 --ssh-host <amsterdam-ip>` → same.
5. On the control: `s-vps fleet sync` — Tokyo and Amsterdam both receive the (currently empty-of-real-users) `users.index.json`. Their existing per-node default users are clobbered (acceptable — they were never given to a real client).
6. On the control: `s-vps user add alice --ttl 30d` → users.index.json now has alice. Auto-sync (or manual sync) propagates her to both nodes.
7. Existing clients (if any) on the original Tokyo box: **their URIs still work** until they refresh their subscription URL. Reality keys haven't rotated. Hysteria2 passwords haven't rotated. Once they refresh the sub URL, they pick up Amsterdam too.

Zero-downtime in the steady-state path. The only window where clients see a brief Xray restart is when `fleet sync` first runs on a node (the very first `users.index.json` arriving from the control replaces the local one, which triggers reload — exactly like `s-vps user add` does today).

### Step 8 — Tests

- **Molecule scenario `multi-node`**: spin up 3 Docker containers (1 control + 2 data). Run the role with `control_enabled=true` on the first and headless mode on the others. Use Ansible to:
  - `s-vps fleet add data-1 --ssh-host <data-1-ip>` (with a fake SSH key pre-installed on data-1 to skip the prompt)
  - `s-vps fleet add data-2 --ssh-host <data-2-ip>`
  - `s-vps user add alice` on the control
  - `s-vps fleet sync`
  - Assert `users.index.json` on data-1 and data-2 both contain alice with the same UUID + Hysteria2 password.
  - Assert the subscription file at `/var/lib/stealth-vps/subscriptions/<alice's token>.txt` on the control contains 4 URIs (2 protocols × 2 nodes).
- **Pytest cases for `stealth_vps.fleet`**:
  - YAML round-trip (load → mutate → save → reload)
  - `push_to_node` mocked at the subprocess level (assert correct `scp` + `ssh` argv)
  - `sync_all` parallelism: 4 nodes finish in approximately `max(per_node_duration)`, not `sum()`
  - Restricted-command authorized_keys generation
  - Multi-node subscription bundle: N×P lines, per-node pubkey baked in

Target: +40-50 new pytest cases. Brings the pkg total to ~360 from v0.9's 313.

### Step 9 — Docs

- `docs/internal/roadmap-v0.10-multi-node.md` ← **this doc**
- `docs/multi-node.md` — public-facing operator guide (parallel to `docs/headless-mode.md`)
- `docs/operations.md` — add "Promoting a single-node install to a fleet" section
- `docs/architecture.md` — add multi-node topology block to the existing ASCII diagram
- `README.md` — bump roadmap row to "shipped", `README.zh-CN.md` mirror
- `CHANGELOG.md` — `[0.10.0]` section

---

## Security model

The threat model expands from "what can compromise a single host" to "what's the blast radius if one host is compromised."

| Asset | Where stored | Blast radius if leaked |
|---|---|---|
| `users.index.json` (UUIDs + Hy2 passwords) | Control + every data node | All clients can be impersonated against any fleet node. **Same blast as single-node v0.9** — multi-node doesn't make this worse. |
| Per-node Reality private key (X25519) | Only on that node | Reality handshakes to that node only. Other nodes unaffected. **Strict improvement vs sharing keys.** |
| SSH key `control_to_<node>` (private half) | Only on control | Attacker with the control's SSH key can write a malicious `users.index.json` to that one node — equivalent to compromising the control. Doesn't grant access to other nodes (per-node keys). |
| Restricted-command authorized_keys (public half) | On each data node | Attacker who gains root on a data node can read the control's pubkey but not impersonate the control elsewhere. Per-node SSH keys means lateral movement requires compromising the control directly. |
| Bot token | Control only | Same as v0.9. |
| Operator's `age` identity (backup decrypt) | Off-host (operator workstation) | Same as v0.9. Backup encrypted to operator pubkey; secret never on stealth-vps boxes. |

### SSH key isolation

The control generates **one ed25519 keypair per data node**. Reasoning:

- Operator never reuses their personal SSH key for fleet management. Personal key compromise ≠ fleet compromise.
- Each data node sees only the public key for its own slot in the fleet. A data node can't replay control commands to another node.
- Rotating one node's key (after suspected compromise) is one `fleet rotate-key <node>` invocation, not a fleet-wide reissue.

### Restricted command on the data node

The control's pubkey on each data node's `/root/.ssh/authorized_keys` is prefixed with:

```text
command="/usr/local/bin/s-vps fleet-receive",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,from="<control-ip>" ssh-ed25519 AAAA... control_to_tokyo-1
```

`s-vps fleet-receive` reads stdin (the new `users.index.json`), validates schema v2, atomic-replaces `/etc/stealth-vps/users.index.json`, calls `Reloader()`. Nothing else. An attacker with the control's SSH key cannot get a shell on the data node, cannot port-forward, cannot read other state files. `from=` clause optionally restricts the source IP — operators on a fixed control IP should enable it; ephemeral control boxes leave it off.

### Backup scope

The v0.9 `s-vps backup` on the control captures `/etc/stealth-vps` (which now includes `fleet/*.yml` and `keys/*`). One restore = full control re-provisioning. Data nodes can be wiped and reprovisioned with single-node installer; their state is reconstructable from the control.

---

## Backwards compatibility

Single-node v0.9 hosts upgrading to v0.10:

- `s-vps update v0.10.0` is a no-op feature-wise. `stealth_vps_control_enabled` defaults to false, fleet/ dir is not created, fleet verbs not surfaced. Identical user experience.
- A single-node host can run `s-vps update` indefinitely without ever going multi-node.
- The `users.index.json` schema doesn't change for v0.10 (still v2). Multi-node is a transport story, not a schema story.

If an operator later decides to go multi-node, they provision a new control box and add the existing host as a data node — see [Migration path](#step-7--migration-path-single-node--multi-node).

---

## Decisions (locked 2026-05-20)

All 8 open questions resolved with the operator. The defaults proposed in the original draft were accepted; recorded here for the implementing dev.

1. **SSH key generation:** shell out to `ssh-keygen -t ed25519 -N "" -f <path>`. No stdlib `cryptography` dep added.

2. **First-bootstrap SSH auth:** prompt-then-paste-pubkey (manual one-time step). The `--bootstrap-via-password` flag is NOT shipped in v0.10.0 — operators scripting bulk bootstrap pre-stage the pubkey via cloud-init / Terraform user-data.

3. **`fleet sync` parallelism:** 4 workers default. Operators can override per invocation via `--parallel N`.

4. **`last_sync_at` granularity:** ISO 8601 UTC (matches `users.index.json` + `sub_expires_at` + every other timestamp in the project).

5. **Partial sync failure:** exit code 1 + per-node status table. Next mutation (or manual `fleet sync`) retries. No persistent "pending sync" queue.

6. **Bot `/user add` from Telegram:** synchronous with 30s timeout. Operator gets immediate feedback; partial failures get an actionable message ("sync to <node> failed — re-run `s-vps fleet sync`").

7. **Subscription bundle remark labels:** suffix with `-<node_id>`. Clients see `stealth-vps-reality-alice-tokyo-1`, `stealth-vps-reality-alice-amsterdam-1`. Alphabetic client-side sort; operator labels nodes so the alphabetic order matches their preferred-priority order.

8. **`fleet rotate-key <node>`:** ships in v0.10.0 (not deferred to a patch). ~80 LOC + tests.

---

## Out-of-scope (deferred to v0.11+)

| Want | Why deferred |
|---|---|
| Control HA / failover | Backup story (v0.9) is "good enough" for v0.10. Real HA needs Raft, etcd, gossip — that's a separate sprint and rewrites the bootstrap story. |
| Bidirectional metrics aggregation | Per-node `:9102` is scrapeable from the control already (or from a separate Prometheus). `s-vps fleet metrics` (single command aggregating all nodes) is a v0.11 convenience. |
| Hot membership without client re-subscription | Inherent to subscription-URL model. v0.11 could add a webhook-style notification, but that's a client-side feature too (Hiddify supports it; V2Box doesn't reliably). |
| Per-user × per-node access matrix | Real demand unclear. Wait for an operator to ask. |
| Geo-DNS / Anycast on the operator's domain | Out of scope of stealth-vps — operator handles DNS with their existing tooling (Cloudflare, Route53, etc). |
| Bot multi-tenant (multiple operators per fleet) | Single-operator pitch is intentional. Marzban does this; we explicitly don't. |
| Cross-region replication of subscription endpoint | Subscription endpoint is HTTP; clients hit it once per refresh. Failure mode = client uses last cached subscription. No replication needed for v0.10. |

---

## Sequencing / timeline (informal estimate)

Solo dev, weeknight pace:

- Step 1 (control mode flag) — 1 day
- Step 2 (`fleet` module + unit tests) — 3-4 days
- Step 3 (`fleet add` CLI) — 2 days
- Step 4 (`fleet sync` + `fleet-receive` hidden cmd + restricted authorized_keys) — 3 days
- Step 5 (multi-node URI builder + sub bundle) — 1 day
- Step 6 (bot + CLI integration, no-sync flag) — 1 day
- Step 7 (migration doc) — 0.5 day
- Step 8 (molecule + pytest) — 3 days
- Step 9 (docs + release) — 1 day

**Total: ~15 dev-days.** Aiming for v0.10.0 cut by 2026-06-15 if work starts immediately after this doc is approved.

---

## What signals success

After v0.10.0 ships and is smoke-tested on a Tokyo+Amsterdam fleet:

- **Operator workflow unchanged in 90% of cases.** `s-vps user add alice` from a shell on the control still works in <2 seconds. Bot's `/user add` still works. Subscription URL still works (just longer file now).
- **`s-vps fleet sync` reports ✓ ✓ in green on a 2-node fleet in <10 seconds wall-clock.**
- **A client refreshing their subscription URL sees 4 entries (2 protocols × 2 nodes) and Hiddify Next picks the lower-RTT one automatically.**
- **Killing the Amsterdam VPS (provider-side) doesn't break Tokyo clients.** Subscription bundle still has both entries; Hiddify just rotates to Tokyo. Operator can `fleet remove amsterdam-1` when convenient.
- **332+ → ~360 automated tests.** No regressions in the single-node test suite.
- **Two real operators (me + one beta tester) running multi-node in production for 2 weeks before announcing.**
