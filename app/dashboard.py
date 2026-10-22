"""Northstar commerce analytics — Streamlit entrypoint.

Run with:  streamlit run app/dashboard.py --server.port 3000
"""

from __future__ import annotations

import streamlit as st

import common

st.set_page_config(
    page_title="Northstar · Commerce Analytics",
    page_icon="◧",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not common.db_ready():
    st.error(
        "The warehouse has not been built yet. Run `make all` (or "
        "`python -m analytics.pipeline --generate`) and refresh this page."
    )
    st.stop()

pages = [
    st.Page("overview.py", title="Executive overview", icon=":material/insights:", default=True),
    st.Page("revenue.py", title="Revenue & forecast", icon=":material/monitoring:"),
    st.Page("customers.py", title="Customers & cohorts", icon=":material/groups:"),
    st.Page("products.py", title="Product performance", icon=":material/inventory_2:"),
    st.Page("marketing.py", title="Marketing & funnel", icon=":material/campaign:"),
    st.Page("data_quality.py", title="Data quality", icon=":material/verified:"),
]

st.navigation(pages, position="sidebar").run()
