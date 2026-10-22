"""Generate a realistic (and deliberately messy) e-commerce dataset.

The output mimics a raw export from an operational system: six channels, a
three-year order history, 60k customers with realistic repeat-purchase decay,
product-level line items, web sessions that form a funnel, and daily marketing
spend. Dirty rows are injected on purpose so the pipeline's cleaning and data
quality layers have something real to catch.

Usage:
    python -m analytics.generate_data            # full dataset
    python -m analytics.generate_data --customers 5000
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    AGE_GROUPS,
    CHANNEL_ALIASES,
    CHANNEL_CAC,
    CHANNEL_CVR,
    CHANNEL_SHARE,
    CHANNEL_TENURE,
    CHANNELS,
    COUNTRIES,
    DOW_INDEX,
    MONTH_INDEX,
    PROMO_DAYS,
    GenConfig,
)

# Non-purchase sessions stop at one of these steps.
FUNNEL_STEPS = ["landing", "product_view", "add_to_cart", "checkout", "purchase"]
STEP_SHARES = {
    "organic_search": [0.46, 0.28, 0.17, 0.09],
    "direct": [0.38, 0.29, 0.20, 0.13],
    "paid_search": [0.52, 0.27, 0.14, 0.07],
    "social": [0.66, 0.22, 0.09, 0.03],
    "email": [0.40, 0.26, 0.21, 0.13],
    "affiliate": [0.50, 0.28, 0.15, 0.07],
}
DEVICE_BY_CHANNEL = {
    "organic_search": [0.55, 0.36, 0.09],
    "direct": [0.48, 0.44, 0.08],
    "paid_search": [0.60, 0.31, 0.09],
    "social": [0.83, 0.12, 0.05],
    "email": [0.66, 0.26, 0.08],
    "affiliate": [0.61, 0.30, 0.09],
}
CPM = {
    "organic_search": 3.2, "direct": 1.5, "paid_search": 18.5,
    "social": 9.4, "email": 2.2, "affiliate": 12.0,
}
CTR = {
    "organic_search": 0.081, "direct": 0.124, "paid_search": 0.046,
    "social": 0.017, "email": 0.058, "affiliate": 0.031,
}
HOUR_WEIGHTS = np.array(
    [0.3, 0.15, 0.1, 0.08, 0.1, 0.25, 0.55, 0.9, 1.2, 1.45, 1.5, 1.35,
     1.2, 1.3, 1.25, 1.2, 1.25, 1.35, 1.5, 1.65, 1.6, 1.4, 1.0, 0.6]
)

PRODUCT_NAMES = {
    "Outdoor & Camping": ("Trailhead", "Summit", "Basecamp", "Cedar", "Ridgeline"),
    "Home & Kitchen": ("Copper", "Linen", "Hearth", "Willow", "Maple"),
    "Apparel": ("Everyday", "Motion", "Drift", "Layer", "Cotton"),
    "Electronics": ("Pulse", "Nova", "Volt", "Sync", "Signal"),
    "Beauty": ("Glow", "Botanic", "Serum", "Dew", "Bloom"),
    "Sports & Fitness": ("Momentum", "Core", "Stride", "Iron", "Kinetic"),
}
PRODUCT_TYPES = {
    "Outdoor & Camping": ["Tent", "Sleeping Bag", "Headlamp", "Backpack", "Camp Stove", "Hammock"],
    "Home & Kitchen": ["Kettle", "Skillet", "Storage Set", "Throw Blanket", "Diffuser", "Mug Set"],
    "Apparel": ["Hoodie", "Rain Jacket", "Merino Tee", "Socks 3-Pack", "Joggers", "Cap"],
    "Electronics": ["Wireless Earbuds", "Power Bank", "Desk Lamp", "Speaker", "Webcam", "Charger"],
    "Beauty": ["Face Serum", "Cleanser", "Lip Balm", "Body Oil", "Sunscreen", "Mask"],
    "Sports & Fitness": ["Yoga Mat", "Resistance Bands", "Water Bottle", "Foam Roller", "Dumbbells", "Jump Rope"],
}
CATEGORY_CVR_WEIGHT = {
    "Outdoor & Camping": 1.0, "Home & Kitchen": 1.15, "Apparel": 0.85,
    "Electronics": 0.9, "Beauty": 1.1, "Sports & Fitness": 1.0,
}


def _daily_weights(days: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Daily demand index: compounding growth x month x weekday x promotions."""
    t = np.arange(len(days))
    growth = 1.012 ** (t / 30.44)
    month = np.array([MONTH_INDEX[m] for m in days.month])
    dow = np.array([DOW_INDEX[d] for d in days.day_of_week])
    promo = np.ones(len(days))
    # Black Friday / Cyber week bump every November, plus random flash promos.
    bf = (days.month == 11) & (days.day >= 24) & (days.day <= 30)
    cyber = (days.month == 12) & (days.day <= 2)
    promo[bf] = 1.85
    promo[cyber] = 1.45
    flash = rng.random(len(days)) < PROMO_DAYS
    promo[flash] *= 1.22
    return growth * month * dow * promo


