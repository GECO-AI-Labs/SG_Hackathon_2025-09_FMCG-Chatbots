#!/usr/bin/env bash
# Pull the latest code and restart both assistants.
#
#   ./deploy/update.sh
#
# The VM is a deployment target, not somewhere to author changes, so local
# edits to tracked files are discarded. Untracked files are left alone, which
# is why .env and data/runtime survive.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_UID=10001
cd "$REPO"

say () { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok  () { printf '  \033[32mok\033[0m %s\n' "$1"; }
bad () { printf '  \033[31m!\033[0m %s\n' "$1"; }

say "Fetching"
BEFORE=$(git rev-parse --short HEAD)
git fetch --quiet origin main
AFTER=$(git rev-parse --short origin/main)

if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "already at $BEFORE, nothing to pull"
else
    git --no-pager log --oneline "HEAD..origin/main" | sed 's/^/  /'
    # Discards local edits to tracked files. .env and data/runtime are
    # untracked, so they are untouched.
    git reset --hard --quiet origin/main
    ok "$BEFORE -> $AFTER"
fi

say "Runtime directory"
mkdir -p data/runtime
if [[ "$(stat -c %u data/runtime)" != "$CONTAINER_UID" ]]; then
    sudo chown -R "$CONTAINER_UID:$CONTAINER_UID" data/runtime
    ok "ownership corrected"
else
    ok "owned by $CONTAINER_UID"
fi

say "Rebuilding"
docker compose up -d --build

say "Waiting for health"
for app in clarity nibbles; do
    port=5000; [[ "$app" == nibbles ]] && port=5001
    for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
            ok "$app responding on $port"; break
        fi
        sleep 2
    done
    curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 || {
        bad "$app never came up"
        docker compose logs --tail 25 "$app"
        exit 1
    }
done

say "Done"
docker compose ps --format '  {{.Name}}  {{.Status}}'
echo
echo "  Proxy configs are untouched by this. If you changed anything in NPM,"
echo "  check:  docker exec proxy-app-1 ls -la /data/nginx/proxy_host/"
