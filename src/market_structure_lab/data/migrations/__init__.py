"""Versioned PostgreSQL data-layer migrations."""

from importlib.resources import files


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


__all__ = ["candle_reconciliation_migration_sql", "candle_recovery_migration_sql"]
