"""Authenticated Phase 5 V2 execution-cost completeness authority.

This publisher does not estimate executable net returns.  It records exactly
which cost dimensions are observed, only proxied, unavailable, or genuinely
not applicable for the independently verified development source.  Public
trade archives cannot establish actual paid fees, bid/ask spread, execution
slippage, latency, fill behaviour, missed fills, or capacity.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field, fields
from enum import Enum
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, ClassVar
import weakref

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationV2,
    verify_validation_aggregate_publication_v2,
)
from market_structure_lab.data.binance_archive_v2 import (
    BinanceArchiveAcquisitionResultV2,
    BinanceArchiveRequestManifestV2,
    verify_binance_archive_acquisition_v2,
    verify_binance_archive_request_manifest_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceAvailabilityV2,
    ValidationSourcePublicationV2,
    verify_validation_source_publication_v2,
)
from market_structure_lab.research.validation_v2_models import (
    CostAuthorityIdentityV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    verify_development_read_boundary_v2,
)

_MAX_PUBLICATION_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT_FILES = 2
_FACTORY = object()
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class CostDimensionV2(str, Enum):
    FEE = "fee"
    SPREAD = "spread"
    SLIPPAGE = "slippage"
    FUNDING = "funding"
    LATENCY = "latency"
    FILL = "fill"
    MISSED_FILL = "missed_fill"
    TURNOVER = "turnover"
    CAPACITY = "capacity"


REQUIRED_COST_DIMENSIONS_V2: tuple[str, ...] = tuple(item.value for item in CostDimensionV2)


class CostEvidenceStatusV2(str, Enum):
    OBSERVED = "observed"
    PROXY = "proxy"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class CostEvaluationStatusV2(str, Enum):
    COMPLETED = "completed"
    NOT_EVALUATED = "not_evaluated"


class CostConclusionV2(str, Enum):
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class CostDimensionEvidenceV2:
    """One non-substitutable cost dimension and its evidence boundary."""

    dimension: CostDimensionV2
    status: CostEvidenceStatusV2
    value: None
    formula: str | None
    units: str | None
    window: str | None
    exclusions: tuple[str, ...]
    archive_sha256: tuple[str, ...]
    checksum_sha256: tuple[str, ...]
    source_publication_identity: str
    boundary_sha256: str
    proxy_not_promotion_grade: bool
    reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, CostDimensionV2):
            raise TypeError("cost dimension must be typed")
        if not isinstance(self.status, CostEvidenceStatusV2):
            raise TypeError("cost evidence status must be typed")
        if self.value is not None:
            raise ValueError("cost evidence cannot contain a numeric default")
        if not self.source_publication_identity.startswith("SRCV2-"):
            raise ValueError("cost evidence source identity is invalid")
        _require_sha256(self.source_publication_identity.removeprefix("SRCV2-"), "source identity")
        _require_sha256(self.boundary_sha256, "boundary_sha256")
        _require_digest_tuple(self.archive_sha256, "archive_sha256", allow_empty=True)
        _require_digest_tuple(self.checksum_sha256, "checksum_sha256", allow_empty=True)
        if len(self.archive_sha256) != len(self.checksum_sha256):
            raise ValueError("archive and checksum bindings differ")
        if len(set(self.exclusions)) != len(self.exclusions) or any(
            not item or item.strip() != item for item in self.exclusions
        ):
            raise ValueError("cost evidence exclusions must be unique canonical strings")
        if self.status is CostEvidenceStatusV2.PROXY:
            if not all((self.formula, self.units, self.window, self.exclusions)):
                raise ValueError("proxy evidence requires formula, units, window, and exclusions")
            if not self.archive_sha256 or not self.checksum_sha256:
                raise ValueError("proxy evidence requires archive and checksum bindings")
            if self.proxy_not_promotion_grade is not True:
                raise ValueError("proxy evidence must be explicitly non-promotion-grade")
            if self.reason is not None:
                raise ValueError("proxy evidence cannot carry an unavailable reason")
        elif self.status is CostEvidenceStatusV2.OBSERVED:
            if self.formula is not None or self.proxy_not_promotion_grade:
                raise ValueError("observed evidence cannot be presented as a proxy")
            if not self.units or not self.window or not self.archive_sha256:
                raise ValueError("observed evidence requires units, window, and archive bindings")
            if self.reason is not None:
                raise ValueError("observed evidence cannot carry an unavailable reason")
        elif self.status is CostEvidenceStatusV2.UNAVAILABLE:
            if any(
                (
                    self.formula,
                    self.units,
                    self.window,
                    self.exclusions,
                    self.archive_sha256,
                    self.checksum_sha256,
                )
            ):
                raise ValueError("unavailable evidence cannot carry substitute measurements")
            if self.proxy_not_promotion_grade or not self.reason:
                raise ValueError("unavailable evidence requires only an explicit reason")
        else:
            if self.dimension is not CostDimensionV2.FUNDING:
                raise ValueError("only verified spot funding may be not applicable")
            if any(
                (
                    self.formula,
                    self.units,
                    self.window,
                    self.exclusions,
                    self.archive_sha256,
                    self.checksum_sha256,
                )
            ):
                raise ValueError("not-applicable funding cannot contain a zero substitute")
            if self.proxy_not_promotion_grade or self.reason != "verified_spot_instrument":
                raise ValueError("spot funding not-applicability requires verified spot identity")

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "value": None,
            "formula": self.formula,
            "units": self.units,
            "window": self.window,
            "exclusions": list(self.exclusions),
            "archive_sha256": list(self.archive_sha256),
            "checksum_sha256": list(self.checksum_sha256),
            "source_publication_identity": self.source_publication_identity,
            "boundary_sha256": self.boundary_sha256,
            "proxy_not_promotion_grade": self.proxy_not_promotion_grade,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedCostAuthorityV2:
    """Publisher-issued authority whose original and parent bytes stay live."""

    coverage_identity: str
    split_identity: str
    boundary_sha256: str
    source_publication_identity: str
    aggregate_identity: str
    source_availability_sha256: str
    archive_manifest_sha256: str
    archive_publication_sha256: str
    archive_audit_publication_sha256: str
    allowed_origin: str
    instrument_kind: str
    dimensions: tuple[CostDimensionEvidenceV2, ...]
    evaluation_status: CostEvaluationStatusV2
    conclusion: CostConclusionV2
    incomplete_promotion_grade_dimensions: tuple[str, ...]
    not_evaluated_reasons: tuple[str, ...]
    final_scope_attempts: int
    final_rows: int
    final_access_records: int
    cost_identity: CostAuthorityIdentityV2
    publication_root: Path = field(repr=False, compare=False)
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None
    _schema: ClassVar[str] = "phase5-validation-cost-authority-v2"

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY:
            raise TypeError("VerifiedCostAuthorityV2 requires its publisher/verifier factory")
        expected_dimensions = tuple(item.dimension.value for item in self.dimensions)
        if expected_dimensions != REQUIRED_COST_DIMENSIONS_V2:
            raise ValueError("cost dimensions are missing, substituted, duplicated, or reordered")
        if not isinstance(self.evaluation_status, CostEvaluationStatusV2):
            raise TypeError("cost evaluation status must be typed")
        if self.conclusion is not CostConclusionV2.INCONCLUSIVE:
            raise ValueError("V2 cost authority cannot validate or promote an edge")
        for value, label in (
            (self.boundary_sha256, "boundary_sha256"),
            (self.source_availability_sha256, "source_availability_sha256"),
            (self.archive_manifest_sha256, "archive_manifest_sha256"),
            (self.archive_publication_sha256, "archive_publication_sha256"),
            (self.archive_audit_publication_sha256, "archive_audit_publication_sha256"),
        ):
            _require_sha256(value, label)
        if not self.coverage_identity.startswith("SCV2-"):
            raise ValueError("coverage identity is invalid")
        if not self.split_identity.startswith("DSV2-"):
            raise ValueError("split identity is invalid")
        if not self.source_publication_identity.startswith("SRCV2-"):
            raise ValueError("source publication identity is invalid")
        if not self.aggregate_identity.startswith("AGGV2-"):
            raise ValueError("aggregate identity is invalid")
        if self.instrument_kind != "spot" or self.allowed_origin != "https://data.binance.vision":
            raise ValueError("cost authority instrument source is not verified Binance spot")
        if self.final_scope_attempts or self.final_rows or self.final_access_records:
            raise ValueError("cost authority cannot contain final access")
        incomplete = tuple(
            item.dimension.value
            for item in self.dimensions
            if item.status in {CostEvidenceStatusV2.UNAVAILABLE, CostEvidenceStatusV2.PROXY}
        )
        if self.incomplete_promotion_grade_dimensions != incomplete:
            raise ValueError("incomplete promotion-grade dimension set differs")
        if self.evaluation_status is CostEvaluationStatusV2.COMPLETED:
            if self.not_evaluated_reasons:
                raise ValueError("completed cost authority cannot have not-evaluated reasons")
        elif not self.not_evaluated_reasons:
            raise ValueError("not-evaluated cost authority requires explicit missing input")
        if self.cost_identity != CostAuthorityIdentityV2.from_payload(self._identity_payload()):
            raise ValueError("cost authority identity differs from its payload")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("cost authority canonical bytes differ from publication")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "coverage_identity": self.coverage_identity,
            "split_identity": self.split_identity,
            "boundary_sha256": self.boundary_sha256,
            "source_publication_identity": self.source_publication_identity,
            "aggregate_identity": self.aggregate_identity,
            "source_availability_sha256": self.source_availability_sha256,
            "archive_manifest_sha256": self.archive_manifest_sha256,
            "archive_publication_sha256": self.archive_publication_sha256,
            "archive_audit_publication_sha256": self.archive_audit_publication_sha256,
            "allowed_origin": self.allowed_origin,
            "instrument_kind": self.instrument_kind,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "evaluation_status": self.evaluation_status.value,
            "conclusion": self.conclusion.value,
            "incomplete_promotion_grade_dimensions": list(
                self.incomplete_promotion_grade_dimensions
            ),
            "not_evaluated_reasons": list(self.not_evaluated_reasons),
            "final_scope_attempts": self.final_scope_attempts,
            "final_rows": self.final_rows,
            "final_access_records": self.final_access_records,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            **self._identity_payload(),
            "cost_identity": self.cost_identity.value,
        }


@dataclass(frozen=True, slots=True)
class _Registration:
    authority: weakref.ReferenceType[VerifiedCostAuthorityV2]
    canonical_bytes: bytes
    publication_root: Path
    source_publication: ValidationSourcePublicationV2
    aggregate_publication: AggregatePublicationV2
    coverage: SourceCoveragePublicationV2
    split: DevelopmentSplitPublicationV2
    boundary: DevelopmentReadBoundaryV2
    availability: ScopedSourceAvailabilityV2
    archive_manifest: BinanceArchiveRequestManifestV2
    archive_manifest_path: Path
    archive_acquisition: BinanceArchiveAcquisitionResultV2


_VERIFIED_COST_AUTHORITIES: dict[int, _Registration] = {}


def publish_validation_cost_authority_v2(
    *,
    source_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    archive_manifest: BinanceArchiveRequestManifestV2,
    archive_manifest_path: Path,
    archive_acquisition: BinanceArchiveAcquisitionResultV2,
    output_root: Path,
) -> VerifiedCostAuthorityV2:
    """Publish one fixed, truthful, development-only cost completeness state."""

    _verify_parents(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
    )
    _require_verified_spot_manifest(archive_manifest)
    output_root = Path(output_root)
    require_regular_directory(output_root.parent)
    if path_exists_no_follow(output_root):
        raise FileExistsError(f"refusing existing cost authority publication: {output_root}")
    authority = _make_authority(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_acquisition=archive_acquisition,
        output_root=output_root,
    )
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.", suffix=".tmp", dir=output_root.parent)
    )
    try:
        _write_no_clobber(stage / "publication.json", authority.canonical_bytes)
        _write_no_clobber(stage / "_SUCCESS", f"{authority.cost_identity.value}\n".encode("ascii"))
        _publish_stage_no_clobber(stage, output_root)
    except Exception:
        if path_exists_no_follow(stage):
            shutil.rmtree(stage, ignore_errors=False)
        raise
    _register(
        authority,
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=Path(archive_manifest_path),
        archive_acquisition=archive_acquisition,
    )
    return verify_validation_cost_authority_v2(
        authority,
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
    )


def verify_validation_cost_authority_v2(
    authority: VerifiedCostAuthorityV2,
    *,
    source_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    archive_manifest: BinanceArchiveRequestManifestV2,
    archive_manifest_path: Path,
    archive_acquisition: BinanceArchiveAcquisitionResultV2,
) -> VerifiedCostAuthorityV2:
    """Reopen every original parent and authority byte before consumption."""

    verify_validation_cost_authority_metadata_v2(authority)
    registered = _VERIFIED_COST_AUTHORITIES.get(id(authority))
    if registered is None or registered.authority() is not authority:
        raise ValueError("cost authority is not the registered original")
    if (
        registered.source_publication is not source_publication
        or registered.aggregate_publication is not aggregate_publication
        or registered.coverage is not coverage
        or registered.split is not split
        or registered.boundary is not boundary
        or registered.availability is not availability
        or registered.archive_manifest is not archive_manifest
        or registered.archive_manifest_path != Path(archive_manifest_path)
        or registered.archive_acquisition is not archive_acquisition
        or registered.publication_root != authority.publication_root
    ):
        raise ValueError("cost authority parent capability is not the registered original")
    _verify_parents(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
    )
    _require_verified_spot_manifest(archive_manifest)
    expected = _make_authority(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_acquisition=archive_acquisition,
        output_root=authority.publication_root,
    )
    try:
        current = publication_json_bytes(authority.to_dict())
    except Exception as error:
        raise ValueError("cost authority current serialization is invalid") from error
    if (
        authority.canonical_bytes != registered.canonical_bytes
        or current != registered.canonical_bytes
        or expected.canonical_bytes != registered.canonical_bytes
        or read_bounded_regular(
            authority.publication_root / "publication.json", _MAX_PUBLICATION_BYTES
        )
        != registered.canonical_bytes
    ):
        raise ValueError("cost authority original bytes, identity, or dimensions changed")
    if read_bounded_regular(authority.publication_root / "_SUCCESS", 128) != (
        f"{authority.cost_identity.value}\n".encode("ascii")
    ):
        raise ValueError("cost authority success marker changed")
    if set(bounded_regular_files(authority.publication_root, maximum=8)) != {
        "publication.json",
        "_SUCCESS",
    }:
        raise ValueError("cost authority publication has missing or extra artifacts")
    return authority


def verify_validation_cost_authority_metadata_v2(
    authority: VerifiedCostAuthorityV2,
) -> VerifiedCostAuthorityV2:
    """Verify registered cost metadata without reopening authority artifacts."""

    if type(authority) is not VerifiedCostAuthorityV2:
        raise TypeError("cost authority must be the exact publisher-issued type")
    if (
        type(authority.evaluation_status) is not CostEvaluationStatusV2
        or type(authority.canonical_bytes) is not bytes
        or type(authority.publication_root) is not type(Path())
        or type(authority.conclusion) is not CostConclusionV2
        or type(authority.cost_identity) is not CostAuthorityIdentityV2
        or type(authority.incomplete_promotion_grade_dimensions) is not tuple
        or len(authority.incomplete_promotion_grade_dimensions) > len(REQUIRED_COST_DIMENSIONS_V2)
        or any(type(item) is not str for item in authority.incomplete_promotion_grade_dimensions)
        or type(authority.not_evaluated_reasons) is not tuple
        or len(authority.not_evaluated_reasons) > len(REQUIRED_COST_DIMENSIONS_V2)
        or any(type(item) is not str for item in authority.not_evaluated_reasons)
        or type(authority.dimensions) is not tuple
        or len(authority.dimensions) != len(REQUIRED_COST_DIMENSIONS_V2)
        or any(
            type(item) is not CostDimensionEvidenceV2
            or type(item.dimension) is not CostDimensionV2
            or type(item.status) is not CostEvidenceStatusV2
            or type(item.exclusions) is not tuple
            or len(item.exclusions) > 100_000
            or any(type(value) is not str for value in item.exclusions)
            or type(item.archive_sha256) is not tuple
            or len(item.archive_sha256) > 100_000
            or any(type(value) is not str for value in item.archive_sha256)
            or type(item.checksum_sha256) is not tuple
            or len(item.checksum_sha256) > 100_000
            or any(type(value) is not str for value in item.checksum_sha256)
            for item in authority.dimensions
        )
    ):
        raise TypeError("cost authority contains non-exact nested metadata")
    registered = _VERIFIED_COST_AUTHORITIES.get(id(authority))
    if registered is None or registered.authority() is not authority:
        raise ValueError("cost authority is not the registered original")
    try:
        current = publication_json_bytes(authority.to_dict())
        identity = CostAuthorityIdentityV2.from_payload(authority._identity_payload())
    except Exception as error:
        raise ValueError("cost authority current serialization is invalid") from error
    if (
        current != registered.canonical_bytes
        or authority.canonical_bytes != registered.canonical_bytes
        or authority.cost_identity != identity
        or authority.publication_root != registered.publication_root
    ):
        raise ValueError("cost authority serialization or identity differs from original")
    return authority


def verified_cost_authority_bytes_v2(authority: VerifiedCostAuthorityV2) -> bytes:
    """Consume a capability only after reopening its complete registered chain."""

    registered = _VERIFIED_COST_AUTHORITIES.get(id(authority))
    if registered is None or registered.authority() is not authority:
        raise ValueError("cost authority is not the registered original")
    verify_validation_cost_authority_v2(
        authority,
        source_publication=registered.source_publication,
        aggregate_publication=registered.aggregate_publication,
        coverage=registered.coverage,
        split=registered.split,
        boundary=registered.boundary,
        availability=registered.availability,
        archive_manifest=registered.archive_manifest,
        archive_manifest_path=registered.archive_manifest_path,
        archive_acquisition=registered.archive_acquisition,
    )
    return registered.canonical_bytes


def load_validation_cost_authority_v2(
    *,
    publication_root: Path,
    expected_cost_identity: CostAuthorityIdentityV2,
    source_publication: ValidationSourcePublicationV2,
    aggregate_publication: AggregatePublicationV2,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    availability: ScopedSourceAvailabilityV2,
    archive_manifest: BinanceArchiveRequestManifestV2,
    archive_manifest_path: Path,
    archive_acquisition: BinanceArchiveAcquisitionResultV2,
) -> VerifiedCostAuthorityV2:
    """Load only exact original bytes anchored by a frozen typed identity."""

    if not isinstance(expected_cost_identity, CostAuthorityIdentityV2):
        raise TypeError("expected cost identity must be typed")
    _verify_parents(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
    )
    root = Path(publication_root)
    content = read_bounded_regular(root / "publication.json", _MAX_PUBLICATION_BYTES)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("cost authority publication is invalid JSON") from error
    if not isinstance(payload, dict) or publication_json_bytes(payload) != content:
        raise ValueError("cost authority publication is not canonical")
    expected = _make_authority(
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_acquisition=archive_acquisition,
        output_root=root,
    )
    if expected.cost_identity != expected_cost_identity or payload != expected.to_dict():
        raise ValueError("cost authority differs from expected publisher-derived identity")
    authority = VerifiedCostAuthorityV2(
        **{
            definition.name: getattr(expected, definition.name)
            for definition in fields(expected)
            if definition.name not in {"publication_root", "canonical_bytes"}
        },
        publication_root=root,
        canonical_bytes=content,
        _factory_token=_FACTORY,
    )
    _register(
        authority,
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=Path(archive_manifest_path),
        archive_acquisition=archive_acquisition,
    )
    return verify_validation_cost_authority_v2(
        authority,
        source_publication=source_publication,
        aggregate_publication=aggregate_publication,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
    )


def _verify_parents(**parents: Any) -> None:
    verify_development_read_boundary_v2(parents["boundary"], parents["coverage"], parents["split"])
    verify_validation_source_publication_v2(
        parents["source_publication"],
        coverage=parents["coverage"],
        split=parents["split"],
        boundary=parents["boundary"],
        availability=parents["availability"],
    )
    verify_validation_aggregate_publication_v2(
        parents["aggregate_publication"],
        minute_publication=parents["source_publication"],
        coverage=parents["coverage"],
        split=parents["split"],
        boundary=parents["boundary"],
        availability=parents["availability"],
    )
    verify_binance_archive_request_manifest_v2(
        parents["archive_manifest"],
        boundary=parents["boundary"],
        source_availability=parents["availability"],
        publication_path=parents["archive_manifest_path"],
    )
    verify_binance_archive_acquisition_v2(
        parents["archive_acquisition"],
        boundary=parents["boundary"],
        manifest=parents["archive_manifest"],
        manifest_path=parents["archive_manifest_path"],
    )


def _make_authority(**parents: Any) -> VerifiedCostAuthorityV2:
    source = parents["source_publication"]
    aggregate = parents["aggregate_publication"]
    boundary = parents["boundary"]
    acquisition = parents["archive_acquisition"]
    archive_available = _status_value(acquisition.status) == "available"
    aggregate_available = _status_value(aggregate.status) == "available"
    reasons = tuple(
        reason
        for condition, reason in (
            (not archive_available, "archive_unavailable"),
            (not aggregate_available, "aggregate_unavailable"),
        )
        if condition
    )
    archives, checksums = _archive_bindings(acquisition) if archive_available else ((), ())
    dimensions = _dimensions(
        available=not reasons,
        source_identity=source.source_publication_identity.value,
        boundary_sha256=boundary.boundary_sha256,
        archives=archives,
        checksums=checksums,
        missing_reason=reasons[0] if reasons else None,
    )
    payload = {
        "coverage_identity": parents["coverage"].coverage_identity.value,
        "split_identity": parents["split"].split_identity.value,
        "boundary_sha256": boundary.boundary_sha256,
        "source_publication_identity": source.source_publication_identity.value,
        "aggregate_identity": aggregate.aggregate_identity.value,
        "source_availability_sha256": parents["availability"].availability_sha256,
        "archive_manifest_sha256": parents["archive_manifest"].manifest_sha256,
        "archive_publication_sha256": acquisition.publication_sha256,
        "archive_audit_publication_sha256": acquisition.audit_publication_sha256,
        "allowed_origin": parents["archive_manifest"].allowed_origin,
        "instrument_kind": "spot",
        "dimensions": dimensions,
        "evaluation_status": CostEvaluationStatusV2.NOT_EVALUATED
        if reasons
        else CostEvaluationStatusV2.COMPLETED,
        "conclusion": CostConclusionV2.INCONCLUSIVE,
        "incomplete_promotion_grade_dimensions": tuple(
            item.dimension.value
            for item in dimensions
            if item.status in {CostEvidenceStatusV2.UNAVAILABLE, CostEvidenceStatusV2.PROXY}
        ),
        "not_evaluated_reasons": reasons,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    identity_payload = {
        **{key: value for key, value in payload.items() if key != "dimensions"},
        "dimensions": [item.to_dict() for item in dimensions],
    }
    identity_payload["evaluation_status"] = payload["evaluation_status"].value
    identity_payload["conclusion"] = payload["conclusion"].value
    identity_payload["incomplete_promotion_grade_dimensions"] = list(
        payload["incomplete_promotion_grade_dimensions"]
    )
    identity_payload["not_evaluated_reasons"] = list(payload["not_evaluated_reasons"])
    identity = CostAuthorityIdentityV2.from_payload(identity_payload)
    public = {
        "schema_version": VerifiedCostAuthorityV2._schema,
        **identity_payload,
        "cost_identity": identity.value,
    }
    return VerifiedCostAuthorityV2(
        **payload,
        cost_identity=identity,
        publication_root=Path(parents["output_root"]),
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_FACTORY,
    )


def _dimensions(
    *,
    available: bool,
    source_identity: str,
    boundary_sha256: str,
    archives: tuple[str, ...],
    checksums: tuple[str, ...],
    missing_reason: str | None,
) -> tuple[CostDimensionEvidenceV2, ...]:
    def unavailable(dimension: CostDimensionV2, reason: str) -> CostDimensionEvidenceV2:
        return CostDimensionEvidenceV2(
            dimension,
            CostEvidenceStatusV2.UNAVAILABLE,
            None,
            None,
            None,
            None,
            (),
            (),
            (),
            source_identity,
            boundary_sha256,
            False,
            reason,
        )

    if not available:
        reason = missing_reason or "required_input_unavailable"
        return tuple(
            CostDimensionEvidenceV2(
                item,
                CostEvidenceStatusV2.NOT_APPLICABLE,
                None,
                None,
                None,
                None,
                (),
                (),
                (),
                source_identity,
                boundary_sha256,
                False,
                "verified_spot_instrument",
            )
            if item is CostDimensionV2.FUNDING
            else unavailable(item, reason)
            for item in CostDimensionV2
        )
    spread = CostDimensionEvidenceV2(
        CostDimensionV2.SPREAD,
        CostEvidenceStatusV2.PROXY,
        None,
        "max(observed_trade_price)-min(observed_trade_price)",
        "quote_asset_per_base_asset",
        "per_complete_utc_aggregate_bar",
        ("not_bid_ask_spread", "no_order_book", "no_quote_timing"),
        archives,
        checksums,
        source_identity,
        boundary_sha256,
        True,
        None,
    )
    slippage = CostDimensionEvidenceV2(
        CostDimensionV2.SLIPPAGE,
        CostEvidenceStatusV2.PROXY,
        None,
        "abs(observed_trade_price-aggregate_close)",
        "quote_asset_per_base_asset",
        "per_complete_utc_aggregate_bar",
        ("not_actual_execution_slippage", "no_order_intent", "no_queue_position"),
        archives,
        checksums,
        source_identity,
        boundary_sha256,
        True,
        None,
    )
    turnover = CostDimensionEvidenceV2(
        CostDimensionV2.TURNOVER,
        CostEvidenceStatusV2.OBSERVED,
        None,
        None,
        "quote_asset",
        "per_verified_development_archive_request",
        ("public_trade_tape_only",),
        archives,
        checksums,
        source_identity,
        boundary_sha256,
        False,
        None,
    )
    reasons = {
        CostDimensionV2.FEE: "no_public_dated_paid_fee_authority",
        CostDimensionV2.LATENCY: "no_actual_execution_latency",
        CostDimensionV2.FILL: "no_actual_fill_probability_or_order_intent",
        CostDimensionV2.MISSED_FILL: "no_actual_missed_fill_records",
        CostDimensionV2.CAPACITY: "observed_turnover_is_not_actual_capacity",
    }
    items: dict[CostDimensionV2, CostDimensionEvidenceV2] = {
        dimension: unavailable(dimension, reason) for dimension, reason in reasons.items()
    }
    items[CostDimensionV2.SPREAD] = spread
    items[CostDimensionV2.SLIPPAGE] = slippage
    items[CostDimensionV2.FUNDING] = CostDimensionEvidenceV2(
        CostDimensionV2.FUNDING,
        CostEvidenceStatusV2.NOT_APPLICABLE,
        None,
        None,
        None,
        None,
        (),
        (),
        (),
        source_identity,
        boundary_sha256,
        False,
        "verified_spot_instrument",
    )
    items[CostDimensionV2.TURNOVER] = turnover
    return tuple(items[item] for item in CostDimensionV2)


def _archive_bindings(
    acquisition: BinanceArchiveAcquisitionResultV2,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        payload = json.loads(acquisition.canonical_bytes)
        objects = payload["objects"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("archive acquisition object bindings are invalid") from error
    if not isinstance(objects, list) or len(objects) != acquisition.object_count or not objects:
        raise ValueError("available archive acquisition has incomplete object bindings")
    pairs: list[tuple[str, str]] = []
    for item in objects:
        if not isinstance(item, dict):
            raise ValueError("archive acquisition object binding is invalid")
        archive = _require_sha256(item.get("local_sha256"), "local archive sha256")
        checksum = _require_sha256(item.get("official_sha256"), "official checksum sha256")
        if archive != checksum:
            raise ValueError("archive local and official checksum bindings disagree")
        pairs.append((archive, checksum))
    pairs.sort()
    return tuple(item[0] for item in pairs), tuple(item[1] for item in pairs)


def _require_verified_spot_manifest(manifest: BinanceArchiveRequestManifestV2) -> None:
    if manifest.allowed_origin != "https://data.binance.vision" or not manifest.requests:
        raise ValueError("cost authority requires verified official spot archive identity")
    if any(
        request.archive_kind not in {"trades", "aggTrades"}
        or not request.object_path.startswith("/data/spot/")
        or request.checksum_path != f"{request.object_path}.CHECKSUM"
        for request in manifest.requests
    ):
        raise ValueError("funding not-applicability requires verified spot archive identity")


def _status_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if raw not in {"available", "unavailable"}:
        raise ValueError("parent publication availability status is invalid")
    return str(raw)


def _register(authority: VerifiedCostAuthorityV2, **parents: Any) -> None:
    identifier = id(authority)

    def cleanup(reference: weakref.ReferenceType[VerifiedCostAuthorityV2]) -> None:
        current = _VERIFIED_COST_AUTHORITIES.get(identifier)
        if current is not None and current.authority is reference:
            _VERIFIED_COST_AUTHORITIES.pop(identifier, None)

    reference = weakref.ref(authority, cleanup)
    _VERIFIED_COST_AUTHORITIES[identifier] = _Registration(
        authority=reference,
        canonical_bytes=authority.canonical_bytes,
        publication_root=authority.publication_root,
        **parents,
    )


def _publish_stage_no_clobber(stage: Path, destination: Path) -> None:
    files = bounded_regular_files(stage, maximum=_MAX_OUTPUT_FILES)
    if set(files) != {"publication.json", "_SUCCESS"}:
        raise ValueError("cost authority stage is incomplete")
    for relative in files:
        descriptor = os.open(
            stage / relative,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RuntimeError("cost authority stage artifact is not regular")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _fsync_directory(stage)
    _rename_no_replace(stage, destination)
    _fsync_directory(destination.parent)


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
        raise FileExistsError(f"refusing existing cost authority publication: {destination}")
    raise OSError(error_number, os.strerror(error_number), destination)


def _write_no_clobber(path: Path, content: bytes) -> None:
    if len(content) > _MAX_PUBLICATION_BYTES:
        raise ValueError("cost authority artifact exceeds hard byte ceiling")
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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_digest_tuple(value: object, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a digest tuple")
    for item in value:
        _require_sha256(item, label)
    if value != tuple(sorted(value)):
        raise ValueError(f"{label} must be deterministically ordered")
    return value


__all__ = [
    "CostConclusionV2",
    "CostDimensionEvidenceV2",
    "CostDimensionV2",
    "CostEvaluationStatusV2",
    "CostEvidenceStatusV2",
    "REQUIRED_COST_DIMENSIONS_V2",
    "VerifiedCostAuthorityV2",
    "load_validation_cost_authority_v2",
    "publish_validation_cost_authority_v2",
    "verified_cost_authority_bytes_v2",
    "verify_validation_cost_authority_metadata_v2",
    "verify_validation_cost_authority_v2",
]
