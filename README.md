# Northstar — Advanced Data Analyst Project

An end-to-end e-commerce analytics project: a realistic raw dataset, a tested
SQL warehouse built on DuckDB, a backtested revenue forecast, and a six-page
analytics dashboard. It is built to look and behave like work you would hand to
a commercial team, not a tutorial notebook.

**What it answers**

- How is revenue trending, and what is it likely to do next quarter?
- Which channels and products actually create profitable growth?
- Which customers are worth keeping, and which are slipping away?
- Is the data trustworthy enough to act on?

---

## Stack

| Layer | Tool |
|---|---|
| Storage / compute | DuckDB 2.0 (single-file warehouse, columnar SQL) |
| Transformation | SQL models in `sql/`, orchestrated by `analytics/pipeline.py` |
| Data generation | NumPy/Pandas → Parquet (`analytics/generate_data.py`) |
| Analytics & modelling | Pandas, NumPy (RFM, cohorts, Holt-Winters, OLS) |
| Presentation | Streamlit + Plotly |
| Testing | pytest + Streamlit `AppTest` |

---

## Quickstart

```bash
make install     # create .venv and install dependencies
make all         # generate ~60k customers of raw data, then build the warehouse
make app         # dashboard on http://localhost:3000
make test        # 23 tests: cleaning, marts, forecast, dashboard pages
```

Or step by step:

```bash
uv venv --python 3.12 .venv && uv pip install -r requirements.txt
PYTHONPATH=src python -m analytics.generate_data          # writes data/raw/*.parquet
PYTHONPATH=src python -m analytics.pipeline --reset       # builds data/warehouse.duckdb
streamlit run app/dashboard.py --server.port 3000
```

A full rebuild takes about 15 seconds on 2 vCPUs.

### Shareable report

`make report` renders a self-contained static analyst report (charts embedded,
works offline) to `reports/analyst_report.html`. The published copy of this
build is live at
**https://site-f412e4a78b524b61a04e5689c2054a73.freebuff.page**.

---

## Data flow

```
data/raw/*.parquet          (messy operational exports)
        │
        ├─ raw.*        views over the parquet files
        │
        ├─ staging.*    typed, de-duplicated, canonicalised, quarantined rows
        │
        ├─ core.*       star schema: dim_date, dim_customers, dim_products,
        │               fct_orders, fct_order_items, fct_sessions, fct_marketing_spend
        │
        └─ marts.*      kpi_daily, kpi_monthly, cohort_retention, rfm, rfm_segments,
                        funnel, funnel_summary, product_performance,
                        channel_economics, channel_summary, revenue_by_dimension,
                        revenue_forecast, forecast_backtest

quality.checks     every cleaning rule + integrity assertion, with pass/fail
meta.pipeline_runs run history: rows, checks, model chosen, accuracy, duration
```

The dashboard reads **only** `marts.*` (plus `quality`/`meta` on the quality
page), so heavy joins never run in the UI.

---

## The dataset

60,000 customers, ~169k orders, ~314k line items, ~3.1M web sessions and daily
marketing spend from Jan 2024 to Sep 2026, generated with genuine structure:

- compounding acquisition growth, weekday and month-of-year seasonality, Black
  Friday / Cyber week spikes and random flash promos;
- per-channel tenure curves, so paid-social cohorts churn faster than organic;
- Pareto-distributed product popularity, prices, costs, discounts, returns;
- sessions that form a real funnel (`landing → product view → cart → checkout →
  purchase`) with channel-specific conversion and bounce behaviour;
- spend that drives acquisition, so CAC and ROAS are derived, not invented.

### Deliberately injected data issues

| Issue | Where | Handled by |
|---|---|---|
| Duplicate order rows (double load) | orders | de-duplication on `order_id` |
| Null and orphan `customer_id` | orders | inner join to `dim_customers` |
| Orders dated before signup / in the future | orders | date sanity filters |
| Messy channel spellings (`ppc`, `fb`, `Paid Search `) | orders, customers, spend | alias map |
| Negative quantities (refund lines) | order_items | `quantity > 0` filter |
| Discount sign errors | orders | absolute value + recompute from line items |
| Null payment method, category, unit cost | all | defaults / imputation |
| Test accounts (`*@example.com`) | customers | excluded |

Each rule has a matching count in `quality.checks`, so the dashboard can state
exactly what was removed instead of asserting "the data is clean".

---

## Metric definitions

