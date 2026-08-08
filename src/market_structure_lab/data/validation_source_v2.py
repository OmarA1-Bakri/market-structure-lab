"""Fail-closed discovery of development-scoped Phase 5 V2 sources.

The PostgreSQL dump and RR publications are provenance metadata only.  This
module never restores or traverses dump table data and never initializes a
database.  A real source is admitted only when an already-existing immutable
descriptor proves exact predicate-before-read enforcement for the verified
development boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, ClassVar, Self
import weakref

from sqlalchemy import Engine, create_engine, text

from market_structure_lab.core.config import (
    CandleSourceMapping,
    MarketDataSettings,
    TimestampUnit,
)

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    iter_verified_regular_lines,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.core.fs_durability import (
    durable_move_no_replace,
    fsync_directory_posix,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.core.secure_windows import (
    WindowsHandleFilesystem,
    WindowsOwnedTreeClaim,
)
from market_structure_lab.research.validation_v2_models import (
    DevelopmentSplitIdentityV2,
    SourceCoverageIdentityV2,
    SourceCoveragePublicationV2,
    SourcePublicationIdentityV2,
    publication_json_bytes,
    verified_source_coverage_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentAccessAuditBindingV2,
    DevelopmentAccessAttemptLedgerV2,
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    verify_development_read_boundary_v2,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.validation_rr_ledger_v2 import (
    RRLedgerInventoryV2,
    iter_scoped_rr_ledger_rows_v2,
    preflight_scoped_rr_ledger_parts_v2,
    reopen_rr_ledger_inventory_v2,
    verify_rr_ledger_inventory_v2,
)
from market_structure_lab.data.validation_source_trust_root_v2 import (
    TRUSTED_VERIFIER_EVIDENCE_SHA256_V2 as _TRUSTED_VERIFIER_EVIDENCE_SHA256,
)

_MAX_DESCRIPTOR_BYTES = 1024 * 1024
_MAX_SOURCE_ANCESTRY_BYTES = 4 * 1024 * 1024
_MAX_CANDIDATES = 1_000
_MAX_WINDOWS_PAIR_PARENT_ENTRIES = 10_000
_MAX_WINDOWS_PAIR_COMMIT_CANDIDATES = 100
_AVAILABILITY_FACTORY = object()
_AVAILABILITY_DOMAIN = "phase5-validation-scoped-source-availability-v2"
_AUDIT_RECORD_DOMAIN = "phase5-validation-source-access-audit-record-v2"
_AUDIT_PUBLICATION_DOMAIN = "phase5-validation-source-access-audit-publication-v2"
_MINUTE_PUBLICATION_AUDIT_RECORD_DOMAIN = "phase5-validation-minute-publication-audit-record-v2"
_MINUTE_PUBLICATION_AUDIT_DOMAIN = "phase5-validation-minute-publication-audit-v2"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_CANONICAL_ROW_BYTES = 4_096
# Frozen validation outcomes use at most a 24-hour (1,440 row) minute path.
# Keep tuple-returning readers conservatively bounded above that horizon.
_MAX_VERIFIED_MINUTE_PATH_ROWS = 10_000
_MAX_POSTGRESQL_MINUTE_BATCH_ROWS = 10_000
_MAX_MINUTE_PUBLICATION_BYTES = 64 * 1024 * 1024
_MAX_MINUTE_PUBLICATION_ENTRIES = 100_000
_MINUTE_PATH_FACTORY = object()
_MINUTE_ROW_FACTORY = object()
_MINUTE_PATH_BUDGET_CEILINGS = {
    "max_total_rows": 50_000_000,
    "max_total_bytes": 64 * 1024 * 1024 * 1024,
    "max_partitions": _MAX_MINUTE_PUBLICATION_ENTRIES - 2,
    "max_returned_rows": 50_000_000,
}
_EXPECTED_DERIVED_TARGET_TIMEFRAMES = ("1h", "4h")
_RECONCILED_VIEW_COLUMNS = (
    "source_row_id",
    "symbol",
    "interval",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "created_at",
    "origin",
    "source_name",
    "payload_checksum",
    "recovery_run_id",
    "reconciliation_run_id",
)
_POSTGRESQL_SOURCE_PREFLIGHT_SQL = """
WITH active_promotion AS (
    SELECT run_id, manifest_sha256, replacement_logical_sha256,
           canonical_logical_sha256
    FROM market_data.candle_reconciliation_promotions
    ORDER BY promotion_id DESC
    LIMIT 1
), view_columns AS (
    SELECT array_agg(column_name ORDER BY ordinal_position) AS names
    FROM information_schema.columns
    WHERE table_schema = 'market_data' AND table_name = 'candles_reconciled'
)
SELECT
    (SELECT count(*) FROM market_data.candle_reconciliation_promotions
      WHERE run_id = 'RR-000008') AS promotion_count,
    active_promotion.run_id,
    active_promotion.manifest_sha256,
    active_promotion.replacement_logical_sha256,
    active_promotion.canonical_logical_sha256,
    view_columns.names AS view_columns,
    pg_get_viewdef('market_data.candles_reconciled'::regclass, true) AS view_definition
