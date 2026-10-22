"""Marketing efficiency and the conversion funnel, channel by channel."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

start, end = common.sidebar_filters()
p_start, p_end = common.prev_window(start, end)
params = {"start": start, "end": end}
common.page_header(
    "Marketing & funnel",
    "Spend, acquisition cost, returns on ad spend and where the funnel leaks (first-touch attribution)",
)

econ = common.q("""
    WITH spend AS (
        SELECT channel, sum(spend_usd) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks
        FROM core.fct_marketing_spend WHERE date BETWEEN $start AND $end GROUP BY 1
    ),
    acquired AS (
        SELECT acquisition_channel AS channel, count(*) AS new_customers
        FROM core.dim_customers WHERE signup_date BETWEEN $start AND $end GROUP BY 1
    ),
    attributed AS (
        SELECT c.acquisition_channel AS channel, sum(o.net_revenue) AS revenue,
               sum(o.gross_margin) AS margin, count(DISTINCT o.customer_id) AS buyers
        FROM core.fct_orders o JOIN core.dim_customers c USING (customer_id)
        WHERE o.is_completed AND o.order_date BETWEEN $start AND $end
        GROUP BY 1
    ),
    clv AS (
        SELECT c.acquisition_channel AS channel, avg(r.clv_12m) AS avg_clv_12m
        FROM marts.rfm r JOIN core.dim_customers c USING (customer_id) GROUP BY 1
    )
    SELECT s.channel, s.spend, s.impressions, s.clicks,
           coalesce(a.new_customers, 0) AS new_customers,
           s.spend / nullif(a.new_customers, 0) AS cac,
           coalesce(t.revenue, 0) AS revenue,
           coalesce(t.margin, 0) AS margin,
           coalesce(t.buyers, 0) AS buyers,
           coalesce(t.revenue, 0) / nullif(s.spend, 0) AS roas,
           coalesce(cl.avg_clv_12m, 0) AS avg_clv_12m
    FROM spend s
    LEFT JOIN acquired a ON a.channel = s.channel
    LEFT JOIN attributed t ON t.channel = s.channel
    LEFT JOIN clv cl ON cl.channel = s.channel
    ORDER BY revenue DESC
""", params)

funnel = common.q("""
    SELECT channel, sum(sessions) AS sessions, sum(product_views) AS product_views,
           sum(add_to_carts) AS add_to_carts, sum(checkouts) AS checkouts, sum(purchases) AS purchases
    FROM marts.funnel
    WHERE month_start BETWEEN date_trunc('month', CAST($start AS DATE)) AND $end
    GROUP BY 1 ORDER BY sessions DESC
