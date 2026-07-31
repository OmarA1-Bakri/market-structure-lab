"""Independent effective-time price-precision authority for Phase 5 validation.

The publisher is deliberately offline.  Current ``exchangeInfo`` bytes are useful
limitation evidence, but are never historical authority.  Only a trusted verifier
of the original, dated publisher schedule can produce ``available`` precision.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, ClassVar
import weakref

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationV2,
    verify_validation_aggregate_publication_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceAvailabilityV2,
    ValidationSourcePublicationV2,
)
from market_structure_lab.research.validation_v2_models import (
    PrecisionAuthorityIdentityV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
)

_MAX_CONTROL_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 100_000
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FACTORY = object()
_TRUSTED_HISTORICAL_PRECISION_VERIFIER_SHA256: frozenset[str] = frozenset()
_SOURCE_SCHEMA = "phase5-publisher-historical-price-precision-schedule-v2"
_VERIFIER_SCHEMA = "phase5-publisher-historical-price-precision-verifier-v2"
_SOURCE_ID_DOMAIN = "phase5-publisher-historical-price-precision-schedule-v2"
_VERIFIER_ID_DOMAIN = "phase5-publisher-historical-price-precision-verifier-v2"


class PrecisionAuthorityStatusV2(str, Enum):
    """Exact authority completeness states."""

    AVAILABLE = "available"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class PrecisionSourceKindV2(str, Enum):
    """The only accepted precision evidence classes."""

    PUBLISHER_HISTORICAL_SCHEDULE = "publisher_historical_effective_schedule"
    CURRENT_ONLY_EXCHANGE_INFO = "current_only_exchange_info"
    UNAVAILABLE = "unavailable"


class PrecisionRequirementStatusV2(str, Enum):
    """Pre-signal detector admission result."""

    READY = "ready"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True, slots=True)
class PrecisionAuthorityRequestV2:
    """Development-only retrieval scope, fixed before precision publication."""

    venue: str
    query_source: str
    source_kind: PrecisionSourceKindV2
    requested_symbols: tuple[str, ...]
    requested_intervals: tuple[tuple[str, str], ...]
    retrieval_identity_sha256: str
    checkpoint_identity_sha256: str
    limitation: str | None

    def __post_init__(self) -> None:
        _canonical_string(self.venue, "venue")
        _canonical_string(self.query_source, "query_source")
        if not isinstance(self.source_kind, PrecisionSourceKindV2):
            raise TypeError("source_kind must be PrecisionSourceKindV2")
        if (
            self.requested_symbols != tuple(sorted(set(self.requested_symbols)))
            or not self.requested_symbols
        ):
            raise ValueError("requested symbols must be non-empty and uniquely sorted")
        for symbol in self.requested_symbols:
            if not symbol or symbol != symbol.upper():
                raise ValueError("requested symbols must be canonical uppercase")
        _validate_intervals(self.requested_intervals)
        _require_sha256(self.retrieval_identity_sha256, "retrieval identity")
        _require_sha256(self.checkpoint_identity_sha256, "checkpoint identity")
        if self.source_kind is PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE:
            if self.limitation is not None:
                raise ValueError("historical schedule request cannot predeclare a limitation")
        elif not self.limitation or self.limitation.strip() != self.limitation:
            raise ValueError("non-historical precision evidence requires an explicit limitation")

    @classmethod
    def from_boundary(
        cls,
        *,
        boundary: DevelopmentReadBoundaryV2,
        venue: str,
        query_source: str,
        source_kind: PrecisionSourceKindV2,
        retrieval_identity_sha256: str,
        checkpoint_identity_sha256: str,
        limitation: str | None,
    ) -> PrecisionAuthorityRequestV2:
        return cls(
            venue=venue,
            query_source=query_source,
            source_kind=source_kind,
            requested_symbols=boundary.allowed_symbols,
            requested_intervals=tuple(
                (_utc_text(item.start), _utc_text(item.end)) for item in boundary.allowed_intervals
            ),
            retrieval_identity_sha256=retrieval_identity_sha256,
            checkpoint_identity_sha256=checkpoint_identity_sha256,
            limitation=limitation,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "venue": self.venue,
            "query_source": self.query_source,
            "source_kind": self.source_kind.value,
            "requested_symbols": list(self.requested_symbols),
            "requested_intervals": [
                {"start": start, "end": end} for start, end in self.requested_intervals
            ],
            "retrieval_identity_sha256": self.retrieval_identity_sha256,
            "checkpoint_identity_sha256": self.checkpoint_identity_sha256,
            "limitation": self.limitation,
        }


@dataclass(frozen=True, slots=True)
class EffectivePrecisionV2:
    """An exact price lattice over one half-open effective interval."""

    symbol: str
    effective_start: str
    effective_end: str
    price_step: Decimal
    price_origin: Decimal

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("precision symbol must be canonical uppercase")
        start = _parse_utc(self.effective_start, "effective_start")
        end = _parse_utc(self.effective_end, "effective_end")
        if end <= start:
            raise ValueError("precision effective interval must be positive")
        if (
            not isinstance(self.price_step, Decimal)
            or not self.price_step.is_finite()
            or self.price_step <= 0
        ):
            raise ValueError("price_step must be an exact positive Decimal")
        if not isinstance(self.price_origin, Decimal) or not self.price_origin.is_finite():
            raise ValueError("price_origin must be an exact finite Decimal")
        if self.price_origin < 0 or self.price_origin >= self.price_step:
            raise ValueError("price_origin must be in the canonical [0, step) lattice range")

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "effective_start": self.effective_start,
            "effective_end": self.effective_end,
            "price_step": _decimal_text(self.price_step),
            "price_origin": _decimal_text(self.price_origin),
        }

    @classmethod
    def from_dict(cls, payload: object) -> EffectivePrecisionV2:
        values = _exact_mapping(
            payload,
            {"symbol", "effective_start", "effective_end", "price_step", "price_origin"},
            "effective precision entry",
        )
        return cls(
            symbol=values["symbol"],  # type: ignore[arg-type]
            effective_start=values["effective_start"],  # type: ignore[arg-type]
            effective_end=values["effective_end"],  # type: ignore[arg-type]
            price_step=_parse_decimal(values["price_step"], "price_step"),
            price_origin=_parse_decimal(values["price_origin"], "price_origin"),
        )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationPrecisionAuthorityV2:
    """Factory-sealed publication whose original evidence is reopened on use."""

    status: PrecisionAuthorityStatusV2
    request: PrecisionAuthorityRequestV2
    coverage_identity: str
    split_identity: str
    boundary_sha256: str
    minute_source_identity: str
    minute_publication_sha256: str
    aggregate_identity: str
    aggregate_publication_sha256: str
    authority_source_sha256: str | None
    authority_source_identity_sha256: str | None
    verifier_evidence_sha256: str | None
    entries: tuple[EffectivePrecisionV2, ...]
    uncovered: tuple[tuple[str, str, str], ...]
    failure: str | None
    signal_count: int
    outcome_rows: int
    final_scope_attempts: int
    final_rows: int
    final_access_records: int
    precision_authority_identity: PrecisionAuthorityIdentityV2
    publication_root: Path = field(repr=False, compare=False)
    authority_source_path: Path | None = field(repr=False, compare=False)
    verifier_evidence_path: Path | None = field(repr=False, compare=False)
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None
    _schema: ClassVar[str] = "phase5-validation-precision-authority-v2"

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY:
            raise TypeError("ValidationPrecisionAuthorityV2 requires its verifier factory")
        if not isinstance(self.status, PrecisionAuthorityStatusV2):
            raise TypeError("precision authority status is invalid")
        for value, label in (
            (self.boundary_sha256, "boundary sha256"),
            (self.minute_publication_sha256, "minute publication sha256"),
            (self.aggregate_publication_sha256, "aggregate publication sha256"),
        ):
            _require_sha256(value, label)
        for optional_value, optional_label in (
            (self.authority_source_sha256, "authority source sha256"),
            (self.authority_source_identity_sha256, "authority source identity"),
            (self.verifier_evidence_sha256, "verifier evidence sha256"),
        ):
            if optional_value is not None:
                _require_sha256(optional_value, optional_label)
        for label in (
            "signal_count",
            "outcome_rows",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
        ):
            if getattr(self, label) != 0:
                raise ValueError(
                    "precision publication must precede signals, outcomes, and final access"
                )
        if self.entries != tuple(
            sorted(self.entries, key=lambda item: (item.symbol, item.effective_start))
        ):
            raise ValueError("precision entries must be deterministically ordered")
        if self.uncovered != tuple(sorted(set(self.uncovered))):
            raise ValueError("uncovered precision intervals must be uniquely ordered")
        if self.status is PrecisionAuthorityStatusV2.AVAILABLE:
            if (
                self.request.source_kind is not PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE
                or self.failure is not None
                or self.uncovered
                or not self.entries
                or self.authority_source_sha256 is None
                or self.authority_source_identity_sha256 is None
                or self.verifier_evidence_sha256 is None
            ):
                raise ValueError("available precision authority is incomplete")
        elif self.status is PrecisionAuthorityStatusV2.INCOMPLETE:
            if (
                self.request.source_kind is not PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE
                or self.failure != "effective_time_coverage_incomplete"
                or not self.uncovered
                or self.authority_source_sha256 is None
                or self.authority_source_identity_sha256 is None
                or self.verifier_evidence_sha256 is None
            ):
                raise ValueError("incomplete precision authority is inconsistent")
        elif (
            self.failure is None
            or self.entries
            or not self.uncovered
            or self.request.source_kind is PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE
        ):
            raise ValueError("unavailable precision authority is inconsistent")
        payload = self._identity_payload()
        if self.precision_authority_identity != PrecisionAuthorityIdentityV2.from_payload(payload):
            raise ValueError("precision authority identity differs")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("precision authority canonical bytes differ")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "request": self.request.to_dict(),
            "coverage_identity": self.coverage_identity,
            "split_identity": self.split_identity,
            "boundary_sha256": self.boundary_sha256,
            "minute_source_identity": self.minute_source_identity,
            "minute_publication_sha256": self.minute_publication_sha256,
            "aggregate_identity": self.aggregate_identity,
            "aggregate_publication_sha256": self.aggregate_publication_sha256,
            "authority_source_sha256": self.authority_source_sha256,
            "authority_source_identity_sha256": self.authority_source_identity_sha256,
            "verifier_evidence_sha256": self.verifier_evidence_sha256,
            "authority_source_path": (
                str(self.authority_source_path) if self.authority_source_path is not None else None
            ),
            "verifier_evidence_path": (
                str(self.verifier_evidence_path)
                if self.verifier_evidence_path is not None
                else None
            ),
            "entries": [item.to_dict() for item in self.entries],
            "uncovered": [
                {"symbol": symbol, "start": start, "end": end}
                for symbol, start, end in self.uncovered
            ],
            "failure": self.failure,
            "signal_count": self.signal_count,
            "outcome_rows": self.outcome_rows,
            "final_scope_attempts": self.final_scope_attempts,
            "final_rows": self.final_rows,
            "final_access_records": self.final_access_records,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            **self._identity_payload(),
            "precision_authority_identity": self.precision_authority_identity.value,
        }


@dataclass(frozen=True, slots=True)
class _Registration:
    publication: weakref.ReferenceType[ValidationPrecisionAuthorityV2]
    publication_bytes: bytes
    publication_root: Path
    source_bytes: bytes | None
    verifier_bytes: bytes | None
    coverage: SourceCoveragePublicationV2
    split: DevelopmentSplitPublicationV2
    boundary: DevelopmentReadBoundaryV2
    availability: ScopedSourceAvailabilityV2
    minute: ValidationSourcePublicationV2
    aggregate: AggregatePublicationV2


_VERIFIED: dict[int, _Registration] = {}


def publish_validation_precision_authority_v2(
    *,
    request: PrecisionAuthorityRequestV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    minute_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
    output_root: Path,
    authority_source_path: Path | None = None,
    verifier_evidence_path: Path | None = None,
) -> ValidationPrecisionAuthorityV2:
    """Publish precision authority without network, database, outcome, or final reads."""

    _verify_parents(
        coverage, split, boundary, availability, minute_publication, aggregate_publication
    )
    _verify_request_scope(request, boundary)
    source_bytes: bytes | None = None
    verifier_bytes: bytes | None = None
    entries: tuple[EffectivePrecisionV2, ...] = ()
    source_identity: str | None = None
    source_sha: str | None = None
    verifier_sha: str | None = None
    uncovered = _full_uncovered(request)
    if request.source_kind is PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE:
        if authority_source_path is None or verifier_evidence_path is None:
            raise ValueError("historical precision requires original source and verifier evidence")
        source_path = _absolute_regular_path(authority_source_path, "authority source")
        verifier_path = _absolute_regular_path(verifier_evidence_path, "verifier evidence")
        source_bytes = read_bounded_regular(source_path, _MAX_SOURCE_BYTES)
        verifier_bytes = read_bounded_regular(verifier_path, _MAX_CONTROL_BYTES)
        entries, source_identity = _verify_historical_source(
            source_bytes, verifier_bytes, request=request
        )
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        verifier_sha = hashlib.sha256(verifier_bytes).hexdigest()
        uncovered = _uncovered_intervals(request, entries)
        if uncovered:
            status = PrecisionAuthorityStatusV2.INCOMPLETE
            failure = "effective_time_coverage_incomplete"
        else:
            status = PrecisionAuthorityStatusV2.AVAILABLE
            failure = None
    else:
        source_path = None
        verifier_path = None
        if authority_source_path is not None:
            source_path = _absolute_regular_path(authority_source_path, "limitation source")
            source_bytes = read_bounded_regular(source_path, _MAX_SOURCE_BYTES)
            source_sha = hashlib.sha256(source_bytes).hexdigest()
        if verifier_evidence_path is not None:
            raise ValueError(
                "unavailable/current-only evidence cannot carry historical verifier authority"
            )
        status = PrecisionAuthorityStatusV2.UNAVAILABLE
        failure = (
            "current_only_exchange_info_has_no_historical_effective_time_authority"
            if request.source_kind is PrecisionSourceKindV2.CURRENT_ONLY_EXCHANGE_INFO
            else "historical_precision_authority_unavailable"
        )
    publication = _make_publication(
        request=request,
        coverage=coverage,
        split=split,
        boundary=boundary,
        minute=minute_publication,
        aggregate=aggregate_publication,
        output_root=Path(output_root),
        status=status,
        entries=entries if status is not PrecisionAuthorityStatusV2.UNAVAILABLE else (),
        uncovered=uncovered,
        source_sha=source_sha,
        source_identity=source_identity,
        verifier_sha=verifier_sha,
        source_path=source_path,
        verifier_path=verifier_path,
        failure=failure,
    )
    _publish(publication)
    try:
        if (
            source_bytes is not None
            and source_path is not None
            and read_bounded_regular(source_path, _MAX_SOURCE_BYTES) != source_bytes
        ):
            raise ValueError("precision source bytes changed during publication")
        if (
            verifier_bytes is not None
            and verifier_path is not None
            and read_bounded_regular(verifier_path, _MAX_CONTROL_BYTES) != verifier_bytes
        ):
            raise ValueError("precision verifier bytes changed during publication")
        _verify_parents(
            coverage, split, boundary, availability, minute_publication, aggregate_publication
        )
    except Exception:
        _remove_publication(publication.publication_root)
        raise
    _register(
        publication,
        source_bytes=source_bytes,
        verifier_bytes=verifier_bytes,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute=minute_publication,
        aggregate=aggregate_publication,
    )
    return verify_validation_precision_authority_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute_publication=minute_publication,
        aggregate_publication=aggregate_publication,
    )


def verify_validation_precision_authority_v2(
    publication: ValidationPrecisionAuthorityV2,
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    minute_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
) -> ValidationPrecisionAuthorityV2:
    """Reopen original publication, evidence bytes, and exact parent capabilities."""

    registration = _registration(publication)
    if (
        registration.coverage is not coverage
        or registration.split is not split
        or registration.boundary is not boundary
        or registration.availability is not availability
        or registration.minute is not minute_publication
        or registration.aggregate is not aggregate_publication
    ):
        raise ValueError("precision authority parent capabilities are stale, copied, or mixed")
    _verify_parents(
        coverage, split, boundary, availability, minute_publication, aggregate_publication
    )
    _verify_request_scope(publication.request, boundary)
    _verify_parent_bindings(
        publication, coverage, split, boundary, minute_publication, aggregate_publication
    )
    if (
        read_bounded_regular(publication.publication_root / "publication.json", _MAX_CONTROL_BYTES)
        != registration.publication_bytes
    ):
        raise ValueError("precision authority original publication bytes changed")
    if read_bounded_regular(
        publication.publication_root / "_SUCCESS", 128
    ) != f"{publication.precision_authority_identity.value}\n".encode("ascii"):
        raise ValueError("precision authority success marker changed")
    if set(bounded_regular_files(publication.publication_root, maximum=8)) != {
        "_SUCCESS",
        "publication.json",
    }:
        raise ValueError("precision authority publication contains missing or extra artifacts")
    if registration.source_bytes is not None:
        if (
            publication.authority_source_path is None
            or read_bounded_regular(publication.authority_source_path, _MAX_SOURCE_BYTES)
            != registration.source_bytes
            or publication.authority_source_sha256
            != hashlib.sha256(registration.source_bytes).hexdigest()
        ):
            raise ValueError("precision authority original source bytes changed")
    elif (
        publication.authority_source_sha256 is not None
        or publication.authority_source_path is not None
    ):
        raise ValueError("precision authority source binding lacks original bytes")
    if registration.verifier_bytes is not None:
        if registration.source_bytes is None:
            raise ValueError("precision verifier lacks its original source bytes")
        if (
            publication.verifier_evidence_path is None
            or read_bounded_regular(publication.verifier_evidence_path, _MAX_CONTROL_BYTES)
            != registration.verifier_bytes
            or publication.verifier_evidence_sha256
            != hashlib.sha256(registration.verifier_bytes).hexdigest()
        ):
            raise ValueError("precision authority original verifier bytes changed")
        entries, source_identity = _verify_historical_source(
            registration.source_bytes,
            registration.verifier_bytes,
            request=publication.request,
        )
        if (
            entries != publication.entries
            or source_identity != publication.authority_source_identity_sha256
        ):
            raise ValueError(
                "precision authority effective schedule differs from original evidence"
            )
        expected_uncovered = _uncovered_intervals(publication.request, entries)
        if expected_uncovered != publication.uncovered:
            raise ValueError(
                "precision authority effective coverage differs from original evidence"
            )
    elif (
        publication.verifier_evidence_sha256 is not None
        or publication.verifier_evidence_path is not None
    ):
        raise ValueError("precision authority verifier binding lacks original bytes")
    return publication


def load_validation_precision_authority_v2(
    *,
    publication_root: Path,
    expected_precision_authority_identity: PrecisionAuthorityIdentityV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    minute_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
) -> ValidationPrecisionAuthorityV2:
    """Load only bytes anchored by an already-frozen typed precision identity."""

    root = Path(publication_root)
    canonical = read_bounded_regular(root / "publication.json", _MAX_CONTROL_BYTES)
    publication = _from_dict(_decode(canonical), publication_root=root, canonical_bytes=canonical)
    if publication.precision_authority_identity != expected_precision_authority_identity:
        raise ValueError("precision authority differs from expected frozen identity")
    source_bytes = (
        read_bounded_regular(publication.authority_source_path, _MAX_SOURCE_BYTES)
        if publication.authority_source_path is not None
        else None
    )
    verifier_bytes = (
        read_bounded_regular(publication.verifier_evidence_path, _MAX_CONTROL_BYTES)
        if publication.verifier_evidence_path is not None
        else None
    )
    _register(
        publication,
        source_bytes=source_bytes,
        verifier_bytes=verifier_bytes,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute=minute_publication,
        aggregate=aggregate_publication,
    )
    return verify_validation_precision_authority_v2(
        publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute_publication=minute_publication,
        aggregate_publication=aggregate_publication,
    )


def precision_requirement_status_v2(
    publication: ValidationPrecisionAuthorityV2,
) -> PrecisionRequirementStatusV2:
    """Fail closed before detector issuance; this function never reads signals/outcomes."""

    registration = _registration(publication)
    verify_validation_precision_authority_v2(
        publication,
        coverage=registration.coverage,
        split=registration.split,
        boundary=registration.boundary,
        availability=registration.availability,
        minute_publication=registration.minute,
        aggregate_publication=registration.aggregate,
    )
    return (
        PrecisionRequirementStatusV2.READY
        if publication.status is PrecisionAuthorityStatusV2.AVAILABLE
        else PrecisionRequirementStatusV2.NOT_EVALUATED
    )


def verified_effective_precision_v2(
    publication: ValidationPrecisionAuthorityV2, *, symbol: str, at: datetime
) -> EffectivePrecisionV2:
    """Return one exact lattice only after the pre-detector availability gate."""

    if precision_requirement_status_v2(publication) is not PrecisionRequirementStatusV2.READY:
        raise ValueError("required precision authority is not_evaluated")
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError("precision lookup time must be UTC-aware")
    matches = tuple(
        item
        for item in publication.entries
        if item.symbol == symbol
        and _parse_utc(item.effective_start, "effective_start")
        <= at
        < _parse_utc(item.effective_end, "effective_end")
    )
    if len(matches) != 1:
        raise ValueError("precision schedule has no unique effective entry")
    return matches[0]


def _make_publication(
    *,
    request: PrecisionAuthorityRequestV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    minute: ValidationSourcePublicationV2,
    aggregate: AggregatePublicationV2,
    output_root: Path,
    status: PrecisionAuthorityStatusV2,
    entries: tuple[EffectivePrecisionV2, ...],
    uncovered: tuple[tuple[str, str, str], ...],
    source_sha: str | None,
    source_identity: str | None,
    verifier_sha: str | None,
    source_path: Path | None,
    verifier_path: Path | None,
    failure: str | None,
) -> ValidationPrecisionAuthorityV2:
    values: dict[str, object] = {
        "status": status,
        "request": request,
        "coverage_identity": coverage.coverage_identity.value,
        "split_identity": split.split_identity.value,
        "boundary_sha256": boundary.boundary_sha256,
        "minute_source_identity": minute.source_publication_identity.value,
        "minute_publication_sha256": hashlib.sha256(minute.canonical_bytes).hexdigest(),
        "aggregate_identity": aggregate.aggregate_identity.value,
        "aggregate_publication_sha256": hashlib.sha256(aggregate.canonical_bytes).hexdigest(),
        "authority_source_sha256": source_sha,
        "authority_source_identity_sha256": source_identity,
        "verifier_evidence_sha256": verifier_sha,
        "authority_source_path": source_path,
        "verifier_evidence_path": verifier_path,
        "entries": entries,
        "uncovered": uncovered,
        "failure": failure,
        "signal_count": 0,
        "outcome_rows": 0,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    identity_payload = _identity_payload_from_values(values)
    identity = PrecisionAuthorityIdentityV2.from_payload(identity_payload)
    public = {
        "schema_version": ValidationPrecisionAuthorityV2._schema,
        **identity_payload,
        "precision_authority_identity": identity.value,
    }
    constructor_values = {
        key: value
        for key, value in values.items()
        if key not in {"authority_source_path", "verifier_evidence_path"}
    }
    return ValidationPrecisionAuthorityV2(
        **constructor_values,  # type: ignore[arg-type]
        precision_authority_identity=identity,
        publication_root=output_root,
        authority_source_path=source_path,
        verifier_evidence_path=verifier_path,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_FACTORY,
    )


def _identity_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    request = values["request"]
    assert isinstance(request, PrecisionAuthorityRequestV2)
    entries = values["entries"]
    uncovered = values["uncovered"]
    assert isinstance(entries, tuple) and isinstance(uncovered, tuple)
    return {
        **{
            key: (
                str(value)
                if isinstance(value, Path)
                else value.value
                if isinstance(value, Enum)
                else value
            )
            for key, value in values.items()
            if key not in {"request", "entries", "uncovered"}
        },
        "request": request.to_dict(),
        "entries": [item.to_dict() for item in entries],
        "uncovered": [{"symbol": s, "start": a, "end": b} for s, a, b in uncovered],
    }


def _verify_historical_source(
    source_bytes: bytes, verifier_bytes: bytes, *, request: PrecisionAuthorityRequestV2
) -> tuple[tuple[EffectivePrecisionV2, ...], str]:
    verifier_sha = hashlib.sha256(verifier_bytes).hexdigest()
    if verifier_sha not in _TRUSTED_HISTORICAL_PRECISION_VERIFIER_SHA256:
        raise ValueError("historical precision verifier is not independently trusted")
    source = _exact_mapping(
        _decode(source_bytes),
        {
            "schema_version",
            "venue",
            "query_source",
            "published_at",
            "retrieval_identity_sha256",
            "checkpoint_identity_sha256",
            "entries",
            "source_identity_sha256",
        },
        "historical precision source",
    )
    if source["schema_version"] != _SOURCE_SCHEMA:
        raise ValueError("historical precision source schema is invalid")
    _parse_utc(source["published_at"], "historical precision published_at")
    raw_entries = source["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) > _MAX_ENTRIES:
        raise ValueError("historical precision entry count is invalid or unbounded")
    entries = tuple(EffectivePrecisionV2.from_dict(item) for item in raw_entries)
    if entries != tuple(sorted(entries, key=lambda item: (item.symbol, item.effective_start))):
        raise ValueError("historical precision entries are not ordered")
    if any(item.symbol not in request.requested_symbols for item in entries):
        raise ValueError("historical precision source contains out-of-scope symbols")
    _validate_schedule(entries)
    payload = {key: value for key, value in source.items() if key != "source_identity_sha256"}
    expected_source_identity = hash_json(_SOURCE_ID_DOMAIN, payload)
    if source["source_identity_sha256"] != expected_source_identity:
        raise ValueError("historical precision source identity differs")
    if (
        source["venue"] != request.venue
        or source["query_source"] != request.query_source
        or source["retrieval_identity_sha256"] != request.retrieval_identity_sha256
        or source["checkpoint_identity_sha256"] != request.checkpoint_identity_sha256
    ):
        raise ValueError("historical precision source scope or checkpoint differs")
    verifier = _exact_mapping(
        _decode(verifier_bytes),
        {
            "schema_version",
            "authority_source_sha256",
            "source_identity_sha256",
            "venue",
            "query_source",
            "retrieval_identity_sha256",
            "checkpoint_identity_sha256",
            "publisher_verified",
            "immutable_original",
            "verification_sha256",
        },
        "historical precision verifier",
    )
    if verifier["schema_version"] != _VERIFIER_SCHEMA:
        raise ValueError("historical precision verifier schema is invalid")
    verifier_payload = {
        key: value for key, value in verifier.items() if key != "verification_sha256"
    }
    if verifier["verification_sha256"] != hash_json(_VERIFIER_ID_DOMAIN, verifier_payload):
        raise ValueError("historical precision verifier identity differs")
    if (
        verifier["authority_source_sha256"] != hashlib.sha256(source_bytes).hexdigest()
        or verifier["source_identity_sha256"] != expected_source_identity
        or verifier["venue"] != request.venue
        or verifier["query_source"] != request.query_source
        or verifier["retrieval_identity_sha256"] != request.retrieval_identity_sha256
        or verifier["checkpoint_identity_sha256"] != request.checkpoint_identity_sha256
        or verifier["publisher_verified"] is not True
        or verifier["immutable_original"] is not True
    ):
        raise ValueError("historical precision verifier does not bind the original dated source")
    return entries, expected_source_identity


def _uncovered_intervals(
    request: PrecisionAuthorityRequestV2, entries: tuple[EffectivePrecisionV2, ...]
) -> tuple[tuple[str, str, str], ...]:
    uncovered: list[tuple[str, str, str]] = []
    by_symbol = {
        symbol: tuple(item for item in entries if item.symbol == symbol)
        for symbol in request.requested_symbols
    }
    for symbol in request.requested_symbols:
        schedule = by_symbol[symbol]
        for start_text, end_text in request.requested_intervals:
            cursor = _parse_utc(start_text, "requested interval start")
            end = _parse_utc(end_text, "requested interval end")
            for item in schedule:
                item_start = _parse_utc(item.effective_start, "effective_start")
                item_end = _parse_utc(item.effective_end, "effective_end")
                if item_end <= cursor or item_start >= end:
                    continue
                if item_start > cursor:
                    uncovered.append((symbol, _utc_text(cursor), _utc_text(min(item_start, end))))
                cursor = max(cursor, min(item_end, end))
                if cursor >= end:
                    break
            if cursor < end:
                uncovered.append((symbol, _utc_text(cursor), _utc_text(end)))
    return tuple(sorted(set(uncovered)))


def _full_uncovered(request: PrecisionAuthorityRequestV2) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (symbol, start, end)
        for symbol in request.requested_symbols
        for start, end in request.requested_intervals
    )


def _validate_schedule(entries: tuple[EffectivePrecisionV2, ...]) -> None:
    previous: dict[str, datetime] = {}
    for item in entries:
        start = _parse_utc(item.effective_start, "effective_start")
        if item.symbol in previous and start < previous[item.symbol]:
            raise ValueError("historical precision schedule overlaps")
        previous[item.symbol] = _parse_utc(item.effective_end, "effective_end")


def _verify_request_scope(
    request: PrecisionAuthorityRequestV2, boundary: DevelopmentReadBoundaryV2
) -> None:
    expected = PrecisionAuthorityRequestV2.from_boundary(
        boundary=boundary,
        venue=request.venue,
        query_source=request.query_source,
        source_kind=request.source_kind,
        retrieval_identity_sha256=request.retrieval_identity_sha256,
        checkpoint_identity_sha256=request.checkpoint_identity_sha256,
        limitation=request.limitation,
    )
    if request != expected:
        raise ValueError("precision request is outside or differs from development boundary")


def _verify_parents(
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    minute: ValidationSourcePublicationV2,
    aggregate: AggregatePublicationV2,
) -> None:
    verify_validation_aggregate_publication_v2(
        aggregate,
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    if minute.final_scope_attempts or minute.final_rows or minute.final_access_records:
        raise ValueError("precision authority cannot bind final source access")
    if aggregate.final_scope_attempts or aggregate.final_rows or aggregate.final_access_records:
        raise ValueError("precision authority cannot bind final aggregate access")


def _verify_parent_bindings(
    publication: ValidationPrecisionAuthorityV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    minute: ValidationSourcePublicationV2,
    aggregate: AggregatePublicationV2,
) -> None:
    if (
        publication.coverage_identity != coverage.coverage_identity.value
        or publication.split_identity != split.split_identity.value
        or publication.boundary_sha256 != boundary.boundary_sha256
        or publication.minute_source_identity != minute.source_publication_identity.value
        or publication.minute_publication_sha256
        != hashlib.sha256(minute.canonical_bytes).hexdigest()
        or publication.aggregate_identity != aggregate.aggregate_identity.value
        or publication.aggregate_publication_sha256
        != hashlib.sha256(aggregate.canonical_bytes).hexdigest()
    ):
        raise ValueError("precision authority parent identities are stale or mixed")


def _register(
    publication: ValidationPrecisionAuthorityV2,
    *,
    source_bytes: bytes | None,
    verifier_bytes: bytes | None,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    minute: ValidationSourcePublicationV2,
    aggregate: AggregatePublicationV2,
) -> None:
    identifier = id(publication)

    def cleanup(reference: weakref.ReferenceType[ValidationPrecisionAuthorityV2]) -> None:
        current = _VERIFIED.get(identifier)
        if current is not None and current.publication is reference:
            _VERIFIED.pop(identifier, None)

    reference = weakref.ref(publication, cleanup)
    _VERIFIED[identifier] = _Registration(
        reference,
        publication.canonical_bytes,
        publication.publication_root,
        source_bytes,
        verifier_bytes,
        coverage,
        split,
        boundary,
        availability,
        minute,
        aggregate,
    )


def _registration(publication: ValidationPrecisionAuthorityV2) -> _Registration:
    if not isinstance(publication, ValidationPrecisionAuthorityV2):
        raise TypeError("precision authority must be verifier-issued")
    registered = _VERIFIED.get(id(publication))
    if registered is None or registered.publication() is not publication:
        raise ValueError("precision authority is not the registered original capability")
    try:
        current = publication_json_bytes(publication.to_dict())
        identity = PrecisionAuthorityIdentityV2.from_payload(publication._identity_payload())
    except Exception as error:
        raise ValueError("precision authority serialization is invalid") from error
    if (
        current != registered.publication_bytes
        or publication.canonical_bytes != registered.publication_bytes
        or publication.precision_authority_identity != identity
        or publication.publication_root != registered.publication_root
    ):
        raise ValueError("precision authority serialization or identity differs from original")
    return registered


def _from_dict(
    payload: object, *, publication_root: Path, canonical_bytes: bytes
) -> ValidationPrecisionAuthorityV2:
    values = _exact_mapping(
        payload,
        {
            "schema_version",
            "status",
            "request",
            "coverage_identity",
            "split_identity",
            "boundary_sha256",
            "minute_source_identity",
            "minute_publication_sha256",
            "aggregate_identity",
            "aggregate_publication_sha256",
            "authority_source_sha256",
            "authority_source_identity_sha256",
            "verifier_evidence_sha256",
            "authority_source_path",
            "verifier_evidence_path",
            "entries",
            "uncovered",
            "failure",
            "signal_count",
            "outcome_rows",
            "final_scope_attempts",
            "final_rows",
            "final_access_records",
            "precision_authority_identity",
        },
        "precision authority publication",
    )
    if values["schema_version"] != ValidationPrecisionAuthorityV2._schema:
        raise ValueError("precision authority schema is invalid")
    request_values = _exact_mapping(
        values["request"],
        {
            "venue",
            "query_source",
            "source_kind",
            "requested_symbols",
            "requested_intervals",
            "retrieval_identity_sha256",
            "checkpoint_identity_sha256",
            "limitation",
        },
        "precision request",
    )
    raw_intervals = request_values["requested_intervals"]
    raw_symbols = request_values["requested_symbols"]
    if not isinstance(raw_intervals, list) or not isinstance(raw_symbols, list):
        raise TypeError("precision request symbols and intervals must be lists")
    request = PrecisionAuthorityRequestV2(
        venue=request_values["venue"],
        query_source=request_values["query_source"],  # type: ignore[arg-type]
        source_kind=PrecisionSourceKindV2(request_values["source_kind"]),
        requested_symbols=tuple(raw_symbols),  # type: ignore[arg-type]
        requested_intervals=tuple(
            (
                _exact_mapping(item, {"start", "end"}, "requested interval")["start"],
                _exact_mapping(item, {"start", "end"}, "requested interval")["end"],
            )
            for item in raw_intervals
        ),  # type: ignore[arg-type]
        retrieval_identity_sha256=request_values["retrieval_identity_sha256"],
        checkpoint_identity_sha256=request_values["checkpoint_identity_sha256"],
        limitation=request_values["limitation"],  # type: ignore[arg-type]
    )
    raw_entries = values["entries"]
    raw_uncovered = values["uncovered"]
    if not isinstance(raw_entries, list) or not isinstance(raw_uncovered, list):
        raise TypeError("precision entries and uncovered intervals must be lists")
    uncovered = tuple(
        (
            _exact_mapping(item, {"symbol", "start", "end"}, "uncovered interval")["symbol"],
            _exact_mapping(item, {"symbol", "start", "end"}, "uncovered interval")["start"],
            _exact_mapping(item, {"symbol", "start", "end"}, "uncovered interval")["end"],
        )
        for item in raw_uncovered
    )
    source_path = _optional_absolute_path(values["authority_source_path"], "authority source")
    verifier_path = _optional_absolute_path(values["verifier_evidence_path"], "verifier evidence")
    return ValidationPrecisionAuthorityV2(
        status=PrecisionAuthorityStatusV2(values["status"]),
        request=request,
        coverage_identity=values["coverage_identity"],
        split_identity=values["split_identity"],
        boundary_sha256=values["boundary_sha256"],  # type: ignore[arg-type]
        minute_source_identity=values["minute_source_identity"],
        minute_publication_sha256=values["minute_publication_sha256"],
        aggregate_identity=values["aggregate_identity"],
        aggregate_publication_sha256=values["aggregate_publication_sha256"],  # type: ignore[arg-type]
        authority_source_sha256=values["authority_source_sha256"],
        authority_source_identity_sha256=values["authority_source_identity_sha256"],
        verifier_evidence_sha256=values["verifier_evidence_sha256"],  # type: ignore[arg-type]
        entries=tuple(EffectivePrecisionV2.from_dict(item) for item in raw_entries),
        uncovered=uncovered,  # type: ignore[arg-type]
        failure=values["failure"],
        signal_count=values["signal_count"],
        outcome_rows=values["outcome_rows"],
        final_scope_attempts=values["final_scope_attempts"],
        final_rows=values["final_rows"],
        final_access_records=values["final_access_records"],  # type: ignore[arg-type]
        precision_authority_identity=PrecisionAuthorityIdentityV2(
            values["precision_authority_identity"]
        ),
        publication_root=publication_root,
        authority_source_path=source_path,
        verifier_evidence_path=verifier_path,
        canonical_bytes=canonical_bytes,
        _factory_token=_FACTORY,
    )


def _publish(publication: ValidationPrecisionAuthorityV2) -> None:
    root = publication.publication_root
    require_regular_directory(root.parent)
    if path_exists_no_follow(root):
        raise FileExistsError(f"refusing existing precision publication: {root}")
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.", suffix=".tmp", dir=root.parent))
    try:
        _write_no_clobber(stage / "publication.json", publication.canonical_bytes)
        _write_no_clobber(
            stage / "_SUCCESS",
            f"{publication.precision_authority_identity.value}\n".encode("ascii"),
        )
        _fsync_tree(stage)
        _rename_no_replace(stage, root)
        _fsync_directory(root.parent)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _remove_publication(root: Path) -> None:
    if path_exists_no_follow(root):
        shutil.rmtree(root, ignore_errors=False)
        _fsync_directory(root.parent)


def _write_no_clobber(path: Path, content: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _fsync_tree(stage: Path) -> None:
    if set(bounded_regular_files(stage, maximum=8)) != {"_SUCCESS", "publication.json"}:
        raise ValueError("precision stage is incomplete")
    _fsync_directory(stage)


def _rename_no_replace(stage: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(stage, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic no-replace precision publication is unsupported")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1) == 0:
        return
    number = ctypes.get_errno()
    if number == errno.EEXIST:
        raise FileExistsError(f"refusing existing precision publication: {destination}")
    raise OSError(number, os.strerror(number), destination)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _absolute_regular_path(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    return candidate


def _optional_absolute_path(value: object, label: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} path must be a string or null")
    return _absolute_regular_path(Path(value), label)


def _decode(content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("precision evidence is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("precision evidence must be a JSON object")
    return payload


def _exact_mapping(payload: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} fields differ from the canonical schema")
    return payload


def _validate_intervals(intervals: tuple[tuple[str, str], ...]) -> None:
    if not intervals or intervals != tuple(sorted(set(intervals))):
        raise ValueError("requested intervals must be non-empty and uniquely ordered")
    previous: datetime | None = None
    for start_text, end_text in intervals:
        start = _parse_utc(start_text, "requested interval start")
        end = _parse_utc(end_text, "requested interval end")
        if end <= start or (previous is not None and start < previous):
            raise ValueError("requested intervals overlap or are invalid")
        previous = end


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be canonical UTC") from error
    if (
        parsed.utcoffset() != timedelta(0)
        or parsed.second
        or parsed.microsecond
        or _utc_text(parsed) != value
    ):
        raise ValueError(f"{label} must be minute-aligned canonical UTC")
    return parsed


def _utc_text(value: datetime) -> str:
    if (
        value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.second
        or value.microsecond
    ):
        raise ValueError("timestamp must be minute-aligned UTC")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value or value.strip() != value or "e" in value.lower():
        raise ValueError(f"{label} must be a canonical fixed-point decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be an exact Decimal") from error
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise ValueError(f"{label} must be a canonical exact Decimal")
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _canonical_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a non-empty canonical string")
    return value


__all__ = [
    "EffectivePrecisionV2",
    "PrecisionAuthorityRequestV2",
    "PrecisionAuthorityStatusV2",
    "PrecisionRequirementStatusV2",
    "PrecisionSourceKindV2",
    "ValidationPrecisionAuthorityV2",
    "load_validation_precision_authority_v2",
    "precision_requirement_status_v2",
    "publish_validation_precision_authority_v2",
    "verified_effective_precision_v2",
    "verify_validation_precision_authority_v2",
]
