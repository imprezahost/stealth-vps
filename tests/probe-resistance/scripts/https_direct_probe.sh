#!/usr/bin/env bash
# Scenario 01: HTTPS direct probe.
#
# Compares (http_code, size_download, ssl_verify_result) between the public
# dest and our VPS. If they match, Reality's reverse-proxy fallback is
# working at the HTTP layer.
#
# Env vars:
#   PROBE_TARGET           — VPS hostname or IP (required)
#   PROBE_REALITY_DEST     — dest hostname configured on the panel (required)
#   PROBE_TARGET_IP        — optional explicit IP; defaults to resolving PROBE_TARGET
#   PROBE_TIMEOUT          — per-request timeout in seconds (default: 15)
#
# Exit codes:
#   0 — pass
#   1 — fail (probe shape differs from baseline)
#   2 — inconclusive (dest unreachable, VPS unreachable, missing env)

set -euo pipefail

: "${PROBE_TARGET:?env PROBE_TARGET (VPS hostname or IP) is required}"
: "${PROBE_REALITY_DEST:?env PROBE_REALITY_DEST is required}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-15}"

if [ -z "${PROBE_TARGET_IP:-}" ]; then
    PROBE_TARGET_IP=$(getent ahostsv4 "$PROBE_TARGET" 2>/dev/null | awk 'NR==1{print $1}')
    if [ -z "$PROBE_TARGET_IP" ]; then
        echo "WHY: could not resolve PROBE_TARGET=$PROBE_TARGET to an IPv4 address"
        exit 2
    fi
fi

# Capture a curl probe as a 3-tuple "http_code size_download ssl_verify_result".
# We use --resolve to force the chosen IP while keeping the SNI = dest.
probe_shape() {
    local label="$1" target_ip="$2"
    local out
    if ! out=$(curl \
            --silent \
            --output /dev/null \
            --max-time "$PROBE_TIMEOUT" \
            --resolve "${PROBE_REALITY_DEST}:443:${target_ip}" \
            --write-out "%{http_code} %{size_download} %{ssl_verify_result}\n" \
            "https://${PROBE_REALITY_DEST}/" 2>&1); then
        echo "WHY: $label probe to $target_ip failed: $out" >&2
        return 2
    fi
    printf "%s" "$out"
}

# 1) Resolve the public dest to its real IP (for the baseline)
DEST_IP=$(getent ahostsv4 "$PROBE_REALITY_DEST" 2>/dev/null | awk 'NR==1{print $1}')
if [ -z "$DEST_IP" ]; then
    echo "WHY: could not resolve PROBE_REALITY_DEST=$PROBE_REALITY_DEST"
    exit 2
fi

# 2) Baseline shape: hit dest at its real IP.
if ! BASELINE=$(probe_shape "baseline" "$DEST_IP"); then
    exit 2
fi

# 3) Probe shape: hit dest's SNI at our VPS IP.
if ! PROBE=$(probe_shape "probe" "$PROBE_TARGET_IP"); then
    exit 2
fi

# 4) Compare the three fields.
read -r B_HTTP B_SIZE B_VERIFY <<<"$BASELINE"
read -r P_HTTP P_SIZE P_VERIFY <<<"$PROBE"

# Size: we tolerate a small relative drift because CDNs do A/B testing.
size_within_tolerance() {
    local b="$1" p="$2"
    [ "$b" -eq 0 ] && [ "$p" -eq 0 ] && return 0
    if [ "$b" -eq 0 ] || [ "$p" -eq 0 ]; then return 1; fi
    # tolerate 25% drift either way (CDNs vary that much on dynamic homepages)
    local lo=$(( b * 75 / 100 ))
    local hi=$(( b * 125 / 100 ))
    [ "$p" -ge "$lo" ] && [ "$p" -le "$hi" ]
}

FAIL=0
WHY=""
if [ "$B_HTTP" != "$P_HTTP" ]; then
    FAIL=1
    WHY+="WHY: http_code baseline=$B_HTTP probe=$P_HTTP\n"
fi
if [ "$B_VERIFY" != "$P_VERIFY" ]; then
    FAIL=1
    WHY+="WHY: ssl_verify_result baseline=$B_VERIFY probe=$P_VERIFY\n"
fi
if ! size_within_tolerance "$B_SIZE" "$P_SIZE"; then
    FAIL=1
    WHY+="WHY: size_download baseline=$B_SIZE probe=$P_SIZE (>25% drift)\n"
fi

if [ "$FAIL" -eq 1 ]; then
    printf "FAIL [baseline=(%s %s %s) probe=(%s %s %s)]\n" \
        "$B_HTTP" "$B_SIZE" "$B_VERIFY" "$P_HTTP" "$P_SIZE" "$P_VERIFY"
    printf "%b" "$WHY"
    exit 1
fi

printf "OK [http=%s size~=%s verify=%s] dest=%s vps=%s\n" \
    "$P_HTTP" "$P_SIZE" "$P_VERIFY" "$PROBE_REALITY_DEST" "$PROBE_TARGET_IP"
exit 0
