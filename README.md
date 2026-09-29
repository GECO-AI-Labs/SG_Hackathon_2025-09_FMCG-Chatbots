# Cashew4Nuts Chatbots

Two assistants for a Singapore nut and snack FMCG business, sharing one
dataset and one model client.

| App | Audience | Port | What it does |
|---|---|---|---|
| **Nibbles** | Customers | 5001 | Answers product, price and policy questions from the catalogue |
| **Clarity** | Commercial team | 5000 | Answers trading questions by querying the dataset with pandas |

Both stream their replies token by token over server-sent events.

## Quick start

```bash
cp .env.example .env          # then fill in AZURE_API_KEY and AZURE_DEPLOYMENT
docker compose up --build
```

Nibbles runs at http://localhost:5001 and Clarity at http://localhost:5000.

To run one app directly without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r apps/clarity/requirements.txt
python apps/clarity/app.py
```

## Layout

```
apps/clarity/        internal analytics assistant
apps/nibbles/        customer-facing assistant
  llm.py             Azure client: routing, streaming, tool calls, failover
  config.py          settings, read from .env
  app.py             Flask routes and the SSE turn loop
data/
  Team_Cashew_Synthetic_Data/   the generated dataset, 2019-2030
  catalog/sku_master_base.csv   the product list the generator builds from
tools/
  config.py                     every tunable parameter of the dataset
  generate_synthetic_data.py    rebuilds all tables from one seed
deploy/nginx/        proxy configs for both subdomains
archive/original_v1/ the previous version, kept for reference
docs/dataset.md      how the data is shaped and why
docs/deployment.md   DNS, nginx, TLS and the streaming check
```

The two apps are deliberately standalone. Each carries its own copy of
`llm.py` so either can be deployed, changed or rolled back without touching
the other.

## Connecting to Azure

`AZURE_API_URL` takes whatever the Foundry portal gives you. The client works
out the route:

| What you paste | Where the request goes |
|---|---|
| `https://<res>.services.ai.azure.com` | `/models/chat/completions?api-version=...` |
| `https://<res>.services.ai.azure.com/openai/v1` | `/openai/v1/chat/completions` |
| `https://<res>.openai.azure.com` + `AZURE_DEPLOYMENT_PATH` | `/openai/deployments/<name>/chat/completions` |
| Any full URL already ending in `/chat/completions` | used as given |

Auth follows the host: `api-key` for Azure, bearer tokens elsewhere.

### Model differences are handled for you

GPT-5 and the o-series reject `max_tokens` and reject any `temperature` other
than the default, while accepting `reasoning_effort` and `verbosity` that
older models reject. The client picks the right payload from the deployment
name, and if Azure still objects it reads the rejection, drops or renames the
field, remembers the correction and retries. A deployment upgrade costs one
wasted request rather than an outage.

Reasoning depth is set per app, because the trade differs. Clarity runs at
`medium` since only staff wait on it. Nibbles runs at `minimal` so shoppers
get a first token quickly.

### Failover

Set `LLM_1_*`, `LLM_2_*` and so on for fallbacks, tried in order when the
primary fails. Blocks still holding a placeholder key are skipped, so an
unfinished block costs nothing.

## How Clarity answers

Clarity has thirteen query tools over the dataset and no ability to do
arithmetic itself. A turn runs as a loop: the model streams, and if it asks
for data rather than answering, the queries run against pandas and the results
go back in. Once it stops asking, the answer streams straight through.

Every figure in an answer came out of a pandas query. The browser shows each
query as it runs, so an analyst can see exactly what produced a number.

Tools cover trading summaries, trends, breakdowns by any dimension, top and
bottom SKUs, period comparisons, campaign ROAS, the paid media funnel,
customer segments, stock cover, basket economics and product lookup.

## How Nibbles answers

Catalogue matching runs first and the matched products are pushed to the
browser immediately, so a shopper sees real items within milliseconds. The
written reply then streams in beside them.

Two safety rules are built in rather than left to the prompt:

- **Confidential columns never load.** `unit_cost_sgd`, `gross_margin_pct` and
  the stock columns are dropped when the catalogue is read, so no prompt
  injection can reach them. `/healthz` reports what was withheld.
- **The model cannot invent product facts.** Ingredients, nutrition, origin,
  certifications, allergens beyond the supplied tags, discounts and delivery
  dates are all off limits. This matters because every product carries tree
  nuts or peanuts.

Customers write in several languages, so Chinese, Japanese, Korean, Malay and
Tamil terms fold to English tokens before the catalogue is searched. Near
misses like "casshew" still resolve.

If the model endpoint is down, Nibbles still returns the matched products with
a plain message rather than failing the request.

## The dataset

`data/Team_Cashew_Synthetic_Data/` holds 12 years of trading, 2019 to 2030:
284k sales lines, 155k orders, 83k online orders, 9.8k customers, 124 SKUs and
84 marketing campaigns.

Rebuild it at any time:

```bash
pip install -r tools/requirements.txt
python tools/generate_synthetic_data.py
python tools/generate_synthetic_data.py --end 2028-12-31 --out data/sample
```

Shape is controlled entirely by `tools/config.py`. See `docs/dataset.md` for
what the numbers do and why.

## Operational notes

- **One worker, many threads.** Both apps keep conversation state in process
  memory, so a second worker would lose a user's context between requests.
  The Dockerfiles run `--workers 1 --threads 16`. Move state to Redis before
  scaling out.
- **`--timeout 0`** stops gunicorn killing a long streamed answer mid-sentence.
- **`X-Accel-Buffering: no`** is set on every stream. Without it nginx buffers
  the whole response and the reply arrives as one blob.
- **`/healthz`** on both apps reports data load state, resolved endpoint and
  the payload shape in use. Check it first when something looks wrong.

## Deploying

Both apps sit behind nginx on one VM, at `nibbles.ai.geco.asia` and
`clarity.ai.geco.asia`. Configs are in `deploy/nginx/` and the full procedure
is in `docs/deployment.md`.

Clarity has no authentication of its own and returns cost and margin figures,
so the nginx block in front of it carries basic auth. Do not publish that
hostname without it.

## Security

`.env` is gitignored and `.env.example` carries placeholders only. Rotate any
key that has been committed anywhere, including in the archived version of
this project.
