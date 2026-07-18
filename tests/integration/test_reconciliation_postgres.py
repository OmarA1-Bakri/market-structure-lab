from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from market_structure_lab.data.migrations import (
    RECOVERY_ADVISORY_LOCK_NAME,
    SchemaPreparationBusyError,
    candle_reconciliation_migration_sql,
    candle_recovery_migration_sql,
    prepare_reconciliation_schema,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationReplacement,
    ReconciliationRepository,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    VerifiedCoverageInterval,
    freeze_reconciliation_run,
    publish_work_unit,
)

BASE_OPEN_TIME_MS = 1_514_764_800_000


@pytest.fixture
def reconciliation_postgres() -> Iterator[Engine]:
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
                '(id, symbol, "interval", open_time, open, high, low, close, volume, '
                "quote_volume, trades) VALUES "
                "(1, 'BTCUSDT', '1m', :first, 10, 11, 9, 10, 1, 10, 1), "
                "(2, 'BTCUSDT', '1m', :second, 20, 21, 19, 20, 2, 40, 2), "
                "(3, 'BTCUSDT', '1m', :fourth, 40, 41, 39, 40, 4, 160, 4)"
            ),
            {
                "first": BASE_OPEN_TIME_MS,
                "second": BASE_OPEN_TIME_MS + 60_000,
                "fourth": BASE_OPEN_TIME_MS + 180_000,
            },
        )
        connection.execute(text(candle_recovery_migration_sql()))
        connection.execute(text(candle_reconciliation_migration_sql()))
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS market_data CASCADE"))
        engine.dispose()


def _sha(character: str) -> str:
    return character * 64


def _run():
    unit = ReconciliationWorkUnit.create(
        "BTCUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 180_000
    )
    return (
        freeze_reconciliation_run(
            run_id="RR-000001",
            cutoff=datetime(2026, 7, 16, tzinfo=UTC),
            dump_sha256=_sha("a"),
            source_row_count=3,
            mapping_version="mapping-v1",
            candidate_venue="binance",
            market_type="spot",
            source_revision="binance-public-data-v1",
            algorithm_version="row-reconciliation-v1",
            code_commit="aedd375",
            uv_lock_sha256=_sha("b"),
            envelopes=(
                TradingEnvelope("BTCUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 180_000),
            ),
            work_units=(unit,),
        ),
        unit,
    )


def _ledger(tmp_path: Path):
    run, unit = _run()
    correction_checksum = RecoveryCandle(
        "BTCUSDT",
        "1m",
        BASE_OPEN_TIME_MS + 60_000,
        Decimal("30"),
        Decimal("31"),
        Decimal("29"),
        Decimal("30"),
        Decimal("3"),
        Decimal("90"),
        3,
    ).row_checksum()
    fill_checksum = RecoveryCandle(
        "BTCUSDT",
        "1m",
        BASE_OPEN_TIME_MS + 120_000,
        Decimal("35"),
        Decimal("36"),
        Decimal("34"),
        Decimal("35"),
        Decimal("3.5"),
        Decimal("122.5"),
        4,
    ).row_checksum()
    records = (
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            BASE_OPEN_TIME_MS,
            ReconciliationClass.EXACT_MATCH,
            _sha("1"),
            _sha("1"),
            (),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            BASE_OPEN_TIME_MS + 60_000,
            ReconciliationClass.BINANCE_CORRECTION,
            _sha("2"),
            correction_checksum,
            ("open", "high", "low", "close", "volume", "quote_volume", "trades"),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            BASE_OPEN_TIME_MS + 120_000,
            ReconciliationClass.BINANCE_FILL,
            None,
            fill_checksum,
            (),
        ),
    )
    manifest = publish_work_unit(
        records,
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(
            SourceArtifactIdentity(
                "fixture",
                _sha("c"),
                _sha("c"),
                "fixture-v1",
                "2026-07-16T00:00:00Z",
            ),
        ),
    )
    replacements = (
        ReconciliationReplacement(
            run_id=run.run_id,
            work_unit_id=unit.work_unit_id,
            symbol="BTCUSDT",
            timeframe="1m",
            open_time_ms=BASE_OPEN_TIME_MS + 60_000,
            classification=ReconciliationClass.BINANCE_CORRECTION,
            open=Decimal("30"),
            high=Decimal("31"),
            low=Decimal("29"),
            close=Decimal("30"),
            volume=Decimal("3"),
            quote_volume=Decimal("90"),
            trades=3,
            source_name="binance_spot",
            source_revision="fixture-v1",
            payload_sha256=_sha("c"),
            binance_row_sha256=correction_checksum,
            retrieved_at="2026-07-16T00:00:00Z",
        ),
        ReconciliationReplacement(
            run_id=run.run_id,
            work_unit_id=unit.work_unit_id,
            symbol="BTCUSDT",
            timeframe="1m",
            open_time_ms=BASE_OPEN_TIME_MS + 120_000,
            classification=ReconciliationClass.BINANCE_FILL,
            open=Decimal("35"),
            high=Decimal("36"),
            low=Decimal("34"),
            close=Decimal("35"),
            volume=Decimal("3.5"),
            quote_volume=Decimal("122.5"),
            trades=4,
            source_name="binance_spot",
            source_revision="fixture-v1",
            payload_sha256=_sha("c"),
            binance_row_sha256=fill_checksum,
            retrieved_at="2026-07-16T00:00:00Z",
        ),
    )
    return run, manifest, replacements


