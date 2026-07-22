from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Iterator

import polars as pl
import pytest

from market_structure_lab.core.identity import canonical_json
from market_structure_lab.data.aggregate_bars import source_rows_sha256
from market_structure_lab.data.aggregate_publication import (
    AGGREGATE_MANIFEST_NAME,
    AggregatePublicationManifest,
    publish_aggregate_bars,
    read_aggregate_publication_manifest,
    verify_aggregate_publication,
)
from market_structure_lab.data.export import (
    SnapshotIdentity,
    SnapshotManifest,
    export_partitioned_snapshot,
)
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)


def minute_frame(count: int) -> pl.DataFrame:
    rows = []
    for offset in range(count):
        value = 100.0 + offset
        rows.append(
            {
                "timestamp": datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=offset),
                "symbol": "SOLUSDT",
                "timeframe": "1m",
                "open": value,
                "high": value + 2.0,
                "low": value - 1.0,
                "close": value + 1.0,
                "volume": float(offset + 1),
                "segment_id": 3,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "timestamp": pl.Datetime("us", "UTC"),
            "symbol": pl.String,
            "timeframe": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "segment_id": pl.UInt64,
        },
    )


def rows(frame: pl.DataFrame) -> list[dict[str, object]]:
    return list(frame.iter_rows(named=True))


def demand(frame: pl.DataFrame, aggregate_bars: int) -> ValidationWorkDemand:
    source_rows = rows(frame)
    return ValidationWorkDemand(
        source_rows=frame.height,
        source_bytes=sum(
            len(canonical_json("canonical-source-minute", {"schema_version": 1, **row}))
            for row in source_rows
        ),
        aggregate_bars=aggregate_bars,
    )


def snapshot_identity(*, config_version: str = "config-v1") -> SnapshotIdentity:
    return SnapshotIdentity(
        dataset_version="DS-009999",
        dump_sha256="1" * 64,
        recovery_sha256="2" * 64,
        mapping_version="canonical-v1",
        config_version=config_version,
        code_commit="905181f9abfabf7787d92777e28aeb7a9e307977",
    )


def parent_snapshot(root: Path, frame: pl.DataFrame) -> tuple[Path, SnapshotManifest]:
    source = frame.drop("segment_id")
    manifest = export_partitioned_snapshot(
        [source],
        output_root=root,
        identity=snapshot_identity(),
        expected_row_count=source.height,
    )
    return root / "dataset_version=DS-009999", manifest


