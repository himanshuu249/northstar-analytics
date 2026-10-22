"""Shared fixtures: build a small but complete warehouse once per test session."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory) -> SimpleNamespace:
    from analytics.config import GenConfig
    from analytics.generate_data import generate
    from analytics.pipeline import run_pipeline

    root = tmp_path_factory.mktemp("northstar")
    raw_dir = root / "raw"
    db_path = root / "warehouse.duckdb"

    generate(GenConfig(n_customers=1500, out_dir=raw_dir, seed=7))
    result = run_pipeline(raw_dir=raw_dir, db_path=db_path, reset=True, verbose=False)

    con = duckdb.connect(str(db_path), read_only=True)
    yield SimpleNamespace(con=con, db_path=db_path, raw_dir=raw_dir, result=result)
    con.close()


@pytest.fixture(scope="session")
def rows(warehouse):
    """Helper that returns the first column of a query."""
    def _rows(sql: str, params: list | None = None) -> list:
        return [r[0] for r in warehouse.con.execute(sql, params or []).fetchall()]
    return _rows
