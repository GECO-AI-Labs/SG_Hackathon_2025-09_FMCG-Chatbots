#!/usr/bin/env bash
# Prepares a fresh Ubuntu host to run both assistants behind nginx.
#
# Safe to re-run. It installs what is missing, fixes the runtime directory
# ownership, and installs the nginx site configs. It does not touch .env and
# it does not start the containers.
#
#   sudo ./deploy/bootstrap-vm.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_UID=10001          # appuser inside both images

say () { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }

say "Packages"
apt-get update -qq
apt-get install -y -qq nginx apache2-utils curl jq bc >/dev/null
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || {
    apt-get install -y -qq docker-compose-plugin >/dev/null; }
echo "  nginx $(nginx -v 2>&1 | grep -o '[0-9.]*') / docker $(docker --version | grep -o '[0-9.]*' | head -1)"

say "Runtime directory"
# The bind mount adopts host ownership, overriding the chown in the image.
# Without this the container cannot write captured leads.
mkdir -p "$REPO/data/runtime"
chown -R "$CONTAINER_UID:$CONTAINER_UID" "$REPO/data/runtime"
echo "  $REPO/data/runtime owned by $CONTAINER_UID"

say "Nginx"
cp "$REPO/deploy/nginx/00-rate-limit.conf" /etc/nginx/conf.d/
cp "$REPO/deploy/nginx/clarity.conf" "$REPO/deploy/nginx/nibbles.conf" /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/clarity.conf /etc/nginx/sites-enabled/clarity.conf
ln -sf /etc/nginx/sites-available/nibbles.conf /etc/nginx/sites-enabled/nibbles.conf
rm -f /etc/nginx/sites-enabled/default

if [[ ! -f /etc/nginx/.htpasswd-clarity ]]; then
    cat <<'MSG'

  Clarity returns unit costs, gross margins and campaign returns, and it has
  no login of its own. It will not serve until a password file exists:

      sudo htpasswd -c /etc/nginx/.htpasswd-clarity <username>

MSG
else
    echo "  basic auth file present"
fi

nginx -t && systemctl reload nginx
echo "  nginx configured and reloaded"

say "Next"
cat <<MSG
  1. cp .env.example .env   and fill AZURE_API_KEY and LLM_1_API_KEY
  2. sudo htpasswd -c /etc/nginx/.htpasswd-clarity <username>   (if not done)
  3. docker compose up -d --build
  4. ./deploy/check-streaming.sh http://nibbles.ai.geco.asia
  5. sudo certbot --nginx -d nibbles.ai.geco.asia -d clarity.ai.geco.asia
     then set COOKIE_SECURE=1 in .env and: docker compose up -d --force-recreate
MSG
