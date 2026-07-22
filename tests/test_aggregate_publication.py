from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from collections.abc import Iterator
from contextlib import contextmanager
import os

import polars as pl
import pytest
import market_structure_lab.data.aggregate_publication as aggregate_publication_module

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
                "segment_id": 0,
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


def publication_demand(
    frame: pl.DataFrame,
    aggregate_bars: int,
    *,
    artifacts: int | None = None,
    artifact_bytes: int = 1024 * 1024,
) -> ValidationWorkDemand:
    base = demand(frame, aggregate_bars)
    return replace(
        base,
        artifacts=aggregate_bars if artifacts is None else artifacts,
        artifact_bytes=artifact_bytes,
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
        segment_id=0,
        target_timeframe="1h",
        expected_source_sha256=expected_source_sha256 or source_rows_sha256(rows(combined)),
        config_version=config_version,
        demand=declared or publication_demand(combined, combined.height // 60),
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
    return root / "symbol=SOLUSDT" / "timeframe=1h" / "segment=0"


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
    assert first.segment_id == 0
    assert first.continuity == "complete-contiguous-1m-v1"
    assert first.config_version == "aggregate-config-v1"
    assert first.parent_partition_bindings == tuple(
        (partition.path, partition.sha256) for partition in source_manifest.partitions
    )
    assert len(first.parent_source_selection_sha256) == 64
    assert first.artifact_scope == "aggregate-parquet-partitions-v1"
    assert first.declared_artifact_count == first.actual_artifact_count == 2
    assert first.actual_artifact_bytes == sum(
        (published_directory(tmp_path / "first") / partition.path).stat().st_size
        for partition in first.partitions
    )
    assert 0 < first.actual_artifact_bytes <= first.declared_artifact_bytes
    assert tree_bytes(tmp_path / "first") == tree_bytes(tmp_path / "second")
    verify_aggregate_publication(published_directory(tmp_path / "first"), first)


def test_default_partition_row_bound_is_valid_and_never_exceeds_memory_cap(
    tmp_path: Path,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    manifest = publish_aggregate_bars(
        [frame],
        output_root=tmp_path / "output",
        parent_snapshot_directory=parent_directory,
        parent_snapshot_manifest=source_manifest,
        symbol="SOLUSDT",
        segment_id=0,
        target_timeframe="1h",
        expected_source_sha256=source_rows_sha256(rows(frame)),
        config_version="aggregate-config-v1",
        demand=publication_demand(frame, 1, artifacts=1),
        budget=ValidationWorkBudget(),
    )

    assert manifest.max_rows_per_partition == 256
    assert all(partition.row_count <= 256 for partition in manifest.partitions)
    verify_aggregate_publication(published_directory(tmp_path / "output"), manifest)


def test_publication_identity_changes_with_config_and_rejects_non_parent_row_content(
    tmp_path: Path,
) -> None:
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
    with pytest.raises(ValueError, match="parent source selection"):
        publish(
            [changed],
            output_root=tmp_path / "changed-row",
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert not (tmp_path / "changed-row").exists()


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


@pytest.mark.parametrize(
    "mutation",
    ("schema-string", "numeric-string", "whitespace", "crlf"),
)
def test_manifest_must_be_exact_canonical_json_bytes(tmp_path: Path, mutation: str) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )
    directory = published_directory(tmp_path / "output")
    manifest_path = directory / AGGREGATE_MANIFEST_NAME
    payload = json.loads(manifest_path.read_bytes())
    if mutation == "schema-string":
        payload["schema_version"] = "1"
        changed = json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    elif mutation == "numeric-string":
        payload["source_row_count"] = "60"
        changed = json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    elif mutation == "whitespace":
        changed = manifest_path.read_text(encoding="utf-8") + "\n"
    else:
        changed = manifest_path.read_text(encoding="utf-8").replace("\n", "\r\n")
    manifest_path.write_bytes(changed.encode("utf-8"))

    with pytest.raises(ValueError, match="canonical|manifest"):
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


@pytest.mark.parametrize("mutation", ("numeric", "arbitrary", "reordered"))
def test_publication_rejects_rows_not_proven_by_parent_snapshot(
    tmp_path: Path, mutation: str
) -> None:
    parent_frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", parent_frame)
    if mutation == "numeric":
        supplied = parent_frame.with_columns(
            pl.when(pl.int_range(pl.len()) == 30)
            .then(pl.col("high") + 1.0)
            .otherwise(pl.col("high"))
            .alias("high")
        )
    elif mutation == "arbitrary":
        supplied = parent_frame.with_columns(
            (pl.col("open") + 1_000.0).alias("open"),
            (pl.col("high") + 1_000.0).alias("high"),
            (pl.col("low") + 1_000.0).alias("low"),
            (pl.col("close") + 1_000.0).alias("close"),
        )
    else:
        supplied = pl.concat(
            (
                parent_frame.slice(0, 20),
                parent_frame.slice(21, 1),
                parent_frame.slice(20, 1),
                parent_frame.slice(22),
            )
        )

    with pytest.raises(ValueError, match="parent source selection"):
        publish(
            [supplied],
            output_root=tmp_path / "output",
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert not (tmp_path / "output").exists()


def test_parent_digest_cannot_authorize_changed_batches_or_leave_output(tmp_path: Path) -> None:
    parent_frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", parent_frame)
    changed = parent_frame.with_columns(
        pl.when(pl.int_range(pl.len()) == 30)
        .then(pl.col("high") + 1.0)
        .otherwise(pl.col("high"))
        .alias("high")
    )

    with pytest.raises(ValueError, match="source digest"):
        publish_aggregate_bars(
            [changed],
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=0,
            target_timeframe="1h",
            expected_source_sha256=source_rows_sha256(rows(parent_frame)),
            config_version="aggregate-config-v1",
            demand=publication_demand(parent_frame, 1),
            budget=ValidationWorkBudget(),
            max_rows_per_partition=1,
        )
    assert not (tmp_path / "output").exists()


def test_publication_rejects_segment_not_present_in_parent_snapshot(tmp_path: Path) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    foreign_segment = frame.with_columns(pl.lit(3, dtype=pl.UInt64).alias("segment_id"))

    with pytest.raises(ValueError, match="parent source selection|segment"):
        publish_aggregate_bars(
            [foreign_segment],
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=3,
            target_timeframe="1h",
            expected_source_sha256=source_rows_sha256(rows(foreign_segment)),
            config_version="aggregate-config-v1",
            demand=publication_demand(foreign_segment, 1),
            budget=ValidationWorkBudget(),
            max_rows_per_partition=1,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "underdeclared",
    (
        "source_rows",
        "artifacts",
        "artifact_bytes",
        "artifact_count_limit",
        "artifact_byte_limit",
    ),
)
def test_publication_budget_rejects_before_source_iteration_or_output_creation(
    tmp_path: Path, underdeclared: str
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
    declared = publication_demand(frame, 1)
    budget = ValidationWorkBudget()
    if underdeclared == "source_rows":
        declared = replace(declared, source_rows=2)
        budget = replace(budget, max_source_rows=1)
    elif underdeclared == "artifacts":
        declared = replace(declared, artifacts=0)
    elif underdeclared == "artifact_bytes":
        declared = replace(declared, artifact_bytes=12 * 1024)
    elif underdeclared == "artifact_count_limit":
        budget = replace(budget, max_artifacts=0)
    else:
        budget = replace(budget, max_artifact_bytes=1)
    expected_stage = {
        "artifact_count_limit": "artifacts",
        "artifact_byte_limit": "artifact_bytes",
    }.get(underdeclared, underdeclared)
    with pytest.raises((ValidationWorkBudgetViolation, ValueError), match=expected_stage):
        publish_aggregate_bars(
            batches,
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=0,
            target_timeframe="1h",
            expected_source_sha256=source_rows_sha256(rows(frame)),
            config_version="aggregate-config-v1",
            demand=declared,
            budget=budget,
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


@pytest.mark.parametrize("race", ("swap", "symlink"))
def test_parent_partition_is_parsed_from_the_same_verified_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    original_read = aggregate_publication_module.read_bounded_regular
    raced = False

    def read_then_race(path: Path, maximum: int) -> bytes:
        nonlocal raced
        content = original_read(path, maximum)
        if path.name == "part.parquet" and not raced:
            raced = True
            external = tmp_path / "raced-parent.parquet"
            if race == "swap":
                changed = frame.drop("segment_id").with_columns(
                    (pl.col("high") + 10.0).alias("high")
                )
                changed = changed.with_columns(pl.lit(0, dtype=pl.UInt64).alias("segment_id"))
                changed.write_parquet(external)
                external.replace(path)
            else:
                path.replace(external)
                path.symlink_to(external)
        return content

    monkeypatch.setattr(aggregate_publication_module, "read_bounded_regular", read_then_race)
    manifest = publish(
        [frame],
        output_root=tmp_path / "output",
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )

    assert raced is True
    verify_aggregate_publication(published_directory(tmp_path / "output"), manifest)


def test_concurrently_claimed_empty_final_directory_is_never_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    output_root = tmp_path / "output"
    final = published_directory(output_root)
    original_verify = aggregate_publication_module.verify_aggregate_publication
    injected = False

    def verify_then_claim(directory: Path, manifest=None) -> None:
        nonlocal injected
        original_verify(directory, manifest)
        if Path(directory).name.endswith(".partial") and not injected:
            injected = True
            final.mkdir(parents=True)

    monkeypatch.setattr(
        aggregate_publication_module,
        "verify_aggregate_publication",
        verify_then_claim,
    )
    with pytest.raises(FileExistsError, match="claim|exists|publication"):
        publish(
            [frame],
            output_root=output_root,
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert injected is True
    assert final.is_dir()
    assert not any(final.iterdir())


def test_symbol_ancestor_symlink_race_cannot_escape_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    output_root = tmp_path / "output"
    external = tmp_path / "external"
    external.mkdir()
    original_verify = aggregate_publication_module.verify_aggregate_publication
    injected = False

    def verify_then_link(directory: Path, manifest=None) -> None:
        nonlocal injected
        original_verify(directory, manifest)
        if Path(directory).name.endswith(".partial") and not injected:
            injected = True
            (output_root / "symbol=SOLUSDT").symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(
        aggregate_publication_module,
        "verify_aggregate_publication",
        verify_then_link,
    )
    with pytest.raises((FileExistsError, RuntimeError, OSError)):
        publish(
            [frame],
            output_root=output_root,
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )
    assert injected is True
    assert not any(external.iterdir())


def test_windows_publication_branch_uses_exclusive_handle_claim_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    output_root = tmp_path / "output"

    class FakeClaim:
        def __init__(self, root: Path) -> None:
            self.root = root
            self.directories: set[str] = set()

        def create_regular_descriptor(self, relative_path: str | Path) -> int:
            destination = self.root / relative_path
            if destination.parent != self.root:
                destination.parent.mkdir(exist_ok=False)
                self.directories.add(destination.parent.relative_to(self.root).as_posix())
            return os.open(
                destination,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )

        def unlink_regular(self, relative_path: str | Path) -> None:
            (self.root / relative_path).unlink()

        @property
        def created_directories(self) -> tuple[str, ...]:
            return tuple(sorted(self.directories))

    class FakeWindowsFilesystem:
        def __init__(self) -> None:
            self.claims = 0

        @contextmanager
        def claim_exclusive_directory(
            self,
            root: Path,
            ancestors: tuple[str, ...],
            final_name: str,
        ):
            self.claims += 1
            final = root.joinpath(*ancestors, final_name)
            final.mkdir(parents=True, exist_ok=False)
            yield FakeClaim(final)

    filesystem = FakeWindowsFilesystem()
    monkeypatch.setattr(aggregate_publication_module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        aggregate_publication_module,
        "WindowsHandleFilesystem",
        lambda: filesystem,
    )
    manifest = publish(
        [frame],
        output_root=output_root,
        parent_directory=parent_directory,
        parent_manifest=source_manifest,
    )

    assert filesystem.claims == 1
    verify_aggregate_publication(published_directory(output_root), manifest)


def test_partial_final_claim_is_terminal_and_retry_never_overwrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = minute_frame(120)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)
    output_root = tmp_path / "output"
    final = published_directory(output_root)
    original_write = aggregate_publication_module._write_exclusive_at
    original_windows_write = aggregate_publication_module._write_exclusive_windows
    writes = 0

    def write_then_fail(parent_fd: int, name: str, content: bytes) -> None:
        nonlocal writes
        if writes == 1:
            raise OSError("injected publication copy failure")
        original_write(parent_fd, name, content)
        writes += 1

    def windows_write_then_fail(claim, name: str, content: bytes) -> None:
        nonlocal writes
        if writes == 1:
            raise OSError("injected publication copy failure")
        original_windows_write(claim, name, content)
        writes += 1

    if os.name == "nt":
        monkeypatch.setattr(
            aggregate_publication_module,
            "_write_exclusive_windows",
            windows_write_then_fail,
        )
    else:
        monkeypatch.setattr(aggregate_publication_module, "_write_exclusive_at", write_then_fail)
    with pytest.raises(OSError, match="injected"):
        publish(
            [frame],
            output_root=output_root,
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )

    assert final.is_dir()
    assert not (final / "_SUCCESS").exists()
    assert (final / "_FAILED.json").is_file()
    failed_verifier = getattr(
        aggregate_publication_module,
        "verify_failed_aggregate_publication",
        None,
    )
    assert callable(failed_verifier)
    failure = failed_verifier(final)
    assert failure.publication_sha256
    assert failure.cause_class == "OSError"
    assert failure.retry_semantics == "terminal-claim-no-retry"
    assert failure.written_artifacts
    anonymous = final / "anonymous"
    anonymous.mkdir()
    with pytest.raises(ValueError, match="anonymous directories"):
        failed_verifier(final)
    anonymous.rmdir()
    monkeypatch.setattr(aggregate_publication_module, "_write_exclusive_at", original_write)
    monkeypatch.setattr(
        aggregate_publication_module,
        "_write_exclusive_windows",
        original_windows_write,
    )
    with pytest.raises(FileExistsError, match="publication"):
        publish(
            [frame],
            output_root=output_root,
            parent_directory=parent_directory,
            parent_manifest=source_manifest,
        )


def test_partition_row_buffer_is_explicitly_capped_before_source_iteration(
    tmp_path: Path,
) -> None:
    frame = minute_frame(60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    class ExplodingBatches:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self) -> Iterator[pl.DataFrame]:
            self.iterations += 1
            raise AssertionError("source must not be iterated")
            yield

    batches = ExplodingBatches()
    with pytest.raises(ValueError, match="memory-bounded"):
        publish_aggregate_bars(
            batches,
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=0,
            target_timeframe="1h",
            expected_source_sha256=source_rows_sha256(rows(frame)),
            config_version="aggregate-config-v1",
            demand=publication_demand(frame, 1),
            budget=ValidationWorkBudget(),
            max_rows_per_partition=257,
        )
    assert batches.iterations == 0
    assert not (tmp_path / "output").exists()


def test_old_artifact_byte_floor_rejects_before_large_source_iteration_or_output(
    tmp_path: Path,
) -> None:
    frame = minute_frame(256 * 60)
    parent_directory, source_manifest = parent_snapshot(tmp_path / "source", frame)

    class ExplodingBatches:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self) -> Iterator[pl.DataFrame]:
            self.iterations += 1
            raise AssertionError("underdeclared publication must not iterate caller batches")
            yield

    batches = ExplodingBatches()
    declared = publication_demand(
        frame,
        256,
        artifacts=1,
        artifact_bytes=81_920,
    )
    with pytest.raises(ValueError, match="artifact_bytes.*upper envelope"):
        publish_aggregate_bars(
            batches,
            output_root=tmp_path / "output",
            parent_snapshot_directory=parent_directory,
            parent_snapshot_manifest=source_manifest,
            symbol="SOLUSDT",
            segment_id=0,
            target_timeframe="1h",
            expected_source_sha256=source_rows_sha256(rows(frame)),
            config_version="aggregate-config-v1",
            demand=declared,
            budget=ValidationWorkBudget(),
            max_rows_per_partition=256,
        )
    assert batches.iterations == 0
    assert not (tmp_path / "output").exists()


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
        {"actual_artifact_bytes": 1},
        {"declared_artifact_bytes": 2},
    ):
        try:
            changed = replace(manifest, **fields, publication_sha256="")
        except ValueError:
            continue
        assert changed.publication_sha256 != manifest.publication_sha256
