"""Shared helpers for the Streamlit dashboard."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "warehouse.duckdb"

COLORS = ["#1F7A6C", "#E0A458", "#5B7DB1", "#B4553F", "#7C9A80", "#8C6BB1", "#A8A29E"]
ACCENT = COLORS[0]
MUTED = "#6b7280"
px.defaults.color_discrete_sequence = COLORS

PRESETS = {
    "Last 30 days": 30,
    "Last 90 days": 90,
    "Last 12 months": 365,
    "All time": None,
}


def db_ready() -> bool:
    return DB_PATH.exists()


_PARAM_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")


@st.cache_data(ttl=120, show_spinner=False)
def q(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a read-only query, passing only the parameters it actually uses."""
    bound = None
    if params:
        used = set(_PARAM_RE.findall(sql))
        bound = {k: v for k, v in params.items() if k in used} or None
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute(sql, bound or {}).fetchdf()
    finally:
        con.close()


def scalar(sql: str, params: dict | None = None):
    df = q(sql, params)
    return None if df.empty else df.iloc[0, 0]


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.1f}k"
    return f"${v:,.0f}"


def number(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.0f}"


def pct(v, digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.{digits}f}%"


def delta_pct(current: float, previous: float) -> float | None:
    if previous in (0, None) or pd.isna(previous):
        return None
    return (current - previous) / previous * 100


# --------------------------------------------------------------------------- #
# Global filters
# --------------------------------------------------------------------------- #
def sidebar_filters() -> tuple[date, date]:
    """Render the period picker and return the inclusive date window."""
    bounds = q("SELECT min(date) AS mn, max(date) AS mx FROM marts.kpi_daily").iloc[0]
    mn, mx = pd.Timestamp(bounds["mn"]).date(), pd.Timestamp(bounds["mx"]).date()

    with st.sidebar:
        st.markdown(
            "<div style='font-size:1.15rem;font-weight:700;letter-spacing:.02em'>Northstar</div>"
            "<div style='color:#6b7280;font-size:.8rem;margin-bottom:1.2rem'>Commerce analytics</div>",
            unsafe_allow_html=True,
        )
        preset = st.selectbox("Reporting period", list(PRESETS), index=2)
        st.caption(f"Data available {mn:%d %b %Y} – {mx:%d %b %Y}")

        end = mx
        if PRESETS[preset] is None:
            start = mn
        elif preset == "Last 12 months":
            start = max(mn, mx - pd.Timedelta(days=364))
        else:
            start = max(mn, mx - pd.Timedelta(days=PRESETS[preset] - 1))

        st.divider()
        runs = q("SELECT max(started_at) AS ts, count(*) AS n FROM meta.pipeline_runs WHERE status = 'success'")
        if not runs.empty and pd.notna(runs.iloc[0]["ts"]):
            st.caption(f"Warehouse refreshed {pd.Timestamp(runs.iloc[0]['ts']):%d %b %Y %H:%M} · {int(runs.iloc[0]['n'])} run(s)")
        failed = scalar("SELECT count(*) FROM quality.checks WHERE NOT passed")
        if failed:
            st.error(f"{failed} data quality check(s) failing")
        else:
            st.caption("✓ All data quality checks passing")

    return start, end


def prev_window(start: date, end: date) -> tuple[date, date]:
    length = (end - start).days + 1
    return start - pd.Timedelta(days=length), start - pd.Timedelta(days=1)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"### {title}")
    if subtitle:
        st.markdown(f"<div style='color:#6b7280;margin-top:-.6rem;margin-bottom:1rem'>{subtitle}</div>",
                    unsafe_allow_html=True)


def kpi_row(items: list[dict]) -> None:
    """Render metric cards.

    Each item: {label, value, delta (pre-formatted string or None),
    color ('normal' | 'inverse' | 'off'), help}.
    """
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        with col:
            delta = item.get("delta")
            st.metric(
                label=item["label"],
                value=item["value"],
                delta=delta,
                help=item.get("help"),
                delta_color=item.get("color", "normal" if delta else "off"),
            )


def delta_text(current: float, previous: float, suffix: str = "%") -> str | None:
    d = delta_pct(current, previous) if suffix == "%" else current - previous
    if d is None or pd.isna(d):
        return None
    return f"{d:+.1f}{suffix}"


def style_fig(fig: go.Figure, height: int = 320, legend: bool = True, title: str | None = None) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=8, r=8, t=48 if title else 16, b=8),
        title=dict(text=title, font=dict(size=14, color="#111827")) if title else None,
        font=dict(family="Inter, system-ui, sans-serif", size=12, color="#374151"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0) if legend else dict(),
        showlegend=legend,
        hoverlabel=dict(bgcolor="white", font_size=12),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#eef1f4", zeroline=False)
    return fig


def section(title: str, note: str = "") -> None:
    st.markdown(f"**{title}**")
    if note:
        st.caption(note)


def empty_warning(df: pd.DataFrame, label: str = "data") -> bool:
    if df.empty:
        st.info(f"No {label} in the selected period.")
        return True
    return False
