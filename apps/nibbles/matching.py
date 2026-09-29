"""Turning what a customer typed into catalogue search terms.

Singapore customers mix languages freely, so a request can arrive as
"roasted peanuts 150g", "烤花生 150克" or "kacang panggang". Everything is
folded to a small set of English tokens before the catalogue is searched.
"""

from __future__ import annotations

import re
from typing import Optional, Set, Tuple

from rapidfuzz import fuzz
from unidecode import unidecode

# --- product families and styles ------------------------------------------
SYNONYMS = {
    "peanut": ["花生", "落花生", "ピーナッツ", "땅콩", "kacang tanah", "mani",
               "peanuts", "peanut"],
    "almond": ["杏仁", "扁桃", "アーモンド", "아몬드", "almendra", "badam",
               "almonds", "almond"],
    "cashew": ["腰果", "カシューナッツ", "캐슈넛", "kacang mete", "kasuy",
               "cashews", "cashew"],
    "pistachio": ["开心果", "開心果", "ピスタチオ", "피스타치오", "pista",
                  "pistachios", "pistachio"],
    "macadamia": ["夏威夷果", "マカダミア", "마카다미아", "macadamias", "macadamia"],
    "walnut": ["核桃", "くるみ", "호두", "walnuts", "walnut"],
    "mixed": ["什錦", "什锦", "混合", "ミックス", "믹스", "campuran",
              "mixed nuts", "assorted", "mixed"],
    "snack": ["零食", "スナック", "간식", "makanan ringan", "snacks", "snack"],
    # styles
    "roasted": ["烤", "焙煎", "焼", "ロースト", "볶음", "볶은", "panggang",
                "sangrai", "roasted"],
    "baked": ["烘烤", "烘焙", "ベイクド", "구운", "baked"],
    "natural": ["原味", "素焼き", "plain", "natural"],
    "salted": ["咸", "鹹", "盐味", "鹽味", "塩", "소금", "asin", "salted"],
    "smoked": ["烟熏", "煙燻", "スモーク", "훈제", "smoked"],
    "honey": ["蜂蜜", "はちみつ", "허니", "madu", "honey"],
    "sugar": ["糖", "砂糖", "설탕", "gula", "sugar", "sweet"],
    "cracker": ["饼干", "餅乾", "クラッカー", "cracker"],
    # intents
    "price": ["价格", "價錢", "多少钱", "多少錢", "いくら", "ราคา", "berapa",
              "magkano", "harga", "how much", "price", "cost"],
    "halal": ["清真", "ハラル", "할랄", "halal"],
    "vegan": ["纯素", "純素", "ヴィーガン", "비건", "vegan"],
    "delivery": ["配送", "送货", "送貨", "配達", "배송", "penghantaran",
                 "delivery", "shipping"],
}

_SUBSTITUTIONS = [
    (re.compile(re.escape(alt), re.IGNORECASE), canonical)
    for canonical, alternatives in SYNONYMS.items()
    for alt in sorted(set(alternatives), key=len, reverse=True)
]

FAMILIES = {"peanut", "almond", "cashew", "pistachio", "macadamia", "walnut",
            "mixed", "snack"}
STYLES = {"roasted", "baked", "natural", "salted", "smoked", "honey", "sugar",
          "cracker"}

# Catalogue category names, keyed by the canonical family token.
FAMILY_TO_CATEGORY = {
    "peanut": "Peanuts", "almond": "Almonds", "cashew": "Cashews",
    "pistachio": "Pistachios", "macadamia": "Macadamia", "walnut": "Walnuts",
    "mixed": "Mixed Nuts", "snack": "Snacks",
}

# --- pack sizes -----------------------------------------------------------
_SIZE_PATTERNS = [
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kilogram|kilo|公斤|千克|キロ|킬로|กก)",
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>g|gram|grams|克|公克|グラム|그램|กรัม)",
]
_SIZE_RE = [re.compile(p, re.IGNORECASE) for p in _SIZE_PATTERNS]
_KILO_UNITS = {"kg", "kilogram", "kilo", "公斤", "千克", "キロ", "킬로", "กก"}


def parse_grams(text: str) -> Optional[int]:
    """Pull a pack size out of free text, normalised to grams."""
    for pattern in _SIZE_RE:
        match = pattern.search(str(text))
        if not match:
            continue
        try:
            value = float(match.group("num").replace(",", "."))
        except ValueError:
            continue
        unit = match.group("unit").lower()
        return int(round(value * 1000)) if unit in _KILO_UNITS else int(round(value))
    return None


def to_english(text: str) -> str:
    """Fold known alternatives to canonical English tokens.

    Replacements are space-padded because CJK text carries no word breaks:
    without the padding "开心果多少钱" becomes the single token
    "pistachioprice" and both the product and the intent are lost.
    """
    result = str(text)
    for pattern, canonical in _SUBSTITUTIONS:
        result = pattern.sub(f" {canonical} ", result)
    return re.sub(r"\s+", " ", result).strip()


def normalise(text: str) -> str:
    folded = unidecode(str(text)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", folded)).strip()


FUZZY_FAMILY_THRESHOLD = 82


def extract(text: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """Return the families, styles and intents found in a message."""
    words = set(normalise(to_english(text)).split())
    families = {f for f in FAMILIES if f in words or f + "s" in words}

    # "casshew", "pistachos": close enough to name the product they meant.
    if not families:
        for word in words:
            if len(word) < 5:
                continue
            for family in FAMILIES:
                if max(fuzz.ratio(word, family),
                       fuzz.ratio(word, family + "s")) >= FUZZY_FAMILY_THRESHOLD:
                    families.add(family)
                    break
    styles = {s for s in STYLES if s in words or s + "s" in words}
    intents = {i for i in ("price", "halal", "vegan", "delivery") if i in words}
    if "how" in words and "much" in words:
        intents.add("price")
    return families, styles, intents


def looks_like_product_question(text: str) -> bool:
    families, styles, intents = extract(text)
    return bool(families or styles or intents or parse_grams(text))