def _pick(rng: np.random.Generator, options: dict, size: int) -> np.ndarray:
    keys = list(options)
    probs = np.array([options[k] for k in keys], dtype=float)
    return rng.choice(np.array(keys, dtype=object), size=size, p=probs / probs.sum())


def _customers(cfg: GenConfig, rng, days, weight) -> pd.DataFrame:
    n = cfg.n_customers
    counts = rng.multinomial(n, weight / weight.sum())
    signup_idx = np.repeat(np.arange(len(days)), counts)

    channel = _pick(rng, CHANNEL_SHARE, n)
    age = _pick(rng, AGE_GROUPS, n)
    country = _pick(rng, COUNTRIES, n)
    email_domain = rng.choice(
        ["gmail.com", "outlook.com", "yahoo.com", "proton.me", "icloud.com"],
        size=n, p=[0.44, 0.19, 0.15, 0.09, 0.13],
    )
    first = rng.choice(
        ["alex", "sam", "jordan", "taylor", "casey", "morgan", "riley", "jamie",
         "avery", "quinn", "noah", "mia", "leo", "zoe", "arjun", "priya"],
        size=n,
    )
    last = rng.choice(
        ["smith", "patel", "garcia", "kim", "murphy", "chen", "silva", "brown",
         "okafor", "novak", "rossi", "haddad"],
        size=n,
    )
    suffix = rng.integers(1, 9999, size=n)
    email = np.array([f"{f}.{l}{s}@{d}" for f, l, s, d in zip(first, last, suffix, email_domain)], dtype=object)

    # --- injected dirt -----------------------------------------------------
    test_mask = rng.random(n) < 0.004
    email[test_mask] = [f"qa_test_{i}@example.com" for i in np.flatnonzero(test_mask)]
    noisy_case = rng.random(n) < 0.03
    email[noisy_case] = np.array([e.upper() for e in email[noisy_case]], dtype=object)
    padded = rng.random(n) < 0.02
    email[padded] = np.array([f"  {e} " for e in email[padded]], dtype=object)

    country_raw = country.astype(object).copy()
    lower = rng.random(n) < 0.02
    country_raw[lower] = np.array([c.lower() for c in country_raw[lower]], dtype=object)

    opt_in = rng.random(n) < 0.71
    opt_in = pd.array(opt_in, dtype="boolean")
    opt_in[rng.random(n) < 0.03] = pd.NA

    return pd.DataFrame(
        {
            "customer_id": np.array([f"C{i:06d}" for i in range(1, n + 1)]),
            "email": email,
            "signup_date": days.to_numpy()[signup_idx],
            "acquisition_channel": channel,
            "country": country_raw,
            "age_group": age,
            "first_device": _pick(rng, {"mobile": 0.6, "desktop": 0.31, "tablet": 0.09}, n),
            "marketing_opt_in": opt_in,
        }
    ), signup_idx, channel


