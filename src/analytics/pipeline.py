"""Build the warehouse: raw -> staging -> core -> marts -> quality -> forecast.

    python -m analytics.pipeline             # build from existing raw data
    python -m analytics.pipeline --generate  # generate data first, then build
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import duckdb

from . import forecast as forecast_mod
from .config import CHANNEL_ALIASES, DB_PATH, RAW_DIR, SQL_DIR, GenConfig
from .quality import new_run_id, run_quality

RAW_FILES = {
    "customers": "customers.parquet",
    "products": "products.parquet",
    "orders": "orders.parquet",
    "order_items": "order_items.parquet",
    "marketing_spend": "marketing_spend.parquet",
}


def _create_raw_views(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> None:
    for schema in ("raw", "staging", "core", "marts", "quality", "meta"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for name, filename in RAW_FILES.items():
        path = (raw_dir / filename).as_posix()
        con.execute(f"CREATE OR REPLACE VIEW raw.{name} AS SELECT * FROM read_parquet('{path}')")
    sessions = (raw_dir / "sessions" / "*.parquet").as_posix()
    con.execute(f"CREATE OR REPLACE VIEW raw.sessions AS SELECT * FROM read_parquet('{sessions}')")


def _create_channel_alias(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    con.execute("CREATE OR REPLACE TABLE staging.channel_alias (alias VARCHAR, canonical VARCHAR)")
    rows = {(alias.lower().strip(), canonical) for alias, canonical in CHANNEL_ALIASES.items()}
    rows |= {(canonical, canonical) for canonical in set(CHANNEL_ALIASES.values())}
    rows.add(("unknown", "unknown"))
    con.executemany("INSERT INTO staging.channel_alias VALUES (?, ?)", sorted(rows))


def _run_sql_files(con: duckdb.DuckDBPyConnection, sql_dir: Path) -> list[str]:
    executed = []
    for path in sorted(sql_dir.glob("*.sql")):
        started = time.time()
        con.execute(path.read_text())
        executed.append(path.name)
        print(f"  ran {path.name:<18} {time.time() - started:5.1f}s")
    return executed


def _table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = [
        "staging.customers", "staging.orders", "staging.order_items", "staging.sessions",
        "core.dim_customers", "core.fct_orders", "core.fct_order_items", "core.fct_sessions",
        "marts.kpi_daily", "marts.rfm", "marts.product_performance",
    ]
    return {t: int(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in tables}


def _log_run(con, run_id, started, duration, status, counts, quality, fc, notes="") -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta.pipeline_runs (
            run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds DOUBLE,
            status VARCHAR, customers BIGINT, orders BIGINT, order_items BIGINT, sessions BIGINT,
            checks_passed INTEGER, checks_failed INTEGER, forecast_method VARCHAR,
            forecast_mape DOUBLE, notes VARCHAR
        )
    """)
    con.execute(
        """INSERT INTO meta.pipeline_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            run_id, started, datetime.now(), round(duration, 2), status,
            counts.get("core.dim_customers", 0),
            counts.get("core.fct_orders", 0),
            counts.get("core.fct_order_items", 0),
            counts.get("core.fct_sessions", 0),
            quality["passed"], quality["failed"],
            fc["method"] if fc else None,
            round(fc["mape"], 2) if fc else None,
            notes,
        ],
    )


def run_pipeline(
    raw_dir: Path | str = RAW_DIR,
    db_path: Path | str = DB_PATH,
    reset: bool = False,
    verbose: bool = True,
) -> dict:
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    missing = [f for f in RAW_FILES.values() if not (raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"missing raw files in {raw_dir}: {missing}. "
            "Run `python -m analytics.generate_data` first (or pass --generate)."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()

    run_id = new_run_id()
    started = time.time()
    con = duckdb.connect(str(db_path))
    try:
        con.execute("SET threads TO 4")
        if verbose:
            print(f"pipeline run {run_id} -> {db_path}")
        _create_raw_views(con, raw_dir)
        _create_channel_alias(con)
        files = _run_sql_files(con, SQL_DIR)

        counts = _table_counts(con)
        fc = forecast_mod.forecast(con)
        quality = run_quality(con, run_id, mape=fc["mape"])
        duration = time.time() - started
        _log_run(con, run_id, datetime.fromtimestamp(started), duration, "success", counts, quality, fc)

        if verbose:
            print(f"\nforecast: {fc['method']} (backtest MAPE {fc['mape']:.1f}%)")
            print(f"quality : {quality['passed']}/{quality['total']} checks passed, "
                  f"{quality['failed']} failed")
            print(f"built in {duration:.1f}s, files: {', '.join(files)}")
            if quality["failures"]:
                for f in quality["failures"]:
                    print(f"  FAILED {f}")

        return {
            "run_id": run_id,
            "db_path": str(db_path),
            "duration_seconds": duration,
            "counts": counts,
            "quality": quality,
            "forecast": fc,
            "files": files,
        }
    except Exception as exc:  # keep a record of failed runs too
        try:
            _log_run(con, run_id, datetime.fromtimestamp(started),
                     time.time() - started, "failed", {}, {"passed": 0, "failed": 0}, None,
                     str(exc)[:400])
        except Exception:
            pass
        raise
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the analytics warehouse")
    ap.add_argument("--generate", action="store_true", help="generate raw data first")
    ap.add_argument("--customers", type=int, default=60_000)
    ap.add_argument("--raw", default=str(RAW_DIR))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--reset", action="store_true", help="delete the existing warehouse first")
    args = ap.parse_args()

    if args.generate:
        from .generate_data import generate

        generate(GenConfig(n_customers=args.customers, out_dir=Path(args.raw)))

    run_pipeline(raw_dir=Path(args.raw), db_path=Path(args.db), reset=args.reset)


if __name__ == "__main__":
    main()
