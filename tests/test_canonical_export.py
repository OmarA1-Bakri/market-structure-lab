from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.data.canonical import CANONICAL_SCHEMA
from market_structure_lab.data.export import (
    SnapshotIdentity,
    export_partitioned_snapshot,
    read_snapshot_manifest,
    verify_snapshot,
)
from market_structure_lab.data.gaps import GapRange
from market_structure_lab.data.segments import (
    SegmentBoundary,
    assign_segment_ids,
    load_canonical_gap_boundaries,
)


def candle_frame(minutes: list[int]) -> pl.DataFrame:
    rows = [
        (
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
            "BTCUSDT",
            "1m",
            100.0 + minute,
            102.0 + minute,
            99.0 + minute,
            101.0 + minute,
            10.0 + minute,
        )
        for minute in minutes
    ]
    return pl.DataFrame(rows, schema=CANONICAL_SCHEMA, orient="row")


def snapshot_identity(version: str = "test-v1") -> SnapshotIdentity:
    return SnapshotIdentity(
        dataset_version=version,
        dump_sha256="a" * 64,
        recovery_sha256="b" * 64,
        mapping_version="candles-v1",
        config_version="config-v1",
        code_commit="0123456789abcdef",
    )


def test_unresolved_gap_creates_hard_segment_boundary() -> None:
    gap = GapRange.create(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=1_704_067_260_000,
        end_ms=1_704_067_380_000,
        expected_minutes=2,
    )
    boundary = SegmentBoundary.from_gap_range(
        gap,
        reason="source_unavailable",
    )
    segmented = assign_segment_ids(candle_frame([0, 3, 4]), [boundary])

    assert segmented["segment_id"].to_list() == [0, 1, 1]
    with pytest.raises(ValueError, match="inside unresolved gap"):
        assign_segment_ids(candle_frame([0, 2, 3]), [boundary])


def test_partially_recovered_gap_uses_exact_remaining_canonical_boundary() -> None:
    engine = create_engine("sqlite://")
    base_ms = 1_704_067_200_000
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE candles_canonical ("
                "symbol TEXT NOT NULL, interval TEXT NOT NULL, open_time INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candles_canonical (symbol, interval, open_time) "
                "VALUES (:symbol, :timeframe, :open_time)"
            ),
            [
                {"symbol": "BTCUSDT", "timeframe": "1m", "open_time": base_ms + minute * 60_000}
                for minute in (0, 1, 3, 4)
            ],
        )
        boundaries = load_canonical_gap_boundaries(
            connection,
            symbol="BTCUSDT",
            timeframe="1m",
            schema=None,
        )

    assert len(boundaries) == 1
    assert boundaries[0].start == datetime(2024, 1, 1, 0, 2, tzinfo=UTC)
    assert boundaries[0].end == datetime(2024, 1, 1, 0, 3, tzinfo=UTC)
    assert boundaries[0].reason == "canonical_missing_candles:1"
    segmented = assign_segment_ids(candle_frame([0, 1, 3, 4]), boundaries)
    assert segmented["segment_id"].to_list() == [0, 0, 1, 1]
    with pytest.raises(ValueError, match="inside unresolved gap"):
        assign_segment_ids(candle_frame([2]), boundaries)


def test_export_is_partitioned_atomic_and_deterministic(tmp_path: Path) -> None:
    identity = snapshot_identity()
    batches = [candle_frame([0, 1]), candle_frame([1_440, 1_441])]
    first = export_partitioned_snapshot(
        batches,
        output_root=tmp_path / "first",
        identity=identity,
    )
    second = export_partitioned_snapshot(
        batches,
        output_root=tmp_path / "second",
        identity=identity,
    )
    published = tmp_path / "first" / "dataset_version=test-v1"

    assert first == second
    assert first.row_count == 4
    assert len(first.partitions) == 2
    assert not (tmp_path / "first" / ".dataset_version=test-v1.partial").exists()
    assert (published / "_SUCCESS").is_file()
    assert read_snapshot_manifest(published / "manifest.json") == first
    verify_snapshot(published)


def test_export_resumes_an_interrupted_partial_snapshot(tmp_path: Path) -> None:
    identity = snapshot_identity("resume-v1")

    def interrupted() -> Iterator[pl.DataFrame]:
        yield candle_frame([0, 1])
        yield candle_frame([1_440])
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        export_partitioned_snapshot(interrupted(), output_root=tmp_path, identity=identity)

    staging = tmp_path / ".dataset_version=resume-v1.partial"
    assert len(list(staging.rglob("*.parquet"))) == 1
    with pytest.raises(FileExistsError, match="different pinned identity"):
        export_partitioned_snapshot(
            [candle_frame([0, 1])],
            output_root=tmp_path,
            identity=replace(identity, recovery_sha256="c" * 64),
        )

    manifest = export_partitioned_snapshot(
        [candle_frame([0, 1]), candle_frame([1_440])],
        output_root=tmp_path,
        identity=identity,
    )

    assert manifest.row_count == 3
    assert not staging.exists()
    verify_snapshot(tmp_path / "dataset_version=resume-v1")


def test_completed_export_is_idempotent_and_detects_tampering(tmp_path: Path) -> None:
    identity = snapshot_identity("idempotent-v1")
    first = export_partitioned_snapshot(
        [candle_frame([0])], output_root=tmp_path, identity=identity
    )
    second = export_partitioned_snapshot([], output_root=tmp_path, identity=identity)
    assert second == first

    partition = tmp_path / "dataset_version=idempotent-v1" / first.partitions[0].path
    partition.write_bytes(partition.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        export_partitioned_snapshot([], output_root=tmp_path, identity=identity)


def test_export_rejects_duplicate_keys_across_batches(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate candle across export batches"):
        export_partitioned_snapshot(
            [candle_frame([0]), candle_frame([0])],
            output_root=tmp_path,
            identity=snapshot_identity("duplicate-v1"),
        )


def test_retained_legacy_snapshot_hash_remains_verifiable() -> None:
    retained = Path("data/exports/snapshots/dataset_version=phase0-xrpusdt-20180505-v1")
    if not retained.is_dir():
        pytest.skip("retained Phase 0 snapshot is not available")

    verify_snapshot(retained)