FROM active_promotion CROSS JOIN view_columns
"""
_VERIFIED_AVAILABILITIES: dict[
    int,
    tuple[
        weakref.ReferenceType[ScopedSourceAvailabilityV2],
        bytes,
        Path,
        tuple[tuple[Path, str], ...],
        Path,
        bytes,
    ],
] = {}
_VERIFIED_SOURCE_PUBLICATIONS: dict[
    int,
    tuple[
        weakref.ReferenceType[ValidationSourcePublicationV2],
        bytes,
        Path,
        Path,
        bytes,
        tuple[bytes, bytes, bytes, bytes],
    ],
] = {}
_VERIFIED_MINUTE_SOURCE_CAPABILITIES: dict[
    int,
    tuple[
        weakref.ReferenceType[VerifiedDevelopmentMinuteSourceV2],
        bytes,
        weakref.ReferenceType[DevelopmentReadBoundaryV2],
        weakref.ReferenceType[ScopedSourceAvailabilityV2],
        tuple[tuple[Path, bytes], ...],
        _FixtureMinuteReadImplementationV2 | _PostgresqlMinuteReadImplementationV2,
    ],
] = {}

_VERIFIED_MINUTE_PATHS: dict[
    int,
    tuple[
        weakref.ReferenceType[VerifiedMinutePathV2],
        ValidationSourcePublicationV2,
        SourceCoveragePublicationV2,
        DevelopmentSplitPublicationV2,
        DevelopmentReadBoundaryV2,
        ScopedSourceAvailabilityV2,
        DevelopmentAccessAttemptLedgerV2,
        DevelopmentAccessAuditBindingV2,
        BoundaryRequestV2,
        int,
        MinutePathReadBudgetV2,
        tuple[object, ...],
    ],
] = {}


@dataclass(frozen=True, slots=True)
class DumpTocMetadataV2:
    """Bounded dump-list metadata; never row-read authority."""

    table: str
    table_data_toc_sha256: str


def verify_dump_toc_metadata_v2(pg_restore_list: bytes) -> DumpTocMetadataV2:
    """Recognize exactly one ``public.candles`` table-data TOC entry."""

    if not isinstance(pg_restore_list, bytes) or len(pg_restore_list) > 16 * 1024 * 1024:
        raise ValueError("pg_restore --list metadata is invalid or unbounded")
    try:
        lines = pg_restore_list.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("pg_restore --list metadata is not UTF-8") from error
    matches = tuple(line.strip() for line in lines if "TABLE DATA public candles " in line)
    if len(matches) != 1:
        raise ValueError("pg_restore --list must identify exactly one public.candles table")
    if any("TABLE DATA market_data candles " in line for line in lines):
        raise ValueError("market_data.candles is not the verified public.candles table")
    return DumpTocMetadataV2(
        table="public.candles",
        table_data_toc_sha256=hash_json("phase5-validation-public-candles-toc-v2", matches[0]),
    )


def reject_pg_restore_row_source_v2(command: tuple[str, ...]) -> None:
    """Reject every pg_restore invocation other than bounded metadata listing."""

    if (
        not command
        or Path(command[0]).name != "pg_restore"
        or "--list" not in command
        or any(
            argument in command
            for argument in ("--data-only", "-a", "--table", "-t", "--dbname", "-d")
        )
    ):
        raise PermissionError(
            "pg_restore is metadata only; row extraction and whole-table traversal are forbidden"
        )


class ScopedSourceStatusV2(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ScopedSourceCandidateV2:
    descriptor_path: str
    source_kind: str
    source_identity: str
    descriptor_sha256: str
    admitted: bool
    rejection_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptor_path": self.descriptor_path,
            "source_kind": self.source_kind,
            "source_identity": self.source_identity,
            "descriptor_sha256": self.descriptor_sha256,
            "admitted": self.admitted,
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ScopedSourceAvailabilityV2:
    """Factory-sealed and original-byte-revalidated source availability."""

    status: ScopedSourceStatusV2
    boundary_sha256: str
    candidates: tuple[ScopedSourceCandidateV2, ...]
    admitted_source_identity: str | None
    rows_read: int
    bytes_read: int
    final_scope_attempts: int
    final_rows: int
    final_access_records: int
    audit_publication_sha256: str
    audit_publication_path: str
    availability_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None
    _schema: ClassVar[str] = "phase5-validation-scoped-source-availability-v2"

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AVAILABILITY_FACTORY:
            raise TypeError("ScopedSourceAvailabilityV2 requires its factory")
        if not isinstance(self.status, ScopedSourceStatusV2):
            raise TypeError("status must be ScopedSourceStatusV2")
        _require_sha256(self.boundary_sha256, "boundary_sha256")
        _require_sha256(self.audit_publication_sha256, "audit_publication_sha256")
        if not Path(self.audit_publication_path).is_absolute():
            raise ValueError("audit_publication_path must be absolute")
        for label in (
            "rows_read",
            "bytes_read",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
        ):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if (
            self.rows_read
            or self.final_scope_attempts
            or self.final_rows
            or self.final_access_records
        ):
            raise ValueError("source discovery cannot read rows or access final scope")
        admitted = tuple(item for item in self.candidates if item.admitted)
        if self.status is ScopedSourceStatusV2.AVAILABLE:
            if len(admitted) != 1 or self.admitted_source_identity != admitted[0].source_identity:
                raise ValueError("available status requires exactly one admitted source")
        elif admitted or self.admitted_source_identity is not None:
            raise ValueError("unavailable status cannot name an admitted source")
        if self.availability_sha256 != hash_json(_AVAILABILITY_DOMAIN, self._identity_payload()):
            raise ValueError("availability identity differs from its payload")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("availability canonical bytes differ from publication")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "boundary_sha256": self.boundary_sha256,
            "status": self.status.value,
            "candidates": [item.to_dict() for item in self.candidates],
            "admitted_source_identity": self.admitted_source_identity,
            "rows_read": self.rows_read,
            "bytes_read": self.bytes_read,
            "final_scope_attempts": self.final_scope_attempts,
            "final_rows": self.final_rows,
            "final_access_records": self.final_access_records,
            "audit_publication_sha256": self.audit_publication_sha256,
            "audit_publication_path": self.audit_publication_path,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            **self._identity_payload(),
            "availability_sha256": self.availability_sha256,
        }

    @classmethod
    def unsealed_for_test(cls, boundary_sha256: str) -> Self:
        """Adversarial helper proving public callers cannot construct authority."""

        empty_audit = "0" * 64
        payload: dict[str, object] = {
            "boundary_sha256": boundary_sha256,
            "status": "unavailable",
            "candidates": [],
            "admitted_source_identity": None,
            "rows_read": 0,
            "bytes_read": 0,
            "final_scope_attempts": 0,
            "final_rows": 0,
            "final_access_records": 0,
            "audit_publication_sha256": empty_audit,
            "audit_publication_path": "/unsealed/audit/publication.json",
        }
        public = {
            "schema_version": cls._schema,
            **payload,
            "availability_sha256": hash_json(_AVAILABILITY_DOMAIN, payload),
        }
        return cls(
            status=ScopedSourceStatusV2.UNAVAILABLE,
            boundary_sha256=boundary_sha256,
            candidates=(),
            admitted_source_identity=None,
            rows_read=0,
            bytes_read=0,
            final_scope_attempts=0,
            final_rows=0,
            final_access_records=0,
            audit_publication_sha256=empty_audit,
            audit_publication_path="/unsealed/audit/publication.json",
            availability_sha256=public["availability_sha256"],  # type: ignore[arg-type]
            canonical_bytes=publication_json_bytes(public),
        )


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResultV2:
    availability: ScopedSourceAvailabilityV2
    publication_root: Path
    audit_ledger_root: Path


@dataclass(frozen=True, slots=True)
class ScopedSourceCandidateEvidenceV2:
    """Non-authorizing exact-byte candidate evidence awaiting trust-root review."""

    publication_root: Path
    source_identity: str
    descriptor_sha256: str
    original_manifest_sha256: str
    predicate_evidence_sha256: str
    verifier_evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.publication_root.is_absolute():
            raise ValueError("candidate evidence publication root must be absolute")
        if not self.source_identity:
            raise ValueError("candidate evidence source identity is required")
        for value, label in (
            (self.descriptor_sha256, "descriptor_sha256"),
            (self.original_manifest_sha256, "original_manifest_sha256"),
            (self.predicate_evidence_sha256, "predicate_evidence_sha256"),
            (self.verifier_evidence_sha256, "verifier_evidence_sha256"),
        ):
            _require_sha256(value, label)


@dataclass(frozen=True, slots=True)
class CanonicalMinuteRowV2:
    """Exact Decimal/UTC one-minute source row."""

    timestamp: datetime
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() != timedelta(0)
            or self.timestamp.second
            or self.timestamp.microsecond
        ):
            raise ValueError("canonical minute timestamp must be minute-aligned UTC")
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("canonical minute symbol must be non-empty uppercase")
        if self.timeframe != "1m":
            raise ValueError("canonical minute timeframe must be 1m")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("canonical minute values must be finite Decimal values")
        if min(values) < 0:
            raise ValueError("canonical minute values must be non-negative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("canonical minute row violates OHLC relationships")

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": _utc_text(self.timestamp),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": _decimal_text(self.volume),
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "timestamp",
                "symbol",
                "timeframe",
                "open",
                "high",
                "low",
                "close",
                "volume",
            },
            "canonical minute row",
        )
        return cls(
            timestamp=_parse_utc_text(values["timestamp"], "canonical minute timestamp"),
            symbol=values["symbol"],  # type: ignore[arg-type]
            timeframe=values["timeframe"],  # type: ignore[arg-type]
            open=_parse_decimal(values["open"], "open"),
            high=_parse_decimal(values["high"], "high"),
            low=_parse_decimal(values["low"], "low"),
            close=_parse_decimal(values["close"], "close"),
            volume=_parse_decimal(values["volume"], "volume"),
        )


@dataclass(frozen=True, slots=True)
class MinutePathReadBudgetV2:
    """Fixed bounds checked before reopening a minute publication."""

    max_total_rows: int
    max_total_bytes: int
    max_partitions: int
    max_returned_rows: int

    def __post_init__(self) -> None:
        for label in self.__dataclass_fields__:
            value = getattr(self, label)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > _MINUTE_PATH_BUDGET_CEILINGS[label]
            ):
                raise ValueError(f"{label} must be positive and within its fixed hard ceiling")


@dataclass(frozen=True, slots=True)
class VerifiedMinuteRowV2:
    """Canonical minute values bound to the exact original JSONL line."""

    timestamp: datetime
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_identity: str
    origin_proof_sha256: str
    line_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MINUTE_ROW_FACTORY:
            raise TypeError("VerifiedMinuteRowV2 requires its factory")
        CanonicalMinuteRowV2(
            timestamp=self.timestamp,
            symbol=self.symbol,
            timeframe=self.timeframe,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )
        if not self.source_identity:
            raise ValueError("verified minute row source identity is required")
        _require_sha256(self.origin_proof_sha256, "verified minute origin proof")
        _require_sha256(self.line_sha256, "verified minute line sha256")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedMinutePathV2:
    """Factory-sealed exact one-row-per-minute path."""

    request: BoundaryRequestV2
    rows: tuple[VerifiedMinuteRowV2, ...]
    partition_paths: tuple[str, ...]
    row_count: int
    byte_count: int
    ordered_row_sha256: str
    audit_binding: DevelopmentAccessAuditBindingV2
    path_identity: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MINUTE_PATH_FACTORY:
            raise TypeError("VerifiedMinutePathV2 requires its factory")
        if not isinstance(self.request, BoundaryRequestV2):
            raise TypeError("verified minute path request must be typed")
        if self.row_count != len(self.rows) or self.row_count < 1:
            raise ValueError("verified minute path row count differs")
        if self.byte_count < 1:
            raise ValueError("verified minute path byte count must be positive")
        if not self.partition_paths or self.partition_paths != tuple(
            sorted(set(self.partition_paths))
        ):
            raise ValueError("verified minute path partitions must be uniquely ordered")
        if (
            not isinstance(self.audit_binding, DevelopmentAccessAuditBindingV2)
            or self.audit_binding.terminal_record is None
            or self.audit_binding.terminal_record.phase != "completion"
        ):
            raise ValueError("verified minute path requires a completed audit binding")
        _require_sha256(self.ordered_row_sha256, "ordered_row_sha256")
        _require_sha256(self.path_identity, "path_identity")

    def verify_original(self) -> VerifiedMinutePathV2:
        """Reopen and verify the exact parents and audit registered at issuance."""

        return verify_original_minute_path_v2(self)


@dataclass(frozen=True, slots=True)
class ValidationSourcePartitionV2:
    path: str
    sha256: str
    byte_count: int
    row_count: int
    symbol: str
    interval_index: int
    interval_start: str
    interval_end: str
    min_timestamp: str
    max_timestamp: str
    source_identity: str
    origin_proof_sha256: str

    def __post_init__(self) -> None:
        relative = Path(self.path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != self.path
            or not self.path.endswith(".jsonl")
        ):
            raise ValueError("validation source partition path is invalid")
        _require_sha256(self.sha256, "partition sha256")
        _require_sha256(self.origin_proof_sha256, "partition origin proof sha256")
        if not self.source_identity:
            raise ValueError("partition source identity is required")
        for label in ("byte_count", "row_count"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"partition {label} must be positive")
        if (
            isinstance(self.interval_index, bool)
            or not isinstance(self.interval_index, int)
            or self.interval_index < 0
        ):
            raise ValueError("partition interval_index must be non-negative")
        start = _parse_utc_text(self.interval_start, "partition interval start")
        end = _parse_utc_text(self.interval_end, "partition interval end")
        minimum = _parse_utc_text(self.min_timestamp, "partition minimum timestamp")
        maximum = _parse_utc_text(self.max_timestamp, "partition maximum timestamp")
        if not start <= minimum <= maximum < end:
            raise ValueError("partition timestamps exceed the half-open interval")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "row_count": self.row_count,
            "symbol": self.symbol,
            "interval_index": self.interval_index,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "min_timestamp": self.min_timestamp,
            "max_timestamp": self.max_timestamp,
            "source_identity": self.source_identity,
            "origin_proof_sha256": self.origin_proof_sha256,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        return cls(
            **_exact_mapping(
                payload,
                {
                    "path",
                    "sha256",
                    "byte_count",
                    "row_count",
                    "symbol",
                    "interval_index",
                    "interval_start",
                    "interval_end",
                    "min_timestamp",
                    "max_timestamp",
                    "source_identity",
                    "origin_proof_sha256",
                },
                "validation source partition",
            )
        )  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationSourcePublicationV2:
    """Factory-sealed development-only canonical minute publication."""

    status: ScopedSourceStatusV2
    coverage_identity: SourceCoverageIdentityV2
    split_identity: DevelopmentSplitIdentityV2
    boundary_sha256: str
    source_availability_sha256: str
    source_audit_sha256: str
    admitted_source_identity: str | None
    origin_kind: str | None
    origin_sha256: str | None
    origin_proof_sha256: str | None
    reconciliation_identity_sha256: str
    raw_dump_identity_sha256: str
    source_mapping_version: str
    allowed_symbols: tuple[str, ...]
    allowed_timeframe: str
    allowed_intervals: tuple[tuple[str, str], ...]
    partitions: tuple[ValidationSourcePartitionV2, ...]
    row_count: int
    byte_count: int
    failure: str | None
    audit_publication_sha256: str
    final_scope_attempts: int
    final_rows: int
    final_access_records: int
    source_publication_identity: SourcePublicationIdentityV2
    publication_root: Path = field(repr=False, compare=False)
    audit_ledger_root: Path = field(repr=False, compare=False)
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MINUTE_PUBLICATION_FACTORY:
            raise TypeError("ValidationSourcePublicationV2 requires its factory")
        if not isinstance(self.status, ScopedSourceStatusV2):
            raise TypeError("validation source status is invalid")
        _require_sha256(self.boundary_sha256, "boundary_sha256")
        _require_sha256(self.source_availability_sha256, "source_availability_sha256")
        _require_sha256(self.source_audit_sha256, "source_audit_sha256")
        _require_sha256(
            self.reconciliation_identity_sha256,
            "reconciliation_identity_sha256",
        )
        _require_sha256(self.raw_dump_identity_sha256, "raw_dump_identity_sha256")
        _require_sha256(self.audit_publication_sha256, "audit_publication_sha256")
        if self.origin_sha256 is not None:
            _require_sha256(self.origin_sha256, "origin_sha256")
        if self.origin_proof_sha256 is not None:
            _require_sha256(self.origin_proof_sha256, "origin_proof_sha256")
        if self.allowed_timeframe != "1m" or "1m" not in self.allowed_timeframes:
            raise ValueError("validation source publication must be one-minute")
        if self.allowed_symbols != tuple(sorted(set(self.allowed_symbols))):
            raise ValueError("validation source symbols must be uniquely sorted")
        if self.final_scope_attempts or self.final_rows or self.final_access_records:
            raise ValueError("validation source publication cannot contain final access")
        if self.row_count != sum(item.row_count for item in self.partitions):
            raise ValueError("validation source partition row counts differ")
        if self.byte_count != sum(item.byte_count for item in self.partitions):
            raise ValueError("validation source partition byte counts differ")
        paths = tuple(item.path for item in self.partitions)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("validation source partition paths are not deterministic")
        for partition in self.partitions:
            if (
                partition.symbol not in self.allowed_symbols
                or partition.interval_index >= len(self.allowed_intervals)
                or (
                    partition.interval_start,
                    partition.interval_end,
                )
                != self.allowed_intervals[partition.interval_index]
            ):
                raise ValueError("validation source partition exceeds its declared source scope")
        if self.status is ScopedSourceStatusV2.AVAILABLE:
            if (
                self.failure is not None
                or self.admitted_source_identity is None
                or self.origin_kind != "admitted-predicate-source-v2"
                or self.origin_sha256 is None
                or self.origin_proof_sha256 is None
                or not self.partitions
            ):
                raise ValueError("available validation source binding is incomplete")
        elif (
            self.failure != "scoped_source_unavailable"
            or self.admitted_source_identity is not None
            or self.origin_kind is not None
            or self.origin_sha256 is not None
            or self.origin_proof_sha256 is not None
            or self.partitions
            or self.row_count
            or self.byte_count
        ):
            raise ValueError("unavailable validation source publication is inconsistent")
        payload = self._identity_payload()
        if self.source_publication_identity != SourcePublicationIdentityV2.from_payload(payload):
            raise ValueError("validation source publication identity differs")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("validation source publication bytes differ")

    @property
    def allowed_timeframes(self) -> tuple[str, ...]:
        return (self.allowed_timeframe,)

    def _identity_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "coverage_identity": self.coverage_identity.value,
            "split_identity": self.split_identity.value,
            "boundary_sha256": self.boundary_sha256,
            "source_availability_sha256": self.source_availability_sha256,
            "source_audit_sha256": self.source_audit_sha256,
            "admitted_source_identity": self.admitted_source_identity,
            "origin_kind": self.origin_kind,
            "origin_sha256": self.origin_sha256,
            "origin_proof_sha256": self.origin_proof_sha256,
            "reconciliation_identity_sha256": self.reconciliation_identity_sha256,
            "raw_dump_identity_sha256": self.raw_dump_identity_sha256,
            "source_mapping_version": self.source_mapping_version,
            "allowed_symbols": list(self.allowed_symbols),
            "allowed_timeframe": self.allowed_timeframe,
            "allowed_intervals": [
                {"start": start, "end": end} for start, end in self.allowed_intervals
            ],
            "partitions": [item.to_dict() for item in self.partitions],
            "row_count": self.row_count,
            "byte_count": self.byte_count,
            "failure": self.failure,
            "audit_publication_sha256": self.audit_publication_sha256,
            "final_scope_attempts": self.final_scope_attempts,
            "final_rows": self.final_rows,
            "final_access_records": self.final_access_records,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase5-validation-development-minute-source-v2",
            **self._identity_payload(),
            "source_publication_identity": self.source_publication_identity.value,
        }


_MINUTE_PUBLICATION_FACTORY = object()
_MINUTE_SOURCE_CAPABILITY_FACTORY = object()


@dataclass(frozen=True, slots=True)
class _FixtureMinuteReadImplementationV2:
    request_rows: tuple[tuple[CanonicalMinuteRowV2, ...], ...]
    fail_request_index: int | None


@dataclass(frozen=True, slots=True)
class _PostgresqlMinuteReadImplementationV2:
    engine: Engine
    batch_size: int
    mapping: CandleSourceMapping
    origin: _VerifiedMinuteSourceOriginV2
    expected_metadata: _PostgresqlSourceMetadataV2
    rr_inventory: RRLedgerInventoryV2


@dataclass(frozen=True, slots=True)
class _PostgresqlSourceMetadataV2:
    promotion_count: int
    run_id: str
    manifest_sha256: str
    replacement_logical_sha256: str
    canonical_logical_sha256: str
    view_columns: tuple[str, ...]
    view_definition: str

    def verifier_payload(self) -> dict[str, object]:
        canonical_view = _canonical_view_definition_bytes(self.view_definition)
        return {
            "promotion_count": self.promotion_count,
            "run_id": self.run_id,
            "manifest_sha256": self.manifest_sha256,
            "replacement_logical_sha256": self.replacement_logical_sha256,
            "canonical_logical_sha256": self.canonical_logical_sha256,
            "view_columns": list(self.view_columns),
            "normalized_view_definition_sha256": hashlib.sha256(canonical_view).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class _MinuteAuditCompletionV2:
    symbol: str
    interval_index: int
    row_count: int
    byte_count: int
    partition_set_sha256: str


@dataclass(frozen=True, slots=True)
class _MinuteAuditVerificationV2:
    canonical_bytes: bytes
    completions: tuple[_MinuteAuditCompletionV2, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedMinuteSourceOriginV2:
    source_identity: str
    origin_kind: str
    origin_sha256: str
    origin_proof_sha256: str
    evidence_bindings: tuple[tuple[Path, bytes], ...]


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedDevelopmentMinuteSourceV2:
    """Verifier-issued row capability; callers cannot supply read behavior."""

    source_identity: str
    origin_kind: str
    origin_sha256: str
    origin_proof_sha256: str
    boundary_sha256: str
    availability_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MINUTE_SOURCE_CAPABILITY_FACTORY:
            raise TypeError("VerifiedDevelopmentMinuteSourceV2 requires its verifier factory")
        if not self.source_identity:
            raise ValueError("verified minute source identity is required")
        if self.origin_kind != "admitted-predicate-source-v2":
            raise ValueError("verified minute source origin kind is invalid")
        for value, label in (
            (self.origin_sha256, "origin_sha256"),
            (self.origin_proof_sha256, "origin_proof_sha256"),
            (self.boundary_sha256, "boundary_sha256"),
            (self.availability_sha256, "availability_sha256"),
        ):
            _require_sha256(value, label)
        if self.canonical_bytes != publication_json_bytes(
            {
                "schema_version": "phase5-verified-development-minute-source-v2",
                "source_identity": self.source_identity,
                "origin_kind": self.origin_kind,
                "origin_sha256": self.origin_sha256,
                "origin_proof_sha256": self.origin_proof_sha256,
                "boundary_sha256": self.boundary_sha256,
                "availability_sha256": self.availability_sha256,
            }
        ):
            raise ValueError("verified minute source capability bytes differ")

    def close(self) -> None:
        """Dispose the sealed reader and revoke this single-process capability."""

        _close_minute_source_capability(self)

    def __enter__(self) -> Self:
        _registered_minute_source_implementation(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def publish_scoped_source_candidate_inputs_v2(
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    raw_dump_path: Path,
    rr_promotion_receipt_path: Path,
    recovery_manifest_path: Path,
    reconciled_view_definition_path: Path,
    rr_publication_root_path: Path | None = None,
    output_root: Path,
) -> Path:
    """Publish ancestry-bound manifest and predicate inputs for independent review."""

    verified_source_coverage_bytes(coverage)
    verify_development_read_boundary_v2(boundary, coverage, split)
    if coverage.reconciliation.replacement_source_policy != "rr-000008-promoted-only":
        raise ValueError("candidate evidence requires the RR-000008 promoted-only ancestry")
    dump_path = _absolute_candidate_ancestry_path(raw_dump_path)
    if not dump_path.is_file():
        raise ValueError("raw dump ancestry path must be an existing file")
    dump_stat = dump_path.stat()
    if dump_stat.st_size != coverage.raw_dump.byte_count:
        raise ValueError("raw dump size differs from verified coverage ancestry")
    if sha256_regular(dump_path) != coverage.raw_dump.dump_sha256:
        raise ValueError("raw dump bytes differ from verified coverage ancestry")
    rr_path = _absolute_candidate_ancestry_path(rr_promotion_receipt_path)
    recovery_path = _absolute_candidate_ancestry_path(recovery_manifest_path)
    view_path = _absolute_candidate_ancestry_path(reconciled_view_definition_path)
    rr_bytes = read_bounded_regular(rr_path, _MAX_SOURCE_ANCESTRY_BYTES)
    recovery_bytes = read_bounded_regular(recovery_path, _MAX_SOURCE_ANCESTRY_BYTES)
    view_bytes = read_bounded_regular(view_path, _MAX_SOURCE_ANCESTRY_BYTES)
    rr_sha = hashlib.sha256(rr_bytes).hexdigest()
    recovery_sha = hashlib.sha256(recovery_bytes).hexdigest()
    view_sha = hashlib.sha256(view_bytes).hexdigest()
    if rr_sha != coverage.reconciliation.promotion_receipt_sha256:
        raise ValueError("RR-000008 receipt differs from verified coverage ancestry")
    if recovery_sha != coverage.compatibility_metadata_sha256:
        raise ValueError("recovery manifest differs from verified coverage ancestry")
    if b"CREATE OR REPLACE VIEW market_data.candles_reconciled AS" not in view_bytes:
        raise ValueError("reconciled-view definition does not define the required source view")
    ancestry = {
        "raw_dump_path": str(dump_path),
        "raw_dump_sha256": coverage.raw_dump.dump_sha256,
        "raw_dump_byte_count": coverage.raw_dump.byte_count,
        "raw_dump_device": dump_stat.st_dev,
        "raw_dump_inode": dump_stat.st_ino,
        "raw_dump_mtime_ns": dump_stat.st_mtime_ns,
        "raw_dump_ctime_ns": dump_stat.st_ctime_ns,
        "pg_restore_list_sha256": coverage.raw_dump.pg_restore_list_sha256,
        "candle_table_toc_identity": coverage.raw_dump.candle_table_toc_identity,
        "source_mapping_version": coverage.raw_dump.source_mapping_version,
        "rr_promotion_receipt_path": str(rr_path),
        "rr_promotion_receipt_sha256": rr_sha,
        "recovery_manifest_path": str(recovery_path),
        "recovery_manifest_sha256": recovery_sha,
        "reconciled_view_definition_path": str(view_path),
        "reconciled_view_definition_sha256": view_sha,
        "source_view": "market_data.candles_reconciled",
        "view_evidence_kind": "migration-definition-code-ancestry-not-live-state",
        "coverage_identity": coverage.coverage_identity.value,
        "raw_dump_identity_sha256": coverage.raw_dump.identity_sha256,
        "reconciliation_identity_sha256": coverage.reconciliation.identity_sha256,
    }
    if rr_publication_root_path is not None:
        rr_inventory = verify_rr_ledger_inventory_v2(
            rr_publication_root_path,
            symbols=boundary.allowed_symbols,
            intervals=tuple((item.start, item.end) for item in boundary.allowed_intervals),
            expected_work_unit_manifest_sha256=(
                coverage.reconciliation.work_unit_manifest_sha256
            ),
            expected_comparison_part_sha256=(
                coverage.reconciliation.comparison_part_sha256
            ),
        )
        ancestry["rr_publication_root_path"] = str(rr_inventory.root)
        ancestry["rr_publication_inventory_sha256"] = rr_inventory.inventory_sha256
        ancestry["rr_run_manifest_sha256"] = rr_inventory.run_manifest_sha256
        ancestry["rr_work_unit_manifest_sha256"] = list(
            coverage.reconciliation.work_unit_manifest_sha256
        )
        ancestry["rr_comparison_part_sha256"] = list(
            coverage.reconciliation.comparison_part_sha256
        )
    source_identity = _candidate_source_identity(ancestry, boundary)
    derived_targets = tuple(split.policy.derived_target_timeframes)
    if derived_targets != _EXPECTED_DERIVED_TARGET_TIMEFRAMES:
        raise ValueError("candidate derived target timeframes differ from the verified policy")
    scope = _source_scope_payload_v3(boundary, derived_targets=derived_targets)
    use_policy = {
        "use_class": "private-quantitative-research",
        "technical_use_only": True,
        "redistribution_authorized": False,
        "commercial_use_authorized": False,
        "data_rights_status": "not-established-by-this-evidence",
    }
    original_payload = {
        "source_identity": source_identity,
        **scope,
        "source_ancestry": ancestry,
        "use_policy": use_policy,
        "read_only": True,
        "predicate_enforcement": "partition-scope-before-open",
    }
    original_bytes = publication_json_bytes(
        {
            "schema_version": "phase5-development-scoped-source-manifest-v3",
            **original_payload,
            "manifest_sha256": hash_json(
                "phase5-development-scoped-source-manifest-v3", original_payload
            ),
        }
    )
    predicate_payload = {
        "source_identity": source_identity,
        **scope,
        "source_view": "market_data.candles_reconciled",
        "boundary_authorization": "before-connection-or-file-open",
        "predicate_stage": "before-file-open-or-query",
        "client_post_filter": False,
        "unbounded_scan": False,
    }
    predicate_bytes = publication_json_bytes(
        {
            "schema_version": "phase5-development-source-predicate-evidence-v3",
            **predicate_payload,
            "evidence_sha256": hash_json(
                "phase5-development-source-predicate-evidence-v3", predicate_payload
            ),
        }
    )
    root = Path(output_root)
    _publish_directory_no_clobber(
        root,
        {"original.json": original_bytes, "predicate.json": predicate_bytes},
    )
    return root.resolve(strict=True)


def publish_independent_scoped_source_verifier_evidence_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    candidate_inputs_root: Path,
    output_root: Path,
    settings: MarketDataSettings,
) -> str:
    """Publish exact catalog metadata from an independently configured review lane."""

    if not isinstance(settings, MarketDataSettings):
        raise TypeError("independent PostgreSQL verifier settings must be MarketDataSettings")
    engine = create_engine(settings.database.url)
    try:
        return _publish_independent_scoped_source_verifier_with_engine_v2(
            boundary=boundary,
            candidate_inputs_root=candidate_inputs_root,
            output_root=output_root,
            engine=engine,
        )
    finally:
        engine.dispose()


def _publish_independent_scoped_source_verifier_with_engine_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    candidate_inputs_root: Path,
    output_root: Path,
    engine: Any,
) -> str:
    """Private metadata-only engine seam for deterministic adversarial tests."""

    source_identity, original_bytes, predicate_bytes = _verify_candidate_inputs_v3(
        boundary=boundary,
        publication_root=Path(candidate_inputs_root),
    )
    original = _decode_canonical_object(original_bytes, "original manifest")
    ancestry = original["source_ancestry"]
    if not isinstance(ancestry, dict):
        raise ValueError("candidate source ancestry is invalid")
    ancestry_bindings = {
        Path(str(ancestry[path_label])): read_bounded_regular(
            Path(str(ancestry[path_label])), _MAX_SOURCE_ANCESTRY_BYTES
        )
        for path_label in (
            "rr_promotion_receipt_path",
            "recovery_manifest_path",
            "reconciled_view_definition_path",
        )
    }
    metadata = _verify_postgresql_metadata_preflight_from_ancestry(
        engine,
        ancestry=ancestry,
        bindings=ancestry_bindings,
    )
    rr_payload: dict[str, object] | None = None
    rr_root = ancestry.get("rr_publication_root_path")
    rr_identity = ancestry.get("rr_publication_inventory_sha256")
    if rr_root is not None or rr_identity is not None:
        if not isinstance(rr_root, str) or not isinstance(rr_identity, str):
            raise ValueError("candidate RR ledger ancestry is incomplete")
        expected_manifests, expected_parts = _rr_authority_from_ancestry(ancestry)
        rr_inventory = verify_rr_ledger_inventory_v2(
            Path(rr_root),
            symbols=boundary.allowed_symbols,
            intervals=tuple((item.start, item.end) for item in boundary.allowed_intervals),
            expected_work_unit_manifest_sha256=expected_manifests,
            expected_comparison_part_sha256=expected_parts,
        )
        if rr_inventory.inventory_sha256 != rr_identity:
            raise ValueError("candidate RR ledger inventory changed before review")
        _require_rr_promotion_run_binding(rr_inventory, metadata.manifest_sha256)
        rr_payload = rr_inventory.verifier_payload()
    verifier_payload = {
        "source_identity": source_identity,
        "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "predicate_evidence_sha256": hashlib.sha256(predicate_bytes).hexdigest(),
        "boundary_sha256": boundary.boundary_sha256,
        "verifier_kind": "independent-original-byte-verifier",
        "immutable": True,
        "postgresql_metadata": metadata.verifier_payload(),
        "rr_ledger": rr_payload,
    }
    verifier_bytes = publication_json_bytes(
        {
            "schema_version": "phase5-development-source-verifier-evidence-v3",
            **verifier_payload,
            "evidence_sha256": hash_json(
                "phase5-development-source-verifier-evidence-v3", verifier_payload
            ),
        }
    )
    _publish_directory_no_clobber(Path(output_root), {"verifier.json": verifier_bytes})
    return hashlib.sha256(verifier_bytes).hexdigest()


def finalize_scoped_source_candidate_evidence_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    candidate_inputs_root: Path,
    verifier_evidence_root: Path,
    output_root: Path,
) -> ScopedSourceCandidateEvidenceV2:
    """Bind producer and reviewer artifacts without admitting or trusting the source."""

    source_identity, original_bytes, predicate_bytes = _verify_candidate_inputs_v3(
        boundary=boundary,
        publication_root=Path(candidate_inputs_root),
    )
    verifier_root = Path(verifier_evidence_root)
    require_regular_directory(verifier_root)
    if set(bounded_regular_files(verifier_root, maximum=2)) != {"verifier.json"}:
        raise ValueError("verifier evidence publication inventory is invalid")
    verifier_bytes = read_bounded_regular(verifier_root / "verifier.json", _MAX_DESCRIPTOR_BYTES)
    _verify_independent_verifier_evidence_v3(
        verifier_bytes,
        source_identity=source_identity,
        original_bytes=original_bytes,
        predicate_bytes=predicate_bytes,
        boundary=boundary,
    )
    descriptor = {
        "schema_version": "phase5-scoped-source-candidate-v2",
        "source_kind": "content-addressed-development-publication",
        "source_identity": source_identity,
        "original_manifest_path": "original.json",
        "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "verifier_evidence_path": "verifier.json",
        "verifier_evidence_sha256": hashlib.sha256(verifier_bytes).hexdigest(),
        "predicate_evidence_path": "predicate.json",
        "predicate_evidence_sha256": hashlib.sha256(predicate_bytes).hexdigest(),
    }
    root = Path(output_root)
    _publish_directory_no_clobber(
        root,
        {
            "candidate.json": publication_json_bytes(descriptor),
            "original.json": original_bytes,
            "predicate.json": predicate_bytes,
            "verifier.json": verifier_bytes,
        },
    )
    descriptor_bytes = publication_json_bytes(descriptor)
    return ScopedSourceCandidateEvidenceV2(
        publication_root=root.resolve(strict=True),
        source_identity=source_identity,
        descriptor_sha256=hashlib.sha256(descriptor_bytes).hexdigest(),
        original_manifest_sha256=hashlib.sha256(original_bytes).hexdigest(),
        predicate_evidence_sha256=hashlib.sha256(predicate_bytes).hexdigest(),
        verifier_evidence_sha256=hashlib.sha256(verifier_bytes).hexdigest(),
    )


def verify_scoped_source_candidate_evidence_v2(
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    publication_root: Path,
) -> ScopedSourceCandidateEvidenceV2:
    """Reopen candidate evidence and validate it without trusting its verifier digest."""

    verified_source_coverage_bytes(coverage)
    verify_development_read_boundary_v2(boundary, coverage, split)
    root = Path(publication_root)
    require_regular_directory(root)
    if set(bounded_regular_files(root, maximum=5)) != {
        "candidate.json",
        "original.json",
        "predicate.json",
        "verifier.json",
    }:
        raise ValueError("candidate evidence publication inventory is invalid")
    descriptor_path = root / "candidate.json"
    descriptor_bytes = read_bounded_regular(descriptor_path, _MAX_DESCRIPTOR_BYTES)
    descriptor = _decode_canonical_object(descriptor_bytes, "candidate descriptor")
    reopened: dict[Path, bytes] = {}

    def read_reference(path: Path, target: str) -> bytes:
        content = _read_candidate_reference(path, target)
        reopened[path] = content
        return content

    _verify_candidate_descriptor_evidence(
        descriptor,
        descriptor_bytes,
        descriptor_path=descriptor_path,
        boundary=boundary,
        read_reference=read_reference,
    )
    original = _decode_canonical_object(reopened[root / "original.json"], "original manifest")
    ancestry = original.get("source_ancestry")
    if not isinstance(ancestry, dict):
        raise ValueError("candidate evidence ancestry is missing")
    expected_source_identity = _candidate_source_identity(ancestry, boundary)
    if descriptor["source_identity"] != expected_source_identity:
        raise ValueError("candidate evidence source ancestry identity differs")
    if ancestry.get("coverage_identity") != coverage.coverage_identity.value:
        raise ValueError("candidate evidence coverage ancestry differs")
    if ancestry.get("raw_dump_identity_sha256") != coverage.raw_dump.identity_sha256:
        raise ValueError("candidate evidence raw-dump ancestry differs")
    if ancestry.get("raw_dump_sha256") != coverage.raw_dump.dump_sha256:
        raise ValueError("candidate evidence raw-dump content identity differs")
    if ancestry.get("reconciliation_identity_sha256") != coverage.reconciliation.identity_sha256:
        raise ValueError("candidate evidence reconciliation ancestry differs")
    if "rr_publication_root_path" in ancestry:
        if tuple(ancestry.get("rr_work_unit_manifest_sha256", ())) != (
            coverage.reconciliation.work_unit_manifest_sha256
        ):
            raise ValueError("candidate evidence work-unit authority differs")
        if tuple(ancestry.get("rr_comparison_part_sha256", ())) != (
            coverage.reconciliation.comparison_part_sha256
        ):
            raise ValueError("candidate evidence comparison-part authority differs")
    return ScopedSourceCandidateEvidenceV2(
        publication_root=root.resolve(strict=True),
        source_identity=expected_source_identity,
        descriptor_sha256=hashlib.sha256(descriptor_bytes).hexdigest(),
        original_manifest_sha256=hashlib.sha256(reopened[root / "original.json"]).hexdigest(),
        predicate_evidence_sha256=hashlib.sha256(reopened[root / "predicate.json"]).hexdigest(),
        verifier_evidence_sha256=hashlib.sha256(reopened[root / "verifier.json"]).hexdigest(),
    )


def _validated_rr_authority_vectors(
    work_unit_manifest_sha256: object,
    comparison_part_sha256: object,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if (
        not isinstance(work_unit_manifest_sha256, list)
        or not work_unit_manifest_sha256
        or not isinstance(comparison_part_sha256, list)
        or not comparison_part_sha256
    ):
        raise ValueError("RR frozen coverage authority is incomplete")
    for label, values in (
        ("rr_work_unit_manifest_sha256", work_unit_manifest_sha256),
        ("rr_comparison_part_sha256", comparison_part_sha256),
    ):
        if any(not isinstance(value, str) for value in values):
            raise ValueError(f"{label} must contain SHA-256 strings")
        for value in values:
            _require_sha256(value, label)
    return tuple(work_unit_manifest_sha256), tuple(comparison_part_sha256)


def _rr_authority_from_ancestry(
    ancestry: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _validated_rr_authority_vectors(
        ancestry.get("rr_work_unit_manifest_sha256"),
        ancestry.get("rr_comparison_part_sha256"),
    )


def _rr_authority_from_rr_payload(
    payload: object,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(payload, Mapping):
        raise ValueError("RR ledger verifier payload is invalid")
    authority = payload.get("coverage_authority")
    if not isinstance(authority, Mapping):
        raise ValueError("RR ledger coverage authority is invalid")
    return _validated_rr_authority_vectors(
        authority.get("work_unit_manifest_sha256"),
        authority.get("comparison_part_sha256"),
    )


def _candidate_source_identity(
    ancestry: Mapping[str, object], boundary: DevelopmentReadBoundaryV2
) -> str:
    return "callscore-rr000008-reconciled-view-v2:" + hash_json(
        "phase5-callscore-rr000008-reconciled-view-source-v2",
        {"source_ancestry": ancestry, "boundary_sha256": boundary.boundary_sha256},
    )


def _absolute_candidate_ancestry_path(value: Path) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("candidate ancestry path contains traversal")
    return Path(os.path.abspath(path))


def _verify_candidate_inputs_v3(
    *,
    boundary: DevelopmentReadBoundaryV2,
    publication_root: Path,
) -> tuple[str, bytes, bytes]:
    root = Path(publication_root)
    require_regular_directory(root)
    if set(bounded_regular_files(root, maximum=3)) != {"original.json", "predicate.json"}:
        raise ValueError("candidate input publication inventory is invalid")
    original_bytes = read_bounded_regular(root / "original.json", _MAX_DESCRIPTOR_BYTES)
    predicate_bytes = read_bounded_regular(root / "predicate.json", _MAX_DESCRIPTOR_BYTES)
    original = _decode_canonical_object(original_bytes, "original manifest")
    predicate = _decode_canonical_object(predicate_bytes, "predicate evidence")
    source_identity = _verify_candidate_inputs_payload_v3(
        original=original,
        original_bytes=original_bytes,
        predicate=predicate,
        predicate_bytes=predicate_bytes,
        boundary=boundary,
        read_reference=_read_candidate_reference,
    )
    return source_identity, original_bytes, predicate_bytes


def _verify_candidate_inputs_payload_v3(
    *,
    original: dict[str, Any],
    original_bytes: bytes,
    predicate: dict[str, Any],
    predicate_bytes: bytes,
    boundary: DevelopmentReadBoundaryV2,
    read_reference: Any,
) -> str:
    ancestry_fields = {
        "raw_dump_path",
        "raw_dump_sha256",
        "raw_dump_byte_count",
        "raw_dump_device",
        "raw_dump_inode",
        "raw_dump_mtime_ns",
        "raw_dump_ctime_ns",
        "pg_restore_list_sha256",
        "candle_table_toc_identity",
        "source_mapping_version",
        "rr_promotion_receipt_path",
        "rr_promotion_receipt_sha256",
        "recovery_manifest_path",
        "recovery_manifest_sha256",
        "reconciled_view_definition_path",
        "reconciled_view_definition_sha256",
        "source_view",
        "view_evidence_kind",
        "coverage_identity",
        "raw_dump_identity_sha256",
        "reconciliation_identity_sha256",
    }
    ancestry = original.get("source_ancestry")
    if not isinstance(ancestry, dict):
        raise ValueError("candidate source ancestry is invalid")
    rr_fields = {
        "rr_publication_root_path",
        "rr_publication_inventory_sha256",
        "rr_run_manifest_sha256",
        "rr_work_unit_manifest_sha256",
        "rr_comparison_part_sha256",
    }
    if set(ancestry) not in {frozenset(ancestry_fields), frozenset(ancestry_fields | rr_fields)}:
        raise ValueError("candidate source ancestry is invalid")
    dump_path = ancestry["raw_dump_path"]
    if not isinstance(dump_path, str) or not Path(dump_path).is_absolute():
        raise ValueError("raw_dump_path must be an absolute reviewed evidence path")
    if not Path(dump_path).is_file():
        raise ValueError("raw dump ancestry path no longer exists")
    if not isinstance(ancestry["raw_dump_sha256"], str):
        raise ValueError("raw_dump_sha256 is invalid")
    _require_sha256(str(ancestry["raw_dump_sha256"]), "raw_dump_sha256")
    if sha256_regular(Path(dump_path)) != ancestry["raw_dump_sha256"]:
        raise ValueError("raw dump original bytes changed")
    dump_stat = Path(dump_path).stat()
    expected_stat = (
        ancestry["raw_dump_byte_count"],
        ancestry["raw_dump_device"],
        ancestry["raw_dump_inode"],
        ancestry["raw_dump_mtime_ns"],
        ancestry["raw_dump_ctime_ns"],
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in expected_stat):
        raise ValueError("raw dump filesystem identity is invalid")
    if expected_stat != (
        dump_stat.st_size,
        dump_stat.st_dev,
        dump_stat.st_ino,
        dump_stat.st_mtime_ns,
        dump_stat.st_ctime_ns,
    ):
        raise ValueError("raw dump filesystem identity changed")
    _require_sha256(str(ancestry["pg_restore_list_sha256"]), "pg_restore_list_sha256")
    for label in ("candle_table_toc_identity", "source_mapping_version"):
        if not isinstance(ancestry[label], str) or not ancestry[label]:
            raise ValueError(f"raw dump {label} is invalid")
    for path_label, sha_label in (
        ("rr_promotion_receipt_path", "rr_promotion_receipt_sha256"),
        ("recovery_manifest_path", "recovery_manifest_sha256"),
        ("reconciled_view_definition_path", "reconciled_view_definition_sha256"),
    ):
        path_value = ancestry[path_label]
        expected_sha = ancestry[sha_label]
        if not isinstance(path_value, str) or not Path(path_value).is_absolute():
            raise ValueError(f"{path_label} must be an absolute reviewed evidence path")
        if not isinstance(expected_sha, str):
            raise ValueError(f"{sha_label} is invalid")
        _require_sha256(expected_sha, sha_label)
        content = read_reference(Path(path_value), f"ancestry:{path_label}")
        if hashlib.sha256(content).hexdigest() != expected_sha:
            raise ValueError(f"{path_label} original bytes changed")
        if path_label == "reconciled_view_definition_path" and (
            b"CREATE OR REPLACE VIEW market_data.candles_reconciled AS" not in content
        ):
            raise ValueError("reconciled-view definition no longer defines the required view")
    if ancestry["source_view"] != "market_data.candles_reconciled":
        raise ValueError("candidate reconciled-view identity is invalid")
    if ancestry["view_evidence_kind"] != "migration-definition-code-ancestry-not-live-state":
        raise ValueError("candidate view evidence kind is invalid")
    for label in (
        "coverage_identity",
        "raw_dump_identity_sha256",
        "reconciliation_identity_sha256",
    ):
        if not isinstance(ancestry[label], str) or not ancestry[label]:
            raise ValueError(f"candidate ancestry {label} is invalid")
    _require_sha256(str(ancestry["raw_dump_identity_sha256"]), "raw_dump_identity_sha256")
    _require_sha256(
        str(ancestry["reconciliation_identity_sha256"]),
        "reconciliation_identity_sha256",
    )
    if rr_fields <= set(ancestry):
        expected_manifests, expected_parts = _rr_authority_from_ancestry(ancestry)
        rr_root = ancestry["rr_publication_root_path"]
        rr_identity = ancestry["rr_publication_inventory_sha256"]
        rr_run_manifest_sha256 = ancestry["rr_run_manifest_sha256"]
        if not isinstance(rr_root, str) or not Path(rr_root).is_absolute():
            raise ValueError("RR publication root must be an absolute reviewed path")
        if not isinstance(rr_identity, str):
            raise ValueError("RR publication inventory identity is invalid")
        _require_sha256(rr_identity, "rr_publication_inventory_sha256")
        if not isinstance(rr_run_manifest_sha256, str):
            raise ValueError("RR run manifest identity is invalid")
        _require_sha256(rr_run_manifest_sha256, "rr_run_manifest_sha256")
        inventory = verify_rr_ledger_inventory_v2(
            Path(rr_root),
            symbols=boundary.allowed_symbols,
            intervals=tuple((item.start, item.end) for item in boundary.allowed_intervals),
            expected_work_unit_manifest_sha256=expected_manifests,
            expected_comparison_part_sha256=expected_parts,
        )
        if inventory.inventory_sha256 != rr_identity:
            raise ValueError("RR publication inventory original bytes changed")
        receipt_bytes = read_reference(
            Path(str(ancestry["rr_promotion_receipt_path"])),
            "ancestry:rr_promotion_receipt_path",
        )
        receipt = _decode_canonical_object(receipt_bytes, "RR-000008 promotion receipt")
        if inventory.run_manifest_sha256 != rr_run_manifest_sha256:
            raise ValueError("RR run manifest differs from producer ancestry")
        _require_rr_promotion_run_binding(inventory, receipt.get("manifest_sha256"))
    source_identity = _candidate_source_identity(ancestry, boundary)
    scope = _source_scope_payload_v3(
        boundary,
        derived_targets=_EXPECTED_DERIVED_TARGET_TIMEFRAMES,
    )
    use_policy = {
        "use_class": "private-quantitative-research",
        "technical_use_only": True,
        "redistribution_authorized": False,
        "commercial_use_authorized": False,
        "data_rights_status": "not-established-by-this-evidence",
    }
    original_payload = {
        "source_identity": source_identity,
        **scope,
        "source_ancestry": ancestry,
        "use_policy": use_policy,
        "read_only": True,
        "predicate_enforcement": "partition-scope-before-open",
    }
    expected_original = {
        "schema_version": "phase5-development-scoped-source-manifest-v3",
        **original_payload,
        "manifest_sha256": hash_json(
            "phase5-development-scoped-source-manifest-v3", original_payload
        ),
    }
    if original != expected_original or publication_json_bytes(original) != original_bytes:
        raise ValueError("candidate scoped source manifest is invalid")
    predicate_payload = {
        "source_identity": source_identity,
        **scope,
        "source_view": "market_data.candles_reconciled",
        "boundary_authorization": "before-connection-or-file-open",
        "predicate_stage": "before-file-open-or-query",
        "client_post_filter": False,
        "unbounded_scan": False,
    }
    expected_predicate = {
        "schema_version": "phase5-development-source-predicate-evidence-v3",
        **predicate_payload,
        "evidence_sha256": hash_json(
            "phase5-development-source-predicate-evidence-v3", predicate_payload
        ),
    }
    if predicate != expected_predicate or publication_json_bytes(predicate) != predicate_bytes:
        raise ValueError("candidate predicate evidence is invalid")
    return source_identity


def _read_candidate_reference(path: Path, target: str) -> bytes:
    maximum = (
        _MAX_SOURCE_ANCESTRY_BYTES if target.startswith("ancestry:") else _MAX_DESCRIPTOR_BYTES
    )
    return read_bounded_regular(path, maximum)


def _verify_independent_verifier_evidence_v3(
    verifier_bytes: bytes,
    *,
    source_identity: str,
    original_bytes: bytes,
    predicate_bytes: bytes,
    boundary: DevelopmentReadBoundaryV2,
) -> None:
    verifier = _decode_canonical_object(verifier_bytes, "verifier evidence")
    metadata_payload = verifier.get("postgresql_metadata")
    _verified_postgresql_metadata_payload(metadata_payload)
    original = _decode_canonical_object(original_bytes, "original manifest")
    ancestry = original.get("source_ancestry")
    if not isinstance(ancestry, dict):
        raise ValueError("independent verifier source ancestry is invalid")
    rr_payload = verifier.get("rr_ledger")
    if "rr_publication_root_path" in ancestry:
        expected_manifests, expected_parts = _rr_authority_from_ancestry(ancestry)
        inventory = reopen_rr_ledger_inventory_v2(
            rr_payload,
            symbols=boundary.allowed_symbols,
            intervals=tuple((item.start, item.end) for item in boundary.allowed_intervals),
            expected_work_unit_manifest_sha256=expected_manifests,
            expected_comparison_part_sha256=expected_parts,
        )
        if inventory.inventory_sha256 != ancestry.get("rr_publication_inventory_sha256"):
            raise ValueError("independent verifier RR inventory differs from producer")
    elif rr_payload is not None:
        raise ValueError("independent verifier adds unbound RR ledger evidence")
    payload = {
        "source_identity": source_identity,
        "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "predicate_evidence_sha256": hashlib.sha256(predicate_bytes).hexdigest(),
        "boundary_sha256": boundary.boundary_sha256,
        "verifier_kind": "independent-original-byte-verifier",
        "immutable": True,
        "postgresql_metadata": metadata_payload,
        "rr_ledger": rr_payload,
    }
    expected = {
        "schema_version": "phase5-development-source-verifier-evidence-v3",
        **payload,
        "evidence_sha256": hash_json("phase5-development-source-verifier-evidence-v3", payload),
    }
    if verifier != expected or publication_json_bytes(verifier) != verifier_bytes:
        raise ValueError("independent verifier evidence is invalid")


def discover_scoped_source_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    candidate_root: Path,
    audit_ledger_root: Path,
    output_root: Path,
) -> ScopedSourceAvailabilityV2:
    """Inspect bounded metadata only and seal available/unavailable truth."""

    _verify_boundary_original(boundary)
    candidate_root = Path(candidate_root)
    audit_ledger_root = Path(audit_ledger_root)
    output_root = Path(output_root)
    require_regular_directory(candidate_root)
    require_regular_directory(audit_ledger_root.parent)
    require_regular_directory(output_root.parent)
    if path_exists_no_follow(audit_ledger_root):
        raise FileExistsError(f"refusing stale audit ledger root: {audit_ledger_root}")
    if path_exists_no_follow(output_root):
        raise FileExistsError(f"refusing stale source discovery output: {output_root}")
    audit_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{audit_ledger_root.name}.", suffix=".tmp", dir=audit_ledger_root.parent
        )
    )
    output_stage = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.", suffix=".tmp", dir=output_root.parent)
    )
    records_dir = audit_stage / "records"
    records_dir.mkdir()
    candidates: list[ScopedSourceCandidateV2] = []
    descriptor_bindings: list[tuple[Path, str]] = []
    descriptor_byte_count = 0
    prior: str | None = None
    audit_sequence = 0

    def read_candidate_evidence(path: Path, target: str) -> bytes:
        nonlocal audit_sequence, prior, descriptor_byte_count
        request = _metadata_request(boundary, target_identity=target)
        audit_sequence += 1
        prior = _append_audit_record(
            records_dir,
            sequence=audit_sequence,
            phase="start",
            boundary=boundary,
            request=request,
            allowed=False,
            byte_count=0,
            prior=prior,
        )
        boundary.authorize(request)
        raw_evidence = _read_candidate_reference(path, target)
        descriptor_byte_count += len(raw_evidence)
        digest_evidence = hashlib.sha256(raw_evidence).hexdigest()
        audit_sequence += 1
        prior = _append_audit_record(
            records_dir,
            sequence=audit_sequence,
            phase="completion",
            boundary=boundary,
            request=request,
            allowed=True,
            byte_count=len(raw_evidence),
            prior=prior,
        )
        descriptor_bindings.append((path, digest_evidence))
        return raw_evidence

    try:
        files = bounded_regular_files(candidate_root, maximum=_MAX_CANDIDATES)
        for relative in files:
            descriptor_path = candidate_root / relative
            raw = read_candidate_evidence(descriptor_path, f"candidate:{relative}")
            digest = hashlib.sha256(raw).hexdigest()
            candidates.append(
                _evaluate_candidate(
                    descriptor_path,
                    raw,
                    digest,
                    boundary,
                    read_reference=read_candidate_evidence,
                )
            )
    except Exception:
        if not _is_windows_platform():
            _remove_staged_tree(audit_stage)
            _remove_staged_tree(output_stage)
        raise
    admitted = tuple(item for item in candidates if item.admitted)
    if len(admitted) > 1:
        candidates = [
            (
                ScopedSourceCandidateV2(
                    descriptor_path=item.descriptor_path,
                    source_kind=item.source_kind,
                    source_identity=item.source_identity,
                    descriptor_sha256=item.descriptor_sha256,
                    admitted=False,
                    rejection_reason="ambiguous_multiple_admissible_sources",
                )
                if item.admitted
                else item
            )
            for item in candidates
        ]
        admitted = ()
    audit_payload = {
        "schema_version": "phase5-validation-source-access-audit-publication-v2",
        "boundary_sha256": boundary.boundary_sha256,
        "record_count": audit_sequence,
        "terminal_record_sha256": prior,
        "rows_admitted": 0,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    audit_publication_sha256 = hash_json(_AUDIT_PUBLICATION_DOMAIN, audit_payload)
    audit_public = {**audit_payload, "audit_publication_sha256": audit_publication_sha256}
    status = (
        ScopedSourceStatusV2.AVAILABLE if len(admitted) == 1 else ScopedSourceStatusV2.UNAVAILABLE
    )
    identity_payload = {
        "boundary_sha256": boundary.boundary_sha256,
        "status": status.value,
        "candidates": [item.to_dict() for item in candidates],
        "admitted_source_identity": admitted[0].source_identity if admitted else None,
        "rows_read": 0,
        "bytes_read": descriptor_byte_count,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
        "audit_publication_sha256": audit_publication_sha256,
        "audit_publication_path": str(audit_ledger_root / "publication.json"),
    }
    availability_sha256 = hash_json(_AVAILABILITY_DOMAIN, identity_payload)
    public = {
        "schema_version": ScopedSourceAvailabilityV2._schema,
        **identity_payload,
        "availability_sha256": availability_sha256,
    }
    availability = ScopedSourceAvailabilityV2(
        status=status,
        boundary_sha256=boundary.boundary_sha256,
        candidates=tuple(candidates),
        admitted_source_identity=identity_payload["admitted_source_identity"],  # type: ignore[arg-type]
        rows_read=0,
        bytes_read=identity_payload["bytes_read"],  # type: ignore[arg-type]
        final_scope_attempts=0,
        final_rows=0,
        final_access_records=0,
        audit_publication_sha256=audit_publication_sha256,
        audit_publication_path=str(audit_ledger_root / "publication.json"),
        availability_sha256=availability_sha256,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_AVAILABILITY_FACTORY,
    )
    audit_bytes = publication_json_bytes(audit_public)
    try:
        _write_no_clobber(audit_stage / "publication.json", audit_bytes)
        _write_no_clobber(output_stage / "publication.json", availability.canonical_bytes)
        _commit_paired_directories(
            first_stage=output_stage,
            first_destination=output_root,
            second_stage=audit_stage,
            second_destination=audit_ledger_root,
        )
    except Exception:
        if not _is_windows_platform():
            _remove_staged_tree(audit_stage)
            _remove_staged_tree(output_stage)
        raise
    publication_path = output_root / "publication.json"
    _register_availability(
        availability,
        publication_path,
        tuple(descriptor_bindings),
        audit_ledger_root / "publication.json",
        audit_bytes,
    )
    return availability


def verify_scoped_source_availability_v2(
    availability: ScopedSourceAvailabilityV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    publication_root: Path,
) -> ScopedSourceAvailabilityV2:
    """Revalidate sealed object, publication bytes, audit bytes, and candidates."""

    _verify_boundary_original(boundary)
    if not isinstance(availability, ScopedSourceAvailabilityV2):
        raise TypeError("availability must be ScopedSourceAvailabilityV2")
    registered = _VERIFIED_AVAILABILITIES.get(id(availability))
    publication_path = Path(publication_root) / "publication.json"
    if (
        registered is None
        or registered[0]() is not availability
        or registered[1] != availability.canonical_bytes
        or registered[2] != publication_path
    ):
        raise ValueError("availability is not an exact verified original publication")
    _require_windows_pair_commit(publication_path.parent, registered[4].parent)
    if (
        read_bounded_regular(publication_path, _MAX_DESCRIPTOR_BYTES)
        != availability.canonical_bytes
    ):
        raise ValueError("availability original publication bytes changed")
    if availability.boundary_sha256 != boundary.boundary_sha256:
        raise ValueError("availability is stale for the development boundary")
    for path, expected_sha in registered[3]:
        if (
            hashlib.sha256(read_bounded_regular(path, _MAX_DESCRIPTOR_BYTES)).hexdigest()
            != expected_sha
        ):
            raise ValueError("source candidate original bytes changed")
    for candidate in availability.candidates:
        descriptor_path = Path(candidate.descriptor_path)
        raw = read_bounded_regular(descriptor_path, _MAX_DESCRIPTOR_BYTES)
        reevaluated = _evaluate_candidate(
            descriptor_path,
            raw,
            hashlib.sha256(raw).hexdigest(),
            boundary,
            read_reference=_read_candidate_reference,
        )
        if reevaluated != candidate:
            raise ValueError("source candidate trusted evidence changed or was reconstructed")
    if registered[4] != Path(availability.audit_publication_path):
        raise ValueError("availability audit publication path changed")
    audit_bytes = _verify_audit_publication(
        registered[4],
        expected_boundary_sha256=boundary.boundary_sha256,
    )
    if (
        audit_bytes != registered[5]
        or hashlib.sha256(audit_bytes).hexdigest() != hashlib.sha256(registered[5]).hexdigest()
    ):
        raise ValueError("availability original audit bytes changed")
    audit_public = json.loads(audit_bytes)
    if audit_public["audit_publication_sha256"] != availability.audit_publication_sha256:
        raise ValueError("availability does not bind its original audit digest")
    return availability


def verified_scoped_source_availability_bytes_v2(
    availability: ScopedSourceAvailabilityV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
) -> bytes:
    """Return bytes only after revalidating the registered original publication."""

    registered = _VERIFIED_AVAILABILITIES.get(id(availability))
    if registered is None or registered[0]() is not availability:
        raise ValueError("availability is not an exact verified original publication")
    verify_scoped_source_availability_v2(
        availability,
        boundary=boundary,
        publication_root=registered[2].parent,
    )
    return registered[1]


def verified_scoped_source_availability_binding_v2(
    availability: ScopedSourceAvailabilityV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
) -> tuple[bytes, Path]:
    """Return original bytes/path after full sealed-publication revalidation."""

    content = verified_scoped_source_availability_bytes_v2(availability, boundary=boundary)
    registered = _VERIFIED_AVAILABILITIES[id(availability)]
    return content, registered[2]


def load_v2_boundary_publications(
    *,
    coverage_path: Path,
    split_path: Path,
    boundary_path: Path,
) -> tuple[
    SourceCoveragePublicationV2,
    DevelopmentSplitPublicationV2,
    DevelopmentReadBoundaryV2,
]:
    """Load and recompute the Lane I publication chain from original bytes."""

    coverage = SourceCoveragePublicationV2.from_dict(_read_json(coverage_path))
    split = DevelopmentSplitPublicationV2.from_dict(_read_json(split_path), coverage)
    boundary = DevelopmentReadBoundaryV2.from_publication_dict(
        _read_json(boundary_path), coverage, split
    )
    return coverage, split, boundary


def load_scoped_source_availability_v2(
    *,
    publication_root: Path,
    boundary: DevelopmentReadBoundaryV2,
) -> ScopedSourceAvailabilityV2:
    """Reopen a discovery publication and its candidate originals."""

    root = Path(publication_root)
    _require_windows_pair_commit_for_first(root)
    payload = _read_json(root / "publication.json")
    availability = _availability_from_dict(payload)
    candidate_bindings = tuple(
        (
            Path(item.descriptor_path),
            item.descriptor_sha256,
        )
        for item in availability.candidates
    )
    _register_availability(
        availability,
        root / "publication.json",
        candidate_bindings,
        Path(availability.audit_publication_path),
        _verify_audit_publication(
            Path(availability.audit_publication_path),
            expected_boundary_sha256=boundary.boundary_sha256,
        ),
    )
    return verify_scoped_source_availability_v2(
        availability, boundary=boundary, publication_root=root
    )


def _issue_test_minute_source_capability_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    request_rows: tuple[tuple[CanonicalMinuteRowV2, ...], ...],
    fail_request_index: int | None,
) -> VerifiedDevelopmentMinuteSourceV2:
    """Issue only the bounded fixture implementation used by adversarial tests."""

    availability_bytes = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
    origin = _derive_verified_minute_source_origin(boundary=boundary, availability=availability)
    if origin is None:
        raise ValueError("minute source capability requires admitted source availability")
    expected_requests = len(boundary.allowed_symbols) * len(boundary.allowed_intervals)
    if (
        not isinstance(request_rows, tuple)
        or len(request_rows) != expected_requests
        or any(
            not isinstance(rows, tuple)
            or any(not isinstance(row, CanonicalMinuteRowV2) for row in rows)
            for rows in request_rows
        )
    ):
        raise ValueError("fixture minute source rows do not cover exact requests")
    if fail_request_index is not None and (
        isinstance(fail_request_index, bool)
        or not isinstance(fail_request_index, int)
        or not 0 <= fail_request_index < expected_requests
    ):
        raise ValueError("fixture failure request index is invalid")
    public = {
        "schema_version": "phase5-verified-development-minute-source-v2",
        "source_identity": origin.source_identity,
        "origin_kind": origin.origin_kind,
        "origin_sha256": origin.origin_sha256,
        "origin_proof_sha256": origin.origin_proof_sha256,
        "boundary_sha256": boundary.boundary_sha256,
        "availability_sha256": availability.availability_sha256,
    }
    capability = VerifiedDevelopmentMinuteSourceV2(
        source_identity=origin.source_identity,
        origin_kind=origin.origin_kind,
        origin_sha256=origin.origin_sha256,
        origin_proof_sha256=origin.origin_proof_sha256,
        boundary_sha256=boundary.boundary_sha256,
        availability_sha256=availability.availability_sha256,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_MINUTE_SOURCE_CAPABILITY_FACTORY,
    )
    _register_minute_source_capability(
        capability,
        boundary=boundary,
        availability=availability,
        availability_bytes=availability_bytes,
        evidence_bindings=origin.evidence_bindings,
        implementation=_FixtureMinuteReadImplementationV2(
            request_rows=request_rows,
            fail_request_index=fail_request_index,
        ),
    )
    return capability


def issue_postgresql_minute_source_capability_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    settings: MarketDataSettings,
    batch_size: int,
) -> VerifiedDevelopmentMinuteSourceV2:
    """Issue a sealed, bounded reader for the admitted reconciled PostgreSQL view."""

    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= _MAX_POSTGRESQL_MINUTE_BATCH_ROWS
    ):
        raise ValueError("PostgreSQL minute batch size must be positive and within its ceiling")
    if not isinstance(settings, MarketDataSettings):
        raise TypeError("PostgreSQL minute source settings must be MarketDataSettings")
    availability_bytes = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
    origin = _derive_verified_minute_source_origin(boundary=boundary, availability=availability)
    if origin is None:
        raise ValueError("PostgreSQL minute source requires AVAILABLE admitted evidence")
    requests = tuple(
        BoundaryRequestV2(
            symbol=symbol,
            timeframe="1m",
            start=interval.start,
            end=interval.end,
            operation_kind=AccessOperationKindV2.ITERATOR,
            target_identity=origin.origin_sha256,
        )
        for symbol in boundary.allowed_symbols
        for interval in boundary.allowed_intervals
    )
    for request in requests:
        boundary.authorize(request)

    mapping = _reconciled_view_mapping(origin)
    rr_inventory = _origin_rr_ledger_inventory(origin, boundary=boundary)
    engine = create_engine(settings.database.url)
    try:
        metadata = _verify_postgresql_metadata_preflight(engine, origin=origin)
    except BaseException:
        engine.dispose()
        raise
    public = {
        "schema_version": "phase5-verified-development-minute-source-v2",
        "source_identity": origin.source_identity,
        "origin_kind": origin.origin_kind,
        "origin_sha256": origin.origin_sha256,
        "origin_proof_sha256": origin.origin_proof_sha256,
        "boundary_sha256": boundary.boundary_sha256,
        "availability_sha256": availability.availability_sha256,
    }
    capability = VerifiedDevelopmentMinuteSourceV2(
        source_identity=origin.source_identity,
        origin_kind=origin.origin_kind,
        origin_sha256=origin.origin_sha256,
        origin_proof_sha256=origin.origin_proof_sha256,
        boundary_sha256=boundary.boundary_sha256,
        availability_sha256=availability.availability_sha256,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_MINUTE_SOURCE_CAPABILITY_FACTORY,
    )
    _register_minute_source_capability(
        capability,
        boundary=boundary,
        availability=availability,
        availability_bytes=availability_bytes,
        evidence_bindings=origin.evidence_bindings,
        implementation=_PostgresqlMinuteReadImplementationV2(
            engine=engine,
            batch_size=batch_size,
            mapping=mapping,
            origin=origin,
            expected_metadata=metadata,
            rr_inventory=rr_inventory,
        ),
    )
    return capability


def publish_validation_source_v2(
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    source_capability: VerifiedDevelopmentMinuteSourceV2 | None,
    publication_root: Path,
    audit_ledger_root: Path,
    max_rows_per_partition: int,
    max_total_rows: int,
    max_total_bytes: int,
    max_partitions: int,
) -> ValidationSourcePublicationV2:
    """Publish exact development-only minute rows without constructing final reads."""

    _validate_minute_publication_limits(
        max_rows_per_partition=max_rows_per_partition,
        max_total_rows=max_total_rows,
        max_total_bytes=max_total_bytes,
        max_partitions=max_partitions,
    )
    verify_development_read_boundary_v2(boundary, coverage, split)
    coverage_bytes = verified_source_coverage_bytes(coverage)
    availability_bytes = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
    publication_root = Path(publication_root)
    audit_ledger_root = Path(audit_ledger_root)
    for destination in (publication_root, audit_ledger_root):
        require_regular_directory(destination.parent)
        if path_exists_no_follow(destination):
            raise FileExistsError(f"refusing stale validation source publication: {destination}")
    publication_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{publication_root.name}.",
            suffix=".tmp",
            dir=publication_root.parent,
        )
    )
    # The access-attempt ledger is committed before any source iterator, ledger
    # part, or observation query can be opened.  Publication data remains staged.
    audit_ledger_root.mkdir(mode=0o700)
    fsync_directory_posix(audit_ledger_root.parent)
    records_dir = audit_ledger_root / "records"
    records_dir.mkdir(mode=0o700)
    progress_dir = audit_ledger_root / "progress"
    progress_dir.mkdir(mode=0o700)
    fsync_directory_posix(audit_ledger_root)
    partitions: list[ValidationSourcePartitionV2] = []
    audit_sequence = 0
    audit_prior: str | None = None
    row_count = 0
    byte_count = 0
    status = availability.status
    admitted_source_identity: str | None = None
    origin_kind: str | None = None
    origin_sha256: str | None = None
    origin_proof_sha256: str | None = None
    failure: str | None = "scoped_source_unavailable"
    active_request: BoundaryRequestV2 | None = None
    active_progress = {"ledger": 0, "database": 0, "emitted": 0, "unavailable": 0}
    active_progress_base = dict(active_progress)
    progress_sequence = 0
    progress_prior: str | None = None
    progress_digests: list[str] = []
    publication_reserved = False
    published_files: list[tuple[Path, Path]] = []
    published_directories: list[Path] = []

    def persist_progress(counts: Mapping[str, int]) -> None:
        nonlocal progress_sequence, progress_prior, active_progress
        active_progress = {
            key: active_progress_base[key] + int(counts[key]) for key in active_progress
        }
        progress_sequence += 1
        payload = {
            "sequence": progress_sequence,
            "boundary_sha256": boundary.boundary_sha256,
            "request": None
            if active_request is None
            else {
                "symbol": active_request.symbol,
                "timeframe": active_request.timeframe,
                "start": _utc_text(active_request.start),
                "end": _utc_text(active_request.end),
            },
            "counts": active_progress,
            "prior_progress_sha256": progress_prior,
            "final_scope_attempts": 0,
            "final_rows": 0,
            "final_access_records": 0,
        }
        digest = hash_json("phase5-validation-minute-auth-progress-v2", payload)
        _write_no_clobber(
            progress_dir / f"{progress_sequence:08d}-{digest}.json",
            publication_json_bytes({**payload, "progress_sha256": digest}),
        )
        progress_prior = digest
        progress_digests.append(digest)
        fsync_directory_posix(progress_dir)

    try:
        _reserve_publication_directory(publication_root)
        publication_reserved = True
        fsync_directory_posix(publication_root.parent)
        if status is ScopedSourceStatusV2.AVAILABLE:
            (
                admitted_source_identity,
                origin_kind,
                origin_sha256,
                origin_proof_sha256,
            ) = _verify_minute_reader_binding(source_capability, boundary, availability)
            failure = None
            for symbol in boundary.allowed_symbols:
                for interval_index, interval in enumerate(boundary.allowed_intervals):
                    _verify_minute_reader_binding(
                        source_capability,
                        boundary,
                        availability,
                        revalidate_evidence=False,
                    )
                    request = BoundaryRequestV2(
                        symbol=symbol,
                        timeframe="1m",
                        start=interval.start,
                        end=interval.end,
                        operation_kind=AccessOperationKindV2.ITERATOR,
                        target_identity=origin_sha256,
                    )
                    active_request = request
                    active_progress_base = dict(active_progress)
                    boundary.authorize(request)
                    audit_sequence += 1
                    audit_prior = _append_minute_publication_audit_record(
                        records_dir,
                        sequence=audit_sequence,
                        phase="start",
                        boundary=boundary,
                        availability=availability,
                        request=request,
                        origin_kind=origin_kind,
                        origin_sha256=origin_sha256,
                        origin_proof_sha256=origin_proof_sha256,
                        row_count=0,
                        byte_count=0,
                        partition_set_sha256=None,
                        prior=audit_prior,
                    )
                    fsync_directory_posix(records_dir)
                    iterator = _iter_verified_minute_source_rows(
                        source_capability,
                        boundary=boundary,
                        availability=availability,
                        request=request,
                        request_index=(
                            boundary.allowed_symbols.index(symbol) * len(boundary.allowed_intervals)
                            + interval_index
                        ),
                        progress=persist_progress,
                    )
                    (
                        request_partitions,
                        request_rows,
                        request_bytes,
                    ) = _stream_minute_request(
                        iterator,
                        request=request,
                        interval_index=interval_index,
                        publication_stage=publication_stage,
                        starting_partition_count=len(partitions),
                        starting_row_count=row_count,
                        starting_byte_count=byte_count,
                        max_rows_per_partition=max_rows_per_partition,
                        max_total_rows=max_total_rows,
                        max_total_bytes=max_total_bytes,
                        max_partitions=max_partitions,
                        source_identity=admitted_source_identity,
                        origin_proof_sha256=origin_proof_sha256,
                    )
                    if request_rows < 1:
                        raise ValueError(
                            "admitted complete source returned an empty development interval"
                        )
                    partitions.extend(request_partitions)
                    row_count += request_rows
                    byte_count += request_bytes
                    audit_sequence += 1
                    audit_prior = _append_minute_publication_audit_record(
                        records_dir,
                        sequence=audit_sequence,
                        phase="completion",
                        boundary=boundary,
                        availability=availability,
                        request=request,
                        origin_kind=origin_kind,
                        origin_sha256=origin_sha256,
                        origin_proof_sha256=origin_proof_sha256,
                        row_count=request_rows,
                        byte_count=request_bytes,
                        partition_set_sha256=_request_partition_set_sha256(request_partitions),
                        prior=audit_prior,
                    )
                    fsync_directory_posix(records_dir)
                    active_request = None
            _verify_minute_reader_binding(source_capability, boundary, availability)
        audit_payload = {
            "schema_version": "phase5-validation-minute-publication-audit-v2",
            "boundary_sha256": boundary.boundary_sha256,
            "source_availability_sha256": availability.availability_sha256,
            "source_audit_sha256": availability.audit_publication_sha256,
            "admitted_source_identity": admitted_source_identity,
            "origin_kind": origin_kind,
            "origin_sha256": origin_sha256,
            "origin_proof_sha256": origin_proof_sha256,
            "record_count": audit_sequence,
            "terminal_record_sha256": audit_prior,
            "authentication_progress": active_progress,
            "progress_record_count": progress_sequence,
            "terminal_progress_sha256": progress_prior,
            "progress_inventory_sha256": hash_json(
                "phase5-validation-minute-auth-progress-inventory-v2",
                progress_digests,
            ),
            "rows_admitted": row_count,
            "bytes_admitted": byte_count,
            "final_scope_attempts": 0,
            "final_rows": 0,
            "final_access_records": 0,
        }
        audit_publication_sha256 = hash_json(_MINUTE_PUBLICATION_AUDIT_DOMAIN, audit_payload)
        audit_bytes = publication_json_bytes(
            {
                **audit_payload,
                "audit_publication_sha256": audit_publication_sha256,
            }
        )
        _write_no_clobber(audit_ledger_root / "publication.json", audit_bytes)
        fsync_directory_posix(audit_ledger_root)
        identity_payload = _minute_publication_identity_payload(
            status=status,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
            admitted_source_identity=admitted_source_identity,
            origin_kind=origin_kind,
            origin_sha256=origin_sha256,
            origin_proof_sha256=origin_proof_sha256,
            partitions=tuple(partitions),
            row_count=row_count,
            byte_count=byte_count,
            failure=failure,
            audit_publication_sha256=audit_publication_sha256,
        )
        identity = SourcePublicationIdentityV2.from_payload(identity_payload)
        public = {
            "schema_version": "phase5-validation-development-minute-source-v2",
            **identity_payload,
            "source_publication_identity": identity.value,
        }
        canonical_bytes = publication_json_bytes(public)
        _write_no_clobber(publication_stage / "publication.json", canonical_bytes)
        _write_no_clobber(
            publication_stage / "_SUCCESS",
            f"{identity.value}\n".encode("ascii"),
        )
        if max(len(canonical_bytes), len(audit_bytes)) > _MAX_MINUTE_PUBLICATION_BYTES:
            raise ValueError("validation source control publication exceeds byte ceiling")
        _populate_reserved_directory(
            publication_stage,
            publication_root,
            published_files=published_files,
            published_directories=published_directories,
        )
        fsync_directory_posix(publication_root)
        fsync_directory_posix(publication_root.parent)
        _remove_staged_tree(publication_stage)
    except Exception as error:
        failure_payload = {
            "schema_version": "phase5-validation-minute-access-attempt-failure-v2",
            "boundary_sha256": boundary.boundary_sha256,
            "source_availability_sha256": availability.availability_sha256,
            "active_request": None
            if active_request is None
            else {
                "symbol": active_request.symbol,
                "timeframe": active_request.timeframe,
                "start": _utc_text(active_request.start),
                "end": _utc_text(active_request.end),
            },
            "completed_rows": row_count,
            "completed_bytes": byte_count,
            "terminal_record_sha256": audit_prior,
            "authentication_progress": active_progress,
            "progress_record_count": progress_sequence,
            "terminal_progress_sha256": progress_prior,
            "progress_inventory_sha256": hash_json(
                "phase5-validation-minute-auth-progress-inventory-v2",
                progress_digests,
            ),
            "failure_type": type(error).__name__,
            "final_scope_attempts": 0,
            "final_rows": 0,
            "final_access_records": 0,
        }
        _write_no_clobber(
            audit_ledger_root / "failure.json",
            publication_json_bytes(
                {
                    **failure_payload,
                    "failure_sha256": hash_json(
                        "phase5-validation-minute-access-attempt-failure-v2",
                        failure_payload,
                    ),
                }
            ),
        )
        fsync_directory_posix(audit_ledger_root)
        _verify_failed_minute_access_audit_v2(audit_ledger_root)
        if publication_reserved:
            _rollback_reserved_publications(
                [publication_root],
                published_files=published_files,
                published_directories=published_directories,
            )
        if not _is_windows_platform():
            _remove_staged_tree(publication_stage)
        raise
    finally:
        if source_capability is not None:
            implementation = _VERIFIED_MINUTE_SOURCE_CAPABILITIES.get(id(source_capability))
            if implementation is not None and isinstance(
                implementation[5], _PostgresqlMinuteReadImplementationV2
            ):
                source_capability.close()
    publication = ValidationSourcePublicationV2(
        status=status,
        coverage_identity=coverage.coverage_identity,
        split_identity=split.split_identity,
        boundary_sha256=boundary.boundary_sha256,
        source_availability_sha256=availability.availability_sha256,
        source_audit_sha256=availability.audit_publication_sha256,
        admitted_source_identity=admitted_source_identity,
        origin_kind=origin_kind,
        origin_sha256=origin_sha256,
        origin_proof_sha256=origin_proof_sha256,
        reconciliation_identity_sha256=coverage.reconciliation.identity_sha256,
        raw_dump_identity_sha256=coverage.raw_dump.identity_sha256,
        source_mapping_version=coverage.raw_dump.source_mapping_version,
        allowed_symbols=boundary.allowed_symbols,
        allowed_timeframe="1m",
        allowed_intervals=tuple(
            (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
        ),
        partitions=tuple(partitions),
        row_count=row_count,
        byte_count=byte_count,
        failure=failure,
        audit_publication_sha256=audit_publication_sha256,
        final_scope_attempts=0,
        final_rows=0,
        final_access_records=0,
        source_publication_identity=identity,
        publication_root=publication_root,
        audit_ledger_root=audit_ledger_root,
        canonical_bytes=canonical_bytes,
        _factory_token=_MINUTE_PUBLICATION_FACTORY,
    )
    _register_source_publication(
        publication,
        audit_bytes=audit_bytes,
        parent_bytes=(
            coverage_bytes,
            split.canonical_bytes,
            boundary.canonical_bytes,
            availability_bytes,
        ),
    )
    return verify_validation_source_publication_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )


def verify_validation_source_publication_v2(
    publication: ValidationSourcePublicationV2,
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> ValidationSourcePublicationV2:
    """Reopen every original parent, audit, partition, and publication byte."""

    verify_validation_source_publication_metadata_v2(publication)
    verify_development_read_boundary_v2(boundary, coverage, split)
    coverage_bytes = verified_source_coverage_bytes(coverage)
    availability_bytes = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
    registered = _VERIFIED_SOURCE_PUBLICATIONS.get(id(publication))
    expected_parents = (
        coverage_bytes,
        split.canonical_bytes,
        boundary.canonical_bytes,
        availability_bytes,
    )
    if (
        registered is None
        or registered[0]() is not publication
        or registered[1] != publication.canonical_bytes
        or registered[2] != publication.publication_root
        or registered[3] != publication.audit_ledger_root
        or registered[5] != expected_parents
    ):
        raise ValueError("validation source is not an exact verified original publication")
    _require_windows_pair_commit(
        publication.publication_root,
        publication.audit_ledger_root,
    )
    if (
        read_bounded_regular(
            publication.publication_root / "publication.json",
            _MAX_MINUTE_PUBLICATION_BYTES,
        )
        != publication.canonical_bytes
    ):
        raise ValueError("validation source publication original bytes changed")
    _validate_minute_publication_parent_bindings(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    expected_origin = _derive_verified_minute_source_origin(
        boundary=boundary, availability=availability
    )
    _verify_minute_publication_origin(publication, expected_origin)
    audit = _verify_minute_publication_audit(
        publication.audit_ledger_root / "publication.json",
        publication=publication,
        expected_origin=expected_origin,
    )
    if audit.canonical_bytes != registered[4]:
        raise ValueError("validation source audit original bytes changed")
    _verify_minute_partition_tree(publication, audit=audit, expected_origin=expected_origin)
    success = read_bounded_regular(publication.publication_root / "_SUCCESS", 128)
    if success != f"{publication.source_publication_identity.value}\n".encode("ascii"):
        raise ValueError("validation source success marker changed")
    return publication


def verify_validation_source_publication_metadata_v2(
    publication: ValidationSourcePublicationV2,
) -> ValidationSourcePublicationV2:
    """Verify registered publication metadata without reading observation artifacts."""

    if type(publication) is not ValidationSourcePublicationV2:
        raise TypeError("validation source publication must be the exact registered type")
    if (
        type(publication.status) is not ScopedSourceStatusV2
        or type(publication.canonical_bytes) is not bytes
        or type(publication.coverage_identity) is not SourceCoverageIdentityV2
        or type(publication.split_identity) is not DevelopmentSplitIdentityV2
        or type(publication.source_publication_identity) is not SourcePublicationIdentityV2
        or type(publication.allowed_symbols) is not tuple
        or len(publication.allowed_symbols) > _MAX_MINUTE_PUBLICATION_ENTRIES
        or any(type(item) is not str for item in publication.allowed_symbols)
        or type(publication.allowed_intervals) is not tuple
        or len(publication.allowed_intervals) > _MAX_MINUTE_PUBLICATION_ENTRIES
        or any(
            type(item) is not tuple or len(item) != 2 or any(type(part) is not str for part in item)
            for item in publication.allowed_intervals
        )
        or type(publication.partitions) is not tuple
        or len(publication.partitions) > _MAX_MINUTE_PUBLICATION_ENTRIES
        or any(type(item) is not ValidationSourcePartitionV2 for item in publication.partitions)
    ):
        raise TypeError("validation source publication contains non-exact nested metadata")
    registered = _VERIFIED_SOURCE_PUBLICATIONS.get(id(publication))
    if registered is None or registered[0]() is not publication:
        raise ValueError("validation source is not a registered original publication")
    current_bytes = publication_json_bytes(publication.to_dict())
    if current_bytes != publication.canonical_bytes or current_bytes != registered[1]:
        raise ValueError("validation source publication metadata differs from registered bytes")
    if publication.row_count != sum(item.row_count for item in publication.partitions):
        raise ValueError("validation source partition row counts differ")
    if publication.byte_count != sum(item.byte_count for item in publication.partitions):
        raise ValueError("validation source partition byte counts differ")
    expected_identity = SourcePublicationIdentityV2.from_payload(publication._identity_payload())
    if publication.source_publication_identity != expected_identity:
        raise ValueError("validation source publication identity differs")
    return publication


def load_validation_source_publication_v2(
    *,
    publication_root: Path,
    audit_ledger_root: Path,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    expected_source_identity: SourcePublicationIdentityV2 | None = None,
    maximum_row_count: int | None = None,
    maximum_byte_count: int | None = None,
    pre_observation_admission: Callable[[ValidationSourcePublicationV2], None] | None = None,
) -> ValidationSourcePublicationV2:
    """Load and seal a publication only after original-byte verification."""

    if expected_source_identity is not None and not isinstance(
        expected_source_identity, SourcePublicationIdentityV2
    ):
        raise TypeError("expected source identity must be typed")
    for value, label in (
        (maximum_row_count, "maximum_row_count"),
        (maximum_byte_count, "maximum_byte_count"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer")
    if pre_observation_admission is not None and not callable(pre_observation_admission):
        raise TypeError("pre_observation_admission must be callable")
    verify_development_read_boundary_v2(boundary, coverage, split)
    coverage_bytes = verified_source_coverage_bytes(coverage)
    availability_bytes = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
    expected_origin = _derive_verified_minute_source_origin(
        boundary=boundary, availability=availability
    )
    publication_root = Path(publication_root)
    audit_ledger_root = Path(audit_ledger_root)
    _require_windows_pair_commit(publication_root, audit_ledger_root)
    canonical_bytes = read_bounded_regular(
        publication_root / "publication.json",
        _MAX_MINUTE_PUBLICATION_BYTES,
    )
    payload = _decode_canonical_object(canonical_bytes, "validation source publication")
    publication = _validation_source_publication_from_dict(
        payload,
        publication_root=publication_root,
        audit_ledger_root=audit_ledger_root,
        canonical_bytes=canonical_bytes,
    )
    _validate_minute_publication_parent_bindings(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    _verify_minute_publication_origin(publication, expected_origin)
    if (
        expected_source_identity is not None
        and publication.source_publication_identity != expected_source_identity
    ):
        raise ValueError("source publication differs from expected frozen identity")
    if maximum_row_count is not None and publication.row_count > maximum_row_count:
        raise ValueError("source publication row_count exceeds admitted maximum")
    if maximum_byte_count is not None and publication.byte_count > maximum_byte_count:
        raise ValueError("source publication byte_count exceeds admitted maximum")
    if pre_observation_admission is not None:
        pre_observation_admission(publication)
    audit = _verify_minute_publication_audit(
        audit_ledger_root / "publication.json",
        publication=publication,
        expected_origin=expected_origin,
    )
    _verify_minute_partition_tree(publication, audit=audit, expected_origin=expected_origin)
    _register_source_publication(
        publication,
        audit_bytes=audit.canonical_bytes,
        parent_bytes=(
            coverage_bytes,
            split.canonical_bytes,
            boundary.canonical_bytes,
            availability_bytes,
        ),
    )
    return verify_validation_source_publication_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )


def read_verified_minute_path_v2(
    publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    request: BoundaryRequestV2,
    expected_row_count: int,
    budget: MinutePathReadBudgetV2,
    audit: DevelopmentAccessAttemptLedgerV2,
) -> VerifiedMinutePathV2:
    """Read an audited, bounded, gap-free path from original minute partitions."""

    if not isinstance(publication, ValidationSourcePublicationV2):
        raise TypeError("minute path publication must be verifier-issued")
    if not isinstance(request, BoundaryRequestV2):
        raise TypeError("minute path request must be BoundaryRequestV2")
    if not isinstance(budget, MinutePathReadBudgetV2):
        raise TypeError("minute path budget must be MinutePathReadBudgetV2")
    if not isinstance(audit, DevelopmentAccessAttemptLedgerV2):
        raise TypeError("minute path audit must be DevelopmentAccessAttemptLedgerV2")
    audit.verify_binding(boundary=boundary)
    audit.start(request)
    audit.adjudicate(request)
    partitions = _select_minute_path_partitions(publication, request)
    _preflight_minute_path(
        publication,
        request=request,
        partitions=partitions,
        expected_row_count=expected_row_count,
        budget=budget,
    )
    verify_validation_source_publication_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    rows, byte_count = _read_minute_path_rows(publication, request, partitions)
    if len(rows) != expected_row_count:
        raise ValueError("verified minute path row count differs from expected row count")
    ordered = hash_json(
        "phase5-validation-minute-path-row-order-v2",
        [item.line_sha256 for item in rows],
    )
    audit.complete(request, row_count=len(rows), byte_count=byte_count)
    audit_binding = audit.verify_binding(
        boundary=boundary,
        completed_request=request,
    )
    identity = _minute_path_identity(
        publication,
        request,
        partitions,
        rows,
        byte_count,
        ordered,
        audit_binding,
    )
    result = VerifiedMinutePathV2(
        request=request,
        rows=rows,
        partition_paths=tuple(item.path for item in partitions),
        row_count=len(rows),
        byte_count=byte_count,
        ordered_row_sha256=ordered,
        audit_binding=audit_binding,
        path_identity=identity,
        _factory_token=_MINUTE_PATH_FACTORY,
    )
    _register_verified_minute_path(
        result,
        publication=publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        audit=audit,
        audit_binding=audit_binding,
        expected_row_count=expected_row_count,
        budget=budget,
    )
    return result


def verify_verified_minute_path_v2(
    path: VerifiedMinutePathV2,
    *,
    publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    request: BoundaryRequestV2,
    expected_row_count: int,
    budget: MinutePathReadBudgetV2,
    audit: DevelopmentAccessAttemptLedgerV2,
) -> VerifiedMinutePathV2:
    """Reopen and rederive a registered minute path without adding audit records."""

    snapshot = _validate_registered_minute_path(
        path,
        publication=publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        audit=audit,
        request=request,
        expected_row_count=expected_row_count,
        budget=budget,
    )
    audit_binding = audit.verify_binding(
        boundary=boundary,
        completed_request=request,
    )
    if audit_binding != path.audit_binding:
        raise ValueError("verified minute path audit binding differs from original")
    partitions = _select_minute_path_partitions(publication, request)
    _preflight_minute_path(
        publication,
        request=request,
        partitions=partitions,
        expected_row_count=expected_row_count,
        budget=budget,
    )
    verify_validation_source_publication_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    rows, byte_count = _read_minute_path_rows(publication, request, partitions)
    ordered = hash_json(
        "phase5-validation-minute-path-row-order-v2",
        [item.line_sha256 for item in rows],
    )
    identity = _minute_path_identity(
        publication,
        request,
        partitions,
        rows,
        byte_count,
        ordered,
        audit_binding,
    )
    current = (
        path.request,
        path.rows,
        path.partition_paths,
        path.row_count,
        path.byte_count,
        path.ordered_row_sha256,
        path.audit_binding,
        path.path_identity,
    )
    expected = (
        request,
        rows,
        tuple(item.path for item in partitions),
        len(rows),
        byte_count,
        ordered,
        audit_binding,
        identity,
    )
    if current != snapshot or current != expected or len(rows) != expected_row_count:
        raise ValueError("verified minute path serialization or identity differs from original")
    return path


def verify_original_minute_path_v2(
    path: VerifiedMinutePathV2,
) -> VerifiedMinutePathV2:
    """Resolve and reverify every exact parent registered with ``path``."""

    if not isinstance(path, VerifiedMinutePathV2):
        raise TypeError("verified minute path must be factory-issued")
    registered = _VERIFIED_MINUTE_PATHS.get(id(path))
    if registered is None or registered[0]() is not path:
        raise ValueError("verified minute path is not the registered original")
    return verify_verified_minute_path_v2(
        path,
        publication=registered[1],
        coverage=registered[2],
        split=registered[3],
        boundary=registered[4],
        availability=registered[5],
        audit=registered[6],
        request=registered[8],
        expected_row_count=registered[9],
        budget=registered[10],
    )


def _select_minute_path_partitions(
    publication: ValidationSourcePublicationV2,
    request: BoundaryRequestV2,
) -> tuple[ValidationSourcePartitionV2, ...]:
    partitions = tuple(
        item
        for item in publication.partitions
        if (
            item.symbol == request.symbol
            and _parse_utc_text(item.max_timestamp, "partition maximum") >= request.start
            and _parse_utc_text(item.min_timestamp, "partition minimum") < request.end
        )
    )
    if not partitions:
        raise ValueError("minute path request has no overlapping manifest partitions")
    if (
        tuple(item.path for item in partitions) != tuple(sorted(item.path for item in partitions))
        or len({item.interval_index for item in partitions}) != 1
        or any(
            item.source_identity != publication.admitted_source_identity
            or item.origin_proof_sha256 != publication.origin_proof_sha256
            for item in partitions
        )
    ):
        raise ValueError("minute path manifest partitions are mixed or unordered")
    return partitions


def _preflight_minute_path(
    publication: ValidationSourcePublicationV2,
    *,
    request: BoundaryRequestV2,
    partitions: tuple[ValidationSourcePartitionV2, ...],
    expected_row_count: int,
    budget: MinutePathReadBudgetV2,
) -> None:
    if (
        isinstance(expected_row_count, bool)
        or not isinstance(expected_row_count, int)
        or expected_row_count < 1
        or expected_row_count > _MAX_VERIFIED_MINUTE_PATH_ROWS
    ):
        raise ValueError("expected row count exceeds the hard path row ceiling")
    if publication.status is not ScopedSourceStatusV2.AVAILABLE:
        raise ValueError("minute path requires an available publication")
    if publication.row_count > budget.max_total_rows:
        raise ValueError("minute publication exceeds total row budget")
    if publication.byte_count > budget.max_total_bytes:
        raise ValueError("minute publication exceeds total byte budget")
    if len(publication.partitions) > budget.max_partitions:
        raise ValueError("minute publication exceeds total partition budget")
    duration = request.end - request.start
    duration_minutes = int(duration.total_seconds() // 60)
    if expected_row_count != duration_minutes:
        raise ValueError("expected row count must equal the request's UTC minute count")
    if expected_row_count > budget.max_returned_rows:
        raise ValueError("minute path exceeds returned row budget")
    if sum(item.byte_count for item in partitions) > budget.max_total_bytes:
        raise ValueError("minute path selected partitions exceed byte budget")
    if request.timeframe != "1m":
        raise PermissionError("minute path request timeframe must be 1m")
    if publication.origin_sha256 is None or request.target_identity != publication.origin_sha256:
        raise ValueError("minute path request target differs from the verified origin")


def _read_minute_path_rows(
    publication: ValidationSourcePublicationV2,
    request: BoundaryRequestV2,
    partitions: tuple[ValidationSourcePartitionV2, ...],
) -> tuple[tuple[VerifiedMinuteRowV2, ...], int]:
    rows: list[VerifiedMinuteRowV2] = []
    byte_count = 0
    expected_timestamp = request.start
    for partition in partitions:
        lines = _iter_verified_minute_partition_lines(
            publication.publication_root / partition.path,
            partition,
        )
        for raw_line in lines:
            line = raw_line + b"\n"
            try:
                decoded = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("verified minute row is not JSON") from error
            envelope = _exact_mapping(
                decoded,
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
                "verified minute row envelope",
            )
            canonical = CanonicalMinuteRowV2.from_dict(
                {
                    key: value
                    for key, value in envelope.items()
                    if key not in {"source_identity", "origin_proof_sha256"}
                }
            )
            if not request.start <= canonical.timestamp < request.end:
                continue
            if (
                canonical.timestamp != expected_timestamp
                or canonical.symbol != request.symbol
                or canonical.timeframe != "1m"
                or envelope["source_identity"] != partition.source_identity
                or envelope["source_identity"] != publication.admitted_source_identity
                or envelope["origin_proof_sha256"] != partition.origin_proof_sha256
                or envelope["origin_proof_sha256"] != publication.origin_proof_sha256
                or _canonical_minute_row_bytes(
                    canonical,
                    source_identity=partition.source_identity,
                    origin_proof_sha256=partition.origin_proof_sha256,
                )
                != line
            ):
                raise ValueError("minute path contains a gap, mixed scope, or changed origin")
            rows.append(
                VerifiedMinuteRowV2(
                    timestamp=canonical.timestamp,
                    symbol=canonical.symbol,
                    timeframe=canonical.timeframe,
                    open=canonical.open,
                    high=canonical.high,
                    low=canonical.low,
                    close=canonical.close,
                    volume=canonical.volume,
                    source_identity=partition.source_identity,
                    origin_proof_sha256=partition.origin_proof_sha256,
                    line_sha256=hashlib.sha256(line).hexdigest(),
                    _factory_token=_MINUTE_ROW_FACTORY,
                )
            )
            byte_count += len(line)
            expected_timestamp += timedelta(minutes=1)
    if not rows or expected_timestamp != request.end:
        raise ValueError("minute path does not contain exactly one row per UTC minute")
    return tuple(rows), byte_count


def _iter_verified_minute_partition_lines(
    path: Path,
    partition: ValidationSourcePartitionV2,
) -> Iterator[bytes]:
    try:
        yield from iter_verified_regular_lines(
            path,
            expected_sha256=partition.sha256,
            expected_byte_count=partition.byte_count,
            expected_line_count=partition.row_count,
            maximum_line_bytes=_MAX_CANONICAL_ROW_BYTES - 1,
        )
    except RuntimeError as error:
        raise ValueError("minute partition bytes/checksum changed") from error


def _minute_path_identity(
    publication: ValidationSourcePublicationV2,
    request: BoundaryRequestV2,
    partitions: tuple[ValidationSourcePartitionV2, ...],
    rows: tuple[VerifiedMinuteRowV2, ...],
    byte_count: int,
    ordered_row_sha256: str,
    audit_binding: DevelopmentAccessAuditBindingV2,
) -> str:
    return hash_json(
        "phase5-validation-verified-minute-path-v2",
        {
            "source_publication_identity": publication.source_publication_identity.value,
            "request": {
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "start": _utc_text(request.start),
                "end": _utc_text(request.end),
                "operation_kind": request.operation_kind.value,
                "target_identity": request.target_identity,
            },
            "partitions": [item.to_dict() for item in partitions],
            "row_count": len(rows),
            "byte_count": byte_count,
            "ordered_row_sha256": ordered_row_sha256,
            "audit": {
                "programme_id": audit_binding.programme_id,
                "attempt_id": audit_binding.attempt_id,
                "boundary_sha256": audit_binding.boundary_sha256,
                "record_count": audit_binding.record_count,
                "terminal_record_sha256": (
                    audit_binding.terminal_record.record_sha256
                    if audit_binding.terminal_record is not None
                    else None
                ),
                "audit_identity": audit_binding.audit_identity,
            },
        },
    )


def _register_verified_minute_path(
    path: VerifiedMinutePathV2,
    *,
    publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    audit: DevelopmentAccessAttemptLedgerV2,
    audit_binding: DevelopmentAccessAuditBindingV2,
    expected_row_count: int,
    budget: MinutePathReadBudgetV2,
) -> None:
    identifier = id(path)
    snapshot = (
        path.request,
        path.rows,
        path.partition_paths,
        path.row_count,
        path.byte_count,
        path.ordered_row_sha256,
        path.audit_binding,
        path.path_identity,
    )

    def cleanup(reference: weakref.ReferenceType[VerifiedMinutePathV2]) -> None:
        current = _VERIFIED_MINUTE_PATHS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_MINUTE_PATHS.pop(identifier, None)

    _VERIFIED_MINUTE_PATHS[identifier] = (
        weakref.ref(path, cleanup),
        publication,
        coverage,
        split,
        boundary,
        availability,
        audit,
        audit_binding,
        path.request,
        expected_row_count,
        budget,
        snapshot,
    )


def _validate_registered_minute_path(
    path: VerifiedMinutePathV2,
    *,
    publication: ValidationSourcePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    audit: DevelopmentAccessAttemptLedgerV2,
    request: BoundaryRequestV2,
    expected_row_count: int,
    budget: MinutePathReadBudgetV2,
) -> tuple[object, ...]:
    if not isinstance(path, VerifiedMinutePathV2):
        raise TypeError("verified minute path must be factory-issued")
    registered = _VERIFIED_MINUTE_PATHS.get(id(path))
    if (
        registered is None
        or registered[0]() is not path
        or registered[1] is not publication
        or registered[2] is not coverage
        or registered[3] is not split
        or registered[4] is not boundary
        or registered[5] is not availability
        or registered[6] is not audit
        or registered[7] != path.audit_binding
        or registered[8] != request
        or registered[9] != expected_row_count
        or registered[10] != budget
    ):
        raise ValueError("verified minute path is not the registered original")
    current = (
        path.request,
        path.rows,
        path.partition_paths,
        path.row_count,
        path.byte_count,
        path.ordered_row_sha256,
        path.audit_binding,
        path.path_identity,
    )
    if current != registered[11]:
        raise ValueError("verified minute path serialization differs from original")
    return registered[11]


def _derive_verified_minute_source_origin(
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> _VerifiedMinuteSourceOriginV2 | None:
    """Derive the minute-source trust tuple from reopened admitted originals."""

    verified_scoped_source_availability_bytes_v2(availability, boundary=boundary)
    admitted = tuple(item for item in availability.candidates if item.admitted)
    if availability.status is ScopedSourceStatusV2.UNAVAILABLE:
        if admitted or availability.admitted_source_identity is not None:
            raise ValueError("unavailable source contains admitted origin evidence")
        return None
    if (
        availability.status is not ScopedSourceStatusV2.AVAILABLE
        or len(admitted) != 1
        or availability.admitted_source_identity != admitted[0].source_identity
    ):
        raise ValueError("available source has no unique admitted origin evidence")
    candidate = admitted[0]
    descriptor_path = Path(candidate.descriptor_path)
    descriptor_bytes = read_bounded_regular(descriptor_path, _MAX_DESCRIPTOR_BYTES)
    descriptor_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
    if descriptor_sha256 != candidate.descriptor_sha256:
        raise ValueError("admitted source descriptor original bytes changed")
    descriptor = _decode_canonical_object(descriptor_bytes, "admitted source descriptor")
    evidence_bindings: list[tuple[Path, bytes]] = [(descriptor_path, descriptor_bytes)]
    reopened: dict[Path, bytes] = {}

    def read_reference(path: Path, target: str) -> bytes:
        content = _read_candidate_reference(path, target)
        reopened[path] = content
        return content

    _verify_trusted_candidate_descriptor(
        descriptor,
        descriptor_bytes,
        descriptor_path=descriptor_path,
        boundary=boundary,
        read_reference=read_reference,
    )
    if descriptor["source_identity"] != candidate.source_identity:
        raise ValueError("admitted descriptor source identity differs")
    proof_payload: dict[str, object] = {
        "source_identity": candidate.source_identity,
        "boundary_sha256": boundary.boundary_sha256,
        "availability_sha256": availability.availability_sha256,
        "descriptor_sha256": descriptor_sha256,
    }
    for label in ("original_manifest", "predicate_evidence", "verifier_evidence"):
        evidence_path = _resolve_evidence_path(descriptor_path.parent, descriptor[f"{label}_path"])
        evidence_bytes = reopened[evidence_path]
        evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
        proof_payload[f"{label}_sha256"] = evidence_sha256
        evidence_bindings.append((evidence_path, evidence_bytes))
    bound_paths = {path for path, _content in evidence_bindings}
    evidence_bindings.extend(
        (path, content) for path, content in reopened.items() if path not in bound_paths
    )
    return _VerifiedMinuteSourceOriginV2(
        source_identity=candidate.source_identity,
        origin_kind="admitted-predicate-source-v2",
        origin_sha256=descriptor_sha256,
        origin_proof_sha256=hash_json(
            "phase5-development-minute-source-origin-proof-v2",
            proof_payload,
        ),
        evidence_bindings=tuple(evidence_bindings),
    )


def _origin_v3_ancestry(origin: _VerifiedMinuteSourceOriginV2) -> dict[str, Any]:
    descriptor_path, descriptor_bytes = origin.evidence_bindings[0]
    descriptor = _decode_canonical_object(descriptor_bytes, "admitted source descriptor")
    original_path = _resolve_evidence_path(
        descriptor_path.parent, descriptor["original_manifest_path"]
    )
    originals = dict(origin.evidence_bindings)
    original_bytes = originals.get(original_path)
    if original_bytes is None:
        raise ValueError("admitted source does not bind its original manifest")
    original = _decode_canonical_object(original_bytes, "admitted source original manifest")
    if original.get("schema_version") != "phase5-development-scoped-source-manifest-v3":
        raise ValueError("PostgreSQL minute source requires reviewed V3 ancestry")
    ancestry = original.get("source_ancestry")
    if not isinstance(ancestry, dict):
        raise ValueError("PostgreSQL minute source ancestry is missing")
    return ancestry


def _reconciled_view_mapping(origin: _VerifiedMinuteSourceOriginV2) -> CandleSourceMapping:
    ancestry = _origin_v3_ancestry(origin)
    version = ancestry.get("source_mapping_version")
    if not isinstance(version, str) or not version:
        raise ValueError("reviewed reconciled source mapping version is invalid")
    return CandleSourceMapping(
        version=version,
        schema="market_data",
        table="candles_reconciled",
        source_id_column="source_row_id",
        timestamp_column="open_time",
        timestamp_unit=TimestampUnit.MILLISECONDS,
        symbol_column="symbol",
        timeframe_column="interval",
        open_column="open",
        high_column="high",
        low_column="low",
        close_column="close",
        volume_column="volume",
        quote_volume_column="quote_volume",
        trades_column="trades",
    )


def _verify_postgresql_metadata_preflight(
    engine: Any,
    *,
    origin: _VerifiedMinuteSourceOriginV2,
    expected: _PostgresqlSourceMetadataV2 | None = None,
) -> _PostgresqlSourceMetadataV2:
    """Verify active promotion and view metadata without reading candle rows."""

    ancestry = _origin_v3_ancestry(origin)
    bindings = dict(origin.evidence_bindings)
    metadata = _verify_postgresql_metadata_preflight_from_ancestry(
        engine,
        ancestry=ancestry,
        bindings=bindings,
        expected=expected,
    )
    verifier_metadata = _origin_verifier_metadata_payload(origin)
    if metadata.verifier_payload() != verifier_metadata:
        raise ValueError("live PostgreSQL metadata differs from allowlisted verifier evidence")
    return metadata


def _verify_postgresql_metadata_preflight_from_ancestry(
    engine: Any,
    *,
    ancestry: Mapping[str, Any],
    bindings: Mapping[Path, bytes],
    expected: _PostgresqlSourceMetadataV2 | None = None,
) -> _PostgresqlSourceMetadataV2:
    rr_path = Path(str(ancestry["rr_promotion_receipt_path"]))
    recovery_path = Path(str(ancestry["recovery_manifest_path"]))
    view_path = Path(str(ancestry["reconciled_view_definition_path"]))
    try:
        receipt = _decode_canonical_object(bindings[rr_path], "RR-000008 promotion receipt")
        recovery = _decode_canonical_object(bindings[recovery_path], "RR-000008 recovery manifest")
        reviewed_view = bindings[view_path].decode("utf-8")
    except (KeyError, UnicodeDecodeError) as error:
        raise ValueError("PostgreSQL source ancestry bindings are incomplete") from error
    required_receipt = {
        "run_id": "RR-000008",
        "manifest_sha256": receipt.get("manifest_sha256"),
        "replacement_logical_sha256": receipt.get("replacement_logical_sha256"),
        "canonical_logical_sha256": receipt.get("canonical_logical_sha256"),
    }
    for label in (
        "manifest_sha256",
        "replacement_logical_sha256",
        "canonical_logical_sha256",
    ):
        value = required_receipt[label]
        if not isinstance(value, str):
            raise ValueError(f"RR-000008 receipt {label} is invalid")
        _require_sha256(value, f"RR-000008 receipt {label}")
    if receipt.get("run_id") != "RR-000008":
        raise ValueError("promotion receipt is not RR-000008")
    if (
        recovery.get("run_id") != "RR-000008"
        or recovery.get("run_manifest_sha256") != required_receipt["manifest_sha256"]
    ):
        raise ValueError("RR-000008 recovery manifest does not bind the promoted manifest")
    if "CREATE OR REPLACE VIEW market_data.candles_reconciled AS" not in reviewed_view:
        raise ValueError("reviewed migration does not define the reconciled source view")

    with engine.connect() as connection:
        row = connection.execute(text(_POSTGRESQL_SOURCE_PREFLIGHT_SQL)).mappings().one()
    promotion_count = row["promotion_count"]
    if isinstance(promotion_count, bool) or not isinstance(promotion_count, int):
        raise ValueError("live PostgreSQL promotion count is invalid")
    columns = row["view_columns"]
    if not isinstance(columns, (list, tuple)) or any(not isinstance(item, str) for item in columns):
        raise ValueError("live reconciled view columns are invalid")
    metadata = _PostgresqlSourceMetadataV2(
        promotion_count=promotion_count,
        run_id=_postgresql_metadata_text(row, "run_id"),
        manifest_sha256=_postgresql_metadata_sha(row, "manifest_sha256"),
        replacement_logical_sha256=_postgresql_metadata_sha(row, "replacement_logical_sha256"),
        canonical_logical_sha256=_postgresql_metadata_sha(row, "canonical_logical_sha256"),
        view_columns=tuple(columns),
        view_definition=_postgresql_metadata_text(row, "view_definition"),
    )
    if (
        metadata.promotion_count != 1
        or metadata.run_id != required_receipt["run_id"]
        or metadata.manifest_sha256 != required_receipt["manifest_sha256"]
        or metadata.replacement_logical_sha256 != required_receipt["replacement_logical_sha256"]
        or metadata.canonical_logical_sha256 != required_receipt["canonical_logical_sha256"]
    ):
        raise ValueError("live PostgreSQL promotion differs from RR-000008 ancestry")
    if metadata.view_columns != _RECONCILED_VIEW_COLUMNS:
        raise ValueError("live reconciled view columns differ from the reviewed mapping")
    if not isinstance(metadata.view_definition, str):
        raise ValueError("live reconciled view definition is unavailable")
    normalized = " ".join(metadata.view_definition.lower().replace('"', "").split())
    reviewed_normalized = " ".join(reviewed_view.lower().replace('"', "").split())
    required_view_fragments = (
        "market_data.candle_reconciliation_promotions",
        "market_data.candle_reconciliation_replacements",
        "market_data.candle_reconciliation_coverage",
        "market_data.candles",
        "promotion_id desc",
        "replacement.open_time = candle.open_time",
        "coverage.start_time <= candle.open_time",
        "candle.open_time < coverage.end_time",
    )
    if any(
        fragment not in normalized or fragment not in reviewed_normalized
        for fragment in required_view_fragments
    ):
        raise ValueError("live reconciled view definition differs from the reviewed contract")
    if expected is not None and metadata != expected:
        raise ValueError("live PostgreSQL source metadata changed after capability issuance")
    return metadata


def _postgresql_metadata_text(row: Mapping[str, Any], label: str) -> str:
    value = row[label]
    if not isinstance(value, str) or not value:
        raise ValueError(f"live PostgreSQL {label} is invalid")
    return value


def _postgresql_metadata_sha(row: Mapping[str, Any], label: str) -> str:
    value = _postgresql_metadata_text(row, label)
    _require_sha256(value, f"live PostgreSQL {label}")
    return value


def _canonical_view_definition_bytes(definition: str) -> bytes:
    if not isinstance(definition, str) or not definition.strip():
        raise ValueError("PostgreSQL view definition is invalid")
    return (" ".join(definition.split()) + "\n").encode()


def _verified_postgresql_metadata_payload(payload: object) -> dict[str, object]:
    values = _exact_mapping(
        payload,
        {
            "promotion_count",
            "run_id",
            "manifest_sha256",
            "replacement_logical_sha256",
            "canonical_logical_sha256",
            "view_columns",
            "normalized_view_definition_sha256",
        },
        "independent PostgreSQL verifier metadata",
    )
    if (
        isinstance(values["promotion_count"], bool)
        or not isinstance(values["promotion_count"], int)
        or values["promotion_count"] != 1
        or not isinstance(values["run_id"], str)
        or values["run_id"] != "RR-000008"
    ):
        raise ValueError("independent verifier does not bind active RR-000008")
    for label in (
        "manifest_sha256",
        "replacement_logical_sha256",
        "canonical_logical_sha256",
        "normalized_view_definition_sha256",
    ):
        if not isinstance(values[label], str):
            raise ValueError(f"independent verifier {label} is invalid")
        _require_sha256(values[label], f"independent verifier {label}")
    if values["view_columns"] != list(_RECONCILED_VIEW_COLUMNS):
        raise ValueError("independent verifier view columns differ from exact mapping")
    return values


def _origin_verifier_metadata_payload(
    origin: _VerifiedMinuteSourceOriginV2,
) -> dict[str, object]:
    for path, content in origin.evidence_bindings:
        if path.name == "verifier.json":
            verifier = _decode_canonical_object(content, "allowlisted verifier evidence")
            return _verified_postgresql_metadata_payload(verifier.get("postgresql_metadata"))
    raise ValueError("admitted origin does not bind independent PostgreSQL metadata")


def _origin_rr_ledger_inventory(
    origin: _VerifiedMinuteSourceOriginV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
) -> RRLedgerInventoryV2:
    for path, content in origin.evidence_bindings:
        if path.name == "verifier.json":
            verifier = _decode_canonical_object(content, "allowlisted verifier evidence")
            rr_payload = verifier.get("rr_ledger")
            expected_manifests, expected_parts = _rr_authority_from_rr_payload(rr_payload)
            return reopen_rr_ledger_inventory_v2(
                rr_payload,
                symbols=boundary.allowed_symbols,
                intervals=tuple((item.start, item.end) for item in boundary.allowed_intervals),
                expected_work_unit_manifest_sha256=expected_manifests,
                expected_comparison_part_sha256=expected_parts,
            )
    raise ValueError("admitted origin does not bind authenticated RR ledger inventory")


def _require_rr_promotion_run_binding(
    inventory: RRLedgerInventoryV2,
    promotion_manifest_sha256: object,
) -> None:
    if (
        not isinstance(promotion_manifest_sha256, str)
        or promotion_manifest_sha256 != inventory.run_manifest_sha256
    ):
        raise ValueError("RR ledger run manifest differs from authenticated promotion receipt")


def _verify_minute_publication_origin(
    publication: ValidationSourcePublicationV2,
    expected_origin: _VerifiedMinuteSourceOriginV2 | None,
) -> None:
    actual = (
        publication.admitted_source_identity,
        publication.origin_kind,
        publication.origin_sha256,
        publication.origin_proof_sha256,
    )
    expected = (
        (None, None, None, None)
        if expected_origin is None
        else (
            expected_origin.source_identity,
            expected_origin.origin_kind,
            expected_origin.origin_sha256,
            expected_origin.origin_proof_sha256,
        )
    )
    if actual != expected:
        raise ValueError("validation source origin differs from admitted original evidence")


def _derive_required_minute_source_origin(
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> _VerifiedMinuteSourceOriginV2:
    origin = _derive_verified_minute_source_origin(boundary=boundary, availability=availability)
    if origin is None:
        raise ValueError("verified minute source lost its admitted origin")
    return origin


def _verify_minute_reader_binding(
    capability: VerifiedDevelopmentMinuteSourceV2 | None,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    *,
    revalidate_evidence: bool = True,
) -> tuple[str, str, str, str]:
    if capability is None:
        raise ValueError("available scoped source requires a verified source capability")
    _verify_registered_minute_source_capability(
        capability,
        boundary=boundary,
        availability=availability,
        revalidate_evidence=revalidate_evidence,
    )
    admitted = tuple(item for item in availability.candidates if item.admitted)
    if len(admitted) != 1:
        raise ValueError("available scoped source has no unique admitted origin")
    if (
        capability.source_identity != availability.admitted_source_identity
        or capability.source_identity != admitted[0].source_identity
        or capability.origin_kind != "admitted-predicate-source-v2"
        or capability.origin_sha256 != admitted[0].descriptor_sha256
        or capability.boundary_sha256 != boundary.boundary_sha256
        or capability.availability_sha256 != availability.availability_sha256
    ):
        raise ValueError(
            "minute source capability has a mixed, stale, or unverified parent binding"
        )
    return (
        capability.source_identity,
        capability.origin_kind,
        capability.origin_sha256,
        capability.origin_proof_sha256,
    )


def _validate_minute_publication_parent_bindings(
    publication: ValidationSourcePublicationV2,
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
) -> None:
    if (
        publication.coverage_identity != coverage.coverage_identity
        or publication.split_identity != split.split_identity
        or publication.boundary_sha256 != boundary.boundary_sha256
        or publication.source_availability_sha256 != availability.availability_sha256
        or publication.source_audit_sha256 != availability.audit_publication_sha256
        or publication.reconciliation_identity_sha256 != coverage.reconciliation.identity_sha256
        or publication.raw_dump_identity_sha256 != coverage.raw_dump.identity_sha256
        or publication.source_mapping_version != coverage.raw_dump.source_mapping_version
        or publication.allowed_symbols != boundary.allowed_symbols
        or publication.allowed_intervals
        != tuple(
            (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
        )
        or publication.status is not availability.status
    ):
        raise ValueError("validation source parent or scope binding is stale")


def _register_minute_source_capability(
    capability: VerifiedDevelopmentMinuteSourceV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    availability_bytes: bytes,
    evidence_bindings: tuple[tuple[Path, bytes], ...],
    implementation: _FixtureMinuteReadImplementationV2 | _PostgresqlMinuteReadImplementationV2,
) -> None:
    identifier = id(capability)

    def cleanup(
        reference: weakref.ReferenceType[VerifiedDevelopmentMinuteSourceV2],
    ) -> None:
        current = _VERIFIED_MINUTE_SOURCE_CAPABILITIES.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_MINUTE_SOURCE_CAPABILITIES.pop(identifier, None)
            registered_implementation = current[5]
            if isinstance(registered_implementation, _PostgresqlMinuteReadImplementationV2):
                registered_implementation.engine.dispose()

    reference = weakref.ref(capability, cleanup)
    _VERIFIED_MINUTE_SOURCE_CAPABILITIES[identifier] = (
        reference,
        capability.canonical_bytes,
        weakref.ref(boundary),
        weakref.ref(availability),
        evidence_bindings,
        implementation,
    )
    if availability_bytes != availability.canonical_bytes:
        raise ValueError("minute source capability availability bytes changed")


def _registered_minute_source_implementation(
    capability: VerifiedDevelopmentMinuteSourceV2,
) -> _FixtureMinuteReadImplementationV2 | _PostgresqlMinuteReadImplementationV2:
    registered = _VERIFIED_MINUTE_SOURCE_CAPABILITIES.get(id(capability))
    if registered is None or registered[0]() is not capability:
        raise ValueError("minute source capability is closed or unregistered")
    return registered[5]


def _close_minute_source_capability(capability: VerifiedDevelopmentMinuteSourceV2) -> None:
    registered = _VERIFIED_MINUTE_SOURCE_CAPABILITIES.pop(id(capability), None)
    if registered is None or registered[0]() is not capability:
        return
    implementation = registered[5]
    if isinstance(implementation, _PostgresqlMinuteReadImplementationV2):
        implementation.engine.dispose()


def _verify_registered_minute_source_capability(
    capability: VerifiedDevelopmentMinuteSourceV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    revalidate_evidence: bool = True,
) -> _FixtureMinuteReadImplementationV2 | _PostgresqlMinuteReadImplementationV2:
    if not isinstance(capability, VerifiedDevelopmentMinuteSourceV2):
        raise TypeError("source reader must be a verifier-issued capability")
    registered = _VERIFIED_MINUTE_SOURCE_CAPABILITIES.get(id(capability))
    if (
        registered is None
        or registered[0]() is not capability
        or registered[1] != capability.canonical_bytes
        or registered[2]() is not boundary
        or registered[3]() is not availability
    ):
        raise ValueError("minute source capability is not the registered original")
    if revalidate_evidence:
        verified_scoped_source_availability_bytes_v2(availability, boundary=boundary)
        for path, original_bytes in registered[4]:
            if read_bounded_regular(path, _MAX_DESCRIPTOR_BYTES) != original_bytes:
                raise ValueError("minute source capability original evidence changed")
    return registered[5]


def _iter_verified_minute_source_rows(
    capability: VerifiedDevelopmentMinuteSourceV2 | None,
    *,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    request: BoundaryRequestV2,
    request_index: int,
    progress: Callable[[Mapping[str, int]], None] | None = None,
) -> Any:
    if capability is None:
        raise ValueError("verified minute source capability is required")
    if (
        request.operation_kind is not AccessOperationKindV2.ITERATOR
        or request.target_identity != capability.origin_sha256
        or request.timeframe != "1m"
        or request.symbol not in boundary.allowed_symbols
        or (request.start, request.end)
        not in {(item.start, item.end) for item in boundary.allowed_intervals}
    ):
        raise PermissionError("request differs from the exact one-minute development boundary")
    implementation = _verify_registered_minute_source_capability(
        capability,
        boundary=boundary,
        availability=availability,
        revalidate_evidence=False,
    )
    boundary.authorize(request)
    if isinstance(implementation, _FixtureMinuteReadImplementationV2):
        if implementation.fail_request_index == request_index:
            raise RuntimeError("fixture verifier-owned minute source interrupted")
        rows = implementation.request_rows[request_index]

        def verified_fixture_rows() -> Any:
            for row in rows:
                _verify_registered_minute_source_capability(
                    capability,
                    boundary=boundary,
                    availability=availability,
                    revalidate_evidence=False,
                )
                boundary.authorize(request)
                yield row

        return verified_fixture_rows()

    def verified_postgresql_rows() -> Any:
        _verify_registered_minute_source_capability(
            capability,
            boundary=boundary,
            availability=availability,
            revalidate_evidence=False,
        )
        boundary.authorize(request)
        _verify_postgresql_metadata_preflight(
            implementation.engine,
            origin=implementation.origin,
            expected=implementation.expected_metadata,
        )
        yield from _iter_rr_authenticated_postgresql_rows(
            implementation,
            request=request,
            progress=progress,
        )

    return verified_postgresql_rows()


_EXACT_POSTGRESQL_MINUTE_SQL = text(
    """