def _products(cfg: GenConfig, rng, days) -> pd.DataFrame:
    n_products = max(140, int(1200 * cfg.scale))
    cat = _pick(rng, {c: 1.0 for c in PRODUCT_TYPES}, n_products)
    types = np.empty(n_products, dtype=object)
    names = np.empty(n_products, dtype=object)
    for c in PRODUCT_TYPES:
        m = cat == c
        k = int(m.sum())
        if k == 0:
            continue
        types[m] = rng.choice(PRODUCT_TYPES[c], size=k)
        names[m] = [f"{b} {t}" for b, t in zip(rng.choice(PRODUCT_NAMES[c], size=k), types[m])]
    # Disambiguate duplicate names with a model number.
    names = np.array([f"{nm} {i % 97 + 1:02d}" for i, nm in enumerate(names)], dtype=object)

    base_price = {
        "Outdoor & Camping": 78, "Home & Kitchen": 46, "Apparel": 58,
        "Electronics": 96, "Beauty": 27, "Sports & Fitness": 41,
    }
    list_price = np.array([base_price[c] for c in cat]) * rng.lognormal(0, 0.42, n_products)
    list_price = np.round(np.clip(list_price, 8, 420), 2)
    margin = np.clip(rng.normal(0.44, 0.10, n_products), 0.12, 0.72)
    unit_cost = np.round(list_price * (1 - margin), 2)

    # 15% of the catalogue launches later in the window.
    launch_frac = rng.random(n_products)
    launch_idx = np.where(launch_frac < 0.85, 0, rng.integers(0, len(days), n_products))
    disc_idx = np.where(rng.random(n_products) < 0.08, rng.integers(0, len(days), n_products), 10**6)

    category = cat.astype(object)
    category[rng.random(n_products) < 0.02] = None

    return pd.DataFrame(
        {
            "product_id": [f"P{i:04d}" for i in range(1, n_products + 1)],
            "product_name": names,
            "category": category,
            "unit_cost": np.round(unit_cost, 2),
            "list_price": list_price,
            "launch_date": days.to_numpy()[launch_idx],
            "discontinued_date": [
                None if d >= len(days) else days[d] for d in disc_idx
            ],
        }
    ), launch_idx


