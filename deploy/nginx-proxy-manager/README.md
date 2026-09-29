# Nginx Proxy Manager settings

This host runs NPM (`proxy-app-1`), which owns ports 80, 81 and 443 and
fronts several other services. Both assistants join its docker network and
publish nothing externally, so NPM reaches them by container name.

Admin UI is on port 81.

## Before you start

Confirm the network name and that NPM can see the containers:

```bash
docker inspect proxy-app-1 \
  -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
```

Put that value in `.env` as `PROXY_NETWORK` if it is not `proxy_default`,
then bring the stack up and check NPM can resolve the names:

```bash
docker compose up -d --build
docker exec proxy-app-1 getent hosts cashew-nibbles cashew-clarity
```

Two addresses means NPM can route to them. Nothing means they are not on the
same network and the proxy hosts below will return 502.

## Proxy host: nibbles.ai.geco.asia

**Details tab**

| Field | Value |
|---|---|
| Domain Names | `nibbles.ai.geco.asia` |
| Scheme | `http` |
| Forward Hostname / IP | `cashew-nibbles` |
| Forward Port | `5001` |
| Cache Assets | **off** |
| Block Common Exploits | on |
| Websockets Support | on |

Cache Assets must be off. Caching an event stream collapses it.

**Advanced tab** — paste this. Without it NPM buffers the response, the reply
arrives in one lump, and streaming is silently gone:

```nginx
proxy_buffering off;
proxy_cache off;
gzip off;
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
chunked_transfer_encoding on;
```

**SSL tab**: request a Let's Encrypt certificate, then enable Force SSL and
HTTP/2. Once that is live set `COOKIE_SECURE=1` in `.env` and run
`docker compose up -d --force-recreate`.

## Proxy host: clarity.ai.geco.asia

Same as above, but forward to `cashew-clarity` on port `5000`.

Clarity returns unit costs, gross margins and campaign returns, and it has no
login of its own. The proxy is the only thing protecting it.

**Access Lists**: create one under the Access Lists menu, add a username and
password under its Authorization tab, leave Satisfy Any off, then select it on
this proxy host's Details tab. Do not publish the hostname until this is on.

Verify it is actually enforced:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://clarity.ai.geco.asia/   # 401
curl -s -o /dev/null -w '%{http_code}\n' -u user:pass \
     https://clarity.ai.geco.asia/                                       # 200
```

## Confirming streaming survived

The failure is silent: the answer still arrives, just all at once.

```bash
./deploy/check-streaming.sh https://nibbles.ai.geco.asia
./deploy/check-streaming.sh https://clarity.ai.geco.asia user:pass
```

If it reports everything landing together, the Advanced tab did not take.
Re-check that the proxy host saved it and that Cache Assets is off.
