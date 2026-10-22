"""End-to-end tests: cleaning, star schema, marts and quality gates."""

from __future__ import annotations

import pytest


def test_cleaning_removes_bad_rows(warehouse):
    con = warehouse.con
    raw_orders = con.execute("SELECT count(*) FROM raw.orders").fetchone()[0]
    clean_orders = con.execute("SELECT count(*) FROM staging.orders").fetchone()[0]
    assert clean_orders < raw_orders, "staging should drop duplicates, orphans and future rows"

    # Keys are unique after cleaning.
    dups = con.execute("""
        SELECT count(*) FROM (
            SELECT order_id FROM staging.orders GROUP BY 1 HAVING count(*) > 1
        )
    """).fetchone()[0]
    assert dups == 0

    # No null/unknown references survive.
    assert con.execute("SELECT count(*) FROM staging.orders WHERE customer_id IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM staging.orders WHERE order_ts IS NULL").fetchone()[0] == 0

    # Channel spellings are canonical.
    channels = {r[0] for r in con.execute("SELECT DISTINCT channel FROM staging.orders").fetchall()}
    assert channels <= {
        "organic_search", "direct", "paid_search", "social", "email", "affiliate", "unknown"
    }

    # Test accounts are gone.
    assert con.execute(
        "SELECT count(*) FROM staging.customers WHERE email LIKE '%@example.com'"
    ).fetchone()[0] == 0


def test_star_schema_integrity(warehouse):
    con = warehouse.con
    assert con.execute("""
        SELECT count(*) FROM core.fct_order_items i
        WHERE NOT EXISTS (SELECT 1 FROM core.dim_products p WHERE p.product_id = i.product_id)
    """).fetchone()[0] == 0
    assert con.execute("""
        SELECT count(*) FROM core.fct_orders o
        WHERE NOT EXISTS (SELECT 1 FROM core.dim_customers c WHERE c.customer_id = o.customer_id)
    """).fetchone()[0] == 0
    assert con.execute("""
        SELECT count(*) FROM core.fct_orders o
        WHERE NOT EXISTS (SELECT 1 FROM core.dim_date d WHERE d.date = o.order_date)
    """).fetchone()[0] == 0

    # Orders reconcile to their line items.
    diff = con.execute("""
        SELECT abs((SELECT sum(net_revenue) FROM core.fct_orders)
                 - (SELECT sum(net_revenue) FROM core.fct_order_items))
    """).fetchone()[0]
    assert diff < 0.01


def test_kpi_daily_is_complete_and_consistent(warehouse):
    con = warehouse.con
    gaps = con.execute("""
        SELECT count(*) FROM (
            SELECT date, lag(date) OVER (ORDER BY date) AS prev FROM marts.kpi_daily
        ) WHERE date - prev > 1
    """).fetchone()[0]
    assert gaps == 0, "the daily mart should have a row for every calendar day"

    bad = con.execute("""
        SELECT count(*) FROM marts.kpi_daily
        WHERE net_revenue < 0 OR orders < 0 OR sessions < 0
           OR conversion_rate_pct > 100 OR gross_margin_pct > 100
    """).fetchone()[0]
    assert bad == 0

    # Monthly mart must reconcile to the daily mart.
    daily = con.execute("SELECT sum(net_revenue) FROM marts.kpi_daily").fetchone()[0]
    monthly = con.execute("SELECT sum(net_revenue) FROM marts.kpi_monthly").fetchone()[0]
    assert abs(daily - monthly) < 1.0


def test_funnel_is_monotonic(warehouse):
    con = warehouse.con
    bad = con.execute("""
        SELECT count(*) FROM marts.funnel_summary
        WHERE sessions < product_views OR product_views < add_to_carts
           OR add_to_carts < checkouts OR checkouts < purchases
    """).fetchone()[0]
    assert bad == 0

    cvr = con.execute("""
        SELECT 100.0 * sum(purchases) / sum(sessions) FROM marts.funnel_summary
    """).fetchone()[0]
    assert 0.5 < cvr < 25, f"implausible blended conversion rate: {cvr}"


def test_cohort_retention(warehouse):
    con = warehouse.con
    assert con.execute("""
        SELECT count(*) FROM marts.cohort_retention WHERE retention_pct < 0 OR retention_pct > 100
    """).fetchone()[0] == 0

    # Month 0 is 100% by construction, and month 1 must be below it.
    month0 = con.execute("""
        SELECT min(retention_pct), max(retention_pct) FROM marts.cohort_retention WHERE month_index = 0
    """).fetchone()
    assert month0 == (100.0, 100.0)
    assert con.execute(
        "SELECT max(retention_pct) FROM marts.cohort_retention WHERE month_index = 1"
    ).fetchone()[0] < 100.0


