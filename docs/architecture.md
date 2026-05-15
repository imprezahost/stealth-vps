# Architecture

## Overview

`stealth-vps` is structured as two cooperating Ansible roles plus a thin installer / cloud-init layer. The installer chooses one of three entry points; both roles run, then the operator interface (`s-vps` CLI + optional Telegram bot) takes over for day-2:

```text
┌────────────────────────────────────────────────────────────────────┐
│                        Entry point (one of)                        │
│  scripts/install.sh    cloud-init/stealth-vps.yaml    ansible-pull │
│      (TUI or env)         (Terraform user-data)        (--tags X)  │
└────────────────────────────┬───────────────────────────────────────┘
                             │   ansible-pull / ansible-playbook
                             ▼
                ┌─────────────────────────┐
                │ stealth-hardening role  │   SSH, fail2ban, UFW,
                │ (Ansible)               │   unattended-upgrades,
                │                         │   sysctl/BBR, Spamhaus
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ stealth-vps role        │   Xray-core (Reality, inside x-ui)
                │ (Ansible)               │   Hysteria2, 3X-UI panel,
                │                         │   prometheus-node-exporter,
                │                         │   stealth-vps-metrics-update timer,
                │                         │   shared stealth_vps Python pkg,
                │                         │   /usr/local/bin/s-vps CLI,
                │                         │   users.index.json schema v1
                └────────────┬────────────┘
                             │
                ┌────────────┴───────────────────┐
                │ Opt-in (v0.6+)                 │
                ▼                                ▼
   ┌─────────────────────────┐      ┌─────────────────────────┐
   │ stealth-vps-bot.service │      │ caddy.service           │
   │ python-telegram-bot     │      │ /.well-known/stealth-   │
   │ /user, /sub, /diagnose  │      │  vps-sub/<token>        │
   │ pair on first /start    │      │ loopback :8443 default  │
   └────────────┬────────────┘      └────────────┬────────────┘
                │  reads panel state                │
                │  writes users.index.json          │  serves files
                │  writes subscription files        │  from disk
                └────────────┬──────────────────────┘
                             ▼
                ┌─────────────────────────┐
                │ /etc/stealth-vps/       │   panel.state.yml
                │ (operator state dir)    │   reality.state.yml
                │                         │   hysteria.state.yml
                │                         │   users.index.json
                │                         │   version, installer.env
                │                         │   bot.env (0640)
                └─────────────────────────┘
```

Each role is independently usable. You can apply `stealth-hardening` to any VPS regardless of whether you want the proxy stack, and you can apply `stealth-vps` to a VPS already hardened by other tooling.

The opt-in services (`stealth-vps-bot`, `caddy`) only get installed when the corresponding feature flag is set (`stealth_vps_bot_enabled` / `stealth_vps_subscription_enabled`). Without them, the v0.6 install is byte-identical to v0.5 plus the `s-vps` CLI and `users.index.json`.

## Why separate roles?

So you can reuse `stealth-hardening` on infrastructure that isn't a proxy VPS — your monitoring host, your build server, anything where the SSH/fail2ban/UFW baseline applies. The split mirrors how the team uses it internally.

## Operator surface (v0.6+)

Three layers, all reading from the same on-disk state:

