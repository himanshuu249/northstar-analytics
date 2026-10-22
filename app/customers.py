"""Customer value: acquisition, cohort retention, RFM segments and CLV."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

start, end = common.sidebar_filters()
p_start, p_end = common.prev_window(start, end)
params = {"start": start, "end": end, "p_start": p_start, "p_end": p_end}
common.page_header("Customers & cohorts", "Who buys, who comes back, and where the future revenue sits")

new = common.q(
    """SELECT coalesce(sum(new_customers), 0) AS n FROM marts.kpi_daily
       WHERE date BETWEEN $start AND $end""", params,
).iloc[0, 0]
prev_new = common.q(
    """SELECT coalesce(sum(new_customers), 0) AS n FROM marts.kpi_daily
       WHERE date BETWEEN $p_start AND $p_end""", params,
).iloc[0, 0]

total_customers = common.scalar("SELECT count(*) FROM core.dim_customers")
active = common.q("""
    SELECT count(*) AS n FROM core.dim_customers
    WHERE last_order_date BETWEEN $start AND $end
""", params).iloc[0, 0]
buyers_in_window = common.q("""
    SELECT count(DISTINCT customer_id) AS n FROM core.fct_orders
    WHERE is_completed AND order_date BETWEEN $start AND $end
""", params).iloc[0, 0]
repeat_rate = common.q("""
    SELECT 100.0 * count(*) FILTER (WHERE frequency > 1) / count(*) AS pct FROM marts.rfm
""").iloc[0, 0]
avg_clv = common.scalar("SELECT avg(clv_12m) FROM marts.rfm")

common.kpi_row(
    [
        {"label": "New customers", "value": common.number(new),
         "delta": common.delta_text(float(new), float(prev_new)),
         "help": "First-purchase customers in the selected period."},
        {"label": "Buyers in period", "value": common.number(buyers_in_window),
         "help": "Distinct customers with at least one completed order."},
        {"label": "Repeat rate", "value": common.pct(repeat_rate),
         "help": "Share of all customers with more than one completed order."},
        {"label": "Avg. 12-month CLV", "value": common.money(avg_clv),
         "help": "Expected margin from the next 12 months, discounted by recency."},
    ]
)

st.divider()
common.section("New vs returning customers", "Monthly acquisition and repeat purchasing")
nm = common.q("""
    WITH f AS (
        SELECT customer_id, min(order_date) AS first_date
        FROM core.fct_orders WHERE is_completed GROUP BY 1
    )
    SELECT date_trunc('month', o.order_date) AS month_start,
           count(DISTINCT o.customer_id) AS buyers,
           count(DISTINCT o.customer_id) FILTER (WHERE o.order_date = f.first_date) AS new_customers
    FROM core.fct_orders o
    JOIN f USING (customer_id)
    WHERE o.is_completed
    GROUP BY 1 ORDER BY 1
