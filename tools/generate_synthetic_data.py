#!/usr/bin/env python3
"""Generate the Cashew4Nuts synthetic dataset for 2019 through 2030.

The dataset backs two chatbots: Nibbles answers customers from the catalogue,
and Clarity answers the commercial team from the transaction history. Both
need the numbers to hold together, so this script builds every table in one
pass from a single seeded random stream and keeps the cross-table identities
true:

  * every e-commerce order reconciles to the sum of its sales lines
  * GST follows the Singapore schedule and applies to delivery as well
  * campaign spend equals the sum of its channel spend rows
  * promotional discounts on a line trace back to a live campaign
  * closing stock reflects recent demand rather than a free-floating number

Shape is controlled entirely by tools/config.py.

Usage:
    python tools/generate_synthetic_data.py
    python tools/generate_synthetic_data.py --end 2028-12-31 --out data/sample
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import config as cfg

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "Team_Cashew_Synthetic_Data"
CATALOG_BASE = ROOT / "data" / "catalog" / "sku_master_base.csv"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def as_choice(mapping: Dict) -> Tuple[np.ndarray, np.ndarray]:
    """Freeze a share dictionary into arrays for repeated sampling."""
    keys = np.array(list(mapping.keys()))
    weights = np.array([mapping[k] for k in mapping], dtype=float)
    return keys, weights / weights.sum()


def weighted_choice(rng: np.random.Generator, mapping: Dict, size: int):
    keys, weights = as_choice(mapping)
    return rng.choice(keys, size=size, p=weights)


def lerp_mix(start: Dict[str, float], end: Dict[str, float], t: float) -> Dict[str, float]:
    """Blend two share dictionaries and renormalise."""
    out = {k: start[k] + (end[k] - start[k]) * t for k in start}
    total = sum(out.values())
    return {k: v / total for k, v in out.items()}


def gst_rate(day: pd.Timestamp) -> float:
    rate = cfg.GST_SCHEDULE[0][1]
    for start, value in cfg.GST_SCHEDULE:
        if day >= pd.Timestamp(start):
            rate = value
    return rate


def money(x) -> float:
    return float(np.round(x, 2))


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

def build_catalog(rng: np.random.Generator) -> pd.DataFrame:
    """Load the product list and derive cost and margin from list price."""
    cat = pd.read_csv(CATALOG_BASE)
    cat = cat.drop(columns=[c for c in ("current_stock_units",) if c in cat.columns])

    margin = cat["category"].map(cfg.MARGIN_BY_CATEGORY).fillna(0.40)
    # Vary each SKU a little around its category margin.
    jitter = rng.normal(0.0, 0.025, len(cat)).clip(-0.06, 0.06)
    margin = (margin + jitter).clip(0.18, 0.62)

    cat["unit_cost_sgd"] = (cat["unit_price_sgd"] * (1 - margin)).round(3)
    cat["gross_margin_pct"] = (
        (cat["unit_price_sgd"] - cat["unit_cost_sgd"]) / cat["unit_price_sgd"] * 100
    ).round(2)
    return cat


def calibrate_sku_weights(cat: pd.DataFrame, target_mix: Dict[str, float]) -> np.ndarray:
    """Find per-SKU purchase weights that land on the target revenue mix.

    Line weights are uniform inside a category, so only the per-category
    multiplier needs solving. A few fixed-point passes converge tightly.
    """
    price = cat["unit_price_sgd"].to_numpy(dtype=float)
    categories = cat["category"].to_numpy()
    mult = {c: 1.0 for c in target_mix}

    for _ in range(60):
        weights = np.array([mult.get(c, 1.0) for c in categories], dtype=float)
        weights /= weights.sum()
        revenue = weights * price
        share = {
            c: revenue[categories == c].sum() / revenue.sum() for c in target_mix
        }
        drift = 0.0
        for c, target in target_mix.items():
            actual = max(share.get(c, 1e-9), 1e-9)
            mult[c] *= (target / actual) ** 0.6
            drift = max(drift, abs(target - actual))
        if drift < 1e-5:
            break

    weights = np.array([mult.get(c, 1.0) for c in categories], dtype=float)
    return weights / weights.sum()


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------

def build_customers(rng: np.random.Generator, start: pd.Timestamp,
                    end: pd.Timestamp) -> pd.DataFrame:
    rows: List[dict] = []
    seq = 100_001

    for year in range(start.year, end.year + 1):
        growth = cfg.CUSTOMER_GROWTH ** max(0, year - 2025)
        count = int(round(cfg.CUSTOMERS_PER_YEAR * growth))
        y_start = max(start, pd.Timestamp(f"{year}-01-01"))
        y_end = min(end, pd.Timestamp(f"{year}-12-31"))
        span = max((y_end - y_start).days, 1)

        for _ in range(count):
            offset = int(rng.integers(0, span + 1))
            when = y_start + pd.Timedelta(days=offset, hours=int(rng.integers(8, 23)))
            if when > end:
                continue
            first = str(rng.choice(cfg.FIRST_NAMES))
            last = str(rng.choice(cfg.LAST_NAMES))
            tier = str(weighted_choice(rng, cfg.TIER_MIX, 1)[0])
            lo, hi = cfg.TIER_POINTS[tier]
            points = int(rng.integers(lo, hi + 1))
            handle = f"{first}.{last}".lower().replace(" ", "")
            rows.append({
                "customer_id": f"CUST{seq}",
                "first_name": first,
                "last_name": last,
                "gender": str(rng.choice(["Male", "Female", "Unknown"],
                                         p=[0.465, 0.454, 0.081])),
                "age": int(rng.integers(*cfg.AGE_RANGE)),
                "email": f"{handle}{rng.integers(1000, 9999)}@{rng.choice(cfg.EMAIL_DOMAINS)}",
                "phone": int(rng.choice([8, 9]) * 10_000_000 + rng.integers(0, 10_000_000)),
                "register_datetime": when.strftime("%Y-%m-%d %H:00"),
                "loyalty_points": points,
                "loyalty_tier": tier,
                "loyalty_value": points,
                "marketing_opt_in": bool(rng.random() < cfg.OPT_IN_RATE),
                "city": "Singapore",
                "country": "Singapore",
            })
            seq += 1

    for n in range(1, cfg.GUEST_ACCOUNTS + 1):
        rows.append({
            "customer_id": f"GUEST{1000 + n}",
            "first_name": "Guest", "last_name": str(n), "gender": "Unknown",
            "age": int(rng.integers(*cfg.AGE_RANGE)),
            "email": f"guest{n}@example.com",
            "phone": int(rng.choice([8, 9]) * 10_000_000 + rng.integers(0, 10_000_000)),
            "register_datetime": start.strftime("%Y-%m-%d 00:00"),
            "loyalty_points": 0, "loyalty_tier": "Bronze", "loyalty_value": 0,
            "marketing_opt_in": False, "city": "Singapore", "country": "Singapore",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# campaigns, events and paid traffic
# ---------------------------------------------------------------------------

def build_campaigns(rng: np.random.Generator, start: pd.Timestamp,
                    end: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame, List]:
    events: List[dict] = []
    traffic: List[dict] = []
    promos: List[Tuple] = []
    campaign_seq = 1
    event_seq = 1

    for year in range(start.year, end.year + 1):
        budget_growth = cfg.EVENT_SPEND_GROWTH ** (year - start.year)

        for theme, month, duration, lift in cfg.CAMPAIGN_THEMES:
            anchor = pd.Timestamp(year=year, month=month, day=1)
            # Move the anchor around inside the month so years do not align.
            begin = anchor + pd.Timedelta(days=int(rng.integers(2, 18)))
            if begin < start or begin > end:
                continue

            campaign_id = f"CAM{campaign_seq:04d}"
            campaign_seq += 1
            n_events = int(weighted_choice(rng, cfg.EVENTS_PER_CAMPAIGN, 1)[0])
            chosen = rng.choice(cfg.EVENT_TYPES, size=n_events, replace=False)

            low, high = cfg.PROMO_DEPTH[theme]
            depth = float(rng.uniform(low, high))
            promo_start = begin
            promo_end = begin + pd.Timedelta(days=duration)

            for event_type in chosen:
                offset = int(rng.integers(-2, 4))
                e_start = begin + pd.Timedelta(days=offset)
                e_days = int(rng.integers(max(5, duration - 4), duration + 7))
                e_end = e_start + pd.Timedelta(days=e_days)
                if e_start > end:
                    continue

                spend = money(rng.uniform(*cfg.EVENT_SPEND_RANGE) * budget_growth)
                roas_lo, roas_hi = cfg.ROAS_RANGE[event_type]
                attributed = money(spend * rng.uniform(roas_lo, roas_hi))
                event_id = f"EVT{event_seq:05d}"
                event_seq += 1

                events.append({
                    "event_id": event_id,
                    "campaign_id": campaign_id,
                    "event_name": f"{event_type} - {theme} {year}",
                    "event_type": event_type,
                    "campaign_theme": theme,
                    "start_date": e_start.date().isoformat(),
                    "end_date": e_end.date().isoformat(),
                    "marketing_spend_sgd": spend,
                    "attributed_sales_sgd": attributed,
                    "roas": round(attributed / spend, 2) if spend else 0.0,
                })

                # Split the event budget across paid channels so the two
                # marketing tables reconcile exactly.
                n_rows = int(weighted_choice(rng, cfg.TRAFFIC_ROWS_PER_EVENT, 1)[0])
                platforms = rng.choice(cfg.TRAFFIC_PLATFORMS, size=n_rows, replace=False)
                splits = rng.dirichlet(np.ones(n_rows) * 2.2)
                allocated = 0.0

                for i, (platform, share) in enumerate(zip(platforms, splits)):
                    is_last = i == n_rows - 1
                    p_spend = money(spend - allocated) if is_last else money(spend * share)
                    allocated += p_spend
                    impressions = int(rng.integers(*cfg.IMPRESSION_RANGE))
                    ctr = float(rng.uniform(*cfg.CTR_RANGE))
                    clicks = max(1, int(round(impressions * ctr / 100)))
                    sessions = int(round(clicks * rng.uniform(*cfg.SESSIONS_PER_CLICK)))
                    conversions = int(round(sessions * rng.uniform(*cfg.CONVERSION_RATE)))
                    traffic.append({
                        "event_id": event_id,
                        "campaign_id": campaign_id,
                        "platform": str(platform),
                        "start_date": e_start.date().isoformat(),
                        "end_date": e_end.date().isoformat(),
                        "impressions": impressions,
                        "clicks": clicks,
                        "ctr_pct": round(clicks / impressions * 100, 2),
                        "sessions": sessions,
                        "estimated_spend_sgd": p_spend,
                        "conversions": conversions,
                        "cost_per_click_sgd": round(p_spend / clicks, 3),
                        "cost_per_conversion_sgd": (
                            round(p_spend / conversions, 2) if conversions else None
                        ),
                    })

            promos.append((campaign_id, theme, depth, lift, promo_start, promo_end))

    return pd.DataFrame(events), pd.DataFrame(traffic), promos


def promo_calendar(promos, start: pd.Timestamp, end: pd.Timestamp) -> Dict:
    """Map each date to the campaign running on it, if any."""
    calendar: Dict[pd.Timestamp, Tuple[str, str, float, float]] = {}
    for campaign_id, theme, depth, lift, p_start, p_end in promos:
        for day in pd.date_range(max(p_start, start), min(p_end, end), freq="D"):
            current = calendar.get(day)
            # When campaigns overlap the deeper discount wins.
            if current is None or depth > current[2]:
                calendar[day] = (campaign_id, theme, depth, lift)
    return calendar


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

def build_orders(rng: np.random.Generator, cat: pd.DataFrame,
                 customers: pd.DataFrame, calendar: Dict,
                 start: pd.Timestamp, end: pd.Timestamp):
    days = pd.date_range(start, end, freq="D")
    total_years = max(end.year - start.year, 1)

    registered = customers[customers.customer_id.str.startswith("CUST")]
    reg_ids = registered.customer_id.to_numpy()
    reg_since = pd.to_datetime(registered.register_datetime).to_numpy()
    guest_ids = customers[customers.customer_id.str.startswith("GUEST")].customer_id.to_numpy()

    sku_ids = cat["sku"].to_numpy()
    sku_desc = cat["sku_description"].to_numpy()
    sku_price = cat["unit_price_sgd"].to_numpy(dtype=float)
    sku_cost = cat["unit_cost_sgd"].to_numpy(dtype=float)
    sku_cat = cat["category"].to_numpy()

    # Recompute purchase weights each year so the category mix drifts.
    weights_by_year = {
        year: calibrate_sku_weights(
            cat, lerp_mix(cfg.CATEGORY_MIX_START, cfg.CATEGORY_MIX_END,
                          (year - start.year) / total_years)
        )
        for year in range(start.year, end.year + 1)
    }
    channel_by_year = {
        year: lerp_mix(cfg.CHANNEL_MIX_START, cfg.CHANNEL_MIX_END,
                       (year - start.year) / total_years)
        for year in range(start.year, end.year + 1)
    }

    hours, hour_p = as_choice(cfg.HOUR_WEIGHTS)
    lines_k, lines_p = as_choice(cfg.LINES_PER_ORDER)
    corp_lines_k, corp_lines_p = as_choice(cfg.LINES_PER_ORDER_CORPORATE)
    qty_k, qty_p = as_choice(cfg.QTY_WEIGHTS)
    corp_qty_k, corp_qty_p = as_choice(cfg.QTY_WEIGHTS_CORPORATE)
    payments = np.array(cfg.PAYMENT_METHODS)
    guest_rate = cfg.GUEST_RATE

    # MONTH_INDEX was fitted on history that already included campaigns, so
    # applying raw lift on top would double-count the peaks. Normalising each
    # month by its own mean lift keeps the monthly total on the fitted index
    # while still concentrating demand into the promotional window.
    month_lift: Dict[Tuple[int, int], float] = {}
    for day in days:
        key = (day.year, day.month)
        entry = calendar.get(day)
        month_lift.setdefault(key, []).append(entry[3] if entry else 1.0)
    month_lift = {k: float(np.mean(v)) for k, v in month_lift.items()}

    sales: List[dict] = []
    ecom: List[dict] = []
    order_seq = 100_001

    for day in days:
        year, month = day.year, day.month
        promo = calendar.get(day)
        lift = promo[3] if promo else 1.0

        expected = (
            cfg.BASE_ORDERS_PER_DAY
            * cfg.YEAR_FACTOR.get(year, 1.0)
            * cfg.MONTH_INDEX[month]
            * cfg.DOW_INDEX[day.dayofweek]
            * cfg.COVID_FACTOR.get((year, month), 1.0)
            * (lift / month_lift.get((year, month), 1.0))
            * rng.normal(1.0, 0.07)
        )
        n_orders = int(rng.poisson(max(expected, 0.5)))
        if not n_orders:
            continue

        weights = weights_by_year[year]
        price_index = cfg.PRICE_INDEX.get(year, 1.0)
        mix = channel_by_year[year]
        channels = weighted_choice(rng, mix, n_orders)
        order_hours = rng.choice(hours, size=n_orders, p=hour_p)
        minutes = rng.integers(0, 60, size=n_orders)
        # Only customers who had registered by this date can buy.
        pool = reg_ids[reg_since <= day.to_datetime64()]
        if not len(pool):
            pool = guest_ids

        # Sample the per-order draws for the whole day in one go.
        corp_flags = channels == "Corporate"
        roll_guest = rng.random(n_orders)
        n_lines_all = np.where(
            corp_flags,
            rng.choice(corp_lines_k, size=n_orders, p=corp_lines_p),
            rng.choice(lines_k, size=n_orders, p=lines_p),
        )

        for idx in range(n_orders):
            channel = str(channels[idx])
            stamp = day + pd.Timedelta(hours=int(order_hours[idx]),
                                       minutes=int(minutes[idx]))
            order_id = f"ORD{order_seq}"
            order_seq += 1
            is_corporate = channel == "Corporate"

            if roll_guest[idx] < guest_rate.get(channel, 0.02):
                customer_id = str(rng.choice(guest_ids))
            else:
                customer_id = str(rng.choice(pool))

            n_lines = int(n_lines_all[idx])
            picks = rng.choice(len(sku_ids), size=n_lines, replace=False, p=weights)
            qty_k_use, qty_p_use = (
                (corp_qty_k, corp_qty_p) if is_corporate else (qty_k, qty_p)
            )
            quantities = rng.choice(qty_k_use, size=n_lines, p=qty_p_use)
            promo_rolls = rng.random(n_lines)
            depth_rolls = rng.uniform(0.7, 1.15, n_lines)

            subtotal = 0.0
            for line_no, pick in enumerate(picks, start=1):
                quantity = int(quantities[line_no - 1])
                list_price = money(sku_price[pick] * price_index)

                discount = 0.0
                campaign_id = None
                if promo and promo_rolls[line_no - 1] < cfg.PROMO_SKU_SHARE:
                    campaign_id = promo[0]
                    discount = min(round(promo[2] * depth_rolls[line_no - 1], 3), 0.45)

                unit_price = money(list_price * (1 - discount))
                gross = money(list_price * quantity)
                net = money(unit_price * quantity)
                cogs = money(sku_cost[pick] * price_index * quantity)
                subtotal += net

                sales.append({
                    "order_id": order_id,
                    "line_id": line_no,
                    "order_datetime": stamp.strftime("%Y-%m-%d %H:%M"),
                    "order_date": day.date().isoformat(),
                    "channel": channel,
                    "platform": channel if channel in cfg.ONLINE_CHANNELS else "",
                    "customer_id": customer_id,
                    "sku": sku_ids[pick],
                    "sku_description": sku_desc[pick],
                    "category": sku_cat[pick],
                    "list_price_sgd": list_price,
                    "discount_pct": round(discount * 100, 2),
                    "unit_price_sgd": unit_price,
                    "quantity": quantity,
                    "line_gross_sales_sgd": gross,
                    "line_discount_sgd": money(gross - net),
                    "line_net_sales_sgd": net,
                    "line_cogs_sgd": cogs,
                    "line_gross_profit_sgd": money(net - cogs),
                    "campaign_id": campaign_id,
                })

            if channel in cfg.ONLINE_CHANNELS:
                subtotal = money(subtotal)
                if subtotal >= cfg.FREE_SHIPPING_THRESHOLD:
                    shipping = 0.0
                elif rng.random() < cfg.VOUCHER_FREE_SHIPPING.get(channel, 0.0):
                    shipping = 0.0
                else:
                    shipping = cfg.SHIPPING_FEE.get(channel, 3.0)
                gst = money((subtotal + shipping) * gst_rate(day))
                ecom.append({
                    "order_id": order_id,
                    "order_datetime": stamp.strftime("%Y-%m-%d %H:%M"),
                    "channel": channel,
                    "platform": channel,
                    "payment_method": str(rng.choice(payments)),
                    "customer_id": customer_id,
                    "subtotal_sgd": subtotal,
                    "shipping_fee_sgd": shipping,
                    "gst_sgd": gst,
                    "grand_total_sgd": money(subtotal + shipping + gst),
                })

    return pd.DataFrame(sales), pd.DataFrame(ecom)


# ---------------------------------------------------------------------------
# closing stock
# ---------------------------------------------------------------------------

def attach_stock(rng: np.random.Generator, cat: pd.DataFrame,
                 sales: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    window_start = end - pd.Timedelta(days=90)
    recent = sales[pd.to_datetime(sales.order_date) >= window_start]
    daily = recent.groupby("sku").quantity.sum() / 90.0
    cover = rng.integers(cfg.STOCK_COVER_DAYS[0], cfg.STOCK_COVER_DAYS[1], len(cat))

    velocity = cat["sku"].map(daily).fillna(0.05).to_numpy(dtype=float)
    cat = cat.copy()
    cat["avg_daily_units_90d"] = np.round(velocity, 3)
    cat["current_stock_units"] = np.maximum(
        25, np.round(velocity * cover).astype(int)
    )
    cat["days_of_cover"] = np.round(
        cat["current_stock_units"] / np.maximum(velocity, 0.01), 1
    )
    order = [
        "sku", "category", "flavour", "pack_size_g", "sku_description",
        "unit_price_sgd", "unit_cost_sgd", "gross_margin_pct",
        "current_stock_units", "avg_daily_units_90d", "days_of_cover",
        "dietary_tags", "allergen_tags", "is_halal",
    ]
    return cat[[c for c in order if c in cat.columns]]


# ---------------------------------------------------------------------------
# faqs
# ---------------------------------------------------------------------------

def build_faqs() -> pd.DataFrame:
    rows = [
        ("FAQ001", "What delivery options do you offer?",
         "We deliver island-wide within 2-3 working days. Delivery is free on "
         f"orders above {cfg.CURRENCY} {cfg.FREE_SHIPPING_THRESHOLD:.0f}.",
         "delivery,shipping"),
        ("FAQ002", "Are your products Halal certified?",
         "Yes. Our nut and snack range is Halal certified unless a product page "
         "states otherwise.", "dietary,halal"),
        ("FAQ003", "Do your products contain allergens?",
         "Most items contain tree nuts and some contain peanuts or gluten. Every "
         "pack lists its allergens, and the allergen tags are on each product page.",
         "dietary,allergens"),
        ("FAQ004", "How should I store the nuts once opened?",
         "Reseal the pack and keep it in a cool, dry place away from sunlight. "
         "Refrigerating opened packs keeps them crisp for longer in Singapore humidity.",
         "storage,quality"),
        ("FAQ005", "What is your shelf life?",
         "Unopened packs keep for 9 to 12 months. The best-before date is printed "
         "on the back of every pack.", "storage,quality"),
        ("FAQ006", "Can I place a corporate or bulk order?",
         "Yes. We handle corporate gifting and bulk orders, including custom "
         "hampers and company-branded packaging. Lead time is 5 to 7 working days.",
         "corporate,bulk"),
        ("FAQ007", "What payment methods do you accept?",
         "PayNow, credit card, Atome, GrabPay and ShopeePay, depending on the "
         "store you order from.", "payment"),
        ("FAQ008", "Can I return or exchange a product?",
         "Yes, within 7 days of delivery if the pack is unopened. Contact us with "
         "your order number and we will arrange a replacement or refund.",
         "returns,refund"),
        ("FAQ009", "Do you ship outside Singapore?",
         "Not at the moment. We deliver within Singapore only, including to "
         "business addresses.", "delivery,international"),
        ("FAQ010", "How does the loyalty programme work?",
         "You earn points on every purchase and move through Bronze, Silver, Gold "
         "and Platinum tiers. Points convert to vouchers you can use at checkout.",
         "loyalty,rewards"),
        ("FAQ011", "Are your nuts suitable for vegans?",
         "Most are. Vegan-friendly items carry a vegan tag; honey-coated products "
         "are not vegan.", "dietary,vegan"),
        ("FAQ012", "Do you offer no-added-sugar or low-salt options?",
         "Yes. Look for the no_added_sugar and low_salt tags, which cover much of "
         "our natural baked and roasted range.", "dietary,health"),
        ("FAQ013", "When do you run promotions?",
         "Our biggest promotions run at Chinese New Year, Ramadan, National Day, "
         "Mid-Autumn, 11.11 and Christmas.", "promotions,pricing"),
        ("FAQ014", "What are your operating hours?",
         "Customer service runs 8am to 5pm Singapore time, Monday to Friday. "
         "Orders can be placed online any time.", "support,hours"),
        ("FAQ015", "Which stores stock your products?",
         "Our range is carried in major supermarkets and online at Shopee, Lazada, "
         "RedMart, GrabMart, FairPrice Online and our own website.", "retail,channels"),
    ]
    return pd.DataFrame(rows, columns=["faq_id", "question", "answer", "faq_tags"])


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=cfg.START_DATE)
    parser.add_argument("--end", default=cfg.END_DATE)
    parser.add_argument("--seed", type=int, default=cfg.SEED)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    print(f"Generating {start.date()} to {end.date()} with seed {args.seed}")

    catalog = build_catalog(rng)
    print(f"  catalogue      {len(catalog):>7,} SKUs")

    customers = build_customers(rng, start, end)
    print(f"  customers      {len(customers):>7,}")

    events, traffic, promos = build_campaigns(rng, start, end)
    calendar = promo_calendar(promos, start, end)
    print(f"  campaigns      {events.campaign_id.nunique():>7,} "
          f"({len(events):,} events, {len(traffic):,} channel rows)")

    sales, ecom = build_orders(rng, catalog, customers, calendar, start, end)
    print(f"  sales lines    {len(sales):>7,} across {sales.order_id.nunique():,} orders")
    print(f"  online orders  {len(ecom):>7,}")

    catalog = attach_stock(rng, catalog, sales, end)
    faqs = build_faqs()

    tables = {
        "sku_master.csv": catalog,
        "customers.csv": customers,
        "sales_transactions.csv": sales,
        "ecommerce_purchases.csv": ecom,
        "events.csv": events,
        "traffic_acquisition.csv": traffic,
        "faqs.csv": faqs,
    }
    for name, frame in tables.items():
        frame.to_csv(out / name, index=False)
        print(f"  wrote {name:<28} {len(frame):>8,} rows")

    print(f"\nDone. Output in {out}")


if __name__ == "__main__":
    main()
