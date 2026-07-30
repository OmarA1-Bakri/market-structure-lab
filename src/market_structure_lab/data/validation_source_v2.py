"""Fail-closed discovery of development-scoped Phase 5 V2 sources.

The PostgreSQL dump and RR publications are provenance metadata only.  This
module never restores or traverses dump table data and never initializes a
database.  A real source is admitted only when an already-existing immutable
descriptor proves exact predicate-before-read enforcement for the verified
development boundary.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, ClassVar, Self
import weakref

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import (
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
)

_MAX_DESCRIPTOR_BYTES = 1024 * 1024
_MAX_CANDIDATES = 1_000
_AVAILABILITY_FACTORY = object()
_AVAILABILITY_DOMAIN = "phase5-validation-scoped-source-availability-v2"
_AUDIT_RECORD_DOMAIN = "phase5-validation-source-access-audit-record-v2"
_AUDIT_PUBLICATION_DOMAIN = "phase5-validation-source-access-audit-publication-v2"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_VERIFIED_AVAILABILITIES: dict[
    int,
    tuple[
        weakref.ReferenceType[ScopedSourceAvailabilityV2],
        bytes,
        Path,
        tuple[tuple[Path, str], ...],
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
        table_data_toc_sha256=hash_json(
            "phase5-validation-public-candles-toc-v2", matches[0]
        ),
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
        if self.rows_read or self.final_scope_attempts or self.final_rows or self.final_access_records:
            raise ValueError("source discovery cannot read rows or access final scope")
        admitted = tuple(item for item in self.candidates if item.admitted)
        if self.status is ScopedSourceStatusV2.AVAILABLE:
            if len(admitted) != 1 or self.admitted_source_identity != admitted[0].source_identity:
                raise ValueError("available status requires exactly one admitted source")
        elif admitted or self.admitted_source_identity is not None:
            raise ValueError("unavailable status cannot name an admitted source")
        if self.availability_sha256 != hash_json(
            _AVAILABILITY_DOMAIN, self._identity_payload()
        ):
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
            availability_sha256=public["availability_sha256"],  # type: ignore[arg-type]
            canonical_bytes=publication_json_bytes(public),
        )


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResultV2:
    availability: ScopedSourceAvailabilityV2
    publication_root: Path
    audit_ledger_root: Path


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
    if path_exists_no_follow(audit_ledger_root):
        raise FileExistsError(f"refusing stale audit ledger root: {audit_ledger_root}")
    if path_exists_no_follow(output_root):
        raise FileExistsError(f"refusing stale source discovery output: {output_root}")
    audit_ledger_root.mkdir(parents=False)
    records_dir = audit_ledger_root / "records"
    records_dir.mkdir()
    candidates: list[ScopedSourceCandidateV2] = []
    descriptor_bindings: list[tuple[Path, str]] = []
    descriptor_byte_count = 0
    prior: str | None = None
    files = bounded_regular_files(candidate_root, maximum=_MAX_CANDIDATES)
    for relative in files:
        descriptor_path = candidate_root / relative
        request = _metadata_request(boundary, target_identity=f"candidate:{relative}")
        prior = _append_audit_record(
            records_dir,
            sequence=len(descriptor_bindings) * 2 + 1,
            phase="start",
            boundary=boundary,
            request=request,
            allowed=False,
            byte_count=0,
            prior=prior,
        )
        boundary.authorize(request)
        raw = read_bounded_regular(descriptor_path, _MAX_DESCRIPTOR_BYTES)
        descriptor_byte_count += len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        prior = _append_audit_record(
            records_dir,
            sequence=len(descriptor_bindings) * 2 + 2,
            phase="completion",
            boundary=boundary,
            request=request,
            allowed=True,
            byte_count=len(raw),
            prior=prior,
        )
        descriptor_bindings.append((descriptor_path, digest))
        candidates.append(
            _evaluate_candidate(str(descriptor_path), raw, digest, boundary)
        )
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
        "record_count": len(descriptor_bindings) * 2,
        "terminal_record_sha256": prior,
        "rows_admitted": 0,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    audit_publication_sha256 = hash_json(_AUDIT_PUBLICATION_DOMAIN, audit_payload)
    audit_public = {**audit_payload, "audit_publication_sha256": audit_publication_sha256}
    _write_no_clobber(audit_ledger_root / "publication.json", publication_json_bytes(audit_public))
    _fsync_directory(audit_ledger_root)
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
        availability_sha256=availability_sha256,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_AVAILABILITY_FACTORY,
    )
    publication_path = output_root / "publication.json"
    _publish_directory_no_clobber(
        output_root, {"publication.json": availability.canonical_bytes}
    )
    _register_availability(
        availability, publication_path, tuple(descriptor_bindings)
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
    if read_bounded_regular(publication_path, _MAX_DESCRIPTOR_BYTES) != availability.canonical_bytes:
        raise ValueError("availability original publication bytes changed")
    if availability.boundary_sha256 != boundary.boundary_sha256:
        raise ValueError("availability is stale for the development boundary")
    for path, expected_sha in registered[3]:
        if hashlib.sha256(read_bounded_regular(path, _MAX_DESCRIPTOR_BYTES)).hexdigest() != expected_sha:
            raise ValueError("source candidate original bytes changed")
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

    content = verified_scoped_source_availability_bytes_v2(
        availability, boundary=boundary
    )
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
    )
    return verify_scoped_source_availability_v2(
        availability, boundary=boundary, publication_root=root
    )


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
        availability_sha256=values["availability_sha256"],  # type: ignore[arg-type]
        canonical_bytes=publication_json_bytes(values),
        _factory_token=_AVAILABILITY_FACTORY,
    )


def _evaluate_candidate(
    relative: str,
    raw: bytes,
    descriptor_sha256: str,
    boundary: DevelopmentReadBoundaryV2,
) -> ScopedSourceCandidateV2:
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _rejected_candidate(relative, "invalid", descriptor_sha256, "invalid_descriptor")
    if not isinstance(decoded, dict):
        return _rejected_candidate(relative, "invalid", descriptor_sha256, "invalid_descriptor")
    kind = str(decoded.get("source_kind", "invalid"))
    identity = str(decoded.get("source_identity", f"invalid:{descriptor_sha256}"))
    if kind in {"postgres-restored-whole-table", "pg_restore", "generic-whole-table"}:
        reason = "whole_table_source_forbidden"
    elif decoded.get("read_only") is not True:
        reason = "source_not_proven_read_only"
    elif decoded.get("predicate_enforcement") not in {
        "partition-scope-before-open",
        "server-side-before-query",
    }:
        reason = "predicate_before_read_not_proven"
    elif decoded.get("boundary_sha256") != boundary.boundary_sha256:
        reason = "boundary_identity_mismatch"
    elif kind not in {
        "content-addressed-development-publication",
        "predicate-enforcing-read-only-service",
        "predicate-enforcing-read-only-view",
    }:
        reason = "source_kind_not_admissible"
    elif not _independent_verification_matches(decoded):
        reason = "independent_verification_missing_or_invalid"
    else:
        reason = None
    return ScopedSourceCandidateV2(
            descriptor_path=relative,
        source_kind=kind,
        source_identity=identity,
        descriptor_sha256=descriptor_sha256,
        admitted=reason is None,
        rejection_reason=reason,
    )


def _independent_verification_matches(decoded: dict[str, object]) -> bool:
    verification = decoded.get("verification")
    if not isinstance(verification, dict):
        return False
    return (
        verification.get("immutable") is True
        and verification.get("predicate_verified_before_read") is True
        and isinstance(verification.get("verifier_identity"), str)
        and bool(verification["verifier_identity"])
        and isinstance(verification.get("original_manifest_sha256"), str)
        and _SHA256.fullmatch(str(verification["original_manifest_sha256"])) is not None
    )


def _rejected_candidate(
    path: str, kind: str, digest: str, reason: str
) -> ScopedSourceCandidateV2:
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
    _write_no_clobber(
        records_dir / f"{sequence:08d}-{digest}.json", publication_json_bytes(public)
    )
    return digest


def _register_availability(
    availability: ScopedSourceAvailabilityV2,
    publication_path: Path,
    descriptor_bindings: tuple[tuple[Path, str], ...],
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
    )


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
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_directory_no_clobber(root: Path, files: dict[str, bytes]) -> None:
    if path_exists_no_follow(root):
        raise FileExistsError(f"refusing stale or concurrent publication: {root}")
    require_regular_directory(root.parent)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.", suffix=".tmp", dir=root.parent)
    )
    try:
        for relative, content in files.items():
            _write_no_clobber(temporary / relative, content)
        _fsync_directory(temporary)
        os.rename(temporary, root)
        _fsync_directory(root.parent)
    finally:
        if temporary.exists():
            for relative in files:
                (temporary / relative).unlink(missing_ok=True)
            temporary.rmdir()


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
    "DumpTocMetadataV2",
    "ScopedSourceAvailabilityV2",
    "ScopedSourceCandidateV2",
    "ScopedSourceStatusV2",
    "discover_scoped_source_v2",
    "load_scoped_source_availability_v2",
    "load_v2_boundary_publications",
    "reject_pg_restore_row_source_v2",
    "verify_dump_toc_metadata_v2",
    "verify_scoped_source_availability_v2",
    "verified_scoped_source_availability_bytes_v2",
    "verified_scoped_source_availability_binding_v2",
]
