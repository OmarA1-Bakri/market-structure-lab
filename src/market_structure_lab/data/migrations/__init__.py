"""Versioned PostgreSQL data-layer migrations."""

from importlib.resources import files

RECONCILIATION_MIGRATION_LOCK_ID = 4_875_179_636_632_247_649


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


__all__ = [
    "candle_reconciliation_migration_sql",
    "candle_recovery_migration_sql",
    "reconciliation_migration_lock_sql",
]
