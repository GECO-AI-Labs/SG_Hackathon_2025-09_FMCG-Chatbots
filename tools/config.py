"""Tunable parameters for the Cashew4Nuts synthetic dataset.

Everything the generator uses to shape the data lives here so the numbers can
be adjusted without touching generation logic. Values covering 2019-2025 were
fitted to the original dataset; values from 2026 onward are the forward plan.
"""

from __future__ import annotations

SEED = 20250929

START_DATE = "2019-01-01"
END_DATE = "2030-12-31"

CURRENCY = "SGD"
COMPANY = "Cashew4Nuts"

# Orders per day in the 2019 baseline year, before any index is applied.
BASE_ORDERS_PER_DAY = 31.9

# Volume relative to 2019. History is fitted; 2026 onward is the growth plan.
YEAR_FACTOR = {
    2019: 1.000,
    2020: 0.950,
    2021: 0.974,
    2022: 1.030,
    2023: 1.033,
    2024: 1.017,
    2025: 1.000,
    2026: 1.075,   # +7.5%
    2027: 1.161,   # +8.0%
    2028: 1.242,   # +7.0%
    2029: 1.317,   # +6.0%
    2030: 1.396,   # +6.0%
}

# Revenue seasonality. January is Chinese New Year, November is 11.11 and
# Black Friday, December is Christmas and corporate gifting. May to June and
# September to October are the school-holiday and post-peak troughs.
MONTH_INDEX = {
    1: 1.198, 2: 1.074, 3: 1.020, 4: 0.940, 5: 0.877, 6: 0.856,
    7: 0.971, 8: 0.988, 9: 0.892, 10: 0.892, 11: 1.202, 12: 1.121,
}

# Monday through Sunday. Weekend retail and marketplace browsing lifts volume.
DOW_INDEX = {0: 0.960, 1: 0.950, 2: 0.955, 3: 0.973, 4: 0.978, 5: 1.090, 6: 1.095}

# Singapore circuit breaker and the recovery that followed.
COVID_FACTOR = {
    (2020, 4): 0.84, (2020, 5): 0.79, (2020, 6): 0.79,
    (2020, 7): 1.06, (2020, 8): 1.08, (2020, 9): 1.20, (2020, 10): 0.96,
}

# Orders open at 09:00 and close at 21:00, with an evening browsing peak.
HOUR_WEIGHTS = {
    9: 0.85, 10: 0.95, 11: 1.00, 12: 1.10, 13: 1.05, 14: 0.95,
    15: 0.92, 16: 0.95, 17: 1.00, 18: 1.05, 19: 1.15, 20: 1.20, 21: 1.05,
}

# Share of orders by channel in 2019 and in 2030. The generator interpolates
# between them so the e-commerce shift shows up as a trend, not a step.
CHANNEL_MIX_START = {
    "Supermarket": 0.2767, "Offline_Retail": 0.1905, "Shopee": 0.1857,
    "Lazada": 0.1235, "Website": 0.1047, "Corporate": 0.0666,
    "GrabMart": 0.0216, "FairPrice_Online": 0.0208, "RedMart": 0.0099,
}
CHANNEL_MIX_END = {
    "Supermarket": 0.2000, "Offline_Retail": 0.1250, "Shopee": 0.2250,
    "Lazada": 0.1500, "Website": 0.1500, "Corporate": 0.0800,
    "GrabMart": 0.0300, "FairPrice_Online": 0.0250, "RedMart": 0.0150,
}

ONLINE_CHANNELS = [
    "Shopee", "Lazada", "Website", "GrabMart", "FairPrice_Online", "RedMart",
]
WALK_IN_CHANNELS = ["Supermarket", "Offline_Retail"]

# Walk-in trade is mostly anonymous; online and corporate trade is not.
GUEST_RATE = {
    "Supermarket": 0.271, "Offline_Retail": 0.267, "Corporate": 0.022,
    "Shopee": 0.018, "Lazada": 0.018, "Website": 0.020,
    "GrabMart": 0.018, "FairPrice_Online": 0.020, "RedMart": 0.018,
}

