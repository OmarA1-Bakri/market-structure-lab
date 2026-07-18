from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
import pytest

from market_structure_lab.data.migrations import (
    RECOVERY_ADVISORY_LOCK_NAME,
    candle_reconciliation_migration_sql,
    prepare_reconciliation_schema,
    reconciliation_migration_lock_sql,
)


class _Result:
    def __init__(self, scalar: bool = True) -> None:
        self.scalar = scalar

    def scalar_one(self) -> bool:
        return self.scalar


class _Connection:
    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.statements: list[tuple[str, object | None]] = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))
        return _Result(self.acquired)


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


def test_reconciliation_migration_lock_is_transaction_scoped_and_stable() -> None:
    sql = reconciliation_migration_lock_sql()

    assert sql == "SELECT pg_advisory_xact_lock(4875179636632247649)"
    assert "pg_try_advisory" not in sql


def test_reconciliation_migration_leaves_transaction_control_to_the_caller() -> None:
    transaction_lines = {
        line.strip().upper() for line in candle_reconciliation_migration_sql().splitlines()
    }

    assert "BEGIN;" not in transaction_lines
    assert "COMMIT;" not in transaction_lines


def test_work_unit_lookup_migration_is_idempotent_and_uses_exact_lookup_columns() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "src"
        / "market_structure_lab"
        / "data"
        / "migrations"
        / "0003_reconciliation_work_unit_lookup.sql"
    )

    assert migration_path.is_file()
    normalized = " ".join(migration_path.read_text(encoding="utf-8").split())
    assert (
        "CREATE INDEX IF NOT EXISTS candle_reconciliation_replacements_work_unit_lookup_idx "
        "ON market_data.candle_reconciliation_replacements(run_id, work_unit_id);"
    ) in normalized


def test_reconciliation_schema_preparation_orders_shared_and_numeric_locks_before_ddl() -> None:
    connection = _Connection()

    prepare_reconciliation_schema(connection)  # type: ignore[arg-type]

    assert len(connection.statements) == 5
    shared_lock, migration_lock, recovery_migration, reconciliation_migration, lookup_migration = (
        connection.statements
    )
    assert shared_lock == (
        "SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_name, 0))",
        {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
    )
    assert migration_lock[0] == "SELECT pg_advisory_xact_lock(4875179636632247649)"
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_runs" in recovery_migration[0]
    assert (
        "CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_runs"
        in reconciliation_migration[0]
    )
    assert (
        "CREATE INDEX IF NOT EXISTS "
        "candle_reconciliation_replacements_work_unit_lookup_idx" in lookup_migration[0]
    )


def test_reconciliation_schema_preparation_fails_closed_before_other_locks_or_ddl() -> None:
    connection = _Connection(acquired=False)

    with pytest.raises(RuntimeError, match="schema preparation is busy"):
        prepare_reconciliation_schema(connection)  # type: ignore[arg-type]

    assert len(connection.statements) == 1
