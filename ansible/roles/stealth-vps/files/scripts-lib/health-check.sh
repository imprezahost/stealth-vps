#!/usr/bin/env bash
# health-check.sh — post-deploy ✓ / ✗ / ⚠ checklist.
#
# Sourced by scripts/install.sh (after ansible-pull succeeds) and by the
# `s-vps diagnose` wrapper. Each `_hc_*` helper prints one line in one of:
#   ✓ green — pass
#   ✗ red   — fail (operator action required)
#   ⚠ yellow — informational warning (works but not ideal)
#
# Exit codes from `health_check_run` summarise the worst result seen:
#   0 → all ✓
#   1 → at least one ✗
#   2 → only ⚠, no ✗
#
# Intentionally NOT `set -euo pipefail` — sourced helper.

_HC_GREEN='\033[0;32m'
_HC_RED='\033[0;31m'
_HC_YELLOW='\033[0;33m'
_HC_RESET='\033[0m'

# Suppress colour when stdout isn't a TTY (e.g. piped to a file in CI).
if ! [[ -t 1 ]]; then
  _HC_GREEN=''; _HC_RED=''; _HC_YELLOW=''; _HC_RESET=''
fi

_hc_pass() { printf "  ${_HC_GREEN}✓${_HC_RESET} %s\n" "$*"; }
_hc_fail() { printf "  ${_HC_RED}✗${_HC_RESET} %s\n" "$*"; _HC_SAW_FAIL=1; }
_hc_warn() { printf "  ${_HC_YELLOW}⚠${_HC_RESET} %s\n" "$*"; _HC_SAW_WARN=1; }

# Check a systemd unit. Pass = active, fail = anything else.
# Usage: _hc_check_unit <unit-name> [<friendly-label>]
_hc_check_unit() {
  local unit="$1" label="${2:-$1}"
  if ! systemctl list-unit-files "${unit}" >/dev/null 2>&1; then
    # Not installed — skip silently (caller controls which units to check).
    return 0
  fi
  if systemctl is-active --quiet "${unit}"; then
    _hc_pass "${label} service is active"
  else
    _hc_fail "${label} service is NOT active (try: journalctl -u ${unit} -n 50)"
  fi
}

# Check a TCP port is listening. Uses `ss` (preferred — preinstalled on
# Debian 12). Pass = listening, fail = not listening.
_hc_check_port() {
  local port="$1" label="${2:-port ${port}}"
  if ! command -v ss >/dev/null 2>&1; then
    _hc_warn "ss not available — cannot verify ${label}"
    return 0
  fi
  if ss -tunlp 2>/dev/null | awk '{print $5}' | grep -qE ":${port}$"; then
    _hc_pass "${label} listening on port ${port}"
  else
    _hc_fail "${label} NOT listening on port ${port}"
  fi
}

# Check a TLS cert's days-until-expiry. Pass ≥30 days, warn 7-30, fail <7.
# Usage: _hc_check_cert <cert-path> [<label>]
_hc_check_cert() {
  local cert="$1" label="${2:-${cert}}"
  if [[ ! -r "${cert}" ]]; then
    _hc_warn "cert not readable: ${cert} (skipping expiry check)"
    return 0
  fi
  local end_epoch now_epoch days_left
  end_epoch=$(openssl x509 -in "${cert}" -noout -enddate 2>/dev/null \
              | sed -n 's/^notAfter=//p' \
              | xargs -I{} date -d {} +%s 2>/dev/null) || end_epoch=""
  if [[ -z "${end_epoch}" ]]; then
    _hc_warn "could not parse expiry from ${cert}"
    return 0
  fi
  now_epoch=$(date +%s)
  days_left=$(( (end_epoch - now_epoch) / 86400 ))
  if   (( days_left < 7 ));  then _hc_fail "${label} expires in ${days_left}d (renewal stuck?)"
  elif (( days_left < 30 )); then _hc_warn "${label} expires in ${days_left}d"
  else                             _hc_pass "${label} valid for ${days_left}d"
  fi
}

# Probe an HTTPS endpoint with `curl -k` (we may not trust the chain when
# the cert is self-signed during early bootstrap). Pass on HTTP 2xx/3xx/4xx
# (the panel returning a 401/302 still proves it's alive), fail on connection
# refused / timeout.
_hc_check_https() {
  local url="$1" label="${2:-${url}}"
  local code
  code=$(curl -sko /dev/null --max-time 5 -w '%{http_code}' "${url}" 2>/dev/null || echo "000")
  if [[ "${code}" =~ ^[234][0-9][0-9]$ ]]; then
    _hc_pass "${label} reachable (HTTP ${code})"
  else
    _hc_fail "${label} NOT reachable (curl exit/code: ${code})"
  fi
}

# Run all checks. Caller may pass flags to skip optional components.
# Usage:
#   health_check_run \
#       [--panel] [--hysteria] [--bot] [--subscription] \
#       [--domain <fqdn>] [--reality-port <n>] [--panel-port <n>]
health_check_run() {
  _HC_SAW_FAIL=0
  _HC_SAW_WARN=0

  local check_panel=0 check_hysteria=0 check_bot=0 check_sub=0
  local domain="" reality_port="443" panel_port="8443"
  while (( $# > 0 )); do
    case "$1" in
      --panel)         check_panel=1 ;;
      --hysteria)      check_hysteria=1 ;;
      --bot)           check_bot=1 ;;
      --subscription)  check_sub=1 ;;
      --domain)        domain="$2"; shift ;;
      --reality-port)  reality_port="$2"; shift ;;
      --panel-port)    panel_port="$2"; shift ;;
      *) echo "health_check_run: unknown arg '$1'" >&2; return 64 ;;
    esac
    shift
  done

  echo "Running post-deploy health check..."

  # Always-on checks.
  _hc_check_unit xray.service "Xray (Reality)"
  _hc_check_port "${reality_port}" "Reality"

  (( check_hysteria )) && {
    _hc_check_unit hysteria-server.service "Hysteria2"
    _hc_check_port "${reality_port}" "Hysteria2 (UDP)"  # shares 443 by default
  }

  (( check_panel )) && {
    _hc_check_unit x-ui.service "3X-UI panel"
    _hc_check_https "https://127.0.0.1:${panel_port}/" "3X-UI panel (loopback)"
  }

  (( check_bot )) && _hc_check_unit stealth-vps-bot.service "Telegram bot"
  (( check_sub )) && _hc_check_unit caddy.service "Caddy (subscriptions)"

  # TLS cert — only when a real domain was configured.
  if [[ -n "${domain}" ]]; then
    _hc_check_cert "/etc/stealth-vps/certs/${domain}/fullchain.pem" "TLS cert for ${domain}"
  fi

  echo

  if (( _HC_SAW_FAIL )); then
    printf "${_HC_RED}Health check: FAIL${_HC_RESET} — see ✗ items above.\n"
    return 1
  elif (( _HC_SAW_WARN )); then
    printf "${_HC_YELLOW}Health check: PASS with warnings${_HC_RESET}\n"
    return 2
  else
    printf "${_HC_GREEN}Health check: all systems nominal${_HC_RESET}\n"
    return 0
  fi
}
