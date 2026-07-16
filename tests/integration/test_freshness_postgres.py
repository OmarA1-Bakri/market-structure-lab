from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from market_structure_lab.data.freshness import build_freshness_manifest
from market_structure_lab.data.freshness_sync import run_freshness_sync
from market_structure_lab.data.gaps import (
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)
from market_structure_lab.data.migrations import candle_recovery_migration_sql
from market_structure_lab.data.recovery import (
    RECOVERY_ADVISORY_LOCK_NAME,
    RecoveryCandle,
    RecoveryRepository,
)
from market_structure_lab.data.sources.base import (
    FetchBatch,
    FetchRequest,
    SourceKline,
    SourceProvenance,
)


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
                'id bigint PRIMARY KEY, symbol text NOT NULL, "interval" text NOT NULL, '
                "open_time bigint NOT NULL, open double precision NOT NULL, "
                "high double precision NOT NULL, low double precision NOT NULL, "
                "close double precision NOT NULL, volume double precision NOT NULL, "
                "quote_volume double precision, trades integer, "
                "created_at timestamp with time zone NOT NULL DEFAULT now(), "
                'UNIQUE(symbol, "interval", open_time))'
            )
        )
        connection.execute(
            text(
                "INSERT INTO market_data.candles "
                '(id, symbol, "interval", open_time, open, high, low, close, volume) VALUES '
                "(1, 'BTCUSDT', '1m', 0, 10, 11, 9, 10, 1), "
                "(2, 'ETHUSDT', '1m', 0, 20, 21, 19, 20, 2)"
            )
        )
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS market_data CASCADE"))
        engine.dispose()


class FakeSource:
    name = "fake-reviewed-source"

    def __init__(self) -> None:
        self.requests: list[FetchRequest] = []

    def fetch(self, request: FetchRequest) -> Iterator[FetchBatch]:
        self.requests.append(request)
        rows = tuple(
            _source_row(request.symbol, minute)
            for minute in range(request.start_ms, request.end_ms, 60_000)
        )
        yield FetchBatch(
            request=request,
            rows=rows,
            provenance=SourceProvenance(
                source_name=self.name,
                source_revision="fixture-v1",
                location="isolated-test",
                payload_checksum="f" * 64,
                retrieved_at="2026-07-16T12:34:00Z",
            ),
        )


def _source_row(symbol: str, open_time_ms: int) -> SourceKline:
    price = Decimal("10") + Decimal(open_time_ms // 60_000)
    return SourceKline(
        symbol=symbol,
        timeframe="1m",
        open_time_ms=open_time_ms,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=Decimal("1"),
    )


def _compatibility() -> RecoveryManifest:
    identity = SourceIdentity("a" * 64, 2, "market-data-candles-v1")
    return RecoveryManifest(
        manifest_version=1,
        source_identity=identity,
        as_of="1970-01-01T00:05:00Z",
        candidate_venue="binance",
        market_type="spot",
        envelopes=(
            ObservedEnvelope("BTCUSDT", "1m", 0, 0, 1),
            ObservedEnvelope("ETHUSDT", "1m", 0, 0, 1),
        ),
        gaps=(),
        provenance_validation={
            "BTCUSDT": ProvenanceState.COMPATIBLE,
            "ETHUSDT": ProvenanceState.SOURCE_CONFLICT,
        },
    )


def _plan(engine: Engine, compatibility: RecoveryManifest, minute: int):
    with engine.connect() as connection:
        return build_freshness_manifest(
            connection,
            dump_identity=compatibility.source_identity,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=datetime(1970, 1, 1, 0, minute, tzinfo=UTC),
        )


def test_isolated_postgres_freshness_invariants(isolated_postgres: Engine) -> None:
    engine = isolated_postgres
    migration = candle_recovery_migration_sql()
    with engine.begin() as connection:
        connection.execute(text(migration))
        connection.execute(text(migration))

    compatibility = _compatibility()
    source = FakeSource()
    first_plan = _plan(engine, compatibility, 3)
    first_report = run_freshness_sync(engine, first_plan, compatibility, source)

    assert first_report.coverage_conserved is True
    assert first_report.recovered_minutes == 2
    assert first_report.after_missing_minutes == 2
    assert [(request.symbol, request.start_ms, request.end_ms) for request in source.requests] == [
        ("BTCUSDT", 60_000, 180_000)
    ]

    same_plan = _plan(engine, compatibility, 3)
    same_report = run_freshness_sync(engine, same_plan, compatibility, source)
    assert same_report.inserted_rows == 0
    assert len(source.requests) == 1

    later_plan = _plan(engine, compatibility, 5)
    later_report = run_freshness_sync(engine, later_plan, compatibility, source)
    assert later_report.recovered_minutes == 2
    assert source.requests[-1] == FetchRequest("BTCUSDT", "1m", 180_000, 300_000)
    assert all(request.symbol == "BTCUSDT" for request in source.requests)

    assert first_report.recovery_run_id is not None
    with engine.begin() as connection:
        repository = RecoveryRepository(connection)
        preferred = repository.publish_batch(
            run_id=first_report.recovery_run_id,
            rows=(
                RecoveryCandle(
                    "BTCUSDT",
                    "1m",
                    0,
                    Decimal("999"),
                    Decimal("999"),
                    Decimal("999"),
                    Decimal("999"),
                    Decimal("1"),
                ),
            ),
            source_name="fake-reviewed-source",
            source_revision="fixture-v1",
            payload_checksum="e" * 64,
            retrieved_at="2026-07-16T12:34:00Z",
        )
        assert preferred.dump_preferred == 1
        origin, close = connection.execute(
            text(
                "SELECT origin, close FROM market_data.candles_canonical "
                "WHERE symbol='BTCUSDT' AND open_time=0"
            )
        ).one()
        assert (origin, float(close)) == ("dump", 10.0)
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(
                text(
                    "UPDATE market_data.candle_supplements SET volume=2 "
                    "WHERE symbol='BTCUSDT' AND open_time=60000"
                )
            )

    with engine.connect() as lock_connection:
        assert lock_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:name, 0))"),
            {"name": RECOVERY_ADVISORY_LOCK_NAME},
        ).scalar_one()
        try:
            with pytest.raises(ValueError, match="another candle recovery"):
                run_freshness_sync(engine, later_plan, compatibility, source)
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:name, 0))"),
                {"name": RECOVERY_ADVISORY_LOCK_NAME},
            )