def test_rfm_segments(warehouse):
    con = warehouse.con
    customers = con.execute("SELECT count(*) FROM marts.rfm").fetchone()[0]
    segmented = con.execute("SELECT sum(customers) FROM marts.rfm_segments").fetchone()[0]
    assert customers == segmented

    assert con.execute("""
        SELECT count(*) FROM marts.rfm
        WHERE r_score NOT BETWEEN 1 AND 5 OR f_score NOT BETWEEN 1 AND 5
           OR m_score NOT BETWEEN 1 AND 5 OR segment IS NULL OR clv_12m < 0
    """).fetchone()[0] == 0

    # Revenue shares add up.
    share = con.execute("SELECT sum(revenue_share_pct) FROM marts.rfm_segments").fetchone()[0]
    assert 99 < share < 101


def test_product_pareto(warehouse):
    con = warehouse.con
    class_a = con.execute("""
        SELECT 100.0 * sum(revenue) / (SELECT sum(revenue) FROM marts.product_performance)
        FROM marts.product_performance WHERE abc_class = 'A'
    """).fetchone()[0]
    assert 65 < class_a < 75, "Class A should carry roughly the first 70% of revenue"

    monotonic = con.execute("""
        SELECT count(*) FROM (
            SELECT cumulative_revenue_pct,
                   lag(cumulative_revenue_pct) OVER (ORDER BY revenue_rank) AS prev
            FROM marts.product_performance
        ) WHERE cumulative_revenue_pct < prev
    """).fetchone()[0]
    assert monotonic == 0


def test_channel_economics(warehouse):
    con = warehouse.con
    assert con.execute("""
        SELECT count(*) FROM marts.channel_summary
        WHERE spend < 0 OR roas < 0 OR cac < 0
    """).fetchone()[0] == 0

    spend = con.execute("SELECT sum(spend) FROM marts.channel_summary").fetchone()[0]
    raw_spend = con.execute("SELECT sum(spend_usd) FROM core.fct_marketing_spend").fetchone()[0]
    assert abs(spend - raw_spend) < 1.0, "channel summary must not duplicate spend"

    customers = con.execute("SELECT sum(new_customers) FROM marts.channel_summary").fetchone()[0]
    total = con.execute("SELECT count(*) FROM core.dim_customers").fetchone()[0]
    assert customers == total, "each customer must be attributed to exactly one channel"


def test_forecast_output(warehouse):
    con = warehouse.con
    fc = con.execute("SELECT * FROM marts.revenue_forecast ORDER BY month_start").fetchall()
    assert len(fc) == 3
    for row in fc:
        month_start, forecast, lo, hi = row[0], row[1], row[2], row[3]
        assert forecast > 0
        assert lo <= forecast <= hi, "point forecast must sit inside its interval"

    months = [r[0] for r in fc]
    for prev, nxt in zip(months, months[1:]):
        assert (nxt.year, nxt.month) == (prev.year + (prev.month // 12), prev.month % 12 + 1)

    best = con.execute("""
        SELECT method, mape FROM marts.forecast_backtest ORDER BY mape LIMIT 1
    """).fetchone()
    assert best[1] < 30, f"selected model is not accurate enough: {best}"
    assert con.execute("SELECT method FROM marts.revenue_forecast LIMIT 1").fetchone()[0] == best[0]


def test_quality_gates_pass(warehouse):
    con = warehouse.con
    failing = con.execute("SELECT check_name, observed FROM quality.checks WHERE NOT passed").fetchall()
    assert failing == [], f"quality checks failing: {failing}"

    # Cleaning checks exist for the issues the generator injects.
    names = {r[0] for r in con.execute("SELECT check_name FROM quality.checks").fetchall()}
    for expected in ["duplicate_order_ids", "orders_before_signup", "non_canonical_channel_values",
                     "negative_quantity_lines", "test_accounts"]:
        assert expected in names

    run = con.execute("""
        SELECT status, checks_failed, forecast_method FROM meta.pipeline_runs
        ORDER BY started_at DESC LIMIT 1
    """).fetchone()
    assert run[0] == "success"
    assert run[1] == 0
    assert run[2] is not None


def test_pipeline_is_idempotent(warehouse, tmp_path):
    """Rebuilding over an existing warehouse must replace rows, not append them."""
    import duckdb

    from analytics.pipeline import run_pipeline

    db = tmp_path / "idempotent.duckdb"
    run_pipeline(raw_dir=warehouse.raw_dir, db_path=db, reset=True, verbose=False)
    con = duckdb.connect(str(db), read_only=True)
    first = con.execute("SELECT count(*) FROM core.fct_orders").fetchone()[0]
    con.close()

    run_pipeline(raw_dir=warehouse.raw_dir, db_path=db, reset=False, verbose=False)
    con = duckdb.connect(str(db), read_only=True)
    second = con.execute("SELECT count(*) FROM core.fct_orders").fetchone()[0]
    runs = con.execute("SELECT count(*) FROM meta.pipeline_runs").fetchone()[0]
    con.close()

    assert second == first, "re-running without --reset should replace, not append"
    assert runs == 2
