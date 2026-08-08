from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.data.validation_source_v2 import _EXACT_POSTGRESQL_MINUTE_SQL


def test_exact_validation_source_sql_executes_against_disposable_integer_schema() -> None:
    url = os.environ.get("MSL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set MSL_TEST_POSTGRES_URL to an isolated disposable PostgreSQL database")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text("CREATE SCHEMA market_data"))
        columns = """
            source_row_id bigint, symbol text, "interval" text, open_time bigint,
            open numeric(38,18), high numeric(38,18), low numeric(38,18),
            close numeric(38,18), volume numeric(38,18), quote_volume numeric(38,18),
            trades bigint, created_at timestamptz, origin text, source_name text,
            payload_checksum text, recovery_run_id text, reconciliation_run_id text
        """
        connection.execute(text(f"CREATE TABLE market_data.candles_reconciled ({columns})"))
        connection.execute(
            text(
                """
                CREATE TABLE market_data.candle_reconciliation_replacements (
                    run_id text, symbol text, "interval" text, open_time bigint,
                    open numeric(38,18), high numeric(38,18), low numeric(38,18),
                    close numeric(38,18), volume numeric(38,18),
                    quote_volume numeric(38,18), trades bigint
                )
                """
            )
        )
        rows = connection.execute(
            _EXACT_POSTGRESQL_MINUTE_SQL,
            {"symbol": "BTCUSDT", "start_ms": 0, "end_ms": 0},
        ).all()
        assert rows == []
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
