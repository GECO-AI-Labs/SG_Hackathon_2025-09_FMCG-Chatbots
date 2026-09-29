# Deployment

Two subdomains on one VM, both proxied by nginx to containers on localhost.

| Host | Container | Port | Exposure |
|---|---|---|---|
| `nibbles.ai.geco.asia` | nibbles | 5001 | Public |
| `clarity.ai.geco.asia` | clarity | 5000 | Staff only, must be gated |

## Replacing an older deployment

The first version ran from a plain directory with the data at the top level,
images side-loaded from `.tar` files and secrets in `llm.env`. The layout has
changed, so replace the directory rather than updating it in place.

```bash
cd /data/cashew-chatbot

# 1. Keep anything the old stack captured. Leads are the only live data.
cp Team_Cashew_Synthetic_Data/Nibbles_Leads.csv ~/leads-backup-$(date +%F).csv

# 2. Stop the old stack and reclaim the side-loaded images.
docker compose down --remove-orphans
docker images                      # note the old image ids
docker image prune -af             # removes anything no longer referenced

# 2b. See "Removing the old images" below for the scoped version.

# 3. Move the old directory aside. Keep it until the new stack is proven.
cd /data && sudo mv cashew-chatbot cashew-chatbot.old-$(date +%F)

# 4. Clone. Both repos are private, so this needs a credential: either a
#    read-only deploy key on the repo, or a fine-grained token.
sudo git clone https://github.com/GECO-AI-Labs/SG_Hackathon_2025-09_FMCG-Chatbots.git \
     /data/cashew-chatbot
sudo chown -R ubuntu:ubuntu /data/cashew-chatbot
cd /data/cashew-chatbot

# 5. Prepare the host: packages, runtime ownership, nginx sites.
sudo ./deploy/bootstrap-vm.sh
sudo htpasswd -c /etc/nginx/.htpasswd-clarity <username>

# 6. Secrets. Not in the repo, so copy or paste them in.
cp .env.example .env && nano .env        # fill AZURE_API_KEY and LLM_1_API_KEY

# 7. Run.
docker compose up -d --build
curl -s localhost:5000/healthz | jq '.ok, .providers'
./deploy/check-streaming.sh http://nibbles.ai.geco.asia
```

Restore the old leads into the new location once you are happy:

```bash
cp ~/leads-backup-*.csv data/runtime/nibbles_leads.csv
sudo chown 10001:10001 data/runtime/nibbles_leads.csv
```

Delete `cashew-chatbot.old-*` and the old `llm.env` only after the new stack
has served traffic. That old env file holds a live key, so remove it rather
than leaving it on disk.

### Removing the old images

Check what else uses this host before reaching for a blanket prune. A shared
VM will have images belonging to other services, and an unused image there is
still one somebody wants.

```bash
docker ps -a          # anything else running or stopped
docker images         # what is on disk
docker volume ls      # named volumes, which may hold another service's data
docker system df      # how much is actually reclaimable
```

**Scoped.** Run this from the *old* directory so its compose file names
exactly the images it created. This is the right option on a shared host.

```bash
cd /data/cashew-chatbot
docker compose down --remove-orphans --rmi all
```

The first version side-loaded images from `.tar` files, so some may not be
referenced by that compose file. Remove those by name:

```bash
docker images | grep -Ei 'clarity|nibbles'
docker image rm <image-id> [<image-id> ...]
```

**Everything.** Only on a host dedicated to these two apps. It removes every
stopped container, unused image, unused network and the build cache.

```bash
docker system prune -af
docker builder prune -af
```

Adding `--volumes` also destroys named volumes. Nothing here uses one, so it
buys you nothing and can delete another service's database. Leave it off.

**Afterwards.** Rebuilds leave dangling layers behind over time:

```bash
docker image prune -f     # dangling only, safe to run regularly
docker system df          # confirm the space came back
```

### What changed that affects the host

| | Old | New |
|---|---|---|
| Data path | `Team_Cashew_Synthetic_Data/` | `data/Team_Cashew_Synthetic_Data/` |
| Images | side-loaded `.tar` | built from the repo |
| Secrets | `llm.env` | `.env`, gitignored |
| Port binding | all interfaces | `127.0.0.1` only |
| Reverse proxy | none | nginx, required |

The port change is the one that will catch you out. Both containers now bind
loopback so nobody can reach Clarity on `<vm-ip>:5000` and skip its basic
auth. Nothing outside the VM can reach either app until nginx is in front.

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
