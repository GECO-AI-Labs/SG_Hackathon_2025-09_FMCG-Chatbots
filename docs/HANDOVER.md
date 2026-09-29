# Handover

State of the Cashew4Nuts chatbot rebuild as of 29 September 2026. Written so
a new session can pick up without re-deriving anything.

## What this is

Two chatbots for a Singapore nut and snack FMCG business, rebuilt from a
disorganised repo into `apps/clarity` and `apps/nibbles`.

| App | Audience | Port | Hostname |
|---|---|---|---|
| Nibbles | Customers | 5001 | nibbles.ai.geco.asia |
| Clarity | Commercial team | 5000 | clarity.ai.geco.asia |

Both stream token by token over SSE. Both are publicly accessible by choice.

## Status

| Item | State |
|---|---|
| Streaming, both apps | Done, verified end to end |
| Azure client, both protocols | Done, verified against live deployments |
| Clarity pandas tool-calling | Done, 13 tools, figures verified against raw pandas |
| Nibbles catalogue grounding | Done, including confidential-column stripping |
| Synthetic data 2019-2030 | Done, 284k sales lines |
| Dockerfiles, compose | Done, running on the VM |
| NPM proxy hosts | Done, both Online with configs written |
| Rate limiting | Config in repo, may not be applied yet |
| **HTTPS** | **Not done. This is where work stopped.** |
| COOKIE_SECURE=1 | Pending, must follow HTTPS |
| Persona tuning | Open, user parked it |

## Infrastructure

VM: `ssh -i azure-ssh.pem ubuntu@20.17.97.189`, repo at `/data/cashew-chatbot`.

Shared host. It also runs POS frontend/backend, aigiant backend, a whatsapp
booking bot, pl400-quiz, alibaba-cap, and **Nginx Proxy Manager**
(`proxy-app-1`) which owns ports 80, 81 and 443. Admin UI on port 81.

Never run a blanket `docker image prune` there. Other services share it.

Both apps join NPM's docker network (`proxy_default`) and NPM routes to them
by container name, `cashew-clarity:5000` and `cashew-nibbles:5001`. They also
publish `127.0.0.1:5000` and `:5001` for host-side curl only. NPM runs in a
container, so it cannot reach a loopback-bound host port; that is why the
shared network exists.

Git remotes: `origin` is GECO-AI-Labs, `personal` is kgmeister. Both private.
The VM pulls over SSH with a per-repo key:
`git config core.sshCommand "ssh -i ~/.ssh/cashew_deploy -o IdentitiesOnly=yes"`.

## Azure

One Foundry resource, two deployments, **two different protocols**:

| Role | Deployment | Endpoint | Protocol |
|---|---|---|---|
| Primary | `SG-Geco-AI-General-Resources-Foundry-gpt-5.4` | `/openai/v1/responses` | Responses |
| Fallback | `DeepSeek-V3.2` | `/openai/v1/chat/completions` | Chat Completions |

Same API key for both. It lives in `.env` only, which is gitignored. `.env`
is not in the repo and must be copied to the VM separately.

`apps/*/llm.py` speaks both protocols and picks one from the URL. It also
repairs rejected parameters at runtime: on a 400 naming a parameter, it drops
or renames the field, remembers the correction, and retries once.

## Decisions and why

- **Two standalone apps**, each with its own copy of `llm.py`, so either can
  be deployed or rolled back without touching the other.
- **Clarity computes, never estimates.** 13 pandas tools; the model has no
  arithmetic path. The old version sent column names only and invented every
  number it quoted.
- **Nibbles cannot see cost or margin.** `unit_cost_sgd`, `gross_margin_pct`
  and the stock columns are dropped when the catalogue loads, not filtered
  later, so no prompt injection reaches them.
- **Nibbles cannot invent product facts.** Ingredients, nutrition, origin,
  certifications, allergens beyond the supplied tags, discounts and delivery
  dates are all refused. Every product carries tree nuts or peanuts.
- **Reasoning effort differs per app.** Clarity `medium`, Nibbles `minimal`.
- **Data regenerated, not extended**, from a seeded generator matched to the
  original 2019-2025 distribution, then carried to 2030.

## Traps already hit

Each of these cost time. Do not rediscover them.

1. **NPM rejects a duplicate `proxy_http_version`.** With Websockets Support
   on, NPM already emits it. Adding it in the Advanced box makes nginx refuse
   the config, NPM rolls it back, logs a successful reload, and still shows
   the host Online. Requests then hit NPM's fallback page, so the hostname
   answers 200 while never reaching the app, and an Access List enforces
   nothing. **After every NPM save, check
   `docker exec proxy-app-1 ls -la /data/nginx/proxy_host/` — one .conf per
   host.**
2. **`APP_ROOT.parents[1]` crashed in the container.** The Dockerfile copies
   `apps/<name>/` to `/app`, which has no grandparent. Fixed, but the lesson
   stands: tests ran from the repo layout and never from a built image.
