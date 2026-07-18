from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
import pytest

from market_structure_lab.data.migrations import (
    RECOVERY_ADVISORY_LOCK_NAME,
    candle_recovery_migration_sql,
    prepare_recovery_schema,
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


def test_migration_is_versioned_append_only_and_dump_preferred() -> None:
    sql = candle_recovery_migration_sql()
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_batches" in sql
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_supplements" in sql
    assert "candle_supplements_validated_key_unique" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "CREATE OR REPLACE VIEW market_data.candles_canonical" in sql
    assert sql.count("open_time >= 1514764800000") == 2
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


def test_recovery_migration_leaves_transaction_control_to_the_caller() -> None:
    transaction_lines = {
        line.strip().upper() for line in candle_recovery_migration_sql().splitlines()
    }

    assert "BEGIN;" not in transaction_lines
    assert "COMMIT;" not in transaction_lines


def test_recovery_schema_preparation_uses_the_shared_lock_domain_before_ddl() -> None:
    connection = _Connection()

    prepare_recovery_schema(connection)  # type: ignore[arg-type]

    assert len(connection.statements) == 3
    shared_lock, migration_lock, migration = connection.statements
    assert shared_lock == (
        "SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_name, 0))",
        {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
    )
    assert migration_lock[0] == "SELECT pg_advisory_xact_lock(4875179636632247649)"
    assert "CREATE TABLE IF NOT EXISTS market_data.candle_recovery_runs" in migration[0]


def test_recovery_schema_preparation_fails_closed_before_other_locks_or_ddl() -> None:
    connection = _Connection(acquired=False)

    with pytest.raises(RuntimeError, match="schema preparation is busy"):
        prepare_recovery_schema(connection)  # type: ignore[arg-type]

    assert len(connection.statements) == 1
