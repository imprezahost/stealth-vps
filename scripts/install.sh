#!/usr/bin/env bash
# stealth-vps one-shot installer (Caminho C — interactive TUI by default).
#
# Bootstraps Ansible and runs the playbook locally against a pinned release
# tag. Designed for fresh Debian 12 / Ubuntu 22.04+ VPS instances.
#
# Two modes — chosen automatically:
#
#   1. Interactive (TUI) — when stdin AND stdout are TTYs. Whiptail prompts
#      for domain, optional services, and bot token. The fast-path lets the
#      operator press <enter> through every prompt and end up with a working
#      install on bare IP (no domain, panel + Reality + Hysteria2).
#
#   2. Env-var (headless) — when not on a TTY (e.g. cloud-init, Terraform
#      user-data, `curl | bash` piping). Reads STEALTH_* environment vars
#      with the same names the TUI would set. This mode is unchanged from
#      v0.5.x so existing IaC users don't have to re-flow their templates.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.7.1/scripts/install.sh | bash
#
# Env vars honored by both modes (TUI uses them as defaults):
#   STEALTH_VERSION              release tag to deploy (default: v0.7.1)
#   STEALTH_REPO                 git URL (default: github.com/imprezahost/stealth-vps)
#   STEALTH_DOMAIN               FQDN pointing at this host, "" = stay on IP
#   STEALTH_BOT_ENABLED          true/false (default: false)
#   STEALTH_BOT_TOKEN            BotFather token (required when BOT_ENABLED)
#   STEALTH_SUBSCRIPTION_ENABLED true/false (default: false)
#   STEALTH_SUBSCRIPTION_EXPOSE  true/false (default: false — loopback only)
#   STEALTH_HYSTERIA_ENABLED     true/false (default: true)
#   STEALTH_PANEL_ENABLED        true/false (default: true; flips to false in v0.7)
#   STEALTH_NONINTERACTIVE       set non-empty to force env-var mode even on TTY
#   STEALTH_LOG_DIR              log destination (default: /var/log/stealth-vps)
#
# The URL is pinned to a release tag so the installer code you fetch matches
# the version it deploys. scripts/release.sh bumps every self-pinned tag
# inside this file in one shot — don't edit them by hand for a release.

set -euo pipefail

# ===========================================================================
# Banner
# ===========================================================================
cat <<'BANNER'
 ╔══════════════════════════════════════════════════════════════╗
 ║                                                              ║
 ║   stealth-vps · Powered by Impreza Host                      ║
 ║   github.com/imprezahost/stealth-vps                         ║
 ║   MIT licensed · privacy-focused VPS toolkit                 ║
 ║                                                              ║
 ╚══════════════════════════════════════════════════════════════╝
BANNER

# ===========================================================================
# Config (env overrides)
# ===========================================================================
STEALTH_VERSION="${STEALTH_VERSION:-v0.7.1}"
STEALTH_REPO="${STEALTH_REPO:-https://github.com/imprezahost/stealth-vps.git}"
STEALTH_LOG_DIR="${STEALTH_LOG_DIR:-/var/log/stealth-vps}"

# Defaults for the operator-facing choices. TUI overwrites these from
# whiptail input; env-var mode uses them as-is.
STEALTH_DOMAIN="${STEALTH_DOMAIN:-}"
STEALTH_PANEL_ENABLED="${STEALTH_PANEL_ENABLED:-true}"
STEALTH_HYSTERIA_ENABLED="${STEALTH_HYSTERIA_ENABLED:-true}"
STEALTH_BOT_ENABLED="${STEALTH_BOT_ENABLED:-false}"
STEALTH_BOT_TOKEN="${STEALTH_BOT_TOKEN:-}"
STEALTH_SUBSCRIPTION_ENABLED="${STEALTH_SUBSCRIPTION_ENABLED:-false}"
STEALTH_SUBSCRIPTION_EXPOSE="${STEALTH_SUBSCRIPTION_EXPOSE:-false}"
STEALTH_TLS_EMAIL="${STEALTH_TLS_EMAIL:-}"

