"""Versioned PostgreSQL data-layer migrations."""

from importlib.resources import files

from sqlalchemy import Connection, text

RECOVERY_ADVISORY_LOCK_NAME = "market_structure_lab.candle_recovery"
RECONCILIATION_MIGRATION_LOCK_ID = 4_875_179_636_632_247_649


class SchemaPreparationBusyError(RuntimeError):
    """Schema DDL cannot start while publication owns the shared lock domain."""


def candle_recovery_migration_sql() -> str:
    return (
        files("market_structure_lab.data.migrations")
        .joinpath("0001_candle_recovery.sql")
        .read_text(encoding="utf-8")
    )


def candle_reconciliation_migration_sql() -> str:
    return (
        files("market_structure_lab.data.migrations")
        .joinpath("0002_candle_reconciliation.sql")
        .read_text(encoding="utf-8")
    )


def reconciliation_migration_lock_sql() -> str:
    """Serialize idempotent reconciliation DDL across parallel workers."""
    return f"SELECT pg_advisory_xact_lock({RECONCILIATION_MIGRATION_LOCK_ID})"


def prepare_recovery_schema(connection: Connection) -> None:
    """Apply recovery DDL atomically when the publication lock domain is free."""
    _acquire_schema_preparation_locks(connection)
    connection.execute(text(candle_recovery_migration_sql()))


def prepare_reconciliation_schema(connection: Connection) -> None:
    """Apply recovery and reconciliation DDL in one caller-owned transaction."""
    _acquire_schema_preparation_locks(connection)
    connection.execute(text(candle_recovery_migration_sql()))
    connection.execute(text(candle_reconciliation_migration_sql()))


def _acquire_schema_preparation_locks(connection: Connection) -> None:
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
        ).scalar_one()
    )
    if not acquired:
        raise SchemaPreparationBusyError("candle schema preparation is busy")
    connection.execute(text(reconciliation_migration_lock_sql()))


__all__ = [
    "RECOVERY_ADVISORY_LOCK_NAME",
    "SchemaPreparationBusyError",
    "candle_reconciliation_migration_sql",
    "candle_recovery_migration_sql",
    "prepare_reconciliation_schema",
    "prepare_recovery_schema",
    "reconciliation_migration_lock_sql",
]
