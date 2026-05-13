#!/usr/bin/env bash
# Scenario 04: port-scan baseline.
#
# Confirms only the expected ports are open externally on the VPS, and that
# expected ports are actually reachable.
#
# Env vars:
#   PROBE_TARGET                  — VPS hostname or IP (required)
#   EXPECTED_TCP_PORTS            — space-separated list of TCP ports that
#                                   MUST be open. Default: "22550 443"
#   ALLOWED_EXTRA_TCP_PORTS       — space-separated list of TCP ports that
#                                   MAY be open (e.g. 80 if LE-on-demand).
#                                   Default: ""
#   HYSTERIA_HOP_MIN              — UDP hop range start (default 20000)
#   HYSTERIA_HOP_MAX              — UDP hop range end (default 50000)
#   NMAP_TCP_TOP                  — top-N TCP ports to scan (default 1000)
#   NMAP_UDP_TOP                  — top-N UDP ports to scan (default 100)
#
# Exit codes:
#   0 — pass (allow-list matches)
#   1 — fail (extra port open or expected port closed)
#   2 — inconclusive (nmap missing, VPS unreachable)

set -euo pipefail

: "${PROBE_TARGET:?env PROBE_TARGET is required}"
EXPECTED_TCP_PORTS="${EXPECTED_TCP_PORTS:-22550 443}"
ALLOWED_EXTRA_TCP_PORTS="${ALLOWED_EXTRA_TCP_PORTS:-}"
HYSTERIA_HOP_MIN="${HYSTERIA_HOP_MIN:-20000}"
HYSTERIA_HOP_MAX="${HYSTERIA_HOP_MAX:-50000}"
NMAP_TCP_TOP="${NMAP_TCP_TOP:-1000}"
NMAP_UDP_TOP="${NMAP_UDP_TOP:-100}"

if ! command -v nmap >/dev/null 2>&1; then
    echo "WHY: nmap not installed on the runner — install nmap or skip this scenario"
    exit 2
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# 1) TCP scan
TCP_OUT="$WORKDIR/tcp.txt"
if ! nmap -Pn -sT --top-ports "$NMAP_TCP_TOP" --open -oG "$TCP_OUT" "$PROBE_TARGET" >/dev/null; then
    echo "WHY: TCP scan failed for $PROBE_TARGET — host unreachable?"
    exit 2
fi

# Extract open TCP ports — column format: "Host: ... Ports: 443/open/tcp//https///, 22550/open/tcp//..."
OPEN_TCP=$(awk -F'Ports: ' '/Ports:/ {print $2}' "$TCP_OUT" \
    | tr ',' '\n' \
    | awk -F'/' '$2=="open"{print $1}' \
    | sort -u)

# 2) UDP scan — slower; only top-N, and we care about the hop range
UDP_OUT="$WORKDIR/udp.txt"
nmap -Pn -sU --top-ports "$NMAP_UDP_TOP" --open -oG "$UDP_OUT" "$PROBE_TARGET" >/dev/null || true

OPEN_UDP=$(awk -F'Ports: ' '/Ports:/ {print $2}' "$UDP_OUT" \
    | tr ',' '\n' \
    | awk -F'/' '$2=="open" || $2=="open|filtered"{print $1}' \
    | sort -u)

# 3) Build allow-set
ALLOW_TCP=$(printf "%s\n%s\n" "$EXPECTED_TCP_PORTS" "$ALLOWED_EXTRA_TCP_PORTS" \
    | tr ' ' '\n' | grep -v '^$' | sort -u)

# 4) Find extras (open but not allowed)
EXTRA_TCP=$(comm -23 \
    <(printf "%s\n" "$OPEN_TCP") \
    <(printf "%s\n" "$ALLOW_TCP"))

# 5) Find missing (allowed-required but closed)
EXPECTED_REQUIRED=$(printf "%s\n" "$EXPECTED_TCP_PORTS" | tr ' ' '\n' | grep -v '^$' | sort -u)
MISSING_TCP=$(comm -23 \
    <(printf "%s\n" "$EXPECTED_REQUIRED") \
    <(printf "%s\n" "$OPEN_TCP"))

# 6) UDP: any open port outside the hop range is an extra. Inside is expected/healthy.
EXTRA_UDP=""
while IFS= read -r p; do
    [ -z "$p" ] && continue
    if [ "$p" -lt "$HYSTERIA_HOP_MIN" ] || [ "$p" -gt "$HYSTERIA_HOP_MAX" ]; then
        EXTRA_UDP+="$p "
    fi
done <<<"$OPEN_UDP"

FAIL=0
WHY=""
if [ -n "$EXTRA_TCP" ]; then
    FAIL=1
    WHY+="WHY: unexpected TCP ports open: $(echo "$EXTRA_TCP" | tr '\n' ' ')\n"
fi
if [ -n "$MISSING_TCP" ]; then
    FAIL=1
    WHY+="WHY: expected TCP ports closed/filtered: $(echo "$MISSING_TCP" | tr '\n' ' ')\n"
fi
if [ -n "$EXTRA_UDP" ]; then
    FAIL=1
    WHY+="WHY: unexpected UDP ports open outside hop range $HYSTERIA_HOP_MIN-$HYSTERIA_HOP_MAX: $EXTRA_UDP\n"
fi

if [ "$FAIL" -eq 1 ]; then
    printf "FAIL: port-scan baseline\n"
    printf "%b" "$WHY"
    exit 1
fi

# Summary
OPEN_TCP_LINE=$(echo "$OPEN_TCP" | tr '\n' ' ')
OPEN_UDP_LINE=$(echo "$OPEN_UDP" | tr '\n' ' ')
printf "OK [tcp_open=%s udp_open=%s hop_range=%s-%s]\n" \
    "${OPEN_TCP_LINE:-none}" \
    "${OPEN_UDP_LINE:-none}" \
    "$HYSTERIA_HOP_MIN" "$HYSTERIA_HOP_MAX"
exit 0