LIB_BASE_URL="https://raw.githubusercontent.com/imprezahost/stealth-vps/${STEALTH_VERSION}/scripts/lib"
LIB_CACHE_DIR="${LIB_CACHE_DIR:-/tmp/stealth-vps-install-lib.$$}"

# ===========================================================================
# Sanity
# ===========================================================================
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this installer must run as root." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "ERROR: this installer supports Debian 12 / Ubuntu 22.04+ (apt-based) only." >&2
  exit 1
fi

# ===========================================================================
# Mode detection
# ===========================================================================
# TUI requires both stdin and stdout on a TTY. `curl | bash` redirects
# stdin to a pipe, so it'll naturally fall through to env-var mode (which
# is what cloud-init and Terraform expect anyway).
INTERACTIVE=0
if [[ -t 0 ]] && [[ -t 1 ]] && [[ -z "${STEALTH_NONINTERACTIVE:-}" ]]; then
  INTERACTIVE=1
fi

# ===========================================================================
# Install base dependencies
# ===========================================================================
echo
echo "[1/5] Installing base dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# whiptail = TUI library, qrencode = ANSI QR rendering, dnsutils = `dig`
# for DNS pre-flight. ca-certificates + curl needed before sourcing libs.
apt-get install -yqq --no-install-recommends \
  ansible git python3-pip \
  ca-certificates curl \
  whiptail qrencode dnsutils \
  >/dev/null

# ===========================================================================
# Fetch lib helpers (pinned to same tag as this installer)
# ===========================================================================
mkdir -p "${LIB_CACHE_DIR}"
trap 'rm -rf "${LIB_CACHE_DIR}"' EXIT

for lib in dns-preflight.sh health-check.sh error-wrap.sh; do
  if ! curl -fsSL "${LIB_BASE_URL}/${lib}" -o "${LIB_CACHE_DIR}/${lib}"; then
    echo "ERROR: failed to fetch ${LIB_BASE_URL}/${lib}" >&2
    echo "       (network problem, or wrong STEALTH_VERSION='${STEALTH_VERSION}')" >&2
    exit 1
  fi
done
# shellcheck disable=SC1091
source "${LIB_CACHE_DIR}/dns-preflight.sh"
# shellcheck disable=SC1091
source "${LIB_CACHE_DIR}/health-check.sh"
# shellcheck disable=SC1091
source "${LIB_CACHE_DIR}/error-wrap.sh"

