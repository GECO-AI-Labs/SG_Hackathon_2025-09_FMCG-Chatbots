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

## Reverse proxy

This host runs Nginx Proxy Manager in docker (`proxy-app-1`), which owns
ports 80, 81 and 443 and already fronts several other services. System nginx
is therefore not used here and installing it would fail to bind.

Both assistants join NPM's docker network and publish nothing externally, so
NPM reaches them by container name. `127.0.0.1:5000` and `:5001` are still
published for local curl checks, which is why NPM cannot use them: from
inside its own container, loopback is itself.

Full proxy host settings are in `deploy/nginx-proxy-manager/README.md`. Two
of them are easy to miss and both break things quietly:

- **Advanced settings** (the gear icon on newer builds) must carry
  `proxy_buffering off` and the rest. NPM buffers by default, which collapses
  the stream into one delivery and removes streaming without any error. Do not
  add `proxy_http_version` there: the Websockets toggle already emits it and
  nginx rejects the duplicate, silently discarding the whole host.
- **Access List** on the Clarity host. Clarity returns unit costs and gross
  margins and has no login of its own.

`deploy/nginx/` holds standalone nginx configs for a future dedicated host.

## Removing the previous version's images

Other services share this host, so a blanket prune is not safe. Reclaim only
what belonged to the old stack, roughly 640MB:

```bash
docker rm clarity nibbles
docker image rm cashew/clarity:v1 cashew/nibbles:v1 clarity:v1 nibbles:v1
```

The two pairs of tags point at the same two image ids, so all four come off
together. Do not run `docker image prune -af` here: it would also remove
images belonging to other projects that have no container, such as the
`caddy:2-alpine` used by the online payment bot.

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