1. **`s-vps` shell CLI** at `/usr/local/bin/s-vps`. Pure bash. Verbs: `update` (re-run ansible-pull at the pinned tag), `diagnose` (✓/✗/⚠ health check), `status`, `version`, `help`. Reads `/etc/stealth-vps/installer.env` for non-secret choices and `/etc/stealth-vps/bot.env` for the bot token on re-runs.
2. **Shared `stealth_vps` Python pkg** at `/usr/local/lib/stealth_vps/`. Pure stdlib. Modules: `state` (atomic users.index.json I/O), `threex_client` (3X-UI REST via urllib + cookiejar), `backends` (`UserBackend` ABC + `ThreeXUIBackend` impl with double-write reconcile), `subscription` (base64 URI rendering, atomic file writes), `urivider` (URI builders for vless:// and hysteria2://). Imported by the metrics updater, the Telegram bot, and (v0.7+) the full Python `s-vps` CLI.
3. **Telegram bot** (opt-in) — `python-telegram-bot` under a hardened systemd unit (`ProtectSystem=strict`, `NoNewPrivileges`, `MemoryDenyWriteExecute`, syscall filter). Pairs on first `/start`. Same `UserBackend` interface as the future CLI, so bot and CLI never diverge.

## State files

Every artifact the role remembers between runs lives under `/etc/stealth-vps/`. Some are role-managed (regenerated when deleted), some are operator-editable, all are version-pinned by `installer.env`:

| File | Mode | Owner | Written by | Purpose |
|---|---|---|---|---|
| `version` | 0644 | root | cli_wrapper.yml | Pinned release tag, read by `s-vps update` |
| `installer.env` | 0644 | root | cli_wrapper.yml | Operator's install choices (domain, optional services). Sourced by `s-vps`. |
| `panel.state.yml` | 0640 | root:bot | panel.yml | 3X-UI port + username + password + base path |
| `reality.state.yml` | 0640 | root:bot | reality_state.yml | X25519 keypair + default client UUID + port |
| `hysteria.state.yml` | 0640 | root:bot | hysteria.yml | Hysteria2 port + auth password + obfs password |
| `users.index.json` | 0660 | root:bot | users_index.yml, then bot | Operator's source-of-truth: who is authorised (schema v1) |
| `bot.env` | 0640 | root:bot | bot.yml | Bot token + admin IDs + per-protocol URI params |
| `tls/` | 0711 | root | tls.yml | Symlinks into acme.sh's cert store (when `stealth_vps_domain` is set) |

The group `stealth-vps-bot` exists only when the bot is enabled; otherwise these files stay `root:root`.

## v0.6 → v0.7 migration anchor

The structural reason `users.index.json` exists *now* (in panel mode) is to make v0.7 headless mode a flag flip, not a rewrite.

In v0.6 the bot and CLI do a **double-write**: every `/user add` calls the 3X-UI panel API first (panel is the runtime fact-of-record), then writes the row to `users.index.json` (operator's portable record). The two stay reconciled because the panel call is reconciled-via-relist before the index write.

In v0.7 the bot and CLI will use a different `UserBackend` implementation — `HeadlessBackend` — that renders Xray's `config.json` directly from the index. The bot and CLI code paths are unchanged; only the backend swap moves. Same `UserBackend.add()` / `.list()` / `.revoke()` interface, different I/O strategy underneath.

That's the whole point of the ABC in [`files/python-pkg/backends.py`](../ansible/roles/stealth-vps/files/python-pkg/backends.py): operator surface (bot commands, sub URLs, `s-vps user add`) stays byte-identical across the panel→headless transition.

## Component choices

- **Xray-core for Reality** — current best-in-class active-probing-resistant transport. See [XTLS/Xray-core](https://github.com/XTLS/Xray-core).
- **Hysteria2 in parallel** — different transport profile (QUIC, throughput-first) that fails in different network conditions than Reality. Running both gives clients automatic fallback.
- **3X-UI for the panel** — community standard, multi-user, traffic accounting. We add a working fail2ban filter for it that upstream issues have not resolved.
- **fail2ban over CrowdSec / WAF** — boring, predictable, no telemetry.

## What this is *not*

- Not a Tor replacement. Reality + Hysteria2 resist active probing and traffic-classification, but the server still knows who you are. Use Tor for anonymity.
- Not a multi-tenant reseller platform. For that, look at [Marzban](https://github.com/Gozargah/Marzban) or [PasarGuard/panel](https://github.com/PasarGuard/panel).
- Not a one-click "everything" panel. It's a deliberate, auditable subset.