# ===========================================================================
# Interactive prompts (whiptail) — only when on a TTY
# ===========================================================================
prompt_tui() {
  local title="stealth-vps installer · ${STEALTH_VERSION}"

  whiptail --title "${title}" \
    --msgbox "Welcome.\n\nThis installer will deploy Xray-Reality + Hysteria2 + the 3X-UI panel on this VPS. Press <Tab> to move between fields, <Space> to toggle, <Enter> to continue.\n\nFast path: press <Enter> through every prompt — you'll get a working install on this VPS's public IP." \
    14 70

  # --- Domain (optional) -------------------------------------------------
  local domain_input
  domain_input=$(whiptail --title "${title}" \
    --inputbox "Domain name (optional)\n\nLeave blank to stay on the VPS's public IP. A domain gets you a Let's Encrypt cert and a panel URL like https://yourdomain.com:8443 — but you'll need to point DNS at this VPS BEFORE clicking OK. Skip for now if you're not sure; you can add a domain later with \`s-vps update\`." \
    16 70 "${STEALTH_DOMAIN}" 3>&1 1>&2 2>&3) || domain_input="${STEALTH_DOMAIN}"
  STEALTH_DOMAIN="${domain_input}"

  # If they entered a domain, ask for the ACME email.
  if [[ -n "${STEALTH_DOMAIN}" ]]; then
    local email_input
    email_input=$(whiptail --title "${title}" \
      --inputbox "Email address for Let's Encrypt\n\nReceives expiry notices and key-recovery instructions. Required when a domain is configured." \
      12 70 "${STEALTH_TLS_EMAIL}" 3>&1 1>&2 2>&3) || email_input=""
    STEALTH_TLS_EMAIL="${email_input}"
  fi

  # --- Optional services -------------------------------------------------
  # `--checklist` returns a quoted, space-separated list of the items
  # whose status is ON. Default selection: panel + hysteria on, bot + sub off.
  # Helper to map true/false → ON/OFF without an inline command substitution
  # (which shellcheck flags SC2046 for "word splitting" on the unquoted result).
  _on_off() { [[ "$1" == "true" ]] && echo ON || echo OFF; }
  local panel_default hysteria_default bot_default sub_default
  panel_default=$(_on_off "${STEALTH_PANEL_ENABLED}")
  hysteria_default=$(_on_off "${STEALTH_HYSTERIA_ENABLED}")
  bot_default=$(_on_off "${STEALTH_BOT_ENABLED}")
  sub_default=$(_on_off "${STEALTH_SUBSCRIPTION_ENABLED}")

  local selected
  selected=$(whiptail --title "${title}" \
    --checklist "Optional services\n\nSpace to toggle. Recommended: keep the first two on, leave the others off until you've verified your install." \
    16 75 6 \
      "panel"        "3X-UI web panel (recommended)"        "${panel_default}" \
      "hysteria"     "Hysteria2 (UDP, fast — recommended)"  "${hysteria_default}" \
      "bot"          "Telegram bot for ops (opt-in)"        "${bot_default}" \
      "sub"          "Subscription endpoint via Caddy"      "${sub_default}" \
    3>&1 1>&2 2>&3) || selected=""

  # Reset to false first; flip on for each selected tag.
  STEALTH_PANEL_ENABLED=false
  STEALTH_HYSTERIA_ENABLED=false
  STEALTH_BOT_ENABLED=false
  STEALTH_SUBSCRIPTION_ENABLED=false
  for tag in ${selected//\"/}; do
    case "${tag}" in
      panel)    STEALTH_PANEL_ENABLED=true ;;
      hysteria) STEALTH_HYSTERIA_ENABLED=true ;;
      bot)      STEALTH_BOT_ENABLED=true ;;
      sub)      STEALTH_SUBSCRIPTION_ENABLED=true ;;
    esac
  done

  # --- Bot token (only if bot enabled) -----------------------------------
  if [[ "${STEALTH_BOT_ENABLED}" == "true" ]]; then
    local token_input
    token_input=$(whiptail --title "${title}" \
      --inputbox "Telegram bot token\n\nGet one from https://t.me/BotFather → /newbot → copy the token.\n\nYour admin chat ID is captured automatically on first /start. Leave blank to skip bot install." \
      14 70 "${STEALTH_BOT_TOKEN}" 3>&1 1>&2 2>&3) || token_input=""
    STEALTH_BOT_TOKEN="${token_input}"
    if [[ -z "${STEALTH_BOT_TOKEN}" ]]; then
      whiptail --title "${title}" \
        --msgbox "No token provided — bot install skipped. You can enable it later with: STEALTH_BOT_TOKEN=... STEALTH_BOT_ENABLED=true s-vps update" \
        9 70 || true
      STEALTH_BOT_ENABLED=false
    fi
  fi

  # --- Subscription exposure (only if sub enabled) -----------------------
  if [[ "${STEALTH_SUBSCRIPTION_ENABLED}" == "true" ]]; then
    if whiptail --title "${title}" \
        --yesno "Expose subscription endpoint to the public internet?\n\n• YES → Caddy binds 0.0.0.0:443 (clients fetch sub URLs from anywhere — convenient but advertises the VPS).\n• NO  → Caddy binds 127.0.0.1:8443 (you fetch via SSH tunnel — stealthier, more setup).\n\nRecommended default: NO. You can flip later." \
        14 75; then
      STEALTH_SUBSCRIPTION_EXPOSE=true
    else
      STEALTH_SUBSCRIPTION_EXPOSE=false
    fi
  fi

  # --- Confirmation summary ----------------------------------------------
  local summary
  summary=$(cat <<SUMMARY
Install plan:

  release tag:        ${STEALTH_VERSION}
  domain:             ${STEALTH_DOMAIN:-<none — stay on IP>}
  ACME email:         ${STEALTH_TLS_EMAIL:-<none>}
  Reality:            on (always)
  Hysteria2:          ${STEALTH_HYSTERIA_ENABLED}
  3X-UI panel:        ${STEALTH_PANEL_ENABLED}
  Telegram bot:       ${STEALTH_BOT_ENABLED}
  Subscription:       ${STEALTH_SUBSCRIPTION_ENABLED} (expose: ${STEALTH_SUBSCRIPTION_EXPOSE})

Estimated install time: 3-6 minutes.

Proceed?
SUMMARY
)
  if ! whiptail --title "${title}" --yesno "${summary}" 20 70; then
    echo "Install cancelled by operator." >&2
    exit 130
  fi
}