def _orders_and_items(cfg, rng, days, weight, cust, signup_idx, cust_channel, products, launch_idx):
    n_cust = len(cust)
    end_idx = len(days) - 1
    ch_idx = np.array([CHANNELS.index(c) for c in cust_channel])
    tenure_scale = np.array([CHANNEL_TENURE[c] for c in CHANNELS])[ch_idx]
    tenure = np.minimum(rng.exponential(scale=tenure_scale).round(), end_idx - signup_idx)
    tenure = np.maximum(tenure, 0).astype(int)

    heterogeneity = rng.gamma(shape=1.4, scale=1 / 1.4, size=n_cust)
    mu = 3.4 * (tenure / 365.0) * heterogeneity
    n_orders = 1 + rng.poisson(mu)
    n_orders = np.minimum(n_orders, 40)

    cust_pos = np.repeat(np.arange(n_cust), n_orders)
    start_idx = signup_idx[cust_pos]
    last_idx = np.minimum(start_idx + tenure[cust_pos], end_idx)

    cum = np.cumsum(weight)
    lo = np.where(start_idx > 0, cum[np.maximum(start_idx - 1, 0)], 0.0)
    hi = cum[last_idx]
    target = lo + rng.random(len(cust_pos)) * np.maximum(hi - lo, 1e-9)
    day_idx = np.clip(np.searchsorted(cum, target, side="right"), start_idx, last_idx)

    hours = rng.choice(24, size=len(day_idx), p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
    ts = (
        days.to_numpy()[day_idx].astype("datetime64[D]").astype("datetime64[s]")
        + hours.astype("timedelta64[h]")
        + rng.integers(0, 3600, len(day_idx)).astype("timedelta64[s]")
    )
    ts = pd.to_datetime(ts)

    order_channel = cust_channel[cust_pos].copy()
    switch = rng.random(len(cust_pos)) < 0.22
    order_channel[switch] = _pick(rng, CHANNEL_SHARE, int(switch.sum()))
    country = cust["country"].to_numpy()[cust_pos]

    payment = _pick(
        rng,
        {"card": 0.61, "paypal": 0.21, "apple_pay": 0.10, "bank_transfer": 0.05, "gift_card": 0.03},
        len(cust_pos),
    )
    device = np.empty(len(cust_pos), dtype=object)
    for c in CHANNELS:
        m = order_channel == c
        if m.any():
            device[m] = rng.choice(["mobile", "desktop", "tablet"], size=int(m.sum()),
                                   p=np.array(DEVICE_BY_CHANNEL[c]) / sum(DEVICE_BY_CHANNEL[c]))

    n_orders_total = len(cust_pos)
    order_id = np.array([f"ORD-{i:07d}" for i in range(1, n_orders_total + 1)])
    session_id = np.array([f"S-{i:09d}" for i in range(1, n_orders_total + 1)])

    # --- line items --------------------------------------------------------
    n_items = np.minimum(1 + rng.poisson(0.82, n_orders_total), 6).astype(int)
    # Bigger baskets during promo windows.
    promo_boost = np.where(weight[day_idx] / np.median(weight) > 1.3, 1, 0)
    n_items = np.minimum(n_items + rng.binomial(promo_boost, 0.25), 8)

    item_order = np.repeat(np.arange(n_orders_total), n_items)
    item_day = day_idx[item_order]

    rank = rng.permutation(len(products))
    pop_w = 1.0 / (np.arange(len(products)) + 3) ** 1.08
    prod_weight = np.empty(len(products))
    prod_weight[rank] = pop_w
    prod_weight /= prod_weight.sum()
    prod_idx = rng.choice(len(products), size=len(item_order), p=prod_weight)

    # Products that have not launched yet get replaced by an in-stock product.
    launched_at_start = launch_idx == 0
    base_w = np.where(launched_at_start, prod_weight, 0.0)
    base_w /= base_w.sum()
    batches = np.unique(launch_idx[launch_idx > 0])
    for b in batches:
        chosen_in_batch = (launch_idx == b)[prod_idx]
        too_early = chosen_in_batch & (item_day < b)
        if too_early.any():
            prod_idx[too_early] = rng.choice(len(products), size=int(too_early.sum()), p=base_w)

    qty = rng.choice([1, 2, 3, 4], size=len(item_order), p=[0.87, 0.09, 0.03, 0.01])
    disc_pct = rng.choice([0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
                          size=len(item_order),
                          p=[0.52, 0.14, 0.14, 0.10, 0.06, 0.03, 0.01])
    price = products["list_price"].to_numpy()[prod_idx]
    cost = products["unit_cost"].to_numpy()[prod_idx]
    gross = np.round(qty * price, 2)
    discount = np.round(gross * disc_pct, 2)
    net = np.round(gross - discount, 2)

    items = pd.DataFrame(
        {
            "order_id": order_id[item_order],
            "product_id": products["product_id"].to_numpy()[prod_idx],
            "quantity": qty,
            "unit_price": price,
            "discount_amount": discount,
            "gross_amount": gross,
            "net_amount": net,
            "unit_cost": cost,
        }
    )

    items["cost_amount"] = items["unit_cost"] * items["quantity"]
    agg = items.groupby("order_id", sort=False).agg(
        items_count=("product_id", "size"),
        units=("quantity", "sum"),
        gross_amount=("gross_amount", "sum"),
        discount_amount=("discount_amount", "sum"),
        net_amount=("net_amount", "sum"),
        cost_amount=("cost_amount", "sum"),
    )

    orders = pd.DataFrame(
        {
            "order_id": order_id,
            "customer_id": cust["customer_id"].to_numpy()[cust_pos],
            "order_ts": ts,
            "channel": order_channel,
            "device": device,
            "payment_method": payment,
            "country": country,
            "session_id": session_id,
        }
    ).merge(agg.reset_index(), on="order_id", how="left")

    # Status, shipping and money fields.
    cancelled = rng.random(n_orders_total) < 0.024
    returned = (~cancelled) & (rng.random(n_orders_total) < 0.071)
    status = np.where(cancelled, "cancelled", np.where(returned, "returned", "completed"))
    shipping = np.where(
        orders["net_amount"] > 75, 0.0, np.where(orders["country"].isin(["US", "CA"]), 6.99, 9.99)
    )
    orders["status"] = status
    orders["shipping_amount"] = np.round(shipping, 2)
    orders["gross_margin"] = np.round(
        orders["net_amount"] - orders["cost_amount"] + orders["shipping_amount"], 2
    )
    orders["refund_amount"] = np.where(returned, orders["net_amount"] + orders["shipping_amount"], 0.0)
    orders["order_date"] = pd.to_datetime(orders["order_ts"]).dt.normalize()

    # --- injected dirt -----------------------------------------------------
    n = n_orders_total
    null_cust = rng.random(n) < 0.0015
    orders.loc[null_cust, "customer_id"] = None
    orphan = rng.random(n) < 0.002
    orders.loc[orphan, "customer_id"] = [f"C99{i:04d}" for i in np.flatnonzero(orphan)]

    messy = rng.random(n) < 0.12
    aliases = list(CHANNEL_ALIASES)
    messy_values = rng.choice(aliases, size=int(messy.sum()))
    orders.loc[messy, "channel"] = messy_values

    orders.loc[rng.random(n) < 0.001, "discount_amount"] *= -1
    orders.loc[rng.random(n) < 0.02, "payment_method"] = None
    orders.loc[rng.random(n) < 0.0005, "order_ts"] = pd.NaT

    future = rng.random(n) < 0.0004
    future_ts = pd.Timestamp(days[-1]) + pd.Timedelta(days=45)
    orders.loc[future, "order_ts"] = future_ts

    early = rng.random(n) < 0.001
    signup_by_cust = dict(zip(cust["customer_id"], cust["signup_date"]))
    early_customers = orders.loc[early & orders["customer_id"].notna(), "customer_id"]
    orders.loc[early_customers.index, "order_ts"] = (
        early_customers.map(signup_by_cust) - pd.Timedelta(days=10)
    )

    bad_qty = rng.random(len(items)) < 0.0015
    items.loc[bad_qty, "quantity"] = -1 * items.loc[bad_qty, "quantity"]

    # Duplicate a few order rows on the way out (classic double-load).
    dup_idx = rng.choice(n, size=max(1, int(n * 0.004)), replace=False)
    orders = pd.concat([orders, orders.iloc[dup_idx]], ignore_index=True)

    return orders, items


def _sessions(cfg, rng, days, weight, orders, out_dir: Path) -> int:
    """Write monthly parquet parts: purchase sessions came from orders, the
    rest are non-converting visits that stop somewhere in the funnel."""
    sess_dir = out_dir / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    for f in sess_dir.glob("*.parquet"):
        f.unlink()

    orders = orders.dropna(subset=["order_ts"]).drop_duplicates("order_id").copy()
    orders["day"] = pd.to_datetime(orders["order_ts"]).dt.normalize()
    # Quarantined rows (future-dated, pre-signup) must not create sessions either.
    orders = orders[(orders["day"] >= days[0]) & (orders["day"] <= days[-1])]
    orders["channel"] = orders["channel"].map(CHANNEL_ALIASES).fillna("direct")

    day_pos = {d: i for i, d in enumerate(days)}
    total = 0
    nonpurchase_seq = 0
    for month, month_orders in orders.groupby(orders["day"].dt.to_period("M"), sort=True):
        rows: list[pd.DataFrame] = []
        # Purchase sessions (one per order).
        purch = pd.DataFrame(
            {
                "session_id": month_orders["session_id"].to_numpy(),
                "session_ts": (month_orders["order_ts"] - pd.to_timedelta(rng.integers(4, 40, len(month_orders)), unit="m")).to_numpy(),
                "channel": month_orders["channel"].to_numpy(),
                "device": month_orders["device"].to_numpy(),
                "country": month_orders["country"].to_numpy(),
                "step_reached": "purchase",
                "duration_seconds": np.round(rng.lognormal(5.6, 0.55, len(month_orders))).astype(int),
            }
        )
        rows.append(purch)

        # Non-converting sessions, generated per (day, channel).
        grp = month_orders.groupby(["day", "channel"], sort=False).size()
        dates_in_month = pd.date_range(month.start_time, month.end_time.normalize(), freq="D")
        for d in dates_in_month:
            if d not in day_pos:
                continue
            base_weight = weight[day_pos[d]] / np.median(weight)
            for ch in CHANNELS:
                n_orders_ch = int(grp.get((d, ch), 0))
                expected = n_orders_ch / CHANNEL_CVR[ch]
                expected *= rng.lognormal(0, 0.10) * base_weight ** 0.08
                n_sessions = int(max(0, round(expected) - n_orders_ch))
                if n_sessions == 0:
                    continue
                steps = rng.choice(FUNNEL_STEPS[:4], size=n_sessions,
                                   p=np.array(STEP_SHARES[ch]) / sum(STEP_SHARES[ch]))
                hours = rng.choice(24, size=n_sessions, p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
                ts = (
                    np.datetime64(d, "s")
                    + hours.astype("timedelta64[h]")
                    + rng.integers(0, 3600, n_sessions).astype("timedelta64[s]")
                )
                ids = [f"S-NP-{nonpurchase_seq + i:09d}" for i in range(n_sessions)]
                nonpurchase_seq += n_sessions
                rows.append(
                    pd.DataFrame(
                        {
                            "session_id": ids,
                            "session_ts": pd.to_datetime(ts),
                            "channel": ch,
                            "device": rng.choice(["mobile", "desktop", "tablet"], size=n_sessions,
                                                 p=np.array(DEVICE_BY_CHANNEL[ch]) / sum(DEVICE_BY_CHANNEL[ch])),
                            "country": _pick(rng, COUNTRIES, n_sessions),
                            "step_reached": steps,
                            "duration_seconds": np.round(
                                rng.lognormal(4.9, 0.65, n_sessions)
                                * np.where(steps == "landing", 0.5, 1.0)
                            ).astype(int),
                        }
                    )
                )
        part = pd.concat(rows, ignore_index=True)
        total += len(part)
        part.to_parquet(sess_dir / f"sessions-{month}.parquet", index=False, compression="zstd")
    return total


def _marketing_spend(rng, days, cust) -> pd.DataFrame:
    signup = pd.to_datetime(cust["signup_date"]).dt.normalize()
    ch = cust["acquisition_channel"]
    daily = pd.MultiIndex.from_product(
        [pd.DatetimeIndex(days), CHANNELS], names=["date", "channel"]
    ).to_frame(index=False)
    counts = (
        pd.DataFrame({"date": signup, "channel": ch})
        .groupby(["date", "channel"]).size().rename("new_customers").reset_index()
    )
    daily = daily.merge(counts, on=["date", "channel"], how="left")
    daily["new_customers"] = daily["new_customers"].fillna(0).astype(int)
    month_age = ((daily["date"] - pd.Timestamp(days[0])).dt.days / 30.44).to_numpy()
    cac = daily["channel"].map(CHANNEL_CAC).to_numpy() * (1.011 ** month_age)
    noise = rng.lognormal(0, 0.28, len(daily))
    base = daily["channel"].map({"organic_search": 40.0, "direct": 8.0, "email": 55.0}).fillna(0).to_numpy()
    spend = np.round(daily["new_customers"].to_numpy() * cac * noise + base, 2)
    impressions = np.round(spend / daily["channel"].map(CPM).to_numpy() * 1000).astype(int)
    clicks = np.minimum(
        impressions,
        np.round(impressions * daily["channel"].map(CTR).to_numpy() * rng.lognormal(0, 0.06, len(daily))).astype(int),
    )
    return pd.DataFrame(
        {
            "date": daily["date"],
            "channel": daily["channel"],
            "spend_usd": spend,
            "impressions": impressions,
            "clicks": clicks,
            "new_customers_attributed": daily["new_customers"],
        }
    )


def generate(cfg: GenConfig | None = None) -> dict[str, int]:
    cfg = cfg or GenConfig()
    rng = np.random.default_rng(cfg.seed)
    out_dir = Path(cfg.out_dir)
    t0 = time.time()

    days = pd.date_range(cfg.start, cfg.end, freq="D")
    weight = _daily_weights(days, rng)

    cust, signup_idx, cust_channel = _customers(cfg, rng, days, weight)
    products, launch_idx = _products(cfg, rng, days)
    orders, items = _orders_and_items(cfg, rng, days, weight, cust, signup_idx, cust_channel, products, launch_idx)
    spend = _marketing_spend(rng, days, cust)

    for d in [out_dir, out_dir / "sessions"]:
        d.mkdir(parents=True, exist_ok=True)
    cust.to_parquet(out_dir / "customers.parquet", index=False, compression="zstd")
    products.to_parquet(out_dir / "products.parquet", index=False, compression="zstd")
    orders.to_parquet(out_dir / "orders.parquet", index=False, compression="zstd")
    items.to_parquet(out_dir / "order_items.parquet", index=False, compression="zstd")
    spend.to_parquet(out_dir / "marketing_spend.parquet", index=False, compression="zstd")
    n_sessions = _sessions(cfg, rng, days, weight, orders, out_dir)

    counts = {
        "customers": len(cust),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
        "sessions": n_sessions,
        "marketing_spend": len(spend),
    }
    print(f"generated {counts} in {time.time() - t0:.1f}s -> {out_dir}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic e-commerce data")
    ap.add_argument("--customers", type=int, default=60_000)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else None
    kwargs = dict(n_customers=args.customers, start=args.start, end=args.end, seed=args.seed)
    if out:
        kwargs["out_dir"] = out
    generate(GenConfig(**kwargs))


if __name__ == "__main__":
    main()
