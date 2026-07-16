from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.data.gaps import (
    GapRange,
    ProvenanceState,
    build_gap_query,
    build_manifest,
    read_manifest,
    verify_manifest_identity,
    with_provenance_states,
    write_manifest,
)
from market_structure_lab.cli.backfill_candles import compare_manifest_to_reviewed_baseline

DUMP_HASH = "1b6bcb39af41048b53729e9b094f0229163eb6ff6af9563adb666c96f5fd4da4"


@pytest.fixture
def candle_connection():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE candles (symbol TEXT, interval TEXT, open_time BIGINT, "
                "UNIQUE(symbol, interval, open_time))"
            )
        )
        yield connection
    engine.dispose()


def test_gap_query_uses_partitioned_lead_and_compact_half_open_ranges() -> None:
    query = build_gap_query(schema=None)
    assert 'lead("open_time")' in query
    assert 'PARTITION BY "symbol", "interval"' in query
    assert "open_time + 60000 AS start_ms" in query
    assert "next_open_time AS end_ms" in query


def test_gap_query_rejects_untrusted_identifiers() -> None:
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        build_gap_query(table="candles; drop table candles")


def test_manifest_discovers_gaps_without_crossing_markets(candle_connection, tmp_path) -> None:
    candle_connection.execute(
        text("INSERT INTO candles VALUES (:s, :i, :t)"),
        [
            {"s": "BTCUSDT", "i": "1m", "t": 0},
            {"s": "BTCUSDT", "i": "1m", "t": 120_000},
            {"s": "BTCUSDT", "i": "1m", "t": 300_000},
            {"s": "ETHUSDT", "i": "1m", "t": 600_000},
            {"s": "ETHUSDT", "i": "1m", "t": 660_000},
            {"s": "BTCUSDT", "i": "5m", "t": 0},
            {"s": "BTCUSDT", "i": "5m", "t": 600_000},
        ],
    )
    manifest = build_manifest(
        candle_connection,
        dump_sha256=DUMP_HASH,
        mapping_version="v1",
        as_of=datetime(2026, 7, 16, tzinfo=UTC),
        schema=None,
    )
    assert [(gap.start_ms, gap.end_ms, gap.expected_minutes) for gap in manifest.gaps] == [
        (60_000, 120_000, 1),
        (180_000, 300_000, 2),
    ]
    assert {item.symbol for item in manifest.envelopes} == {"BTCUSDT", "ETHUSDT"}
    assert manifest.provenance_validation == {
        "BTCUSDT": ProvenanceState.PENDING,
        "ETHUSDT": ProvenanceState.PENDING,
    }

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_manifest(manifest, first)
    write_manifest(manifest, second)
    assert first.read_bytes() == second.read_bytes()
    assert read_manifest(first) == manifest


def test_manifest_identity_refuses_row_count_dump_or_mapping_drift(candle_connection) -> None:
    candle_connection.execute(text("INSERT INTO candles VALUES ('BTCUSDT', '1m', 0)"))
    manifest = build_manifest(
        candle_connection,
        dump_sha256=DUMP_HASH,
        mapping_version="v1",
        as_of=datetime(2026, 7, 16, tzinfo=UTC),
        schema=None,
    )
    verify_manifest_identity(
        candle_connection,
        manifest,
        dump_sha256=DUMP_HASH.upper(),
        mapping_version="v1",
        schema=None,
    )
    candle_connection.execute(text("INSERT INTO candles VALUES ('BTCUSDT', '1m', 60000)"))
    with pytest.raises(ValueError, match="does not match"):
        verify_manifest_identity(
            candle_connection,
            manifest,
            dump_sha256=DUMP_HASH,
            mapping_version="v1",
            schema=None,
        )


def test_manifest_requires_timezone_and_exact_provenance_coverage(candle_connection) -> None:
    candle_connection.execute(text("INSERT INTO candles VALUES ('BTCUSDT', '1m', 0)"))
    with pytest.raises(ValueError, match="timezone-aware"):
        build_manifest(
            candle_connection,
            dump_sha256=DUMP_HASH,
            mapping_version="v1",
            as_of=datetime(2026, 7, 16),
            schema=None,
        )
    manifest = build_manifest(
        candle_connection,
        dump_sha256=DUMP_HASH,
        mapping_version="v1",
        as_of=datetime(2026, 7, 16, tzinfo=UTC),
        schema=None,
    )
    with pytest.raises(ValueError, match="every manifest symbol"):
        with_provenance_states(manifest, {})


def test_gap_range_validates_expected_half_open_minutes() -> None:
    with pytest.raises(ValueError, match="half-open bounds"):
        GapRange.create("BTCUSDT", "1m", 60_000, 180_000, 1)


def test_plan_reports_reviewed_baseline_mismatches_explicitly(candle_connection) -> None:
    candle_connection.execute(text("INSERT INTO candles VALUES ('BTCUSDT', '1m', 0)"))
    manifest = build_manifest(
        candle_connection,
        dump_sha256=DUMP_HASH,
        mapping_version="v1",
        as_of=datetime(2026, 7, 16, tzinfo=UTC),
        schema=None,
    )
    comparison = compare_manifest_to_reviewed_baseline(manifest)
    assert comparison["matches"] is False
    assert comparison["mismatches"]["symbols"] == {"expected": 25, "actual": 1}
    assert comparison["mismatches"]["source_rows"] == {
        "expected": 35_748_117,
        "actual": 1,
    }