3. **The exec bit does not survive a Windows filesystem.** `chmod +x` is a
   no-op there, so scripts committed 100644 and sudo reported "command not
   found". Use `git update-index --chmod=+x`.
4. **`data/runtime` must be owned by uid 10001.** A bind mount adopts host
   ownership and overrides the image's chown, so lead capture fails silently.
   `deploy/bootstrap-vm.sh` handles it.
5. **`/healthz` was leaking the backend.** It served the Azure resource
   hostname, deployment name, table names and paths publicly. Now liveness
   only; `HEALTH_DETAIL=1` restores detail for debugging.
6. **CRLF in `.env`** makes docker compose read the key with a trailing `\r`
   and Azure returns a confusing 401. Do not edit it in Notepad.
7. **Network is blocked from the assistant's sandbox.** Azure and the VM are
   unreachable from there, so every live check has to be run by the user.

8. **Never drop the tool schemas while `function_call` items are still in the
   conversation.** Clarity's final round used to send `tools=None` to force an
   answer. The model still had calls to make, had no namespace to express them
   in, and wrote the raw call syntax (`to=functions.breakdown`,
   `multi_tool_use.parallel`) into the answer, which streamed straight to the
   commercial team. Use `tool_choice="none"` instead: it forbids new calls and
   keeps the namespace defined.
9. **Reasoning items must be replayed across tool rounds.** The Responses
   stream returns them as separate items. Dropping them makes the model
   re-plan from nothing every round and reissue the same queries until the
   rounds run out, which is what exhausted them in the first place.
   `deploy/check-tool-loop.py` covers both, offline, in about a second.

## Where work stopped

HTTPS. Both hosts serve plain HTTP.

1. Confirm `dig +short <host>` returns `20.17.97.189` for both.
2. Confirm the Azure NSG allows inbound 443.
3. In NPM, per host, SSL tab: request a new Let's Encrypt certificate, Force
   SSL on, HTTP/2 on, **HSTS off** for now. One host at a time; a failure
   costs rate limit.
4. Re-check the proxy_host file count.
5. Then and only then: `COOKIE_SECURE=1` in `.env` and
   `docker compose up -d --force-recreate`. Earlier than that and browsers
   drop the session cookie silently.
6. `./deploy/check-streaming.sh https://nibbles.ai.geco.asia`

## Open items

- **Personas read as generic.** User's words: the persona is default. Voice
  lives in `apps/*/prompts.py`. The prompts are heavy on grounding rules and
  light on character, and GPT-5.4 defaults to a flat register. Change voice
  only; leave the grounding rules alone.
- **Nibbles takes about 6.4s.** GPT-5.4 spent 262 reasoning tokens phrasing a
  price. DeepSeek would answer in one or two seconds and cost far less. Would
  need per-app provider ordering, which does not exist yet.
- **Rate limiting may not be applied.** `deploy/nginx-proxy-manager/http.conf`
  goes to `/data/nginx/custom/http.conf`, then `limit_req zone=cashew_chat
  burst=10 nodelay;` in each host's Advanced box. Both endpoints are public
  and every request spends Azure tokens.
- **No container smoke test.** Trap 2 would have been caught by one.
- **Sessions are in process memory**, so both apps run one worker. Redis
  before scaling out.
- **The old key was exposed** in `archive/original_v1/llm.env` locally and in
  chat. Rotating it in Foundry is still worth doing.

## Verification

```bash
cd /data/cashew-chatbot
docker compose ps
curl -s localhost:5000/healthz                        # {"ok":true}
docker exec proxy-app-1 getent hosts cashew-nibbles cashew-clarity
docker exec proxy-app-1 ls -la /data/nginx/proxy_host/
./deploy/check-streaming.sh http://nibbles.ai.geco.asia
```

With `HEALTH_DETAIL=1`, Clarity's health should report 7 tables, 283,585
sales rows, a range ending 2030-12-31 and 13 tools. Nibbles should report 124
products and 15 FAQs.

Known-good figures for spot-checking Clarity's answers: 2030 net sales
SGD 295,398.86 across 16,272 orders, gross profit SGD 123,196.74, AOV 18.15,
Shopee 59,831.88.

## Repo map

```
apps/clarity/   analytics assistant: llm.py, datastore.py, analytics.py,
                prompts.py, app.py, config.py
apps/nibbles/   customer assistant: llm.py, catalog.py, matching.py,
                sessions.py, prompts.py, app.py, config.py
data/           the dataset, plus catalog/sku_master_base.csv
tools/          config.py and generate_synthetic_data.py
deploy/         bootstrap-vm.sh, check-streaming.sh, nginx/,
                nginx-proxy-manager/
docs/           dataset.md, deployment.md, this file
archive/        the original version, kept for reference
```

No secrets in any committed file. `.env` is local and on the VM only.
