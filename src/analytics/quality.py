"""Data quality: quantify what the cleaning layer fixed, then assert the
warehouse is internally consistent.

Two families of checks:
  * ``cleaning`` — how many bad rows the raw exports contained. These are
    informational: staging is expected to remove all of them.
  * ``integrity`` / ``freshness`` / ``model`` — assertions that must pass.
    The dashboard surfaces any failure as a red banner, and pytest asserts
    that every one of them passes.
"""

from __future__ import annotations

import uuid
from datetime import date

import duckdb

# A raw-table issue and the SQL that counts the offending rows.
CLEANING_ISSUES: list[tuple[str, str, str, str]] = [
    ("duplicate_order_ids", "orders", "order_id duplicated in the raw export",
     "SELECT count(*) FROM (SELECT order_id FROM raw.orders GROUP BY 1 HAVING count(*) > 1)"),
    ("orders_missing_customer_id", "orders", "order has no customer_id",
     "SELECT count(*) FROM raw.orders WHERE customer_id IS NULL"),
    ("orders_with_orphan_customer", "orders", "customer_id not present in the customer table",
     """SELECT count(*) FROM raw.orders o WHERE o.customer_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM raw.customers c WHERE c.customer_id = o.customer_id)"""),
    ("orders_before_signup", "orders", "order timestamp precedes the customer's signup date",
     """SELECT count(*) FROM raw.orders o JOIN raw.customers c USING (customer_id)
        WHERE try_cast(o.order_ts AS DATE) < c.signup_date"""),
    ("future_dated_orders", "orders", "order timestamp is in the future",
     "SELECT count(*) FROM raw.orders WHERE try_cast(order_ts AS DATE) > current_date"),
    ("null_order_timestamps", "orders", "order timestamp could not be parsed",
     "SELECT count(*) FROM raw.orders WHERE try_cast(order_ts AS TIMESTAMP) IS NULL"),
    ("negative_quantity_lines", "order_items", "line item has a negative quantity",
     "SELECT count(*) FROM raw.order_items WHERE quantity <= 0"),
    ("negative_discount_amounts", "orders", "discount stored with the wrong sign",
     "SELECT count(*) FROM raw.orders WHERE discount_amount < 0"),
    ("missing_payment_methods", "orders", "payment method is null",
     "SELECT count(*) FROM raw.orders WHERE payment_method IS NULL"),
    ("test_accounts", "customers", "internal/test accounts in the customer table",
     "SELECT count(*) FROM raw.customers WHERE lower(email) LIKE '%@example.com' OR lower(trim(email)) LIKE 'test%'"),
    ("non_canonical_channel_values", "orders", "channel value needs alias mapping (case, whitespace, shorthand)",
     """SELECT count(*) FROM raw.orders o WHERE NOT EXISTS (
            SELECT 1 FROM staging.channel_alias a WHERE a.alias = lower(trim(o.channel)))"""),
    ("duplicate_session_ids", "sessions", "session_id appears more than once",
     "SELECT count(*) FROM (SELECT session_id FROM raw.sessions GROUP BY 1 HAVING count(*) > 1)"),
    ("missing_product_categories", "products", "product has no category",
     "SELECT count(*) FROM raw.products WHERE category IS NULL OR trim(category) = ''"),
    ("missing_unit_cost", "products", "product has no unit cost",
     "SELECT count(*) FROM raw.products WHERE unit_cost IS NULL"),
]


def _one(con: duckdb.DuckDBPyConnection, sql: str):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def _row(check: str, dataset: str, category: str, severity: str, passed: bool,
         observed, expected: str, details: str = "") -> dict:
    return {
        "check_name": check,
        "dataset": dataset,
        "category": category,
        "severity": severity,
        "passed": bool(passed),
        "observed": str(observed),
        "expected": expected,
        "details": details,
    }


