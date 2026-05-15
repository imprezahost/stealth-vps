# Installer UX contract

`scripts/install.sh` is the entry point new users hit. This document fixes
its observable contract — the prompts, env vars, exit codes, and recovery
paths — so we don't accidentally break the experience between releases.

If you're a contributor about to change `install.sh`, **read this first**.
If you're an operator looking for "what does this prompt mean?", skip to
the [Prompts](#prompts) and [Env vars](#env-vars) sections.

---

## Design principle

> **Fast path wins.** Pressing `<Enter>` through every prompt MUST produce a
> working install on the VPS's public IP, with no domain, no bot, no
> subscription endpoint, and no manual edits required after.

Every prompt's default has to honor this. New prompts that violate it
(e.g. a required text field with no default) need a strong justification
and an explicit override path via env var.

---

## Two modes

The installer picks its mode automatically. There is no `--interactive`
flag — interactivity is detected from the TTY state.

### Interactive (TUI)

Selected when **all three** conditions hold:

1. `[[ -t 0 ]]` — stdin is a TTY.
2. `[[ -t 1 ]]` — stdout is a TTY.
3. `${STEALTH_NONINTERACTIVE:-}` is empty.

Triggered by **downloading then running**:

```bash
curl -fsSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.9/scripts/install.sh -o install.sh
sudo bash install.sh
```

The installer uses `whiptail` for prompts (ships in `whiptail` /
`newt` package on Debian/Ubuntu, installed as a base dep by the
installer itself).

### Env-var (headless)

Selected when any of the TUI conditions fails — most commonly when stdin
is piped:

```bash
# cloud-init / Terraform / Pulumi / one-liner
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.9/scripts/install.sh | sudo bash
```

The pipe makes stdin a non-TTY, so the TUI is skipped and the installer
reads all configuration from environment variables. This mode is
**byte-compatible with v0.5.x** — existing IaC templates keep working.

To force env-var mode on a TTY (e.g. for scripted re-runs):

```bash
STEALTH_NONINTERACTIVE=1 sudo bash install.sh
```

---

## Prompts

TUI mode walks through these screens, in order:

| # | Prompt | Default | Notes |
|---|---|---|---|
| 1 | Welcome (msgbox) | — | Explains keys; press Enter to continue. |
| 2 | Domain (inputbox, optional) | empty | Blank = "stay on IP". |
| 3 | ACME email (inputbox) | empty | **Only when domain is non-empty.** Required by Let's Encrypt. |
| 4 | Optional services (checklist) | panel ✓, hysteria ✓, bot ✗, sub ✗ | Reality is always on; not in the list. |
| 5 | Bot token (inputbox) | empty | **Only when bot is checked.** Blank → bot install silently skipped. |
| 6 | Expose subscription? (yesno) | NO | **Only when subscription is checked.** NO = loopback (`127.0.0.1:8443`). |
| 7 | Summary (yesno) | YES | Operator's last chance to bail. Cancel exits with code 130. |

### Why no port prompts

Ports are auto-picked from the disjoint ranges in `defaults/main.yml`
(Reality: 40000-60000 TCP, Hysteria2: 40000-60000 UDP, panel: 30000-39999).
Operators almost never need to pin specific ports, and the conflict-aware
random pick is more reliable than asking for a number a user has no way
to validate. Override via `-e stealth_vps_reality_port=...` if needed.

---

## Env vars

These names are read by both modes. In TUI mode they populate the prompt
defaults; in env-var mode they ARE the configuration.

| Variable | Default | Effect |
|---|---|---|
| `STEALTH_VERSION` | `v0.5.9` | Release tag to deploy. `release.sh` bumps this. |
| `STEALTH_REPO` | github upstream | Git URL. Override for forks / mirrors. |
| `STEALTH_LOG_DIR` | `/var/log/stealth-vps` | Where install logs go. |
| `STEALTH_DOMAIN` | empty | FQDN that resolves to this VPS, or empty for bare-IP install. |
| `STEALTH_TLS_EMAIL` | empty | Required when `STEALTH_DOMAIN` is set. |
| `STEALTH_PANEL_ENABLED` | `true` | 3X-UI web panel. v0.7 flips default to `false`. |
| `STEALTH_HYSTERIA_ENABLED` | `true` | Hysteria2 server. |
| `STEALTH_BOT_ENABLED` | `false` | Telegram bot. Requires `STEALTH_BOT_TOKEN`. |
| `STEALTH_BOT_TOKEN` | empty | BotFather token. Without it, bot install is skipped. |
| `STEALTH_SUBSCRIPTION_ENABLED` | `false` | Caddy + subscription endpoint. |
| `STEALTH_SUBSCRIPTION_EXPOSE` | `false` | Bind subscription on `0.0.0.0:443` instead of `127.0.0.1:8443`. |
| `STEALTH_NONINTERACTIVE` | empty | Set to anything to force env-var mode on a TTY. |

After install, the non-secret subset is rendered to
`/etc/stealth-vps/installer.env` so `s-vps update` re-uses the same
choices. Secrets (`STEALTH_BOT_TOKEN`) live in their own service-specific
env files at mode 0600 and are NOT echoed into `installer.env`.

---

## Phases

The installer prints `[N/5]` markers so a stuck user can tell us where
it died:

1. **Install base dependencies** — `apt-get install ansible git python3-pip
   whiptail qrencode dnsutils ca-certificates curl`.
2. **Gather install options** — TUI prompts (interactive) or env-var read
   (headless).
3. **DNS pre-flight** — when domain is set, polls `1.1.1.1` / `8.8.8.8` /
   `9.9.9.9` for up to 10 minutes waiting for A record to match the VPS's
   detected public IP. Failure exits BEFORE running Ansible so the VPS is
   never touched.
4. **Run the playbook** — `ansible-pull` against the pinned tag.
5. **Post-deploy health check** — runs `health_check_run` from
   `scripts/lib/health-check.sh`; prints ✓/✗/⚠ per check. A failed check
   does NOT exit the installer with non-zero — the install itself
   succeeded, the operator just needs to be told that e.g. the panel didn't
   come up.

Then: the QR code for the Reality URI (if `qrencode` rendered one) and a
"next steps" summary banner.

---

## Lib helpers

The three files under `scripts/lib/` are sourced both by `install.sh` (via
`curl` at the pinned tag) and by the installed `s-vps` wrapper (from
`/usr/local/lib/stealth-vps/scripts-lib/`). Same code, same behavior.

* **`dns-preflight.sh`** — `dns_preflight_wait <domain> <ip>` polls public
  resolvers until match or timeout. Bypasses the system resolver
  deliberately (some VPS providers ship split-horizon DNS with stale
  caches).
* **`health-check.sh`** — `health_check_run` runs systemd / port / TLS
  cert / HTTPS-probe checks. Returns 0 (all ✓), 1 (any ✗), 2 (only ⚠).
* **`error-wrap.sh`** — `error_wrap_explain <log>` scans the install log
  for known failure regexes and prints a one-paragraph remediation. Add
  new patterns by appending to `_EW_KNOWN_PATTERNS`.

Keep these files **sourceable** — no `set -euo pipefail`, no side effects
at source time. Define functions only.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Install succeeded. Health check may have raised ⚠ — see the output. |
| 1 | Pre-flight or dependency error (DNS, missing apt, network). |
| 130 | Operator cancelled the summary prompt. |
| other | Ansible exited non-zero — see the log path printed before exit. |

---

## Changing the installer

Two rules that exist because we've broken them before:

1. **Don't add a required prompt without a default.** The "press Enter
   through everything" fast path is the single most important UX
   property. Any new prompt must ship with a sensible default that
   produces a working install.
2. **Don't write to `/etc/stealth-vps/` from `install.sh`.** Anything that
   lives on the deployed system is the role's job to render. The
   installer collects choices and passes them to Ansible; the role owns
   on-disk state. (`installer.env` is rendered by `cli_wrapper.yml`, not
   by `install.sh`.)

Bumping the release tag in this file is handled by `scripts/release.sh`
— don't edit `STEALTH_VERSION="${STEALTH_VERSION:-v0.5.9}"` by hand.
