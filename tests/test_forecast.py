"""Unit tests for the forecasting models and the backtest harness."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics import forecast as F


def synthetic_series(months: int = 36, seed: int = 3):
    rng = np.random.default_rng(seed)
    t = np.arange(months)
    month = (np.arange(months) + 3) % 12 + 1
    seasonal = np.array([0.86, 0.89, 0.95, 0.97, 1.0, 0.98, 1.02, 0.99, 1.0, 1.06, 1.27, 1.36])
    y = 100_000 * (1.02 ** t) * seasonal[month - 1] * rng.lognormal(0, 0.04, months)
    return y, month


def test_all_methods_produce_finite_positive_forecasts():
    y, month = synthetic_series()
    future = np.array([1, 2, 3])
    for name, fn in F.METHODS.items():
        pred = fn(y, month, future)
        assert len(pred) == 3, name
        assert np.all(np.isfinite(pred)), f"{name} produced non-finite output"
        assert np.all(pred >= 0), f"{name} produced negative revenue"
        assert np.all(pred < 10 * y.max()), f"{name} extrapolated implausibly"


def test_backtest_scores_every_method():
    y, month = synthetic_series()
    scores, errors, actuals = F.backtest(y, month)
    assert set(scores["method"]) == set(F.METHODS)
    assert len(errors) == len(F.METHODS)
    assert scores["mape"].notna().all()
    # The good models should beat seasonal-naive on a clean seasonal series.
    best = scores.sort_values("mape").iloc[0]
    naive = scores[scores["method"] == "seasonal_naive"].iloc[0]
    assert best["mape"] < naive["mape"]
    assert best["mape"] < 15


def test_adjusted_trend_follows_recent_level():
    """A level shift in the most recent months must pull the forecast with it."""
    y, month = synthetic_series()
    base = F.METHODS["trend_seasonal_adjusted"](y, month, np.array([10, 11, 12]))
    shifted = y.copy()
    shifted[-3:] *= 1.5
    after = F.METHODS["trend_seasonal_adjusted"](shifted, month, np.array([10, 11, 12]))
    assert np.all(after > base), "forecast should react to a sustained level shift"


def test_holt_winters_is_bounded():
    y, month = synthetic_series(months=26, seed=11)
    pred = F.METHODS["holt_winters"](y, month, np.array([1, 2, 3]))
    assert np.all(pred <= 3.5 * np.mean(y[-3:])), "Holt-Winters must be clipped to a sane ceiling"


def test_backtest_intervals_widen_with_horizon(warehouse):
    fc = warehouse.con.execute(
        "SELECT lower_bound, upper_bound FROM marts.revenue_forecast ORDER BY month_start"
    ).fetchall()
    widths = [hi - lo for lo, hi in fc]
    assert widths[0] <= widths[-1] + 1e-6


def test_insufficient_history_is_rejected(warehouse, tmp_path):
    import duckdb

    con = duckdb.connect(str(tmp_path / "empty.duckdb"))
    con.execute("CREATE SCHEMA marts")
    con.execute("CREATE SCHEMA core")
    con.execute("CREATE TABLE marts.kpi_monthly (month_start DATE, net_revenue DOUBLE)")
    con.execute("CREATE TABLE core.fct_orders (order_date DATE)")
    con.execute("INSERT INTO core.fct_orders VALUES (DATE '2026-01-01')")
    for i in range(3):
        con.execute("INSERT INTO marts.kpi_monthly VALUES (?, 1000)", [pd.Timestamp("2024-01-01") + pd.offsets.MonthBegin(i)])
    with pytest.raises(ValueError):
        F.forecast(con)
    con.close()