def publish(
    batches: list[pl.DataFrame],
    *,
    output_root: Path,
    parent_directory: Path,
    parent_manifest: SnapshotManifest,
    config_version: str = "aggregate-config-v1",
    expected_source_sha256: str | None = None,
    declared: ValidationWorkDemand | None = None,
) -> AggregatePublicationManifest:
    combined = pl.concat(batches)
    return publish_aggregate_bars(
        batches,
        output_root=output_root,
        parent_snapshot_directory=parent_directory,
        parent_snapshot_manifest=parent_manifest,
        symbol="SOLUSDT",
        segment_id=3,
        target_timeframe="1h",
        expected_source_sha256=expected_source_sha256 or source_rows_sha256(rows(combined)),
        config_version=config_version,
        demand=declared or demand(combined, combined.height // 60),
        budget=ValidationWorkBudget(),
        max_rows_per_partition=1,
    )


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def published_directory(root: Path) -> Path:
    return root / "symbol=SOLUSDT" / "timeframe=1h" / "segment=3"


def test_clean_root_publication_is_byte_identical_across_source_chunking(tmp_path: Path) -> None:
    frame = minute_frame(120)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    first = publish(
        [frame],
        output_root=tmp_path / "first",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    second = publish(
        [frame.slice(0, 7), frame.slice(7, 68), frame.slice(75)],
        output_root=tmp_path / "second",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )

    assert first == second
    assert first.parent_snapshot_sha256 == source_manifest.snapshot_sha256
    assert first.parent_snapshot_identity == source_manifest.identity
    assert first.source_row_count == 120
    assert first.aggregate_bar_count == 2
    assert first.symbol == "SOLUSDT"
    assert first.source_timeframe == "1m"
    assert first.target_timeframe == "1h"
    assert first.segment_id == 3
    assert first.continuity == "complete-contiguous-1m-v1"
    assert first.config_version == "aggregate-config-v1"
    assert tree_bytes(tmp_path / "first") == tree_bytes(tmp_path / "second")
    verify_aggregate_publication(published_directory(tmp_path / "first"), first)


def test_publication_identity_changes_with_parent_config_or_row_content(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    first = publish(
        [frame],
        output_root=tmp_path / "first",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    changed_config = publish(
        [frame],
        output_root=tmp_path / "changed-config",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
        config_version="aggregate-config-v2",
    )
    changed_parent = replace(source_manifest, snapshot_sha256="f" * 64)

    assert first.publication_sha256 != changed_config.publication_sha256
    with pytest.raises(ValueError, match="parent snapshot"):
        publish(
            [frame],
            output_root=tmp_path / "changed-parent",
            parent_directory=parent_directory,
            parent_manifest=changed_parent,
        )

    changed = frame.with_columns(
        pl.when(pl.int_range(pl.len()) == 30)
        .then(pl.col("high") + 1.0)
        .otherwise(pl.col("high"))
        .alias("high")
    )
    changed_rows = publish(
        [changed],
        output_root=tmp_path / "changed-row",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    assert first.publication_sha256 != changed_rows.publication_sha256


def test_existing_publication_rejects_without_overwrite(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    before = tree_bytes(tmp_path / "output")

    with pytest.raises(FileExistsError, match="publication"):
        publish(
            [frame],
            output_root=tmp_path / "output",
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert tree_bytes(tmp_path / "output") == before


@pytest.mark.parametrize("mutation", ("serialized-byte", "missing", "extra", "manifest"))
def test_verifier_rejects_corrupt_missing_extra_or_changed_artifacts(
    tmp_path: Path, mutation: str
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    manifest = publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    directory = published_directory(tmp_path / "output")
    if mutation == "serialized-byte":
        target = directory / manifest.partitions[0].path
        payload = bytearray(target.read_bytes())
        payload[len(payload) // 2] ^= 1
        target.write_bytes(payload)
    elif mutation == "missing":
        (directory / manifest.partitions[0].path).unlink()
    elif mutation == "extra":
        (directory / "extra.bin").write_bytes(b"extra")
    else:
        target = directory / AGGREGATE_MANIFEST_NAME
        target.write_bytes(
            target.read_bytes().replace(b"aggregate-config-v1", b"aggregate-config-v9")
        )

    with pytest.raises((RuntimeError, ValueError)):
        verify_aggregate_publication(directory)


def test_verifier_rejects_symlinked_artifact(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    manifest = publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    directory = published_directory(tmp_path / "output")
    partition = directory / manifest.partitions[0].path
    external = tmp_path / "external.parquet"
    partition.replace(external)
    partition.symlink_to(external)

    with pytest.raises(RuntimeError, match="symlink|reparse"):
        verify_aggregate_publication(directory)


def test_oversized_or_invalid_manifest_rejects_before_unbounded_read(tmp_path: Path) -> None:
    directory = tmp_path / "publication"
    directory.mkdir()
    manifest_path = directory / AGGREGATE_MANIFEST_NAME
    manifest_path.write_bytes(b"{" + b" " * (16 * 1024 * 1024 + 1) + b"}")

    with pytest.raises(RuntimeError, match="bounded size"):
        read_aggregate_publication_manifest(manifest_path)


def test_source_digest_mismatch_publishes_no_final_artifact(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    with pytest.raises(ValueError, match="source digest"):
        publish(
            [frame],
            output_root=tmp_path / "output",
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
            expected_source_sha256="f" * 64,
        )
    assert not published_directory(tmp_path / "output").exists()


def test_publication_budget_rejects_before_source_iteration_or_output_creation(
    tmp_path: Path,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    class ExplodingBatches:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self) -> Iterator[pl.DataFrame]:
            self.iterations += 1
            raise AssertionError("publication source must not be iterated")
            yield

    batches = ExplodingBatches()
    with pytest.raises(ValidationWorkBudgetViolation, match="source_rows"):
        publish_aggregate_bars(
            batches,
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=3,
            target_timeframe="1h",
            expected_source_sha256="a" * 64,
            config_version="aggregate-config-v1",
            demand=ValidationWorkDemand(source_rows=2),
            budget=replace(ValidationWorkBudget(), max_source_rows=1),
            max_rows_per_partition=1,
        )
    assert batches.iterations == 0
    assert not (tmp_path / "output").exists()


def test_symlinked_output_root_rejects_before_publication(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink|reparse"):
        publish(
            [frame],
            output_root=linked_output,
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert not any(real_output.iterdir())


def test_manifest_schema_timestamp_numeric_source_order_parent_and_byte_mutations_reject(
    tmp_path: Path,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    manifest = publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )

    with pytest.raises(ValueError, match="schema"):
        replace(manifest, schema_version=2, publication_sha256="")
    for fields in (
        {"min_timestamp": "2025-01-01T00:01:00Z"},
        {"source_row_count": 61},
        {"source_sha256": "e" * 64},
        {"parent_snapshot_sha256": "e" * 64},
    ):
        try:
            changed = replace(manifest, **fields, publication_sha256="")
        except ValueError:
            continue
        assert changed.publication_sha256 != manifest.publication_sha256
