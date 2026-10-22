"""Revenue forecasting with an honest rolling-origin backtest.

Six candidate models are scored by forecasting the last six months from
successive origins (1–3 months ahead, the same horizon the dashboard shows).
The winner — by MAPE — is refit on the full history and used to project the
next three months, with 95% intervals built from the backtest errors at each
horizon. Every model's score is persisted so the dashboard can show why the
winner won instead of presenting a forecast on faith.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON = 3
BACKTEST_ORIGINS = 6
MIN_TRAIN = 18
MIN_HISTORY = 20


def _seasonal_dummies(months: np.ndarray) -> np.ndarray:
    """January is the baseline; 11 dummies for Feb..Dec."""
    return (months[:, None] == np.arange(2, 13)[None, :]).astype(float)


def _trend_seasonal_fit(y: np.ndarray, months: np.ndarray):
    n = len(y)
    t = np.arange(n, dtype=float)
    X = np.column_stack([np.ones(n), t, _seasonal_dummies(months)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coef, n


def _trend_seasonal(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    coef, n = _trend_seasonal_fit(y, months)
    tf = np.arange(n, n + len(future), dtype=float)
    XF = np.column_stack([np.ones(len(future)), tf, _seasonal_dummies(future)])
    return np.clip(XF @ coef, 0, None)


def _trend_seasonal_adjusted(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    """Trend + month seasonality, rescaled by the model's latest residual.

    Growth in this business is fast and compounding, so a global linear trend
    systematically lags the current run-rate. Correcting by the residual of the
    most recent month anchors the forecast to the present without fitting noise
    (the correction is clipped to ±40%).
    """
    coef, n = _trend_seasonal_fit(y, months)
    last_row = np.concatenate([[1.0, float(n - 1)], _seasonal_dummies(months[-1:])[0]])
    fitted_last = float(last_row @ coef)
    ratio = float(np.clip(y[-1] / fitted_last, 0.6, 1.4)) if fitted_last > 0 else 1.0
    tf = np.arange(n, n + len(future), dtype=float)
    XF = np.column_stack([np.ones(len(future)), tf, _seasonal_dummies(future)])
    return np.clip(XF @ coef * ratio, 0, None)


def _trend_only(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    n = len(y)
    t = np.arange(n, dtype=float)
    coef, *_ = np.linalg.lstsq(np.column_stack([np.ones(n), t]), y, rcond=None)
    tf = np.arange(n, n + len(future), dtype=float)
    return np.clip(coef[0] + coef[1] * tf, 0, None)


def _seasonal_naive(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    out = []
    for m in future:
        candidates = np.flatnonzero(months == m)
        out.append(y[candidates[-1]] if len(candidates) else y[-1])
    return np.array(out, dtype=float)


def _moving_average_3(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    return np.repeat(max(float(np.mean(y[-3:])), 0.0), len(future))


def _hw_run(y: np.ndarray, months: np.ndarray, alpha: float, beta: float, gamma: float):
    """Holt-Winters with multiplicative seasonality (monthly data)."""
    n = len(y)
    m = months - 1
    level = float(y[:12].mean())
    trend = float((y[12:24].mean() - y[:12].mean()) / 12) if n >= 24 else 0.0
    seas = np.ones(12)
    for mo in range(12):
        vals = y[:12][m[:12] == mo]
        seas[mo] = float((vals / level).mean()) if len(vals) else 1.0
    seas = seas / seas.mean()

    fitted = np.zeros(n)
    for i in range(n):
        s = float(np.clip(seas[m[i]], 0.25, 3.0))
        fitted[i] = (level + trend) * s
        new_level = alpha * (y[i] / s) + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        seas[m[i]] = float(np.clip(gamma * (y[i] / max(new_level, 1.0)) + (1 - gamma) * s, 0.25, 3.0))
        level, trend = max(new_level, 1.0), new_trend
    return level, trend, seas, fitted


def _holt_winters(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    """Holt-Winters with smoothing parameters chosen by one-step SSE."""
    if len(y) < 24:
        return _decomposition(y, months, future)
    ys = np.maximum(y, 1.0)
    best_sse, best_params = np.inf, (0.3, 0.05, 0.2)
    for alpha in (0.15, 0.3, 0.5, 0.7):
        for beta in (0.01, 0.05, 0.15, 0.3):
            for gamma in (0.05, 0.15, 0.3, 0.5):
                _, _, _, fitted = _hw_run(ys, months, alpha, beta, gamma)
                sse = float(np.mean((ys[12:] - fitted[12:]) ** 2))
                if sse < best_sse:
                    best_sse, best_params = sse, (alpha, beta, gamma)

    level, trend, seas, _ = _hw_run(ys, months, *best_params)
    out = [(level + (i + 1) * trend) * float(seas[int(future[i]) - 1]) for i in range(len(future))]
    out = np.clip(out, 0, None)
    # Guard against pathological extrapolation on short/noisy series.
    ceiling = 3.0 * float(np.mean(ys[-3:]))
    return np.minimum(out, ceiling)


def _decomposition(y: np.ndarray, months: np.ndarray, future: np.ndarray) -> np.ndarray:
    """Log-linear trend on the last two years x month-of-year index."""
    n = len(y)
    t = np.arange(n, dtype=float)
    ly = np.log(np.maximum(y, 1.0))
    window = min(n, 24)
    coef = np.polyfit(t[-window:], ly[-window:], 1)
    trend = np.exp(np.polyval(coef, t))
    ratio = y / np.maximum(trend, 1e-9)

    seasonal = {}
    for m in range(1, 13):
        vals = ratio[months == m]
        seasonal[m] = float(np.median(vals)) if len(vals) else 1.0
    norm = float(np.mean(list(seasonal.values()))) or 1.0
    seasonal = {m: v / norm for m, v in seasonal.items()}

    out = [np.exp(np.polyval(coef, n + i)) * seasonal.get(int(future[i]), 1.0)
           for i in range(len(future))]
    return np.clip(np.array(out, dtype=float), 0, None)


METHODS = {
    "trend_seasonal_adjusted": _trend_seasonal_adjusted,
    "trend_seasonal": _trend_seasonal,
    "trend_only": _trend_only,
    "decomposition": _decomposition,
    "holt_winters": _holt_winters,
    "seasonal_naive": _seasonal_naive,
    "moving_average_3": _moving_average_3,
}


def backtest(
    y: np.ndarray,
    months: np.ndarray,
    origins: int = BACKTEST_ORIGINS,
    max_h: int = HORIZON,
    min_train: int = MIN_TRAIN,
) -> tuple[pd.DataFrame, dict[str, list[float]], dict[str, list[float]]]:
    """Rolling-origin backtest at the dashboard's forecast horizon.

    Returns (scores, errors_by_horizon, absolute_actuals_by_horizon).
    """
    n = len(y)
    scores, errors, actuals = [], {}, {}
    for name, fn in METHODS.items():
        err_h: dict[int, list[float]] = {h: [] for h in range(1, max_h + 1)}
        act_h: dict[int, list[float]] = {h: [] for h in range(1, max_h + 1)}
        for origin in range(max(0, n - origins), n):
            if origin < min_train:
                continue
            steps = min(max_h, n - origin)
            pred = fn(y[:origin], months[:origin], months[origin:origin + steps])
            actual = y[origin:origin + steps]
            for j in range(steps):
                err_h[j + 1].append(float(actual[j] - pred[j]))
                act_h[j + 1].append(float(actual[j]))

        all_err = np.concatenate([err_h[h] for h in range(1, max_h + 1)])
        all_act = np.concatenate([act_h[h] for h in range(1, max_h + 1)])
        mask = all_act != 0
        mape = float(np.mean(np.abs(all_err[mask] / all_act[mask])) * 100) if mask.any() else np.nan
        scores.append(
            {
                "method": name,
                "mape": round(mape, 2),
                "mae": round(float(np.mean(np.abs(all_err))), 2),
                "bias": round(float(np.mean(all_err)), 2),
                "origins": sum(1 for o in range(max(0, n - origins), n) if o >= min_train),
                "max_horizon": max_h,
            }
        )
        errors[name] = {h: err_h[h] for h in range(1, max_h + 1)}
        actuals[name] = {h: act_h[h] for h in range(1, max_h + 1)}
    return pd.DataFrame(scores), errors, actuals


def forecast(con) -> dict:
    """Read the monthly series, pick the best model, persist forecast + scores."""
    hist = con.execute("""
        SELECT month_start, net_revenue
        FROM marts.kpi_monthly
        ORDER BY month_start
    """).fetchdf()
    last_order_date = con.execute("SELECT max(order_date) FROM core.fct_orders").fetchone()[0]

    # Drop an incomplete final month so the model is not fit on a partial period.
    final_month_end = pd.Timestamp(hist["month_start"].iloc[-1]) + pd.offsets.MonthEnd(0)
    if pd.Timestamp(last_order_date) < final_month_end - pd.Timedelta(days=3):
        hist = hist.iloc[:-1]

    y = hist["net_revenue"].to_numpy(dtype=float)
    months = pd.to_datetime(hist["month_start"]).dt.month.to_numpy()

    if len(y) < MIN_HISTORY:
        raise ValueError(f"need at least {MIN_HISTORY} months of history, found {len(y)}")

    scores, errors, _ = backtest(y, months)
    winner = scores.sort_values("mape").iloc[0]
    method = str(winner["method"])

    last_month = pd.Timestamp(hist["month_start"].iloc[-1])
    future_dates = [last_month + pd.offsets.MonthBegin(i + 1) for i in range(HORIZON)]
    point = np.asarray(METHODS[method](y, months, np.array([d.month for d in future_dates])), dtype=float)

    band = []
    for step in range(1, HORIZON + 1):
        errs = np.array(errors[method].get(step, []), dtype=float)
        pooled = np.concatenate([np.asarray(v, dtype=float) for v in errors[method].values()])
        sd = float(np.std(errs, ddof=1)) if len(errs) > 1 else float(np.std(pooled))
        band.append(1.96 * sd)
    band = np.array(band)

    out = pd.DataFrame(
        {
            "month_start": future_dates,
            "forecast": np.round(point, 2),
            "lower_bound": np.round(np.clip(point - band, 0, None), 2),
            "upper_bound": np.round(point + band, 2),
            "method": method,
            "history_months": len(y),
        }
    )

    scores = scores.assign(selected=scores["method"].eq(method)).sort_values("mape")
    con.execute("CREATE SCHEMA IF NOT EXISTS marts")
    con.execute("CREATE OR REPLACE TABLE marts.revenue_forecast AS SELECT *, CAST(now() AS TIMESTAMP) AS generated_at FROM out")
    con.execute("CREATE OR REPLACE TABLE marts.forecast_backtest AS SELECT *, CAST(now() AS TIMESTAMP) AS scored_at FROM scores")

    return {
        "method": method,
        "mape": float(winner["mape"]),
        "mae": float(winner["mae"]),
        "scores": scores.to_dict("records"),
        "forecast": out.to_dict("records"),
    }
