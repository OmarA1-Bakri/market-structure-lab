"""Outcome-blind split freezing and immutable development read authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from math import ceil
from typing import Any, NamedTuple, Self, TypeVar
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import (
    DevelopmentSplitIdentityV2,
    SourceCoverageIdentityV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
    verified_source_coverage_bytes,
)

_SPLIT_FACTORY = object()
_BOUNDARY_FACTORY = object()
_SPLIT_HOLDOUT_DOMAIN = "phase5-validation-asset-holdout-order-v2"
_SPLIT_IDENTITY_DOMAIN = "phase5-validation-development-split-v2"
_BOUNDARY_IDENTITY_DOMAIN = "phase5-validation-development-read-boundary-v2"
_T = TypeVar("_T")
_VERIFIED_SPLIT_OBJECTS: dict[
    int, tuple[weakref.ReferenceType[DevelopmentSplitPublicationV2], bytes, bytes]
] = {}
_VERIFIED_BOUNDARY_OBJECTS: dict[
    int,
    tuple[
        weakref.ReferenceType[DevelopmentReadBoundaryV2],
        bytes,
        bytes,
        bytes,
    ],
] = {}


def _utc_text(value: datetime) -> str:
    _require_minute_aligned(value, "identity timestamp")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp") from error
    _require_utc(parsed, label)
    return parsed


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value


def _require_minute_aligned(value: datetime, label: str) -> None:
    _require_utc(value, label)
    if value.second or value.microsecond:
        raise ValueError(f"{label} must be minute-aligned")


class UtcIntervalV2(NamedTuple):
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class SplitPolicyV2:
    """Public, fixed-domain split policy independent of programme outcomes."""

    block_count: int
    minimum_complete_days: int
    asset_holdout_fraction_numerator: int
    asset_holdout_fraction_denominator: int
    asset_holdout_salt: str
    purge_hours: int
    embargo_hours: int
    timeframes: tuple[str, ...]

    def __post_init__(self) -> None:
        for label in (
            "block_count",
            "minimum_complete_days",
            "asset_holdout_fraction_numerator",
            "asset_holdout_fraction_denominator",
            "purge_hours",
            "embargo_hours",
        ):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.block_count != 6:
            raise ValueError("V2 split policy requires exactly six blocks")
        if self.minimum_complete_days < 1:
            raise ValueError("minimum_complete_days must be positive")
        if (
            not 0
            < self.asset_holdout_fraction_numerator
            < (self.asset_holdout_fraction_denominator)
        ):
            raise ValueError("asset holdout fraction must be strictly between zero and one")
        if (
            not isinstance(self.asset_holdout_salt, str)
            or not self.asset_holdout_salt
            or self.asset_holdout_salt.strip() != self.asset_holdout_salt
        ):
            raise ValueError("asset_holdout_salt must be canonical")
        if (
            not isinstance(self.timeframes, tuple)
            or not self.timeframes
            or len(self.timeframes) != len(set(self.timeframes))
        ):
            raise ValueError("timeframes must be a unique tuple")

    @property
    def policy_sha256(self) -> str:
        return hash_json("phase5-validation-split-policy-v2", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "block_count": self.block_count,
            "minimum_complete_days": self.minimum_complete_days,
            "asset_holdout_fraction_numerator": self.asset_holdout_fraction_numerator,
            "asset_holdout_fraction_denominator": self.asset_holdout_fraction_denominator,
            "asset_holdout_salt": self.asset_holdout_salt,
            "purge_hours": self.purge_hours,
            "embargo_hours": self.embargo_hours,
            "timeframes": list(self.timeframes),
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "block_count",
                "minimum_complete_days",
                "asset_holdout_fraction_numerator",
                "asset_holdout_fraction_denominator",
                "asset_holdout_salt",
                "purge_hours",
                "embargo_hours",
                "timeframes",
            },
            "split policy",
        )
        timeframes = values["timeframes"]
        if not isinstance(timeframes, list) or any(
            not isinstance(item, str) for item in timeframes
        ):
            raise TypeError("split policy timeframes must be a string list")
        return cls(
            block_count=values["block_count"],  # type: ignore[arg-type]
            minimum_complete_days=values["minimum_complete_days"],  # type: ignore[arg-type]
            asset_holdout_fraction_numerator=values[  # type: ignore[arg-type]
                "asset_holdout_fraction_numerator"
            ],
            asset_holdout_fraction_denominator=values[  # type: ignore[arg-type]
                "asset_holdout_fraction_denominator"
            ],
            asset_holdout_salt=values["asset_holdout_salt"],  # type: ignore[arg-type]
            purge_hours=values["purge_hours"],  # type: ignore[arg-type]
            embargo_hours=values["embargo_hours"],  # type: ignore[arg-type]
            timeframes=tuple(timeframes),
        )


@dataclass(frozen=True, slots=True)
class GridBlockV2:
    index: int
    start: datetime
    end: datetime
    day_count: int
    block_sha256: str

    def __post_init__(self) -> None:
        _require_utc(self.start, "block start")
        _require_utc(self.end, "block end")
        if self.end <= self.start:
            raise ValueError("grid block must be positive")
        if self.day_count != (self.end - self.start).days or self.day_count < 1:
            raise ValueError("grid block day_count does not match its interval")

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "start": _utc_text(self.start),
            "end": _utc_text(self.end),
            "day_count": self.day_count,
            "block_sha256": self.block_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExcludedCoverageSymbolV2:
    symbol: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "reason": self.reason}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class DevelopmentSplitPublicationV2:
    """Metadata-only development/final split, created before programme identity."""

    coverage_identity: SourceCoverageIdentityV2
    policy: SplitPolicyV2
    eligible_symbols: tuple[str, ...]
    excluded_symbols: tuple[ExcludedCoverageSymbolV2, ...]
    development_symbols: tuple[str, ...]
    asset_holdout_symbols: tuple[str, ...]
    blocks: tuple[GridBlockV2, ...]
    split_identity: DevelopmentSplitIdentityV2
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SPLIT_FACTORY:
            raise TypeError("DevelopmentSplitPublicationV2 requires its freeze factory")
        if not isinstance(self.coverage_identity, SourceCoverageIdentityV2):
            raise TypeError("coverage_identity must be SourceCoverageIdentityV2")
        if not isinstance(self.policy, SplitPolicyV2):
            raise TypeError("policy must be SplitPolicyV2")
        if len(self.blocks) != self.policy.block_count:
            raise ValueError("split block count differs from policy")
        if set(self.development_symbols) & set(self.asset_holdout_symbols):
            raise ValueError("development and asset-holdout symbols overlap")
        if set(self.development_symbols) | set(self.asset_holdout_symbols) != set(
            self.eligible_symbols
        ):
            raise ValueError("development and holdout symbols must cover eligible symbols")
        if self.split_identity != DevelopmentSplitIdentityV2.from_payload(self._identity_payload()):
            raise ValueError("split identity does not match metadata")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("split canonical bytes do not match publication")

    @property
    def development_blocks(self) -> tuple[GridBlockV2, ...]:
        return self.blocks[:-1]

    @property
    def temporal_holdout(self) -> GridBlockV2:
        return self.blocks[-1]

    def _identity_payload(self) -> dict[str, object]:
        return {
            "coverage_identity": self.coverage_identity.value,
            "policy": self.policy.to_dict(),
            "eligible_symbols": list(self.eligible_symbols),
            "excluded_symbols": [item.to_dict() for item in self.excluded_symbols],
            "development_symbols": list(self.development_symbols),
            "asset_holdout_symbols": list(self.asset_holdout_symbols),
            "blocks": [item.to_dict() for item in self.blocks],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase5-validation-development-split-v2",
            **self._identity_payload(),
            "split_identity": self.split_identity.value,
        }

    @classmethod
    def from_dict(
        cls, payload: object, coverage: SourceCoveragePublicationV2
    ) -> DevelopmentSplitPublicationV2:
        values = _exact_mapping(
            payload,
            {
                "schema_version",
                "coverage_identity",
                "policy",
                "eligible_symbols",
                "excluded_symbols",
                "development_symbols",
                "asset_holdout_symbols",
                "blocks",
                "split_identity",
            },
            "development split publication",
        )
        if values["schema_version"] != "phase5-validation-development-split-v2":
            raise ValueError("development split schema_version is invalid")
        policy = SplitPolicyV2.from_dict(values["policy"])
        expected = freeze_development_split_v2(coverage=coverage, policy=policy)
        if values != expected.to_dict():
            raise ValueError("development split publication differs from frozen metadata")
        return expected


def freeze_development_split_v2(
    *,
    coverage: SourceCoveragePublicationV2,
    policy: SplitPolicyV2,
    candle_rows: object | None = None,
    outcomes: object | None = None,
) -> DevelopmentSplitPublicationV2:
    """Derive a split solely from coverage metadata; row arguments remain untouched."""

    del candle_rows, outcomes
    if not isinstance(coverage, SourceCoveragePublicationV2):
        raise TypeError("coverage must be SourceCoveragePublicationV2")
    if not isinstance(policy, SplitPolicyV2):
        raise TypeError("policy must be SplitPolicyV2")
    eligible = []
    excluded = []
    for entry in coverage.entries:
        if entry.source_conflict:
            excluded.append(ExcludedCoverageSymbolV2(entry.symbol, "source_conflict"))
        elif not entry.mapping_compatible:
            excluded.append(ExcludedCoverageSymbolV2(entry.symbol, "mapping_incompatible"))
        elif not set(policy.timeframes).issubset(entry.timeframes):
            excluded.append(ExcludedCoverageSymbolV2(entry.symbol, "timeframe_incomplete"))
        else:
            eligible.append(entry)
    if len(eligible) < 2:
        raise ValueError("split requires at least two eligible symbols")
    common_start = max(item.complete_start for item in eligible)
    common_end = min(item.complete_end for item in eligible)
    floored_start = datetime(common_start.year, common_start.month, common_start.day, tzinfo=UTC)
    if common_start != floored_start:
        common_start = floored_start + timedelta(days=1)
    else:
        common_start = floored_start
    common_end = datetime(common_end.year, common_end.month, common_end.day, tzinfo=UTC)
    total_days = (common_end - common_start).days
    if total_days < policy.minimum_complete_days:
        raise ValueError("eligible common coverage is shorter than the minimum")
    block_days = total_days // policy.block_count
    if block_days < 1:
        raise ValueError("common coverage cannot form six positive blocks")
    grid_end = common_start + timedelta(days=block_days * policy.block_count)
    blocks = tuple(
        _grid_block(
            index=index,
            start=common_start + timedelta(days=index * block_days),
            end=(
                grid_end
                if index == policy.block_count - 1
                else common_start + timedelta(days=(index + 1) * block_days)
            ),
        )
        for index in range(policy.block_count)
    )
    eligible_symbols = tuple(sorted(item.symbol for item in eligible))
    ranked = tuple(
        sorted(
            eligible_symbols,
            key=lambda symbol: (
                hash_json(
                    _SPLIT_HOLDOUT_DOMAIN,
                    {
                        "public_salt": policy.asset_holdout_salt,
                        "coverage_identity": coverage.coverage_identity.value,
                        "symbol": symbol,
                    },
                ),
                symbol,
            ),
        )
    )
    holdout_count = max(
        1,
        ceil(
            len(eligible_symbols)
            * policy.asset_holdout_fraction_numerator
            / policy.asset_holdout_fraction_denominator
        ),
    )
    if holdout_count >= len(eligible_symbols):
        raise ValueError("asset holdout would consume all eligible symbols")
    asset_holdout = tuple(sorted(ranked[:holdout_count]))
    development = tuple(symbol for symbol in eligible_symbols if symbol not in set(asset_holdout))
    identity_payload = {
        "coverage_identity": coverage.coverage_identity.value,
        "policy": policy.to_dict(),
        "eligible_symbols": list(eligible_symbols),
        "excluded_symbols": [item.to_dict() for item in excluded],
        "development_symbols": list(development),
        "asset_holdout_symbols": list(asset_holdout),
        "blocks": [item.to_dict() for item in blocks],
    }
    identity = DevelopmentSplitIdentityV2.from_payload(identity_payload)
    public = {
        "schema_version": "phase5-validation-development-split-v2",
        **identity_payload,
        "split_identity": identity.value,
    }
    publication = DevelopmentSplitPublicationV2(
        coverage_identity=coverage.coverage_identity,
        policy=policy,
        eligible_symbols=eligible_symbols,
        excluded_symbols=tuple(excluded),
        development_symbols=development,
        asset_holdout_symbols=asset_holdout,
        blocks=blocks,
        split_identity=identity,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_SPLIT_FACTORY,
    )
    coverage_bytes = verified_source_coverage_bytes(coverage)
    if any(
        not (entry.complete_start <= block.start < block.end <= entry.complete_end)
        for entry in eligible
        for block in publication.blocks
    ):
        raise ValueError("split block extends outside eligible verified coverage")
    _register_verified_split(publication, coverage_bytes)
    return publication


def _grid_block(*, index: int, start: datetime, end: datetime) -> GridBlockV2:
    payload = {
        "index": index,
        "start": _utc_text(start),
        "end": _utc_text(end),
        "day_count": (end - start).days,
    }
    return GridBlockV2(
        index=index,
        start=start,
        end=end,
        day_count=(end - start).days,
        block_sha256=hash_json("phase5-validation-grid-block-v2", payload),
    )


class AccessOperationKindV2(str, Enum):
    FILE = "file"
    PROCESS = "process"
    NETWORK = "network"
    QUERY = "query"
    ITERATOR = "iterator"


@dataclass(frozen=True, slots=True)
class BoundaryRequestV2:
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    operation_kind: AccessOperationKindV2
    target_identity: str

    def __post_init__(self) -> None:
        _require_utc(self.start, "request start")
        _require_utc(self.end, "request end")
        _require_minute_aligned(self.start, "request start")
        _require_minute_aligned(self.end, "request end")
        if self.end <= self.start:
            raise ValueError("request interval must be positive")
        if not isinstance(self.operation_kind, AccessOperationKindV2):
            raise TypeError("operation_kind must be AccessOperationKindV2")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class DevelopmentReadBoundaryV2:
    """Factory-issued immutable authority for exact development-only reads."""

    coverage_identity: SourceCoverageIdentityV2
    split_identity: DevelopmentSplitIdentityV2
    allowed_symbols: tuple[str, ...]
    allowed_timeframes: tuple[str, ...]
    allowed_intervals: tuple[UtcIntervalV2, ...]
    forbidden_asset_symbols: tuple[str, ...]
    forbidden_temporal_intervals: tuple[UtcIntervalV2, ...]
    boundary_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _BOUNDARY_FACTORY:
            raise TypeError("DevelopmentReadBoundaryV2 requires its factory")
        if set(self.allowed_symbols) & set(self.forbidden_asset_symbols):
            raise ValueError("boundary allowed and forbidden symbols overlap")
        if self.boundary_sha256 != hash_json(_BOUNDARY_IDENTITY_DOMAIN, self._identity_payload()):
            raise ValueError("boundary identity does not match its scope")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("boundary canonical bytes do not match publication")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "coverage_identity": self.coverage_identity.value,
            "split_identity": self.split_identity.value,
            "allowed_symbols": list(self.allowed_symbols),
            "allowed_timeframes": list(self.allowed_timeframes),
            "allowed_intervals": [
                {"start": _utc_text(item.start), "end": _utc_text(item.end)}
                for item in self.allowed_intervals
            ],
            "forbidden_asset_symbols": list(self.forbidden_asset_symbols),
            "forbidden_temporal_intervals": [
                {"start": _utc_text(item.start), "end": _utc_text(item.end)}
                for item in self.forbidden_temporal_intervals
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase5-validation-development-read-boundary-v2",
            **self._identity_payload(),
            "boundary_sha256": self.boundary_sha256,
        }

    def constructor_fields(self) -> dict[str, object]:
        """Return public fields for adversarial direct-construction tests."""

        return {
            "coverage_identity": self.coverage_identity,
            "split_identity": self.split_identity,
            "allowed_symbols": self.allowed_symbols,
            "allowed_timeframes": self.allowed_timeframes,
            "allowed_intervals": self.allowed_intervals,
            "forbidden_asset_symbols": self.forbidden_asset_symbols,
            "forbidden_temporal_intervals": self.forbidden_temporal_intervals,
            "boundary_sha256": self.boundary_sha256,
            "canonical_bytes": self.canonical_bytes,
        }

    def authorize(self, request: BoundaryRequestV2) -> None:
        _verify_registered_boundary(self)
        if not isinstance(request, BoundaryRequestV2):
            raise TypeError("boundary request must be BoundaryRequestV2")
        allowed = (
            request.symbol in self.allowed_symbols
            and request.timeframe in self.allowed_timeframes
            and any(
                request.start >= interval.start and request.end <= interval.end
                for interval in self.allowed_intervals
            )
        )
        if not allowed:
            raise PermissionError("request exceeds the immutable development read boundary")

    @classmethod
    def from_publication_dict(
        cls,
        payload: object,
        coverage: SourceCoveragePublicationV2,
        split: DevelopmentSplitPublicationV2,
    ) -> DevelopmentReadBoundaryV2:
        expected = issue_development_read_boundary_v2(coverage, split)
        if not isinstance(payload, dict) or payload != expected.to_dict():
            raise ValueError("boundary publication differs from verified split")
        return expected


def issue_development_read_boundary_v2(
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
) -> DevelopmentReadBoundaryV2:
    """Issue the exact development capability from verified coverage and split."""

    if split.coverage_identity != coverage.coverage_identity:
        raise ValueError("split coverage identity differs from coverage publication")
    coverage_bytes = verified_source_coverage_bytes(coverage)
    split_bytes = _verified_split_bytes(split, coverage_bytes)
    reopened_coverage = SourceCoveragePublicationV2.from_dict(
        _decode_publication(coverage_bytes, "coverage")
    )
    reopened_split = DevelopmentSplitPublicationV2.from_dict(
        _decode_publication(split_bytes, "split"), reopened_coverage
    )
    if reopened_split.to_dict() != split.to_dict():
        raise ValueError("split differs from recomputed original publication bytes")
    intervals = tuple(UtcIntervalV2(block.start, block.end) for block in split.development_blocks)
    forbidden = (UtcIntervalV2(split.temporal_holdout.start, split.temporal_holdout.end),)
    payload = {
        "coverage_identity": coverage.coverage_identity.value,
        "split_identity": split.split_identity.value,
        "allowed_symbols": list(split.development_symbols),
        "allowed_timeframes": list(split.policy.timeframes),
        "allowed_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)} for item in intervals
        ],
        "forbidden_asset_symbols": list(split.asset_holdout_symbols),
        "forbidden_temporal_intervals": [
            {"start": _utc_text(item.start), "end": _utc_text(item.end)} for item in forbidden
        ],
    }
    digest = hash_json(_BOUNDARY_IDENTITY_DOMAIN, payload)
    public = {
        "schema_version": "phase5-validation-development-read-boundary-v2",
        **payload,
        "boundary_sha256": digest,
    }
    boundary = DevelopmentReadBoundaryV2(
        coverage_identity=coverage.coverage_identity,
        split_identity=split.split_identity,
        allowed_symbols=split.development_symbols,
        allowed_timeframes=split.policy.timeframes,
        allowed_intervals=intervals,
        forbidden_asset_symbols=split.asset_holdout_symbols,
        forbidden_temporal_intervals=forbidden,
        boundary_sha256=digest,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_BOUNDARY_FACTORY,
    )
    _register_verified_boundary(boundary, coverage_bytes, split_bytes)
    return boundary


def verify_development_read_boundary_v2(
    boundary: DevelopmentReadBoundaryV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
) -> DevelopmentReadBoundaryV2:
    if not isinstance(boundary, DevelopmentReadBoundaryV2):
        raise TypeError("boundary must be DevelopmentReadBoundaryV2")
    _verify_registered_boundary(boundary)
    if boundary.coverage_identity != coverage.coverage_identity:
        raise ValueError("boundary coverage identity is stale or wrong")
    if boundary.split_identity != split.split_identity:
        raise ValueError("boundary split identity is stale or wrong")
    expected = issue_development_read_boundary_v2(coverage, split)
    if boundary != expected:
        raise ValueError("boundary scope differs from the verified split")
    return boundary


def open_development_consumer_v2(
    boundary: DevelopmentReadBoundaryV2,
    request: BoundaryRequestV2,
    consumer_factory: Callable[[], _T],
) -> _T:
    """Reject outside-scope requests before constructing any I/O consumer."""

    boundary.authorize(request)
    return consumer_factory()


@dataclass(frozen=True, slots=True)
class AccessAuditCountersV2:
    source_file_attempts: int = 0
    source_process_attempts: int = 0
    source_network_attempts: int = 0
    source_query_attempts: int = 0
    source_iterator_attempts: int = 0
    rows_admitted: int = 0
    bytes_admitted: int = 0
    denied_boundary_attempts: int = 0
    final_scope_attempts: int = 0
    final_rows: int = 0
    final_access_records: int = 0


@dataclass(frozen=True, slots=True)
class AccessAuditRecordV2:
    sequence: int
    phase: str
    programme_id: str
    attempt_id: str
    boundary_sha256: str
    request: BoundaryRequestV2
    allowed: bool
    row_count: int
    byte_count: int
    prior_record_sha256: str | None
    record_sha256: str


class DevelopmentAccessAttemptLedgerV2:
    """Append-only attempt observations kept separate from boundary authority."""

    def __init__(
        self,
        *,
        programme_id: str,
        attempt_id: str,
        boundary: DevelopmentReadBoundaryV2,
    ) -> None:
        if not programme_id.startswith("VPV2-") or len(programme_id) != 69:
            raise ValueError("programme_id must be a VPV2 identity")
        if not attempt_id.startswith("VA-") or len(attempt_id) != 67:
            raise ValueError("attempt_id must be a VA identity")
        _verify_registered_boundary(boundary)
        self._programme_id = programme_id
        self._attempt_id = attempt_id
        self._boundary = boundary
        self._records: list[AccessAuditRecordV2] = []

    @property
    def records(self) -> tuple[AccessAuditRecordV2, ...]:
        return self._verified_records()

    @property
    def counters(self) -> AccessAuditCountersV2:
        records = self._verified_records()
        attempts = {
            kind: sum(
                record.phase == "start" and record.request.operation_kind is kind
                for record in records
            )
            for kind in AccessOperationKindV2
        }
        return AccessAuditCountersV2(
            source_file_attempts=attempts[AccessOperationKindV2.FILE],
            source_process_attempts=attempts[AccessOperationKindV2.PROCESS],
            source_network_attempts=attempts[AccessOperationKindV2.NETWORK],
            source_query_attempts=attempts[AccessOperationKindV2.QUERY],
            source_iterator_attempts=attempts[AccessOperationKindV2.ITERATOR],
            rows_admitted=sum(record.row_count for record in records),
            bytes_admitted=sum(record.byte_count for record in records),
            denied_boundary_attempts=sum(
                record.phase == "adjudication" and not record.allowed for record in records
            ),
            final_scope_attempts=sum(
                record.phase == "start" and _is_final_scope(self._boundary, record.request)
                for record in records
            ),
            final_rows=0,
            final_access_records=0,
        )

    def start(self, request: BoundaryRequestV2) -> AccessAuditRecordV2:
        if self._phase_for(request) is not None:
            raise ValueError("ledger operation ordering requires one start")
        return self._append(request, "start", False, 0, 0)

    def adjudicate(self, request: BoundaryRequestV2) -> AccessAuditRecordV2:
        if self._phase_for(request) != "start":
            raise ValueError("ledger operation ordering requires start before adjudication")
        try:
            self._boundary.authorize(request)
        except PermissionError:
            self._append(request, "adjudication", False, 0, 0)
            raise
        return self._append(request, "adjudication", True, 0, 0)

    def complete(
        self,
        request: BoundaryRequestV2,
        *,
        row_count: int,
        byte_count: int,
    ) -> AccessAuditRecordV2:
        if self._phase_for(request) != "adjudication_allowed":
            raise ValueError(
                "ledger operation ordering requires allowed adjudication before completion"
            )
        self._boundary.authorize(request)
        if min(row_count, byte_count) < 0:
            raise ValueError("completion counts must be non-negative")
        return self._append(request, "completion", True, row_count, byte_count)

    def _phase_for(self, request: BoundaryRequestV2) -> str | None:
        matching = [record for record in self._records if record.request == request]
        if not matching:
            return None
        last = matching[-1]
        if last.phase == "adjudication" and last.allowed:
            return "adjudication_allowed"
        return last.phase

    def _append(
        self,
        request: BoundaryRequestV2,
        phase: str,
        allowed: bool,
        row_count: int,
        byte_count: int,
    ) -> AccessAuditRecordV2:
        sequence = len(self._records) + 1
        payload = {
            "sequence": sequence,
            "phase": phase,
            "programme_id": self._programme_id,
            "attempt_id": self._attempt_id,
            "boundary_sha256": self._boundary.boundary_sha256,
            "request": {
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "start": _utc_text(request.start),
                "end": _utc_text(request.end),
                "operation_kind": request.operation_kind.value,
                "target_identity": request.target_identity,
            },
            "allowed": allowed,
            "row_count": row_count,
            "byte_count": byte_count,
            "prior_record_sha256": (self._records[-1].record_sha256 if self._records else None),
        }
        record = AccessAuditRecordV2(
            sequence=sequence,
            phase=phase,
            programme_id=self._programme_id,
            attempt_id=self._attempt_id,
            boundary_sha256=self._boundary.boundary_sha256,
            request=request,
            allowed=allowed,
            row_count=row_count,
            byte_count=byte_count,
            prior_record_sha256=payload["prior_record_sha256"],  # type: ignore[arg-type]
            record_sha256=hash_json("phase5-validation-access-attempt-v2", payload),
        )
        self._records.append(record)
        return record

    def _verified_records(self) -> tuple[AccessAuditRecordV2, ...]:
        prior: str | None = None
        for sequence, record in enumerate(self._records, start=1):
            payload = {
                "sequence": sequence,
                "phase": record.phase,
                "programme_id": record.programme_id,
                "attempt_id": record.attempt_id,
                "boundary_sha256": record.boundary_sha256,
                "request": {
                    "symbol": record.request.symbol,
                    "timeframe": record.request.timeframe,
                    "start": _utc_text(record.request.start),
                    "end": _utc_text(record.request.end),
                    "operation_kind": record.request.operation_kind.value,
                    "target_identity": record.request.target_identity,
                },
                "allowed": record.allowed,
                "row_count": record.row_count,
                "byte_count": record.byte_count,
                "prior_record_sha256": prior,
            }
            if (
                record.sequence != sequence
                or record.programme_id != self._programme_id
                or record.attempt_id != self._attempt_id
                or record.boundary_sha256 != self._boundary.boundary_sha256
                or record.prior_record_sha256 != prior
                or record.record_sha256 != hash_json("phase5-validation-access-attempt-v2", payload)
            ):
                raise ValueError("access ledger chain verification failed")
            prior = record.record_sha256
        return tuple(self._records)


def _register_verified_split(split: DevelopmentSplitPublicationV2, coverage_bytes: bytes) -> None:
    identifier = id(split)

    def cleanup(reference: weakref.ReferenceType[DevelopmentSplitPublicationV2]) -> None:
        current = _VERIFIED_SPLIT_OBJECTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_SPLIT_OBJECTS.pop(identifier, None)

    reference = weakref.ref(split, cleanup)
    _VERIFIED_SPLIT_OBJECTS[identifier] = (
        reference,
        split.canonical_bytes,
        coverage_bytes,
    )


def _verified_split_bytes(split: DevelopmentSplitPublicationV2, coverage_bytes: bytes) -> bytes:
    registered = _VERIFIED_SPLIT_OBJECTS.get(id(split))
    if (
        registered is None
        or registered[0]() is not split
        or registered[1] != split.canonical_bytes
        or registered[2] != coverage_bytes
    ):
        raise ValueError("split is not an exact verified original publication")
    return registered[1]


def _register_verified_boundary(
    boundary: DevelopmentReadBoundaryV2,
    coverage_bytes: bytes,
    split_bytes: bytes,
) -> None:
    identifier = id(boundary)

    def cleanup(reference: weakref.ReferenceType[DevelopmentReadBoundaryV2]) -> None:
        current = _VERIFIED_BOUNDARY_OBJECTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_BOUNDARY_OBJECTS.pop(identifier, None)

    reference = weakref.ref(boundary, cleanup)
    _VERIFIED_BOUNDARY_OBJECTS[identifier] = (
        reference,
        boundary.canonical_bytes,
        coverage_bytes,
        split_bytes,
    )


def _verify_registered_boundary(boundary: DevelopmentReadBoundaryV2) -> None:
    registered = _VERIFIED_BOUNDARY_OBJECTS.get(id(boundary))
    if (
        registered is None
        or registered[0]() is not boundary
        or registered[1] != boundary.canonical_bytes
    ):
        raise ValueError("boundary is not an exact verified original publication")
    coverage = SourceCoveragePublicationV2.from_dict(_decode_publication(registered[2], "coverage"))
    split = DevelopmentSplitPublicationV2.from_dict(
        _decode_publication(registered[3], "split"), coverage
    )
    expected = issue_development_read_boundary_v2(coverage, split)
    if expected.to_dict() != boundary.to_dict():
        raise ValueError("boundary differs from recomputed original publication bytes")


def _decode_publication(content: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = __import__("json").loads(content)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{label} original publication bytes are invalid") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} original publication must be an object")
    return decoded


def _is_final_scope(boundary: DevelopmentReadBoundaryV2, request: BoundaryRequestV2) -> bool:
    return request.symbol in boundary.forbidden_asset_symbols or any(
        request.start < interval.end and request.end > interval.start
        for interval in boundary.forbidden_temporal_intervals
    )


def _exact_mapping(payload: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be an object")
    if set(payload) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")
    return payload


__all__ = [
    "AccessAuditCountersV2",
    "AccessOperationKindV2",
    "BoundaryRequestV2",
    "DevelopmentAccessAttemptLedgerV2",
    "DevelopmentReadBoundaryV2",
    "DevelopmentSplitPublicationV2",
    "ExcludedCoverageSymbolV2",
    "GridBlockV2",
    "SplitPolicyV2",
    "UtcIntervalV2",
    "freeze_development_split_v2",
    "issue_development_read_boundary_v2",
    "open_development_consumer_v2",
    "verify_development_read_boundary_v2",
]
