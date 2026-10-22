"""Data quality: what the cleaning layer caught, and whether the warehouse is sound."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

common.sidebar_filters()
common.page_header("Data quality & lineage", "Every cleaning rule, every assertion, and the latest pipeline runs")

checks = common.q("SELECT * FROM quality.checks ORDER BY category, passed, check_name")
if checks.empty:
    st.info("No quality results yet — run the pipeline.")
    st.stop()

failed = checks[~checks["passed"]]
cleaning = checks[checks["category"] == "cleaning"]
integrity = checks[checks["category"].isin(["integrity", "freshness"])]
model = checks[checks["category"] == "model"]

common.kpi_row(
    [
        {"label": "Checks run", "value": common.number(len(checks)),
         "help": "All assertions evaluated in the latest pipeline run."},
        {"label": "Passing", "value": common.number(len(checks) - len(failed)),
         "delta": "All green" if failed.empty else None, "color": "normal",
         "help": "Assertions that held on the latest build."},
        {"label": "Failing", "value": common.number(len(failed)),
         "help": "Assertions that need attention."},
        {"label": "Rows quarantined", "value": common.number(
            cleaning.loc[~cleaning["check_name"].str.startswith("rowcount_"), "observed"]
            .astype(float).sum() if not cleaning.empty else 0),
         "help": "Raw rows flagged by a cleaning rule (rules can overlap, so this is an upper bound)."},
    ]
)

if not failed.empty:
    st.error(
        "Failing checks: " + ", ".join(f"{r.check_name} ({r.observed})" for r in failed.itertuples())
    )
else:
    st.success("All integrity, freshness and model checks pass on the current warehouse.")

st.divider()
left, right = st.columns([1, 1], gap="large")
with left:
    common.section("What the cleaning layer caught", "Raw issues found in the source exports")
    if not cleaning.empty:
        show = cleaning[cleaning["check_name"].str.startswith("rowcount_") == False].copy()
        show = show[["check_name", "dataset", "observed", "details"]].copy()
        show["observed"] = show["observed"].astype(float).astype(int)
        show = show.sort_values("observed", ascending=False)
        fig = px.bar(show[show["observed"] > 0].head(12), x="observed", y="check_name", orientation="h",
                     labels={"observed": "Rows affected", "check_name": ""},
                     color="observed", color_continuous_scale=["#e6efed", common.ACCENT])
        fig.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
        fig.update_traces(hovertemplate="%{y}<br>%{x:,.0f} rows<extra></extra>")
        st.plotly_chart(common.style_fig(fig, height=360, legend=False), width="stretch", key="dq_clean")
        st.caption("These rows are removed or repaired by the staging models; they never reach the marts.")

with right:
    common.section("Row counts: raw vs clean")
    counts = cleaning[cleaning["check_name"].str.startswith("rowcount_")].copy()
    if not counts.empty:
        counts["clean_rows"] = counts["expected"].str.extract(r"-> (\d+) clean").astype(float)
        counts["raw_rows"] = counts["observed"].astype(float) + counts["clean_rows"]
        counts["table"] = counts["dataset"]
        fig = go.Figure()
        fig.add_bar(x=counts["table"], y=counts["raw_rows"], name="Raw rows", marker_color="#cfdcd9",
                    hovertemplate="%{x}<br>%{y:,.0f} raw<extra></extra>")
        fig.add_bar(x=counts["table"], y=counts["clean_rows"], name="Clean rows", marker_color=common.ACCENT,
                    hovertemplate="%{x}<br>%{y:,.0f} clean<extra></extra>")
        fig.update_layout(barmode="group", xaxis_tickangle=-20)
        st.plotly_chart(common.style_fig(fig, height=360), width="stretch", key="dq_counts")

st.divider()
common.section("Integrity and model checks", "These must pass before the numbers are trustworthy")
show = integrity[["check_name", "dataset", "severity", "passed", "observed", "expected", "details"]].copy()
st.dataframe(
    show.rename(columns={"check_name": "Check", "dataset": "Dataset", "severity": "Severity",
                         "passed": "Passed", "observed": "Observed", "expected": "Expected",
                         "details": "Rule"}),
    hide_index=True, width="stretch",
    column_config={"Passed": st.column_config.CheckboxColumn()},
)

if not model.empty:
    common.section("Model checks")
    st.dataframe(
        model[["check_name", "passed", "observed", "expected", "details"]]
        .rename(columns={"check_name": "Check", "passed": "Passed", "observed": "Observed",
                         "expected": "Expected", "details": "Rule"}),
        hide_index=True, width="stretch",
        column_config={"Passed": st.column_config.CheckboxColumn()},
    )

st.divider()
common.section("Warehouse inventory", "Table sizes and freshness")
inventory = common.q("""
    SELECT 'staging' AS layer, 'staging.orders' AS table_name, count(*) AS rows,
           max(order_date)::VARCHAR AS latest_event
    FROM staging.orders
    UNION ALL SELECT 'core', 'core.fct_orders', count(*), max(order_date)::VARCHAR FROM core.fct_orders
    UNION ALL SELECT 'core', 'core.fct_order_items', count(*), max(order_date)::VARCHAR FROM core.fct_order_items
    UNION ALL SELECT 'core', 'core.fct_sessions', count(*), max(session_date)::VARCHAR FROM core.fct_sessions
    UNION ALL SELECT 'core', 'core.dim_customers', count(*), max(signup_date)::VARCHAR FROM core.dim_customers
    UNION ALL SELECT 'core', 'core.dim_products', count(*), '' FROM core.dim_products
    UNION ALL SELECT 'marts', 'marts.kpi_daily', count(*), max(date)::VARCHAR FROM marts.kpi_daily
    UNION ALL SELECT 'marts', 'marts.rfm', count(*), '' FROM marts.rfm
    UNION ALL SELECT 'marts', 'marts.product_performance', count(*), '' FROM marts.product_performance