# Corporate gifting buys deeper baskets than a supermarket impulse purchase.
LINES_PER_ORDER = {1: 0.4493, 2: 0.3518, 3: 0.1498, 4: 0.0491}
LINES_PER_ORDER_CORPORATE = {1: 0.24, 2: 0.37, 3: 0.25, 4: 0.11, 5: 0.03}
QTY_WEIGHTS = {1: 0.6201, 2: 0.2970, 3: 0.0631, 4: 0.0137, 5: 0.0061}
QTY_WEIGHTS_CORPORATE = {1: 0.30, 2: 0.33, 3: 0.21, 4: 0.11, 6: 0.04, 8: 0.01}

# Target revenue share per category in 2019 and 2030. Premium nuts gain share
# as disposable income and gifting demand grow; peanuts slowly give it up.
CATEGORY_MIX_START = {
    "Cashews": 0.2575, "Almonds": 0.1875, "Mixed Nuts": 0.1372,
    "Macadamia": 0.1341, "Pistachios": 0.1103, "Peanuts": 0.0705,
    "Walnuts": 0.0718, "Snacks": 0.0310,
}
CATEGORY_MIX_END = {
    "Cashews": 0.2600, "Almonds": 0.1850, "Mixed Nuts": 0.1450,
    "Macadamia": 0.1500, "Pistachios": 0.1250, "Peanuts": 0.0560,
    "Walnuts": 0.0500, "Snacks": 0.0290,
}

# Gross margin by category, used to derive unit cost from list price.
MARGIN_BY_CATEGORY = {
    "Cashews": 0.42, "Almonds": 0.40, "Macadamia": 0.46, "Mixed Nuts": 0.44,
    "Pistachios": 0.43, "Peanuts": 0.35, "Walnuts": 0.39, "Snacks": 0.48,
}

# List prices hold flat through the fitted years, then track input-cost
# inflation. Applied to transaction list price, not to the master price list.
PRICE_INDEX = {
    2019: 1.000, 2020: 1.000, 2021: 1.000, 2022: 1.000, 2023: 1.000,
    2024: 1.000, 2025: 1.000, 2026: 1.021, 2027: 1.043, 2028: 1.064,
    2029: 1.085, 2030: 1.107,
}

# Singapore GST. Rates changed on 1 Jan 2023 and again on 1 Jan 2024.
GST_SCHEDULE = [("2019-01-01", 0.07), ("2023-01-01", 0.08), ("2024-01-01", 0.09)]

FREE_SHIPPING_THRESHOLD = 50.0
SHIPPING_FEE = {
    "Website": 3.90, "GrabMart": 3.00, "FairPrice_Online": 3.00,
    "RedMart": 2.50, "Shopee": 2.50, "Lazada": 2.50,
}
# Marketplaces absorb delivery through platform vouchers this often.
VOUCHER_FREE_SHIPPING = {"Shopee": 0.62, "Lazada": 0.58}

PAYMENT_METHODS = ["PayNow", "Credit Card", "Atome", "GrabPay", "ShopeePay"]

# --- customers ------------------------------------------------------------
CUSTOMERS_PER_YEAR = 750          # new registrations, 2019 baseline
CUSTOMER_GROWTH = 1.06            # registration growth from 2026
GUEST_ACCOUNTS = 100
AGE_RANGE = (18, 70)
OPT_IN_RATE = 0.713

# A realistic loyalty pyramid. The original dataset had this inverted, with
# half the base sitting in Platinum.
TIER_MIX = {"Bronze": 0.46, "Silver": 0.31, "Gold": 0.18, "Platinum": 0.05}
TIER_POINTS = {
    "Bronze": (0, 499), "Silver": (500, 1999),
    "Gold": (2000, 5999), "Platinum": (6000, 12000),
}