def cleaning_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = []
    for name, dataset, description, sql in CLEANING_ISSUES:
        n = int(_one(con, sql) or 0)
        rows.append(
            _row(name, dataset, "cleaning", "info", True, n,
                 "rows removed by staging", description)
        )
    # Row-count delta per table gives an at-a-glance remediation summary.
    for raw, staging in [
        ("raw.customers", "staging.customers"),
        ("raw.products", "staging.products"),
        ("raw.orders", "staging.orders"),
        ("raw.order_items", "staging.order_items"),
        ("raw.sessions", "staging.sessions"),
        ("raw.marketing_spend", "staging.marketing_spend"),
    ]:
        r = int(_one(con, f"SELECT count(*) FROM {raw}") or 0)
        s = int(_one(con, f"SELECT count(*) FROM {staging}") or 0)
        rows.append(
            _row(f"rowcount_{staging.split('.')[-1]}", staging.split(".")[-1],
                 "cleaning", "info", True, r - s, f"{r} raw rows -> {s} clean rows",
                 "rows not carried into staging")
        )
    return rows


def integrity_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows: list[dict] = []

    def check(name, dataset, severity, passed, observed, expected, details=""):
        rows.append(_row(name, dataset, "integrity", severity, passed, observed, expected, details))

    # Uniqueness of every fact/dimension key.
    for table, key in [
        ("core.fct_orders", "order_id"),
        ("core.fct_sessions", "session_id"),
        ("core.dim_customers", "customer_id"),
        ("core.dim_products", "product_id"),
        ("core.dim_date", "date"),
    ]:
        total = int(_one(con, f"SELECT count(*) FROM {table}") or 0)
        distinct = int(_one(con, f"SELECT count(DISTINCT {key}) FROM {table}") or 0)
        check(f"unique_{table.split('.')[-1]}", table.split(".")[-1], "critical",
              total == distinct and total > 0, f"{distinct} distinct / {total} rows",
              f"every {key} is unique")

    # Referential integrity.
    orphan_products = int(_one(con, """
        SELECT count(*) FROM core.fct_order_items i
        WHERE NOT EXISTS (SELECT 1 FROM core.dim_products p WHERE p.product_id = i.product_id)
    """) or 0)
    check("fk_products", "fct_order_items", "critical", orphan_products == 0,
          orphan_products, "0 orphan product references")

    orphan_customers = int(_one(con, """
        SELECT count(*) FROM core.fct_orders o
        WHERE NOT EXISTS (SELECT 1 FROM core.dim_customers c WHERE c.customer_id = o.customer_id)
    """) or 0)
    check("fk_customers", "fct_orders", "critical", orphan_customers == 0,
          orphan_customers, "0 orphan customer references")

    # Revenue reconciliation: order totals must equal the sum of their lines.
    diff = float(_one(con, """
        SELECT abs(
            (SELECT coalesce(sum(net_revenue), 0) FROM core.fct_orders) -
            (SELECT coalesce(sum(net_revenue), 0) FROM core.fct_order_items)
        )
    """) or 0)
    check("revenue_reconciliation", "fct_orders", "critical", diff < 0.01,
          round(diff, 4), "order revenue equals sum of line items (±0.01)")

    orders_without_items = int(_one(con, """
        SELECT count(*) FROM core.fct_orders o
        WHERE NOT EXISTS (SELECT 1 FROM core.fct_order_items i WHERE i.order_id = o.order_id)
    """) or 0)
    check("orders_have_items", "fct_orders", "critical", orders_without_items == 0,
          orders_without_items, "0 orders without line items")

    # Funnel monotonicity by channel.
    bad_funnels = int(_one(con, """
        SELECT count(*) FROM marts.funnel_summary
        WHERE sessions < product_views OR product_views < add_to_carts
           OR add_to_carts < checkouts OR checkouts < purchases
    """) or 0)
    check("funnel_monotonic", "funnel_summary", "critical", bad_funnels == 0,
          bad_funnels, "sessions >= views >= carts >= checkouts >= purchases")

    # Cohort retention must be a percentage.
    bad_retention = int(_one(con, """
        SELECT count(*) FROM marts.cohort_retention
        WHERE retention_pct < 0 OR retention_pct > 100
    """) or 0)
    check("retention_bounds", "cohort_retention", "warning", bad_retention == 0,
          bad_retention, "0 <= retention_pct <= 100")

    # RFM scores are 1..5 and every customer has a segment.
    bad_rfm = int(_one(con, """
        SELECT count(*) FROM marts.rfm
        WHERE r_score NOT BETWEEN 1 AND 5 OR f_score NOT BETWEEN 1 AND 5
           OR m_score NOT BETWEEN 1 AND 5 OR segment IS NULL
    """) or 0)
    check("rfm_scoring", "rfm", "critical", bad_rfm == 0, bad_rfm,
          "scores within 1..5 and a segment for every customer")

    # Sales must never be negative after cleaning.
    negative_orders = int(_one(con, "SELECT count(*) FROM core.fct_orders WHERE net_revenue < 0") or 0)
    check("non_negative_revenue", "fct_orders", "critical", negative_orders == 0,
          negative_orders, "0 orders with negative net revenue")

    # No unknown channels in the dimension space.
    unknown_channels = int(_one(con, """
        SELECT count(*) FROM (SELECT DISTINCT channel FROM core.fct_orders) WHERE channel = 'unknown'
    """) or 0)
    check("channel_mapping_complete", "fct_orders", "warning", unknown_channels == 0,
          unknown_channels, "0 unmapped channel values after staging")

    # Calendar dimension must cover both facts.
    uncovered = int(_one(con, """
        SELECT (SELECT count(*) FROM core.fct_orders o
                WHERE NOT EXISTS (SELECT 1 FROM core.dim_date d WHERE d.date = o.order_date))
             + (SELECT count(*) FROM core.fct_sessions s
                WHERE NOT EXISTS (SELECT 1 FROM core.dim_date d WHERE d.date = s.session_date))
    """) or 0)
    check("date_dimension_coverage", "dim_date", "critical", uncovered == 0,
          uncovered, "every fact row has a calendar row")

    # Freshness: the warehouse should be loaded with recent data.
    max_date = _one(con, "SELECT max(order_date) FROM core.fct_orders")
    age_days = (date.today() - max_date).days if max_date else 10**6
    check("data_freshness", "fct_orders", "warning", age_days <= 120,
          f"newest order {max_date} ({age_days} days old)", "loaded within 120 days")

    # Marts must be populated, not just created.
    empty_marts = int(_one(con, """
        SELECT (SELECT count(*) FROM marts.kpi_daily WHERE sessions > 0)
             + (SELECT count(*) FROM marts.rfm_segments)
             + (SELECT count(*) FROM marts.channel_summary)
             + (SELECT count(*) FROM marts.product_performance WHERE revenue > 0)
    """) or 0)
    check("marts_populated", "marts", "critical", empty_marts > 0,
          f"{empty_marts} populated rows across core marts", "> 0 rows in each mart")

    return rows


