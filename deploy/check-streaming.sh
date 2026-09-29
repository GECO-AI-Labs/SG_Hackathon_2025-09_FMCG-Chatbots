#!/usr/bin/env bash
# Verifies that replies actually arrive in pieces rather than all at once.
#
# The failure mode is silent: a buffered stream still returns the right answer,
# just in one lump at the end, so it only shows up as timing.
#
#   ./deploy/check-streaming.sh                      # containers on localhost
#   ./deploy/check-streaming.sh https://nibbles.ai.geco.asia
#   ./deploy/check-streaming.sh https://clarity.ai.geco.asia user:password
set -uo pipefail

BASE="${1:-http://127.0.0.1:5001}"
AUTH="${2:-}"
MSG='{"message":"honey cashews 150g"}'
[[ "$BASE" == *clarity* || "$BASE" == *5000* ]] && MSG='{"message":"How did 2030 go?"}'

CURL=(curl -N -s --max-time 180 -H 'Content-Type: application/json' -d "$MSG")
[[ -n "$AUTH" ]] && CURL+=(-u "$AUTH")

echo "Streaming check against $BASE"
echo

TMP=$(mktemp)
START=$(date +%s.%N)
"${CURL[@]}" "$BASE/chat" | while IFS= read -r line; do
    [[ "$line" == data:* ]] || continue
    NOW=$(date +%s.%N)
    printf '%6.2fs  %s\n' "$(echo "$NOW - $START" | bc)" "${line:0:88}"
    echo "$NOW" >> "$TMP"
done

COUNT=$(wc -l < "$TMP")
if [[ "$COUNT" -lt 2 ]]; then
    echo; echo "FAIL: only $COUNT events. The endpoint is not streaming at all."
    rm -f "$TMP"; exit 1
fi

FIRST=$(head -1 "$TMP"); LAST=$(tail -1 "$TMP")
SPREAD=$(echo "$LAST - $FIRST" | bc)
rm -f "$TMP"

echo
printf 'events: %s   spread: %.2fs\n' "$COUNT" "$SPREAD"
if (( $(echo "$SPREAD > 0.3" | bc -l) )); then
    echo "PASS: events arrived progressively."
else
    echo "FAIL: every event landed at once. Something is buffering."
    echo
    echo "Check, in this order:"
    echo "  1. nginx    proxy_buffering off, gzip off in the location block"
    echo "  2. gunicorn --worker-class gthread --timeout 0"
    echo "  3. a CDN in front (Cloudflare and similar buffer SSE by default)"
    exit 1
fi