FIRST_NAMES = [
    "Aarav", "Priya", "Wei Hao", "Ryan", "Aria", "Noah", "Nurul", "Felicia",
    "Aiden", "Darren", "Caleb", "Wei Ling", "Jun Jie", "Lucas", "Isabella",
    "Emily", "Hui Min", "Amir", "Alex", "Chloe", "Siti", "Rajesh", "Mei Ling",
    "Daniel", "Sophia", "Haziq", "Xin Yi", "Marcus", "Natalie", "Farah",
    "Jia Hui", "Ethan", "Rachel", "Zul", "Kai Wen", "Olivia", "Deepak", "Serene",
]
LAST_NAMES = [
    "Lau", "Wong", "Ng", "Sharma", "Rahman", "Tan", "Teo", "Koh", "Lee", "Yap",
    "Liew", "Chew", "Chan", "Quek", "Foo", "Ong", "Yeo", "Chua", "Phua", "Lim",
    "Goh", "Sim", "Toh", "Heng", "Loh", "Ibrahim", "Kaur", "Nair", "Pillai", "Seah",
]
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com"]

# --- marketing ------------------------------------------------------------
# Themes anchored to the Singapore retail calendar.
CAMPAIGN_THEMES = [
    # name, month, typical duration in days, demand lift on overlapping days
    ("CNY", 1, 14, 1.45),
    ("Ramadan", 4, 12, 1.22),
    ("National Day", 8, 8, 1.18),
    ("Back-to-School", 7, 10, 1.15),
    ("Mid-Autumn", 9, 10, 1.20),
    ("11.11 Mega", 11, 7, 1.70),
    ("Xmas", 12, 14, 1.40),
]
EVENT_TYPES = [
    "Supermarket Sampling", "Lazada Mega Day", "Shopee Flash Sale",
    "Meta Retargeting", "TikTok Spark Ads", "Website Promo",
    "Corporate Gifting Push",
]
EVENTS_PER_CAMPAIGN = {2: 0.42, 3: 0.33, 4: 0.25}
EVENT_SPEND_RANGE = (850.0, 8900.0)
EVENT_SPEND_GROWTH = 1.05          # annual marketing budget growth

# Return on ad spend. The original dataset averaged 0.16x, which made every
# campaign look like a loss. These are plausible FMCG figures.
ROAS_RANGE = {
    "Shopee Flash Sale": (3.2, 6.5), "Lazada Mega Day": (3.0, 6.0),
    "Website Promo": (2.4, 5.0), "Meta Retargeting": (2.0, 4.5),
    "TikTok Spark Ads": (1.4, 3.6), "Supermarket Sampling": (1.1, 2.8),
    "Corporate Gifting Push": (2.6, 5.5),
}

# Promotional depth while a campaign runs.
PROMO_DEPTH = {
    "CNY": (0.10, 0.25), "11.11 Mega": (0.15, 0.35), "Xmas": (0.10, 0.22),
    "Ramadan": (0.08, 0.20), "Mid-Autumn": (0.08, 0.18),
    "National Day": (0.08, 0.18), "Back-to-School": (0.05, 0.15),
}
PROMO_SKU_SHARE = 0.35             # share of the range on deal during a campaign

TRAFFIC_PLATFORMS = ["Shopee Ads", "Lazada Ads", "Meta", "Google", "TikTok", "Email"]
TRAFFIC_ROWS_PER_EVENT = {2: 0.27, 3: 0.40, 4: 0.33}
IMPRESSION_RANGE = (65_000, 2_000_000)
CTR_RANGE = (1.10, 1.85)
SESSIONS_PER_CLICK = (1.20, 1.80)
CONVERSION_RATE = (0.020, 0.060)

# --- inventory ------------------------------------------------------------
# Stock cover in days of recent demand, converted to units at write time.
STOCK_COVER_DAYS = (25, 120)
