#!/usr/bin/env bash
# error-wrap.sh — translate cryptic ansible-pull failures into friendly hints.
#
# Sourced by scripts/install.sh after a failed ansible-pull run. The wrapper
# scans the log for known failure signatures and prints a one-paragraph
# explanation + remediation step. Falls back to a generic "open the log,
# share it in a GitLab issue" message when no signature matches.
#
# Adding a new pattern: append a `_ew_pattern` entry to KNOWN_PATTERNS in
# the format "regex|||friendly headline|||remediation step".
#
# Intentionally NOT `set -euo pipefail` — sourced helper.

_EW_RED='\033[0;31m'
_EW_YELLOW='\033[0;33m'
_EW_CYAN='\033[0;36m'
_EW_RESET='\033[0m'

if ! [[ -t 1 ]]; then
  _EW_RED=''; _EW_YELLOW=''; _EW_CYAN=''; _EW_RESET=''
fi

# Known failure patterns. Order matters — first match wins, so put the most
# specific patterns above the more generic ones.
#
# Each entry: "<grep regex>|||<headline>|||<remediation>"
_EW_KNOWN_PATTERNS=(
  "Could not resolve host: github\.com|||GitHub is unreachable from this VPS|||Check egress connectivity: \`curl -v https://github.com\`. If the VPS is in mainland China, GitHub may be blocked — try a Hong Kong / Singapore region."

  "Failed to validate the SSL certificate|||TLS verification failed during download|||The VPS clock may be wrong (acme/curl reject expired-looking certs). Run \`timedatectl status\` and \`chronyc tracking\`."

  "acme.sh.*Verify error|||Let's Encrypt could not reach your domain|||DNS isn't pointing at this VPS yet, or port 80 is firewalled. Verify \`dig +short <your-domain>\` matches this VPS's IP and that nothing else listens on :80."

  "Connection refused.*8443|||3X-UI panel didn't come up|||Check \`journalctl -u x-ui -n 100\`. A common cause: another service already bound :8443. Override with \`STEALTH_PANEL_PORT=18443\`."

  "is not a valid attribute for a Play|||Old Ansible version|||This installer requires ansible-core ≥ 2.14. Update via \`apt install --only-upgrade ansible\`."

  "Permission denied.*\.ansible|||~/.ansible owned by another user|||A previous run left files owned by root in an unexpected home. Try \`rm -rf ~/.ansible/tmp\` and re-run."

  "No space left on device|||Disk full|||Run \`df -h /\` — stealth-vps needs ~2 GiB free. Clean apt caches with \`apt-get clean\`."

  "Unable to lock the administration directory|||Another apt run is in progress|||Wait for the other apt/dpkg to finish (e.g. unattended-upgrades), then re-run. Check with \`fuser /var/lib/dpkg/lock-frontend\`."

  "FAILED! .*xray|||Xray-core failed to start|||Run \`journalctl -u xray -n 100\`. The most common cause is a port conflict on :443 — stop the conflicting service or change \`stealth_vps_reality_port\`."
)

# error_wrap_explain <log-file>
# Scan the log, print the first matching pattern's headline + remediation.
# If nothing matches, print a generic fallback.
error_wrap_explain() {
  local log="$1"
  if [[ ! -r "${log}" ]]; then
    printf "${_EW_YELLOW}(could not read log: %s)${_EW_RESET}\n" "${log}" >&2
    return 1
  fi

  local matched=0 entry regex headline remedy
  for entry in "${_EW_KNOWN_PATTERNS[@]}"; do
    regex="${entry%%|||*}"
    local rest="${entry#*|||}"
    headline="${rest%%|||*}"
    remedy="${rest#*|||}"
    if grep -qE "${regex}" "${log}" 2>/dev/null; then
      printf "\n${_EW_RED}╳ %s${_EW_RESET}\n" "${headline}"
      printf "${_EW_CYAN}  → %s${_EW_RESET}\n\n" "${remedy}"
      matched=1
      break
    fi
  done

  if (( ! matched )); then
    cat <<EOF

${_EW_RED}╳ The installer failed but the cause didn't match any known pattern.${_EW_RESET}
${_EW_CYAN}  → Full log: ${log}${_EW_RESET}
${_EW_CYAN}  → Please open an issue with the last ~100 lines of that log:${_EW_RESET}
${_EW_CYAN}     https://github.com/imprezahost/stealth-vps/issues/new${_EW_RESET}

EOF
  fi
}

# error_wrap_run <log-file> <command...>
# Convenience wrapper: run <command>, tee to <log-file>, and on failure call
# error_wrap_explain. Returns the command's exit code.
error_wrap_run() {
  local log="$1"; shift
  if "$@" 2>&1 | tee "${log}"; then
    return 0
  fi
  local rc="${PIPESTATUS[0]}"
  error_wrap_explain "${log}"
  return "${rc}"
}
