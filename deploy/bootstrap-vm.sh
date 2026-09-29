#!/usr/bin/env bash
# Prepares this host to run both assistants behind Nginx Proxy Manager.
#
# This host is shared. The script only reads the docker state, fixes the one
# directory the containers need to write, and reports what needs doing by
# hand. It installs no proxy, stops no container and removes no image.
#
#   sudo ./deploy/bootstrap-vm.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_UID=10001          # appuser inside both images
PORTS=(5000 5001)
TODO=0

say  () { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn () { printf '  \033[33m!\033[0m %s\n' "$1"; TODO=$((TODO+1)); }
ok   () { printf '  \033[32mok\033[0m %s\n' "$1"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
command -v docker >/dev/null || { echo "docker not found."; exit 1; }

say "Ports this stack wants"
for port in "${PORTS[@]}"; do
    if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
        warn "port $port already in use by:"
        ss -ltnp "sport = :$port" 2>/dev/null | tail -n +2 | sed 's/^/       /'
        warn "  change CLARITY_PORT or NIBBLES_PORT in .env and in compose"
    else
        ok "port $port free"
    fi
done

say "Reverse proxy"
PROXY=$(docker ps --filter ancestor=jc21/nginx-proxy-manager:latest \
        --format '{{.Names}}' | head -1)
if [[ -n "$PROXY" ]]; then
    ok "Nginx Proxy Manager running as '$PROXY'"
    NET=$(docker inspect "$PROXY" \
          -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
          | awk '{print $1}')
    ok "its network is '$NET'"
    CONFIGURED=$(grep -E '^PROXY_NETWORK=' "$REPO/.env" 2>/dev/null | cut -d= -f2 || true)
    if [[ -z "$CONFIGURED" ]]; then
        warn "set PROXY_NETWORK=$NET in .env"
    elif [[ "$CONFIGURED" != "$NET" ]]; then
        warn "PROXY_NETWORK in .env is '$CONFIGURED' but NPM is on '$NET'"
    else
        ok "PROXY_NETWORK in .env matches"
    fi
    warn "add both proxy hosts in the NPM UI on port 81"
    warn "  see deploy/nginx-proxy-manager/README.md; the Advanced tab is"
    warn "  required or NPM buffers the stream and streaming stops working"
else
    warn "Nginx Proxy Manager is not running here"
    warn "  either start it, or use deploy/nginx/ with system nginx instead"
fi

say "Runtime directory"
# A bind mount adopts host ownership and overrides the chown done in the
# image, so without this the container cannot write captured leads.
mkdir -p "$REPO/data/runtime"
chown -R "$CONTAINER_UID:$CONTAINER_UID" "$REPO/data/runtime"
ok "data/runtime owned by $CONTAINER_UID"

say "Secrets"
if [[ -f "$REPO/.env" ]]; then
    MISSING=0
    for key in AZURE_API_KEY LLM_1_API_KEY; do
        grep -qE "^$key=.+" "$REPO/.env" || { warn "$key is empty in .env"; MISSING=1; }
    done
    [[ $MISSING -eq 0 ]] && ok ".env has both keys"
    chmod 600 "$REPO/.env"
    ok ".env permissions set to 600"
else
    warn "no .env yet:  cp .env.example .env  then fill the two keys"
fi

say "Leftovers from the previous version"
OLD_C=$(docker ps -a --filter name='^/clarity$' --filter name='^/nibbles$' \
        --format '{{.Names}}' | tr '\n' ' ')
if [[ -n "$OLD_C" ]]; then
    warn "old containers still present: $OLD_C"
    warn "  docker rm $OLD_C"
fi
OLD_I=$(docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -E '^(cashew/)?(clarity|nibbles):v1$' | tr '\n' ' ')
if [[ -n "$OLD_I" ]]; then
    warn "old images still present: $OLD_I"
    warn "  docker image rm $OLD_I     # about 640MB, after removing the containers"
    warn "  do NOT run a blanket prune; other services on this host share it"
fi
[[ -f "$REPO/../cashew-chatbot/llm.env" ]] && warn "old llm.env still on disk; it holds a live key"

say "Next"
cat <<MSG
  1. docker compose up -d --build
  2. docker exec ${PROXY:-proxy-app-1} getent hosts cashew-nibbles cashew-clarity
     (two addresses means the proxy can route to them)
  3. Add both proxy hosts in NPM, with the Advanced tab settings and an
     Access List on clarity
  4. ./deploy/check-streaming.sh https://nibbles.ai.geco.asia
MSG
[[ $TODO -gt 0 ]] && echo && echo "  $TODO item(s) above need you."
exit 0
