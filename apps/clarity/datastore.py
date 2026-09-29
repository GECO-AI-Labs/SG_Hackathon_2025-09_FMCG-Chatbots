"""Loads the Cashew4Nuts tables and serves filtered slices of them.

Everything is held in memory. The dataset is a few hundred thousand rows, so
loading once at boot costs a second and removes per-question file reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

SALES = "sales_transactions"
ECOM = "ecommerce_purchases"
SKUS = "sku_master"
CUSTOMERS = "customers"
EVENTS = "events"
TRAFFIC = "traffic_acquisition"
FAQS = "faqs"

DATE_COLUMNS = {
    SALES: ["order_datetime", "order_date"],
    ECOM: ["order_datetime"],
    CUSTOMERS: ["register_datetime"],
    EVENTS: ["start_date", "end_date"],
    TRAFFIC: ["start_date", "end_date"],
}


class DataStore:
    """In-memory access to the dataset, with the joins Clarity needs."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.tables: Dict[str, pd.DataFrame] = {}
        self.errors: List[str] = []
        self._load()

    # -- loading ---------------------------------------------------------
    def _load(self) -> None:
        if not self.data_dir.is_dir():
            self.errors.append(f"Data directory not found: {self.data_dir}")
            return

        for path in sorted(self.data_dir.glob("*.csv")):
            name = path.stem.lower()
            try:
                frame = pd.read_csv(path, parse_dates=DATE_COLUMNS.get(name, None))
            except Exception as exc:                      # noqa: BLE001
                self.errors.append(f"{path.name}: {exc}")
                continue
            self.tables[name] = frame

        sales = self.tables.get(SALES)
        if sales is not None and "order_date" in sales.columns:
            sales["year"] = sales["order_date"].dt.year
            sales["month"] = sales["order_date"].dt.to_period("M").astype(str)
            sales["quarter"] = sales["order_date"].dt.to_period("Q").astype(str)
            # Pack size and flavour live on the master, not the line.
            master = self.tables.get(SKUS)
            if master is not None:
                extra = [
                    c for c in ("flavour", "pack_size_g", "gross_margin_pct")
                    if c in master.columns
                ]
                if extra:
                    self.tables[SALES] = sales.merge(
                        master[["sku"] + extra], on="sku", how="left"
                    )

    # -- accessors -------------------------------------------------------
    def table(self, name: str) -> pd.DataFrame:
        frame = self.tables.get(name)
        if frame is None:
            raise KeyError(f"Table '{name}' is not loaded.")
        return frame

    @property
    def ready(self) -> bool:
        return SALES in self.tables and SKUS in self.tables

    def date_range(self):
        sales = self.tables.get(SALES)
        if sales is None or sales.empty:
            return None, None
        return sales["order_date"].min(), sales["order_date"].max()

    def sales_slice(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        channel: Optional[str] = None,
        category: Optional[str] = None,
        sku: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """Filter sales lines. Unknown filter values return an empty frame."""
        frame = self.table(SALES)
        if start:
            frame = frame[frame["order_date"] >= pd.Timestamp(start)]
        if end:
            frame = frame[frame["order_date"] <= pd.Timestamp(end)]
        if channel:
            frame = frame[frame["channel"].str.lower() == channel.strip().lower()]
        if category:
            frame = frame[frame["category"].str.lower() == category.strip().lower()]
        if sku:
            frame = frame[frame["sku"].str.upper() == sku.strip().upper()]
        if campaign_id:
            frame = frame[frame["campaign_id"] == campaign_id.strip().upper()]
        return frame

    # -- prompt context --------------------------------------------------
    def schema_note(self) -> str:
        """A compact description of what is queryable, for the system prompt."""
        first, last = self.date_range()
        lines = [
            "Dataset: Cashew4Nuts, a Singapore nut and snack FMCG business.",
            f"Sales history runs {first:%Y-%m-%d} to {last:%Y-%m-%d}."
            if first is not None else "Sales history unavailable.",
            "",
            "Tables and row counts:",
        ]
        for name, frame in sorted(self.tables.items()):
            lines.append(f"  {name}: {len(frame):,} rows")

        sales = self.tables.get(SALES)
        if sales is not None:
            lines += [
                "",
                "Dimensions available on sales lines:",
                f"  channels: {', '.join(sorted(sales['channel'].dropna().unique()))}",
                f"  categories: {', '.join(sorted(sales['category'].dropna().unique()))}",
                "",
                "Money columns are in SGD and exclude GST. line_net_sales_sgd is "
                "revenue after discount; line_gross_profit_sgd is net sales less "
                "cost of goods.",
            ]
        if self.errors:
            lines.append("Load warnings: " + "; ".join(self.errors[:3]))
        return "\n".join(lines)
