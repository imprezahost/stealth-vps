#!/usr/bin/env bash
# stealth-vps release version bumper.
#
# Bumps the project version across every self-pinned file in one shot,
# so a release cut never misses an entry point and ships a deploy that
# fetches the previous tag's playbook (which has historically caused
# "I installed v0.5.4 by accident" bugs — see v0.4.2 hotfix).
#
# Usage:
#   scripts/release.sh <old> <new>             # apply the bump
#   scripts/release.sh --dry-run <old> <new>   # show what would change, no writes
#
# Example:
#   scripts/release.sh v0.5.8 v0.6.0
#
# Versions must match  vMAJOR.MINOR.PATCH[-prerelease]  — the same regex the
# Terraform module and Pulumi builder validate against.
#
# What this script bumps (auto, blunt sed across the file):
#   - scripts/install.sh, cloud-init/stealth-vps.yaml
#   - docs/terraform.md, terraform/README.md
#   - terraform/modules/stealth-vps/{variables.tf,README.md}
#   - terraform/examples/*/{variables.tf,terraform.tfvars.example}  (5 providers)
#   - pulumi/stealth-vps/{src/index.ts,README.md}
#   - pulumi/examples/hetzner/{index.ts,README.md,Pulumi.dev.yaml.example}
#
# What this script does NOT touch (manual edits expected each release):
#   - CHANGELOG.md             — write a new section by hand
#   - README.md                — append roadmap-table row + bump status banner
#   - README.zh-CN.md          — mirror the README.md edits
#   - pulumi/README.md         — "Limitations at vX.Y.Z" section header is a
#                                judgment call; review whether limitations still
#                                apply at the new version
#
# The script prints a reminder for the manual files at the end.

set -euo pipefail

# --- Args ----------------------------------------------------------------

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

