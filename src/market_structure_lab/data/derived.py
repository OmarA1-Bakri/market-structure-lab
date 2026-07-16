"""Atomic, bounded publication for outcome-blind feature and event datasets."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureRegistry,
    FeatureValueKind,
)

MANIFEST_NAME = "manifest.json"
SUCCESS_NAME = "_SUCCESS"
_IDENTITY_NAME = ".publication-identity.json"
_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATASET_ID = re.compile(r"^DS-[0-9]{6}$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_PROHIBITED_TOKENS = frozenset(
    {
        "continuation",
        "forward",
        "future",
        "hit",
        "label",
        "mae",
        "mfe",
        "outcome",
        "pnl",
        "profit",
        "profitability",
        "returns",
        "reversal",
        "target",
        "trade",
    }
)

PublicationKind = Literal["features", "events"]


@dataclass(frozen=True, slots=True)
class DerivedPublicationIdentity:
    """Frozen inputs required to reproduce a derived dataset publication."""

    dataset_version: str
    dataset_snapshot_sha256: str
    feature_set_id: str
    feature_registry_sha256: str
    config_version: str
    profile_version: str
    window_policy_id: str
    event_version: str
    normalizer_artifact_sha256: str | None
    code_commit: str
    uv_lock_sha256: str

    def __post_init__(self) -> None:
        if _DATASET_ID.fullmatch(self.dataset_version) is None:
            raise ValueError("dataset_version must match DS-######")
        if _FEATURE_SET_ID.fullmatch(self.feature_set_id) is None:
            raise ValueError("feature_set_id must match FS-######")
        for name in (
            "dataset_snapshot_sha256",
            "feature_registry_sha256",
            "uv_lock_sha256",
        ):
            value = cast(str, getattr(self, name))
            if _SHA256.fullmatch(value) is None:
                raise ValueError(f"{name} must be a SHA-256 hex digest")
            object.__setattr__(self, name, value.lower())
        if _COMMIT.fullmatch(self.code_commit) is None:
            raise ValueError("code_commit must be a hexadecimal Git object ID")
        object.__setattr__(self, "code_commit", self.code_commit.lower())
        for name in ("config_version", "profile_version", "window_policy_id", "event_version"):
            _safe_component(cast(str, getattr(self, name)), name)
        if self.normalizer_artifact_sha256 is not None:
            if _SHA256.fullmatch(self.normalizer_artifact_sha256) is None:
                raise ValueError("normalizer_artifact_sha256 must be a SHA-256 hex digest or None")
            object.__setattr__(
                self, "normalizer_artifact_sha256", self.normalizer_artifact_sha256.lower()
            )


@dataclass(frozen=True, slots=True)
class DerivedPartitionRecord:
    path: str
    sha256: str
    row_count: int
    min_timestamp: str
    max_timestamp: str

    def __post_init__(self) -> None:
        _validated_partition_path(self.path)
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("partition sha256 must be a SHA-256 hex digest")
        if self.row_count < 1:
            raise ValueError("partition row_count must be positive")
        if _parse_utc(self.min_timestamp) > _parse_utc(self.max_timestamp):
            raise ValueError("partition timestamp bounds are inverted")


@dataclass(frozen=True, slots=True)
class DerivedEvidence:
    null_value_count: int = 0
    warmup_row_count: int = 0
    event_count: int = 0
    overlap_pair_count: int = 0
    overlap_event_count: int = 0
    maximum_concurrency: int = 0
    events_by_type: tuple[tuple[str, int], ...] = ()
    event_trigger_versions: tuple[str, ...] = ()
    leakage_audit_passed: bool = True
    prohibited_fields: tuple[str, ...] = ()
    cutoff_violation_count: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.null_value_count,
            self.warmup_row_count,
            self.event_count,
            self.overlap_pair_count,
            self.overlap_event_count,
            self.maximum_concurrency,
            self.cutoff_violation_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("evidence counts cannot be negative")
        if tuple(sorted(self.events_by_type)) != self.events_by_type:
            raise ValueError("events_by_type must be sorted")
        if tuple(sorted(set(self.event_trigger_versions))) != self.event_trigger_versions:
            raise ValueError("event_trigger_versions must be sorted and unique")
        if self.leakage_audit_passed != (
            not self.prohibited_fields and self.cutoff_violation_count == 0
        ):
            raise ValueError("leakage evidence is internally inconsistent")

    @property
    def overlap_event_ratio(self) -> float:
        return self.overlap_event_count / self.event_count if self.event_count else 0.0

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["events_by_type"] = dict(self.events_by_type)
        payload["overlap_event_ratio"] = self.overlap_event_ratio
        return payload


@dataclass(frozen=True, slots=True)
class DerivedPublicationManifest:
    schema_version: int
    publication_kind: PublicationKind
    identity: DerivedPublicationIdentity
    row_count: int
    min_timestamp: str | None
    max_timestamp: str | None
    max_rows_per_part: int
    max_buffered_rows: int
    parquet_schema: tuple[tuple[str, str], ...]
    evidence: DerivedEvidence
    partitions: tuple[DerivedPartitionRecord, ...]
    content_sha256: str
    publication_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported derived publication manifest schema")
        if self.publication_kind not in ("features", "events"):
            raise ValueError("unsupported publication kind")
        if self.row_count < 0 or self.max_rows_per_part < 1:
            raise ValueError("invalid publication row limits")
        if not 0 <= self.max_buffered_rows <= self.max_rows_per_part:
            raise ValueError("max_buffered_rows exceeds the configured bound")
        paths = tuple(item.path for item in self.partitions)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("publication partitions must have unique deterministic ordering")
        if sum(item.row_count for item in self.partitions) != self.row_count:
            raise ValueError("partition row counts do not match publication row count")
        for value, label in (
            (self.content_sha256, "content_sha256"),
            (self.publication_sha256, "publication_sha256"),
        ):
            if _SHA256.fullmatch(value) is None:
                raise ValueError(f"{label} must be a SHA-256 hex digest")

    def logical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "publication_kind": self.publication_kind,
            "identity": asdict(self.identity),
            "row_count": self.row_count,
            "min_timestamp": self.min_timestamp,
            "max_timestamp": self.max_timestamp,
            "max_rows_per_part": self.max_rows_per_part,
            "max_buffered_rows": self.max_buffered_rows,
            "parquet_schema": dict(self.parquet_schema),
            "evidence": self.evidence.to_dict(),
            "partitions": [asdict(item) for item in self.partitions],
            "content_sha256": self.content_sha256,
        }

    def to_json(self) -> str:
        payload = self.logical_dict()
        payload["publication_sha256"] = self.publication_sha256
        return json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


@dataclass(slots=True)
class _OverlapState:
    end: datetime
    affected: bool = False


class _EventEvidenceCounter:
    def __init__(self) -> None:
        self.by_type: Counter[str] = Counter()
        self.trigger_versions: set[str] = set()
        self.event_count = 0
        self.overlap_pairs = 0
        self.overlap_events = 0
        self.maximum_concurrency = 0
        self._stream: tuple[str, str, int] | None = None
        self._active: list[tuple[datetime, int, _OverlapState]] = []
        self._sequence = 0

    def add(
        self,
        *,
        symbol: str,
        timeframe: str,
        segment_id: int,
        start: datetime,
        end: datetime,
        event_type: str,
        trigger_version: str,
    ) -> None:
        stream = (symbol, timeframe, segment_id)
        if self._stream != stream:
            self._finish_stream()
            self._stream = stream
        while self._active and self._active[0][0] <= start:
            _, _, state = heapq.heappop(self._active)
            self.overlap_events += int(state.affected)
        active_count = len(self._active)
        state = _OverlapState(end=end, affected=active_count > 0)
        if active_count:
            self.overlap_pairs += active_count
            for _, _, active in self._active:
                active.affected = True
        heapq.heappush(self._active, (end, self._sequence, state))
        self._sequence += 1
        self.maximum_concurrency = max(self.maximum_concurrency, active_count + 1)
        self.event_count += 1
        self.by_type[event_type] += 1
        self.trigger_versions.add(trigger_version)

    def _finish_stream(self) -> None:
        self.overlap_events += sum(int(state.affected) for _, _, state in self._active)
        self._active.clear()

    def finish(self) -> None:
        self._finish_stream()


def publish_feature_rows(
    rows: Iterable[FeatureRow],
    *,
    output_root: str | Path,
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistry,
    max_rows_per_part: int = 100_000,
) -> DerivedPublicationManifest:
    """Publish registered feature rows with a fixed bounded Parquet row buffer."""

    _validate_registry_identity(identity, registry)
    schema = _feature_schema(registry)

    def converted() -> Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]]:
        for row in rows:
            registry.validate_row(row)
            _validate_feature_identity(row, identity)
            _audit_names(row.values)
            payload: dict[str, object] = {
                "timestamp": row.timestamp,
                "information_cutoff": row.information_cutoff,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "segment_id": row.segment_id,
                "dataset_version": row.dataset_version,
                "config_version": row.config_version,
                "profile_version": row.profile_version,
                "window_policy_id": row.window_policy_id,
                "feature_set_id": row.feature_set_id,
                "registry_id": row.registry_id,
                **row.values,
            }
            key = (row.symbol, row.timeframe, row.timestamp)
            yield payload, row.timestamp, key

    return _publish(
        converted(),
        output_root=output_root,
        identity=identity,
        kind="features",
        schema=schema,
        max_rows_per_part=max_rows_per_part,
        feature_names=registry.names,
    )


def publish_market_events(
    events: Iterable[object],
    *,
    output_root: str | Path,
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistry,
    max_rows_per_part: int = 100_000,
) -> DerivedPublicationManifest:
    """Publish exact ``MarketEvent`` rows without widening their Parquet schema."""

    from market_structure_lab.events.models import MarketEvent

    _validate_registry_identity(identity, registry)
    schema = _event_schema(registry)
    expected_fields = {item.name for item in fields(MarketEvent)}
    counter = _EventEvidenceCounter()

    def converted() -> Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]]:
        previous_start: tuple[str, str, int, datetime] | None = None
        for event in events:
            if not isinstance(event, MarketEvent):
                raise TypeError("events must contain only MarketEvent values")
            if {item.name for item in fields(event)} != expected_fields:
                raise ValueError("MarketEvent schema does not match the frozen publication schema")
            _validate_event_identity(event, identity, registry)
            _audit_names(event.feature_values)
            _audit_names(event.metadata)
            event_type = event.kind.value
            start_key = (event.symbol, event.timeframe, event.segment_id, event.start)
            if previous_start is not None and start_key < previous_start:
                raise ValueError("events must be ordered by symbol, timeframe, segment, and start")
            previous_start = start_key
            counter.add(
                symbol=event.symbol,
                timeframe=event.timeframe,
                segment_id=event.segment_id,
                start=event.start,
                end=event.end,
                event_type=event_type,
                trigger_version=event.trigger_version,
            )
            payload: dict[str, object] = {
                "event_id": event.event_id,
                "event_type": event_type,
                "start": event.start,
                "end": event.end,
                "information_cutoff": event.information_cutoff,
                "symbol": event.symbol,
                "timeframe": event.timeframe,
                "segment_id": event.segment_id,
                "dataset_version": event.dataset_version,
                "config_version": event.config_version,
                "profile_version": event.profile_version,
                "window_policy_id": event.window_policy_id,
                "feature_set_id": event.feature_set_id,
                "registry_id": event.registry_id,
                "registry_sha256": event.registry_sha256,
                "trigger_version": event.trigger_version,
                "exploratory": event.exploratory,
                "metadata_json": _json_text(event.metadata),
                **event.feature_values,
            }
            key = (
                event.symbol,
                event.timeframe,
                event.segment_id,
                event.start,
                event.end,
                event_type,
                event.event_id,
            )
            yield payload, event.information_cutoff, key
        counter.finish()

    return _publish(
        converted(),
        output_root=output_root,
        identity=identity,
        kind="events",
        schema=schema,
        max_rows_per_part=max_rows_per_part,
        feature_names=registry.names,
        event_counter=counter,
    )


def read_derived_manifest(path: str | Path) -> DerivedPublicationManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_evidence = dict(payload["evidence"])
        raw_evidence.pop("overlap_event_ratio", None)
        raw_evidence["events_by_type"] = tuple(sorted(raw_evidence["events_by_type"].items()))
        raw_evidence["event_trigger_versions"] = tuple(raw_evidence["event_trigger_versions"])
        raw_evidence["prohibited_fields"] = tuple(raw_evidence["prohibited_fields"])
        manifest = DerivedPublicationManifest(
            schema_version=int(payload["schema_version"]),
            publication_kind=payload["publication_kind"],
            identity=DerivedPublicationIdentity(**payload["identity"]),
            row_count=int(payload["row_count"]),
            min_timestamp=payload["min_timestamp"],
            max_timestamp=payload["max_timestamp"],
            max_rows_per_part=int(payload["max_rows_per_part"]),
            max_buffered_rows=int(payload["max_buffered_rows"]),
            parquet_schema=tuple(payload["parquet_schema"].items()),
            evidence=DerivedEvidence(**raw_evidence),
            partitions=tuple(DerivedPartitionRecord(**item) for item in payload["partitions"]),
            content_sha256=str(payload["content_sha256"]),
            publication_sha256=str(payload["publication_sha256"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid derived publication manifest: {path}") from error
    return manifest


def verify_derived_publication(
    directory: str | Path,
    manifest: DerivedPublicationManifest | None = None,
    *,
    require_success: bool = True,
) -> None:
    root = Path(directory)
    active = manifest or read_derived_manifest(root / MANIFEST_NAME)
    logical_hash = hashlib.sha256(_canonical_json(active.logical_dict())).hexdigest()
    if active.publication_sha256 != logical_hash:
        raise ValueError("derived publication manifest logical hash mismatch")
    success = root / SUCCESS_NAME
    if require_success and (
        not success.is_file() or success.read_text(encoding="utf-8").strip() != logical_hash
    ):
        raise ValueError("derived publication completion marker is absent or invalid")
    counted = 0
    schema = dict(active.parquet_schema)
    for partition in active.partitions:
        path = root / _validated_partition_path(partition.path)
        if not path.is_file() or _sha256_file(path) != partition.sha256:
            raise ValueError(f"partition checksum mismatch: {partition.path}")
        actual_schema = {name: str(dtype) for name, dtype in pl.read_parquet_schema(path).items()}
        if actual_schema != schema:
            raise ValueError(f"partition schema mismatch: {partition.path}")
        frame = (
            pl.scan_parquet(path)
            .select(
                pl.len().alias("rows"),
                pl.col(_timestamp_column(active.publication_kind)).min().alias("minimum"),
                pl.col(_timestamp_column(active.publication_kind)).max().alias("maximum"),
            )
            .collect()
        )
        if frame.item(0, "rows") != partition.row_count:
            raise ValueError(f"partition row count mismatch: {partition.path}")
        if _iso_utc(frame.item(0, "minimum")) != partition.min_timestamp:
            raise ValueError(f"partition minimum timestamp mismatch: {partition.path}")
        if _iso_utc(frame.item(0, "maximum")) != partition.max_timestamp:
            raise ValueError(f"partition maximum timestamp mismatch: {partition.path}")
        counted += partition.row_count
    if counted != active.row_count:
        raise ValueError("partition row counts do not match publication row count")
    expected = {item.path for item in active.partitions}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*.parquet")}
    if actual != expected:
        raise ValueError("publication contains unmanifested or missing partitions")


def _publish(
    rows: Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]],
    *,
    output_root: str | Path,
    identity: DerivedPublicationIdentity,
    kind: PublicationKind,
    schema: pl.Schema,
    max_rows_per_part: int,
    feature_names: tuple[str, ...],
    event_counter: _EventEvidenceCounter | None = None,
) -> DerivedPublicationManifest:
    if isinstance(max_rows_per_part, bool) or not isinstance(max_rows_per_part, int):
        raise TypeError("max_rows_per_part must be an integer")
    if max_rows_per_part < 1:
        raise ValueError("max_rows_per_part must be positive")
    final, staging = _publication_paths(output_root, identity, kind)
    if final.exists():
        active = read_derived_manifest(final / MANIFEST_NAME)
        if active.identity != identity or active.publication_kind != kind:
            raise FileExistsError("derived publication exists with a different identity")
        verify_derived_publication(final, active)
        _verify_existing_input(
            rows,
            active=active,
            max_rows_per_part=max_rows_per_part,
            feature_names=feature_names,
            event_counter=event_counter,
        )
        return active
    if (staging / SUCCESS_NAME).exists():
        active = read_derived_manifest(staging / MANIFEST_NAME)
        if active.identity != identity or active.publication_kind != kind:
            raise FileExistsError("completed staging publication has a different identity")
        verify_derived_publication(staging, active)
        _verify_existing_input(
            rows,
            active=active,
            max_rows_per_part=max_rows_per_part,
            feature_names=feature_names,
            event_counter=event_counter,
        )
        staging.replace(final)
        return active

    staging.mkdir(parents=True, exist_ok=True)
    identity_path = staging / _IDENTITY_NAME
    identity_text = _json_text(asdict(identity)) + "\n"
    if identity_path.exists() and identity_path.read_text(encoding="utf-8") != identity_text:
        raise FileExistsError("incomplete publication has a different pinned identity")
    if not identity_path.exists():
        _atomic_write_text(identity_path, identity_text)
    for temporary in staging.rglob(".*.tmp"):
        temporary.unlink()

    records: list[DerivedPartitionRecord] = []
    buffer: list[dict[str, object]] = []
    current_partition: tuple[str, int, int] | None = None
    part_numbers: Counter[tuple[str, int, int]] = Counter()
    previous_key: tuple[object, ...] | None = None
    total = null_values = warmup_rows = max_buffered = 0
    minimum: datetime | None = None
    maximum: datetime | None = None
    content_digest = hashlib.sha256()

    def flush() -> None:
        nonlocal buffer
        if not buffer or current_partition is None:
            return
        part_numbers[current_partition] += 1
        part_number = part_numbers[current_partition]
        symbol, year, month = current_partition
        relative = (
            Path(f"symbol={_safe_component(symbol, 'symbol')}")
            / f"year={year:04d}"
            / f"month={month:02d}"
            / f"part-{part_number:06d}.parquet"
        )
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame = pl.DataFrame(buffer, schema=schema)
        temporary = destination.with_name(f".{destination.name}.tmp")
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        checksum = _sha256_file(temporary)
        if destination.exists():
            if _sha256_file(destination) != checksum:
                temporary.unlink()
                raise FileExistsError(
                    f"stale staging partition conflicts with repeated input: {relative.as_posix()}"
                )
            temporary.unlink()
        else:
            os.replace(temporary, destination)
        timestamp_column = _timestamp_column(kind)
        first = cast(datetime, frame[timestamp_column].min())
        last = cast(datetime, frame[timestamp_column].max())
        records.append(
            DerivedPartitionRecord(
                path=relative.as_posix(),
                sha256=checksum,
                row_count=frame.height,
                min_timestamp=_iso_utc(first),
                max_timestamp=_iso_utc(last),
            )
        )
        buffer = []

    for payload, partition_timestamp, key in rows:
        _require_utc(partition_timestamp, "partition timestamp")
        if previous_key is not None and key < previous_key:
            raise ValueError(f"out-of-order {kind} row")
        if key == previous_key:
            raise ValueError(f"duplicate {kind} row")
        previous_key = key
        content_digest.update(_canonical_row(payload))
        content_digest.update(b"\n")
        partition = (
            cast(str, payload["symbol"]),
            partition_timestamp.year,
            partition_timestamp.month,
        )
        if current_partition is not None and partition != current_partition:
            flush()
        current_partition = partition
        row_nulls = sum(payload[name] is None for name in feature_names)
        null_values += row_nulls
        warmup_rows += int(row_nulls > 0)
        buffer.append(payload)
        max_buffered = max(max_buffered, len(buffer))
        total += 1
        minimum = partition_timestamp if minimum is None else min(minimum, partition_timestamp)
        maximum = partition_timestamp if maximum is None else max(maximum, partition_timestamp)
        if len(buffer) == max_rows_per_part:
            flush()
    flush()

    ordered = tuple(sorted(records, key=lambda item: item.path))
    expected = {item.path for item in ordered}
    actual = {path.relative_to(staging).as_posix() for path in staging.rglob("*.parquet")}
    if actual != expected:
        raise RuntimeError("staging area contains stale or conflicting partitions")
    if event_counter is not None:
        evidence = DerivedEvidence(
            null_value_count=null_values,
            warmup_row_count=warmup_rows,
            event_count=event_counter.event_count,
            overlap_pair_count=event_counter.overlap_pairs,
            overlap_event_count=event_counter.overlap_events,
            maximum_concurrency=event_counter.maximum_concurrency,
            events_by_type=tuple(sorted(event_counter.by_type.items())),
            event_trigger_versions=tuple(sorted(event_counter.trigger_versions)),
        )
    else:
        evidence = DerivedEvidence(null_value_count=null_values, warmup_row_count=warmup_rows)
    schema_pairs = tuple(sorted((name, str(dtype)) for name, dtype in schema.items()))
    provisional = DerivedPublicationManifest(
        schema_version=1,
        publication_kind=kind,
        identity=identity,
        row_count=total,
        min_timestamp=_iso_utc(minimum) if minimum is not None else None,
        max_timestamp=_iso_utc(maximum) if maximum is not None else None,
        max_rows_per_part=max_rows_per_part,
        max_buffered_rows=max_buffered,
        parquet_schema=schema_pairs,
        evidence=evidence,
        partitions=ordered,
        content_sha256=content_digest.hexdigest(),
        publication_sha256="0" * 64,
    )
    publication_hash = hashlib.sha256(_canonical_json(provisional.logical_dict())).hexdigest()
    manifest = DerivedPublicationManifest(
        **{
            **{item.name: getattr(provisional, item.name) for item in fields(provisional)},
            "publication_sha256": publication_hash,
        }
    )
    _atomic_write_text(staging / MANIFEST_NAME, manifest.to_json())
    verify_derived_publication(staging, manifest, require_success=False)
    _atomic_write_text(staging / SUCCESS_NAME, publication_hash + "\n")
    verify_derived_publication(staging, manifest)
    identity_path.unlink()
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.replace(final)
    except FileExistsError:
        raise FileExistsError("derived publication was published concurrently") from None
    return manifest


def _feature_schema(registry: FeatureRegistry) -> pl.Schema:
    values: dict[str, pl.DataType | type[pl.DataType]] = {
        "timestamp": pl.Datetime("us", "UTC"),
        "information_cutoff": pl.Datetime("us", "UTC"),
        "symbol": pl.String,
        "timeframe": pl.String,
        "segment_id": pl.Int64,
        "dataset_version": pl.String,
        "config_version": pl.String,
        "profile_version": pl.String,
        "window_policy_id": pl.String,
        "feature_set_id": pl.String,
        "registry_id": pl.String,
    }
    for definition in registry.definitions:
        values[definition.name] = _feature_dtype(definition.value_kind)
    return pl.Schema(values)


def _verify_existing_input(
    rows: Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]],
    *,
    active: DerivedPublicationManifest,
    max_rows_per_part: int,
    feature_names: tuple[str, ...],
    event_counter: _EventEvidenceCounter | None,
) -> None:
    """Consume a repeated publication and prove its logical content is identical."""

    if active.max_rows_per_part != max_rows_per_part:
        raise FileExistsError("derived publication uses a different row-partition limit")
    digest = hashlib.sha256()
    previous_key: tuple[object, ...] | None = None
    total = null_values = warmup_rows = 0
    minimum: datetime | None = None
    maximum: datetime | None = None
    for payload, timestamp, key in rows:
        _require_utc(timestamp, "partition timestamp")
        if previous_key is not None and key < previous_key:
            raise ValueError(f"out-of-order {active.publication_kind} row")
        if key == previous_key:
            raise ValueError(f"duplicate {active.publication_kind} row")
        previous_key = key
        digest.update(_canonical_row(payload))
        digest.update(b"\n")
        row_nulls = sum(payload[name] is None for name in feature_names)
        null_values += row_nulls
        warmup_rows += int(row_nulls > 0)
        total += 1
        minimum = timestamp if minimum is None else min(minimum, timestamp)
        maximum = timestamp if maximum is None else max(maximum, timestamp)
    if event_counter is None:
        evidence = DerivedEvidence(
            null_value_count=null_values,
            warmup_row_count=warmup_rows,
        )
    else:
        evidence = DerivedEvidence(
            null_value_count=null_values,
            warmup_row_count=warmup_rows,
            event_count=event_counter.event_count,
            overlap_pair_count=event_counter.overlap_pairs,
            overlap_event_count=event_counter.overlap_events,
            maximum_concurrency=event_counter.maximum_concurrency,
            events_by_type=tuple(sorted(event_counter.by_type.items())),
            event_trigger_versions=tuple(sorted(event_counter.trigger_versions)),
        )
    actual = (
        total,
        _iso_utc(minimum) if minimum is not None else None,
        _iso_utc(maximum) if maximum is not None else None,
        evidence,
        digest.hexdigest(),
    )
    expected = (
        active.row_count,
        active.min_timestamp,
        active.max_timestamp,
        active.evidence,
        active.content_sha256,
    )
    if actual != expected:
        raise FileExistsError("derived publication content conflicts with the existing identity")


def _event_schema(registry: FeatureRegistry) -> pl.Schema:
    values: dict[str, pl.DataType | type[pl.DataType]] = {
        "event_id": pl.String,
        "event_type": pl.String,
        "start": pl.Datetime("us", "UTC"),
        "end": pl.Datetime("us", "UTC"),
        "information_cutoff": pl.Datetime("us", "UTC"),
        "symbol": pl.String,
        "timeframe": pl.String,
        "segment_id": pl.Int64,
        "dataset_version": pl.String,
        "config_version": pl.String,
        "profile_version": pl.String,
        "window_policy_id": pl.String,
        "feature_set_id": pl.String,
        "registry_id": pl.String,
        "registry_sha256": pl.String,
        "trigger_version": pl.String,
        "exploratory": pl.Boolean,
        "metadata_json": pl.String,
    }
    for definition in registry.definitions:
        values[definition.name] = _feature_dtype(definition.value_kind)
    return pl.Schema(values)


def _feature_dtype(kind: FeatureValueKind) -> pl.DataType | type[pl.DataType]:
    if kind is FeatureValueKind.FLOAT:
        return pl.Float64
    if kind is FeatureValueKind.INTEGER:
        return pl.Int64
    return pl.String


def _validate_registry_identity(
    identity: DerivedPublicationIdentity, registry: FeatureRegistry
) -> None:
    registry.audit_discovery()
    if identity.feature_set_id != registry.feature_set_id:
        raise ValueError("publication feature_set_id does not match the registry")
    if identity.feature_registry_sha256 != registry.sha256:
        raise ValueError("publication feature_registry_sha256 does not match the registry")


def _validate_feature_identity(row: FeatureRow, identity: DerivedPublicationIdentity) -> None:
    expected = {
        "dataset_version": identity.dataset_version,
        "config_version": identity.config_version,
        "profile_version": identity.profile_version,
        "window_policy_id": identity.window_policy_id,
        "feature_set_id": identity.feature_set_id,
    }
    for name, value in expected.items():
        if getattr(row, name) != value:
            raise ValueError(f"feature row {name} does not match publication identity")


def _validate_event_identity(
    event: Any, identity: DerivedPublicationIdentity, registry: FeatureRegistry
) -> None:
    expected = {
        "dataset_version": identity.dataset_version,
        "config_version": identity.config_version,
        "profile_version": identity.profile_version,
        "window_policy_id": identity.window_policy_id,
        "feature_set_id": identity.feature_set_id,
        "registry_id": registry.registry_id,
        "registry_sha256": identity.feature_registry_sha256,
    }
    for name, value in expected.items():
        if getattr(event, name) != value:
            raise ValueError(f"event {name} does not match publication identity")
    if event.information_cutoff != event.end:
        raise ValueError("event information_cutoff must equal its exclusive end")
    registry.validate_row(
        FeatureRow(
            timestamp=event.end - _timeframe_duration(event.timeframe),
            information_cutoff=event.end,
            symbol=event.symbol,
            timeframe=event.timeframe,
            segment_id=event.segment_id,
            dataset_version=event.dataset_version,
            config_version=event.config_version,
            profile_version=event.profile_version,
            window_policy_id=event.window_policy_id,
            feature_set_id=event.feature_set_id,
            registry_id=event.registry_id,
            values=event.feature_values,
        )
    )


def _timeframe_duration(timeframe: str) -> timedelta:
    from market_structure_lab.features.models import timeframe_duration

    return timeframe_duration(timeframe)


def _publication_paths(
    output_root: str | Path,
    identity: DerivedPublicationIdentity,
    kind: PublicationKind,
) -> tuple[Path, Path]:
    base = (
        Path(output_root)
        / f"dataset_version={identity.dataset_version}"
        / f"feature_set={identity.feature_set_id}"
    )
    return base / kind, base / f".{kind}.partial"


def _audit_names(values: Mapping[str, object]) -> None:
    for name, value in values.items():
        tokens = set(re.split(r"[^A-Za-z0-9]+", name.lower()))
        if tokens & _PROHIBITED_TOKENS:
            raise ValueError(f"prohibited outcome or future field: {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite value in field: {name}")


def _timestamp_column(kind: PublicationKind) -> str:
    return "timestamp" if kind == "features" else "information_cutoff"


def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or _PATH_COMPONENT.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe path component")
    return value


def _validated_partition_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("partition path must be a normalized relative path")
    if re.fullmatch(r"part-[0-9]{6}\.parquet", path.name) is None:
        raise ValueError("partition path must end in a numbered Parquet part")
    return path


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_utc(parsed, "manifest timestamp")
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    _require_utc(value, "timestamp")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _canonical_row(payload: Mapping[str, object]) -> bytes:
    normalized = {
        name: _iso_utc(value) if isinstance(value, datetime) else value
        for name, value in payload.items()
    }
    return _canonical_json(normalized)


def _json_text(payload: Mapping[str, object]) -> str:
    return _canonical_json(dict(payload)).decode("utf-8")


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
