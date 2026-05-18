# Migrating from 3X-UI panel mode to headless mode

Runbook for operators on a v0.6.x install who want to move to the v0.7+ headless layout. See [headless-mode.md](headless-mode.md) for the architectural overview.

## Pre-requisites

1. **On v0.6.4 or later.** Earlier versions don't have the `users.index.json` double-write — `s-vps migrate from-3xui` refuses to migrate without it.
   ```bash
   s-vps version | head -n1
   # stealth-vps:      v0.6.4   ← need this or later
   ```
   If you're on an older release, run `s-vps update v0.6.4` first.

2. **At least one user in the index.** New v0.6.x installs seed `stealth-vps-default` automatically. Confirm:
   ```bash
   s-vps user list
   # LABEL                  STATUS    ...
   # stealth-vps-default    enabled   ...
   ```
   Empty index → migration refuses (the headless-side Xray won't start with `clients[]`).

3. **A backup window.** The cutover briefly switches Xray from the 3X-UI-bundled binary to a standalone Xray-core install. Active VLESS-Reality sessions get reset (SIGTERM → SIGSTART), Hysteria2 sessions survive (port unchanged). Schedule the migration outside peak hours.

4. **Tested config.** Run `s-vps diagnose` first — clean output means you're ready. If it flags issues, fix them on panel mode before migrating; troubleshooting is easier on the familiar code path.

## One-shot cutover

The CLI you need (`s-vps migrate`) only ships in v0.7.0+. If you're on v0.6.x, bring `s-vps` up to v0.7.x first via the normal update — the migrate command lands automatically:

```bash
sudo s-vps update v0.7.2        # gets the v0.7 CLI on disk
                                # (this update still uses panel mode;
                                # nothing functional changes yet)
```

Then run the cutover:

```bash
# 1. Backup snapshot — strongly recommended. Anything goes wrong, you
#    can restore /etc/stealth-vps + /etc/x-ui + /etc/xray verbatim.
sudo tar czf /root/stealth-vps-backup-$(date +%Y%m%d).tar.gz \
    /etc/stealth-vps \
    /etc/x-ui \
    /etc/systemd/system/x-ui.service 2>/dev/null

# 2. Rename panel.state.yml + stop+disable x-ui.service (the latter
#    frees the Reality port so the standalone xray installed by the
#    next step can bind it). select_backend() now picks HeadlessBackend.
#    The rename includes a timestamp so you can `--rollback` later if
#    something goes wrong.
sudo s-vps migrate from-3xui

# 3. Re-run ansible with panel_enabled=false. The env override wins
#    over `STEALTH_PANEL_ENABLED=true` in /etc/stealth-vps/installer.env
#    on v0.7.1+ (v0.7.0 had a regression where source clobbered env;
#    if you're stuck on v0.7.0, edit installer.env first instead).
sudo STEALTH_PANEL_ENABLED=false s-vps update

# 4. Verify the new layout came up clean.
sudo s-vps diagnose
sudo s-vps user list

# 5. Optional: also stop Caddy if you weren't using it for subscription URLs.
sudo systemctl disable --now caddy.service   # only if you weren't using
                                             # Caddy for subscription URLs
```

That's the whole flow. Total downtime is the duration of step 3 (typically 60-90 seconds on a 2-core VPS). The migrate command already disabled x-ui in step 2; step 5 is just an optional Caddy stop if you weren't using subscription URLs.

## What changes on disk

| Path                                          | Before (panel mode)              | After (headless)                                            |
|-----------------------------------------------|----------------------------------|-------------------------------------------------------------|
| `/etc/stealth-vps/panel.state.yml`            | Present                          | Renamed → `panel.state.yml.before-migrate-<ts>` (kept)      |
| `/etc/stealth-vps/users.index.json`           | Mirrored from panel              | Authoritative source                                        |
| `/etc/stealth-vps/reloader-args.json`         | Absent                           | Written by `headless_reload.yml`                            |
| `/usr/local/bin/xray`                         | Absent (xray bundled w/ x-ui)    | Upstream Xray-core release binary                           |
| `/etc/xray/config.json`                       | Absent                           | Rendered single-source-of-truth Reality config              |
| `/etc/systemd/system/xray.service`            | Absent                           | Hardened standalone unit (NoNewPrivileges, ProtectSystem=strict, MemoryDenyWriteExecute, syscall filter) |
| `/etc/hysteria/config.yaml` (`auth:`)         | `type: password`                 | `type: userpass` map keyed by label                          |
| `/etc/systemd/system/x-ui.service`            | Active                           | Disabled after step 5 (file kept for rollback)              |

The panel binary at `/usr/local/x-ui/` stays installed. Nothing inside `/usr/local/x-ui` is removed; we just stop the service.

## Rollback (within the same session)

If `s-vps diagnose` after step 4 reports problems and you want to abort:

```bash
# 1. Restore panel.state.yml from the backup name created by step 2.
sudo s-vps migrate from-3xui --rollback

# 2. Re-converge with panel enabled.
sudo STEALTH_PANEL_ENABLED=true s-vps update

# 3. Restart x-ui (rollback DOESN'T re-enable it — operator's call).
sudo systemctl enable --now x-ui.service
```

If you've already passed step 5 (panel + caddy disabled) and want to rollback:

```bash
sudo systemctl enable --now x-ui.service
sudo systemctl enable --now caddy.service   # if you had it
sudo s-vps migrate from-3xui --rollback
sudo STEALTH_PANEL_ENABLED=true s-vps update
```

Rollback restores the *most recent* `before-migrate-<ts>` backup. Multiple migration attempts leave multiple backups; `--rollback` always picks the lexicographically latest (= chronologically latest).

## Common issues

### `s-vps: users.index.json missing at /etc/stealth-vps/users.index.json`

You're on a v0.5 or pre-v0.6 install. Upgrade through v0.6.x first — `ThreeXUIBackend.add/revoke` populates the index over time, but for first-install hosts the migration helper expects the index to already exist. Run `s-vps update v0.6.4` to bring it up.

### `s-vps: users.index.json has zero users`

The role's `users_index.yml` seeds `stealth-vps-default` on first install, then ThreeXUIBackend keeps adding/removing as the bot mutates. If the index is empty, something cleaned it out (operator manually deleted the file? `rm -rf /etc/stealth-vps`?). Run `s-vps update` to re-seed before migrating.

### `Re-run ansible-pull failed with: cannot stat panel.state.yml`

Step 3 ran with `STEALTH_PANEL_ENABLED=true` somehow (default in your installer.env). The role's `panel.yml` then tried to load the now-absent file. Two fixes:

1. Verify your CLI invocation: `STEALTH_PANEL_ENABLED=false s-vps update`. The env var must be on the **same** command, not exported separately for the shell.
2. Update `/etc/stealth-vps/installer.env` to `STEALTH_PANEL_ENABLED=false` so future `s-vps update` calls without the env override Just Work.

### `systemctl reload xray.service` fails after a user add

`stealth_vps.reloader` writes the new `config.json` first, *then* SIGHUPs. If the SIGHUP fails, the index already reflects the change. Run `sudo systemctl restart xray.service` to force-pick-up. The connection drop affects only Reality clients; Hysteria2 is on a separate service.

### `s-vps user list` shows users but `vless://` URI says `your.vps.example`

Set `STEALTH_DOMAIN=<your-fqdn>` in `/etc/stealth-vps/installer.env`. The URI renderer reads it as the hostname for the rendered VLESS URI. Without it, the placeholder shows up.

### Panel was using a non-default Reality remark

The role pins `stealth_vps_reality_remark='stealth-vps-reality'`. If you renamed the inbound in the panel UI, the migration helper will refuse to talk to it (`ThreeXUIBackend._get_reality_inbound` raises `Reality inbound not found`). Rename it back in the panel before migrating, then run.

## Verification checklist

After step 4, before step 5:

```bash
# Services running
sudo systemctl is-active xray.service                    # active
sudo systemctl is-active hysteria-server.service         # active (if enabled)
sudo systemctl is-active x-ui.service                    # active (still up, idle — disabled in step 5)

# Listening ports
sudo ss -tlnp '( sport = :{{ reality_port }} )'          # xray
sudo ss -ulnp '( sport = :{{ hysteria_port }} )'         # hysteria-server

# Config rendered
sudo cat /etc/xray/config.json   | jq '.inbounds[0].settings.clients[].email'
sudo cat /etc/hysteria/config.yaml | jq '.auth'           # .auth.type == "userpass"
sudo cat /etc/stealth-vps/users.index.json | jq '.users | keys'

# Reloader-args file regenerated
sudo cat /etc/stealth-vps/reloader-args.json | jq '.hysteria_per_user'
# true

# Diagnose end-to-end
sudo s-vps diagnose
```

At least one client should reconnect successfully (test on a phone client or `xray client` from another machine). Once verified, run step 5.

## Telegram bot users

The bot's `/user add` and `/sub` endpoints are panel-mode-only in v0.7.0. If your operations depend on the bot, **stay on panel mode** until v0.7.1, which ships the bot's HeadlessBackend integration. Headless-mode bot will:

1. Detect the absence of `panel.state.yml` at startup (same `select_backend()` rule the CLI uses).
2. Route `/user add` through `HeadlessBackend.add` instead of `ThreeXUIBackend.add`.
3. Wire the same reloader the CLI uses.

The state file format doesn't change between v0.7.0 and v0.7.1, so an early-adopter on v0.7.0 can upgrade to v0.7.1 in place without re-running migration.
