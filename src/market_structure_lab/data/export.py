from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl

from market_structure_lab.data.canonical import (
    CANONICAL_COLUMNS,
    CANONICAL_SCHEMA,
    validate_candle_frame,
)
from market_structure_lab.data.segments import CandleSegmenter, SegmentBoundary

_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
MANIFEST_NAME = "manifest.json"
SUCCESS_NAME = "_SUCCESS"
_IDENTITY_NAME = ".snapshot-identity.json"


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    dataset_version: str
    dump_sha256: str
    recovery_sha256: str
    mapping_version: str
    config_version: str
    code_commit: str
    freshness_report_sha256: str | None = None
    freshness_manifest_sha256: str | None = None
    compatibility_manifest_sha256: str | None = None
    freshness_as_of: str | None = None
    publication_policy: str | None = None

    def __post_init__(self) -> None:
        if not _PATH_COMPONENT.fullmatch(self.dataset_version):
            raise ValueError("dataset_version must be a safe path component")
        if not _SHA256.fullmatch(self.dump_sha256):
            raise ValueError("dump_sha256 must be a SHA-256 hex digest")
        if not _SHA256.fullmatch(self.recovery_sha256):
            raise ValueError("recovery_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "dump_sha256", self.dump_sha256.lower())
        object.__setattr__(self, "recovery_sha256", self.recovery_sha256.lower())
        for value in (self.mapping_version, self.config_version, self.code_commit):
            if not value:
                raise ValueError("snapshot identity values must be non-empty")
        freshness_values = (
            self.freshness_report_sha256,
            self.freshness_manifest_sha256,
            self.compatibility_manifest_sha256,
            self.freshness_as_of,
            self.publication_policy,
        )
        if any(value is not None for value in freshness_values):
            if any(value is None for value in freshness_values):
                raise ValueError("freshness snapshot identity evidence must be complete")
            for digest in (
                self.freshness_report_sha256,
                self.freshness_manifest_sha256,
                self.compatibility_manifest_sha256,
            ):
                if digest is None or not _SHA256.fullmatch(digest):
                    raise ValueError("freshness identity hashes must be SHA-256 hex digests")
            object.__setattr__(
                self,
                "freshness_report_sha256",
                cast(str, self.freshness_report_sha256).lower(),
            )
            object.__setattr__(
                self,
                "freshness_manifest_sha256",
                cast(str, self.freshness_manifest_sha256).lower(),
            )
            object.__setattr__(
                self,
                "compatibility_manifest_sha256",
                cast(str, self.compatibility_manifest_sha256).lower(),
            )
            if self.freshness_as_of is None:
                raise ValueError("freshness snapshot identity requires an as-of cutoff")
            normalized_as_of = normalize_manifest_timestamp(self.freshness_as_of)
            if _iso_utc(normalized_as_of) != self.freshness_as_of:
                raise ValueError("freshness as-of cutoff must use canonical UTC formatting")
            if self.publication_policy not in {
                "require_healthy",
                "allow_provenance_blocked",
            }:
                raise ValueError("unsupported freshness snapshot publication policy")


@dataclass(frozen=True, slots=True)
class PartitionRecord:
    path: str
    sha256: str
    row_count: int
    min_timestamp: str
    max_timestamp: str

    def __post_init__(self) -> None:
        _validated_relative_path(self.path)
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("partition sha256 must be a SHA-256 hex digest")
        if self.row_count < 1:
            raise ValueError("partition row_count must be positive")
        if normalize_manifest_timestamp(self.min_timestamp) > normalize_manifest_timestamp(
            self.max_timestamp
        ):
            raise ValueError("partition timestamp bounds are inverted")


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    schema_version: int
    identity: SnapshotIdentity
    row_count: int
    min_timestamp: str | None
    max_timestamp: str | None
    partitions: tuple[PartitionRecord, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported snapshot manifest schema")
        if self.row_count < 0:
            raise ValueError("snapshot row_count cannot be negative")
        paths = tuple(item.path for item in self.partitions)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("snapshot partitions must have unique deterministic ordering")
        if sum(item.row_count for item in self.partitions) != self.row_count:
            raise ValueError("partition row counts do not match snapshot row count")
        if not _SHA256.fullmatch(self.snapshot_sha256):
            raise ValueError("snapshot_sha256 must be a SHA-256 hex digest")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def export_partitioned_snapshot(
    batches: Iterable[pl.DataFrame],
    *,
    output_root: str | Path,
    identity: SnapshotIdentity,
    boundaries: tuple[SegmentBoundary, ...] | list[SegmentBoundary] = (),
) -> SnapshotManifest:
    """Write a pinned snapshot in bounded UTC-day partitions and publish atomically."""
    root = Path(output_root)
    final = root / f"dataset_version={identity.dataset_version}"
    if final.exists():
        manifest = read_snapshot_manifest(final / MANIFEST_NAME)
        if manifest.identity != identity:
            raise FileExistsError("dataset version already exists with a different identity")
        verify_snapshot(final, manifest)
        return manifest

    staging = root / f".dataset_version={identity.dataset_version}.partial"
    staging.mkdir(parents=True, exist_ok=True)
    identity_path = staging / _IDENTITY_NAME
    identity_json = json.dumps(asdict(identity), sort_keys=True, separators=(",", ":")) + "\n"
    if identity_path.exists() and identity_path.read_text(encoding="utf-8") != identity_json:
        raise FileExistsError("incomplete dataset version has a different pinned identity")
    if not identity_path.exists():
        _atomic_write_text(identity_path, identity_json)
    for temporary in staging.rglob(".*.tmp"):
        temporary.unlink()
    segmenter = CandleSegmenter(boundaries)

    records: list[PartitionRecord] = []
    current_partition: tuple[str, str, str] | None = None
    current_rows: list[dict[str, object]] = []
    previous_key: tuple[str, str, datetime] | None = None
    total_rows = 0
    minimum: datetime | None = None
    maximum: datetime | None = None

    def flush() -> None:
        nonlocal current_rows
        if current_partition is None or not current_rows:
            return
        symbol, timeframe, date = current_partition
        partition_schema = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))
        frame = pl.DataFrame(current_rows, schema=partition_schema)
        relative = Path(
            f"symbol={_safe_component(symbol, 'symbol')}"
        ) / f"timeframe={_safe_component(timeframe, 'timeframe')}" / f"date={date}" / "part.parquet"
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        checksum = sha256_file(temporary)
        if destination.exists() and sha256_file(destination) == checksum:
            temporary.unlink()
        else:
            os.replace(temporary, destination)
        first_timestamp = cast(datetime, current_rows[0]["timestamp"])
        last_timestamp = cast(datetime, current_rows[-1]["timestamp"])
        records.append(
            PartitionRecord(
                path=relative.as_posix(),
                sha256=checksum,
                row_count=frame.height,
                min_timestamp=_iso_utc(first_timestamp),
                max_timestamp=_iso_utc(last_timestamp),
            )
        )
        current_rows = []

    for batch in batches:
        validate_candle_frame(batch)
        segmented = segmenter.apply(batch)
        for row in segmented.select(*CANONICAL_COLUMNS, "segment_id").iter_rows(named=True):
            timestamp = row["timestamp"]
            key = (str(row["symbol"]), str(row["timeframe"]), timestamp)
            if previous_key is not None and key <= previous_key:
                problem = "duplicate" if key == previous_key else "out-of-order"
                raise ValueError(f"{problem} candle across export batches: {key!r}")
            previous_key = key
            partition = (key[0], key[1], timestamp.astimezone(UTC).date().isoformat())
            if current_partition is not None and partition != current_partition:
                flush()
            current_partition = partition
            current_rows.append(dict(row))
            total_rows += 1
            minimum = timestamp if minimum is None else min(minimum, timestamp)
            maximum = timestamp if maximum is None else max(maximum, timestamp)
    flush()

    ordered_records = tuple(sorted(records, key=lambda item: item.path))
    expected_paths = {item.path for item in ordered_records}
    actual_paths = {
        path.relative_to(staging).as_posix() for path in staging.rglob("*.parquet")
    }
    unexpected = actual_paths.difference(expected_paths)
    if unexpected:
        raise RuntimeError(f"staging area contains stale partitions: {sorted(unexpected)}")

    manifest_without_hash: dict[str, Any] = {
        "schema_version": 1,
        "identity": asdict(identity),
        "row_count": total_rows,
        "min_timestamp": _iso_utc(minimum) if minimum else None,
        "max_timestamp": _iso_utc(maximum) if maximum else None,
        "partitions": [asdict(item) for item in ordered_records],
    }
    snapshot_hash = hashlib.sha256(_canonical_json(manifest_without_hash)).hexdigest()
    manifest = SnapshotManifest(
        schema_version=1,
        identity=identity,
        row_count=total_rows,
        min_timestamp=manifest_without_hash["min_timestamp"],
        max_timestamp=manifest_without_hash["max_timestamp"],
        partitions=ordered_records,
        snapshot_sha256=snapshot_hash,
    )
    _atomic_write_text(staging / MANIFEST_NAME, manifest.to_json())
    _atomic_write_text(staging / SUCCESS_NAME, f"{snapshot_hash}\n")
    verify_snapshot(staging, manifest)
    identity_path.unlink()
    try:
        staging.replace(final)
    except FileExistsError:
        raise FileExistsError("dataset version was published concurrently") from None
    return manifest


