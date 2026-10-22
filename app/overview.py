"""Executive overview: the five numbers that matter, then where they come from."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

start, end = common.sidebar_filters()
p_start, p_end = common.prev_window(start, end)
params = {"start": start, "end": end, "p_start": p_start, "p_end": p_end}

common.page_header(
    "Executive overview",
    f"{start:%d %b %Y} – {end:%d %b %Y} · compared with {p_start:%d %b} – {p_end:%d %b %Y}",
)

totals = common.q(
    """
    SELECT
        coalesce(sum(net_revenue), 0)  AS revenue,
        coalesce(sum(orders), 0)       AS orders,
        coalesce(sum(new_customers), 0) AS new_customers,
        coalesce(sum(sessions), 0)     AS sessions,
        coalesce(sum(purchases), 0)    AS purchases,
        coalesce(sum(gross_margin), 0) AS margin,
        coalesce(sum(refunds), 0)      AS refunds,
        coalesce(sum(active_customers), 0) AS active_customers
    FROM marts.kpi_daily WHERE date BETWEEN $start AND $end
    """,
    params,
)
prev = common.q(
    """
    SELECT
        coalesce(sum(net_revenue), 0) AS revenue,
        coalesce(sum(orders), 0)      AS orders,
        coalesce(sum(new_customers), 0) AS new_customers,
        coalesce(sum(sessions), 0)    AS sessions,
        coalesce(sum(purchases), 0)   AS purchases,
        coalesce(sum(gross_margin), 0) AS margin,
        coalesce(sum(refunds), 0)     AS refunds
    FROM marts.kpi_daily WHERE date BETWEEN $p_start AND $p_end
    """,
    params,
)

t, p = totals.iloc[0], prev.iloc[0]
aov = t.revenue / t.orders if t.orders else 0
p_aov = p.revenue / p.orders if p.orders else 0
cvr = 100 * t.purchases / t.sessions if t.sessions else 0
p_cvr = 100 * p.purchases / p.sessions if p.sessions else 0
gm = 100 * t.margin / t.revenue if t.revenue else 0
p_gm = 100 * p.margin / p.revenue if p.revenue else 0

common.kpi_row(
    [
        {"label": "Net revenue", "value": common.money(t.revenue),
         "delta": common.delta_text(t.revenue, p.revenue),
         "help": "Completed orders, net of discounts, excluding shipping and refunds."},
        {"label": "Orders", "value": common.number(t.orders),
         "delta": common.delta_text(t.orders, p.orders),
         "help": "Completed orders in the period."},
        {"label": "Average order value", "value": common.money(aov),
         "delta": common.delta_text(aov, p_aov),
         "help": "Net revenue divided by completed orders."},
        {"label": "Gross margin", "value": common.pct(gm),
         "delta": common.delta_text(gm, p_gm, " pts"),
         "help": "Net revenue less product cost, plus shipping. Delta is in percentage points."},
    ]
)

common.kpi_row(
    [
        {"label": "Session conversion", "value": common.pct(cvr, 2),
         "delta": common.delta_text(cvr, p_cvr, " pts"),
         "help": "Purchase sessions divided by all sessions. Delta is in percentage points."},
        {"label": "New customers", "value": common.number(t.new_customers),
         "delta": common.delta_text(t.new_customers, p.new_customers),
         "help": "Customers acquired in this period."},
        {"label": "Sessions", "value": common.number(t.sessions),
         "delta": common.delta_text(t.sessions, p.sessions),
         "help": "Sampled web sessions across all channels."},
        {"label": "Refunds", "value": common.money(t.refunds),
         "delta": common.delta_text(t.refunds, p.refunds), "color": "inverse",
         "help": "Value of returned orders. Lower is better."},
    ]
)

st.divider()

left, right = st.columns([2, 1], gap="large")
with left:
    trend = common.q(
        """
        SELECT date, net_revenue, orders,
               avg(net_revenue) OVER (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_7d
        FROM marts.kpi_daily
        WHERE date BETWEEN $start AND $end
        ORDER BY date
        """,
        params,
    )
    common.section("Revenue trend", "Daily net revenue with a 7-day rolling average")
    fig = go.Figure()
    fig.add_bar(x=trend["date"], y=trend["net_revenue"], name="Daily", marker_color="#cfdcd9",
                hovertemplate="%{x|%d %b %Y}<br>$%{y:,.0f}<extra></extra>")
    fig.add_scatter(x=trend["date"], y=trend["rolling_7d"], name="7-day average",
                    line=dict(color=common.ACCENT, width=2.4), hovertemplate="%{x|%d %b}<br>$%{y:,.0f}<extra></extra>")
    st.plotly_chart(common.style_fig(fig, height=340), width="stretch", key="ov_trend")

with right:
    channels = common.q(
        """
        SELECT channel, sum(net_revenue) AS revenue
        FROM core.fct_orders
        WHERE is_completed AND order_date BETWEEN $start AND $end
        GROUP BY 1 ORDER BY revenue DESC
        """,
        params,
    )
    common.section("Revenue by channel", "First order channel of the buying customer")
    fig = px.bar(
        channels.sort_values("revenue"),
        x="revenue", y="channel", orientation="h",
        color="revenue", color_continuous_scale=["#d9e8e5", common.ACCENT],
        labels={"revenue": "Net revenue", "channel": ""},
    )
    fig.update_traces(hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>")
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(common.style_fig(fig, height=340, legend=False), width="stretch", key="ov_channel")

left, right = st.columns([1, 1], gap="large")
with left:
    cats = common.q(
        """
        SELECT p.category,
               sum(i.net_revenue)  AS revenue,
               sum(i.gross_margin) AS margin
        FROM core.fct_order_items i
        JOIN core.dim_products p USING (product_id)
        WHERE i.is_completed AND i.order_date BETWEEN $start AND $end
        GROUP BY 1 ORDER BY revenue DESC
        """,
        params,
    )
    cats["margin_pct"] = 100 * cats["margin"] / cats["revenue"]
    common.section("Category performance", "Revenue and gross margin by category")
    fig = px.bar(cats, x="category", y="revenue", color="margin_pct",
                 color_continuous_scale=["#f3ddc4", "#b4553f"],
                 labels={"revenue": "Net revenue", "category": "", "margin_pct": "Margin %"})
    fig.update_traces(hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
    st.plotly_chart(common.style_fig(fig, height=330, legend=False), width="stretch", key="ov_cat")

with right:
    seg = common.q("SELECT * FROM marts.rfm_segments ORDER BY revenue DESC")
    common.section("Customer value mix", "RFM segments, all time")
    fig = px.bar(seg.sort_values("revenue"), x="revenue", y="segment", orientation="h",
                 color="revenue", color_continuous_scale=["#d9e8e5", common.ACCENT],
                 labels={"revenue": "Lifetime revenue", "segment": ""})
    fig.update_traces(hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>")
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(common.style_fig(fig, height=330, legend=False), width="stretch", key="ov_seg")

# --------------------------------------------------------------------------- #
# Auto-written takeaways — the numbers a human would actually say out loud
# --------------------------------------------------------------------------- #
st.divider()
common.section("What the numbers say")

ch_top = channels.iloc[0] if not channels.empty else None
share = 100 * ch_top.revenue / t.revenue if ch_top is not None and t.revenue else 0
seg_top = seg.iloc[0] if not seg.empty else None
rev_change = common.delta_pct(t.revenue, p.revenue)

statements = []
if rev_change is not None:
    direction = "up" if rev_change >= 0 else "down"
    statements.append(
        f"Net revenue is **{common.money(t.revenue)}**, {direction} **{abs(rev_change):.1f}%** versus the previous "
        f"period, on {common.number(t.orders)} completed orders."
    )
if ch_top is not None:
    statements.append(
        f"**{ch_top.channel.replace('_', ' ').title()}** is the largest channel with {common.money(ch_top.revenue)} "
        f"({share:.0f}% of revenue)."
    )
if t.sessions:
    statements.append(
        f"Conversion is **{cvr:.2f}%** ({cvr - p_cvr:+.2f} pts); average order value is **{common.money(aov)}** "
        f"({(common.delta_pct(aov, p_aov) or 0):+.1f}%)."
    )
if seg_top is not None:
    statements.append(
        f"**{seg_top.segment}** customers drive {seg_top.revenue_share_pct:.0f}% of lifetime revenue "
        f"across {int(seg_top.customers):,} customers."
    )
for s in statements:
    st.markdown(f"- {s}")

if common.scalar("SELECT count(*) FROM quality.checks WHERE NOT passed"):
    st.warning("Some data quality checks are failing — see the Data quality page before trusting these figures.")
