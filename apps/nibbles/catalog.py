"""The product catalogue Nibbles is allowed to talk about.

Two rules shape this module. First, the customer bot loads only the
customer-safe columns: unit cost and gross margin exist in sku_master and must
never reach a shopper, so they are dropped at load rather than filtered later.
Second, every answer is grounded here, which means a lookup that finds nothing
has to say so instead of inventing a product.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd
from rapidfuzz import fuzz, process

import matching

# Everything else in sku_master stays server-side.
CUSTOMER_SAFE_COLUMNS = [
    "sku", "category", "flavour", "pack_size_g", "sku_description",
    "unit_price_sgd", "dietary_tags", "allergen_tags", "is_halal",
]
CONFIDENTIAL_COLUMNS = [
    "unit_cost_sgd", "gross_margin_pct", "current_stock_units",
    "avg_daily_units_90d", "days_of_cover",
]


def image_path(category, flavour, grams) -> str:
    """Static art for a SKU. Mirrors the naming in tools/generate_product_art.py,
    which draws one image per category, flavour and pack form."""
    def slug(text) -> str:
        return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    try:
        g = int(grams)
    except (TypeError, ValueError):
        g = 150
    form = "snack" if g <= 80 else "pouch" if g <= 150 else "jar"
    return f"/static/products/{slug(category)}--{slug(flavour)}--{form}.svg"


class Catalog:
    """Product lookup over sku_master, plus the published FAQ answers."""

    def __init__(self, data_dir: Path, currency: str = "SGD") -> None:
        self.data_dir = Path(data_dir)
        self.currency = currency
        self.errors: List[str] = []

        master = self._find("sku_master.csv")
        if master is None:
            raise FileNotFoundError(f"sku_master.csv not found under {data_dir}")
        frame = pd.read_csv(master)
        if frame.empty:
            raise ValueError("sku_master.csv is empty.")

        dropped = [c for c in CONFIDENTIAL_COLUMNS if c in frame.columns]
        frame = frame[[c for c in CUSTOMER_SAFE_COLUMNS if c in frame.columns]].copy()
        self.dropped_columns = dropped

        frame["_search"] = (
            frame["sku_description"].astype(str) + " "
            + frame["category"].astype(str) + " "
            + frame["flavour"].astype(str)
        ).map(matching.normalise)
        frame["_grams"] = pd.to_numeric(frame["pack_size_g"], errors="coerce")
        frame["_price"] = pd.to_numeric(frame["unit_price_sgd"], errors="coerce")
        self.df = frame
        self._choices = frame["_search"].tolist()

        self.faqs = pd.DataFrame()
        faq_path = self._find("faqs.csv")
        if faq_path is not None:
            try:
                self.faqs = pd.read_csv(faq_path)
            except Exception as exc:                            # noqa: BLE001
                self.errors.append(f"faqs.csv: {exc}")

    # -- loading ---------------------------------------------------------
    def _find(self, filename: str) -> Optional[Path]:
        base = self.data_dir
        if base.is_file():
            return base if base.name.lower() == filename.lower() else None
        for path in base.rglob("*"):
            if path.is_file() and path.name.lower() == filename.lower():
                return path
        return None

    # -- formatting ------------------------------------------------------
    def price(self, value) -> str:
        if value is None or pd.isna(value):
            return "price on request"
        return f"{self.currency} {float(value):,.2f}"

    def _rows(self, frame: pd.DataFrame, limit: int) -> List[Dict]:
        out: List[Dict] = []
        for _, row in frame.head(limit).iterrows():
            out.append({
                "sku": row["sku"],
                "name": row["sku_description"],
                "category": row.get("category"),
                "flavour": row.get("flavour"),
                "grams": int(row["_grams"]) if pd.notna(row["_grams"]) else None,
                "price": None if pd.isna(row["_price"]) else float(row["_price"]),
                "price_display": self.price(row["_price"]),
                "dietary_tags": str(row.get("dietary_tags") or ""),
                "allergen_tags": str(row.get("allergen_tags") or ""),
                "is_halal": bool(row.get("is_halal", False)),
                "image": image_path(row.get("category"), row.get("flavour"),
                                    row["_grams"] if pd.notna(row["_grams"]) else None),
            })
        return out

    # -- search ----------------------------------------------------------
    def by_tokens(self, families: Set[str], styles: Set[str],
                  grams: Optional[int] = None, limit: int = 8) -> List[Dict]:
        """Exact-ish match on category and flavour, then nearest pack size."""
        frame = self.df
        if families:
            wanted = {matching.FAMILY_TO_CATEGORY[f] for f in families
                      if f in matching.FAMILY_TO_CATEGORY}
            if wanted:
                frame = frame[frame["category"].isin(wanted)]
        if styles and not frame.empty:
            pattern = r"\b(" + "|".join(sorted(styles)) + r")\b"
            frame = frame[frame["_search"].str.contains(pattern, regex=True, na=False)]
        if frame.empty:
            return []

        if grams:
            distance = frame["_grams"].apply(
                lambda g: abs(int(g) - grams) if pd.notna(g) else 10_000
            )
            frame = frame.assign(_distance=distance).sort_values(
                ["_distance", "_price"], na_position="last")
        else:
            frame = frame.sort_values(["_grams", "_price"], na_position="last")
        return self._rows(frame, limit)

    def fuzzy(self, query: str, grams: Optional[int] = None,
              limit: int = 6) -> List[Dict]:
        """Fallback search for spelling slips and phrasings we do not model."""
        needle = matching.normalise(matching.to_english(query))
        if not needle:
            return []
        hits = process.extract(needle, self._choices, scorer=fuzz.WRatio,
                               limit=limit * 4)
        scored = []
        for _text, score, index in hits:
            row = self.df.iloc[index]
            penalty = 0.0
            if grams and pd.notna(row["_grams"]):
                penalty = min(30.0, abs(int(row["_grams"]) - grams) / 10.0)
            scored.append((score - penalty, index))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        keep = [index for score, index in scored[:limit] if score >= 55]
        return self._rows(self.df.iloc[keep], limit) if keep else []

    def search(self, query: str, limit: int = 8) -> List[Dict]:
        """The single entry point used by the app."""
        families, styles, _ = matching.extract(query)
        grams = matching.parse_grams(matching.to_english(query)) or \
            matching.parse_grams(query)
        items = self.by_tokens(families, styles, grams, limit) if (families or styles) else []
        return items or self.fuzzy(query, grams, limit)

    def suggest(self, families: Set[str], styles: Set[str],
                exclude: Set[str], limit: int = 3) -> List[Dict]:
        """Nudges based on what this shopper has shown interest in."""
        items = self.by_tokens(families, styles, None, limit * 4)
        if not items:
            items = self._rows(self.df.sample(min(limit * 3, len(self.df)),
                                              random_state=7), limit * 3)
        fresh = [item for item in items if item["name"] not in exclude]
        return (fresh or items)[:limit]

    def faq_matches(self, query: str, limit: int = 3) -> List[Dict]:
        if self.faqs.empty:
            return []
        needle = matching.normalise(query)
        if not needle:
            return []
        terms = [t for t in needle.split() if len(t) > 3]
        if not terms:
            return []
        haystack = (
            self.faqs["question"].astype(str) + " "
            + self.faqs["answer"].astype(str) + " "
            + self.faqs["faq_tags"].astype(str)
        ).str.lower()
        mask = haystack.apply(lambda text: any(term in text for term in terms))
        return self.faqs[mask].head(limit)[["question", "answer"]].to_dict("records")