def test_schema_preparation_fails_closed_while_publication_is_open_then_replays(
    reconciliation_postgres: Engine,
    tmp_path: Path,
) -> None:
    run, manifest, replacements = _ledger(tmp_path)
    with reconciliation_postgres.begin() as setup:
        setup.execute(text("DROP VIEW market_data.candles_reconciled"))

    publication_connection = reconciliation_postgres.connect()
    schema_connection = reconciliation_postgres.connect()
    publication = publication_connection.begin()
    try:
        publication_connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
        )
        repository = ReconciliationRepository(publication_connection)
        repository.register_run(run)
        assert repository.publish_work_unit(manifest, replacements) == 2

        with pytest.raises(SchemaPreparationBusyError, match="schema preparation is busy"):
            with schema_connection.begin():
                prepare_reconciliation_schema(schema_connection)

        with schema_connection.begin():
            assert (
                schema_connection.execute(
                    text("SELECT to_regclass('market_data.candles_reconciled') IS NOT NULL")
                ).scalar_one()
                is False
            )

        publication.commit()

        with schema_connection.begin():
            prepare_reconciliation_schema(schema_connection)
            assert (
                schema_connection.execute(
                    text("SELECT to_regclass('market_data.candles_reconciled') IS NOT NULL")
                ).scalar_one()
                is True
            )
        with schema_connection.begin():
            prepare_reconciliation_schema(schema_connection)
            assert schema_connection.execute(
                text("SELECT count(*) FROM market_data.candle_reconciliation_replacements")
            ).scalar_one() == len(replacements)
    finally:
        if publication.is_active:
            publication.rollback()
        schema_connection.close()
        publication_connection.close()


def test_work_unit_lookup_supports_replacement_count_without_sequential_scan(
    reconciliation_postgres: Engine,
    tmp_path: Path,
) -> None:
    run, manifest, replacements = _ledger(tmp_path)
    with reconciliation_postgres.begin() as connection:
        prepare_reconciliation_schema(connection)
        repository = ReconciliationRepository(connection)
        repository.register_run(run)
        assert repository.publish_work_unit(manifest, replacements) == 2
        index_definitions = (
            connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname='market_data' "
                    "AND tablename='candle_reconciliation_replacements' "
                    "AND indexname='candle_reconciliation_replacements_work_unit_lookup_idx'"
                )
            )
            .scalars()
            .all()
        )

        assert index_definitions == [
            "CREATE INDEX candle_reconciliation_replacements_work_unit_lookup_idx ON "
            "market_data.candle_reconciliation_replacements USING btree "
            "(run_id, work_unit_id)"
        ]

        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.execute(
            text(
                "EXPLAIN (FORMAT JSON) "
                "SELECT count(*) "
                "FROM market_data.candle_reconciliation_replacements "
                "WHERE run_id=:run_id AND work_unit_id=:work_unit_id"
            ),
            {"run_id": run.run_id, "work_unit_id": manifest.work_unit_id},
        ).scalar_one()[0]["Plan"]

    nodes = [plan]
    for node in nodes:
        nodes.extend(node.get("Plans", ()))
    assert any(
        node.get("Index Name") == "candle_reconciliation_replacements_work_unit_lookup_idx"
        for node in nodes
    )
    assert all(node["Node Type"] not in {"Seq Scan", "Gather", "Gather Merge"} for node in nodes)


def test_promoted_view_uses_verified_dump_correction_and_fill_only(
    reconciliation_postgres: Engine,
    tmp_path: Path,
) -> None:
    run, manifest, replacements = _ledger(tmp_path)
    replacement_digest = hashlib.sha256()
    for replacement in replacements:
        replacement_digest.update(
            json.dumps(
                {
                    "binance_row_sha256": replacement.binance_row_sha256,
                    "open_time_ms": replacement.open_time_ms,
                    "symbol": replacement.symbol,
                    "timeframe": replacement.timeframe,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    candidate_replacement_logical_sha256 = replacement_digest.hexdigest()
    with reconciliation_postgres.begin() as connection:
        repository = ReconciliationRepository(connection)
        repository.register_run(run)
        assert repository.publish_work_unit(manifest, replacements) == 2
        promotion = repository.promote(
            run,
            (manifest,),
            (
                VerifiedCoverageInterval(
                    "BTCUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 180_000
                ),
            ),
            candidate_replacement_logical_sha256=candidate_replacement_logical_sha256,
        )
        replay = repository.promote(
            run,
            (manifest,),
            (
                VerifiedCoverageInterval(
                    "BTCUSDT", "1m", BASE_OPEN_TIME_MS, BASE_OPEN_TIME_MS + 180_000
                ),
            ),
            candidate_replacement_logical_sha256=candidate_replacement_logical_sha256,
        )
        rows = connection.execute(
            text(
                "SELECT open_time, origin, close FROM market_data.candles_reconciled "
                "ORDER BY open_time"
            )
        ).all()

    assert promotion == replay
    assert [(row.open_time, row.origin, float(row.close)) for row in rows] == [
        (BASE_OPEN_TIME_MS, "dump_verified_match", 10.0),
        (BASE_OPEN_TIME_MS + 60_000, "binance_correction", 30.0),
        (BASE_OPEN_TIME_MS + 120_000, "binance_fill", 35.0),
    ]
    assert len(promotion.canonical_logical_sha256) == 64


def test_reconciliation_tables_reject_mutation(
    reconciliation_postgres: Engine,
    tmp_path: Path,
) -> None:
    run, manifest, replacements = _ledger(tmp_path)
    with reconciliation_postgres.begin() as connection:
        repository = ReconciliationRepository(connection)
        repository.register_run(run)
        repository.publish_work_unit(manifest, replacements)

    with pytest.raises(DBAPIError, match="append-only"):
        with reconciliation_postgres.begin() as connection:
            connection.execute(
                text(
                    "UPDATE market_data.candle_reconciliation_replacements "
                    "SET close=999 WHERE run_id='RR-000001'"
                )
            )
