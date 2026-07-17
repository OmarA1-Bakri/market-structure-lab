from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from market_structure_lab.data.migrations import candle_reconciliation_migration_sql


def test_reconciliation_migration_is_append_only_and_promotion_gated() -> None:
    sql = candle_reconciliation_migration_sql()

    for table in (
        "candle_reconciliation_runs",
        "candle_reconciliation_work_units",
        "candle_reconciliation_replacements",
        "candle_reconciliation_coverage",
        "candle_reconciliation_promotions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS market_data.{table}" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "candle_reconciliation_replacement_key_unique" in sql
    assert "CREATE OR REPLACE VIEW market_data.candles_reconciled" in sql
    assert sql.count("open_time >= 1514764800000") == 2
    assert "active_promotion" in sql
    assert "dump_verified_match" in sql
    assert "binance_correction" in sql
    assert "binance_fill" in sql
    assert "market_data.candles AS candle" in sql
    assert "market_data.candle_reconciliation_coverage AS coverage" in sql
    assert "market_data.candle_reconciliation_replacements AS replacement" in sql


def test_reconciliation_migration_compiles_for_psycopg_percent_rules() -> None:
    compiled = str(text(candle_reconciliation_migration_sql()).compile(dialect=PGDialect_psycopg()))

    assert "open_time %% 60000" in compiled
