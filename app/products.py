"""Product performance: Pareto, margin quality and return risk."""

from __future__ import annotations

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

start, end = common.sidebar_filters()
params = {"start": start, "end": end}
common.page_header("Product performance", "Where the catalogue earns, and which SKUs quietly cost you margin")

abc = common.q("""
    SELECT abc_class, count(*) AS products, sum(revenue) AS revenue, sum(gross_margin) AS margin
    FROM marts.product_performance GROUP BY 1 ORDER BY 1
""")
top = common.q("""
    SELECT * FROM marts.product_performance ORDER BY revenue DESC LIMIT 15
""")
sku_total = int(abc["products"].sum()) if not abc.empty else 0
a_share = abc.loc[abc["abc_class"] == "A", "revenue"].sum() / max(abc["revenue"].sum(), 1) * 100
a_products = int(abc.loc[abc["abc_class"] == "A", "products"].sum()) if not abc.empty else 0
worst_returns = common.q("""
    SELECT product_name, category, orders, return_rate_pct, revenue
    FROM marts.product_performance WHERE orders >= 20
    ORDER BY return_rate_pct DESC LIMIT 1
""")

common.kpi_row(
    [
        {"label": "Active SKUs", "value": common.number(sku_total),
         "help": "Products in the catalogue."},
        {"label": "Class A SKUs", "value": f"{a_products:,}",
         "help": "Products making up the first 70% of cumulative revenue (Pareto)."},
        {"label": "Revenue from A", "value": common.pct(a_share),
         "help": "Share of revenue earned by Class A products."},
        {"label": "Worst return rate",
         "value": common.pct(worst_returns.iloc[0]["return_rate_pct"]) if not worst_returns.empty else "—",
         "help": f"{worst_returns.iloc[0]['product_name']}" if not worst_returns.empty else ""},
    ]
)

st.divider()
left, right = st.columns([3, 2], gap="large")
with left:
    common.section("Pareto curve", "Cumulative revenue share by product rank")
    cur = common.q("""
        SELECT revenue_rank, cumulative_revenue_pct, abc_class
        FROM marts.product_performance WHERE revenue > 0 ORDER BY revenue_rank
    """)
    fig = go.Figure()
    fig.add_scatter(x=cur["revenue_rank"], y=cur["cumulative_revenue_pct"], mode="lines",
                    line=dict(color=common.ACCENT, width=2.4), name="Cumulative revenue",
                    hovertemplate="Rank %{x}<br>%{y:.1f}% of revenue<extra></extra>")
    fig.add_hline(y=70, line_dash="dot", line_color="#E0A458",
                  annotation_text="A/B cut-off (70%)", annotation_position="bottom right")
    fig.add_hline(y=90, line_dash="dot", line_color="#9ca3af",
                  annotation_text="B/C cut-off (90%)", annotation_position="bottom right")
    fig.update_layout(xaxis_title="Product rank", yaxis_title="Cumulative revenue %")
    st.plotly_chart(common.style_fig(fig, height=360, legend=False), width="stretch", key="prod_pareto")

with right:
    common.section("ABC classes")
    fig = go.Figure()
    fig.add_bar(x=abc["abc_class"], y=abc["products"], name="Products", marker_color="#cfdcd9", yaxis="y",
                hovertemplate="%{x}: %{y} products<extra></extra>")
    fig.add_bar(x=abc["abc_class"], y=abc["revenue"], name="Revenue", marker_color=common.ACCENT, yaxis="y2",
                hovertemplate="%{x}: $%{y:,.0f}<extra></extra>")
    fig.update_layout(
        yaxis=dict(title="Products"),
        yaxis2=dict(title="Revenue", overlaying="y", side="right", showgrid=False),
        barmode="group",
    )
    st.plotly_chart(common.style_fig(fig, height=360), width="stretch", key="prod_abc")
    st.caption(
        f"Class A is {a_products} SKUs ({a_products / max(sku_total, 1) * 100:.0f}% of the catalogue) "
        f"but earns {a_share:.0f}% of revenue."
    )

st.divider()
common.section("Category scorecard", "Revenue, margin and return behaviour by category")
cat = common.q("""
    SELECT category,
           sum(revenue)                AS revenue,
           sum(gross_margin)           AS margin,
           sum(units)                  AS units,
           count(*)                    AS skus,
           sum(returned_orders)        AS returned_orders,
           avg(return_rate_pct)        AS return_rate_pct
    FROM marts.product_performance
    GROUP BY 1 ORDER BY revenue DESC
""")
cat["margin_pct"] = 100 * cat["margin"] / cat["revenue"].replace(0, np.nan)
st.dataframe(
    cat.rename(columns={"category": "Category", "revenue": "Revenue", "margin": "Gross margin",
                        "margin_pct": "Margin %", "units": "Units", "skus": "SKUs",
                        "returned_orders": "Returned orders", "return_rate_pct": "Avg return rate %"}),
    hide_index=True, width="stretch",
    column_config={
        "Revenue": st.column_config.NumberColumn(format="$%.0f"),
        "Gross margin": st.column_config.NumberColumn(format="$%.0f"),
        "Margin %": st.column_config.NumberColumn(format="%.1f"),
        "Units": st.column_config.NumberColumn(format="%d"),
        "SKUs": st.column_config.NumberColumn(format="%d"),
        "Returned orders": st.column_config.NumberColumn(format="%d"),
        "Avg return rate %": st.column_config.NumberColumn(format="%.1f"),
    },
)

st.divider()
left, right = st.columns(2, gap="large")
with left:
    common.section("Top 15 products by revenue")
    show = top[["product_name", "category", "revenue", "units", "margin_pct", "return_rate_pct"]].copy()
    st.dataframe(
        show.rename(columns={"product_name": "Product", "category": "Category", "revenue": "Revenue",
                             "units": "Units", "margin_pct": "Margin %", "return_rate_pct": "Return %"}),
        hide_index=True, width="stretch", height=420,
        column_config={
            "Revenue": st.column_config.NumberColumn(format="$%.0f"),
            "Units": st.column_config.NumberColumn(format="%d"),
            "Margin %": st.column_config.NumberColumn(format="%.0f"),
            "Return %": st.column_config.NumberColumn(format="%.1f"),
        },
    )

with right:
    common.section("High revenue, weak margin", "Products worth repricing or renegotiating")
    weak = common.q("""
        SELECT product_name, revenue, margin_pct, avg_discount_pct
        FROM marts.product_performance
        WHERE revenue > (SELECT avg(revenue) FROM marts.product_performance)
        ORDER BY margin_pct ASC LIMIT 12
    """)
    fig = px.scatter(weak, x="avg_discount_pct", y="margin_pct", size="revenue", text="product_name",
                     labels={"avg_discount_pct": "Average discount %", "margin_pct": "Gross margin %"},
                     color="margin_pct", color_continuous_scale=["#b4553f", "#e6c9a8"])
    fig.update_traces(textposition="top center", textfont_size=9,
                      hovertemplate="%{text}<br>margin %{y:.1f}%<br>discount %{x:.1f}%<extra></extra>")
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(common.style_fig(fig, height=420, legend=False), width="stretch", key="prod_weak")
