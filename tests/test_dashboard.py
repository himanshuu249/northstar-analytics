"""Smoke-test every dashboard page against the isolated test warehouse.

Pages are executed with Streamlit's AppTest harness, so a broken query or a
bad column reference fails the build instead of showing a stack trace to a
reader of the dashboard.
"""

from __future__ import annotations

import pytest

PAGES = [
    "overview.py",
    "revenue.py",
    "customers.py",
    "products.py",
    "marketing.py",
    "data_quality.py",
]


@pytest.fixture()
def app_common(warehouse):
    import common

    original = common.DB_PATH
    common.DB_PATH = warehouse.db_path
    common.q.clear()
    yield common
    common.DB_PATH = original
    common.q.clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app_common, page):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    script = Path(__file__).resolve().parents[1] / "app" / page
    at = AppTest.from_file(str(script), default_timeout=120).run()
    errors = [str(e.value) for e in at.exception]
    assert errors == [], f"{page} raised: {errors}"
    # Every page should render at least one metric or chart.
    assert at.markdown or at.metric or at.dataframe
