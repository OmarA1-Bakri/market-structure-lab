"""Verified development-only 1h/4h aggregate publications for Phase 5 V2.

The module consumes only a sealed development-minute publication.  It never
opens a database, network source, or final-holdout payload.  Aggregation is
Decimal based, UTC aligned, gap aware, bounded before source-row iteration, and
published as canonical JSON/JSONL bytes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, ClassVar, Self
import weakref

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    iter_bounded_regular_lines,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceAvailabilityV2,
    ScopedSourceStatusV2,
    ValidationSourcePartitionV2,
    ValidationSourcePublicationV2,
    verify_validation_source_publication_v2,
)
from market_structure_lab.research.validation_v2_models import (
    AggregatePublicationIdentityV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    verify_development_read_boundary_v2,
)

_TARGET_MINUTES: dict[str, int] = {"1h": 60, "4h": 240}
_TARGET_TIMEFRAMES = tuple(_TARGET_MINUTES)
_MAX_CONTROL_BYTES = 16 * 1024 * 1024
_MAX_ROW_BYTES = 8 * 1024
_HARD_BUDGET_CEILINGS = {
    "max_source_rows": 50_000_000,
    "max_source_bytes": 64 * 1024 * 1024 * 1024,
    "max_parent_partitions": 100_000,
    "max_source_rows_per_chunk": 100_000,
    "max_members": 100_000,
    "max_aggregate_rows": 2_000_000,
    "max_rows_per_partition": 100_000,
    "max_output_bytes": 16 * 1024 * 1024 * 1024,
    "max_output_files": 100_000,
}
_FACTORY = object()
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class _AggregateRegistration:
    publication: weakref.ReferenceType[AggregatePublicationV2]
    publication_bytes: bytes
    publication_root: Path
    minute: ValidationSourcePublicationV2
    minute_bytes: bytes
    boundary: DevelopmentReadBoundaryV2
    boundary_bytes: bytes
    coverage: SourceCoveragePublicationV2
    split: DevelopmentSplitPublicationV2
    availability: ScopedSourceAvailabilityV2


_VERIFIED_AGGREGATES: dict[int, _AggregateRegistration] = {}


@dataclass(frozen=True, slots=True)
class AggregatePublicationBudgetV2:
    """Fixed positive bounds checked before aggregate source-row iteration."""

    max_source_rows: int
    max_source_bytes: int
    max_parent_partitions: int
    max_source_rows_per_chunk: int
    max_members: int
    max_aggregate_rows: int
    max_rows_per_partition: int
    max_output_bytes: int
    max_output_files: int

    def __post_init__(self) -> None:
        for label in self.__dataclass_fields__:
            value = getattr(self, label)
            ceiling = _HARD_BUDGET_CEILINGS[label]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > ceiling
            ):
                raise ValueError(f"{label} must be positive and within its fixed hard ceiling")
        if self.max_source_rows_per_chunk < max(_TARGET_MINUTES.values()):
            raise ValueError("source row chunk ceiling must hold one complete 4h bar")

    @property
    def budget_sha256(self) -> str:
        return hash_json("phase5-validation-aggregate-budget-v2", self.to_dict())

    def to_dict(self) -> dict[str, int]:
        return {label: getattr(self, label) for label in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class AggregatePartitionV2:
    path: str
    sha256: str
    byte_count: int
    row_count: int
    symbol: str
    interval_index: int
    target_timeframe: str
    segment_id: int
    min_timestamp: str
    max_timestamp: str

    def __post_init__(self) -> None:
        relative = Path(self.path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != self.path
            or not self.path.endswith(".jsonl")
        ):
            raise ValueError("aggregate partition path is invalid")
        _require_sha256(self.sha256, "aggregate partition sha256")
        _positive(self.byte_count, "aggregate partition byte_count")
        _positive(self.row_count, "aggregate partition row_count")
        _nonnegative(self.interval_index, "aggregate partition interval_index")
        _nonnegative(self.segment_id, "aggregate partition segment_id")
        if self.target_timeframe not in _TARGET_TIMEFRAMES:
            raise ValueError("aggregate partition timeframe is invalid")
        minimum = _parse_utc(self.min_timestamp, "aggregate partition minimum")
        maximum = _parse_utc(self.max_timestamp, "aggregate partition maximum")
        if maximum < minimum:
            raise ValueError("aggregate partition timestamp order is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "row_count": self.row_count,
            "symbol": self.symbol,
            "interval_index": self.interval_index,
            "target_timeframe": self.target_timeframe,
            "segment_id": self.segment_id,
            "min_timestamp": self.min_timestamp,
            "max_timestamp": self.max_timestamp,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        return cls(**_exact_mapping(payload, set(cls.__dataclass_fields__), "aggregate partition"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AggregateMemberV2:
    symbol: str
    interval_index: int
    interval_start: str
    interval_end: str
    target_timeframe: str
    source_partition_paths: tuple[str, ...]
    source_partition_set_sha256: str
    source_row_count: int
    ordered_source_row_sha256: str
    segment_count: int
    row_count: int
    byte_count: int
    partitions: tuple[AggregatePartitionV2, ...]
    member_sha256: str

    def __post_init__(self) -> None:
        _nonnegative(self.interval_index, "aggregate member interval_index")
        start = _parse_utc(self.interval_start, "aggregate member interval start")
        end = _parse_utc(self.interval_end, "aggregate member interval end")
        if end <= start:
            raise ValueError("aggregate member interval is invalid")
        if self.target_timeframe not in _TARGET_TIMEFRAMES:
            raise ValueError("aggregate member timeframe is invalid")
        if not self.source_partition_paths or self.source_partition_paths != tuple(
            sorted(set(self.source_partition_paths))
        ):
            raise ValueError("aggregate source partition paths must be uniquely ordered")
        _require_sha256(self.source_partition_set_sha256, "source partition set sha256")
        _require_sha256(self.ordered_source_row_sha256, "ordered source row sha256")
        _positive(self.source_row_count, "aggregate member source_row_count")
        _positive(self.segment_count, "aggregate member segment_count")
        _positive(self.row_count, "aggregate member row_count")
        _positive(self.byte_count, "aggregate member byte_count")
        paths = tuple(item.path for item in self.partitions)
        if paths != tuple(sorted(set(paths))) or not paths:
            raise ValueError("aggregate member partitions must be uniquely ordered")
        if sum(item.row_count for item in self.partitions) != self.row_count:
            raise ValueError("aggregate member partition row count differs")
        if sum(item.byte_count for item in self.partitions) != self.byte_count:
            raise ValueError("aggregate member partition byte count differs")
        if {item.segment_id for item in self.partitions} != set(range(self.segment_count)):
            raise ValueError("aggregate member segment partition grid differs")
        expected = hash_json("phase5-validation-aggregate-member-v2", self._payload())
        if self.member_sha256 != expected:
            raise ValueError("aggregate member identity differs")

    def _payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "interval_index": self.interval_index,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "target_timeframe": self.target_timeframe,
            "source_partition_paths": list(self.source_partition_paths),
            "source_partition_set_sha256": self.source_partition_set_sha256,
            "source_row_count": self.source_row_count,
            "ordered_source_row_sha256": self.ordered_source_row_sha256,
            "segment_count": self.segment_count,
            "row_count": self.row_count,
            "byte_count": self.byte_count,
            "partitions": [item.to_dict() for item in self.partitions],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "member_sha256": self.member_sha256}

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "symbol",
                "interval_index",
                "interval_start",
                "interval_end",
                "target_timeframe",
                "source_partition_paths",
                "source_partition_set_sha256",
                "source_row_count",
                "ordered_source_row_sha256",
                "segment_count",
                "row_count",
                "byte_count",
                "partitions",
                "member_sha256",
            },
            "aggregate member",
        )
        raw_paths = values["source_partition_paths"]
        raw_partitions = values["partitions"]
        if not isinstance(raw_paths, list) or not isinstance(raw_partitions, list):
            raise TypeError("aggregate member path and partition values must be lists")
        return cls(
            symbol=values["symbol"],  # type: ignore[arg-type]
            interval_index=values["interval_index"],  # type: ignore[arg-type]
            interval_start=values["interval_start"],  # type: ignore[arg-type]
            interval_end=values["interval_end"],  # type: ignore[arg-type]
            target_timeframe=values["target_timeframe"],  # type: ignore[arg-type]
            source_partition_paths=tuple(raw_paths),  # type: ignore[arg-type]
            source_partition_set_sha256=values["source_partition_set_sha256"],  # type: ignore[arg-type]
            source_row_count=values["source_row_count"],  # type: ignore[arg-type]
            ordered_source_row_sha256=values["ordered_source_row_sha256"],  # type: ignore[arg-type]
            segment_count=values["segment_count"],  # type: ignore[arg-type]
            row_count=values["row_count"],  # type: ignore[arg-type]
            byte_count=values["byte_count"],  # type: ignore[arg-type]
            partitions=tuple(AggregatePartitionV2.from_dict(item) for item in raw_partitions),
            member_sha256=values["member_sha256"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class AggregateRowV2:
    timestamp: datetime
    symbol: str
    target_timeframe: str
    interval_index: int
    segment_id: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_row_count: int
    source_first_row_sha256: str
    source_last_row_sha256: str
    ordered_source_rows_sha256: str
    row_sha256: str

    def __post_init__(self) -> None:
        _require_utc_aligned(self.timestamp, self.target_timeframe)
        _nonnegative(self.interval_index, "aggregate row interval_index")
        _nonnegative(self.segment_id, "aggregate row segment_id")
        expected_count = _TARGET_MINUTES.get(self.target_timeframe)
        if self.source_row_count != expected_count:
            raise ValueError("aggregate row source count is incomplete")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("aggregate values must be finite Decimal values")
        if (
            min(values) < 0
            or self.low > min(self.open, self.close)
            or self.high < max(self.open, self.close)
        ):
            raise ValueError("aggregate row violates OHLC relationships")
        for value, label in (
            (self.source_first_row_sha256, "source_first_row_sha256"),
            (self.source_last_row_sha256, "source_last_row_sha256"),
            (self.ordered_source_rows_sha256, "ordered_source_rows_sha256"),
        ):
            _require_sha256(value, label)
        expected = hash_json("phase5-validation-aggregate-row-v2", self._payload())
        if self.row_sha256 != expected:
            raise ValueError("aggregate row identity differs")

    def _payload(self) -> dict[str, object]:
        return {
            "timestamp": _utc_text(self.timestamp),
            "symbol": self.symbol,
            "target_timeframe": self.target_timeframe,
            "interval_index": self.interval_index,
            "segment_id": self.segment_id,
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": _decimal_text(self.volume),
            "source_row_count": self.source_row_count,
            "source_first_row_sha256": self.source_first_row_sha256,
            "source_last_row_sha256": self.source_last_row_sha256,
            "ordered_source_rows_sha256": self.ordered_source_rows_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "row_sha256": self.row_sha256}

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "timestamp",
                "symbol",
                "target_timeframe",
                "interval_index",
                "segment_id",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source_row_count",
                "source_first_row_sha256",
                "source_last_row_sha256",
                "ordered_source_rows_sha256",
                "row_sha256",
            },
            "aggregate row",
        )
        return cls(
            timestamp=_parse_utc(values["timestamp"], "aggregate row timestamp"),
            symbol=values["symbol"],  # type: ignore[arg-type]
            target_timeframe=values["target_timeframe"],  # type: ignore[arg-type]
            interval_index=values["interval_index"],  # type: ignore[arg-type]
            segment_id=values["segment_id"],  # type: ignore[arg-type]
            open=_parse_decimal(values["open"], "aggregate open"),
            high=_parse_decimal(values["high"], "aggregate high"),
            low=_parse_decimal(values["low"], "aggregate low"),
            close=_parse_decimal(values["close"], "aggregate close"),
            volume=_parse_decimal(values["volume"], "aggregate volume"),
            source_row_count=values["source_row_count"],  # type: ignore[arg-type]
            source_first_row_sha256=values["source_first_row_sha256"],  # type: ignore[arg-type]
            source_last_row_sha256=values["source_last_row_sha256"],  # type: ignore[arg-type]
            ordered_source_rows_sha256=values["ordered_source_rows_sha256"],  # type: ignore[arg-type]
            row_sha256=values["row_sha256"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class AggregatePublicationV2:
    """Factory-sealed aggregate publication and original-byte read capability."""

    status: ScopedSourceStatusV2
    coverage_identity: str
    split_identity: str
    boundary_sha256: str
    minute_source_identity: str
    minute_publication_sha256: str
    minute_audit_publication_sha256: str
    reconciliation_identity_sha256: str
    raw_dump_identity_sha256: str
    source_mapping_version: str
    admitted_source_identity: str | None
    origin_kind: str | None
    origin_sha256: str | None
    origin_proof_sha256: str | None
    allowed_symbols: tuple[str, ...]
    allowed_intervals: tuple[tuple[str, str], ...]
    target_timeframes: tuple[str, ...]
    parent_partition_bindings: tuple[tuple[str, str, int, int, str, str], ...]
    budget_sha256: str
    members: tuple[AggregateMemberV2, ...]
    row_count: int
    byte_count: int
    failure: str | None
    final_scope_attempts: int
    final_rows: int
    final_access_records: int
    aggregate_identity: AggregatePublicationIdentityV2
    publication_root: Path = field(repr=False, compare=False)
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None
    _schema: ClassVar[str] = "phase5-validation-development-aggregates-v2"

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY:
            raise TypeError("AggregatePublicationV2 requires its verifier factory")
        if not isinstance(self.status, ScopedSourceStatusV2):
            raise TypeError("aggregate publication status is invalid")
        for value, label in (
            (self.boundary_sha256, "boundary_sha256"),
            (self.minute_publication_sha256, "minute_publication_sha256"),
            (self.minute_audit_publication_sha256, "minute_audit_publication_sha256"),
            (self.reconciliation_identity_sha256, "reconciliation_identity_sha256"),
            (self.raw_dump_identity_sha256, "raw_dump_identity_sha256"),
            (self.budget_sha256, "budget_sha256"),
        ):
            _require_sha256(value, label)
        if self.target_timeframes != _TARGET_TIMEFRAMES:
            raise ValueError("aggregate publication target timeframe grid differs")
        if self.allowed_symbols != tuple(sorted(set(self.allowed_symbols))):
            raise ValueError("aggregate allowed symbols must be uniquely ordered")
        if self.final_scope_attempts or self.final_rows or self.final_access_records:
            raise ValueError("aggregate publication cannot contain final access")
        binding_paths = tuple(item[0] for item in self.parent_partition_bindings)
        if binding_paths != tuple(sorted(set(binding_paths))):
            raise ValueError("aggregate parent partitions must be uniquely ordered")
        for (
            path,
            sha256,
            byte_count,
            row_count,
            source_identity,
            proof,
        ) in self.parent_partition_bindings:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("aggregate parent partition path is invalid")
            _require_sha256(sha256, "aggregate parent partition sha256")
            _positive(byte_count, "aggregate parent partition byte_count")
            _positive(row_count, "aggregate parent partition row_count")
            if not source_identity:
                raise ValueError("aggregate parent source identity is required")
            _require_sha256(proof, "aggregate parent origin proof")
        if self.status is ScopedSourceStatusV2.AVAILABLE:
            expected_grid = tuple(
                (symbol, index, timeframe)
                for symbol in self.allowed_symbols
                for index in range(len(self.allowed_intervals))
                for timeframe in self.target_timeframes
            )
            actual_grid = tuple(
                (member.symbol, member.interval_index, member.target_timeframe)
                for member in self.members
            )
            if actual_grid != expected_grid:
                raise ValueError("aggregate member grid is missing, extra, duplicate, or reordered")
            if self.failure is not None or not self.members:
                raise ValueError("available aggregate publication is incomplete")
        elif (
            self.failure != "scoped_source_unavailable"
            or self.members
            or self.row_count
            or self.byte_count
            or self.parent_partition_bindings
        ):
            raise ValueError("unavailable aggregate publication is inconsistent")
        if self.row_count != sum(item.row_count for item in self.members):
            raise ValueError("aggregate publication row count differs")
        if self.byte_count != sum(item.byte_count for item in self.members):
            raise ValueError("aggregate publication byte count differs")
        payload = self._identity_payload()
        if self.aggregate_identity != AggregatePublicationIdentityV2.from_payload(payload):
            raise ValueError("aggregate publication identity differs")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("aggregate publication canonical bytes differ")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "coverage_identity": self.coverage_identity,
            "split_identity": self.split_identity,
            "boundary_sha256": self.boundary_sha256,
            "minute_source_identity": self.minute_source_identity,
            "minute_publication_sha256": self.minute_publication_sha256,
            "minute_audit_publication_sha256": self.minute_audit_publication_sha256,
            "reconciliation_identity_sha256": self.reconciliation_identity_sha256,
            "raw_dump_identity_sha256": self.raw_dump_identity_sha256,
            "source_mapping_version": self.source_mapping_version,
            "admitted_source_identity": self.admitted_source_identity,
            "origin_kind": self.origin_kind,
            "origin_sha256": self.origin_sha256,
            "origin_proof_sha256": self.origin_proof_sha256,
            "allowed_symbols": list(self.allowed_symbols),
            "allowed_intervals": [
                {"start": start, "end": end} for start, end in self.allowed_intervals
            ],
            "target_timeframes": list(self.target_timeframes),
            "parent_partition_bindings": [
                {
                    "path": path,
                    "sha256": sha256,
                    "byte_count": byte_count,
                    "row_count": row_count,
                    "source_identity": source_identity,
                    "origin_proof_sha256": proof,
                }
                for path, sha256, byte_count, row_count, source_identity, proof in self.parent_partition_bindings
            ],
            "budget_sha256": self.budget_sha256,
            "members": [item.to_dict() for item in self.members],
            "row_count": self.row_count,
            "byte_count": self.byte_count,
            "failure": self.failure,
            "final_scope_attempts": self.final_scope_attempts,
            "final_rows": self.final_rows,
            "final_access_records": self.final_access_records,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            **self._identity_payload(),
            "aggregate_identity": self.aggregate_identity.value,
        }


def publish_validation_aggregates_v2(
    *,
    minute_publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    output_root: Path,
    budget: AggregatePublicationBudgetV2,
) -> AggregatePublicationV2:
    """Publish exact development-only 1h/4h aggregates from sealed minute bytes."""

    if not isinstance(minute_publication, ValidationSourcePublicationV2):
        raise TypeError("minute publication must be a verified original publication")
    if not isinstance(budget, AggregatePublicationBudgetV2):
        raise TypeError("aggregate budget must be AggregatePublicationBudgetV2")
    _preflight(minute_publication, boundary, budget)
    verify_development_read_boundary_v2(boundary, coverage, split)
    verify_validation_source_publication_v2(
        minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    _verify_parent_scope(minute_publication, coverage, split, boundary, availability)
    output_root = Path(output_root)
    require_regular_directory(output_root.parent)
    if path_exists_no_follow(output_root):
        raise FileExistsError(f"refusing existing aggregate publication: {output_root}")
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.", suffix=".tmp", dir=output_root.parent)
    )
    try:
        members: tuple[AggregateMemberV2, ...]
        if minute_publication.status is ScopedSourceStatusV2.UNAVAILABLE:
            members = ()
            failure = "scoped_source_unavailable"
        else:
            members = _build_members(
                minute_publication=minute_publication,
                boundary=boundary,
                stage=stage,
                budget=budget,
            )
            failure = None
        publication = _make_publication(
            minute_publication=minute_publication,
            coverage=coverage,
            split=split,
            boundary=boundary,
            output_root=output_root,
            budget=budget,
            members=members,
            failure=failure,
        )
        if len(publication.canonical_bytes) > _MAX_CONTROL_BYTES:
            raise ValueError("aggregate control publication exceeds hard byte ceiling")
        _write_no_clobber(stage / "publication.json", publication.canonical_bytes)
        _write_no_clobber(
            stage / "_SUCCESS", f"{publication.aggregate_identity.value}\n".encode("ascii")
        )
        _publish_stage_no_clobber(stage, output_root)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    _register(
        publication,
        minute_publication=minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    return verify_validation_aggregate_publication_v2(
        publication,
        minute_publication=minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )


def verify_validation_aggregate_publication_v2(
    publication: AggregatePublicationV2,
    *,
    minute_publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> AggregatePublicationV2:
    """Reopen the exact parent and every original aggregate publication byte."""

    if not isinstance(publication, AggregatePublicationV2):
        raise TypeError("aggregate publication must be verifier-issued")
    registered = _validate_registered_publication(publication)
    if (
        registered.minute is not minute_publication
        or registered.minute_bytes != minute_publication.canonical_bytes
        or registered.boundary is not boundary
        or registered.boundary_bytes != boundary.canonical_bytes
        or registered.coverage is not coverage
        or registered.split is not split
        or registered.availability is not availability
    ):
        raise ValueError("aggregate publication parent capability is not the original")
    verify_development_read_boundary_v2(boundary, coverage, split)
    verify_validation_source_publication_v2(
        minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    _verify_parent_scope(minute_publication, coverage, split, boundary, availability)
    if (
        read_bounded_regular(publication.publication_root / "publication.json", _MAX_CONTROL_BYTES)
        != publication.canonical_bytes
    ):
        raise ValueError("aggregate publication original bytes changed")
    success = read_bounded_regular(publication.publication_root / "_SUCCESS", 128)
    if success != f"{publication.aggregate_identity.value}\n".encode("ascii"):
        raise ValueError("aggregate success marker changed")
    _verify_publication_bindings(publication, minute_publication, coverage, split, boundary)
    _verify_rederived_members(publication, minute_publication, boundary)
    expected_files = {"publication.json", "_SUCCESS"}
    for member in publication.members:
        for partition in member.partitions:
            expected_files.add(partition.path)
    scan_limit = max(32, len(expected_files) * 8)
    if (
        set(
            bounded_regular_files(
                publication.publication_root,
                maximum=scan_limit,
            )
        )
        != expected_files
    ):
        raise ValueError("aggregate publication has missing or extra artifacts")
    return publication


def load_validation_aggregate_publication_v2(
    *,
    publication_root: Path,
    expected_aggregate_identity: AggregatePublicationIdentityV2,
    minute_publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> AggregatePublicationV2:
    """Load only a publication anchored by its previously frozen identity."""

    if not isinstance(expected_aggregate_identity, AggregatePublicationIdentityV2):
        raise TypeError("expected aggregate identity must be typed")
    verify_validation_source_publication_v2(
        minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    root = Path(publication_root)
    canonical_bytes = read_bounded_regular(root / "publication.json", _MAX_CONTROL_BYTES)
    publication = _publication_from_dict(
        _decode_json(canonical_bytes, "aggregate publication"),
        publication_root=root,
        canonical_bytes=canonical_bytes,
    )
    if publication.aggregate_identity != expected_aggregate_identity:
        raise ValueError("aggregate publication differs from expected frozen identity")
    _register(
        publication,
        minute_publication=minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    return verify_validation_aggregate_publication_v2(
        publication,
        minute_publication=minute_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )


def iter_verified_aggregate_rows_v2(
    publication: AggregatePublicationV2,
    *,
    budget: AggregatePublicationBudgetV2,
) -> Iterator[AggregateRowV2]:
    """Iterate bounded original aggregate bytes from a verifier-issued capability."""

    registered = _validate_registered_publication(publication)
    verify_validation_aggregate_publication_v2(
        publication,
        minute_publication=registered.minute,
        coverage=registered.coverage,
        split=registered.split,
        boundary=registered.boundary,
        availability=registered.availability,
    )
    if publication.row_count > budget.max_aggregate_rows:
        raise ValueError("aggregate read exceeds row budget")
    if publication.byte_count > budget.max_output_bytes:
        raise ValueError("aggregate read exceeds byte budget")
    if sum(len(member.partitions) for member in publication.members) > budget.max_output_files:
        raise ValueError("aggregate read exceeds file budget")
    if (
        read_bounded_regular(publication.publication_root / "publication.json", _MAX_CONTROL_BYTES)
        != publication.canonical_bytes
    ):
        raise ValueError("aggregate publication original bytes changed")
    for member in publication.members:
        for partition in member.partitions:
            if partition.row_count > budget.max_rows_per_partition:
                raise ValueError("aggregate partition exceeds row buffer budget")
            path = publication.publication_root / partition.path
            if sha256_regular(path) != partition.sha256:
                raise ValueError("aggregate partition checksum changed")
            count = 0
            for line in iter_bounded_regular_lines(
                path,
                maximum_lines=partition.row_count,
                maximum_line_bytes=_MAX_ROW_BYTES,
            ):
                count += 1
                row = AggregateRowV2.from_dict(_decode_json(line, "aggregate row"))
                _verify_partition_rows((row,), partition, member, None)
                yield row
            if count != partition.row_count:
                raise ValueError("aggregate partition row count changed")


@dataclass(frozen=True, slots=True)
class _SourceRow:
    timestamp: datetime
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    row_sha256: str


def _preflight(
    publication: ValidationSourcePublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    budget: AggregatePublicationBudgetV2,
) -> None:
    if publication.row_count > budget.max_source_rows:
        raise ValueError("source row demand exceeds aggregate budget ceiling")
    if publication.byte_count > budget.max_source_bytes:
        raise ValueError("source byte demand exceeds aggregate budget ceiling")
    if len(publication.partitions) > budget.max_parent_partitions:
        raise ValueError("parent partition demand exceeds aggregate budget ceiling")
    member_count = len(boundary.allowed_symbols) * len(boundary.allowed_intervals) * 2
    if member_count > budget.max_members:
        raise ValueError("aggregate member grid exceeds budget ceiling")
    estimated_rows = publication.row_count // 60 + publication.row_count // 240
    if estimated_rows > budget.max_aggregate_rows:
        raise ValueError("aggregate row demand exceeds budget ceiling")
    if publication.status is ScopedSourceStatusV2.AVAILABLE:
        estimated_segments = publication.row_count // 240
        estimated_files = max(member_count, estimated_rows, estimated_segments * 2)
        if estimated_files > budget.max_output_files:
            raise ValueError("aggregate output file demand exceeds budget ceiling")
        conservative_bytes = estimated_rows * _MAX_ROW_BYTES + _MAX_CONTROL_BYTES
        if conservative_bytes > budget.max_output_bytes:
            raise ValueError("aggregate output byte demand exceeds budget ceiling")


def _verify_parent_scope(
    publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> None:
    expected_intervals = tuple(
        (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
    )
    if (
        publication.coverage_identity != coverage.coverage_identity
        or publication.split_identity != split.split_identity
        or publication.boundary_sha256 != boundary.boundary_sha256
        or publication.source_availability_sha256 != availability.availability_sha256
        or publication.allowed_symbols != boundary.allowed_symbols
        or publication.allowed_intervals != expected_intervals
        or publication.allowed_timeframe != "1m"
        or publication.final_scope_attempts
        or publication.final_rows
        or publication.final_access_records
    ):
        raise ValueError("minute publication has stale, mixed, or final-scope parent binding")
    expected_groups = tuple(
        (symbol, index)
        for symbol in boundary.allowed_symbols
        for index in range(len(boundary.allowed_intervals))
    )
    actual_groups = tuple(
        sorted({(item.symbol, item.interval_index) for item in publication.partitions})
    )
    if publication.status is ScopedSourceStatusV2.AVAILABLE and actual_groups != expected_groups:
        raise ValueError("minute publication partition grid is missing or extra")


def _build_members(
    *,
    minute_publication: ValidationSourcePublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    stage: Path,
    budget: AggregatePublicationBudgetV2,
) -> tuple[AggregateMemberV2, ...]:
    grouped: dict[tuple[str, int], list[ValidationSourcePartitionV2]] = {}
    for partition in minute_publication.partitions:
        grouped.setdefault((partition.symbol, partition.interval_index), []).append(partition)
    members: list[AggregateMemberV2] = []
    total_rows = 0
    total_bytes = 0
    total_files = 0
    for symbol in boundary.allowed_symbols:
        for interval_index, interval in enumerate(boundary.allowed_intervals):
            group_members = _build_group_members(
                minute_publication=minute_publication,
                parent_partitions=tuple(grouped[(symbol, interval_index)]),
                symbol=symbol,
                interval_index=interval_index,
                interval_start=interval.start,
                interval_end=interval.end,
                stage=stage,
                budget=budget,
            )
            for member in group_members:
                total_rows += member.row_count
                total_bytes += member.byte_count
                total_files += len(member.partitions)
                if total_rows > budget.max_aggregate_rows:
                    raise ValueError("aggregate rows exceed budget ceiling")
                if total_bytes > budget.max_output_bytes or total_files > budget.max_output_files:
                    raise ValueError("aggregate output exceeds byte or file budget ceiling")
                members.append(member)
    return tuple(members)


class _MemberWriter:
    """Bounded partition writer for one aggregate member."""

    def __init__(
        self,
        *,
        stage: Path,
        symbol: str,
        interval_index: int,
        timeframe: str,
        max_rows: int,
    ) -> None:
        self.stage = stage
        self.symbol = symbol
        self.interval_index = interval_index
        self.timeframe = timeframe
        self.max_rows = max_rows
        self.partitions: list[AggregatePartitionV2] = []
        self.buffer: list[AggregateRowV2] = []
        self.segment_id = 0
        self.partition_index = 0
        self.row_count = 0
        self.byte_count = 0

    def append(self, row: AggregateRowV2) -> None:
        self.buffer.append(row)
        self.row_count += 1
        if len(self.buffer) == self.max_rows:
            self.flush()

    def finish_segment(self) -> None:
        self.flush()
        self.segment_id += 1
        self.partition_index = 0

    def flush(self) -> None:
        if not self.buffer:
            return
        relative = (
            Path("partitions")
            / f"symbol={self.symbol}"
            / f"interval={self.interval_index:04d}"
            / f"timeframe={self.timeframe}"
            / f"segment={self.segment_id:06d}"
            / f"part={self.partition_index:06d}.jsonl"
        )
        content = b"".join(_aggregate_row_bytes(row) for row in self.buffer)
        destination = self.stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_no_clobber(destination, content)
        self.partitions.append(
            AggregatePartitionV2(
                path=relative.as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
                byte_count=len(content),
                row_count=len(self.buffer),
                symbol=self.symbol,
                interval_index=self.interval_index,
                target_timeframe=self.timeframe,
                segment_id=self.segment_id,
                min_timestamp=_utc_text(self.buffer[0].timestamp),
                max_timestamp=_utc_text(self.buffer[-1].timestamp),
            )
        )
        self.byte_count += len(content)
        self.buffer.clear()
        self.partition_index += 1


def _build_group_members(
    *,
    minute_publication: ValidationSourcePublicationV2,
    parent_partitions: tuple[ValidationSourcePartitionV2, ...],
    symbol: str,
    interval_index: int,
    interval_start: datetime,
    interval_end: datetime,
    stage: Path,
    budget: AggregatePublicationBudgetV2,
) -> tuple[AggregateMemberV2, ...]:
    writers = {
        timeframe: _MemberWriter(
            stage=stage,
            symbol=symbol,
            interval_index=interval_index,
            timeframe=timeframe,
            max_rows=budget.max_rows_per_partition,
        )
        for timeframe in _TARGET_TIMEFRAMES
    }
    rolling = hashlib.sha256(b"phase5-validation-aggregate-source-row-order-v2\x00")
    buffer: list[_SourceRow] = []
    source_count = 0
    segment_count = 0
    previous: datetime | None = None

    def finish_segment() -> None:
        nonlocal segment_count
        if buffer:
            raise ValueError("partial or non-UTC-aligned terminal aggregate segment")
        for writer in writers.values():
            writer.finish_segment()
        segment_count += 1

    source_rows = _iter_group_rows(
        minute_publication,
        parent_partitions,
        symbol=symbol,
        interval_index=interval_index,
        interval_start=interval_start,
        interval_end=interval_end,
        budget=budget,
    )
    for chunk in _iter_chunks(
        source_rows,
        maximum=budget.max_source_rows_per_chunk,
    ):
        for row in chunk:
            if previous is None or row.timestamp != previous + timedelta(minutes=1):
                if previous is not None:
                    finish_segment()
                if row.timestamp.minute or row.timestamp.hour % 4:
                    raise ValueError("partial or non-UTC-aligned aggregate segment")
            rolling.update(bytes.fromhex(row.row_sha256))
            source_count += 1
            buffer.append(row)
            previous = row.timestamp
            if len(buffer) == 240:
                block = tuple(buffer)
                for timeframe, writer in writers.items():
                    for aggregate in _aggregate_segment(
                        block, timeframe, interval_index, segment_count
                    ):
                        writer.append(aggregate)
                buffer.clear()
    if source_count < 1:
        raise ValueError("aggregate member source is empty")
    finish_segment()
    source_paths = tuple(item.path for item in parent_partitions)
    source_partition_set = hash_json(
        "phase5-validation-aggregate-parent-partition-set-v2",
        [item.to_dict() for item in parent_partitions],
    )
    ordered_source = rolling.hexdigest()
    members = []
    for timeframe in _TARGET_TIMEFRAMES:
        writer = writers[timeframe]
        payload = {
            "symbol": symbol,
            "interval_index": interval_index,
            "interval_start": _utc_text(interval_start),
            "interval_end": _utc_text(interval_end),
            "target_timeframe": timeframe,
            "source_partition_paths": list(source_paths),
            "source_partition_set_sha256": source_partition_set,
            "source_row_count": source_count,
            "ordered_source_row_sha256": ordered_source,
            "segment_count": segment_count,
            "row_count": writer.row_count,
            "byte_count": writer.byte_count,
            "partitions": [item.to_dict() for item in writer.partitions],
        }
        members.append(
            AggregateMemberV2(
                symbol=symbol,
                interval_index=interval_index,
                interval_start=_utc_text(interval_start),
                interval_end=_utc_text(interval_end),
                target_timeframe=timeframe,
                source_partition_paths=source_paths,
                source_partition_set_sha256=source_partition_set,
                source_row_count=source_count,
                ordered_source_row_sha256=ordered_source,
                segment_count=segment_count,
                row_count=writer.row_count,
                byte_count=writer.byte_count,
                partitions=tuple(writer.partitions),
                member_sha256=hash_json("phase5-validation-aggregate-member-v2", payload),
            )
        )
    return tuple(members)


def _iter_chunks(rows: Iterator[Any], *, maximum: int) -> Iterator[tuple[Any, ...]]:
    while True:
        chunk = []
        try:
            for _ in range(maximum):
                chunk.append(next(rows))
        except StopIteration:
            if chunk:
                yield tuple(chunk)
            return
        yield tuple(chunk)


def _iter_group_rows(
    publication: ValidationSourcePublicationV2,
    partitions: tuple[ValidationSourcePartitionV2, ...],
    *,
    symbol: str,
    interval_index: int,
    interval_start: datetime,
    interval_end: datetime,
    budget: AggregatePublicationBudgetV2,
) -> Iterator[_SourceRow]:
    previous: datetime | None = None
    count = 0
    for partition in partitions:
        if (
            partition.symbol != symbol
            or partition.interval_index != interval_index
            or partition.source_identity != publication.admitted_source_identity
            or partition.origin_proof_sha256 != publication.origin_proof_sha256
        ):
            raise ValueError("minute source parent or origin binding changed")
        path = publication.publication_root / partition.path
        for line in iter_bounded_regular_lines(
            path,
            maximum_lines=partition.row_count,
            maximum_line_bytes=_MAX_ROW_BYTES,
        ):
            values = _exact_mapping(
                _decode_json(line, "canonical minute row"),
                {
                    "timestamp",
                    "symbol",
                    "timeframe",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "source_identity",
                    "origin_proof_sha256",
                },
                "canonical minute row",
            )
            timestamp = _parse_utc(values["timestamp"], "minute timestamp")
            if (
                values["symbol"] != symbol
                or values["timeframe"] != "1m"
                or values["source_identity"] != publication.admitted_source_identity
                or values["origin_proof_sha256"] != publication.origin_proof_sha256
                or not interval_start <= timestamp < interval_end
                or (previous is not None and timestamp <= previous)
            ):
                raise ValueError("minute source row scope, order, or origin changed")
            count += 1
            previous = timestamp
            yield _SourceRow(
                timestamp=timestamp,
                symbol=symbol,
                open=_parse_decimal(values["open"], "minute open"),
                high=_parse_decimal(values["high"], "minute high"),
                low=_parse_decimal(values["low"], "minute low"),
                close=_parse_decimal(values["close"], "minute close"),
                volume=_parse_decimal(values["volume"], "minute volume"),
                row_sha256=hashlib.sha256(line + b"\n").hexdigest(),
            )
    if count < 1:
        raise ValueError("aggregate member source is empty")


def _aggregate_segment(
    segment: tuple[_SourceRow, ...],
    timeframe: str,
    interval_index: int,
    segment_id: int,
) -> Iterator[AggregateRowV2]:
    width = _TARGET_MINUTES[timeframe]
    if len(segment) % width:
        raise ValueError("partial terminal aggregate bar")
    for start in range(0, len(segment), width):
        source = segment[start : start + width]
        digests = [item.row_sha256 for item in source]
        payload = {
            "timestamp": _utc_text(source[0].timestamp),
            "symbol": source[0].symbol,
            "target_timeframe": timeframe,
            "interval_index": interval_index,
            "segment_id": segment_id,
            "open": _decimal_text(source[0].open),
            "high": _decimal_text(max(item.high for item in source)),
            "low": _decimal_text(min(item.low for item in source)),
            "close": _decimal_text(source[-1].close),
            "volume": _decimal_text(sum((item.volume for item in source), Decimal(0))),
            "source_row_count": width,
            "source_first_row_sha256": digests[0],
            "source_last_row_sha256": digests[-1],
            "ordered_source_rows_sha256": hash_json(
                "phase5-validation-aggregate-bar-source-order-v2", digests
            ),
        }
        yield AggregateRowV2(
            timestamp=source[0].timestamp,
            symbol=source[0].symbol,
            target_timeframe=timeframe,
            interval_index=interval_index,
            segment_id=segment_id,
            open=source[0].open,
            high=max(item.high for item in source),
            low=min(item.low for item in source),
            close=source[-1].close,
            volume=sum((item.volume for item in source), Decimal(0)),
            source_row_count=width,
            source_first_row_sha256=digests[0],
            source_last_row_sha256=digests[-1],
            ordered_source_rows_sha256=payload["ordered_source_rows_sha256"],  # type: ignore[arg-type]
            row_sha256=hash_json("phase5-validation-aggregate-row-v2", payload),
        )


def _make_publication(
    *,
    minute_publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    output_root: Path,
    budget: AggregatePublicationBudgetV2,
    members: tuple[AggregateMemberV2, ...],
    failure: str | None,
) -> AggregatePublicationV2:
    bindings = tuple(
        (
            item.path,
            item.sha256,
            item.byte_count,
            item.row_count,
            item.source_identity,
            item.origin_proof_sha256,
        )
        for item in minute_publication.partitions
    )
    payload: dict[str, object] = {
        "status": minute_publication.status.value,
        "coverage_identity": coverage.coverage_identity.value,
        "split_identity": split.split_identity.value,
        "boundary_sha256": boundary.boundary_sha256,
        "minute_source_identity": minute_publication.source_publication_identity.value,
        "minute_publication_sha256": hashlib.sha256(minute_publication.canonical_bytes).hexdigest(),
        "minute_audit_publication_sha256": minute_publication.audit_publication_sha256,
        "reconciliation_identity_sha256": coverage.reconciliation.identity_sha256,
        "raw_dump_identity_sha256": coverage.raw_dump.identity_sha256,
        "source_mapping_version": coverage.raw_dump.source_mapping_version,
        "admitted_source_identity": minute_publication.admitted_source_identity,
        "origin_kind": minute_publication.origin_kind,
        "origin_sha256": minute_publication.origin_sha256,
        "origin_proof_sha256": minute_publication.origin_proof_sha256,
        "allowed_symbols": list(boundary.allowed_symbols),
        "allowed_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)}
            for item in boundary.allowed_intervals
        ],
        "target_timeframes": list(_TARGET_TIMEFRAMES),
        "parent_partition_bindings": [
            {
                "path": path,
                "sha256": sha256,
                "byte_count": byte_count,
                "row_count": row_count,
                "source_identity": source_identity,
                "origin_proof_sha256": proof,
            }
            for path, sha256, byte_count, row_count, source_identity, proof in bindings
        ],
        "budget_sha256": budget.budget_sha256,
        "members": [item.to_dict() for item in members],
        "row_count": sum(item.row_count for item in members),
        "byte_count": sum(item.byte_count for item in members),
        "failure": failure,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    identity = AggregatePublicationIdentityV2.from_payload(payload)
    public = {
        "schema_version": AggregatePublicationV2._schema,
        **payload,
        "aggregate_identity": identity.value,
    }
    return AggregatePublicationV2(
        status=minute_publication.status,
        coverage_identity=coverage.coverage_identity.value,
        split_identity=split.split_identity.value,
        boundary_sha256=boundary.boundary_sha256,
        minute_source_identity=minute_publication.source_publication_identity.value,
        minute_publication_sha256=payload["minute_publication_sha256"],  # type: ignore[arg-type]
        minute_audit_publication_sha256=minute_publication.audit_publication_sha256,
        reconciliation_identity_sha256=coverage.reconciliation.identity_sha256,
        raw_dump_identity_sha256=coverage.raw_dump.identity_sha256,
        source_mapping_version=coverage.raw_dump.source_mapping_version,
        admitted_source_identity=minute_publication.admitted_source_identity,
        origin_kind=minute_publication.origin_kind,
        origin_sha256=minute_publication.origin_sha256,
        origin_proof_sha256=minute_publication.origin_proof_sha256,
        allowed_symbols=boundary.allowed_symbols,
        allowed_intervals=tuple(
            (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
        ),
        target_timeframes=_TARGET_TIMEFRAMES,
        parent_partition_bindings=bindings,
        budget_sha256=budget.budget_sha256,
        members=members,
        row_count=payload["row_count"],  # type: ignore[arg-type]
        byte_count=payload["byte_count"],  # type: ignore[arg-type]
        failure=failure,
        final_scope_attempts=0,
        final_rows=0,
        final_access_records=0,
        aggregate_identity=identity,
        publication_root=output_root,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_FACTORY,
    )


def _publication_from_dict(
    payload: object, *, publication_root: Path, canonical_bytes: bytes
) -> AggregatePublicationV2:
    values = _exact_mapping(
        payload,
        {
            "schema_version",
            "status",
            "coverage_identity",
            "split_identity",
            "boundary_sha256",
            "minute_source_identity",
            "minute_publication_sha256",
            "minute_audit_publication_sha256",
            "reconciliation_identity_sha256",
            "raw_dump_identity_sha256",
            "source_mapping_version",
            "admitted_source_identity",
            "origin_kind",
            "origin_sha256",
            "origin_proof_sha256",
            "allowed_symbols",
            "allowed_intervals",
            "target_timeframes",
            "parent_partition_bindings",
            "budget_sha256",
            "members",
            "row_count",
            "byte_count",
            "failure",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "aggregate_identity",
        },
        "aggregate publication",
    )
    if values["schema_version"] != AggregatePublicationV2._schema:
        raise ValueError("aggregate publication schema is invalid")
    raw_intervals = values["allowed_intervals"]
    raw_bindings = values["parent_partition_bindings"]
    raw_members = values["members"]
    if not all(
        isinstance(item, list)
        for item in (
            values["allowed_symbols"],
            values["target_timeframes"],
            raw_intervals,
            raw_bindings,
            raw_members,
        )
    ):
        raise TypeError("aggregate publication collection fields must be lists")
    intervals = tuple(
        (
            _exact_mapping(item, {"start", "end"}, "aggregate allowed interval")["start"],
            _exact_mapping(item, {"start", "end"}, "aggregate allowed interval")["end"],
        )
        for item in raw_intervals
    )
    bindings = []
    for item in raw_bindings:
        binding = _exact_mapping(
            item,
            {"path", "sha256", "byte_count", "row_count", "source_identity", "origin_proof_sha256"},
            "aggregate parent binding",
        )
        bindings.append(
            (
                binding["path"],
                binding["sha256"],
                binding["byte_count"],
                binding["row_count"],
                binding["source_identity"],
                binding["origin_proof_sha256"],
            )
        )
    return AggregatePublicationV2(
        status=ScopedSourceStatusV2(values["status"]),
        coverage_identity=values["coverage_identity"],  # type: ignore[arg-type]
        split_identity=values["split_identity"],  # type: ignore[arg-type]
        boundary_sha256=values["boundary_sha256"],  # type: ignore[arg-type]
        minute_source_identity=values["minute_source_identity"],  # type: ignore[arg-type]
        minute_publication_sha256=values["minute_publication_sha256"],  # type: ignore[arg-type]
        minute_audit_publication_sha256=values["minute_audit_publication_sha256"],  # type: ignore[arg-type]
        reconciliation_identity_sha256=values["reconciliation_identity_sha256"],  # type: ignore[arg-type]
        raw_dump_identity_sha256=values["raw_dump_identity_sha256"],  # type: ignore[arg-type]
        source_mapping_version=values["source_mapping_version"],  # type: ignore[arg-type]
        admitted_source_identity=values["admitted_source_identity"],  # type: ignore[arg-type]
        origin_kind=values["origin_kind"],  # type: ignore[arg-type]
        origin_sha256=values["origin_sha256"],  # type: ignore[arg-type]
        origin_proof_sha256=values["origin_proof_sha256"],  # type: ignore[arg-type]
        allowed_symbols=tuple(values["allowed_symbols"]),  # type: ignore[arg-type]
        allowed_intervals=intervals,  # type: ignore[arg-type]
        target_timeframes=tuple(values["target_timeframes"]),  # type: ignore[arg-type]
        parent_partition_bindings=tuple(bindings),  # type: ignore[arg-type]
        budget_sha256=values["budget_sha256"],  # type: ignore[arg-type]
        members=tuple(AggregateMemberV2.from_dict(item) for item in raw_members),
        row_count=values["row_count"],  # type: ignore[arg-type]
        byte_count=values["byte_count"],  # type: ignore[arg-type]
        failure=values["failure"],  # type: ignore[arg-type]
        final_scope_attempts=values["final_scope_attempts"],  # type: ignore[arg-type]
        final_rows=values["final_rows"],  # type: ignore[arg-type]
        final_access_records=values["final_access_records"],  # type: ignore[arg-type]
        aggregate_identity=AggregatePublicationIdentityV2(values["aggregate_identity"]),  # type: ignore[arg-type]
        publication_root=publication_root,
        canonical_bytes=canonical_bytes,
        _factory_token=_FACTORY,
    )


def _verify_publication_bindings(
    publication: AggregatePublicationV2,
    minute: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
) -> None:
    bindings = tuple(
        (
            item.path,
            item.sha256,
            item.byte_count,
            item.row_count,
            item.source_identity,
            item.origin_proof_sha256,
        )
        for item in minute.partitions
    )
    if (
        publication.coverage_identity != coverage.coverage_identity.value
        or publication.split_identity != split.split_identity.value
        or publication.boundary_sha256 != boundary.boundary_sha256
        or publication.minute_source_identity != minute.source_publication_identity.value
        or publication.minute_publication_sha256
        != hashlib.sha256(minute.canonical_bytes).hexdigest()
        or publication.minute_audit_publication_sha256 != minute.audit_publication_sha256
        or publication.reconciliation_identity_sha256 != coverage.reconciliation.identity_sha256
        or publication.raw_dump_identity_sha256 != coverage.raw_dump.identity_sha256
        or publication.source_mapping_version != coverage.raw_dump.source_mapping_version
        or publication.admitted_source_identity != minute.admitted_source_identity
        or publication.origin_kind != minute.origin_kind
        or publication.origin_sha256 != minute.origin_sha256
        or publication.origin_proof_sha256 != minute.origin_proof_sha256
        or publication.parent_partition_bindings != bindings
        or publication.allowed_symbols != boundary.allowed_symbols
        or publication.allowed_intervals
        != tuple(
            (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
        )
        or publication.status is not minute.status
    ):
        raise ValueError(
            "aggregate publication parent, source, audit, or boundary binding is stale"
        )


def _verify_rederived_members(
    publication: AggregatePublicationV2,
    minute: ValidationSourcePublicationV2,
    boundary: DevelopmentReadBoundaryV2,
) -> None:
    if publication.status is ScopedSourceStatusV2.UNAVAILABLE:
        return
    members = {
        (item.symbol, item.interval_index, item.target_timeframe): item
        for item in publication.members
    }
    for symbol in boundary.allowed_symbols:
        for interval_index, interval in enumerate(boundary.allowed_intervals):
            parents = tuple(
                item
                for item in minute.partitions
                if item.symbol == symbol and item.interval_index == interval_index
            )
            expected_paths = tuple(item.path for item in parents)
            expected_set = hash_json(
                "phase5-validation-aggregate-parent-partition-set-v2",
                [item.to_dict() for item in parents],
            )
            group_members = {
                timeframe: members[(symbol, interval_index, timeframe)]
                for timeframe in _TARGET_TIMEFRAMES
            }
            actual = {
                timeframe: _iter_member_rows(
                    publication,
                    member,
                    boundary,
                )
                for timeframe, member in group_members.items()
            }
            rolling = hashlib.sha256(b"phase5-validation-aggregate-source-row-order-v2\x00")
            buffer: list[_SourceRow] = []
            source_count = 0
            segment_count = 0
            previous: datetime | None = None

            def finish_segment() -> None:
                nonlocal segment_count
                if buffer:
                    raise ValueError("rederived aggregate source has a partial terminal segment")
                segment_count += 1

            source_rows = _iter_group_rows(
                minute,
                parents,
                symbol=symbol,
                interval_index=interval_index,
                interval_start=interval.start,
                interval_end=interval.end,
                budget=AggregatePublicationBudgetV2(
                    **_HARD_BUDGET_CEILINGS,
                ),
            )
            for chunk in _iter_chunks(
                source_rows,
                maximum=_HARD_BUDGET_CEILINGS["max_source_rows_per_chunk"],
            ):
                for row in chunk:
                    if previous is None or row.timestamp != previous + timedelta(minutes=1):
                        if previous is not None:
                            finish_segment()
                        if row.timestamp.minute or row.timestamp.hour % 4:
                            raise ValueError("rederived aggregate segment is not UTC aligned")
                    rolling.update(bytes.fromhex(row.row_sha256))
                    source_count += 1
                    buffer.append(row)
                    previous = row.timestamp
                    if len(buffer) == 240:
                        block = tuple(buffer)
                        for timeframe in _TARGET_TIMEFRAMES:
                            for expected in _aggregate_segment(
                                block,
                                timeframe,
                                interval_index,
                                segment_count,
                            ):
                                observed = next(actual[timeframe], None)
                                if observed != expected:
                                    raise ValueError(
                                        "aggregate row differs from rederived minute parent"
                                    )
                        buffer.clear()
            if source_count < 1:
                raise ValueError("rederived aggregate source is empty")
            finish_segment()
            ordered_source = rolling.hexdigest()
            for timeframe, member in group_members.items():
                if next(actual[timeframe], None) is not None:
                    raise ValueError("aggregate publication contains extra rederived rows")
                if (
                    member.source_partition_paths != expected_paths
                    or member.source_partition_set_sha256 != expected_set
                    or member.source_row_count != source_count
                    or member.ordered_source_row_sha256 != ordered_source
                    or member.segment_count != segment_count
                ):
                    raise ValueError(
                        "aggregate member parent order, digest, or segment identity differs"
                    )


def _iter_member_rows(
    publication: AggregatePublicationV2,
    member: AggregateMemberV2,
    boundary: DevelopmentReadBoundaryV2,
) -> Iterator[AggregateRowV2]:
    for partition in member.partitions:
        path = publication.publication_root / partition.path
        if sha256_regular(path) != partition.sha256:
            raise ValueError("aggregate partition checksum changed")
        count = 0
        byte_count = 0
        first: AggregateRowV2 | None = None
        previous: AggregateRowV2 | None = None
        step = timedelta(minutes=_TARGET_MINUTES[partition.target_timeframe])
        for line in iter_bounded_regular_lines(
            path,
            maximum_lines=partition.row_count,
            maximum_line_bytes=_MAX_ROW_BYTES,
        ):
            row = AggregateRowV2.from_dict(_decode_json(line, "aggregate row"))
            byte_count += len(line) + 1
            count += 1
            if first is None:
                first = row
            if previous is not None and row.timestamp != previous.timestamp + step:
                raise ValueError("aggregate partition rows overlap or have a gap")
            _verify_row_scope(row, partition, member, boundary)
            previous = row
            yield row
        if (
            first is None
            or previous is None
            or count != partition.row_count
            or byte_count != partition.byte_count
            or _utc_text(first.timestamp) != partition.min_timestamp
            or _utc_text(previous.timestamp) != partition.max_timestamp
        ):
            raise ValueError("aggregate partition bounds, rows, or bytes changed")


def _verify_row_scope(
    row: AggregateRowV2,
    partition: AggregatePartitionV2,
    member: AggregateMemberV2,
    boundary: DevelopmentReadBoundaryV2,
) -> None:
    if (
        row.symbol != member.symbol
        or row.symbol != partition.symbol
        or row.interval_index != member.interval_index
        or row.interval_index != partition.interval_index
        or row.target_timeframe != member.target_timeframe
        or row.target_timeframe != partition.target_timeframe
        or row.segment_id != partition.segment_id
    ):
        raise ValueError("aggregate row member grid differs")
    interval = boundary.allowed_intervals[row.interval_index]
    if (
        row.symbol not in boundary.allowed_symbols
        or not interval.start <= row.timestamp < interval.end
    ):
        raise ValueError("aggregate row overlaps forbidden or final scope")


def _verify_partition_rows(
    rows: tuple[AggregateRowV2, ...],
    partition: AggregatePartitionV2,
    member: AggregateMemberV2,
    boundary: DevelopmentReadBoundaryV2 | None,
) -> None:
    if not rows:
        raise ValueError("aggregate partition is empty")
    for row in rows:
        if (
            row.symbol != member.symbol
            or row.symbol != partition.symbol
            or row.interval_index != member.interval_index
            or row.interval_index != partition.interval_index
            or row.target_timeframe != member.target_timeframe
            or row.target_timeframe != partition.target_timeframe
            or row.segment_id != partition.segment_id
        ):
            raise ValueError("aggregate row member grid differs")
        if boundary is not None:
            interval = boundary.allowed_intervals[row.interval_index]
            if (
                row.symbol not in boundary.allowed_symbols
                or not interval.start <= row.timestamp < interval.end
            ):
                raise ValueError("aggregate row overlaps forbidden or final scope")


def _register(
    publication: AggregatePublicationV2,
    *,
    minute_publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> None:
    identifier = id(publication)

    def cleanup(reference: weakref.ReferenceType[AggregatePublicationV2]) -> None:
        current = _VERIFIED_AGGREGATES.get(identifier)
        if current is not None and current.publication is reference:
            _VERIFIED_AGGREGATES.pop(identifier, None)

    reference = weakref.ref(publication, cleanup)
    _VERIFIED_AGGREGATES[identifier] = _AggregateRegistration(
        publication=reference,
        publication_bytes=publication.canonical_bytes,
        publication_root=publication.publication_root,
        minute=minute_publication,
        minute_bytes=minute_publication.canonical_bytes,
        boundary=boundary,
        boundary_bytes=boundary.canonical_bytes,
        coverage=coverage,
        split=split,
        availability=availability,
    )


def _validate_registered_publication(
    publication: AggregatePublicationV2,
) -> _AggregateRegistration:
    if not isinstance(publication, AggregatePublicationV2):
        raise TypeError("aggregate publication must be verifier-issued")
    registered = _VERIFIED_AGGREGATES.get(id(publication))
    if (
        registered is None
        or registered.publication() is not publication
        or registered.publication_root != publication.publication_root
    ):
        raise ValueError("aggregate publication is not the registered original")
    try:
        current_bytes = publication_json_bytes(publication.to_dict())
        current_identity = AggregatePublicationIdentityV2.from_payload(
            publication._identity_payload()
        )
    except Exception as error:
        raise ValueError("aggregate publication current serialization is invalid") from error
    if (
        current_bytes != registered.publication_bytes
        or publication.canonical_bytes != registered.publication_bytes
        or publication.aggregate_identity != current_identity
        or read_bounded_regular(
            registered.publication_root / "publication.json",
            _MAX_CONTROL_BYTES,
        )
        != registered.publication_bytes
    ):
        raise ValueError("aggregate publication serialization or identity differs from original")
    return registered


def _aggregate_row_bytes(row: AggregateRowV2) -> bytes:
    return (
        json.dumps(
            row.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _publish_stage_no_clobber(stage: Path, destination: Path) -> None:
    _fsync_staged_tree(stage)
    _rename_no_replace(stage, destination)
    try:
        _fsync_directory(destination.parent)
    except Exception:
        shutil.rmtree(destination, ignore_errors=False)
        _fsync_directory(destination.parent)
        raise


def _fsync_staged_tree(stage: Path) -> None:
    files = bounded_regular_files(
        stage,
        maximum=_HARD_BUDGET_CEILINGS["max_output_files"] + 2,
    )
    if "_SUCCESS" not in files:
        raise ValueError("aggregate stage lacks its terminal success marker")
    directories = {stage}
    for relative in files:
        path = stage / relative
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RuntimeError("aggregate stage artifact is not regular")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directories.update(path.parents)
    for directory in sorted(
        (item for item in directories if item == stage or stage in item.parents),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _fsync_directory(directory)


def _rename_no_replace(stage: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(stage, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic no-replace directory publication is unsupported")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(f"refusing existing aggregate publication: {destination}")
    raise OSError(error_number, os.strerror(error_number), destination)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_no_clobber(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_json(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not canonical JSON") from error
    compact = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    pretty = publication_json_bytes(value).rstrip(b"\n")
    if not isinstance(value, dict) or content.rstrip(b"\n") not in {compact, pretty}:
        raise ValueError(f"{label} bytes are not canonical")
    return value


def _exact_mapping(payload: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} has missing or extra fields")
    return payload


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be UTC") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.second
        or parsed.microsecond
    ):
        raise ValueError(f"{label} must be minute-aligned UTC")
    return parsed


def _parse_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be canonical Decimal text")
    try:
        parsed = Decimal(value)
    except Exception as error:
        raise ValueError(f"{label} is invalid") from error
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise ValueError(f"{label} is not canonical")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_utc_aligned(value: datetime, timeframe: str) -> None:
    if timeframe not in _TARGET_MINUTES:
        raise ValueError("aggregate timeframe is invalid")
    if (
        value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.minute
        or value.second
        or value.microsecond
    ):
        raise ValueError("aggregate timestamp must be UTC hour aligned")
    if timeframe == "4h" and value.hour % 4:
        raise ValueError("4h aggregate timestamp must be UTC four-hour aligned")


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _nonnegative(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


__all__ = [
    "AggregateMemberV2",
    "AggregatePartitionV2",
    "AggregatePublicationBudgetV2",
    "AggregatePublicationV2",
    "AggregateRowV2",
    "iter_verified_aggregate_rows_v2",
    "load_validation_aggregate_publication_v2",
    "publish_validation_aggregates_v2",
    "verify_validation_aggregate_publication_v2",
]
