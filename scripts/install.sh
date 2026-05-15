#!/usr/bin/env bash
# stealth-vps one-shot installer.
#
# Bootstraps Ansible and runs the playbook locally against a pinned release tag.
# Designed for fresh Debian 12 / Ubuntu 22.04+ VPS instances.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.9/scripts/install.sh | bash
#   curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.5.9/scripts/install.sh \
#     | STEALTH_VERSION=v0.5.9 bash
#
# The URL is pinned to a release tag so the installer code you fetch matches the
# version it deploys. To install a different release, change the tag in the URL
# AND pass STEALTH_VERSION to match (otherwise the installer would fetch one
# version's bootstrap script and deploy a different version's playbook).

set -euo pipefail

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
cat <<'BANNER'
 ╔══════════════════════════════════════════════════════════════╗
 ║                                                              ║
 ║   stealth-vps · Powered by Impreza Host                      ║
 ║   github.com/imprezahost/stealth-vps                         ║
 ║   MIT licensed · privacy-focused VPS toolkit                 ║
 ║                                                              ║
 ╚══════════════════════════════════════════════════════════════╝
BANNER

# ---------------------------------------------------------------------------
# Config (override via env)
# ---------------------------------------------------------------------------
STEALTH_VERSION="${STEALTH_VERSION:-v0.5.9}"
STEALTH_REPO="${STEALTH_REPO:-https://github.com/imprezahost/stealth-vps.git}"
STEALTH_LOG_DIR="${STEALTH_LOG_DIR:-/var/log/stealth-vps}"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this installer must run as root." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "ERROR: this installer supports Debian 12 / Ubuntu 22.04+ (apt-based) only." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Install Ansible
# ---------------------------------------------------------------------------
echo "[1/3] Installing Ansible and dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -yqq --no-install-recommends ansible git python3-pip >/dev/null

# ---------------------------------------------------------------------------
# Run the playbook
# ---------------------------------------------------------------------------
echo "[2/3] Running stealth-vps playbook (version: ${STEALTH_VERSION})..."
mkdir -p "${STEALTH_LOG_DIR}"

ansible-pull \
  -U "${STEALTH_REPO}" \
  -C "${STEALTH_VERSION}" \
  -i 'localhost,' \
  -c local \
  ansible/playbooks/site.yml \
  2>&1 | tee "${STEALTH_LOG_DIR}/install-$(date +%Y%m%d-%H%M%S).log"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo "[3/3] Done. Connection details have been written to:"
echo "    /root/stealth-vps-credentials.txt"
echo
echo "Documentation: https://github.com/imprezahost/stealth-vps"
echo "Need a CN-optimized VPS to run this on? https://imprezahost.com"
echo
