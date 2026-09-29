"""The analytical toolkit Clarity calls to get real numbers.

Each public function runs a pandas query and returns a small, rounded dict.
The model never sees raw rows and never does arithmetic itself, so a figure in
an answer either came from here or is not in the answer.

Results are deliberately compact. Sending a hundred rows of detail into the
prompt costs latency and invites the model to summarise badly, so every
breakdown is capped and sorted with the interesting end first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from datastore import CUSTOMERS, ECOM, EVENTS, FAQS, SKUS, TRAFFIC, DataStore

MAX_ROWS = 25
MONEY = 2


def _money(value) -> float:
    return round(float(value), MONEY)


def _pct(part, whole) -> Optional[float]:
    whole = float(whole or 0)
    return round(float(part) / whole * 100, 2) if whole else None


def _metrics(frame: pd.DataFrame) -> Dict[str, Any]:
    """The standard measure set for any slice of sales lines."""
    if frame.empty:
        return {
            "orders": 0, "units": 0, "net_sales_sgd": 0.0, "gross_profit_sgd": 0.0,
            "gross_margin_pct": None, "aov_sgd": None, "discount_pct_of_gross": None,
        }
    net = frame["line_net_sales_sgd"].sum()
    orders = int(frame["order_id"].nunique())
    gross = frame["line_gross_sales_sgd"].sum()
    return {
        "orders": orders,
        "units": int(frame["quantity"].sum()),
        "net_sales_sgd": _money(net),
        "gross_profit_sgd": _money(frame["line_gross_profit_sgd"].sum()),
        "gross_margin_pct": _pct(frame["line_gross_profit_sgd"].sum(), net),
        "aov_sgd": _money(net / orders) if orders else None,
        "discount_pct_of_gross": _pct(frame["line_discount_sgd"].sum(), gross),
    }


def _delta(current: Dict[str, Any], prior: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("orders", "units", "net_sales_sgd", "gross_profit_sgd", "aov_sgd"):
        now, was = current.get(key), prior.get(key)
        if isinstance(now, (int, float)) and isinstance(was, (int, float)) and was:
            out[f"{key}_change_pct"] = round((now - was) / was * 100, 2)
    return out


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

class Analytics:
    """Bound to one DataStore; every tool is a method on this class."""

    def __init__(self, store: DataStore) -> None:
        self.store = store

    # -- orientation -----------------------------------------------------
    def data_dictionary(self) -> Dict[str, Any]:
        """What exists to be queried, and over what period."""
        first, last = self.store.date_range()
        sales = self.store.table("sales_transactions")
        return {
            "date_range": {
                "first_order": str(first.date()) if first is not None else None,
                "last_order": str(last.date()) if last is not None else None,
            },
            "row_counts": {k: len(v) for k, v in self.store.tables.items()},
            "channels": sorted(sales["channel"].dropna().unique().tolist()),
            "categories": sorted(sales["category"].dropna().unique().tolist()),
            "campaign_themes": (
                sorted(self.store.table(EVENTS)["campaign_theme"].dropna().unique().tolist())
                if EVENTS in self.store.tables
                and "campaign_theme" in self.store.table(EVENTS).columns else []
            ),
            "note": "Money is SGD and excludes GST unless a field name says otherwise.",
        }

    # -- headline --------------------------------------------------------
    def sales_overview(self, start: str = None, end: str = None,
                       channel: str = None, category: str = None,
                       compare_previous: bool = False) -> Dict[str, Any]:
        """Headline trading numbers for a period, optionally versus the period
        of equal length immediately before it."""
        frame = self.store.sales_slice(start, end, channel, category)
        result: Dict[str, Any] = {
            "period": {"start": start, "end": end},
            "filters": {"channel": channel, "category": category},
            "metrics": _metrics(frame),
        }
        if frame.empty:
            result["note"] = "No sales lines matched these filters."
            return result

        if compare_previous and start and end:
            a, b = pd.Timestamp(start), pd.Timestamp(end)
            span = b - a
            prior = self.store.sales_slice(
                (a - span - pd.Timedelta(days=1)).date().isoformat(),
                (a - pd.Timedelta(days=1)).date().isoformat(),
                channel, category,
            )
            result["previous_period"] = {
                "start": (a - span - pd.Timedelta(days=1)).date().isoformat(),
                "end": (a - pd.Timedelta(days=1)).date().isoformat(),
                "metrics": _metrics(prior),
            }
            result["change_vs_previous"] = _delta(result["metrics"],
                                                  result["previous_period"]["metrics"])
        return result

    # -- time series -----------------------------------------------------
    def sales_trend(self, start: str = None, end: str = None,
                    freq: str = "month", channel: str = None,
                    category: str = None, limit: int = MAX_ROWS) -> Dict[str, Any]:
        """Revenue, orders and margin over time. freq is week, month, quarter
        or year. Returns the most recent periods when the series is long."""
        frame = self.store.sales_slice(start, end, channel, category)
        if frame.empty:
            return {"periods": [], "note": "No sales lines matched these filters."}

        rule = {"week": "W", "month": "M", "quarter": "Q", "year": "Y"}.get(
            (freq or "month").lower(), "M"
        )
        grouped = frame.groupby(frame["order_date"].dt.to_period(rule))
        rows: List[Dict[str, Any]] = []
        for period, chunk in grouped:
            entry = {"period": str(period)}
            entry.update(_metrics(chunk))
            rows.append(entry)

        truncated = len(rows) > limit
        if truncated:
            rows = rows[-limit:]
        out: Dict[str, Any] = {"freq": freq, "periods": rows}
        if len(rows) > 1:
            first_rev = rows[0]["net_sales_sgd"] or 0
            last_rev = rows[-1]["net_sales_sgd"] or 0
            if first_rev:
                out["change_first_to_last_pct"] = round(
                    (last_rev - first_rev) / first_rev * 100, 2
                )
        if truncated:
            out["note"] = f"Showing the most recent {limit} periods."
        return out

    # -- breakdowns ------------------------------------------------------
    def breakdown(self, dimension: str, start: str = None, end: str = None,
                  metric: str = "net_sales_sgd", channel: str = None,
                  category: str = None, limit: int = MAX_ROWS) -> Dict[str, Any]:
        """Split a period by channel, category, flavour, pack_size_g, platform,
        campaign_theme or year, ranked by the chosen metric."""
        column = {
            "channel": "channel", "category": "category", "flavour": "flavour",
            "pack_size": "pack_size_g", "pack_size_g": "pack_size_g",
            "platform": "platform", "year": "year", "month": "month",
            "quarter": "quarter", "sku": "sku",
        }.get((dimension or "").lower())
        if not column:
            return {"error": f"Cannot break down by '{dimension}'.",
                    "supported": ["channel", "category", "flavour", "pack_size_g",
                                  "platform", "year", "month", "quarter", "sku"]}

        frame = self.store.sales_slice(start, end, channel, category)
        if frame.empty or column not in frame.columns:
            return {"rows": [], "note": "No sales lines matched these filters."}

        total = frame["line_net_sales_sgd"].sum()
        rows: List[Dict[str, Any]] = []
        for value, chunk in frame.groupby(column, dropna=False):
            entry = {dimension: ("(none)" if pd.isna(value) else value)}
            entry.update(_metrics(chunk))
            entry["share_of_net_sales_pct"] = _pct(chunk["line_net_sales_sgd"].sum(), total)
            rows.append(entry)

        key = metric if metric in rows[0] else "net_sales_sgd"
        rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))
        return {
            "dimension": dimension,
            "ranked_by": key,
            "total_net_sales_sgd": _money(total),
            "rows": rows[:limit],
        }

    def top_products(self, start: str = None, end: str = None,
                     metric: str = "net_sales_sgd", limit: int = 10,
                     category: str = None, channel: str = None,
                     worst_first: bool = False) -> Dict[str, Any]:
        """Best or worst selling SKUs. Set worst_first to find slow movers."""
        frame = self.store.sales_slice(start, end, channel, category)
        if frame.empty:
            return {"rows": [], "note": "No sales lines matched these filters."}

        rows: List[Dict[str, Any]] = []
        for (sku, desc), chunk in frame.groupby(["sku", "sku_description"]):
            entry = {"sku": sku, "sku_description": desc}
            entry.update(_metrics(chunk))
            rows.append(entry)

        key = metric if rows and metric in rows[0] else "net_sales_sgd"
        rows.sort(key=lambda r: (r.get(key) is None, (r.get(key) or 0) * (1 if worst_first else -1)))
        return {
            "ranked_by": key,
            "direction": "ascending" if worst_first else "descending",
            "rows": rows[: min(int(limit or 10), MAX_ROWS)],
        }

    def compare_periods(self, period_a_start: str, period_a_end: str,
                        period_b_start: str, period_b_end: str,
                        dimension: str = None, limit: int = 12) -> Dict[str, Any]:
        """Period A against period B, overall and optionally by dimension.
        Period A is the later or focus period."""
        a = self.store.sales_slice(period_a_start, period_a_end)
        b = self.store.sales_slice(period_b_start, period_b_end)
        result: Dict[str, Any] = {
            "period_a": {"start": period_a_start, "end": period_a_end,
                         "metrics": _metrics(a)},
            "period_b": {"start": period_b_start, "end": period_b_end,
                         "metrics": _metrics(b)},
        }
        result["change_a_vs_b"] = _delta(result["period_a"]["metrics"],
                                         result["period_b"]["metrics"])
        if not dimension:
            return result

        column = {"channel": "channel", "category": "category",
                  "flavour": "flavour", "sku": "sku"}.get(dimension.lower())
        if not column:
            result["note"] = f"Cannot split by '{dimension}'."
            return result

        rows: List[Dict[str, Any]] = []
        keys = sorted(set(a[column].dropna()) | set(b[column].dropna()))
        for value in keys:
            rev_a = a.loc[a[column] == value, "line_net_sales_sgd"].sum()
            rev_b = b.loc[b[column] == value, "line_net_sales_sgd"].sum()
            rows.append({
                dimension: value,
                "net_sales_a_sgd": _money(rev_a),
                "net_sales_b_sgd": _money(rev_b),
                "change_sgd": _money(rev_a - rev_b),
                "change_pct": round((rev_a - rev_b) / rev_b * 100, 2) if rev_b else None,
            })
        rows.sort(key=lambda r: -abs(r["change_sgd"]))
        result["by_" + dimension] = rows[:limit]
        return result

    # -- marketing -------------------------------------------------------
    def campaign_performance(self, start: str = None, end: str = None,
                             theme: str = None, limit: int = 15,
                             worst_first: bool = False) -> Dict[str, Any]:
        """Campaign spend, attributed sales and ROAS, plus the discounted
        revenue actually recorded against each campaign in sales."""
        if EVENTS not in self.store.tables:
            return {"error": "events table is not loaded."}
        events = self.store.table(EVENTS).copy()
        if start:
            events = events[events["start_date"] >= pd.Timestamp(start)]
        if end:
            events = events[events["start_date"] <= pd.Timestamp(end)]
        if theme and "campaign_theme" in events.columns:
            events = events[events["campaign_theme"].str.lower() == theme.strip().lower()]
        if events.empty:
            return {"rows": [], "note": "No campaigns in this window."}

        sales = self.store.table("sales_transactions")
        promo = (
            sales[sales["campaign_id"].notna()]
            .groupby("campaign_id")
            .agg(promo_net_sales_sgd=("line_net_sales_sgd", "sum"),
                 promo_discount_sgd=("line_discount_sgd", "sum"),
                 promo_units=("quantity", "sum"))
        )

        grouped = events.groupby("campaign_id").agg(
            theme=("campaign_theme", "first") if "campaign_theme" in events.columns
            else ("event_name", "first"),
            events=("event_id", "count"),
            first_start=("start_date", "min"),
            last_end=("end_date", "max"),
            spend_sgd=("marketing_spend_sgd", "sum"),
            attributed_sales_sgd=("attributed_sales_sgd", "sum"),
        ).join(promo)

        rows: List[Dict[str, Any]] = []
        for campaign_id, row in grouped.iterrows():
            spend = float(row["spend_sgd"])
            rows.append({
                "campaign_id": campaign_id,
                "theme": row.get("theme"),
                "window": f"{row['first_start']:%Y-%m-%d} to {row['last_end']:%Y-%m-%d}",
                "events": int(row["events"]),
                "spend_sgd": _money(spend),
                "attributed_sales_sgd": _money(row["attributed_sales_sgd"]),
                "roas": round(float(row["attributed_sales_sgd"]) / spend, 2) if spend else None,
                "promo_net_sales_sgd": _money(row.get("promo_net_sales_sgd") or 0),
                "promo_discount_given_sgd": _money(row.get("promo_discount_sgd") or 0),
                "promo_units": int(row.get("promo_units") or 0),
            })
        rows.sort(key=lambda r: (r["roas"] is None,
                                 (r["roas"] or 0) * (1 if worst_first else -1)))
        total_spend = sum(r["spend_sgd"] for r in rows)
        total_attr = sum(r["attributed_sales_sgd"] for r in rows)
        return {
            "campaigns": len(rows),
            "total_spend_sgd": _money(total_spend),
            "total_attributed_sales_sgd": _money(total_attr),
            "blended_roas": round(total_attr / total_spend, 2) if total_spend else None,
            "ranked_by": "roas " + ("ascending" if worst_first else "descending"),
            "rows": rows[:limit],
        }

    def traffic_funnel(self, start: str = None, end: str = None,
                       platform: str = None) -> Dict[str, Any]:
        """Paid media funnel by platform: impressions through conversions,
        with cost per click and cost per conversion."""
        if TRAFFIC not in self.store.tables:
            return {"error": "traffic_acquisition table is not loaded."}
        frame = self.store.table(TRAFFIC).copy()
        if start:
            frame = frame[frame["start_date"] >= pd.Timestamp(start)]
        if end:
            frame = frame[frame["start_date"] <= pd.Timestamp(end)]
        if platform:
            frame = frame[frame["platform"].str.lower() == platform.strip().lower()]
        if frame.empty:
            return {"rows": [], "note": "No paid media rows in this window."}

        rows: List[Dict[str, Any]] = []
        for name, chunk in frame.groupby("platform"):
            impressions = int(chunk["impressions"].sum())
            clicks = int(chunk["clicks"].sum())
            sessions = int(chunk["sessions"].sum())
            conversions = int(chunk["conversions"].sum())
            spend = float(chunk["estimated_spend_sgd"].sum())
            rows.append({
                "platform": name,
                "impressions": impressions,
                "clicks": clicks,
                "ctr_pct": _pct(clicks, impressions),
                "sessions": sessions,
                "conversions": conversions,
                "conversion_rate_pct": _pct(conversions, sessions),
                "spend_sgd": _money(spend),
                "cost_per_click_sgd": round(spend / clicks, 3) if clicks else None,
                "cost_per_conversion_sgd": round(spend / conversions, 2) if conversions else None,
            })
        rows.sort(key=lambda r: -r["spend_sgd"])
        return {"rows": rows, "total_spend_sgd": _money(frame["estimated_spend_sgd"].sum())}

    # -- customers -------------------------------------------------------
    def customer_insights(self, dimension: str = "loyalty_tier",
                          start: str = None, end: str = None) -> Dict[str, Any]:
        """Buying behaviour split by loyalty_tier, age_band or gender.
        Guest (walk-in) orders are reported separately since they carry no
        customer profile."""
        if CUSTOMERS not in self.store.tables:
            return {"error": "customers table is not loaded."}
        sales = self.store.sales_slice(start, end)
        if sales.empty:
            return {"rows": [], "note": "No sales lines in this window."}

        customers = self.store.table(CUSTOMERS).copy()
        if dimension == "age_band":
            customers["age_band"] = pd.cut(
                customers["age"], [17, 24, 34, 44, 54, 64, 120],
                labels=["18-24", "25-34", "35-44", "45-54", "55-64", "65+"],
            )
        column = dimension if dimension in customers.columns else "loyalty_tier"

        merged = sales.merge(
            customers[["customer_id", column]], on="customer_id", how="left"
        )
        guests = merged[merged["customer_id"].str.startswith("GUEST")]
        known = merged[~merged["customer_id"].str.startswith("GUEST")]

        rows: List[Dict[str, Any]] = []
        for value, chunk in known.groupby(column, observed=True):
            entry = {dimension: str(value), "customers": int(chunk["customer_id"].nunique())}
            entry.update(_metrics(chunk))
            orders_per = entry["orders"] / entry["customers"] if entry["customers"] else None
            entry["orders_per_customer"] = round(orders_per, 2) if orders_per else None
            rows.append(entry)
        rows.sort(key=lambda r: -(r["net_sales_sgd"] or 0))

        repeat = known.groupby("customer_id")["order_id"].nunique()
        return {
            "dimension": dimension,
            "rows": rows,
            "repeat_purchase_rate_pct": _pct((repeat > 1).sum(), len(repeat)),
            "guest_walk_in": _metrics(guests),
        }

    # -- inventory -------------------------------------------------------
    def inventory_risk(self, max_days_cover: float = 30.0,
                       min_days_cover: float = 150.0,
                       category: str = None, limit: int = 15) -> Dict[str, Any]:
        """SKUs at risk of stocking out, and SKUs sitting on excess cover.
        Cover is current stock divided by average daily units over 90 days."""
        if SKUS not in self.store.tables:
            return {"error": "sku_master table is not loaded."}
        frame = self.store.table(SKUS).copy()
        if category:
            frame = frame[frame["category"].str.lower() == category.strip().lower()]
        if frame.empty or "days_of_cover" not in frame.columns:
            return {"note": "Stock cover is unavailable for this selection."}

        columns = ["sku", "sku_description", "category", "current_stock_units",
                   "avg_daily_units_90d", "days_of_cover", "unit_price_sgd"]
        columns = [c for c in columns if c in frame.columns]

        low = frame[frame["days_of_cover"] <= max_days_cover].nsmallest(
            limit, "days_of_cover")
        high = frame[frame["days_of_cover"] >= min_days_cover].nlargest(
            limit, "days_of_cover")
        stock_value = (frame["current_stock_units"] * frame.get(
            "unit_cost_sgd", frame["unit_price_sgd"])).sum()
        return {
            "thresholds": {"low_cover_days": max_days_cover,
                           "excess_cover_days": min_days_cover},
            "skus_reviewed": len(frame),
            "stock_at_cost_sgd": _money(stock_value),
            "at_risk_of_stockout": low[columns].round(2).to_dict("records"),
            "excess_cover": high[columns].round(2).to_dict("records"),
        }

    # -- lookups ---------------------------------------------------------
    def product_lookup(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Find SKUs by code, description, category or flavour."""
        frame = self.store.table(SKUS)
        needle = (query or "").strip().lower()
        if not needle:
            return {"rows": [], "note": "Empty query."}
        haystack = (
            frame["sku"].str.lower() + " " + frame["sku_description"].str.lower()
            + " " + frame["category"].str.lower() + " " + frame["flavour"].str.lower()
        )
        hits = frame[haystack.str.contains(needle, regex=False)]
        return {
            "matches": len(hits),
            "rows": hits.head(min(int(limit or 10), MAX_ROWS)).round(2).to_dict("records"),
        }

    def faq_lookup(self, query: str = None, limit: int = 8) -> Dict[str, Any]:
        """Published customer-facing policy answers, useful when a staff
        question is really about what customers have been told."""
        if FAQS not in self.store.tables:
            return {"error": "faqs table is not loaded."}
        frame = self.store.table(FAQS)
        if query:
            needle = query.strip().lower()
            mask = (
                frame["question"].str.lower().str.contains(needle, regex=False)
                | frame["answer"].str.lower().str.contains(needle, regex=False)
                | frame["faq_tags"].str.lower().str.contains(needle, regex=False)
            )
            frame = frame[mask]
        return {"rows": frame.head(limit).to_dict("records")}

    def basket_economics(self, start: str = None, end: str = None,
                         channel: str = None) -> Dict[str, Any]:
        """Order economics including delivery and GST, from the online orders
        table. Walk-in channels are excluded because they carry no fees."""
        if ECOM not in self.store.tables:
            return {"error": "ecommerce_purchases table is not loaded."}
        frame = self.store.table(ECOM).copy()
        if start:
            frame = frame[frame["order_datetime"] >= pd.Timestamp(start)]
        if end:
            frame = frame[frame["order_datetime"] <= pd.Timestamp(end)]
        if channel:
            frame = frame[frame["channel"].str.lower() == channel.strip().lower()]
        if frame.empty:
            return {"note": "No online orders matched these filters."}

        rows: List[Dict[str, Any]] = []
        for name, chunk in frame.groupby("channel"):
            rows.append({
                "channel": name,
                "orders": len(chunk),
                "subtotal_sgd": _money(chunk["subtotal_sgd"].sum()),
                "avg_basket_sgd": _money(chunk["subtotal_sgd"].mean()),
                "delivery_collected_sgd": _money(chunk["shipping_fee_sgd"].sum()),
                "free_delivery_share_pct": _pct((chunk["shipping_fee_sgd"] == 0).sum(),
                                                len(chunk)),
                "gst_collected_sgd": _money(chunk["gst_sgd"].sum()),
            })
        rows.sort(key=lambda r: -r["subtotal_sgd"])
        payments = (
            frame["payment_method"].value_counts(normalize=True).mul(100).round(1)
            .to_dict()
        )
        return {"rows": rows, "payment_mix_pct": payments}


