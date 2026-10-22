"""Render a self-contained static analyst report from the warehouse.

The Streamlit app is the working tool; this report is the shareable artefact.
It writes one HTML file with Plotly embedded inline, so it can be published as
a static asset or emailed and opened offline.

    python -m analytics.report
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from .config import DB_PATH, PROJECT_ROOT

OUT_PATH = PROJECT_ROOT / "reports" / "analyst_report.html"

ACCENT = "#1F7A6C"
GOLD = "#E0A458"
RUST = "#B4553F"
INK = "#111827"
MUTED = "#6b7280"
GRID = "#eef1f4"


def _money(v: float) -> str:
    v = float(v or 0)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.1f}k"
    return f"${v:,.0f}"


def _num(v: float) -> str:
    return f"{float(v or 0):,.0f}"


def _pct(v: float, d: int = 1) -> str:
    return f"{float(v or 0):,.{d}f}%"


def _fig(fig: go.Figure, height: int = 340, legend: bool = True) -> str:
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        font=dict(family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", size=12, color="#374151"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0) if legend else dict(),
        showlegend=legend,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="white"),
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})


def _table(df: pd.DataFrame, formats: dict[str, str]) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c in formats and isinstance(v, (int, float)) and pd.notna(v):
                cells.append(f"<td class='num'>{formats[c].format(v)}</td>")
            elif isinstance(v, (int, float)) and pd.notna(v):
                cells.append(f"<td class='num'>{v:,}</td>")
            else:
                cells.append(f"<td>{html.escape(str(v))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _kpi(label: str, value: str, note: str = "") -> str:
    note_html = f"<div class='kpi-note'>{html.escape(note)}</div>" if note else ""
    return (
        f"<div class='kpi'><div class='kpi-label'>{html.escape(label)}</div>"
        f"<div class='kpi-value'>{value}</div>{note_html}</div>"
    )


CSS = """
:root { --ink:#111827; --muted:#6b7280; --accent:#1F7A6C; --line:#e5e7eb; --bg:#ffffff; }
* { box-sizing: border-box; }
body { margin:0; background:#f7f8f7; color:var(--ink);
  font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  line-height:1.55; -webkit-font-smoothing:antialiased; }
.page { max-width:1120px; margin:0 auto; background:var(--bg); padding:56px 64px 72px;
  box-shadow:0 1px 3px rgba(17,24,39,.06); }
header { border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:8px; }
.brand { font-size:.78rem; letter-spacing:.16em; text-transform:uppercase; color:var(--accent); font-weight:700; }
h1 { font-size:2.1rem; margin:.35rem 0 .2rem; letter-spacing:-.02em; }
.sub { color:var(--muted); font-size:.95rem; }
h2 { font-size:1.18rem; margin:2.6rem 0 .2rem; letter-spacing:-.01em; }
h2 .num { color:var(--accent); font-variant-numeric:tabular-nums; margin-right:.45rem; }
.note { color:var(--muted); font-size:.86rem; margin:.15rem 0 .9rem; }
.kpis { display:grid; grid-template-columns:repeat(4, 1fr); gap:14px; margin:26px 0 8px; }
.kpi { border:1px solid var(--line); border-top:3px solid var(--accent); padding:14px 16px; border-radius:4px; }
.kpi-label { font-size:.74rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); font-weight:600; }
.kpi-value { font-size:1.42rem; font-weight:700; margin-top:.3rem; font-variant-numeric:tabular-nums; }
.kpi-note { font-size:.76rem; color:var(--muted); margin-top:.25rem; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:26px; }
.summary { background:#f4f8f7; border-left:3px solid var(--accent); padding:18px 22px; margin:26px 0 0; }
.summary ul { margin:.4rem 0 0; padding-left:1.1rem; }
.summary li { margin-bottom:.45rem; }
table { width:100%; border-collapse:collapse; font-size:.83rem; margin:.4rem 0 1rem; }
th { text-align:left; font-weight:600; color:var(--muted); border-bottom:1px solid var(--ink);
  padding:7px 8px; font-size:.75rem; text-transform:uppercase; letter-spacing:.04em; }
td { border-bottom:1px solid var(--line); padding:7px 8px; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
tr:last-child td { border-bottom:none; }
footer { margin-top:44px; border-top:1px solid var(--line); padding-top:16px; color:var(--muted); font-size:.78rem; }
.tag { display:inline-block; padding:2px 9px; border-radius:99px; background:#f4f8f7; color:var(--accent);
  font-size:.72rem; font-weight:600; letter-spacing:.04em; text-transform:uppercase; }
@media (max-width:820px) { .page{padding:28px} .kpis{grid-template-columns:repeat(2,1fr)} .grid2{grid-template-columns:1fr} }
"""


def build_report(db_path: Path = DB_PATH) -> Path:
    con = duckdb.connect(str(db_path), read_only=True)
    q = lambda sql: con.execute(sql).fetchdf()

    daily = q("SELECT * FROM marts.kpi_daily ORDER BY date")
    monthly = q("SELECT * FROM marts.kpi_monthly ORDER BY month_start")
    forecast = q("SELECT * FROM marts.revenue_forecast ORDER BY month_start")
    scores = q("SELECT * FROM marts.forecast_backtest ORDER BY mape")
    channels = q("SELECT * FROM marts.channel_summary ORDER BY attributed_revenue DESC")
    cohort = q("SELECT * FROM marts.cohort_retention")
    segments = q("SELECT * FROM marts.rfm_segments ORDER BY revenue DESC")
    products = q("SELECT * FROM marts.product_performance ORDER BY revenue DESC")
    funnel = q("SELECT * FROM marts.funnel_summary ORDER BY sessions DESC")
    quality = q("SELECT * FROM quality.checks")
    repeat_rate = q("SELECT 100.0 * count(*) FILTER (WHERE frequency > 1) / count(*) AS pct FROM marts.rfm").iloc[0, 0]
    run = q("SELECT * FROM meta.pipeline_runs ORDER BY started_at DESC LIMIT 1").iloc[0]

    totals = daily[["net_revenue", "orders", "sessions", "purchases", "new_customers"]].sum()
    margin = daily["gross_margin"].sum()
    aov = totals.net_revenue / max(totals.orders, 1)
    cvr = 100 * totals.purchases / max(totals.sessions, 1)
    gm = 100 * margin / max(totals.net_revenue, 1)
    d0, d1 = daily["date"].min(), daily["date"].max()
    latest, prior = monthly.iloc[-1], monthly.iloc[-2]
    mom = (latest.net_revenue - prior.net_revenue) / prior.net_revenue * 100
    best_model = scores.iloc[0]
    failed = int((~quality["passed"]).sum())
    next_month = forecast.iloc[0]
    a_class = products[products["abc_class"] == "A"]
    a_share = 100 * a_class["revenue"].sum() / products["revenue"].sum()

    # ---------------------------------------------------------------- figures
    fig_rev = go.Figure()
    hist = monthly.tail(18)
    fig_rev.add_bar(x=hist["month_start"], y=hist["net_revenue"], name="Net revenue",
                    marker_color="#cfdcd9", hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>")
    fig_rev.add_scatter(x=hist["month_start"], y=hist["rolling_3m_avg"], name="3-month average",
                        line=dict(color=ACCENT, width=2.2))
    if not forecast.empty:
        anchor_x = hist["month_start"].iloc[-1]
        anchor_y = hist["net_revenue"].iloc[-1]
        xs = [anchor_x] + list(forecast["month_start"])
        fig_rev.add_scatter(x=xs, y=[anchor_y] + list(forecast["upper_bound"]), line=dict(width=0),
                            hoverinfo="skip", showlegend=False)
        fig_rev.add_scatter(x=xs, y=[anchor_y] + list(forecast["lower_bound"]), line=dict(width=0),
                            fill="tonexty", fillcolor="rgba(224,164,88,.20)", hoverinfo="skip",
                            showlegend=False)
        fig_rev.add_scatter(x=xs, y=[anchor_y] + list(forecast["forecast"]),
                            name="Forecast", mode="lines+markers",
                            line=dict(color=GOLD, width=2.4, dash="dot"))
    fig_rev.update_yaxes(tickprefix="$", tickformat="~s")

    fig_ch = go.Figure(go.Bar(
        x=channels["attributed_revenue"], y=channels["channel"], orientation="h",
        marker_color=[ACCENT if i == 0 else "#9dc0ba" for i in range(len(channels))],
        hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>",
    ))
    fig_ch.update_xaxes(tickprefix="$", tickformat="~s")

    piv = cohort[cohort["month_index"] <= 12].pivot_table(
        index="cohort_month", columns="month_index", values="retention_pct"
    ).tail(12)
    sizes = cohort.groupby("cohort_month")["cohort_size"].first()
    fig_cohort = go.Figure(go.Heatmap(
        z=piv.values, x=[f"M{c}" for c in piv.columns],
        y=[f"{pd.Timestamp(c):%b %y} (n={int(sizes[c]):,})" for c in piv.index],
        colorscale=[[0, "#f7f9f8"], [0.35, "#a9c8c2"], [1, ACCENT]],
        colorbar=dict(thickness=9), hovertemplate="%{y} · %{x}<br>%{z:.1f}%<extra></extra>",
    ))

    fig_seg = go.Figure()
    fig_seg.add_bar(x=segments["segment"], y=segments["customer_share_pct"], name="% of customers",
                    marker_color="#cfdcd9")
    fig_seg.add_bar(x=segments["segment"], y=segments["revenue_share_pct"], name="% of revenue",
                    marker_color=ACCENT)
    fig_seg.update_layout(barmode="group", xaxis_tickangle=-22)
    fig_seg.update_yaxes(ticksuffix="%")

    fig_pareto = go.Figure(go.Scatter(
        x=products["revenue_rank"], y=products["cumulative_revenue_pct"], mode="lines",
        line=dict(color=ACCENT, width=2.4), hovertemplate="Rank %{x}<br>%{y:.1f}%<extra></extra>",
    ))
    fig_pareto.add_hline(y=70, line_dash="dot", line_color=GOLD)
    fig_pareto.add_hline(y=90, line_dash="dot", line_color="#9ca3af")
    fig_pareto.update_yaxes(ticksuffix="%")

    fig_cac = go.Figure()
    fig_cac.add_bar(x=channels["channel"], y=channels["cac"], name="CAC", marker_color=RUST)
    fig_cac.add_bar(x=channels["channel"], y=channels["avg_clv_12m"], name="Avg 12m CLV", marker_color=ACCENT)
    fig_cac.update_layout(barmode="group", xaxis_tickangle=-20)
    fig_cac.update_yaxes(tickprefix="$", tickformat="~s")

    fig_funnel = go.Figure()
    f = funnel.set_index("channel")
    steps = ["sessions", "product_views", "add_to_carts", "checkouts", "purchases"]
    fig_funnel.add_scatter(
        x=["Sessions", "Product views", "Add to cart", "Checkout", "Purchase"],
        y=[f[s].sum() for s in steps], mode="lines+markers+text",
        text=[f"{v / f['sessions'].sum() * 100:.1f}%" for v in [f[s].sum() for s in steps]],
        textposition="top center", line=dict(color=ACCENT, width=2.4), fill="tozeroy",
        fillcolor="rgba(31,122,108,.08)", hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
    )
    fig_funnel.update_yaxes(tickformat="~s")

    # ---------------------------------------------------------------- tables
    ch_table_df = pd.DataFrame({
        "Channel": channels["channel"].str.replace("_", " ").str.title(),
        "Spend": channels["spend"],
        "New customers": channels["new_customers"],
        "CAC": channels["cac"],
        "Revenue": channels["attributed_revenue"],
        "ROAS": channels["roas"],
        "LTV : CAC": channels["ltv_cac_ratio"],
    })
    ch_table = _table(ch_table_df, {
        "Spend": "${:,.0f}", "CAC": "${:,.0f}", "Revenue": "${:,.0f}",
        "ROAS": "{:.2f}x", "LTV : CAC": "{:.2f}",
    })

    prod_table_df = pd.DataFrame({
        "Product": products["product_name"].head(10),
        "Category": products["category"].head(10),
        "Revenue": products["revenue"].head(10),
        "Units": products["units"].head(10),
        "Margin": products["margin_pct"].head(10),
        "Return rate": products["return_rate_pct"].head(10),
    })
    prod_table = _table(prod_table_df, {
        "Revenue": "${:,.0f}", "Units": "{:,.0f}", "Margin": "{:.1f}%", "Return rate": "{:.1f}%",
    })

    score_table_df = pd.DataFrame({
        "Model": scores["method"].str.replace("_", " ").str.title(),
        "MAPE": scores["mape"],
        "MAE": scores["mae"],
        "Bias": scores["bias"],
        "Selected": scores["selected"].map({True: "✓", False: ""}),
    })
    score_table = _table(score_table_df, {
        "MAPE": "{:.1f}%", "MAE": "${:,.0f}", "Bias": "${:,.0f}",
    })

    q_counts = quality[quality["category"] == "cleaning"]
    quarantined = int(q_counts[~q_counts["check_name"].str.startswith("rowcount_")]["observed"].astype(float).sum())

    ltv = channels.dropna(subset=["ltv_cac_ratio"]).sort_values("ltv_cac_ratio")
    weak = ltv[ltv["ltv_cac_ratio"] < 1.5]
    if not weak.empty:
        w = weak.iloc[0]
        weak_channel_note = (
            f"<strong>{w['channel'].replace('_', ' ').title()}</strong> has the thinnest unit economics at "
            f"{w['ltv_cac_ratio']:.2f}x 12-month CLV per dollar of CAC, so it is a candidate for budget "
            f"reallocation rather than more spend."
        )
    else:
        weak_channel_note = (
            f"Every channel clears 1.5x 12-month CLV per dollar of CAC; the lowest is "
            f"{ltv.iloc[0]['channel'].replace('_', ' ').title()} at {ltv.iloc[0]['ltv_cac_ratio']:.2f}x."
        )

    takeaways = [
        f"Net revenue is <strong>{_money(totals.net_revenue)}</strong> across {_num(totals.orders)} completed "
        f"orders, averaging <strong>{_money(aov)}</strong> per order at a {_pct(gm)} gross margin.",
        f"Revenue grew <strong>{_pct(latest.mom_growth_pct)}</strong> month over month in "
        f"{latest.month_label}, and <strong>{_pct(latest.yoy_growth_pct)}</strong> year over year.",
        f"<strong>{channels.iloc[0]['channel'].replace('_', ' ').title()}</strong> is the largest channel "
        f"({_money(channels.iloc[0]['attributed_revenue'])}, {channels.iloc[0]['roas']:.1f}x ROAS). "
        f"{weak_channel_note}",
        f"{int(segments.iloc[0]['customers']):,} Champions ({segments.iloc[0]['customer_share_pct']:.0f}% of "
        f"customers) generate {segments.iloc[0]['revenue_share_pct']:.0f}% of revenue; "
        f"{int(segments[segments['segment'] == 'At Risk - High Value']['customers'].sum()):,} high-value "
        f"customers are at risk and worth a win-back campaign.",
        f"Class A products are {len(a_class)} of {len(products):,} SKUs and earn {_pct(a_share)} of revenue, "
        f"a textbook Pareto distribution.",
        f"The three-month forecast is {_money(forecast['forecast'].iloc[0])} / "
        f"{_money(forecast['forecast'].iloc[1])} / {_money(forecast['forecast'].iloc[2])}, from "
        f"{best_model['method'].replace('_', ' ')} at {best_model['mape']:.1f}% backtest MAPE.",
    ]

    generated = datetime.now().strftime("%d %B %Y")
    kpis = "".join([
        _kpi("Net revenue", _money(totals.net_revenue), f"{_pct(mom)} month over month"),
        _kpi("Orders", _num(totals.orders), f"{_pct(latest.yoy_growth_pct)} year over year"),
        _kpi("Average order value", _money(aov), f"{_pct(gm)} gross margin"),
        _kpi("Conversion rate", _pct(cvr, 2), f"{_num(totals.sessions)} sessions"),
        _kpi("New customers", _num(totals.new_customers), "first recorded purchase"),
        _kpi("Repeat rate", _pct(repeat_rate), "customers with 2+ orders"),
        _kpi("Next month forecast", _money(next_month["forecast"]), f"{best_model['mape']:.1f}% backtest MAPE"),
        _kpi("Data quality", f"{len(quality) - failed}/{len(quality)}", "checks passing"),
    ])

    parts = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Northstar · Commerce Analytics Report</title><style>{CSS}</style>"
        f"<script>{get_plotlyjs()}</script></head><body><div class='page'>",
        "<header>",
        "<div class='brand'>Northstar · Commerce Analytics</div>",
        "<h1>Revenue, customers and growth: what the data says</h1>",
        f"<div class='sub'>Reporting period {d0:%d %B %Y} – {d1:%d %B %Y} · "
        f"{_num(totals.orders)} orders · {_num(totals.sessions)} sessions · prepared {generated}</div>",
        "</header>",
        f"<div class='kpis'>{kpis}</div>",
        "<div class='summary'><strong>Executive summary</strong><ul>"
        + "".join(f"<li>{t}</li>" for t in takeaways)
        + "</ul></div>",
        "<h2><span class='num'>01</span>Revenue and the next quarter</h2>",
        "<p class='note'>Monthly net revenue with a three-month average. The dotted line is the selected "
        "model's projection; the shaded band is its 95% interval, built from held-out forecast errors.</p>",
        _fig(fig_rev, 360),
        score_table,
        "<h2><span class='num'>02</span>Where revenue comes from</h2>",
        "<div class='grid2'>",
        f"<div><p class='note'>First-touch revenue by acquisition channel.</p>{_fig(fig_ch, 330, False)}</div>",
        f"<div><p class='note'>CAC versus expected 12-month customer value.</p>{_fig(fig_cac, 330)}</div>",
        "</div>",
        ch_table,
        "<h2><span class='num'>03</span>Retention and customer value</h2>",
        f"<p class='note'>Monthly retention by acquisition cohort; month 0 is the acquisition month.</p>"
        f"{_fig(fig_cohort, 380, False)}",
        f"<p class='note'>RFM segments: customers versus revenue contribution.</p>{_fig(fig_seg, 320)}",
        "<h2><span class='num'>04</span>Product portfolio</h2>",
        "<div class='grid2'>",
        f"<div><p class='note'>Cumulative revenue share by product rank.</p>{_fig(fig_pareto, 330, False)}</div>",
        f"<div><p class='note'>Blended session funnel across the period.</p>{_fig(fig_funnel, 330, False)}</div>",
        "</div>",
        prod_table,
        "<h2><span class='num'>05</span>Data quality and methodology</h2>",
        f"<p class='note'>The pipeline quarantined <strong>{quarantined:,}</strong> problem rows across "
        f"{len(q_counts)} cleaning rules (duplicates, orphan keys, pre-signup and future-dated orders, "
        f"negative quantities, unmapped channel names, test accounts). "
        f"<strong>{len(quality) - failed} of {len(quality)}</strong> quality checks pass on run "
        f"<code>{run['run_id']}</code>, which built in {run['duration_seconds']:.1f}s.</p>",
        "<div class='grid2'>",
        "<div><p class='note'><strong>Revenue</strong> is completed orders only, net of discounts, "
        "excluding shipping and refunds. Returns are tracked separately.</p>",
        "<p class='note'><strong>Attribution</strong> is first-touch: revenue is credited to the channel "
        "that acquired the customer, which suits LTV:CAC rather than last-click media optimisation.</p></div>",
        "<div><p class='note'><strong>Forecasting</strong> compares seven models by rolling-origin backtest "
        "at a 1–3 month horizon; the winner is refit on full history.</p>",
        "<p class='note'><strong>Data</strong> is synthetic and seeded for reproducibility, so the patterns "
        "are illustrative rather than commercial fact.</p></div>",
        "</div>",
        "<footer>",
        f"Source: <code>data/warehouse.duckdb</code> · pipeline run {run['run_id']} · "
        f"generated by <code>analytics/report.py</code> on {generated}.",
        "</footer></div></body></html>",
    ]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("".join(parts), encoding="utf-8")
    con.close()
    return OUT_PATH


def main() -> None:
    path = build_report()
    size = path.stat().st_size / 1024 / 1024
    print(f"wrote {path} ({size:.2f} MB)")


if __name__ == "__main__":
    main()