if (( INTERACTIVE )); then
  echo
  echo "[2/5] Gathering install options (interactive TUI)..."
  prompt_tui
else
  echo
  echo "[2/5] Headless mode — using STEALTH_* env vars (no TUI)."
fi

# ===========================================================================
# DNS pre-flight (only when a domain is set)
# ===========================================================================
echo
echo "[3/5] DNS pre-flight..."
if [[ -n "${STEALTH_DOMAIN}" ]]; then
  echo "  Detecting this VPS's public IPv4..."
  if vps_ip=$(dns_detect_public_ipv4); then
    echo "  This VPS: ${vps_ip}"
    if ! dns_preflight_wait "${STEALTH_DOMAIN}" "${vps_ip}"; then
      cat >&2 <<EOF

DNS pre-flight failed. The installer stopped BEFORE running Ansible so
nothing has changed on this VPS.

What to do:
  1. Log into your DNS provider (Cloudflare, Route53, registrar, …).
  2. Create an A record:  ${STEALTH_DOMAIN}  →  ${vps_ip}
  3. Wait a few minutes, then re-run this installer.

Or skip the domain and stay on bare IP (re-run with STEALTH_DOMAIN="").
EOF
      exit 1
    fi
  else
    echo "  ⚠ could not detect public IPv4 — skipping DNS pre-flight."
    echo "    (egress firewall blocking ipify/icanhazip? Check connectivity.)"
  fi
else
  echo "  (no domain configured — staying on IP, no pre-flight needed)"
fi

# ===========================================================================
# Run the playbook
# ===========================================================================
echo
echo "[4/5] Running stealth-vps playbook (release: ${STEALTH_VERSION})..."
mkdir -p "${STEALTH_LOG_DIR}"
log_file="${STEALTH_LOG_DIR}/install-$(date +%Y%m%d-%H%M%S).log"

# Build -e overrides. Use a flat string (not @file) because secrets like
# STEALTH_BOT_TOKEN should not be written to disk pre-install. ansible-pull
# reads -e values verbatim — they only appear on the process command line,
# which is fine on a single-tenant VPS.
ansible_extra_vars=(
  "stealth_vps_release_tag=${STEALTH_VERSION}"
  "stealth_vps_domain=${STEALTH_DOMAIN}"
  "stealth_vps_tls_email=${STEALTH_TLS_EMAIL}"
  "stealth_vps_panel_enabled=${STEALTH_PANEL_ENABLED}"
  "stealth_vps_hysteria_enabled=${STEALTH_HYSTERIA_ENABLED}"
  "stealth_vps_bot_enabled=${STEALTH_BOT_ENABLED}"
  "stealth_vps_bot_token=${STEALTH_BOT_TOKEN}"
  "stealth_vps_subscription_enabled=${STEALTH_SUBSCRIPTION_ENABLED}"
  "stealth_vps_subscription_expose=${STEALTH_SUBSCRIPTION_EXPOSE}"
)

if ansible-pull \
      -U "${STEALTH_REPO}" \
      -C "${STEALTH_VERSION}" \
      -i 'localhost,' \
      -c local \
      -e "${ansible_extra_vars[*]}" \
      ansible/playbooks/site.yml \
      2>&1 | tee "${log_file}"; then
  ansible_rc=0
else
  ansible_rc="${PIPESTATUS[0]}"
fi

if (( ansible_rc != 0 )); then
  echo
  echo "✗ Ansible exited with code ${ansible_rc}." >&2
  error_wrap_explain "${log_file}"
  exit "${ansible_rc}"
fi

