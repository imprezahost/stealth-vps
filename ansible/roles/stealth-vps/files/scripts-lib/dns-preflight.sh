#!/usr/bin/env bash
# dns-preflight.sh — wait for a domain's A record to point at this VPS.
#
# Sourced by scripts/install.sh when the operator enables a domain.
# Defines:
#   dns_preflight_wait <domain> <expected_ipv4> [max_attempts] [interval_sec]
#       Polls public resolvers until the domain resolves to <expected_ipv4>.
#       Returns 0 on match, 1 on timeout, 2 on missing tooling.
#   dns_detect_public_ipv4
#       Echoes this host's public IPv4 (via ifconfig.me / icanhazip / api64.ipify).
#
# Why this exists: Let's Encrypt HTTP-01 + Reality SNI both need DNS to be
# *already* pointing at the VPS before we deploy. Without a pre-flight, the
# Ansible run dies 8 minutes in with an opaque acme.sh error. With it, the
# user sees a friendly countdown and a one-line remediation hint.
#
# Resolvers used: 1.1.1.1, 8.8.8.8, 9.9.9.9. We deliberately bypass the
# system resolver because some VPS providers (e.g. Vultr Tokyo) ship a
# split-horizon resolver that caches stale records for 5+ minutes.

# Intentionally no `set -euo pipefail` here — this file is sourced, and
# inheriting set flags would surprise callers that don't expect them.

_DNS_PREFLIGHT_RESOLVERS=(1.1.1.1 8.8.8.8 9.9.9.9)

# Echo this host's public IPv4. Tries three providers; first success wins.
# Returns empty + exit 1 if all fail (offline VPS, weird egress firewall).
dns_detect_public_ipv4() {
  local ip
  for endpoint in \
      "https://api.ipify.org" \
      "https://ifconfig.me/ip" \
      "https://icanhazip.com"
  do
    if ip=$(curl -fsS --max-time 5 "${endpoint}" 2>/dev/null); then
      # Strip trailing whitespace / newline and validate it looks IPv4-ish.
      ip="${ip//[$'\t\r\n ']}"
      if [[ "${ip}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        echo "${ip}"
        return 0
      fi
    fi
  done
  return 1
}

# Resolve <domain> via one public resolver. Echoes the first A record, or
# empty string on NXDOMAIN / timeout. Uses `dig` if present, falls back to
# `getent hosts` (which respects /etc/hosts and the system resolver — less
# reliable but better than nothing on minimal images).
_dns_preflight_resolve() {
  local domain="$1" resolver="$2" result=""
  if command -v dig >/dev/null 2>&1; then
    result=$(dig +short +time=3 +tries=1 A "${domain}" "@${resolver}" 2>/dev/null \
              | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
              | head -n1)
  else
    # No dig — last-resort fallback. `getent hosts` ignores the explicit
    # resolver argument, so we lose the split-horizon-bypass guarantee.
    result=$(getent hosts "${domain}" 2>/dev/null | awk '{print $1; exit}')
  fi
  echo "${result}"
}

# Wait until <domain> resolves to <expected_ipv4> on at least one resolver.
#
# Returns:
#   0 → matched within budget
#   1 → timed out
#   2 → missing tooling (no dig AND no getent)
dns_preflight_wait() {
  local domain="$1"
  local expected="$2"
  local max_attempts="${3:-40}"     # 40 × 15s = 10 min default
  local interval="${4:-15}"

  if ! command -v dig >/dev/null 2>&1 && ! command -v getent >/dev/null 2>&1; then
    echo "  ✗ Neither 'dig' nor 'getent' available — cannot pre-flight DNS." >&2
    return 2
  fi

  echo "  Waiting for ${domain} → ${expected} (up to $((max_attempts * interval / 60)) min)..."
  local attempt=0 got=""
  while (( attempt < max_attempts )); do
    for resolver in "${_DNS_PREFLIGHT_RESOLVERS[@]}"; do
      got=$(_dns_preflight_resolve "${domain}" "${resolver}")
      if [[ "${got}" == "${expected}" ]]; then
        echo "  ✓ ${domain} resolves to ${expected} (via ${resolver})"
        return 0
      fi
    done
    attempt=$((attempt + 1))
    if (( attempt < max_attempts )); then
      printf '    attempt %d/%d: got %q, want %q — retrying in %ds\n' \
        "${attempt}" "${max_attempts}" "${got:-<no answer>}" "${expected}" "${interval}"
      sleep "${interval}"
    fi
  done

  echo "  ✗ ${domain} did not resolve to ${expected} after $((max_attempts * interval / 60)) min" >&2
  echo "    Last answer: ${got:-<no answer>}" >&2
  echo "    Fix the A record at your DNS provider, then re-run the installer." >&2
  return 1
}