""", params)

spend_total = econ["spend"].sum()
rev_total = econ["revenue"].sum()
cac_blend = spend_total / econ["new_customers"].sum() if econ["new_customers"].sum() else 0
roas_blend = rev_total / spend_total if spend_total else 0
best = econ.sort_values("roas", ascending=False).iloc[0] if not econ.empty else None

common.kpi_row(
    [
        {"label": "Marketing spend", "value": common.money(spend_total),
         "help": "Media and content spend in the selected period."},
        {"label": "Blended CAC", "value": common.money(cac_blend),
         "help": "Total spend divided by newly acquired customers."},
        {"label": "Blended ROAS", "value": f"{roas_blend:.2f}x",
         "help": "Revenue from customers acquired in this period, divided by spend."},
        {"label": "Best channel",
         "value": best.channel.replace("_", " ").title() if best is not None else "—",
         "delta": f"{best.roas:.1f}x ROAS" if best is not None else None, "color": "off",
         "help": "Highest return on ad spend in the period."},
    ]
)

st.divider()
left, right = st.columns(2, gap="large")
with left:
    common.section("Spend vs attributed revenue", "By channel")
    fig = go.Figure()
    fig.add_bar(x=econ["channel"], y=econ["spend"], name="Spend", marker_color="#cfdcd9",
                hovertemplate="%{x}<br>spend $%{y:,.0f}<extra></extra>")
    fig.add_bar(x=econ["channel"], y=econ["revenue"], name="Attributed revenue", marker_color=common.ACCENT,
                hovertemplate="%{x}<br>revenue $%{y:,.0f}<extra></extra>")
    fig.update_layout(barmode="group", xaxis_tickangle=-20)
    st.plotly_chart(common.style_fig(fig, height=330), width="stretch", key="mkt_spendrev")

with right:
    common.section("Return on ad spend", "Revenue per dollar spent, by channel")
    fig = px.bar(econ.sort_values("roas"), x="roas", y="channel", orientation="h",
                 color="roas", color_continuous_scale=["#e8d3bd", common.ACCENT],
                 labels={"roas": "ROAS (x)", "channel": ""})
    fig.add_vline(x=1, line_dash="dot", line_color="#b4553f", annotation_text="break-even",
                  annotation_position="top")
    fig.update_traces(hovertemplate="%{y}<br>%{x:.2f}x<extra></extra>")
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(common.style_fig(fig, height=330, legend=False), width="stretch", key="mkt_roas")

st.divider()
left, right = st.columns([1, 1], gap="large")
with left:
    common.section("CAC vs 12-month CLV", "A channel is healthy when CLV comfortably exceeds acquisition cost")
    fig = go.Figure()
    fig.add_bar(x=econ["channel"], y=econ["cac"], name="CAC", marker_color="#b4553f",
                hovertemplate="%{x}<br>CAC $%{y:,.0f}<extra></extra>")
    fig.add_bar(x=econ["channel"], y=econ["avg_clv_12m"], name="Avg 12m CLV", marker_color=common.ACCENT,
                hovertemplate="%{x}<br>CLV $%{y:,.0f}<extra></extra>")
    fig.update_layout(barmode="group", xaxis_tickangle=-20)
    st.plotly_chart(common.style_fig(fig, height=330), width="stretch", key="mkt_cac")

with right:
    common.section("Funnel by channel", "Sessions to purchase in the selected period")
    if not funnel.empty:
        chosen = st.selectbox("Channel", funnel["channel"].tolist(), key="mkt_funnel_channel")
        row = funnel[funnel["channel"] == chosen].iloc[0]
        steps = ["Sessions", "Product views", "Add to cart", "Checkout", "Purchase"]
        values = [row.sessions, row.product_views, row.add_to_carts, row.checkouts, row.purchases]
        fig = go.Figure(go.Funnel(
            y=steps, x=values,
            textinfo="value+percent initial",
            marker=dict(color=["#e6efed", "#bcd6d1", "#8fbcb5", "#5d9b93", common.ACCENT]),
            connector=dict(line=dict(color="#e5e7eb")),
        ))
        st.plotly_chart(common.style_fig(fig, height=330, legend=False), width="stretch", key="mkt_funnel")
        drop = (1 - row.purchases / row.sessions) * 100 if row.sessions else 0
        st.caption(f"{chosen.replace('_', ' ').title()}: {row.purchases:,.0f} purchases from "
                   f"{row.sessions:,.0f} sessions ({100 - drop:.2f}% conversion).")

st.divider()
common.section("Channel economics detail")
table = econ.copy()
table["ltv_cac"] = table["avg_clv_12m"] / table["cac"].replace(0, float("nan"))
table["ctr_pct"] = 100 * table["clicks"] / table["impressions"].replace(0, pd.NA)
st.dataframe(
    table.rename(columns={
        "channel": "Channel", "spend": "Spend", "impressions": "Impressions", "clicks": "Clicks",
        "ctr_pct": "CTR %", "new_customers": "New customers", "cac": "CAC", "revenue": "Attributed revenue",
        "margin": "Gross margin", "buyers": "Buyers", "roas": "ROAS", "avg_clv_12m": "Avg 12m CLV",
        "ltv_cac": "LTV : CAC",
    }),
    hide_index=True, width="stretch",
    column_config={
        "Spend": st.column_config.NumberColumn(format="$%.0f"),
        "Impressions": st.column_config.NumberColumn(format="%d"),
        "Clicks": st.column_config.NumberColumn(format="%d"),
        "CTR %": st.column_config.NumberColumn(format="%.2f"),
        "New customers": st.column_config.NumberColumn(format="%d"),
        "CAC": st.column_config.NumberColumn(format="$%.0f"),
        "Attributed revenue": st.column_config.NumberColumn(format="$%.0f"),
        "Gross margin": st.column_config.NumberColumn(format="$%.0f"),
        "Buyers": st.column_config.NumberColumn(format="%d"),
        "ROAS": st.column_config.NumberColumn(format="%.2fx"),
        "Avg 12m CLV": st.column_config.NumberColumn(format="$%.0f"),
        "LTV : CAC": st.column_config.NumberColumn(format="%.2f"),
    },
)
st.caption(
    "Attribution is first-touch: revenue is credited to the channel that originally acquired the customer, "
    "which is why ROAS here reflects lifetime value economics rather than last-click media performance."
)