def read_snapshot_manifest(path: str | Path) -> SnapshotManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        identity = SnapshotIdentity(**payload["identity"])
        partitions = tuple(PartitionRecord(**item) for item in payload["partitions"])
        return SnapshotManifest(
            schema_version=int(payload["schema_version"]),
            identity=identity,
            row_count=int(payload["row_count"]),
            min_timestamp=payload["min_timestamp"],
            max_timestamp=payload["max_timestamp"],
            partitions=partitions,
            snapshot_sha256=str(payload["snapshot_sha256"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid snapshot manifest: {path}") from error


def verify_snapshot(directory: str | Path, manifest: SnapshotManifest | None = None) -> None:
    root = Path(directory)
    active = manifest or read_snapshot_manifest(root / MANIFEST_NAME)
    if active.schema_version != 1:
        raise ValueError("unsupported snapshot manifest schema")
    payload: dict[str, Any] = {
        "schema_version": active.schema_version,
        "identity": asdict(active.identity),
        "row_count": active.row_count,
        "min_timestamp": active.min_timestamp,
        "max_timestamp": active.max_timestamp,
        "partitions": [asdict(item) for item in active.partitions],
    }
    expected_snapshot_hash = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if active.snapshot_sha256 != expected_snapshot_hash:
        raise ValueError("snapshot manifest logical hash mismatch")
    success_path = root / SUCCESS_NAME
    if not success_path.is_file() or success_path.read_text(encoding="utf-8").strip() != expected_snapshot_hash:
        raise ValueError("snapshot completion marker is absent or invalid")
    counted_rows = 0
    for partition in active.partitions:
        path = root / _validated_relative_path(partition.path)
        if not path.is_file() or sha256_file(path) != partition.sha256:
            raise ValueError(f"partition checksum mismatch: {partition.path}")
        counted_rows += partition.row_count
    if counted_rows != active.row_count:
        raise ValueError("partition row counts do not match snapshot row count")
    expected_paths = {item.path for item in active.partitions}
    actual_paths = {path.relative_to(root).as_posix() for path in root.rglob("*.parquet")}
    if actual_paths != expected_paths:
        raise ValueError("snapshot contains unmanifested or missing partitions")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _safe_component(value: str, label: str) -> str:
    if not _PATH_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} must be a safe path component")
    return value


def _validated_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("partition path must be a normalized relative path")
    if path.name != "part.parquet":
        raise ValueError("partition path must end in part.parquet")
    return path


def normalize_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("manifest timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