SELECT source.symbol, source."interval" AS timeframe,
       source.open_time AS open_time_ms,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.open::text ELSE source.open::text END AS open,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.high::text ELSE source.high::text END AS high,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.low::text ELSE source.low::text END AS low,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.close::text ELSE source.close::text END AS close,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.volume::text ELSE source.volume::text END AS volume,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.quote_volume::text ELSE source.quote_volume::text END AS quote_volume,
       CASE WHEN source.origin IN ('binance_correction', 'binance_fill')
            THEN replacement.trades::text ELSE source.trades::text END AS trades,
       source.origin, source.recovery_run_id, source.reconciliation_run_id
FROM market_data.candles_reconciled AS source
LEFT JOIN market_data.candle_reconciliation_replacements AS replacement
  ON replacement.run_id='RR-000008'
 AND replacement.symbol=source.symbol
 AND replacement."interval"=source."interval"
 AND replacement.open_time=source.open_time
WHERE source.symbol=:symbol AND source."interval"='1m'
  AND source.open_time >= :start_ms
  AND source.open_time < :end_ms
ORDER BY source.open_time
"""
).execution_options(stream_results=True)


def _iter_exact_postgresql_minute_rows(
    implementation: _PostgresqlMinuteReadImplementationV2,
    *,
    request: BoundaryRequestV2,
    on_batch: Callable[[int], None] | None = None,
) -> Iterator[Mapping[str, Any]]:
    """Yield exact text/Decimal inputs; never route trust values through Float64."""

    with implementation.engine.connect() as connection:
        result = connection.execution_options(
            stream_results=True,
            yield_per=implementation.batch_size,
        ).execute(
            _EXACT_POSTGRESQL_MINUTE_SQL,
            {
                "symbol": request.symbol,
                "start_ms": int(request.start.timestamp() * 1000),
                "end_ms": int(request.end.timestamp() * 1000),
            },
        )
        mappings = result.mappings()
        while True:
            batch = mappings.fetchmany(implementation.batch_size)
            if not batch:
                return
            if len(batch) > implementation.batch_size:
                raise ValueError("PostgreSQL exact iterator exceeded its batch ceiling")
            if on_batch is not None:
                on_batch(len(batch))
            for row in batch:
                yield dict(row)


def _iter_rr_authenticated_postgresql_rows(
    implementation: _PostgresqlMinuteReadImplementationV2,
    *,
    request: BoundaryRequestV2,
    progress: Callable[[Mapping[str, int]], None] | None = None,
) -> Iterator[CanonicalMinuteRowV2]:
    preflight_scoped_rr_ledger_parts_v2(
        implementation.rr_inventory,
        symbol=request.symbol,
        start=request.start,
        end=request.end,
    )
    counts = {"ledger": 0, "database": 0, "emitted": 0, "unavailable": 0}

    def checkpoint() -> None:
        if progress is not None:
            progress(counts)

    def ledger_batch(size: int) -> None:
        counts["ledger"] += size
        checkpoint()

    def database_batch(size: int) -> None:
        counts["database"] += size
        checkpoint()

    ledger = iter_scoped_rr_ledger_rows_v2(
        implementation.rr_inventory,
        symbol=request.symbol,
        start=request.start,
        end=request.end,
        batch_size=implementation.batch_size,
        on_batch=ledger_batch,
        preflight_verified=True,
    )
    source: Iterator[Mapping[str, Any]] | None = None
    previous_source_ms: int | None = None
    try:
        for ledger_row in ledger:
            if not ledger_row.admits_candle:
                counts["unavailable"] += 1
                continue
            if source is None:
                source = iter(
                    _iter_exact_postgresql_minute_rows(
                        implementation,
                        request=request,
                        on_batch=database_batch,
                    )
                )
            source_row = next(source, None)
            if source_row is None:
                raise ValueError("PostgreSQL is missing an RR-authenticated admitted row")
            source_ms = _exact_source_open_time_ms(source_row)
            if previous_source_ms is not None and source_ms <= previous_source_ms:
                raise ValueError("PostgreSQL minute rows are duplicated or reordered")
            if source_ms != ledger_row.open_time_ms:
                raise ValueError("PostgreSQL contains an extra or missing RR-authenticated row")
            candle, origin = _exact_recovery_candle(source_row)
            if (
                candle.symbol != ledger_row.symbol
                or candle.timeframe != "1m"
                or candle.row_checksum() != ledger_row.expected_row_sha256
                or origin != ledger_row.expected_origin
                or source_row.get("reconciliation_run_id") != "RR-000008"
            ):
                raise ValueError("PostgreSQL row differs from RR authenticated content")
            previous_source_ms = source_ms
            counts["emitted"] += 1
            yield CanonicalMinuteRowV2(
                timestamp=datetime.fromtimestamp(candle.open_time_ms / 1000, tz=UTC),
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
        if source is not None and next(source, None) is not None:
            raise ValueError("PostgreSQL contains unaccounted rows after RR ledger exhaustion")
    except BaseException:
        checkpoint()
        raise
    checkpoint()


def _exact_source_open_time_ms(row: Mapping[str, Any]) -> int:
    value = row.get("open_time_ms")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("PostgreSQL exact row timestamp is invalid")
    return value


def _exact_recovery_candle(row: Mapping[str, Any]) -> tuple[RecoveryCandle, str]:
    required = ("symbol", "timeframe", "open", "high", "low", "close", "volume", "origin")
    if any(not isinstance(row.get(label), str) for label in required):
        raise ValueError("PostgreSQL exact row text mapping is invalid")
    candle = RecoveryCandle(
        symbol=str(row["symbol"]),
        timeframe=str(row["timeframe"]),
        open_time_ms=_exact_source_open_time_ms(row),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
        quote_volume=(
            None if row.get("quote_volume") is None else Decimal(str(row["quote_volume"]))
        ),
        trades=None if row.get("trades") is None else int(str(row["trades"])),
    )
    return candle, str(row["origin"])


def _stream_minute_request(
    iterator: Any,
    *,
    request: BoundaryRequestV2,
    interval_index: int,
    publication_stage: Path,
    starting_partition_count: int,
    starting_row_count: int,
    starting_byte_count: int,
    max_rows_per_partition: int,
    max_total_rows: int,
    max_total_bytes: int,
    max_partitions: int,
    source_identity: str,
    origin_proof_sha256: str,
) -> tuple[list[ValidationSourcePartitionV2], int, int]:
    partitions: list[ValidationSourcePartitionV2] = []
    row_buffer: list[tuple[CanonicalMinuteRowV2, bytes]] = []
    request_rows = 0
    request_bytes = 0
    prior_timestamp: datetime | None = None
    partition_index = 0

    def flush() -> None:
        nonlocal partition_index
        if not row_buffer:
            return
        if starting_partition_count + len(partitions) >= max_partitions:
            raise ValueError("validation source publication exceeds partition ceiling")
        relative = (
            Path("partitions")
            / f"symbol={request.symbol}"
            / f"interval={interval_index:04d}"
            / f"part={partition_index:06d}.jsonl"
        )
        content = b"".join(item[1] for item in row_buffer)
        destination = publication_stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_no_clobber(destination, content)
        partitions.append(
            ValidationSourcePartitionV2(
                path=relative.as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
                byte_count=len(content),
                row_count=len(row_buffer),
                symbol=request.symbol,
                interval_index=interval_index,
                interval_start=_utc_text(request.start),
                interval_end=_utc_text(request.end),
                min_timestamp=_utc_text(row_buffer[0][0].timestamp),
                max_timestamp=_utc_text(row_buffer[-1][0].timestamp),
                source_identity=source_identity,
                origin_proof_sha256=origin_proof_sha256,
            )
        )
        row_buffer.clear()
        partition_index += 1

    for row in iterator:
        if not isinstance(row, CanonicalMinuteRowV2):
            raise TypeError("minute reader must yield CanonicalMinuteRowV2")
        if (
            row.symbol != request.symbol
            or row.timeframe != request.timeframe
            or not request.start <= row.timestamp < request.end
        ):
            raise PermissionError("minute row exceeds its authorized half-open request")
        if prior_timestamp is not None and row.timestamp <= prior_timestamp:
            raise ValueError("minute reader rows must be strictly ordered and unique")
        content = _canonical_minute_row_bytes(
            row,
            source_identity=source_identity,
            origin_proof_sha256=origin_proof_sha256,
        )
        if len(content) > _MAX_CANONICAL_ROW_BYTES:
            raise ValueError("canonical minute row exceeds byte ceiling")
        if starting_row_count + request_rows >= max_total_rows:
            raise ValueError("validation source publication exceeds row ceiling")
        if starting_byte_count + request_bytes + len(content) > max_total_bytes:
            raise ValueError("validation source publication exceeds byte ceiling")
        row_buffer.append((row, content))
        request_rows += 1
        request_bytes += len(content)
        prior_timestamp = row.timestamp
        if len(row_buffer) == max_rows_per_partition:
            flush()
    flush()
    return partitions, request_rows, request_bytes


def _append_minute_publication_audit_record(
    records_dir: Path,
    *,
    sequence: int,
    phase: str,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    request: BoundaryRequestV2,
    origin_kind: str,
    origin_sha256: str,
    origin_proof_sha256: str,
    row_count: int,
    byte_count: int,
    partition_set_sha256: str | None,
    prior: str | None,
) -> str:
    payload = {
        "sequence": sequence,
        "phase": phase,
        "boundary_sha256": boundary.boundary_sha256,
        "source_availability_sha256": availability.availability_sha256,
        "source_audit_sha256": availability.audit_publication_sha256,
        "origin_kind": origin_kind,
        "origin_sha256": origin_sha256,
        "origin_proof_sha256": origin_proof_sha256,
        "request": {
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "start": _utc_text(request.start),
            "end": _utc_text(request.end),
            "operation_kind": request.operation_kind.value,
            "target_identity": request.target_identity,
        },
        "allowed": True,
        "row_count": row_count,
        "byte_count": byte_count,
        "partition_set_sha256": partition_set_sha256,
        "prior_record_sha256": prior,
    }
    digest = hash_json(_MINUTE_PUBLICATION_AUDIT_RECORD_DOMAIN, payload)
    _write_no_clobber(
        records_dir / f"{sequence:08d}-{digest}.json",
        publication_json_bytes({**payload, "record_sha256": digest}),
    )
    return digest


def _minute_publication_identity_payload(
    *,
    status: ScopedSourceStatusV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    admitted_source_identity: str | None,
    origin_kind: str | None,
    origin_sha256: str | None,
    origin_proof_sha256: str | None,
    partitions: tuple[ValidationSourcePartitionV2, ...],
    row_count: int,
    byte_count: int,
    failure: str | None,
    audit_publication_sha256: str,
) -> dict[str, object]:
    return {
        "status": status.value,
        "coverage_identity": coverage.coverage_identity.value,
        "split_identity": split.split_identity.value,
        "boundary_sha256": boundary.boundary_sha256,
        "source_availability_sha256": availability.availability_sha256,
        "source_audit_sha256": availability.audit_publication_sha256,
        "admitted_source_identity": admitted_source_identity,
        "origin_kind": origin_kind,
        "origin_sha256": origin_sha256,
        "origin_proof_sha256": origin_proof_sha256,
        "reconciliation_identity_sha256": coverage.reconciliation.identity_sha256,
        "raw_dump_identity_sha256": coverage.raw_dump.identity_sha256,
        "source_mapping_version": coverage.raw_dump.source_mapping_version,
        "allowed_symbols": list(boundary.allowed_symbols),
        "allowed_timeframe": "1m",
        "allowed_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)}
            for item in boundary.allowed_intervals
        ],
        "partitions": [item.to_dict() for item in partitions],
        "row_count": row_count,
        "byte_count": byte_count,
        "failure": failure,
        "audit_publication_sha256": audit_publication_sha256,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }


def _validation_source_publication_from_dict(
    payload: object,
    *,
    publication_root: Path,
    audit_ledger_root: Path,
    canonical_bytes: bytes,
) -> ValidationSourcePublicationV2:
    values = _exact_mapping(
        payload,
        {
            "schema_version",
            "status",
            "coverage_identity",
            "split_identity",
            "boundary_sha256",
            "source_availability_sha256",
            "source_audit_sha256",
            "admitted_source_identity",
            "origin_kind",
            "origin_sha256",
            "origin_proof_sha256",
            "reconciliation_identity_sha256",
            "raw_dump_identity_sha256",
            "source_mapping_version",
            "allowed_symbols",
            "allowed_timeframe",
            "allowed_intervals",
            "partitions",
            "row_count",
            "byte_count",
            "failure",
            "audit_publication_sha256",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "source_publication_identity",
        },
        "validation source publication",
    )
    if values["schema_version"] != "phase5-validation-development-minute-source-v2":
        raise ValueError("validation source publication schema is invalid")
    raw_intervals = values["allowed_intervals"]
    raw_partitions = values["partitions"]
    raw_symbols = values["allowed_symbols"]
    if (
        not isinstance(raw_intervals, list)
        or not isinstance(raw_partitions, list)
        or not isinstance(raw_symbols, list)
    ):
        raise TypeError("validation source publication collections are invalid")
    intervals = tuple(
        (
            _exact_mapping(item, {"start", "end"}, "allowed interval")["start"],
            _exact_mapping(item, {"start", "end"}, "allowed interval")["end"],
        )
        for item in raw_intervals
    )
    return ValidationSourcePublicationV2(
        status=ScopedSourceStatusV2(values["status"]),
        coverage_identity=SourceCoverageIdentityV2(values["coverage_identity"]),
        split_identity=DevelopmentSplitIdentityV2(values["split_identity"]),
        boundary_sha256=values["boundary_sha256"],  # type: ignore[arg-type]
        source_availability_sha256=values["source_availability_sha256"],  # type: ignore[arg-type]
        source_audit_sha256=values["source_audit_sha256"],  # type: ignore[arg-type]
        admitted_source_identity=values["admitted_source_identity"],  # type: ignore[arg-type]
        origin_kind=values["origin_kind"],  # type: ignore[arg-type]
        origin_sha256=values["origin_sha256"],  # type: ignore[arg-type]
        origin_proof_sha256=values["origin_proof_sha256"],  # type: ignore[arg-type]
        reconciliation_identity_sha256=values[  # type: ignore[arg-type]
            "reconciliation_identity_sha256"
        ],
        raw_dump_identity_sha256=values["raw_dump_identity_sha256"],  # type: ignore[arg-type]
        source_mapping_version=values["source_mapping_version"],  # type: ignore[arg-type]
        allowed_symbols=tuple(raw_symbols),  # type: ignore[arg-type]
        allowed_timeframe=values["allowed_timeframe"],  # type: ignore[arg-type]
        allowed_intervals=intervals,  # type: ignore[arg-type]
        partitions=tuple(ValidationSourcePartitionV2.from_dict(item) for item in raw_partitions),
        row_count=values["row_count"],  # type: ignore[arg-type]
        byte_count=values["byte_count"],  # type: ignore[arg-type]
        failure=values["failure"],  # type: ignore[arg-type]
        audit_publication_sha256=values["audit_publication_sha256"],  # type: ignore[arg-type]
        final_scope_attempts=values["final_scope_attempts"],  # type: ignore[arg-type]
        final_rows=values["final_rows"],  # type: ignore[arg-type]
        final_access_records=values["final_access_records"],  # type: ignore[arg-type]
        source_publication_identity=SourcePublicationIdentityV2(
            values["source_publication_identity"]  # type: ignore[arg-type]
        ),
        publication_root=publication_root,
        audit_ledger_root=audit_ledger_root,
        canonical_bytes=canonical_bytes,
        _factory_token=_MINUTE_PUBLICATION_FACTORY,
    )


def _verify_minute_publication_audit(
    publication_path: Path,
    *,
    publication: ValidationSourcePublicationV2,
    expected_origin: _VerifiedMinuteSourceOriginV2 | None,
) -> _MinuteAuditVerificationV2:
    content = read_bounded_regular(publication_path, _MAX_MINUTE_PUBLICATION_BYTES)
    public = _decode_canonical_object(content, "minute publication audit")
    values = _exact_mapping(
        public,
        {
            "schema_version",
            "boundary_sha256",
            "source_availability_sha256",
            "source_audit_sha256",
            "admitted_source_identity",
            "origin_kind",
            "origin_sha256",
            "origin_proof_sha256",
            "record_count",
            "terminal_record_sha256",
            "authentication_progress",
            "progress_record_count",
            "terminal_progress_sha256",
            "progress_inventory_sha256",
            "rows_admitted",
            "bytes_admitted",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "audit_publication_sha256",
        },
        "minute publication audit",
    )
    payload = {key: value for key, value in values.items() if key != "audit_publication_sha256"}
    expected_source_identity = None if expected_origin is None else expected_origin.source_identity
    expected_origin_kind = None if expected_origin is None else expected_origin.origin_kind
    expected_origin_sha256 = None if expected_origin is None else expected_origin.origin_sha256
    expected_origin_proof_sha256 = (
        None if expected_origin is None else expected_origin.origin_proof_sha256
    )
    if (
        values["schema_version"] != "phase5-validation-minute-publication-audit-v2"
        or values["boundary_sha256"] != publication.boundary_sha256
        or values["source_availability_sha256"] != publication.source_availability_sha256
        or values["source_audit_sha256"] != publication.source_audit_sha256
        or values["admitted_source_identity"] != publication.admitted_source_identity
        or values["admitted_source_identity"] != expected_source_identity
        or values["origin_kind"] != publication.origin_kind
        or values["origin_kind"] != expected_origin_kind
        or values["origin_sha256"] != publication.origin_sha256
        or values["origin_sha256"] != expected_origin_sha256
        or values["origin_proof_sha256"] != publication.origin_proof_sha256
        or values["origin_proof_sha256"] != expected_origin_proof_sha256
        or values["rows_admitted"] != publication.row_count
        or values["bytes_admitted"] != publication.byte_count
        or values["final_scope_attempts"] != 0
        or values["final_rows"] != 0
        or values["final_access_records"] != 0
        or values["audit_publication_sha256"]
        != hash_json(_MINUTE_PUBLICATION_AUDIT_DOMAIN, payload)
        or values["audit_publication_sha256"] != publication.audit_publication_sha256
    ):
        raise ValueError("minute publication audit binding is invalid")
    _verify_authentication_progress_v2(publication_path.parent / "progress", values)
    records = bounded_regular_files(
        publication_path.parent / "records",
        maximum=_MAX_MINUTE_PUBLICATION_ENTRIES,
    )
    if values["record_count"] != len(records):
        raise ValueError("minute publication audit record count differs")
    expected_requests = tuple(
        (symbol, interval_index, start, end)
        for symbol in publication.allowed_symbols
        for interval_index, (start, end) in enumerate(publication.allowed_intervals)
    )
    expected_record_count = (
        len(expected_requests) * 2 if publication.status is ScopedSourceStatusV2.AVAILABLE else 0
    )
    if len(records) != expected_record_count:
        raise ValueError("minute publication audit request coverage differs")
    prior: str | None = None
    rows = 0
    byte_count = 0
    completions: list[_MinuteAuditCompletionV2] = []
    for sequence, relative in enumerate(records, start=1):
        record = _decode_canonical_object(
            read_bounded_regular(
                publication_path.parent / "records" / relative,
                _MAX_DESCRIPTOR_BYTES,
            ),
            "minute publication audit record",
        )
        if set(record) != {
            "sequence",
            "phase",
            "boundary_sha256",
            "source_availability_sha256",
            "source_audit_sha256",
            "origin_kind",
            "origin_sha256",
            "origin_proof_sha256",
            "request",
            "allowed",
            "row_count",
            "byte_count",
            "partition_set_sha256",
            "prior_record_sha256",
            "record_sha256",
        }:
            raise ValueError("minute publication audit record schema is invalid")
        digest = record.get("record_sha256")
        record_payload = {key: value for key, value in record.items() if key != "record_sha256"}
        request_index = (sequence - 1) // 2
        symbol, _, start, end = expected_requests[request_index]
        request = record.get("request")
        expected_phase = "start" if sequence % 2 else "completion"
        if (
            digest != hash_json(_MINUTE_PUBLICATION_AUDIT_RECORD_DOMAIN, record_payload)
            or record.get("sequence") != sequence
            or record.get("phase") != expected_phase
            or record.get("prior_record_sha256") != prior
            or record.get("boundary_sha256") != publication.boundary_sha256
            or record.get("source_availability_sha256") != publication.source_availability_sha256
            or record.get("source_audit_sha256") != publication.source_audit_sha256
            or record.get("origin_kind") != publication.origin_kind
            or record.get("origin_kind") != expected_origin_kind
            or record.get("origin_sha256") != publication.origin_sha256
            or record.get("origin_sha256") != expected_origin_sha256
            or record.get("origin_proof_sha256") != publication.origin_proof_sha256
            or record.get("origin_proof_sha256") != expected_origin_proof_sha256
            or record.get("allowed") is not True
            or request
            != {
                "symbol": symbol,
                "timeframe": "1m",
                "start": start,
                "end": end,
                "operation_kind": AccessOperationKindV2.ITERATOR.value,
                "target_identity": expected_origin_sha256,
            }
        ):
            raise ValueError("minute publication audit chain or scope is invalid")
        record_rows = _nonnegative_count(record.get("row_count"), "audit rows")
        record_bytes = _nonnegative_count(record.get("byte_count"), "audit bytes")
        partition_set_sha256 = record.get("partition_set_sha256")
        if expected_phase == "start" and (
            record_rows or record_bytes or partition_set_sha256 is not None
        ):
            raise ValueError("minute publication audit start contains output")
        if expected_phase == "completion":
            if record_rows < 1 or record_bytes < 1:
                raise ValueError("minute publication audit completion is empty")
            partition_set_sha256 = _require_sha256(
                partition_set_sha256, "audit partition_set_sha256"
            )
            rows += record_rows
            byte_count += record_bytes
            completions.append(
                _MinuteAuditCompletionV2(
                    symbol=symbol,
                    interval_index=expected_requests[request_index][1],
                    row_count=record_rows,
                    byte_count=record_bytes,
                    partition_set_sha256=partition_set_sha256,
                )
            )
        prior = digest  # type: ignore[assignment]
    if (
        values["terminal_record_sha256"] != prior
        or rows != publication.row_count
        or byte_count != publication.byte_count
    ):
        raise ValueError("minute publication audit terminal totals differ")
    return _MinuteAuditVerificationV2(
        canonical_bytes=content,
        completions=tuple(completions),
    )


def _verify_authentication_progress_v2(
    progress_root: Path,
    audit: Mapping[str, object],
) -> None:
    files = bounded_regular_files(progress_root, maximum=_MAX_MINUTE_PUBLICATION_ENTRIES)
    count = audit.get("progress_record_count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(files):
        raise ValueError("minute authentication progress count differs")
    prior: str | None = None
    digests: list[str] = []
    previous_counts = {"ledger": 0, "database": 0, "emitted": 0, "unavailable": 0}
    for sequence, relative in enumerate(files, start=1):
        record = _decode_canonical_object(
            read_bounded_regular(progress_root / relative, _MAX_DESCRIPTOR_BYTES),
            "minute authentication progress",
        )
        values = _exact_mapping(
            record,
            {
                "sequence",
                "boundary_sha256",
                "request",
                "counts",
                "prior_progress_sha256",
                "final_scope_attempts",
                "final_rows",
                "final_access_records",
                "progress_sha256",
            },
            "minute authentication progress",
        )
        counts = _exact_mapping(
            values["counts"],
            {"ledger", "database", "emitted", "unavailable"},
            "minute authentication progress counts",
        )
        normalized = {
            key: _nonnegative_count(counts[key], f"progress {key}") for key in previous_counts
        }
        payload = {key: value for key, value in values.items() if key != "progress_sha256"}
        digest = hash_json("phase5-validation-minute-auth-progress-v2", payload)
        if (
            values["sequence"] != sequence
            or values["prior_progress_sha256"] != prior
            or values["progress_sha256"] != digest
            or any(normalized[key] < previous_counts[key] for key in normalized)
            or values["final_scope_attempts"] != 0
            or values["final_rows"] != 0
            or values["final_access_records"] != 0
        ):
            raise ValueError("minute authentication progress chain is invalid")
        prior = digest
        digests.append(digest)
        previous_counts = normalized
    if (
        audit.get("terminal_progress_sha256") != prior
        or audit.get("progress_inventory_sha256")
        != hash_json("phase5-validation-minute-auth-progress-inventory-v2", digests)
        or audit.get("authentication_progress") != previous_counts
    ):
        raise ValueError("minute authentication progress terminal binding differs")


def _verify_failed_minute_access_audit_v2(audit_root: Path) -> None:
    content = read_bounded_regular(audit_root / "failure.json", _MAX_DESCRIPTOR_BYTES)
    record = _decode_canonical_object(content, "failed minute access audit")
    values = _exact_mapping(
        record,
        {
            "schema_version",
            "boundary_sha256",
            "source_availability_sha256",
            "active_request",
            "completed_rows",
            "completed_bytes",
            "terminal_record_sha256",
            "authentication_progress",
            "progress_record_count",
            "terminal_progress_sha256",
            "progress_inventory_sha256",
            "failure_type",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "failure_sha256",
        },
        "failed minute access audit",
    )
    payload = {key: value for key, value in values.items() if key != "failure_sha256"}
    if (
        values["schema_version"] != "phase5-validation-minute-access-attempt-failure-v2"
        or values["failure_sha256"]
        != hash_json("phase5-validation-minute-access-attempt-failure-v2", payload)
        or values["final_scope_attempts"] != 0
        or values["final_rows"] != 0
        or values["final_access_records"] != 0
    ):
        raise ValueError("failed minute access audit binding is invalid")
    _verify_authentication_progress_v2(audit_root / "progress", values)


def _verify_minute_partition_tree(
    publication: ValidationSourcePublicationV2,
    *,
    audit: _MinuteAuditVerificationV2,
    expected_origin: _VerifiedMinuteSourceOriginV2 | None,
) -> None:
    files = bounded_regular_files(
        publication.publication_root,
        maximum=_MAX_MINUTE_PUBLICATION_ENTRIES,
    )
    expected_files = tuple(
        sorted(("_SUCCESS", "publication.json", *(item.path for item in publication.partitions)))
    )
    if files != expected_files:
        raise ValueError("validation source publication contains missing or extra artifacts")
    total_rows = 0
    total_bytes = 0
    prior_key: tuple[str, int, datetime] | None = None
    group_part_index: dict[tuple[str, int], int] = {}
    group_partitions: dict[tuple[str, int], list[ValidationSourcePartitionV2]] = {}
    expected_source_identity = None if expected_origin is None else expected_origin.source_identity
    expected_origin_proof_sha256 = (
        None if expected_origin is None else expected_origin.origin_proof_sha256
    )
    for partition in publication.partitions:
        if (
            partition.symbol not in publication.allowed_symbols
            or partition.interval_index >= len(publication.allowed_intervals)
            or (
                partition.interval_start,
                partition.interval_end,
            )
            != publication.allowed_intervals[partition.interval_index]
            or partition.source_identity != publication.admitted_source_identity
            or partition.source_identity != expected_source_identity
            or partition.origin_proof_sha256 != publication.origin_proof_sha256
            or partition.origin_proof_sha256 != expected_origin_proof_sha256
        ):
            raise ValueError("validation source partition scope or origin is invalid")
        group = (partition.symbol, partition.interval_index)
        group_partitions.setdefault(group, []).append(partition)
        expected_part = group_part_index.get(group, 0)
        expected_path = (
            Path("partitions")
            / f"symbol={partition.symbol}"
            / f"interval={partition.interval_index:04d}"
            / f"part={expected_part:06d}.jsonl"
        ).as_posix()
        if partition.path != expected_path:
            raise ValueError("validation source partition path order is invalid")
        group_part_index[group] = expected_part + 1
        lines = _iter_verified_minute_partition_lines(
            publication.publication_root / partition.path,
            partition,
        )
        row_count = 0
        first_timestamp: datetime | None = None
        last_timestamp: datetime | None = None
        for raw_line in lines:
            line = raw_line + b"\n"
            try:
                decoded_row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("canonical minute row is not JSON") from error
            envelope = _exact_mapping(
                decoded_row,
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
                "canonical minute row envelope",
            )
            if (
                envelope["source_identity"] != partition.source_identity
                or envelope["origin_proof_sha256"] != partition.origin_proof_sha256
                or partition.source_identity != publication.admitted_source_identity
                or envelope["source_identity"] != expected_source_identity
                or partition.origin_proof_sha256 != publication.origin_proof_sha256
                or envelope["origin_proof_sha256"] != expected_origin_proof_sha256
            ):
                raise ValueError("canonical minute row origin proof differs")
            row = CanonicalMinuteRowV2.from_dict(
                {
                    key: value
                    for key, value in envelope.items()
                    if key not in {"source_identity", "origin_proof_sha256"}
                }
            )
            if (
                _canonical_minute_row_bytes(
                    row,
                    source_identity=partition.source_identity,
                    origin_proof_sha256=partition.origin_proof_sha256,
                )
                != line
            ):
                raise ValueError("canonical minute row bytes are not deterministic")
            if (
                row.symbol != partition.symbol
                or row.timeframe != "1m"
                or partition.interval_index >= len(publication.allowed_intervals)
            ):
                raise ValueError("canonical minute row partition scope is invalid")
            start_text, end_text = publication.allowed_intervals[partition.interval_index]
            start = _parse_utc_text(start_text, "allowed interval start")
            end = _parse_utc_text(end_text, "allowed interval end")
            if not start <= row.timestamp < end:
                raise ValueError("canonical minute row exceeds development scope")
            key = (row.symbol, partition.interval_index, row.timestamp)
            if prior_key is not None and key <= prior_key:
                raise ValueError("canonical minute publication keys are not ordered")
            prior_key = key
            row_count += 1
            if first_timestamp is None:
                first_timestamp = row.timestamp
            last_timestamp = row.timestamp
        if (
            first_timestamp is None
            or last_timestamp is None
            or _utc_text(first_timestamp) != partition.min_timestamp
            or _utc_text(last_timestamp) != partition.max_timestamp
        ):
            raise ValueError("validation source partition timestamp bounds changed")
        total_rows += row_count
        total_bytes += partition.byte_count
    if total_rows != publication.row_count or total_bytes != publication.byte_count:
        raise ValueError("validation source aggregate partition counts changed")
    completion_by_group = {(item.symbol, item.interval_index): item for item in audit.completions}
    if set(completion_by_group) != set(group_partitions):
        raise ValueError("validation source audit and partition request coverage differ")
    for group, members in group_partitions.items():
        completion = completion_by_group[group]
        if (
            completion.row_count != sum(item.row_count for item in members)
            or completion.byte_count != sum(item.byte_count for item in members)
            or completion.partition_set_sha256 != _request_partition_set_sha256(members)
        ):
            raise ValueError("validation source audit partition aggregates differ")


def _register_source_publication(
    publication: ValidationSourcePublicationV2,
    *,
    audit_bytes: bytes,
    parent_bytes: tuple[bytes, bytes, bytes, bytes],
) -> None:
    identifier = id(publication)

    def cleanup(reference: weakref.ReferenceType[ValidationSourcePublicationV2]) -> None:
        current = _VERIFIED_SOURCE_PUBLICATIONS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_SOURCE_PUBLICATIONS.pop(identifier, None)

    reference = weakref.ref(publication, cleanup)
    _VERIFIED_SOURCE_PUBLICATIONS[identifier] = (
        reference,
        publication.canonical_bytes,
        publication.publication_root,
        publication.audit_ledger_root,
        audit_bytes,
        parent_bytes,
    )


def _validate_minute_publication_limits(
    *,
    max_rows_per_partition: int,
    max_total_rows: int,
    max_total_bytes: int,
    max_partitions: int,
) -> None:
    values = {
        "max_rows_per_partition": max_rows_per_partition,
        "max_total_rows": max_total_rows,
        "max_total_bytes": max_total_bytes,
        "max_partitions": max_partitions,
    }
    ceilings = {
        "max_rows_per_partition": 100_000,
        "max_total_rows": 50_000_000,
        "max_total_bytes": 64 * 1024 * 1024 * 1024,
        "max_partitions": _MAX_MINUTE_PUBLICATION_ENTRIES - 2,
    }
    for label, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or value > ceilings[label]
        ):
            raise ValueError(f"{label} is outside its fixed positive ceiling")


def _canonical_minute_row_bytes(
    row: CanonicalMinuteRowV2,
    *,
    source_identity: str,
    origin_proof_sha256: str,
) -> bytes:
    return (
        json.dumps(
            {
                **row.to_dict(),
                "source_identity": source_identity,
                "origin_proof_sha256": origin_proof_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _request_partition_set_sha256(
    partitions: Any,
) -> str:
    return hash_json(
        "phase5-validation-minute-request-partitions-v2",
        [item.to_dict() for item in partitions],
    )


def _nonnegative_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _parse_utc_text(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be canonical UTC") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.second
        or parsed.microsecond
        or _utc_text(parsed) != value
    ):
        raise ValueError(f"{label} must be minute-aligned canonical UTC")
    return parsed.astimezone(UTC)


def _parse_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a canonical decimal string")
    try:
        parsed = Decimal(value)
    except Exception as error:
        raise ValueError(f"{label} is not a decimal") from error
    if _decimal_text(parsed) != value:
        raise ValueError(f"{label} is not a canonical decimal string")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("canonical decimal must be finite")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _availability_from_dict(payload: object) -> ScopedSourceAvailabilityV2:
    values = _exact_mapping(
        payload,
        {
            "schema_version",
            "boundary_sha256",
            "status",
            "candidates",
            "admitted_source_identity",
            "rows_read",
            "bytes_read",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "audit_publication_sha256",
            "audit_publication_path",
            "availability_sha256",
        },
        "scoped source availability",
    )
    if values["schema_version"] != ScopedSourceAvailabilityV2._schema:
        raise ValueError("scoped source availability schema is invalid")
    raw_candidates = values["candidates"]
    if not isinstance(raw_candidates, list):
        raise TypeError("availability candidates must be a list")
    candidates = tuple(
        ScopedSourceCandidateV2(
            descriptor_path=item["descriptor_path"],
            source_kind=item["source_kind"],
            source_identity=item["source_identity"],
            descriptor_sha256=item["descriptor_sha256"],
            admitted=item["admitted"],
            rejection_reason=item["rejection_reason"],
        )
        for item in (
            _exact_mapping(
                candidate,
                {
                    "descriptor_path",
                    "source_kind",
                    "source_identity",
                    "descriptor_sha256",
                    "admitted",
                    "rejection_reason",
                },
                "source candidate",
            )
            for candidate in raw_candidates
        )
    )
    return ScopedSourceAvailabilityV2(
        status=ScopedSourceStatusV2(values["status"]),
        boundary_sha256=values["boundary_sha256"],  # type: ignore[arg-type]
        candidates=candidates,
        admitted_source_identity=values["admitted_source_identity"],  # type: ignore[arg-type]
        rows_read=values["rows_read"],  # type: ignore[arg-type]
        bytes_read=values["bytes_read"],  # type: ignore[arg-type]
        final_scope_attempts=values["final_scope_attempts"],  # type: ignore[arg-type]
        final_rows=values["final_rows"],  # type: ignore[arg-type]
        final_access_records=values["final_access_records"],  # type: ignore[arg-type]
        audit_publication_sha256=values["audit_publication_sha256"],  # type: ignore[arg-type]
        audit_publication_path=values["audit_publication_path"],  # type: ignore[arg-type]
        availability_sha256=values["availability_sha256"],  # type: ignore[arg-type]
        canonical_bytes=publication_json_bytes(values),
        _factory_token=_AVAILABILITY_FACTORY,
    )


def _evaluate_candidate(
    descriptor_path: Path,
    raw: bytes,
    descriptor_sha256: str,
    boundary: DevelopmentReadBoundaryV2,
    *,
    read_reference: Any,
) -> ScopedSourceCandidateV2:
    path_text = str(descriptor_path)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _rejected_candidate(path_text, "invalid", descriptor_sha256, "invalid_descriptor")
    if not isinstance(decoded, dict):
        return _rejected_candidate(path_text, "invalid", descriptor_sha256, "invalid_descriptor")
    kind = str(decoded.get("source_kind", "invalid"))
    identity = str(decoded.get("source_identity", f"invalid:{descriptor_sha256}"))
    if kind in {"postgres-restored-whole-table", "pg_restore", "generic-whole-table"}:
        reason = "whole_table_source_forbidden"
    else:
        try:
            _verify_trusted_candidate_descriptor(
                decoded,
                raw,
                descriptor_path=descriptor_path,
                boundary=boundary,
                read_reference=read_reference,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            reason = "trusted_original_predicate_evidence_invalid"
        else:
            reason = None
    return ScopedSourceCandidateV2(
        descriptor_path=path_text,
        source_kind=kind,
        source_identity=identity,
        descriptor_sha256=descriptor_sha256,
        admitted=reason is None,
        rejection_reason=reason,
    )


def _verify_trusted_candidate_descriptor(
    descriptor: dict[str, object],
    descriptor_bytes: bytes,
    *,
    descriptor_path: Path,
    boundary: DevelopmentReadBoundaryV2,
    read_reference: Any,
) -> None:
    verifier_sha = _verify_candidate_descriptor_evidence(
        descriptor,
        descriptor_bytes,
        descriptor_path=descriptor_path,
        boundary=boundary,
        read_reference=read_reference,
    )
    if verifier_sha not in _TRUSTED_VERIFIER_EVIDENCE_SHA256:
        raise ValueError("verifier evidence is not rooted in the trusted exact allowlist")


def _verify_candidate_descriptor_evidence(
    descriptor: dict[str, object],
    descriptor_bytes: bytes,
    *,
    descriptor_path: Path,
    boundary: DevelopmentReadBoundaryV2,
    read_reference: Any,
) -> str:
    expected_fields = {
        "schema_version",
        "source_kind",
        "source_identity",
        "original_manifest_path",
        "original_manifest_sha256",
        "verifier_evidence_path",
        "verifier_evidence_sha256",
        "predicate_evidence_path",
        "predicate_evidence_sha256",
    }
    if (
        set(descriptor) != expected_fields
        or descriptor["schema_version"] != "phase5-scoped-source-candidate-v2"
        or descriptor["source_kind"] != "content-addressed-development-publication"
        or publication_json_bytes(descriptor) != descriptor_bytes
    ):
        raise ValueError("candidate descriptor schema or kind is not trusted")
    source_identity = descriptor["source_identity"]
    if not isinstance(source_identity, str) or not source_identity:
        raise ValueError("candidate source identity is invalid")
    originals: dict[str, tuple[dict[str, Any], str, bytes]] = {}
    for label in ("original_manifest", "verifier_evidence", "predicate_evidence"):
        path_value = descriptor[f"{label}_path"]
        expected_sha = descriptor[f"{label}_sha256"]
        path = _resolve_evidence_path(descriptor_path.parent, path_value)
        content = read_reference(path, f"{source_identity}:{label}")
        actual_sha = hashlib.sha256(content).hexdigest()
        if expected_sha != actual_sha:
            raise ValueError(f"{label} digest differs from original bytes")
        decoded = _decode_canonical_object(content, label)
        originals[label] = (decoded, actual_sha, content)
    original, original_sha, original_bytes = originals["original_manifest"]
    verifier, verifier_sha, verifier_bytes = originals["verifier_evidence"]
    predicate, predicate_sha, predicate_bytes = originals["predicate_evidence"]
    if original.get("schema_version") == "phase5-development-scoped-source-manifest-v3":
        verified_source_identity = _verify_candidate_inputs_payload_v3(
            original=original,
            original_bytes=original_bytes,
            predicate=predicate,
            predicate_bytes=predicate_bytes,
            boundary=boundary,
            read_reference=read_reference,
        )
        if verified_source_identity != source_identity:
            raise ValueError("candidate descriptor source identity differs from ancestry")
        _verify_independent_verifier_evidence_v3(
            verifier_bytes,
            source_identity=source_identity,
            original_bytes=original_bytes,
            predicate_bytes=predicate_bytes,
            boundary=boundary,
        )
        return verifier_sha
    scope = _boundary_scope_payload(boundary)
    original_payload = {
        "source_identity": source_identity,
        **scope,
        "read_only": True,
        "predicate_enforcement": "partition-scope-before-open",
    }
    if original != {
        "schema_version": "phase5-development-scoped-source-manifest-v2",
        **original_payload,
        "manifest_sha256": hash_json(
            "phase5-development-scoped-source-manifest-v2", original_payload
        ),
    }:
        raise ValueError("original source manifest does not prove exact immutable scope")
    verifier_payload = {
        "source_identity": source_identity,
        "original_manifest_sha256": original_sha,
        "predicate_evidence_sha256": predicate_sha,
        "boundary_sha256": boundary.boundary_sha256,
        "verifier_kind": "independent-original-byte-verifier",
        "immutable": True,
    }
    if verifier != {
        "schema_version": "phase5-development-source-verifier-evidence-v2",
        **verifier_payload,
        "evidence_sha256": hash_json(
            "phase5-development-source-verifier-evidence-v2", verifier_payload
        ),
    }:
        raise ValueError("independent verifier evidence is invalid")
    predicate_payload = {
        "source_identity": source_identity,
        **scope,
        "predicate_stage": "before-file-open-or-query",
        "client_post_filter": False,
        "unbounded_scan": False,
    }
    if predicate != {
        "schema_version": "phase5-development-source-predicate-evidence-v2",
        **predicate_payload,
        "evidence_sha256": hash_json(
            "phase5-development-source-predicate-evidence-v2", predicate_payload
        ),
    }:
        raise ValueError("predicate-before-read evidence is invalid")
    return verifier_sha


def _boundary_scope_payload(boundary: DevelopmentReadBoundaryV2) -> dict[str, object]:
    return {
        "boundary_sha256": boundary.boundary_sha256,
        "allowed_symbols": list(boundary.allowed_symbols),
        "allowed_timeframes": list(boundary.allowed_timeframes),
        "allowed_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)}
            for item in boundary.allowed_intervals
        ],
    }


def _source_scope_payload_v3(
    boundary: DevelopmentReadBoundaryV2,
    *,
    derived_targets: tuple[str, ...],
) -> dict[str, object]:
    if tuple(boundary.derived_target_timeframes) != derived_targets:
        raise ValueError("candidate derived targets differ from the development boundary")
    if (
        tuple(boundary.source_readable_timeframes) != ("1m",)
        or tuple(boundary.allowed_timeframes) != boundary.source_readable_timeframes
    ):
        raise ValueError("candidate boundary has an invalid one-minute source policy")
    return {
        "boundary_sha256": boundary.boundary_sha256,
        "allowed_symbols": list(boundary.allowed_symbols),
        "source_readable_timeframes": ["1m"],
        "derived_target_timeframes": list(derived_targets),
        "allowed_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)}
            for item in boundary.allowed_intervals
        ],
    }


def _resolve_evidence_path(parent: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("trusted evidence path must be canonical and relative")
    relative = Path(value)
    if ".." in relative.parts or relative.as_posix() != value:
        raise ValueError("trusted evidence path contains traversal")
    return parent / relative


def _decode_canonical_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not JSON") from error
    if not isinstance(decoded, dict) or publication_json_bytes(decoded) != content:
        raise ValueError(f"{label} is not canonical original bytes")
    return decoded


def _rejected_candidate(path: str, kind: str, digest: str, reason: str) -> ScopedSourceCandidateV2:
    return ScopedSourceCandidateV2(
        descriptor_path=path,
        source_kind=kind,
        source_identity=f"rejected:{digest}",
        descriptor_sha256=digest,
        admitted=False,
        rejection_reason=reason,
    )


def _metadata_request(
    boundary: DevelopmentReadBoundaryV2, *, target_identity: str
) -> BoundaryRequestV2:
    interval = boundary.allowed_intervals[0]
    return BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe=boundary.allowed_timeframes[0],
        start=interval.start,
        end=interval.end,
        operation_kind=AccessOperationKindV2.FILE,
        target_identity=target_identity,
    )


def _append_audit_record(
    records_dir: Path,
    *,
    sequence: int,
    phase: str,
    boundary: DevelopmentReadBoundaryV2,
    request: BoundaryRequestV2,
    allowed: bool,
    byte_count: int,
    prior: str | None,
) -> str:
    payload = {
        "sequence": sequence,
        "phase": phase,
        "boundary_sha256": boundary.boundary_sha256,
        "request": {
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "start": _utc_text(request.start),
            "end": _utc_text(request.end),
            "operation_kind": request.operation_kind.value,
            "target_identity": request.target_identity,
        },
        "allowed": allowed,
        "row_count": 0,
        "byte_count": byte_count,
        "prior_record_sha256": prior,
    }
    digest = hash_json(_AUDIT_RECORD_DOMAIN, payload)
    public = {**payload, "record_sha256": digest}
    _write_no_clobber(records_dir / f"{sequence:08d}-{digest}.json", publication_json_bytes(public))
    return digest


def _register_availability(
    availability: ScopedSourceAvailabilityV2,
    publication_path: Path,
    descriptor_bindings: tuple[tuple[Path, str], ...],
    audit_publication_path: Path,
    audit_bytes: bytes,
) -> None:
    identifier = id(availability)

    def cleanup(reference: weakref.ReferenceType[ScopedSourceAvailabilityV2]) -> None:
        current = _VERIFIED_AVAILABILITIES.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_AVAILABILITIES.pop(identifier, None)

    reference = weakref.ref(availability, cleanup)
    _VERIFIED_AVAILABILITIES[identifier] = (
        reference,
        availability.canonical_bytes,
        publication_path,
        descriptor_bindings,
        audit_publication_path,
        audit_bytes,
    )


def _verify_audit_publication(
    publication_path: Path,
    *,
    expected_boundary_sha256: str,
) -> bytes:
    content = read_bounded_regular(publication_path, _MAX_DESCRIPTOR_BYTES)
    public = _decode_canonical_object(content, "source access audit publication")
    values = _exact_mapping(
        public,
        {
            "schema_version",
            "boundary_sha256",
            "record_count",
            "terminal_record_sha256",
            "rows_admitted",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "audit_publication_sha256",
        },
        "source access audit publication",
    )
    if (
        values["schema_version"] != "phase5-validation-source-access-audit-publication-v2"
        or values["boundary_sha256"] != expected_boundary_sha256
        or values["rows_admitted"] != 0
        or values["final_scope_attempts"] != 0
        or values["final_rows"] != 0
        or values["final_access_records"] != 0
    ):
        raise ValueError("source audit scope or zero-access counters are invalid")
    payload = {key: value for key, value in values.items() if key != "audit_publication_sha256"}
    if values["audit_publication_sha256"] != hash_json(_AUDIT_PUBLICATION_DOMAIN, payload):
        raise ValueError("source audit publication digest is invalid")
    records = bounded_regular_files(publication_path.parent / "records", maximum=10_000)
    if values["record_count"] != len(records):
        raise ValueError("source audit record count differs from the chain")
    prior: str | None = None
    for sequence, relative in enumerate(records, start=1):
        record = _decode_canonical_object(
            read_bounded_regular(
                publication_path.parent / "records" / relative,
                _MAX_DESCRIPTOR_BYTES,
            ),
            "source access audit record",
        )
        digest = record.get("record_sha256")
        record_payload = {key: value for key, value in record.items() if key != "record_sha256"}
        if (
            record.get("sequence") != sequence
            or record.get("boundary_sha256") != expected_boundary_sha256
            or record.get("prior_record_sha256") != prior
            or digest != hash_json(_AUDIT_RECORD_DOMAIN, record_payload)
        ):
            raise ValueError("source audit record chain is invalid")
        prior = digest  # type: ignore[assignment]
    if values["terminal_record_sha256"] != prior:
        raise ValueError("source audit terminal digest differs from its chain")
    return content


def _commit_paired_directories(
    *,
    first_stage: Path,
    first_destination: Path,
    second_stage: Path,
    second_destination: Path,
) -> None:
    if _is_windows_platform():
        _commit_paired_directories_windows(
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=second_destination,
        )
        return
    reserved: list[Path] = []
    published_files: list[tuple[Path, Path]] = []
    published_directories: list[Path] = []
    try:
        fsync_directory_posix(first_stage)
        fsync_directory_posix(second_stage)
        _reserve_publication_directory(first_destination)
        reserved.append(first_destination)
        _reserve_publication_directory(second_destination)
        reserved.append(second_destination)
        _populate_reserved_directory(
            first_stage,
            first_destination,
            published_files=published_files,
            published_directories=published_directories,
        )
        _populate_reserved_directory(
            second_stage,
            second_destination,
            published_files=published_files,
            published_directories=published_directories,
        )
        fsync_directory_posix(first_destination)
        fsync_directory_posix(second_destination)
        fsync_directory_posix(first_destination.parent)
        if second_destination.parent != first_destination.parent:
            fsync_directory_posix(second_destination.parent)
    except Exception:
        _rollback_reserved_publications(
            reserved,
            published_files=published_files,
            published_directories=published_directories,
        )
        raise
    finally:
        _remove_staged_tree(first_stage)
        _remove_staged_tree(second_stage)


def _commit_paired_directories_windows(
    *,
    first_stage: Path,
    first_destination: Path,
    second_stage: Path,
    second_destination: Path,
) -> None:
    with (
        _claim_windows_owned_tree(first_stage) as first_claim,
        _claim_windows_owned_tree(second_stage) as second_claim,
    ):
        first_moved = False
        try:
            commit_path = _windows_pair_commit_path(first_destination, second_destination)
            if path_exists_no_follow(commit_path):
                raise FileExistsError(f"refusing existing paired commit: {commit_path}")
            with ExitStack() as incomplete_stack:
                incomplete_claims: list[WindowsOwnedTreeClaim] = []
                for destination, staged_claim in (
                    (first_destination, first_claim),
                    (second_destination, second_claim),
                ):
                    if path_exists_no_follow(destination):
                        incomplete = incomplete_stack.enter_context(
                            _claim_windows_owned_tree(destination)
                        )
                        if incomplete.manifest != staged_claim.manifest:
                            raise FileExistsError(
                                f"refusing foreign incomplete publication: {destination}"
                            )
                        incomplete_claims.append(incomplete)
                    require_regular_directory(destination.parent)
                for incomplete in incomplete_claims:
                    incomplete.delete_exact()
            first_claim.move_to(first_destination)
            first_moved = True
            second_claim.move_to(second_destination)
            _publish_windows_pair_commit(
                commit_path,
                first_destination=first_destination,
                first_manifest=first_claim.manifest,
                second_destination=second_destination,
                second_manifest=second_claim.manifest,
            )
        except Exception as error:
            rollback_error: Exception | None = None
            if first_moved:
                try:
                    first_claim.move_to(first_stage)
                except Exception as failure:
                    rollback_error = failure
            cleanup_errors: list[Exception] = []
            for claim in (first_claim, second_claim):
                try:
                    claim.delete_exact()
                except Exception as failure:
                    cleanup_errors.append(failure)
            if rollback_error is not None or cleanup_errors:
                failures = ([rollback_error] if rollback_error is not None else []) + cleanup_errors
                raise RuntimeError(
                    "Windows paired publication exact-handle cleanup failed: "
                    + "; ".join(str(item) for item in failures)
                ) from error
            raise error


def _windows_pair_commit_path(first: Path, second: Path) -> Path:
    identity = hashlib.sha256(
        f"{first.absolute()}\0{second.absolute()}".encode("utf-8")
    ).hexdigest()[:24]
    return first.parent / f".windows-pair-{identity}.commit.json"


def _publish_windows_pair_commit(
    path: Path,
    *,
    first_destination: Path,
    first_manifest: tuple[tuple[str, str | None], ...],
    second_destination: Path,
    second_manifest: tuple[tuple[str, str | None], ...],
) -> None:
    payload: dict[str, object] = {
        "schema_version": "windows-paired-publication-commit-v1",
        "first_destination": str(first_destination.absolute()),
        "first_manifest": [list(item) for item in first_manifest],
        "second_destination": str(second_destination.absolute()),
        "second_manifest": [list(item) for item in second_manifest],
    }
    payload["commit_sha256"] = hash_json("windows-paired-publication-commit-v1", payload)
    content = publication_json_bytes(payload)
    stage = Path(tempfile.mkdtemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent))
    staged_commit = stage / path.name
    descriptor = os.open(
        staged_commit,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        with _claim_windows_owned_tree(stage) as claim:
            try:
                claim.move_member_to(staged_commit.name, path)
            finally:
                claim.delete_exact()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_windows_pair_commit(first: Path, second: Path) -> None:
    if not _is_windows_platform():
        return
    path = _windows_pair_commit_path(first, second)
    values = _exact_mapping(
        _decode_canonical_object(
            read_bounded_regular(path, _MAX_DESCRIPTOR_BYTES),
            "Windows paired publication commit",
        ),
        {
            "schema_version",
            "first_destination",
            "first_manifest",
            "second_destination",
            "second_manifest",
            "commit_sha256",
        },
        "Windows paired publication commit",
    )
    commit_sha256 = values["commit_sha256"]
    payload = {key: value for key, value in values.items() if key != "commit_sha256"}
    first_manifest = _decode_windows_pair_manifest(values["first_manifest"])
    second_manifest = _decode_windows_pair_manifest(values["second_manifest"])
    if (
        values["schema_version"] != "windows-paired-publication-commit-v1"
        or values["first_destination"] != str(first.absolute())
        or values["second_destination"] != str(second.absolute())
        or commit_sha256 != hash_json("windows-paired-publication-commit-v1", payload)
    ):
        raise ValueError("Windows paired publication commit is invalid")
    with (
        _claim_windows_owned_tree(first) as first_claim,
        _claim_windows_owned_tree(second) as second_claim,
    ):
        if first_claim.manifest != first_manifest or second_claim.manifest != second_manifest:
            raise ValueError("Windows paired publication trees differ from their commit")


def _decode_windows_pair_manifest(value: object) -> tuple[tuple[str, str | None], ...]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise ValueError("Windows paired publication manifest exceeds its bound")
    decoded: list[tuple[str, str | None]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or (item[1] is not None and not isinstance(item[1], str))
        ):
            raise ValueError("Windows paired publication manifest entry is invalid")
        decoded.append((item[0], item[1]))
    result = tuple(decoded)
    paths = tuple(path for path, _ in result)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise ValueError("Windows paired publication manifest paths are not canonical")
    for path, digest in result:
        relative = Path(path)
        if (
            not path
            or relative.is_absolute()
            or relative.as_posix() != path
            or ".." in relative.parts
            or digest is not None
            and _SHA256.fullmatch(digest) is None
        ):
            raise ValueError("Windows paired publication manifest entry is invalid")
    return result


def _require_windows_pair_commit_for_first(first: Path) -> None:
    if not _is_windows_platform():
        return
    candidates = _bounded_windows_pair_commit_candidates(first.parent)
    matches: list[Path] = []
    for candidate in candidates:
        values = _exact_mapping(
            _decode_canonical_object(
                read_bounded_regular(candidate, _MAX_DESCRIPTOR_BYTES),
                "Windows paired publication commit",
            ),
            {
                "schema_version",
                "first_destination",
                "first_manifest",
                "second_destination",
                "second_manifest",
                "commit_sha256",
            },
            "Windows paired publication commit",
        )
        if values.get("first_destination") == str(first.absolute()):
            second = values.get("second_destination")
            if not isinstance(second, str):
                raise ValueError("Windows paired publication commit destination is invalid")
            _require_windows_pair_commit(first, Path(second))
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError("Windows paired publication commit is missing or ambiguous")


def _bounded_windows_pair_commit_candidates(parent: Path) -> tuple[Path, ...]:
    require_regular_directory(parent)
    candidates: list[Path] = []
    entry_count = 0
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_WINDOWS_PAIR_PARENT_ENTRIES:
                    raise RuntimeError("Windows paired publication parent scan exceeds its bound")
                if not (
                    entry.name.startswith(".windows-pair-") and entry.name.endswith(".commit.json")
                ):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                file_attributes = int(getattr(metadata, "st_file_attributes", 0))
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or file_attributes & 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise RuntimeError("Windows paired publication commit must be a regular file")
                candidates.append(Path(entry.path))
                if len(candidates) > _MAX_WINDOWS_PAIR_COMMIT_CANDIDATES:
                    raise RuntimeError("Windows paired publication commit scan exceeds its bound")
    except OSError as error:
        raise RuntimeError("Windows paired publication parent scan failed") from error
    return tuple(sorted(candidates, key=lambda item: item.name))


def _claim_windows_owned_tree(path: Path) -> WindowsOwnedTreeClaim:
    return WindowsHandleFilesystem().claim_owned_tree(path)


def _reserve_publication_directory(destination: Path) -> None:
    """Atomically reserve a publication root without replacing any path."""

    destination.mkdir(mode=0o700)


def _populate_reserved_directory(
    stage: Path,
    destination: Path,
    *,
    published_files: list[tuple[Path, Path]],
    published_directories: list[Path],
) -> None:
    directories = sorted(
        (path for path in stage.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
    )
    for source_directory in directories:
        target_directory = destination / source_directory.relative_to(stage)
        target_directory.mkdir()
        published_directories.append(target_directory)
    for relative in bounded_regular_files(stage, maximum=100_000):
        source = stage / relative
        target = destination / relative
        os.link(source, target, follow_symlinks=False)
        published_files.append((source, target))


def _rollback_reserved_publications(
    reserved: list[Path],
    *,
    published_files: list[tuple[Path, Path]],
    published_directories: list[Path],
) -> None:
    for source, target in reversed(published_files):
        try:
            source_stat = source.stat(follow_symlinks=False)
            target_stat = target.stat(follow_symlinks=False)
            if (
                source_stat.st_dev == target_stat.st_dev
                and source_stat.st_ino == target_stat.st_ino
            ):
                target.unlink()
        except FileNotFoundError:
            continue
    for directory in reversed(published_directories):
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            continue
    for destination in reversed(reserved):
        try:
            destination.rmdir()
        except (FileNotFoundError, OSError):
            continue


def _remove_staged_tree(root: Path) -> None:
    if not root.exists():
        return
    for relative in reversed(bounded_regular_files(root, maximum=20_000)):
        (root / relative).unlink()
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.rmdir()
    root.rmdir()


def _verify_boundary_original(boundary: DevelopmentReadBoundaryV2) -> None:
    if not isinstance(boundary, DevelopmentReadBoundaryV2):
        raise TypeError("boundary must be DevelopmentReadBoundaryV2")
    # authorize is deliberately invoked with an allowed metadata request: Lane I
    # revalidates the factory registration and every original parent byte.
    boundary.authorize(_metadata_request(boundary, target_identity="boundary-self-check"))


def _read_json(path: Path) -> dict[str, Any]:
    raw = read_bounded_regular(Path(path), _MAX_DESCRIPTOR_BYTES)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"publication is not canonical JSON: {path}") from error
    if not isinstance(decoded, dict) or publication_json_bytes(decoded) != raw:
        raise ValueError(f"publication original bytes are not canonical: {path}")
    return decoded


def _write_no_clobber(path: Path, content: bytes) -> None:
    if path_exists_no_follow(path):
        raise FileExistsError(f"refusing stale or concurrent publication: {path}")
    require_regular_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        durable_move_no_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_directory_no_clobber(root: Path, files: dict[str, bytes]) -> None:
    if path_exists_no_follow(root):
        raise FileExistsError(f"refusing stale or concurrent publication: {root}")
    require_regular_directory(root.parent)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", suffix=".tmp", dir=root.parent))
    try:
        for relative, content in files.items():
            _write_no_clobber(temporary / relative, content)
        if not _is_windows_platform():
            fsync_directory_posix(temporary)
        durable_move_no_replace(temporary, root)
    finally:
        if temporary.exists():
            for relative in files:
                (temporary / relative).unlink(missing_ok=True)
            temporary.rmdir()


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _exact_mapping(payload: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")
    return payload


__all__ = [
    "CanonicalMinuteRowV2",
    "DumpTocMetadataV2",
    "MinutePathReadBudgetV2",
    "ScopedSourceAvailabilityV2",
    "ScopedSourceCandidateV2",
    "ScopedSourceStatusV2",
    "ValidationSourcePartitionV2",
    "ValidationSourcePublicationV2",
    "VerifiedMinutePathV2",
    "VerifiedMinuteRowV2",
    "discover_scoped_source_v2",
    "load_scoped_source_availability_v2",
    "load_validation_source_publication_v2",
    "load_v2_boundary_publications",
    "publish_validation_source_v2",
    "read_verified_minute_path_v2",
    "reject_pg_restore_row_source_v2",
    "verify_dump_toc_metadata_v2",
    "verify_scoped_source_availability_v2",
    "verify_original_minute_path_v2",
    "verify_validation_source_publication_metadata_v2",
    "verify_validation_source_publication_v2",
    "verify_verified_minute_path_v2",
    "verified_scoped_source_availability_bytes_v2",
    "verified_scoped_source_availability_binding_v2",
]
