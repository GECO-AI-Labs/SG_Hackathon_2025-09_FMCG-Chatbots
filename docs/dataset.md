# The synthetic dataset

Twelve years of trading for Cashew4Nuts, 2019 to 2030, generated from one
seed so any run reproduces the same numbers.

Everything is built in a single pass because the tables have to agree with
each other. Run `python tools/generate_synthetic_data.py` to rebuild.

## What it contains

| Table | Rows | Notes |
|---|---:|---|
| `sales_transactions.csv` | 283,585 | One row per order line, with cost and margin |
| `ecommerce_purchases.csv` | 82,905 | Online order headers with delivery and GST |
| `customers.csv` | 9,830 | 9,730 registered plus 100 walk-in guest accounts |
| `sku_master.csv` | 124 | Price, cost, margin, stock and cover |
| `events.csv` | 227 | Marketing events across 84 campaigns |
| `traffic_acquisition.csv` | 693 | Paid media rows per event |
| `faqs.csv` | 15 | Published customer policy answers |

## Identities that always hold

These are what let Clarity answer without contradicting itself:

- Every online order's `subtotal_sgd` equals the sum of its sales lines
- `grand_total_sgd` equals subtotal plus delivery plus GST
- GST follows the Singapore schedule: 7% to the end of 2022, 8% through 2023,
  9% from 2024, charged on delivery as well as goods
- Each campaign's spend equals the sum of its channel spend rows
- A discounted line always names the campaign that discounted it, and a line
  with no campaign is never discounted
- Stock cover reflects the last 90 days of demand for that SKU
- No sales line references a customer or SKU that does not exist

## How the shape is built

**Volume.** A baseline of 31.9 orders a day in 2019, scaled by a year factor,
a month index, a day-of-week index and Poisson noise.

**Seasonality.** January is Chinese New Year, November is 11.11 and Black
Friday, December is Christmas and corporate gifting. May to June and September
to October are the troughs. The month index was fitted to the original data,
which already contained campaign effects, so campaign lift is normalised
against its own monthly mean before it is applied. Without that step the peaks
would be counted twice.

**The 2020 dip.** April to June 2020 runs at roughly 80% on the circuit
breaker, then recovers above trend through September.

**Growth.** History is flat, matching the original. From 2026 revenue grows
7.5%, 8%, 7%, 6% and 6%, reaching SGD 295k in 2030.

**Channel shift.** E-commerce moves from 47% of orders in 2019 to 59% by 2030,
interpolated year by year so it reads as a trend rather than a step.
Supermarket and offline retail give up the share.

**Category drift.** Premium lines gain slowly. Macadamia moves from 12.4% to
15.6% of revenue and peanuts fall from 7.0% to 5.6%.

**Prices.** List prices hold flat through 2025 and then track input-cost
inflation at about 2% a year. `sku_master.csv` carries the base price list;
the inflation index is applied to `list_price_sgd` on each transaction, which
is how a real business would see it.

## Changes from the original dataset

The 2019-2025 history was rebuilt to match the original closely: orders land
within 1%, revenue within 5%, and the category and channel mixes within half a
percentage point. Five things were fixed rather than reproduced.

**Free delivery now works.** `faqs.csv` promised free delivery above SGD 50
and the data never applied it. Delivery is now free above the threshold, and
marketplaces absorb it through vouchers on top of that.

**Campaigns make money.** The original averaged 0.16x return on ad spend, so
every campaign in seven years lost money and "which campaigns worked" had no
useful answer. ROAS now runs 1.2x to 6.4x depending on channel, blended at
3.5x.

**Promotions exist.** Every line previously sold at exact list price, which
made it impossible to connect a campaign to a sales lift. Around 10% of lines
now carry a campaign discount, and each names its campaign.

**Cost and margin exist.** There was no cost column anywhere, so no margin
question could be answered. Every SKU now has a unit cost derived from a
category margin, and every line carries COGS and gross profit.

**The loyalty pyramid is the right way up.** Half the customer base sat in
Platinum. It now runs 46% Bronze, 31% Silver, 18% Gold, 5% Platinum.

## Tuning it

All parameters live in `tools/config.py`. The ones worth knowing:

- `YEAR_FACTOR` — volume per year relative to 2019
- `MONTH_INDEX`, `DOW_INDEX` — seasonality
- `CHANNEL_MIX_START` / `_END` — the e-commerce shift
- `CATEGORY_MIX_START` / `_END` — category drift, solved for automatically
- `MARGIN_BY_CATEGORY` — drives unit cost
- `ROAS_RANGE`, `PROMO_DEPTH` — campaign returns and discount depth
- `PRICE_INDEX` — price inflation by year

Category mix is a target, not an input. The generator solves for the per-SKU
purchase weights that produce the requested revenue mix, so a changed target
needs no other edits.
