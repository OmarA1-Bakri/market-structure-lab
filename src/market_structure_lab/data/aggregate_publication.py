"""Immutable bounded publication for complete canonical aggregate bars."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import TemporaryDirectory
from typing import Any, Final, cast

import polars as pl

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    regular_file_matches,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.data.aggregate_bars import (
    CONTINUITY_ID,
    CanonicalAggregateBar,
    OrderedSourceIdentity,
    canonical_source_row_identity,
    canonical_source_row_payload,
    iter_complete_aggregate_bars,
    spool_complete_aggregate_bars,
    target_timeframe_minutes,
)
from market_structure_lab.data.canonical import CANONICAL_SCHEMA, validate_candle_frame
from market_structure_lab.data.export import (
    PartitionRecord,
    SnapshotIdentity,
    SnapshotManifest,
    read_snapshot_manifest,
    verify_snapshot,
)
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)

AGGREGATE_MANIFEST_NAME: Final = "manifest.json"
AGGREGATE_SUCCESS_NAME: Final = "_SUCCESS"
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_PUBLICATION_ENTRIES = 2_000_010
_MAX_PARENT_PARTITION_BYTES = 64 * 1024 * 1024
_MAX_PARENT_PARTITION_ROWS = 2_000
MAX_AGGREGATE_ROWS_PER_PARTITION: Final = 256
_MAX_IDENTITY_COMPONENT_BYTES = 128
# The publication schema has eight 64-bit scalar fields, nine bounded text fields,
# one list offset, and bounded per-row definition/validity metadata. Text offsets
# use eight bytes here even though Arrow commonly uses four, keeping admission
# independent of the writer's internal offset width.
_PARQUET_SCALAR_BYTES = 8
_PARQUET_TEXT_OFFSET_BYTES = 8
_MAX_TIMESTAMP_TEXT_BYTES = 32
_MAX_TIMEFRAME_TEXT_BYTES = 8
_MAX_CONTINUITY_TEXT_BYTES = 64
_SHA256_TEXT_BYTES = 64
_PARQUET_ROW_METADATA_UPPER_BYTES = 512
_FIXED_AGGREGATE_PARQUET_ROW_UPPER_BYTES = (
    8 * _PARQUET_SCALAR_BYTES
    + 2 * _MAX_TIMESTAMP_TEXT_BYTES
    + _MAX_IDENTITY_COMPONENT_BYTES
    + 2 * _MAX_TIMEFRAME_TEXT_BYTES
    + _MAX_CONTINUITY_TEXT_BYTES
    + 3 * _SHA256_TEXT_BYTES
    + 9 * _PARQUET_TEXT_OFFSET_BYTES
    + _PARQUET_TEXT_OFFSET_BYTES
    + _PARQUET_ROW_METADATA_UPPER_BYTES
)
_SOURCE_ID_PARQUET_UPPER_BYTES = _SHA256_TEXT_BYTES + _PARQUET_TEXT_OFFSET_BYTES
# One bounded row group is written per partition. This covers Parquet magic,
# page/column headers, statistics, encodings, and footer metadata independently
# of compression effectiveness.
_PARQUET_PARTITION_OVERHEAD_UPPER_BYTES = 256 * 1024
_ARTIFACT_SCOPE = "aggregate-parquet-partitions-v1"
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AggregateSourceSelectionReceipt:
    """Manifest-backed identity of every selected canonical parent minute."""

    parent_snapshot_sha256: str
    symbol: str
    source_timeframe: str
    segment_id: int
    source_row_count: int
    source_bytes: int
    source_sha256: str
    source_min_timestamp: str
    source_max_timestamp: str
    parent_partition_bindings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_sha256(self.parent_snapshot_sha256, "parent snapshot sha256")
        _require_sha256(self.source_sha256, "source sha256")
        if _SAFE_COMPONENT.fullmatch(self.symbol) is None:
            raise ValueError("parent source selection symbol is invalid")
        _require_bounded_component(self.symbol, "parent source selection symbol")
        if self.source_timeframe != "1m":
            raise ValueError("parent source selection timeframe must be 1m")
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("parent source selection segment_id must be non-negative")
        if self.source_row_count < 1 or self.source_bytes < 1:
            raise ValueError("parent source selection counts must be positive")
        minimum = _parse_utc(self.source_min_timestamp)
        maximum = _parse_utc(self.source_max_timestamp)
        if maximum != minimum + timedelta(minutes=self.source_row_count - 1):
            raise ValueError("parent source selection must be contiguous")
        paths = tuple(path for path, _ in self.parent_partition_bindings)
        if not paths or paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("parent source partitions must be non-empty and uniquely ordered")
        for path, sha256 in self.parent_partition_bindings:
            if not path or Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("parent source partition path is invalid")
            _require_sha256(sha256, "parent source partition sha256")

    @property
    def sha256(self) -> str:
        return hash_json("aggregate-parent-source-selection", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "segment_id": self.segment_id,
            "source_row_count": self.source_row_count,
            "source_bytes": self.source_bytes,
            "source_sha256": self.source_sha256,
            "source_min_timestamp": self.source_min_timestamp,
            "source_max_timestamp": self.source_max_timestamp,
            "parent_partition_bindings": [
                {"path": path, "sha256": sha256} for path, sha256 in self.parent_partition_bindings
            ],
        }


@dataclass(frozen=True, slots=True)
class AggregatePublicationManifest:
    """Complete immutable provenance and artifact identity for one aggregate stream."""

    schema_version: int
    parent_snapshot_identity: SnapshotIdentity
    parent_snapshot_sha256: str
    symbol: str
    source_timeframe: str
    target_timeframe: str
    segment_id: int
    continuity: str
    config_version: str
    work_budget_sha256: str
    work_demand_sha256: str
    parent_source_selection_sha256: str
    parent_partition_bindings: tuple[tuple[str, str], ...]
    source_row_count: int
    source_bytes: int
    source_sha256: str
    source_min_timestamp: str
    source_max_timestamp: str
    aggregate_bar_count: int
    min_timestamp: str
    max_timestamp: str
    artifact_scope: str
    max_rows_per_partition: int
    artifact_count_limit: int
    artifact_byte_limit: int
    declared_artifact_count: int
    declared_artifact_bytes: int
    actual_artifact_count: int
    actual_artifact_bytes: int
    partitions: tuple[PartitionRecord, ...]
    publication_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported aggregate publication schema")
        _require_sha256(self.parent_snapshot_sha256, "parent snapshot sha256")
        _require_sha256(self.work_budget_sha256, "work budget sha256")
        _require_sha256(self.work_demand_sha256, "work demand sha256")
        _require_sha256(
            self.parent_source_selection_sha256,
            "parent source selection sha256",
        )
        _require_sha256(self.source_sha256, "source sha256")
        if _SAFE_COMPONENT.fullmatch(self.symbol) is None:
            raise ValueError("aggregate publication symbol must be a safe component")
        _require_bounded_component(self.symbol, "aggregate publication symbol")
        if self.source_timeframe != "1m":
            raise ValueError("aggregate publication source timeframe must be 1m")
        target_minutes = target_timeframe_minutes(self.target_timeframe)
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("aggregate publication segment_id must be non-negative")
        if self.continuity != CONTINUITY_ID:
            raise ValueError("aggregate publication continuity identity is unsupported")
        if _SAFE_COMPONENT.fullmatch(self.config_version) is None:
            raise ValueError("aggregate config_version must be a safe identity component")
        _require_bounded_component(self.config_version, "aggregate config_version")
        if self.aggregate_bar_count < 1:
            raise ValueError("aggregate publication must contain at least one complete bar")
        if self.source_bytes < 1:
            raise ValueError("aggregate publication source_bytes must be positive")
        if self.source_row_count != self.aggregate_bar_count * target_minutes:
            raise ValueError("aggregate publication source count is not complete")
        binding_paths = tuple(path for path, _ in self.parent_partition_bindings)
        if (
            not binding_paths
            or binding_paths != tuple(sorted(binding_paths))
            or len(binding_paths) != len(set(binding_paths))
        ):
            raise ValueError("parent partition bindings must be non-empty and uniquely ordered")
        for path, sha256 in self.parent_partition_bindings:
            if not path or Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("parent partition binding path is invalid")
            _require_sha256(sha256, "parent partition binding sha256")
        source_minimum = _parse_utc(self.source_min_timestamp)
        source_maximum = _parse_utc(self.source_max_timestamp)
        if source_maximum != source_minimum + timedelta(minutes=self.source_row_count - 1):
            raise ValueError("aggregate parent source selection is not contiguous")
        if self.artifact_scope != _ARTIFACT_SCOPE:
            raise ValueError("aggregate artifact scope is unsupported")
        if (
            isinstance(self.max_rows_per_partition, bool)
            or self.max_rows_per_partition < 1
            or self.max_rows_per_partition > MAX_AGGREGATE_ROWS_PER_PARTITION
        ):
            raise ValueError("aggregate partition row bound is invalid")
        paths = tuple(partition.path for partition in self.partitions)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("aggregate publication partitions must be uniquely ordered")
        if sum(partition.row_count for partition in self.partitions) != self.aggregate_bar_count:
            raise ValueError("aggregate partition row counts do not match aggregate count")
        if any(partition.row_count > self.max_rows_per_partition for partition in self.partitions):
            raise ValueError("aggregate partition exceeds the declared row buffer bound")
        expected_artifact_count = math.ceil(self.aggregate_bar_count / self.max_rows_per_partition)
        if self.artifact_count_limit < 1 or self.artifact_byte_limit < 1:
            raise ValueError("aggregate artifact limits must be positive")
        if self.declared_artifact_count != expected_artifact_count:
            raise ValueError("declared artifacts do not match deterministic partition count")
        if self.declared_artifact_count > self.artifact_count_limit:
            raise ValueError("declared artifacts exceed the frozen artifact count limit")
        if self.actual_artifact_count != len(self.partitions):
            raise ValueError("actual artifacts do not match aggregate partitions")
        if self.actual_artifact_count > self.declared_artifact_count:
            raise ValueError("actual artifacts exceed the declared artifact count")
        artifact_byte_upper = _conservative_artifact_byte_upper(
            self.aggregate_bar_count,
            expected_artifact_count,
            target_minutes,
        )
        if self.declared_artifact_bytes < artifact_byte_upper:
            raise ValueError("declared artifact_bytes are below the conservative upper envelope")
        if self.declared_artifact_bytes > self.artifact_byte_limit:
            raise ValueError("declared artifact_bytes exceed the frozen artifact byte limit")
        if self.actual_artifact_bytes < 1:
            raise ValueError("actual artifact_bytes must be positive")
        if self.actual_artifact_bytes > self.declared_artifact_bytes:
            raise ValueError("actual artifact_bytes exceed the declared artifact_bytes")
        expected_selection_sha256 = hash_json(
            "aggregate-parent-source-selection",
            self.source_selection_dict(),
        )
        if self.parent_source_selection_sha256 != expected_selection_sha256:
            raise ValueError("parent source selection identity mismatch")
        minimum = _parse_utc(self.min_timestamp)
        maximum = _parse_utc(self.max_timestamp)
        if maximum != minimum + timedelta(minutes=target_minutes * (self.aggregate_bar_count - 1)):
            raise ValueError("aggregate publication timestamp range is not contiguous")
        expected = hash_json("aggregate-publication", self.logical_dict())
        if self.publication_sha256 and self.publication_sha256 != expected:
            raise ValueError("aggregate publication identity mismatch")
        object.__setattr__(self, "publication_sha256", expected)

    def logical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "parent_snapshot_identity": self.parent_snapshot_identity.to_dict(),
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "target_timeframe": self.target_timeframe,
            "segment_id": self.segment_id,
            "continuity": self.continuity,
            "config_version": self.config_version,
            "work_budget_sha256": self.work_budget_sha256,
            "work_demand_sha256": self.work_demand_sha256,
            "parent_source_selection_sha256": self.parent_source_selection_sha256,
            "parent_partition_bindings": [
                {"path": path, "sha256": sha256} for path, sha256 in self.parent_partition_bindings
            ],
            "source_row_count": self.source_row_count,
            "source_bytes": self.source_bytes,
            "source_sha256": self.source_sha256,
            "source_min_timestamp": self.source_min_timestamp,
            "source_max_timestamp": self.source_max_timestamp,
            "aggregate_bar_count": self.aggregate_bar_count,
            "min_timestamp": self.min_timestamp,
            "max_timestamp": self.max_timestamp,
            "artifact_scope": self.artifact_scope,
            "max_rows_per_partition": self.max_rows_per_partition,
            "artifact_count_limit": self.artifact_count_limit,
            "artifact_byte_limit": self.artifact_byte_limit,
            "declared_artifact_count": self.declared_artifact_count,
            "declared_artifact_bytes": self.declared_artifact_bytes,
            "actual_artifact_count": self.actual_artifact_count,
            "actual_artifact_bytes": self.actual_artifact_bytes,
            "partitions": [asdict(partition) for partition in self.partitions],
        }

    def source_selection_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "segment_id": self.segment_id,
            "source_row_count": self.source_row_count,
            "source_bytes": self.source_bytes,
            "source_sha256": self.source_sha256,
            "source_min_timestamp": self.source_min_timestamp,
            "source_max_timestamp": self.source_max_timestamp,
            "parent_partition_bindings": [
                {"path": path, "sha256": sha256} for path, sha256 in self.parent_partition_bindings
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.logical_dict(), "publication_sha256": self.publication_sha256}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def publish_aggregate_bars(
    batches: Iterable[pl.DataFrame],
    *,
    output_root: str | Path,
    parent_snapshot_directory: str | Path,
    parent_snapshot_manifest: SnapshotManifest,
    symbol: str,
    segment_id: int,
    target_timeframe: str,
    expected_source_sha256: str,
    config_version: str,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
    max_rows_per_partition: int = MAX_AGGREGATE_ROWS_PER_PARTITION,
) -> AggregatePublicationManifest:
    """Publish one exact aggregate stream atomically without materialising all bars."""

    if _SAFE_COMPONENT.fullmatch(symbol) is None:
        raise ValueError("symbol must be a safe path component")
    _require_bounded_component(symbol, "symbol")
    if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 0:
        raise ValueError("segment_id must be a non-negative integer")
    target_timeframe_minutes(target_timeframe)
    if _SAFE_COMPONENT.fullmatch(config_version) is None:
        raise ValueError("config_version must be a safe identity component")
    _require_bounded_component(config_version, "config_version")
    if not isinstance(demand, ValidationWorkDemand):
        raise TypeError("demand must be a ValidationWorkDemand")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be a ValidationWorkBudget")
    if (
        isinstance(max_rows_per_partition, bool)
        or not isinstance(max_rows_per_partition, int)
        or max_rows_per_partition < 1
        or max_rows_per_partition > MAX_AGGREGATE_ROWS_PER_PARTITION
        or max_rows_per_partition > budget.max_aggregate_bars
    ):
        raise ValueError(
            "max_rows_per_partition must be positive, explicitly memory-bounded, and budget-bounded"
        )
    _require_sha256(expected_source_sha256, "expected source sha256")
    if not isinstance(parent_snapshot_manifest, SnapshotManifest):
        raise TypeError("parent_snapshot_manifest must be a SnapshotManifest")
    budget.preflight(demand, deferred_work=batches)

    parent_directory = Path(parent_snapshot_directory)
    recorded_parent = read_snapshot_manifest(parent_directory / "manifest.json")
    if recorded_parent != parent_snapshot_manifest:
        raise ValueError("parent snapshot manifest does not match the recorded snapshot")
    verify_snapshot(parent_directory, recorded_parent)
    source_selection = _build_parent_source_selection(
        parent_directory,
        recorded_parent,
        symbol=symbol,
        segment_id=segment_id,
        target_timeframe=target_timeframe,
        demand=demand,
        budget=budget,
    )
    if expected_source_sha256 != source_selection.source_sha256:
        raise ValueError("source digest does not match the verified parent source selection")
    _require_exact_demand("source_rows", demand.source_rows, source_selection.source_row_count)
    _require_exact_demand("source_bytes", demand.source_bytes, source_selection.source_bytes)
    expected_aggregate_bars = source_selection.source_row_count // target_timeframe_minutes(
        target_timeframe
    )
    _require_exact_demand(
        "aggregate_bars",
        demand.aggregate_bars,
        expected_aggregate_bars,
    )
    expected_artifacts = math.ceil(expected_aggregate_bars / max_rows_per_partition)
    _require_exact_demand("artifacts", demand.artifacts, expected_artifacts)
    artifact_byte_upper = _conservative_artifact_byte_upper(
        expected_aggregate_bars,
        expected_artifacts,
        target_timeframe_minutes(target_timeframe),
    )
    if demand.artifact_bytes < artifact_byte_upper:
        raise ValueError(
            "artifact_bytes declaration is below the conservative aggregate partition upper envelope"
        )

    spool_directory = TemporaryDirectory(prefix="market-structure-lab-aggregate-")
    try:
        spool = spool_complete_aggregate_bars(
            batches,
            spool_path=Path(spool_directory.name) / "bars.jsonl",
            target_timeframe=target_timeframe,
            expected_source_sha256=expected_source_sha256,
            parent_snapshot_sha256=parent_snapshot_manifest.snapshot_sha256,
            demand=demand,
            budget=budget,
        )
        root = Path(output_root)
        created_root = False
        if path_exists_no_follow(root):
            require_regular_directory(root)
        else:
            root.mkdir(parents=True)
            created_root = True
            require_regular_directory(root)
        final = (
            root
            / f"symbol={symbol}"
            / f"timeframe={target_timeframe}"
            / f"segment={segment_id}"
        )
        if path_exists_no_follow(final):
            raise FileExistsError("aggregate publication already exists")
        staging = root / f".{symbol}-{target_timeframe}-{segment_id}.partial"
        if path_exists_no_follow(staging):
            raise FileExistsError("incomplete aggregate publication already exists")
        staging.mkdir()
    except Exception:
        spool_directory.cleanup()
        raise

    records: list[PartitionRecord] = []
    buffered: list[CanonicalAggregateBar] = []
    source_row_count = 0
    aggregate_count = 0
    minimum: datetime | None = None
    maximum: datetime | None = None

    def flush() -> None:
        nonlocal buffered
        if not buffered:
            return
        part_index = len(records)
        relative = Path(f"chunk={part_index:06d}") / "part.parquet"
        destination = staging / relative
        destination.parent.mkdir()
        temporary = destination.with_name(f".{destination.name}.tmp")
        frame = pl.DataFrame(
            [item.to_dict() for item in buffered],
            schema=_aggregate_frame_schema(),
        )
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        os.replace(temporary, destination)
        records.append(
            PartitionRecord(
                path=relative.as_posix(),
                sha256=sha256_regular(destination),
                row_count=len(buffered),
                min_timestamp=_iso_utc(buffered[0].timestamp),
                max_timestamp=_iso_utc(buffered[-1].timestamp),
            )
        )
        buffered = []

    try:
        for bar in iter_complete_aggregate_bars(spool):
            if bar.symbol != symbol or bar.segment_id != segment_id:
                raise ValueError("aggregate row does not match publication symbol or segment")
            buffered.append(bar)
            source_row_count += bar.source_row_count
            aggregate_count += 1
            minimum = bar.timestamp if minimum is None else minimum
            maximum = bar.timestamp
            if len(buffered) == max_rows_per_partition:
                flush()
        flush()
        if minimum is None or maximum is None:
            raise ValueError("aggregate publication requires at least one complete bar")
        actual_artifact_count, actual_artifact_bytes = _partition_artifact_metrics(
            staging,
            tuple(records),
        )
        if actual_artifact_count > demand.artifacts:
            raise ValidationWorkBudgetViolation(
                "artifacts",
                actual_artifact_count,
                demand.artifacts,
            )
        if actual_artifact_bytes > demand.artifact_bytes:
            raise ValidationWorkBudgetViolation(
                "artifact_bytes",
                actual_artifact_bytes,
                demand.artifact_bytes,
            )
        if actual_artifact_count > budget.max_artifacts:
            raise ValidationWorkBudgetViolation(
                "artifacts",
                actual_artifact_count,
                budget.max_artifacts,
            )
        if actual_artifact_bytes > budget.max_artifact_bytes:
            raise ValidationWorkBudgetViolation(
                "artifact_bytes",
                actual_artifact_bytes,
                budget.max_artifact_bytes,
            )
        manifest = AggregatePublicationManifest(
            schema_version=1,
            parent_snapshot_identity=parent_snapshot_manifest.identity,
            parent_snapshot_sha256=parent_snapshot_manifest.snapshot_sha256,
            symbol=symbol,
            source_timeframe="1m",
            target_timeframe=target_timeframe,
            segment_id=segment_id,
            continuity=CONTINUITY_ID,
            config_version=config_version,
            work_budget_sha256=budget.sha256,
            work_demand_sha256=hash_json("validation-work-demand", asdict(demand)),
            parent_source_selection_sha256=source_selection.sha256,
            parent_partition_bindings=source_selection.parent_partition_bindings,
            source_row_count=source_row_count,
            source_bytes=source_selection.source_bytes,
            source_sha256=expected_source_sha256,
            source_min_timestamp=source_selection.source_min_timestamp,
            source_max_timestamp=source_selection.source_max_timestamp,
            aggregate_bar_count=aggregate_count,
            min_timestamp=_iso_utc(minimum),
            max_timestamp=_iso_utc(maximum),
            artifact_scope=_ARTIFACT_SCOPE,
            max_rows_per_partition=max_rows_per_partition,
            artifact_count_limit=budget.max_artifacts,
            artifact_byte_limit=budget.max_artifact_bytes,
            declared_artifact_count=demand.artifacts,
            declared_artifact_bytes=demand.artifact_bytes,
            actual_artifact_count=actual_artifact_count,
            actual_artifact_bytes=actual_artifact_bytes,
            partitions=tuple(records),
        )
        _atomic_write(staging / AGGREGATE_MANIFEST_NAME, manifest.to_json().encode("utf-8"))
        _atomic_write(
            staging / AGGREGATE_SUCCESS_NAME,
            f"{manifest.publication_sha256}\n".encode("utf-8"),
        )
        verify_aggregate_publication(staging, manifest)
        _publish_staged_no_clobber(
            staging,
            root,
            symbol=symbol,
            target_timeframe=target_timeframe,
            segment_id=segment_id,
            manifest=manifest,
        )
        verify_aggregate_publication(final, manifest)
        require_regular_directory(staging)
        shutil.rmtree(staging)
        spool_directory.cleanup()
        return manifest
    except Exception:
        if path_exists_no_follow(staging):
            require_regular_directory(staging)
            shutil.rmtree(staging)
        if created_root:
            try:
                root.rmdir()
            except OSError:
                pass
        spool_directory.cleanup()
        raise


def read_aggregate_publication_manifest(
    path: str | Path,
) -> AggregatePublicationManifest:
    """Read and reconstruct one bounded aggregate publication manifest."""

    try:
        recorded_bytes = read_bounded_regular(Path(path), _MAX_MANIFEST_BYTES)
        payload = json.loads(recorded_bytes)
        expected_keys = {
            "schema_version",
            "parent_snapshot_identity",
            "parent_snapshot_sha256",
            "symbol",
            "source_timeframe",
            "target_timeframe",
            "segment_id",
            "continuity",
            "config_version",
            "work_budget_sha256",
            "work_demand_sha256",
            "parent_source_selection_sha256",
            "parent_partition_bindings",
            "source_row_count",
            "source_bytes",
            "source_sha256",
            "source_min_timestamp",
            "source_max_timestamp",
            "aggregate_bar_count",
            "min_timestamp",
            "max_timestamp",
            "artifact_scope",
            "max_rows_per_partition",
            "artifact_count_limit",
            "artifact_byte_limit",
            "declared_artifact_count",
            "declared_artifact_bytes",
            "actual_artifact_count",
            "actual_artifact_bytes",
            "partitions",
            "publication_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("aggregate manifest fields are invalid")
        partitions_raw = payload["partitions"]
        if not isinstance(partitions_raw, list):
            raise ValueError("aggregate manifest partitions are invalid")
        if len(partitions_raw) > _MAX_PUBLICATION_ENTRIES:
            raise ValueError("aggregate manifest partition count exceeds its bound")
        bindings_raw = payload["parent_partition_bindings"]
        if not isinstance(bindings_raw, list) or any(
            not isinstance(item, dict) or set(item) != {"path", "sha256"} for item in bindings_raw
        ):
            raise ValueError("aggregate parent partition bindings are invalid")
        manifest = AggregatePublicationManifest(
            schema_version=int(payload["schema_version"]),
            parent_snapshot_identity=SnapshotIdentity(**payload["parent_snapshot_identity"]),
            parent_snapshot_sha256=str(payload["parent_snapshot_sha256"]),
            symbol=str(payload["symbol"]),
            source_timeframe=str(payload["source_timeframe"]),
            target_timeframe=str(payload["target_timeframe"]),
            segment_id=int(payload["segment_id"]),
            continuity=str(payload["continuity"]),
            config_version=str(payload["config_version"]),
            work_budget_sha256=str(payload["work_budget_sha256"]),
            work_demand_sha256=str(payload["work_demand_sha256"]),
            parent_source_selection_sha256=str(payload["parent_source_selection_sha256"]),
            parent_partition_bindings=tuple(
                (str(item["path"]), str(item["sha256"])) for item in bindings_raw
            ),
            source_row_count=int(payload["source_row_count"]),
            source_bytes=int(payload["source_bytes"]),
            source_sha256=str(payload["source_sha256"]),
            source_min_timestamp=str(payload["source_min_timestamp"]),
            source_max_timestamp=str(payload["source_max_timestamp"]),
            aggregate_bar_count=int(payload["aggregate_bar_count"]),
            min_timestamp=str(payload["min_timestamp"]),
            max_timestamp=str(payload["max_timestamp"]),
            artifact_scope=str(payload["artifact_scope"]),
            max_rows_per_partition=int(payload["max_rows_per_partition"]),
            artifact_count_limit=int(payload["artifact_count_limit"]),
            artifact_byte_limit=int(payload["artifact_byte_limit"]),
            declared_artifact_count=int(payload["declared_artifact_count"]),
            declared_artifact_bytes=int(payload["declared_artifact_bytes"]),
            actual_artifact_count=int(payload["actual_artifact_count"]),
            actual_artifact_bytes=int(payload["actual_artifact_bytes"]),
            partitions=tuple(PartitionRecord(**item) for item in partitions_raw),
            publication_sha256=str(payload["publication_sha256"]),
        )
        if recorded_bytes != manifest.to_json().encode("utf-8"):
            raise ValueError("aggregate publication manifest is not exact canonical JSON")
        return manifest
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid aggregate publication manifest: {path}") from error


def verify_aggregate_publication(
    directory: str | Path,
    manifest: AggregatePublicationManifest | None = None,
) -> None:
    """Verify a complete publication tree using bounded no-follow primitives."""

    root = Path(directory)
    require_regular_directory(root)
    recorded = read_aggregate_publication_manifest(root / AGGREGATE_MANIFEST_NAME)
    if manifest is not None and recorded != manifest:
        raise ValueError("aggregate publication manifest differs from recorded bytes")
    active = recorded
    if not regular_file_matches(
        root / AGGREGATE_SUCCESS_NAME,
        f"{active.publication_sha256}\n".encode("utf-8"),
    ):
        raise ValueError("aggregate publication completion marker is absent or invalid")
    expected = {
        AGGREGATE_MANIFEST_NAME,
        AGGREGATE_SUCCESS_NAME,
        *(partition.path for partition in active.partitions),
    }
    actual = set(bounded_regular_files(root, maximum=_MAX_PUBLICATION_ENTRIES))
    if actual != expected:
        raise ValueError("aggregate publication contains missing or unmanifested artifacts")
    for partition in active.partitions:
        if sha256_regular(root / partition.path) != partition.sha256:
            raise ValueError(f"aggregate partition checksum mismatch: {partition.path}")
    actual_artifact_count = len(active.partitions)
    actual_artifact_bytes = sum(
        _regular_file_size(root / partition.path) for partition in active.partitions
    )
    if (
        actual_artifact_count != active.actual_artifact_count
        or actual_artifact_bytes != active.actual_artifact_bytes
    ):
        raise ValueError("aggregate actual artifact metrics differ from staged files")
    if (
        actual_artifact_count > active.declared_artifact_count
        or actual_artifact_count > active.artifact_count_limit
        or actual_artifact_bytes > active.declared_artifact_bytes
        or actual_artifact_bytes > active.artifact_byte_limit
    ):
        raise ValueError("aggregate actual artifacts exceed declared or frozen limits")


def _build_parent_source_selection(
    parent_directory: Path,
    parent_manifest: SnapshotManifest,
    *,
    symbol: str,
    segment_id: int,
    target_timeframe: str,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
) -> AggregateSourceSelectionReceipt:
    source_identity = OrderedSourceIdentity()
    source_bytes = 0
    minimum: datetime | None = None
    maximum: datetime | None = None
    previous: datetime | None = None
    bindings: list[tuple[str, str]] = []
    path_prefix = f"symbol={symbol}/timeframe=1m/"
    expected_schema = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))
    target_minutes = target_timeframe_minutes(target_timeframe)

    for partition in parent_manifest.partitions:
        if not partition.path.startswith(path_prefix):
            continue
        if partition.row_count > _MAX_PARENT_PARTITION_ROWS:
            raise ValueError("parent source partition row count exceeds the bounded daily limit")
        partition_path = parent_directory / partition.path
        partition_bytes = read_bounded_regular(partition_path, _MAX_PARENT_PARTITION_BYTES)
        if hashlib.sha256(partition_bytes).hexdigest() != partition.sha256:
            raise ValueError("parent source partition bytes differ from the verified manifest")
        frame = pl.read_parquet(BytesIO(partition_bytes))
        if frame.schema != expected_schema:
            raise ValueError("parent source partition schema is invalid")
        if frame.height != partition.row_count:
            raise ValueError("parent source partition row count differs from its manifest")
        validate_candle_frame(frame)
        if frame.is_empty():
            raise ValueError("parent source partition cannot be empty")
        if frame["symbol"].unique().to_list() != [symbol]:
            raise ValueError("parent source partition symbol differs from its path")
        if frame["timeframe"].unique().to_list() != ["1m"]:
            raise ValueError("parent source partition timeframe differs from its path")
        first_timestamp = cast(datetime, frame["timestamp"][0])
        last_timestamp = cast(datetime, frame["timestamp"][-1])
        if (
            _iso_utc(first_timestamp) != partition.min_timestamp
            or _iso_utc(last_timestamp) != partition.max_timestamp
        ):
            raise ValueError("parent source partition timestamps differ from its manifest")
        selected = frame.filter(pl.col("segment_id") == segment_id)
        if selected.is_empty():
            continue
        bindings.append((partition.path, partition.sha256))
        for raw_row in selected.iter_rows(named=True):
            row = canonical_source_row_payload(raw_row)
            timestamp = cast(datetime, row["timestamp"])
            if previous is None:
                if int(timestamp.timestamp() * 1_000_000) % (target_minutes * 60_000_000):
                    raise ValueError("parent source selection first minute is not target aligned")
                minimum = timestamp
            elif timestamp != previous + timedelta(minutes=1):
                raise ValueError(
                    "parent source selection crosses a gap, reorder, duplicate, or segment boundary"
                )
            previous = timestamp
            maximum = timestamp
            source_identity.update(canonical_source_row_identity(raw_row))
            source_bytes += len(canonical_json("canonical-source-minute", row))
            if source_identity.count > min(demand.source_rows, budget.max_source_rows):
                raise ValidationWorkBudgetViolation(
                    "source_rows",
                    source_identity.count,
                    min(demand.source_rows, budget.max_source_rows),
                )
            if source_bytes > min(demand.source_bytes, budget.max_source_bytes):
                raise ValidationWorkBudgetViolation(
                    "source_bytes",
                    source_bytes,
                    min(demand.source_bytes, budget.max_source_bytes),
                )

    if minimum is None or maximum is None:
        raise ValueError("parent source selection contains no rows for the requested segment")
    if source_identity.count % target_minutes:
        raise ValueError("parent source selection ends with a partial target period")
    return AggregateSourceSelectionReceipt(
        parent_snapshot_sha256=parent_manifest.snapshot_sha256,
        symbol=symbol,
        source_timeframe="1m",
        segment_id=segment_id,
        source_row_count=source_identity.count,
        source_bytes=source_bytes,
        source_sha256=source_identity.hexdigest(),
        source_min_timestamp=_iso_utc(minimum),
        source_max_timestamp=_iso_utc(maximum),
        parent_partition_bindings=tuple(bindings),
    )


def _partition_artifact_metrics(
    directory: Path,
    partitions: tuple[PartitionRecord, ...],
) -> tuple[int, int]:
    expected = {partition.path for partition in partitions}
    actual = set(bounded_regular_files(directory, maximum=_MAX_PUBLICATION_ENTRIES))
    if actual != expected:
        raise ValueError("aggregate staging contains unexpected partition artifacts")
    return len(actual), sum(_regular_file_size(directory / path) for path in actual)


def _conservative_artifact_byte_upper(
    aggregate_bars: int,
    artifacts: int,
    source_rows_per_aggregate: int,
) -> int:
    """Bound partition bytes from validated field widths and fixed Parquet structure."""

    maximum_row_bytes = (
        _FIXED_AGGREGATE_PARQUET_ROW_UPPER_BYTES
        + source_rows_per_aggregate * _SOURCE_ID_PARQUET_UPPER_BYTES
    )
    return aggregate_bars * maximum_row_bytes + artifacts * _PARQUET_PARTITION_OVERHEAD_UPPER_BYTES


def _require_exact_demand(stage: str, declared: int, required: int) -> None:
    if declared != required:
        raise ValueError(f"{stage} declaration must equal the verified requirement {required}")


def _require_bounded_component(value: str, label: str) -> None:
    if len(value.encode("utf-8")) > _MAX_IDENTITY_COMPONENT_BYTES:
        raise ValueError(f"{label} exceeds the bounded identity width")


def _publish_staged_no_clobber(
    staging: Path,
    root: Path,
    *,
    symbol: str,
    target_timeframe: str,
    segment_id: int,
    manifest: AggregatePublicationManifest,
) -> None:
    """Claim and fill a publication with directory-relative no-follow operations."""

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory:
        raise RuntimeError("safe aggregate publication requires no-follow directory operations")
    directory_flags = os.O_RDONLY | no_follow | directory
    root_fd = os.open(root, directory_flags)
    try:
        symbol_fd = _create_or_open_directory_at(root_fd, f"symbol={symbol}", directory_flags)
        try:
            timeframe_fd = _create_or_open_directory_at(
                symbol_fd,
                f"timeframe={target_timeframe}",
                directory_flags,
            )
            try:
                final_name = f"segment={segment_id}"
                try:
                    os.mkdir(final_name, dir_fd=timeframe_fd)
                except FileExistsError as error:
                    raise FileExistsError("aggregate publication already exists") from error
                final_fd = os.open(final_name, directory_flags, dir_fd=timeframe_fd)
                try:
                    for partition in manifest.partitions:
                        relative = Path(partition.path)
                        if len(relative.parts) != 2:
                            raise ValueError("aggregate partition path is not canonical")
                        chunk_fd = _create_directory_at(final_fd, relative.parts[0], directory_flags)
                        try:
                            content = read_bounded_regular(
                                staging / relative,
                                manifest.declared_artifact_bytes,
                            )
                            if hashlib.sha256(content).hexdigest() != partition.sha256:
                                raise ValueError(
                                    f"staged aggregate partition changed: {partition.path}"
                                )
                            _write_exclusive_at(chunk_fd, relative.parts[1], content)
                        finally:
                            os.close(chunk_fd)
                    manifest_bytes = read_bounded_regular(
                        staging / AGGREGATE_MANIFEST_NAME,
                        _MAX_MANIFEST_BYTES,
                    )
                    if manifest_bytes != manifest.to_json().encode("utf-8"):
                        raise ValueError("staged aggregate manifest changed")
                    _write_exclusive_at(final_fd, AGGREGATE_MANIFEST_NAME, manifest_bytes)
                    success_bytes = read_bounded_regular(
                        staging / AGGREGATE_SUCCESS_NAME,
                        128,
                    )
                    expected_success = f"{manifest.publication_sha256}\n".encode("utf-8")
                    if success_bytes != expected_success:
                        raise ValueError("staged aggregate completion marker changed")
                    _write_exclusive_at(final_fd, AGGREGATE_SUCCESS_NAME, success_bytes)
                    os.fsync(final_fd)
                finally:
                    os.close(final_fd)
                os.fsync(timeframe_fd)
            finally:
                os.close(timeframe_fd)
        finally:
            os.close(symbol_fd)
    finally:
        os.close(root_fd)


def _create_or_open_directory_at(parent_fd: int, name: str, flags: int) -> int:
    try:
        os.mkdir(name, dir_fd=parent_fd)
    except FileExistsError:
        pass
    return os.open(name, flags, dir_fd=parent_fd)


def _create_directory_at(parent_fd: int, name: str, flags: int) -> int:
    os.mkdir(name, dir_fd=parent_fd)
    return os.open(name, flags, dir_fd=parent_fd)


def _write_exclusive_at(parent_fd: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(name, flags, 0o644, dir_fd=parent_fd)
    try:
        view = memoryview(content)
        while view:
            written = os.write(file_fd, view)
            if written < 1:
                raise OSError("aggregate artifact write made no progress")
            view = view[written:]
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _regular_file_size(path: Path) -> int:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("aggregate artifact must be a regular file")
    return metadata.st_size


def _aggregate_frame_schema() -> pl.Schema:
    return pl.Schema(
        cast(
            Any,
            {
                "schema_version": pl.Int64,
                "timestamp": pl.String,
                "bar_close": pl.String,
                "symbol": pl.String,
                "source_timeframe": pl.String,
                "target_timeframe": pl.String,
                "segment_id": pl.Int64,
                "continuity": pl.String,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
                "source_row_count": pl.Int64,
                "source_row_ids": pl.List(pl.String),
                "source_sha256": pl.String,
                "parent_snapshot_sha256": pl.String,
                "row_sha256": pl.String,
            },
        )
    )


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("aggregate manifest timestamp must be timezone-aware")
    normalized = parsed.astimezone(UTC)
    if _iso_utc(normalized) != value:
        raise ValueError("aggregate manifest timestamp must use canonical UTC formatting")
    return normalized


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


__all__ = [
    "AGGREGATE_MANIFEST_NAME",
    "AGGREGATE_SUCCESS_NAME",
    "MAX_AGGREGATE_ROWS_PER_PARTITION",
    "AggregatePublicationManifest",
    "AggregateSourceSelectionReceipt",
    "publish_aggregate_bars",
    "read_aggregate_publication_manifest",
    "verify_aggregate_publication",
]