| Metric | Definition |
|---|---|
| Net revenue | Completed orders: `sum(quantity × unit_price − discount)`. Excludes shipping and refunds. |
| AOV | Net revenue ÷ completed orders. |
| Gross margin | Net revenue − product cost + shipping. Margin % is over net revenue. |
| Conversion rate | Purchases ÷ sessions (a purchase session is one order). |
| CAC | Channel spend ÷ customers acquired in that channel. |
| ROAS | Revenue from customers acquired in that channel ÷ channel spend (first-touch). |
| 12-month CLV | `AOV × orders-per-year × 0.5^(recency/180) × margin rate` — expected margin over the next year, decayed by recency. |
| RFM segment | R/F/M quintiles (1–5) mapped to Champions, Loyal, New/Promising, At Risk, Hibernating, Needs Attention, Occasional. |
| ABC class | A = first 70% of cumulative product revenue, B = 70–90%, C = remainder. |

Revenue, ROAS and CAC are quoted on completed orders only; returns are tracked
separately so return rate never flatters revenue.

---

## Forecasting

Seven candidate models are scored by **rolling-origin backtest**: each forecasts
the last six months from successive origins, one to three months ahead — the
same horizon the dashboard displays. The winner by MAPE is refit on all history
and projected three months forward with 95% intervals from held-out errors.

| Model | Idea |
|---|---|
| `trend_seasonal_adjusted` | OLS trend + month dummies, rescaled by the latest residual so it tracks the current run-rate |
| `trend_seasonal` | OLS trend + month dummies |
| `trend_only` | Linear trend |
| `decomposition` | Log-linear trend × month-of-year index |
| `holt_winters` | Triple exponential smoothing, multiplicative seasonality, parameters tuned in-sample |
| `seasonal_naive` | Same month last year |
| `moving_average_3` | Last three months |

On the shipped dataset the winner is `trend_seasonal_adjusted` at **8.8% MAPE**
(≈$95k MAE on a ~$1M monthly run-rate), and every model's score is visible in
the dashboard — including the ones that lost badly, which is the point.

---

## Data quality

Two families of checks, all persisted to `quality.checks`:

1. **Cleaning** — how many bad rows existed in the raw exports (informational).
2. **Integrity / freshness / model** — assertions that must pass: key
   uniqueness, referential integrity, order-to-line-item revenue reconciliation,
   funnel monotonicity, retention bounds, RFM score ranges, calendar coverage,
   data freshness, and forecast accuracy.

`tests/test_pipeline.py` fails the build if any check regresses, so the pipeline
cannot silently rot. The Data quality page also shows row counts per layer, the
warehouse inventory and pipeline run history.

---

## Project structure

```
app/
  dashboard.py        Streamlit entrypoint + navigation
  common.py           query cache, date filters, formatting, chart theme
  overview.py         executive KPIs, channel/category mix, written takeaways
  revenue.py          monthly trend, forecast + intervals, seasonality, scorecard
  customers.py        new vs returning, cohort retention, RFM, CLV, top customers
  products.py         Pareto/ABC, category scorecard, margin vs discount
  marketing.py        spend, CAC, ROAS, LTV:CAC, funnel by channel
  data_quality.py     cleaning issues, integrity checks, inventory, run history
sql/
  01_staging.sql      cleaning rules
  02_core.sql         star schema
  03_marts.sql        business aggregates
src/analytics/
  config.py           channels, cost curves, seasonality indices, paths
  generate_data.py    synthetic dataset with injected dirt
  pipeline.py         orchestration: raw → staging → core → marts → quality → forecast
  quality.py          cleaning counts + integrity assertions
  forecast.py         models, rolling-origin backtest, intervals
  report.py           renders the self-contained static analyst report
reports/               generated HTML report (not committed)
deploy/                static site manifest + worker used to publish the report
tests/                23 tests: pipeline, marts, forecast, dashboard pages
```

---

## Notes and limitations

- **Synthetic data.** Patterns are deliberately realistic, but no finding here
  describes a real business. The generator is seeded, so results are
  reproducible; `--seed`/`--customers` change the scenario.
- **First-touch attribution.** Channel revenue is credited to the customer's
  original acquisition channel. This is the right lens for LTV:CAC, not for
  last-click media optimisation.
- **Cohort retention** is measured on completed orders and is naturally
  triangular: recent cohorts have shorter histories.
- **Static snapshot.** The pipeline is batch, not streaming; freshness is a
  check, not a guarantee.
- **Preview settings.** `.streamlit/config.toml` disables CORS/XSRF so the app
  works behind a proxied preview. The dashboard is read-only and holds no
  credentials; if you deploy it, put real authentication in front of it.