""")
nm["returning"] = (nm["buyers"] - nm["new_customers"]).clip(lower=0)

fig = go.Figure()
fig.add_bar(x=nm["month_start"], y=nm["returning"], name="Returning", marker_color="#cfdcd9",
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>")
fig.add_bar(x=nm["month_start"], y=nm["new_customers"], name="New", marker_color=common.ACCENT,
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>")
fig.update_layout(barmode="stack")
st.plotly_chart(common.style_fig(fig, height=320), width="stretch", key="cust_newret")

st.divider()
common.section("Cohort retention", "Share of each first-purchase cohort still buying N months later")
cohort = common.q("SELECT * FROM marts.cohort_retention WHERE month_index <= 12")
if not cohort.empty:
    pivot = cohort.pivot_table(index="cohort_month", columns="month_index", values="retention_pct")
    sizes = cohort.groupby("cohort_month")["cohort_size"].first()
    pivot = pivot.tail(14)
    labels = [f"{pd.Timestamp(c):%b %y} (n={int(sizes[c]):,})" for c in pivot.index]
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[f"M{c}" for c in pivot.columns], y=labels,
        colorscale=[[0, "#f7f9f8"], [0.35, "#a9c8c2"], [1, common.ACCENT]],
        hovertemplate="%{y} · %{x}<br>%{z:.1f}% retained<extra></extra>",
        colorbar=dict(thickness=10, title="%"),
    ))
    st.plotly_chart(common.style_fig(fig, height=420, legend=False), width="stretch", key="cust_cohort")
    st.caption(
        "Month 0 is the acquisition month (100% by construction). Retention is measured on completed orders; "
        "recent cohorts have shorter histories, so the matrix is naturally triangular."
    )

st.divider()
left, right = st.columns([1, 1], gap="large")
with left:
    common.section("RFM segments", "Recency, frequency and monetary scores, 1–5")
    seg = common.q("""
        SELECT segment, customers, revenue_share_pct, customer_share_pct, avg_monetary, avg_clv_12m
        FROM marts.rfm_segments ORDER BY revenue DESC
    """)
    fig = px.bar(seg, x="customers", y="segment", orientation="h", color="revenue_share_pct",
                 color_continuous_scale=["#dbe9e6", common.ACCENT],
                 labels={"customers": "Customers", "segment": "", "revenue_share_pct": "Revenue %"})
    fig.update_traces(hovertemplate="%{y}<br>%{x:,.0f} customers<extra></extra>")
    st.plotly_chart(common.style_fig(fig, height=330, legend=False), width="stretch", key="cust_seg")

with right:
    common.section("Where revenue comes from", "Customer share vs revenue share by segment")
    fig = go.Figure()
    fig.add_bar(x=seg["segment"], y=seg["customer_share_pct"], name="% of customers", marker_color="#cfdcd9",
                hovertemplate="%{x}<br>%{y:.1f}% of customers<extra></extra>")
    fig.add_bar(x=seg["segment"], y=seg["revenue_share_pct"], name="% of revenue", marker_color=common.ACCENT,
                hovertemplate="%{x}<br>%{y:.1f}% of revenue<extra></extra>")
    fig.update_layout(barmode="group", xaxis_tickangle=-25)
    st.plotly_chart(common.style_fig(fig, height=330), width="stretch", key="cust_segshare")

st.divider()
common.section("Segment detail")
st.dataframe(
    seg.rename(columns={
        "segment": "Segment", "customers": "Customers", "revenue_share_pct": "Revenue %",
        "customer_share_pct": "Customer %", "avg_monetary": "Avg lifetime spend",
        "avg_clv_12m": "Avg 12m CLV",
    }),
    hide_index=True, width="stretch",
    column_config={
        "Customers": st.column_config.NumberColumn(format="%d"),
        "Revenue %": st.column_config.NumberColumn(format="%.1f"),
        "Customer %": st.column_config.NumberColumn(format="%.1f"),
        "Avg lifetime spend": st.column_config.NumberColumn(format="$%.0f"),
        "Avg 12m CLV": st.column_config.NumberColumn(format="$%.0f"),
    },
)

with st.expander("Highest-value customers"):
    top = common.q("""
        SELECT c.customer_id, c.email, c.country, c.acquisition_channel,
               r.frequency, r.monetary, r.clv_12m, r.segment, r.recency_days
        FROM marts.rfm r JOIN core.dim_customers c USING (customer_id)
        ORDER BY r.clv_12m DESC LIMIT 50
    """)
    st.dataframe(
        top, hide_index=True, width="stretch",
        column_config={
            "customer_id": "Customer",
            "acquisition_channel": "Channel",
            "frequency": "Orders",
            "monetary": st.column_config.NumberColumn("Lifetime spend", format="$%.0f"),
            "clv_12m": st.column_config.NumberColumn("12m CLV", format="$%.0f"),
            "recency_days": st.column_config.NumberColumn("Days since order", format="%d"),
        },
    )
