# Headless mode (v0.7+)

Starting with v0.7.0, `stealth-vps` can run **without the 3X-UI panel**. This is the *headless* mode. The panel-based architecture from v0.6.x is still fully supported — pick the mode that matches your operational preferences.

> TL;DR: set `stealth_vps_panel_enabled=false` in your inventory (or via `installer.env`'s `STEALTH_PANEL_ENABLED=false`), then run `s-vps update`. The role installs a standalone Xray-core systemd unit, switches Hysteria2 to per-user auth, and the `s-vps` CLI takes over what the panel UI used to do.

## Why headless?

| Concern                 | Panel mode (v0.6.x)                            | Headless mode (v0.7+)                                    |
|-------------------------|------------------------------------------------|----------------------------------------------------------|
| Attack surface          | 3X-UI panel exposed on a high port + Caddy     | None of that — only Xray + Hysteria2 listeners           |
| User management UI      | 3X-UI web UI                                   | `s-vps user *` CLI + optional Telegram bot               |
| Hysteria2 auth          | Shared password across all clients             | Per-user `auth.userpass` map (revoke 1 user safely)      |
| Xray binary             | Bundled in 3X-UI release tarball               | Standalone upstream Xray-core (`/usr/local/bin/xray`)    |
| State                   | Panel DB + `users.index.json` (double-write)   | `users.index.json` (single source of truth)              |
| Config render pipeline  | 3X-UI panel writes to its own DB → Xray reload | `stealth_vps.reloader` renders + SIGHUPs on every change |
| Operator update path    | `s-vps update`                                 | `s-vps update` (same)                                    |
| Resource footprint      | 3X-UI panel (Go) + Caddy + Xray                | Just Xray + Hysteria2 (~20 MB RSS less)                  |

Headless is the recommended default for new installs. Panel mode stays for operators who prefer a web UI for user management.

## Components

```text
┌─────────────────────────────────────────────────────────────────┐
│ Operator                                                        │
│   ┌────────────┐    ┌──────────────────┐                        │
│   │ s-vps CLI  │    │ Telegram bot     │  (optional, opt-in)    │
│   │ user add   │    │ /user add        │                        │
│   │ user list  │    │ /sub             │                        │
│   │ migrate    │    │ /diagnose        │                        │
│   └─────┬──────┘    └────────┬─────────┘                        │
└─────────┼────────────────────┼──────────────────────────────────┘
          │                    │
          ▼                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │ HeadlessBackend (stealth_vps.backends_headless)          │
   │   add/revoke/list/show on users.index.json               │
   └────────┬─────────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────────────────┐
   │ stealth_vps.reloader.Reloader                            │
   │   - reads users.index.json + reality.state.yml +         │
   │     hysteria.state.yml                                   │
   │   - re-renders /etc/xray/config.json                     │
   │     and /etc/hysteria/config.yaml                        │
   │   - SIGHUP xray.service + hysteria-server.service        │
   └────────┬─────────────────────────────────────────────────┘
            │
            ▼
   ┌────────────────────────┐   ┌─────────────────────────────┐
   │ xray.service           │   │ hysteria-server.service     │
   │ (standalone Xray-core) │   │ (apernet/hysteria, per-user │
   │ VLESS-Reality inbound  │   │  auth.userpass mode)        │
   └────────────────────────┘   └─────────────────────────────┘
```

### `users.index.json` — the single source of truth

```json
{
  "version": 1,
  "users": {
    "alice": {
      "reality_uuid":      "00000000-0000-0000-0000-000000000001",
      "hysteria_password": "32-char-url-safe-token",
      "sub_token":         "43-char-url-safe-token",
      "created_at":        "2026-05-18T15:30:00Z",
      "enabled":           true
    }
  }
}
```

Atomic writes (`os.replace` rename) so concurrent readers (bot, metrics updater) always see a consistent snapshot.

### `stealth_vps.reloader` — Python rendering + SIGHUP

Module at `/usr/local/lib/stealth_vps/reloader.py`. Pure stdlib (no jinja2, no PyYAML). Public surface:

```python
from stealth_vps.reloader import Reloader, render_xray_config, render_hysteria_config

r = Reloader(reality_enabled=True, hysteria_enabled=True, hysteria_per_user=True, ...)
r()  # full reload — renders both configs + SIGHUPs both services
```

The role writes `/etc/stealth-vps/reloader-args.json` at the tail of every converge so the `s-vps` CLI can reconstruct the same Reloader on demand. CLI:

```bash
# Driver invoked by ansible at the end of every headless-mode converge.
python3 -m stealth_vps.reloader --reality-enabled true --hysteria-enabled true ...
```

### `s-vps` operator CLI

The bash wrapper at `/usr/local/bin/s-vps` dispatches v0.7+ verbs to `python3 -m stealth_vps.cli`:

| Verb                  | What it does                                                                |
|-----------------------|------------------------------------------------------------------------------|
| `user add LABEL`      | Generate UUID + Hysteria password + sub token; write index; SIGHUP services |
| `user revoke LABEL`   | Flip `enabled=false`; SIGHUP                                                |
| `user list [--json]`  | Print the index as a table (or NDJSON)                                      |
| `user show LABEL`     | Full record + VLESS/Hysteria2 URIs + sub URL (`--qr` for terminal QR)       |
| `reload`              | Force a full re-render. Useful after manually editing the index.            |
| `migrate from-3xui`   | Panel → headless cutover. See [migration-3xui-to-headless.md](migration-3xui-to-headless.md). |

The legacy verbs (`update`, `diagnose`, `status`, `version`) stay in the bash wrapper unchanged.

## First install in headless mode

`scripts/install.sh` prompts the question; for non-interactive installs pass an env var:

```bash
curl -sL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.7.0/scripts/install.sh \
  | STEALTH_PANEL_ENABLED=false STEALTH_DOMAIN=vpn.example.com bash
```

This runs ansible-pull with `stealth_vps_panel_enabled=false`. The role:

1. Skips `panel.yml` (no 3X-UI install).
2. Runs `reality_xray_binary.yml` → downloads upstream Xray-core to `/usr/local/bin/xray`.
3. Runs `reality_state.yml` → generates X25519 keypair + Reality state.
4. Runs `reality_xray_standalone.yml` → renders `/etc/xray/config.json`, installs hardened `xray.service` unit, starts it.
5. Runs `hysteria.yml` with per-user mode on → renders `auth.userpass` map.
6. Seeds `users.index.json` via `users_index.yml`.
7. Runs `headless_reload.yml` → re-renders configs from the now-seeded index + SIGHUPs services.

After install:

```bash
s-vps user list                 # the seeded default client
s-vps user show stealth-vps-default
s-vps user add alice            # second client, generates URIs + sub URL
```

## Day-2: adding & revoking users

```bash
$ s-vps user add bob
✓ added user 'bob'
  reality_uuid     : 7c1f...
  hysteria_password: 32-char-token
  sub_token        : 43-char-token

  vless URI       : vless://7c1f...@vpn.example.com:51820?...#stealth-vps-reality-bob
  hysteria2 URI   : hysteria2://32-char-token@vpn.example.com:36000?...#stealth-vps-hysteria2-bob
  subscription URL: https://vpn.example.com/.well-known/stealth-vps-sub/43-char-token

$ s-vps user revoke alice
✓ revoked user 'alice'

$ s-vps user list
LABEL                            STATUS     REALITY_UUID                           SUB_TOKEN      CREATED
----------------------------------------------------------------------------------------------------------------
alice                            REVOKED    11111111-...                           ab12cd34…ef56  2026-05-18T15:30:00Z
bob                              enabled    7c1f...                                f1e2d3c4…b5a6  2026-05-18T16:45:00Z
stealth-vps-default              enabled    00000000-...                           00000000…cdef  2026-05-18T14:00:00Z
```

Each mutation:

1. Mutates `users.index.json` atomically (write-tmp + `os.replace`).
2. Calls `Reloader.__call__` → re-renders `/etc/xray/config.json` and `/etc/hysteria/config.yaml`.
3. `systemctl reload xray.service hysteria-server.service` → SIGHUP, in-flight connections survive.

If step 3 fails (e.g. systemctl isn't reachable from the CLI's effective user), the index already reflects the change. Re-run `s-vps reload` after fixing the systemctl path.

## Operations notes

- **Hysteria2 per-user**: `stealth_vps_hysteria_per_user_enabled` defaults to `not panel_enabled`. Override with `STEALTH_HYSTERIA_PER_USER_ENABLED=true|false` in your inventory if you need different semantics.
- **Hysteria's `userpass` map is inline**: Hysteria2 doesn't read a separate userpass file. Every add/revoke rewrites the whole `config.yaml`. SIGHUP is cheap (sub-second) — clients with active QUIC connections stay connected.
- **`/etc/xray/config.json` and `/etc/hysteria/config.yaml`**: both rendered by the reloader from the index. Manual edits get clobbered on the next mutation or converge. To make a permanent change, override the relevant role variable.
- **The seed default client**: `stealth-vps-default` is the role-managed seed. The CLI's label validator rejects creating new labels with the `stealth-vps-` prefix to keep operator-created users out of the role's namespace.
- **Rollback**: re-enable panel mode by restoring `panel.state.yml` from the migration backup and running `s-vps update`. See [migration-3xui-to-headless.md](migration-3xui-to-headless.md) for the full procedure.

## Limits

- **Telegram bot in headless mode** lands in v0.7.1. The bot currently expects a panel API to talk to; the v0.7.0 release ships the CLI only. Operators that rely on `/user add` from Telegram should stay on panel mode until v0.7.1.
- **Per-user Reality** (multi-client `clients[]` array) works out of the box. Per-user **subscription URLs** also work via the existing `sub_token` rotation.
- **3X-UI traffic stats**: the panel's per-inbound stats are unavailable in headless mode. Hysteria2's `trafficStats:` API is — `stealth_vps_metrics_enabled=true` keeps the existing Prometheus scrape path.

## Source files

| Component                                 | Source                                                         |
|-------------------------------------------|----------------------------------------------------------------|
| Reloader                                  | `ansible/roles/stealth-vps/files/stealth_vps/reloader.py`      |
| HeadlessBackend                           | `ansible/roles/stealth-vps/files/stealth_vps/backends_headless.py` |
| CLI (`user`/`reload`/`migrate`)           | `ansible/roles/stealth-vps/files/stealth_vps/cli.py`           |
| Bash wrapper                              | `ansible/roles/stealth-vps/files/s-vps`                        |
| Headless-mode reload task                 | `ansible/roles/stealth-vps/tasks/headless_reload.yml`          |
| Xray standalone setup                     | `ansible/roles/stealth-vps/tasks/reality_xray_standalone.yml`  |
| Templates (dict-build + `to_nice_json`)   | `templates/xray-config.json.j2`, `templates/hysteria-config.yaml.j2` |
| Molecule scenario                         | `tests/molecule/headless/`                                     |
