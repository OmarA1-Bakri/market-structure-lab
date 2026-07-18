from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from market_structure_lab.cli import backfill_candles
from market_structure_lab.data.migrations import SchemaPreparationBusyError


def _raise(error: Exception):
    def fail(_args) -> int:
        raise error

    return fail


def test_database_errors_do_not_leak_sql_parameters_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _DriverError(Exception):
        sqlstate = "40P01"

    error = OperationalError(
        "INSERT INTO secret_table VALUES (:secret)",
        {"secret": "sentinel-password", "close": "999.123"},
        _DriverError("driver leaked postgresql://user:secret@host/database"),
    )
    monkeypatch.setattr(backfill_candles, "_fetch", _raise(error))

    exit_code = backfill_candles.main(["fetch", "--manifest", "manifest.json"])

    assert exit_code == 1
    assert capsys.readouterr().err == "database operation failed (SQLSTATE 40P01)\n"


def test_schema_contention_is_reported_concisely(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        backfill_candles,
        "_fetch",
        _raise(SchemaPreparationBusyError("sentinel detail must not be printed")),
    )

    exit_code = backfill_candles.main(["fetch", "--manifest", "manifest.json"])

    assert exit_code == 1
    assert capsys.readouterr().err == "candle schema preparation is busy\n"


def test_unexpected_runtime_errors_are_reported_without_internal_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        backfill_candles,
        "_fetch",
        _raise(RuntimeError("sentinel postgresql://user:secret@host/database")),
    )

    exit_code = backfill_candles.main(["fetch", "--manifest", "manifest.json"])

    assert exit_code == 1
    assert capsys.readouterr().err == "candle recovery operation failed\n"
