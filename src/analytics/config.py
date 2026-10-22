"""Central configuration for the Northstar analytics project."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "warehouse.duckdb"
SQL_DIR = PROJECT_ROOT / "sql"

CHANNELS = [
    "organic_search",
    "direct",
    "paid_search",
    "social",
    "email",
    "affiliate",
]

# Messy spellings found in the raw order export. Staging normalises these.
CHANNEL_ALIASES = {
    "organic_search": "organic_search",
    "organic": "organic_search",
    "seo": "organic_search",
    " Organic_Search ": "organic_search",
    "direct": "direct",
    "Direct ": "direct",
    "(direct)": "direct",
    "paid_search": "paid_search",
    "Paid Search": "paid_search",
    "paid search": "paid_search",
    "Paid_Search ": "paid_search",
    "ppc": "paid_search",
    "social": "social",
    "Social": "social",
    "fb": "social",
    "facebook": "social",
    "IG": "social",
    "email": "email",
    "Email": "email",
    "newsletter": "email",
    "affiliate": "affiliate",
    "Affiliate ": "affiliate",
    "aff": "affiliate",
}

CATEGORIES = [
    "Outdoor & Camping",
    "Home & Kitchen",
    "Apparel",
    "Electronics",
    "Beauty",
    "Sports & Fitness",
]

COUNTRIES = {"US": 0.50, "GB": 0.16, "DE": 0.13, "CA": 0.09, "AU": 0.07, "FR": 0.05}

DEVICES = {"mobile": 0.58, "desktop": 0.33, "tablet": 0.09}

AGE_GROUPS = {"18-24": 0.16, "25-34": 0.34, "35-44": 0.24, "45-54": 0.15, "55+": 0.11}

CHANNEL_SHARE = {
    "organic_search": 0.27,
    "direct": 0.13,
    "paid_search": 0.23,
    "social": 0.16,
    "email": 0.11,
    "affiliate": 0.10,
}

# Acquisition cost per new customer (USD) at the start of the period.
CHANNEL_CAC = {
    "organic_search": 22.0,
    "direct": 9.0,
    "paid_search": 118.0,
    "social": 96.0,
    "email": 28.0,
    "affiliate": 72.0,
}

# Monthly survival scale (days) — how long customers stay order-active.
CHANNEL_TENURE = {
    "organic_search": 340,
    "direct": 470,
    "paid_search": 215,
    "social": 135,
    "email": 430,
    "affiliate": 265,
}

# Session -> purchase conversion rate by channel.
CHANNEL_CVR = {
    "organic_search": 0.062,
    "direct": 0.091,
    "paid_search": 0.049,
    "social": 0.030,
    "email": 0.088,
    "affiliate": 0.058,
}

MONTH_INDEX = {
    1: 0.86, 2: 0.89, 3: 0.95, 4: 0.97, 5: 1.00, 6: 0.98,
    7: 1.02, 8: 0.99, 9: 1.00, 10: 1.06, 11: 1.27, 12: 1.36,
}
DOW_INDEX = {0: 0.94, 1: 0.98, 2: 1.00, 3: 1.04, 4: 1.12, 5: 1.07, 6: 1.01}

PROMO_DAYS = 0.02  # share of days that get a mid-size promo bump


@dataclass(frozen=True)
class GenConfig:
    """Knobs for the synthetic data generator."""

    n_customers: int = 60_000
    start: str = "2024-01-01"
    end: str = "2026-09-30"
    seed: int = 42
    out_dir: Path = field(default_factory=lambda: RAW_DIR)

    @property
    def scale(self) -> float:
        """Row counts scale linearly with customer volume (used by tests)."""
        return self.n_customers / 60_000
