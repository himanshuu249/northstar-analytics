"""Revenue trends, seasonality and the backtested three-month forecast."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common

start, end = common.sidebar_filters()
common.page_header("Revenue & forecast", "Monthly run-rate, seasonality and a model that is judged on held-out months")

monthly = common.q("SELECT * FROM marts.kpi_monthly ORDER BY month_start")
forecast = common.q("SELECT * FROM marts.revenue_forecast ORDER BY month_start")
backtest = common.q("SELECT * FROM marts.forecast_backtest ORDER BY mape")
if common.empty_warning(monthly, "monthly data"):
    st.stop()

latest = monthly.iloc[-1]
prev = monthly.iloc[-2] if len(monthly) > 1 else latest
next_month = forecast.iloc[0] if not forecast.empty else None

common.kpi_row(
    [
        {"label": f"Latest month ({latest.month_label})", "value": common.money(latest.net_revenue),
         "delta": common.delta_text(latest.net_revenue, prev.net_revenue),
         "help": "Net revenue for the most recent complete month versus the month before."},
        {"label": "Year over year", "value": common.delta_text(latest.net_revenue, latest.same_month_last_year) or "—",
         "help": "Growth against the same calendar month last year."},
        {"label": "3-month average", "value": common.money(latest.rolling_3m_avg),
         "help": "Smoothed run-rate, less sensitive to one-off promotions."},
        {"label": "Next month forecast", "value": common.money(next_month.forecast) if next_month is not None else "—",
         "delta": f"{backtest.iloc[0].mape:.1f}% MAPE" if not backtest.empty else None,
         "color": "off",
         "help": "Selected model's point forecast. Delta shows the backtest error, not a change."},
    ]
)

st.divider()

# --------------------------------------------------------------------------- #
# Revenue history + forecast
# --------------------------------------------------------------------------- #
common.section("Monthly revenue and forecast", "Bars are actuals; the dotted line is the selected model's projection with a 95% band")

history = monthly[monthly["month_start"] >= (pd.Timestamp(end) - pd.DateOffset(months=17))].copy()
fig = go.Figure()
fig.add_bar(
    x=history["month_start"], y=history["net_revenue"], name="Net revenue",
    marker_color="#cfdcd9", hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>",
)
fig.add_scatter(
    x=history["month_start"], y=history["rolling_3m_avg"], name="3-month average",
    line=dict(color=common.ACCENT, width=2.2), hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>",
)

if not forecast.empty:
    anchor_x = history["month_start"].iloc[-1]
    anchor_y = history["net_revenue"].iloc[-1]
    bridge_x = [anchor_x] + list(forecast["month_start"])
    bridge_y = [anchor_y] + list(forecast["forecast"])
    upper_y = [anchor_y] + list(forecast["upper_bound"])
    lower_y = [anchor_y] + list(forecast["lower_bound"])
    fig.add_scatter(
        x=bridge_x, y=upper_y, line=dict(width=0), showlegend=False, hoverinfo="skip", name="Upper bound"
    )
    fig.add_scatter(
        x=bridge_x, y=lower_y, line=dict(width=0), fill="tonexty", fillcolor="rgba(224,164,88,0.18)",
        showlegend=False, hoverinfo="skip", name="Lower bound",
    )
    fig.add_scatter(
        x=bridge_x, y=bridge_y, name=f"Forecast ({forecast.iloc[0].method.replace('_', ' ')})",
        line=dict(color="#E0A458", width=2.4, dash="dot"), mode="lines+markers",
        hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>",
    )
st.plotly_chart(common.style_fig(fig, height=380), width="stretch", key="rev_monthly")

if not forecast.empty:
    f1, f2 = forecast.iloc[0], forecast.iloc[-1]
    st.caption(
        f"Forecast: {f1.month_start:%b %Y} {common.money(f1.forecast)} "
        f"(95% CI {common.money(f1.lower_bound)} – {common.money(f1.upper_bound)}), "
        f"rising to {common.money(f2.forecast)} by {f2.month_start:%b %Y}. "
        f"Method selected by rolling-origin backtest; intervals come from held-out errors."
    )

left, right = st.columns([3, 2], gap="large")
with left:
    common.section("Model scorecard", "Each model forecast the last six months from successive origins (1–3 months ahead)")
    bt = backtest[["method", "mape", "mae", "bias", "selected"]].copy()
    bt["method"] = bt["method"].str.replace("_", " ").str.title()
    st.dataframe(
        bt.rename(columns={"method": "Model", "mape": "MAPE %", "mae": "MAE ($)", "bias": "Bias ($)", "selected": "Selected"}),
        hide_index=True, width="stretch",
        column_config={
            "MAPE %": st.column_config.NumberColumn(format="%.1f"),
            "MAE ($)": st.column_config.NumberColumn(format="$%.0f"),
            "Bias ($)": st.column_config.NumberColumn(format="$%.0f"),
            "Selected": st.column_config.CheckboxColumn(),
        },
    )
    st.caption("Bias is signed: a positive value means the model under-forecast in the backtest.")

with right:
    common.section("Seasonality", "Average revenue index by month (1.0 = an average month)")
    seas = monthly.copy()
    seas["month"] = seas["month_start"].dt.month
    seas["index"] = seas["net_revenue"] / seas.groupby("year")["net_revenue"].transform("mean")
    profile = seas.groupby("month")["index"].median().reindex(range(1, 13))
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    colors = [common.ACCENT if v >= 1 else "#b9c9c6" for v in profile.fillna(1)]
    fig = go.Figure(go.Bar(x=names, y=profile.values, marker_color=colors,
                           hovertemplate="%{x}<br>index %{y:.2f}<extra></extra>"))
    fig.add_hline(y=1, line_dash="dot", line_color="#9ca3af")
    st.plotly_chart(common.style_fig(fig, height=300, legend=False), width="stretch", key="rev_season")

st.divider()
left, right = st.columns(2, gap="large")

with left:
    common.section("Weekday pattern", "Average net revenue by day of week")
    wd = common.q("""
        SELECT day_of_week, day_name, avg(net_revenue) AS avg_revenue, avg(aov) AS avg_aov
        FROM marts.kpi_daily
        WHERE net_revenue > 0
        GROUP BY 1, 2 ORDER BY 1
    """)
    fig = px.bar(wd, x="day_name", y="avg_revenue", labels={"day_name": "", "avg_revenue": "Avg revenue"})
    fig.update_traces(marker_color=common.ACCENT, hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
    st.plotly_chart(common.style_fig(fig, height=300, legend=False), width="stretch", key="rev_weekday")

with right:
    common.section("Revenue heatmap", "Monthly revenue by year")
    pivot = monthly.pivot_table(index="year", columns="month", values="net_revenue", aggfunc="sum")
    pivot = pivot.reindex(columns=range(1, 13))
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=names, y=pivot.index.astype(str),
        colorscale=[[0, "#f6f8f7"], [1, common.ACCENT]],
        hovertemplate="%{y} %{x}<br>$%{z:,.0f}<extra></extra>",
        colorbar=dict(thickness=10, len=0.8),
    ))
    st.plotly_chart(common.style_fig(fig, height=300, legend=False), width="stretch", key="rev_heatmap")

st.divider()
common.section("Monthly detail")
table = monthly[["month_label", "net_revenue", "orders", "aov", "gross_margin_pct",
                 "mom_growth_pct", "yoy_growth_pct", "roas"]].copy()
st.dataframe(
    table.sort_values("month_label", ascending=False),
    hide_index=True, width="stretch", height=320,
    column_config={
        "month_label": "Month",
        "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%.0f"),
        "orders": st.column_config.NumberColumn("Orders", format="%d"),
        "aov": st.column_config.NumberColumn("AOV", format="$%.0f"),
        "gross_margin_pct": st.column_config.NumberColumn("Margin %", format="%.1f"),
        "mom_growth_pct": st.column_config.NumberColumn("MoM %", format="%+.1f"),
        "yoy_growth_pct": st.column_config.NumberColumn("YoY %", format="%+.1f"),
        "roas": st.column_config.NumberColumn("ROAS", format="%.2f"),
    },
)
