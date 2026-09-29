# Deployment

Two subdomains on one VM, both proxied by nginx to containers on localhost.

| Host | Container | Port | Exposure |
|---|---|---|---|
| `nibbles.ai.geco.asia` | nibbles | 5001 | Public |
| `clarity.ai.geco.asia` | clarity | 5000 | Staff only, must be gated |

## DNS

Point both A records at the VM's public IP. Nginx routes on the Host header,
so one IP serves both.

```
clarity.ai.geco.asia.   A   <vm-ip>
nibbles.ai.geco.asia.   A   <vm-ip>
```

## Nginx

```bash
sudo cp deploy/nginx/00-rate-limit.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/clarity.conf deploy/nginx/nibbles.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/clarity.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/nibbles.conf /etc/nginx/sites-enabled/

# Clarity needs a password. It reads cost and margin data.
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd-clarity <username>

sudo nginx -t && sudo systemctl reload nginx
```

### Why the proxy settings matter

Nginx buffers proxied responses by default. Left on, it collects the entire
answer and delivers it in one piece, which removes streaming completely and
puts you back where the old version was. Three settings prevent that:

- `proxy_buffering off` releases each chunk as it arrives
- `proxy_read_timeout 3600s` stops nginx cutting a long answer off at 60s
- `proxy_http_version 1.1` with an empty `Connection` header keeps the
  connection open for the stream

The apps also send `X-Accel-Buffering: no` on every stream, which nginx
honours on its own. Both are in place because either one alone is a single
point of failure.

## TLS

Both records are HTTP today. Nibbles collects names, emails and phone numbers
through `/lead`, and Clarity returns costs and margins, so neither should stay
on plain HTTP once the addresses are public.

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d nibbles.ai.geco.asia -d clarity.ai.geco.asia
```

Certbot rewrites the server blocks and adds the redirect. Afterwards set
`COOKIE_SECURE=1` in `.env` and restart, so session cookies stop travelling in
clear text:

```bash
docker compose up -d --force-recreate
```

Leave `COOKIE_SECURE=0` until TLS is live. On plain HTTP a secure cookie is
dropped by the browser and sessions silently stop working.

## Running the containers

```bash
cp .env.example .env     # fill in AZURE_API_KEY and AZURE_DEPLOYMENT
docker compose up -d --build
curl -s localhost:5000/healthz | jq .ok
curl -s localhost:5001/healthz | jq .ok
```

Both containers restart unless stopped, so they survive a VM reboot.

## Checking that streaming survived the proxy

The failure mode is silent: the answer still arrives, just all at once. Test
through the public hostname, not localhost, because the proxy is what breaks it.

```bash
curl -N -s https://nibbles.ai.geco.asia/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"honey cashews 150g"}' \
  | while read -r line; do printf '%s  %s\n' "$(date +%T.%3N)" "$line"; done
```

Timestamps should climb across the `delta` lines. Identical timestamps mean
something between the browser and gunicorn is still buffering.

## Scaling

Both apps hold conversation state in process memory, so they run one worker
with sixteen threads. Adding workers will drop users' context between
requests. Move sessions to Redis before scaling out.