if [[ $# -ne 2 ]]; then
  cat >&2 <<EOF
Usage: $0 [--dry-run] <old-version> <new-version>

Example: $0 v0.5.8 v0.6.0
EOF
  exit 2
fi

OLD_VERSION="$1"
NEW_VERSION="$2"

VERSION_RE='^v[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$'
for ver in "$OLD_VERSION" "$NEW_VERSION"; do
  if [[ ! "$ver" =~ $VERSION_RE ]]; then
    echo "ERROR: '$ver' is not a SemVer tag like 'v0.6.0' or 'v0.6.0-rc.1'." >&2
    exit 2
  fi
done

if [[ "$OLD_VERSION" == "$NEW_VERSION" ]]; then
  echo "ERROR: old and new versions are identical ($OLD_VERSION)." >&2
  exit 2
fi

# --- Locate repo root ----------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- Auto-bump file allowlist --------------------------------------------
#
# Every entry here MUST be a file where a blunt `sed s|<old>|<new>|g` is
# safe — i.e., the only occurrences of the old version are the
# self-pinning ones, NOT historical references (which is why CHANGELOG.md
# and README.md are excluded; their old-version mentions are part of the
# roadmap history and must not rotate).

AUTO_FILES=(
  scripts/install.sh
  cloud-init/stealth-vps.yaml
  docs/terraform.md
  docs/installer-ux.md
  terraform/README.md
  terraform/modules/stealth-vps/variables.tf
  terraform/modules/stealth-vps/README.md
  terraform/examples/hetzner/variables.tf
  terraform/examples/hetzner/terraform.tfvars.example
  terraform/examples/aws/variables.tf
  terraform/examples/aws/terraform.tfvars.example
  terraform/examples/digitalocean/variables.tf
  terraform/examples/digitalocean/terraform.tfvars.example
  terraform/examples/vultr/variables.tf
  terraform/examples/vultr/terraform.tfvars.example
  terraform/examples/proxmox/variables.tf
  terraform/examples/proxmox/terraform.tfvars.example
  pulumi/stealth-vps/src/index.ts
  pulumi/stealth-vps/README.md
  pulumi/examples/hetzner/index.ts
  pulumi/examples/hetzner/README.md
  pulumi/examples/hetzner/Pulumi.dev.yaml.example
)

MANUAL_FILES=(
  CHANGELOG.md
  README.md
  README.zh-CN.md
  pulumi/README.md
)

# --- Partial-bump file allowlist -----------------------------------------
#
# Files where a blanket sed across the whole file would clobber historical
# references (roadmap rows, "what shipped in v0.X.Y" prose) but where some
# *specific* lines DO need to track the latest release — install URLs,
# Terraform/Pulumi module refs, env-var examples. For these we apply sed
# only on lines that match one of the PARTIAL_PATTERNS regexes.
#
# Symptom this avoids: "v0.6.2 ships, but the README install command on
# the homepage still says v0.6.1". Happened in v0.6.2 → fixed in v0.6.3.

PARTIAL_FILES=(
  README.md
  README.zh-CN.md
)

# sed address regexes — a line that matches ANY of these gets its
# OLD_VERSION → NEW_VERSION token bumped. Roadmap rows like
# `| v0.6.1 | ... | shipped 2026-05-15 |` don't match any pattern,
# so they stay as historical fact.
PARTIAL_PATTERNS=(
  'raw\.githubusercontent\.com.*scripts/install\.sh'
  '\?ref=v[0-9]'
  'stealth_version[[:space:]]*='
  '"v[0-9]+\.[0-9]+\.[0-9]+"'
  'STEALTH_VERSION=v[0-9]'
)

# --- Sanity: all listed files exist --------------------------------------

missing=()
for f in "${AUTO_FILES[@]}" "${MANUAL_FILES[@]}"; do
  [[ -f "$f" ]] || missing+=("$f")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: missing files (release.sh is out of date with the repo):" >&2
  printf '  %s\n' "${missing[@]}" >&2
  echo "Update the AUTO_FILES / MANUAL_FILES arrays in scripts/release.sh." >&2
  exit 1
fi

# --- Run the bump --------------------------------------------------------

echo "Bumping ${OLD_VERSION} → ${NEW_VERSION} across ${#AUTO_FILES[@]} auto-files..."
if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "(dry-run mode — no files will be written)"
fi
echo

# Detect sed flavor for in-place editing. GNU sed: -i. BSD sed (macOS): -i ''.
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(sed -i)
else
  SED_INPLACE=(sed -i '')
fi

total_matches=0
files_changed=0
files_skipped=0
for f in "${AUTO_FILES[@]}"; do
  # grep -c returns the count; -F = fixed string (no regex). The `|| true`
  # keeps `set -e` happy when grep returns 1 (zero matches).
  count=$(grep -c -F "${OLD_VERSION}" "$f" 2>/dev/null || true)
  if [[ "${count}" -eq 0 ]]; then
    printf '  %-58s  (no match — skipping)\n' "$f"
    files_skipped=$((files_skipped + 1))
    continue
  fi

  if [[ ${DRY_RUN} -eq 1 ]]; then
    printf '  %-58s  %d match(es)\n' "$f" "${count}"
  else
    # `|` as sed delimiter — the version strings themselves contain `.`
    # but not `|`, so this is safe and slightly more readable than `/`.
    "${SED_INPLACE[@]}" "s|${OLD_VERSION}|${NEW_VERSION}|g" "$f"
    printf '  %-58s  %d match(es) → bumped\n' "$f" "${count}"
  fi
  total_matches=$((total_matches + count))
  files_changed=$((files_changed + 1))
done

echo
echo "Auto-bump summary: ${files_changed} file(s) changed, ${files_skipped} skipped, ${total_matches} occurrence(s)."

# --- Partial bump (install URLs etc.) on README files -------------------

echo
echo "Selective bump on ${#PARTIAL_FILES[@]} README-style file(s)..."
partial_changed=0
partial_total=0
for f in "${PARTIAL_FILES[@]}"; do
  [[ -f "$f" ]] || { echo "  ${f}: missing, skipped"; continue; }
  this_file_count=0
  for p in "${PARTIAL_PATTERNS[@]}"; do
    # Count matching lines that ALSO contain OLD_VERSION on that line.
    hits=$(grep -E "${p}" "$f" 2>/dev/null | grep -cF "${OLD_VERSION}" 2>/dev/null || true)
    if [[ "${hits}" -eq 0 ]]; then
      continue
    fi
    if [[ ${DRY_RUN} -eq 0 ]]; then
      # `\#regex#s|old|new|g` — `#` is the address-pattern delimiter
      # (custom-delimiter form, since some PARTIAL_PATTERNS contain `/`
      # like `scripts/install.sh`, which would terminate the default
      # `/` address delimiter mid-pattern). Substitution still uses
      # `|` since version strings contain `.` but not `|`.
      "${SED_INPLACE[@]}" "\\#${p}#s|${OLD_VERSION}|${NEW_VERSION}|g" "$f"
    fi
    this_file_count=$((this_file_count + hits))
  done
  if [[ ${this_file_count} -gt 0 ]]; then
    partial_changed=$((partial_changed + 1))
    partial_total=$((partial_total + this_file_count))
    if [[ ${DRY_RUN} -eq 1 ]]; then
      printf '  %-58s  %d install/ref line(s)\n' "$f" "${this_file_count}"
    else
      printf '  %-58s  %d install/ref line(s) → bumped\n' "$f" "${this_file_count}"
    fi
  else
    printf '  %-58s  (no install/ref lines to bump)\n' "$f"
  fi
done

echo
echo "Partial-bump summary: ${partial_changed} README(s) touched, ${partial_total} install/ref line(s)."

# --- Manual-file reminder -----------------------------------------------

echo
echo "Manual edits still needed (release.sh does NOT touch these):"
for f in "${MANUAL_FILES[@]}"; do
  hits=$(grep -c -F "${OLD_VERSION}" "$f" 2>/dev/null || true)
  printf '  %-58s  (%d occurrence(s) of %s)\n' "$f" "${hits}" "${OLD_VERSION}"
done
cat <<EOF

Manual checklist for ${NEW_VERSION}:
  - CHANGELOG.md         add new "## [${NEW_VERSION#v}] - YYYY-MM-DD" section
  - README.md            bump "Status: ${OLD_VERSION}" banner + append roadmap row
                         (install URLs + Terraform refs were already auto-bumped
                         by the partial-bump pass above)
  - README.zh-CN.md      mirror README.md banner / status text changes
                         (install URLs already auto-bumped)
  - pulumi/README.md     review "Limitations at ${OLD_VERSION}" section header
                         (bump to ${NEW_VERSION} if limitations still apply,
                         remove or rename if resolved)

After manual edits:
  git add -A
  git commit -m "chore(release): ${NEW_VERSION}"
  git tag -a "${NEW_VERSION}" -m "Release ${NEW_VERSION}"
EOF

if [[ ${DRY_RUN} -eq 1 ]]; then
  echo
  echo "Dry-run only — re-run without --dry-run to apply."
fi
