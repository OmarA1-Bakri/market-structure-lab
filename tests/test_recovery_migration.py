from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from market_structure_lab.data.migrations import candle_recovery_migration_sql


def test_migration_is_versioned_append_only_and_dump_preferred() -> None:
    sql = candle_recovery_migration_sql()
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_batches" in sql
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_supplements" in sql
    assert "candle_supplements_validated_key_unique" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "CREATE OR REPLACE VIEW market_data.candles_canonical" in sql
    assert "NOT EXISTS" in sql
    assert "FROM market_data.candles AS candle" in sql
    for application_field in ("regime", "confidence", "returns", "volatility", "volume_ratio"):
        assert application_field not in sql


def test_migration_records_every_terminal_resolution_and_real_provenance() -> None:
    sql = candle_recovery_migration_sql()
    for resolution in (
        "recovered",
        "partially_recovered",
        "provider_absent",
        "non_trading",
        "source_unavailable",
        "source_conflict",
        "fetch_failed",
        "unresolved",
    ):
        assert f"'{resolution}'" in sql
    for field in ("payload_checksum", "row_checksum", "source_revision", "retrieved_at"):
        assert field in sql


def test_migration_compiles_modulo_for_psycopg_parameter_rules() -> None:
    compiled = str(text(candle_recovery_migration_sql()).compile(dialect=PGDialect_psycopg()))

    assert "open_time %% 60000" in compiled