# ---------------------------------------------------------------------------
# tool schemas handed to the model
# ---------------------------------------------------------------------------

_DATE = {"type": "string", "description": "ISO date, YYYY-MM-DD"}


def _tool(name: str, description: str, properties: Dict[str, Any],
          required: List[str] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _tool("data_dictionary",
          "List the tables, row counts, date range, channels, categories and "
          "campaign themes available. Call this first when unsure what exists.",
          {}),
    _tool("sales_overview",
          "Headline trading numbers for a period: orders, units, net sales, "
          "gross profit, margin, AOV and discount depth. Set compare_previous "
          "to also return the preceding period of equal length.",
          {"start": _DATE, "end": _DATE,
           "channel": {"type": "string"}, "category": {"type": "string"},
           "compare_previous": {"type": "boolean"}}),
    _tool("sales_trend",
          "Time series of revenue, orders and margin. Use for trends, growth, "
          "seasonality and year-on-year questions.",
          {"start": _DATE, "end": _DATE,
           "freq": {"type": "string", "enum": ["week", "month", "quarter", "year"]},
           "channel": {"type": "string"}, "category": {"type": "string"},
           "limit": {"type": "integer"}}),
    _tool("breakdown",
          "Split a period by one dimension and rank it. Use for mix, share and "
          "'which X performs best' questions.",
          {"dimension": {"type": "string",
                         "enum": ["channel", "category", "flavour", "pack_size_g",
                                  "platform", "year", "month", "quarter", "sku"]},
           "start": _DATE, "end": _DATE,
           "metric": {"type": "string",
                      "enum": ["net_sales_sgd", "gross_profit_sgd", "units",
                               "orders", "gross_margin_pct", "aov_sgd"]},
           "channel": {"type": "string"}, "category": {"type": "string"},
           "limit": {"type": "integer"}},
          ["dimension"]),
    _tool("top_products",
          "Best or worst performing SKUs. Set worst_first true for slow movers.",
          {"start": _DATE, "end": _DATE,
           "metric": {"type": "string",
                      "enum": ["net_sales_sgd", "gross_profit_sgd", "units",
                               "orders", "gross_margin_pct"]},
           "limit": {"type": "integer"}, "category": {"type": "string"},
           "channel": {"type": "string"}, "worst_first": {"type": "boolean"}}),
    _tool("compare_periods",
          "Compare two explicit date ranges, overall and optionally split by a "
          "dimension, showing where the variance sits.",
          {"period_a_start": _DATE, "period_a_end": _DATE,
           "period_b_start": _DATE, "period_b_end": _DATE,
           "dimension": {"type": "string",
                         "enum": ["channel", "category", "flavour", "sku"]},
           "limit": {"type": "integer"}},
          ["period_a_start", "period_a_end", "period_b_start", "period_b_end"]),
    _tool("campaign_performance",
          "Campaign spend, attributed sales, ROAS and the discounted revenue "
          "recorded against each campaign. Use for marketing ROI questions.",
          {"start": _DATE, "end": _DATE, "theme": {"type": "string"},
           "limit": {"type": "integer"}, "worst_first": {"type": "boolean"}}),
    _tool("traffic_funnel",
          "Paid media funnel by platform: impressions, clicks, CTR, sessions, "
          "conversions, cost per click and cost per conversion.",
          {"start": _DATE, "end": _DATE, "platform": {"type": "string"}}),
    _tool("customer_insights",
          "Buying behaviour by loyalty_tier, age_band or gender, with repeat "
          "purchase rate. Walk-in guest orders are reported separately.",
          {"dimension": {"type": "string",
                         "enum": ["loyalty_tier", "age_band", "gender"]},
           "start": _DATE, "end": _DATE}),
    _tool("inventory_risk",
          "SKUs close to stocking out and SKUs holding excess cover, based on "
          "stock against 90-day demand.",
          {"max_days_cover": {"type": "number"},
           "min_days_cover": {"type": "number"},
           "category": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("basket_economics",
          "Online order economics including average basket, delivery collected, "
          "free delivery share, GST and payment mix.",
          {"start": _DATE, "end": _DATE, "channel": {"type": "string"}}),
    _tool("product_lookup",
          "Find SKUs by code, description, category or flavour, with price, "
          "cost, margin and stock.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}},
          ["query"]),
    _tool("faq_lookup",
          "The published customer-facing policy answers, for questions about "
          "what customers have been told.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}),
]

TOOL_NAMES = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