# ===========================================================================
# Post-deploy health check
# ===========================================================================
echo
echo "[5/5] Post-deploy health check..."
hc_args=()
[[ "${STEALTH_PANEL_ENABLED}" == "true" ]]        && hc_args+=(--panel)
[[ "${STEALTH_HYSTERIA_ENABLED}" == "true" ]]     && hc_args+=(--hysteria)
[[ "${STEALTH_BOT_ENABLED}" == "true" ]]          && hc_args+=(--bot)
[[ "${STEALTH_SUBSCRIPTION_ENABLED}" == "true" ]] && hc_args+=(--subscription)
[[ -n "${STEALTH_DOMAIN}" ]]                       && hc_args+=(--domain "${STEALTH_DOMAIN}")
# Panel base_path is randomised per install; pull it out of the state file
# so the HTTPS probe lands on the real URL, not bare /.
if [[ -r /etc/stealth-vps/panel.state.yml ]]; then
  panel_base_path=$(awk -F: '/^web_base_path:/ { gsub(/[[:space:]'"'"'"]/, "", $2); print $2; exit }' \
                      /etc/stealth-vps/panel.state.yml)
  [[ -n "${panel_base_path}" ]] && hc_args+=(--panel-base-path "${panel_base_path}")
fi
health_check_run "${hc_args[@]}" || true   # don't fail the install on a warn

# ===========================================================================
# QR + summary
# ===========================================================================
CREDS_FILE="/root/stealth-vps-credentials.txt"

if [[ -r "${CREDS_FILE}" ]]; then
  # The vless line in credentials.txt is indented for readability;
  # extract only the URI itself.
  reality_uri=$(grep -m1 -oE 'vless://[^[:space:]]+' "${CREDS_FILE}" 2>/dev/null || true)
  if [[ -n "${reality_uri}" ]] && command -v qrencode >/dev/null 2>&1; then
    echo
    echo "─── Reality URI (scan this QR with your client) ─────────────"
    echo
    qrencode -t ANSIUTF8 -m 2 "${reality_uri}"
    echo
  fi
fi

cat <<EOF

╔══════════════════════════════════════════════════════════════╗
║  stealth-vps is ready.                                       ║
╠══════════════════════════════════════════════════════════════╣
║  Credentials:  ${CREDS_FILE}
║  Install log:  ${log_file}
║  Operator CLI: s-vps {update|diagnose|status|version}
║
║  Next steps:
║    cat ${CREDS_FILE}      # full URIs + panel password
║    s-vps diagnose                          # re-run health check
║
║  Docs:    https://github.com/imprezahost/stealth-vps
║  CN VPS:  https://imprezahost.com
╚══════════════════════════════════════════════════════════════╝

EOF

# Bot pairing reminder — only when the bot was enabled.
if [[ "${STEALTH_BOT_ENABLED}" == "true" ]] && [[ -n "${STEALTH_BOT_TOKEN}" ]]; then
  cat <<'EOF'
┌─ Telegram bot is in pairing mode ──────────────────────────┐
│                                                            │
│  Open Telegram, find your bot, and send /start.            │
│  The first chat to message it becomes the admin.           │
│                                                            │
│  Then try:                                                 │
│    /help        list commands                              │
│    /creds       DM the credentials file                    │
│    /user list   show enabled users                         │
│                                                            │
└────────────────────────────────────────────────────────────┘

EOF
fi

# Subscription endpoint reminder — only when the sub endpoint was enabled.
if [[ "${STEALTH_SUBSCRIPTION_ENABLED}" == "true" ]]; then
  if [[ "${STEALTH_SUBSCRIPTION_EXPOSE}" == "true" ]] && [[ -n "${STEALTH_DOMAIN}" ]]; then
    sub_url="https://${STEALTH_DOMAIN}/.well-known/stealth-vps-sub/<token>"
  else
    sub_url="http://127.0.0.1:8443/.well-known/stealth-vps-sub/<token>  (via SSH tunnel)"
  fi
  cat <<EOF
┌─ Subscription endpoint ────────────────────────────────────┐
│                                                            │
│  Caddy serves per-user sub files at:                       │
│    ${sub_url}
│                                                            │
│  Get a token with the bot: /sub <label>                    │
│                                                            │
└────────────────────────────────────────────────────────────┘

EOF
fi
