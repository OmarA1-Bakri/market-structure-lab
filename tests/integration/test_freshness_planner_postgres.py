from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, event, text

from market_structure_lab.data.freshness import build_freshness_manifest
from market_structure_lab.data.gaps import (
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)
from market_structure_lab.data.migrations import candle_recovery_migration_sql

BASE_OPEN_TIME_MS = 1_514_764_800_000


@pytest.fixture
def isolated_postgres() -> Iterator[Engine]:
    url = os.environ.get("MSL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set MSL_TEST_POSTGRES_URL to an isolated disposable PostgreSQL database")
    engine = create_engine(url)
    if "test" not in str(engine.url.database).lower():
        engine.dispose()
        pytest.fail("MSL_TEST_POSTGRES_URL database name must contain 'test'")
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS market_data CASCADE"))
        connection.execute(text("CREATE SCHEMA market_data"))
        connection.execute(
            text(
                "CREATE TABLE market_data.candles ("
                "id bigint PRIMARY KEY, symbol text NOT NULL, "
                '"interval" text NOT NULL, open_time bigint NOT NULL, '
                "open double precision NOT NULL, high double precision NOT NULL, "
                "low double precision NOT NULL, close double precision NOT NULL, "
                "volume double precision NOT NULL, quote_volume double precision, "
                "trades integer, created_at timestamp with time zone NOT NULL DEFAULT now(), "
                "CONSTRAINT candles_symbol_interval_open_time_key "
                'UNIQUE(symbol, "interval", open_time))'
            )
        )
        connection.execute(
            text(
                "INSERT INTO market_data.candles "
                '(id, symbol, "interval", open_time, open, high, low, close, volume) VALUES '
                "(1, 'BTCUSDT', '1m', :base, 10, 11, 9, 10, 1), "
                "(2, 'BTCUSDT', '1m', :btc_two, 12, 13, 11, 12, 1), "
                "(3, 'ETHUSDT', '1m', :base, 20, 21, 19, 20, 2), "
                "(4, 'ETHUSDT', '1m', :eth_two, 21, 22, 20, 21, 2)"
            ),
            {
                "base": BASE_OPEN_TIME_MS,
                "btc_two": BASE_OPEN_TIME_MS + 120_000,
                "eth_two": BASE_OPEN_TIME_MS + 60_000,
            },
        )
        connection.execute(text(candle_recovery_migration_sql()))
        connection.execute(
            text(
                "INSERT INTO market_data.candle_recovery_runs ("
                "run_id, manifest_sha256, dump_sha256, source_row_count, mapping_version, "
                "recovery_as_of, status"
                ") VALUES ("
                "'00000000-0000-0000-0000-000000000001', :manifest_sha, :dump_sha, "
                "4, 'fixture-v1', now(), 'completed'"
                ")"
            ),
            {"manifest_sha": "b" * 64, "dump_sha": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO market_data.candle_supplements ("
                "run_id, source_name, source_revision, symbol, "
                '"interval", open_time, open, high, low, close, volume, retrieved_at, '
                "payload_checksum, row_checksum, validation_status"
                ") VALUES "
                "('00000000-0000-0000-0000-000000000001', 'fixture', 'v1', "
                "'BTCUSDT', '1m', :btc_one, 11, 12, 10, 11, 1, now(), :payload, :row1, "
                "'validated'), "
                "('00000000-0000-0000-0000-000000000001', 'fixture', 'v1', "
                "'BTCUSDT', '1m', :btc_two, 999, 999, 999, 999, 1, now(), :payload, :row2, "
                "'validated'), "
                "('00000000-0000-0000-0000-000000000001', 'fixture', 'v1', "
                "'BTCUSDT', '1m', :btc_four, 14, 15, 13, 14, 1, now(), :payload, :row3, "
                "'validated'), "
                "('00000000-0000-0000-0000-000000000001', 'fixture', 'v1', "
                "'ETHUSDT', '1m', :eth_three, 23, 24, 22, 23, 1, now(), :payload, :row4, "
                "'validated')"
            ),
            {
                "btc_one": BASE_OPEN_TIME_MS + 60_000,
                "btc_two": BASE_OPEN_TIME_MS + 120_000,
                "btc_four": BASE_OPEN_TIME_MS + 240_000,
                "eth_three": BASE_OPEN_TIME_MS + 180_000,
                "payload": "c" * 64,
                "row1": "1" * 64,
                "row2": "2" * 64,
                "row3": "3" * 64,
                "row4": "4" * 64,
            },
        )
        connection.execute(
            text(
                "CREATE VIEW market_data.candles_canonical_reference AS "
                "SELECT * FROM market_data.candles_canonical"
            )
        )
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS market_data CASCADE"))
        engine.dispose()


def _compatibility() -> RecoveryManifest:
    identity = SourceIdentity("a" * 64, 4, "fixture-v1")
    return RecoveryManifest(
        manifest_version=1,
        source_identity=identity,
        as_of="2018-01-01T00:06:00Z",
        candidate_venue="binance",
        market_type="spot",
        envelopes=(
            ObservedEnvelope("BTCUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 120_000, 2),
            ObservedEnvelope("ETHUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 60_000, 2),
        ),
        gaps=(),
        provenance_validation={
            "BTCUSDT": ProvenanceState.COMPATIBLE,
            "ETHUSDT": ProvenanceState.SOURCE_CONFLICT,
        },
    )


def _plan(
    connection: Connection,
    compatibility: RecoveryManifest,
    *,
    table: str = "candles_canonical",
):
    return build_freshness_manifest(
        connection,
        dump_identity=compatibility.source_identity,
        compatibility_manifest=compatibility,
        compatibility_manifest_sha256=compatibility.sha256(),
        as_of=datetime(2018, 1, 1, 0, 6, tzinfo=UTC),
        table=table,
    )


def _plan_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.get("Plans", ()):
            yield from _plan_nodes(child)


def test_default_postgres_key_stream_matches_generic_manifest_and_uses_indexes(
    isolated_postgres: Engine,
) -> None:
    compatibility = _compatibility()
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(isolated_postgres, "before_cursor_execute", capture_statement)
    try:
        with isolated_postgres.connect() as connection:
            optimized = _plan(connection, compatibility)
    finally:
        event.remove(isolated_postgres, "before_cursor_execute", capture_statement)

    with isolated_postgres.connect() as connection:
        reference = _plan(
            connection,
            compatibility,
            table="candles_canonical_reference",
        )

    assert optimized.canonical_bytes() == reference.canonical_bytes()
    key_stream_queries = [
        statement for statement in statements if "WITH canonical_key AS" in statement
    ]
    assert len(key_stream_queries) == 4
    assert all("market_data.candles_canonical" not in query for query in key_stream_queries)
    assert all("NOT EXISTS" in query for query in key_stream_queries)
    assert all("SELECT *" not in query for query in key_stream_queries)
    assert all("lead(" not in query.lower() for query in key_stream_queries)
    assert all("first_value(" not in query.lower() for query in key_stream_queries)
    assert "SET LOCAL enable_seqscan = off" in statements
    assert "SET LOCAL enable_bitmapscan = off" in statements
    assert "SET LOCAL enable_hashjoin = off" in statements
    assert "SET LOCAL max_parallel_workers_per_gather = 0" in statements

    with isolated_postgres.begin() as connection:
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        connection.execute(text("SET LOCAL enable_bitmapscan = off"))
        connection.execute(text("SET LOCAL enable_hashjoin = off"))
        connection.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
        explained = connection.execute(
            text(
                """
EXPLAIN (COSTS OFF, FORMAT JSON)
WITH canonical_key AS (
    (
        SELECT candle.open_time
        FROM market_data.candles AS candle
        WHERE candle.symbol = 'BTCUSDT'
          AND candle."interval" = '1m'
        ORDER BY candle.open_time
    )
    UNION ALL
    (
        SELECT supplement.open_time
        FROM market_data.candle_supplements AS supplement
        WHERE supplement.symbol = 'BTCUSDT'
          AND supplement."interval" = '1m'
          AND supplement.validation_status = 'validated'
          AND NOT EXISTS (
              SELECT 1
              FROM market_data.candles AS candle
              WHERE candle.symbol = supplement.symbol
                AND candle."interval" = supplement."interval"
                AND candle.open_time = supplement.open_time
          )
        ORDER BY supplement.open_time
    )
),
ordered AS (
    SELECT
        open_time,
        lag(open_time) OVER (ORDER BY open_time) AS previous_open_time
    FROM canonical_key
)
SELECT open_time
FROM ordered
WHERE open_time > previous_open_time + 60000
ORDER BY open_time
"""
            )
        ).scalar_one()

    nodes = tuple(_plan_nodes(explained[0]["Plan"]))
    index_names = {str(node["Index Name"]) for node in nodes if "Index Name" in node}
    assert "candles_symbol_interval_open_time_key" in index_names
    assert "candle_supplements_validated_key_unique" in index_names
    assert not any(node.get("Node Type") == "Sort" for node in nodes)
    assert not any(node.get("Node Type") == "Seq Scan" for node in nodes)


def test_default_postgres_key_stream_rejects_unreviewed_canonical_symbol(
    isolated_postgres: Engine,
) -> None:
    compatibility = _compatibility()
    with isolated_postgres.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO market_data.candles "
                '(id, symbol, "interval", open_time, open, high, low, close, volume) '
                "VALUES (5, 'ADAUSDT', '1m', :open_time, 1, 1, 1, 1, 1)"
            ),
            {"open_time": BASE_OPEN_TIME_MS},
        )

    with isolated_postgres.connect() as connection:
        with pytest.raises(ValueError, match="cover canonical symbols exactly"):
            _plan(connection, compatibility)