"""
)
st.dataframe(
    inventory.rename(columns={"layer": "Layer", "table_name": "Table", "rows": "Rows",
                              "latest_event": "Latest event"}),
    hide_index=True, width="stretch",
    column_config={"Rows": st.column_config.NumberColumn(format="%d")},
)

common.section("Pipeline runs")
runs = common.q("""
    SELECT run_id, started_at, round(duration_seconds, 1) AS duration_s, status,
           customers, orders, order_items, sessions,
           checks_passed, checks_failed, forecast_method, round(forecast_mape, 2) AS forecast_mape
    FROM meta.pipeline_runs ORDER BY started_at DESC LIMIT 15
""")
st.dataframe(
    runs.rename(columns={
        "run_id": "Run", "started_at": "Started", "duration_s": "Duration (s)", "status": "Status",
        "customers": "Customers", "orders": "Orders", "order_items": "Line items", "sessions": "Sessions",
        "checks_passed": "Checks passed", "checks_failed": "Checks failed",
        "forecast_method": "Forecast model", "forecast_mape": "MAPE %",
    }),
    hide_index=True, width="stretch",
    column_config={
        "Started": st.column_config.DatetimeColumn(format="DD MMM YYYY HH:mm"),
        "Duration (s)": st.column_config.NumberColumn(format="%.1f"),
        "Customers": st.column_config.NumberColumn(format="%d"),
        "Orders": st.column_config.NumberColumn(format="%d"),
        "Line items": st.column_config.NumberColumn(format="%d"),
        "Sessions": st.column_config.NumberColumn(format="%d"),
        "MAPE %": st.column_config.NumberColumn(format="%.2f"),
    },
)

with st.expander("How the quality model works"):
    st.markdown(
        """
**Cleaning rules** are applied in `sql/01_staging.sql`: de-duplication on business keys,
null and orphan keys dropped, channel spellings mapped to a canonical list, test accounts
excluded, sign errors repaired, future-dated and pre-signup orders quarantined, and negative
quantity lines removed. Each rule has a matching count in `analytics/quality.py`, so the
dashboard can quantify exactly what was fixed instead of just asserting "the data is clean".

**Integrity checks** assert uniqueness of every key, referential integrity between facts and
dimensions, revenue reconciliation between orders and line items, funnel monotonicity,
retention bounds, RFM score ranges, calendar coverage and freshness.

**`tests/test_pipeline.py`** rebuilds a small dataset end-to-end and fails the build if any
check regresses, so the pipeline cannot silently rot.
        """
    )