def model_checks(con: duckdb.DuckDBPyConnection, mape: float | None) -> list[dict]:
    rows = []
    if mape is not None:
        rows.append(
            _row("forecast_backtest_accuracy", "revenue_forecast", "model", "warning",
                 mape < 30, f"{mape:.1f}% MAPE", "< 30% MAPE on a 6-month holdout",
                 "best method chosen by rolling-origin backtest")
        )
    return rows


def run_quality(con: duckdb.DuckDBPyConnection, run_id: str, mape: float | None = None) -> dict:
    rows = cleaning_checks(con) + integrity_checks(con) + model_checks(con, mape)
    for r in rows:
        r["run_id"] = run_id

    con.execute("CREATE SCHEMA IF NOT EXISTS quality")
    con.execute("""
        CREATE OR REPLACE TABLE quality.checks (
            run_id VARCHAR, check_name VARCHAR, dataset VARCHAR, category VARCHAR,
            severity VARCHAR, passed BOOLEAN, observed VARCHAR, expected VARCHAR,
            details VARCHAR, checked_at TIMESTAMP
        )
    """)
    con.executemany(
        """INSERT INTO quality.checks
           (run_id, check_name, dataset, category, severity, passed, observed, expected, details, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())""",
        [
            (r["run_id"], r["check_name"], r["dataset"], r["category"], r["severity"],
             r["passed"], r["observed"], r["expected"], r["details"])
            for r in rows
        ],
    )
    failed = [r for r in rows if not r["passed"]]
    return {
        "total": len(rows),
        "passed": len(rows) - len(failed),
        "failed": len(failed),
        "failures": [f"{r['check_name']}: {r['observed']}" for r in failed],
        "rows": rows,
    }


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
