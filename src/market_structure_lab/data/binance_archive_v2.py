"""Offline request freezing and bounded acquisition for official Binance archives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import time
from typing import Any, ClassVar, Self
import urllib.error
import urllib.parse
import urllib.request
import weakref
import zipfile

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceAvailabilityV2,
    verified_scoped_source_availability_binding_v2,
    verified_scoped_source_availability_bytes_v2,
)
from market_structure_lab.research.validation_v2_models import publication_json_bytes
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentReadBoundaryV2,
)

_MANIFEST_FACTORY = object()
_ACQUISITION_FACTORY = object()
_MANIFEST_DOMAIN = "phase5-binance-archive-request-manifest-v2"
_REQUEST_DOMAIN = "phase5-binance-archive-request-v2"
_ACQUISITION_DOMAIN = "phase5-binance-archive-acquisition-publication-v2"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_PUBLICATION_BYTES = 64 * 1024 * 1024
_UNSUPPORTED_TAPE_AUTHORITIES = (
    "fees",
    "spread",
    "slippage",
    "funding",
    "latency",
    "fill_probability",
    "missed_fills",
    "capacity",
    "historical_exchange_info",
)
_VERIFIED_MANIFESTS: dict[
    int,
    tuple[
        weakref.ReferenceType[BinanceArchiveRequestManifestV2],
        bytes,
        Path,
        bytes,
    ],
] = {}
_VERIFIED_ACQUISITIONS: dict[
    int,
    tuple[
        weakref.ReferenceType[BinanceArchiveAcquisitionResultV2],
        bytes,
        Path,
        tuple[tuple[Path, str], ...],
        bytes,
        Path,
        bytes,
    ],
] = {}


class ArchiveAcquisitionExpectedError(RuntimeError):
    """Expected, receipt-worthy acquisition failure."""


class ArchiveBudgetExceeded(ArchiveAcquisitionExpectedError):
    """A frozen work ceiling was reached."""


class ArchiveIntegrityError(ArchiveAcquisitionExpectedError):
    """Official checksum/archive content failed verification."""


class ArchiveNetworkUnavailable(ArchiveAcquisitionExpectedError):
    """The bounded official HTTPS request could not complete."""


@dataclass(frozen=True, slots=True)
class ArchiveBudgetsV2:
    max_requests: int
    max_compressed_object_bytes: int
    max_total_compressed_bytes: int
    max_decompressed_object_bytes: int
    max_total_decompressed_bytes: int
    max_files: int
    max_disk_bytes: int
    max_rows: int
    chunk_bytes: int
    max_runtime_seconds: int
    max_concurrency: int
    retry_ceiling: int

    def __post_init__(self) -> None:
        for label in self.__dataclass_fields__:
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if self.chunk_bytes > self.max_compressed_object_bytes:
            raise ValueError("stream chunk cannot exceed the compressed object ceiling")
        if self.max_concurrency > self.max_requests:
            raise ValueError("concurrency cannot exceed the request ceiling")
        if self.retry_ceiling > self.max_requests:
            raise ValueError("retry ceiling cannot exceed the request ceiling")

    @classmethod
    def testing(cls, *, max_requests: int = 20_000) -> Self:
        return cls(
            max_requests=max_requests,
            max_compressed_object_bytes=8 * 1024 * 1024,
            max_total_compressed_bytes=256 * 1024 * 1024,
            max_decompressed_object_bytes=64 * 1024 * 1024,
            max_total_decompressed_bytes=1024 * 1024 * 1024,
            max_files=20_000,
            max_disk_bytes=2 * 1024 * 1024 * 1024,
            max_rows=10_000_000,
            chunk_bytes=64 * 1024,
            max_runtime_seconds=600,
            max_concurrency=1,
            retry_ceiling=2,
        )

    def to_dict(self) -> dict[str, int]:
        return {label: getattr(self, label) for label in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("archive budgets have unexpected or missing fields")
        return cls(**payload)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class BinanceArchiveRequestV2:
    symbol: str
    archive_kind: str
    period: str
    start: datetime
    end: datetime
    object_path: str
    checksum_path: str
    request_sha256: str

    def __post_init__(self) -> None:
        if self.archive_kind not in {"trades", "aggTrades"}:
            raise ValueError("archive_kind must be trades or aggTrades")
        if self.period not in {"daily", "monthly"}:
            raise ValueError("archive period must be daily or monthly")
        if self.start.tzinfo is None or self.start.utcoffset() != timedelta(0):
            raise ValueError("request start must be UTC-aware")
        if self.end <= self.start:
            raise ValueError("archive request interval must be positive")
        if self.checksum_path != f"{self.object_path}.CHECKSUM":
            raise ValueError("checksum sidecar must be adjacent to archive object")
        if self.request_sha256 != hash_json(_REQUEST_DOMAIN, self._payload()):
            raise ValueError("archive request identity differs from its payload")

    def _payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "archive_kind": self.archive_kind,
            "period": self.period,
            "start": _utc_text(self.start),
            "end": _utc_text(self.end),
            "object_path": self.object_path,
            "checksum_path": self.checksum_path,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "request_sha256": self.request_sha256}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BinanceArchiveRequestManifestV2:
    boundary_sha256: str
    source_availability_sha256: str
    allowed_origin: str
    redirect_policy: str
    max_redirects: int
    tls_min_version: str
    require_ca_validation: bool
    require_binance_sha256_sidecar: bool
    require_local_sha256: bool
    budgets: ArchiveBudgetsV2
    requests: tuple[BinanceArchiveRequestV2, ...]
    unsupported_authorities: tuple[str, ...]
    manifest_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None
    _schema: ClassVar[str] = "phase5-binance-archive-request-manifest-v2"

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MANIFEST_FACTORY:
            raise TypeError("BinanceArchiveRequestManifestV2 requires its factory")
        _require_sha256(self.boundary_sha256, "boundary_sha256")
        _require_sha256(self.source_availability_sha256, "source_availability_sha256")
        _validate_policy(
            self.allowed_origin,
            self.redirect_policy,
            self.max_redirects,
            self.tls_min_version,
            self.require_ca_validation,
            self.require_binance_sha256_sidecar,
            self.require_local_sha256,
        )
        if len(self.requests) * 2 > self.budgets.max_requests:
            raise ValueError("archive plus checksum requests exceed max_requests")
        if len(self.requests) > self.budgets.max_files:
            raise ValueError("archive request count exceeds max_files")
        if self.unsupported_authorities != _UNSUPPORTED_TAPE_AUTHORITIES:
            raise ValueError("trade tape authority exclusions are incomplete")
        if self.manifest_sha256 != hash_json(_MANIFEST_DOMAIN, self._identity_payload()):
            raise ValueError("archive request manifest identity differs from payload")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("archive request manifest bytes are not canonical")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "boundary_sha256": self.boundary_sha256,
            "source_availability_sha256": self.source_availability_sha256,
            "allowed_origin": self.allowed_origin,
            "redirect_policy": self.redirect_policy,
            "max_redirects": self.max_redirects,
            "tls_min_version": self.tls_min_version,
            "require_ca_validation": self.require_ca_validation,
            "require_binance_sha256_sidecar": self.require_binance_sha256_sidecar,
            "require_local_sha256": self.require_local_sha256,
            "budgets": self.budgets.to_dict(),
            "requests": [item.to_dict() for item in self.requests],
            "unsupported_authorities": list(self.unsupported_authorities),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            **self._identity_payload(),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class ObservedTradeV2:
    price: str
    quantity: str
    timestamp_ms: int
    turnover: str
    unsupported_authorities: ClassVar[tuple[str, ...]] = _UNSUPPORTED_TAPE_AUTHORITIES


def parse_observed_trade_row_v2(
    row: tuple[str, ...], *, archive_kind: str
) -> ObservedTradeV2:
    """Parse only observable tape fields; never synthesize execution authority."""

    if archive_kind not in {"trades", "aggTrades"}:
        raise ValueError("archive_kind must be trades or aggTrades")
    minimum = 5 if archive_kind == "trades" else 6
    if len(row) < minimum:
        raise ValueError("observed trade archive row is incomplete")
    try:
        price = Decimal(row[1])
        quantity = Decimal(row[2])
        if price < 0 or quantity < 0:
            raise ValueError("price and quantity must be non-negative")
        if archive_kind == "trades":
            turnover = Decimal(row[3])
            timestamp = int(row[4])
        else:
            turnover = price * quantity
            timestamp = int(row[5])
    except (InvalidOperation, ValueError) as error:
        raise ValueError("observed trade archive row is invalid") from error
    if timestamp < 0:
        raise ValueError("observed trade timestamp must be non-negative")
    if timestamp >= 100_000_000_000_000:
        if timestamp % 1_000:
            raise ValueError("observed microsecond timestamp is not millisecond aligned")
        timestamp //= 1_000
    return ObservedTradeV2(
        price=_decimal_text(price),
        quantity=_decimal_text(quantity),
        timestamp_ms=timestamp,
        turnover=_decimal_text(turnover),
    )


def freeze_binance_archive_requests_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    source_availability: ScopedSourceAvailabilityV2,
    budgets: ArchiveBudgetsV2,
    output: Path,
    allowed_origin: str = "https://data.binance.vision",
    redirect_policy: str = "same-origin-only",
    max_redirects: int = 1,
    tls_min_version: str = "TLSv1.2",
    require_ca_validation: bool = True,
    require_binance_sha256_sidecar: bool = True,
    require_local_sha256: bool = True,
) -> BinanceArchiveRequestManifestV2:
    """Freeze all request paths offline after original authority verification."""

    source_bytes, _ = verified_scoped_source_availability_binding_v2(
        source_availability, boundary=boundary
    )
    _verify_boundary_original(boundary)
    _validate_policy(
        allowed_origin,
        redirect_policy,
        max_redirects,
        tls_min_version,
        require_ca_validation,
        require_binance_sha256_sidecar,
        require_local_sha256,
    )
    if not isinstance(budgets, ArchiveBudgetsV2):
        raise TypeError("budgets must be ArchiveBudgetsV2")
    requests = tuple(
        _make_request(symbol, period, start, end)
        for symbol in boundary.allowed_symbols
        for period, start, end in _bounded_periods(boundary)
    )
    for request in requests:
        boundary.authorize(
            BoundaryRequestV2(
                symbol=request.symbol,
                timeframe="1m",
                start=request.start,
                end=request.end,
                operation_kind=AccessOperationKindV2.NETWORK,
                target_identity=request.request_sha256,
            )
        )
        _validate_archive_path(request.object_path)
        _validate_archive_path(request.checksum_path)
    payload = {
        "boundary_sha256": boundary.boundary_sha256,
        "source_availability_sha256": source_availability.availability_sha256,
        "allowed_origin": allowed_origin,
        "redirect_policy": redirect_policy,
        "max_redirects": max_redirects,
        "tls_min_version": tls_min_version,
        "require_ca_validation": require_ca_validation,
        "require_binance_sha256_sidecar": require_binance_sha256_sidecar,
        "require_local_sha256": require_local_sha256,
        "budgets": budgets.to_dict(),
        "requests": [item.to_dict() for item in requests],
        "unsupported_authorities": list(_UNSUPPORTED_TAPE_AUTHORITIES),
    }
    digest = hash_json(_MANIFEST_DOMAIN, payload)
    public = {
        "schema_version": BinanceArchiveRequestManifestV2._schema,
        **payload,
        "manifest_sha256": digest,
    }
    manifest = BinanceArchiveRequestManifestV2(
        boundary_sha256=boundary.boundary_sha256,
        source_availability_sha256=source_availability.availability_sha256,
        allowed_origin=allowed_origin,
        redirect_policy=redirect_policy,
        max_redirects=max_redirects,
        tls_min_version=tls_min_version,
        require_ca_validation=require_ca_validation,
        require_binance_sha256_sidecar=require_binance_sha256_sidecar,
        require_local_sha256=require_local_sha256,
        budgets=budgets,
        requests=requests,
        unsupported_authorities=_UNSUPPORTED_TAPE_AUTHORITIES,
        manifest_sha256=digest,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=_MANIFEST_FACTORY,
    )
    output = Path(output)
    _write_no_clobber(output, manifest.canonical_bytes)
    _register_manifest(manifest, output, source_bytes)
    return manifest


def verify_binance_archive_request_manifest_v2(
    manifest: BinanceArchiveRequestManifestV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    source_availability: ScopedSourceAvailabilityV2,
    publication_path: Path,
) -> BinanceArchiveRequestManifestV2:
    _verify_boundary_original(boundary)
    source_bytes = verified_scoped_source_availability_bytes_v2(
        source_availability, boundary=boundary
    )
    registered = _VERIFIED_MANIFESTS.get(id(manifest))
    publication_path = Path(publication_path)
    if (
        registered is None
        or registered[0]() is not manifest
        or registered[1] != manifest.canonical_bytes
        or registered[2] != publication_path
        or registered[3] != source_bytes
    ):
        raise ValueError("archive manifest is not an exact verified original publication")
    if read_bounded_regular(publication_path, _MAX_PUBLICATION_BYTES) != manifest.canonical_bytes:
        raise ValueError("archive manifest original publication bytes changed")
    if manifest.boundary_sha256 != boundary.boundary_sha256:
        raise ValueError("archive manifest is stale for the boundary")
    for request in manifest.requests:
        boundary.authorize(
            BoundaryRequestV2(
                symbol=request.symbol,
                timeframe="1m",
                start=request.start,
                end=request.end,
                operation_kind=AccessOperationKindV2.NETWORK,
                target_identity=request.request_sha256,
            )
        )
    return manifest


def load_binance_archive_request_manifest_v2(
    *,
    publication_path: Path,
    boundary: DevelopmentReadBoundaryV2,
    source_availability: ScopedSourceAvailabilityV2,
) -> BinanceArchiveRequestManifestV2:
    raw = read_bounded_regular(Path(publication_path), _MAX_PUBLICATION_BYTES)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("archive manifest publication is invalid") from error
    if not isinstance(payload, dict) or publication_json_bytes(payload) != raw:
        raise ValueError("archive manifest publication is not canonical")
    manifest = _manifest_from_dict(payload)
    source_bytes = verified_scoped_source_availability_bytes_v2(
        source_availability, boundary=boundary
    )
    _register_manifest(manifest, Path(publication_path), source_bytes)
    return verify_binance_archive_request_manifest_v2(
        manifest,
        boundary=boundary,
        source_availability=source_availability,
        publication_path=publication_path,
    )


def load_binance_archive_request_manifest_for_acquisition_v2(
    *,
    publication_path: Path,
    boundary: DevelopmentReadBoundaryV2,
) -> BinanceArchiveRequestManifestV2:
    """Reopen the immutable request bytes without reconstructing source authority."""

    raw = read_bounded_regular(Path(publication_path), _MAX_PUBLICATION_BYTES)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("archive manifest publication is invalid") from error
    if not isinstance(payload, dict) or publication_json_bytes(payload) != raw:
        raise ValueError("archive manifest publication is not canonical")
    manifest = _manifest_from_dict(payload)
    _register_manifest(manifest, Path(publication_path), b"")
    _verify_manifest_original(manifest, boundary=boundary, publication_path=publication_path)
    return manifest


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BinanceArchiveAcquisitionResultV2:
    status: str
    manifest_sha256: str
    object_count: int
    compressed_bytes: int
    decompressed_bytes: int
    row_count: int
    final_scope_attempts: int
    final_rows: int
    unsupported_authorities: tuple[str, ...]
    publication_sha256: str
    publication_path: Path
    audit_publication_sha256: str
    audit_publication_path: Path
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _ACQUISITION_FACTORY:
            raise TypeError("BinanceArchiveAcquisitionResultV2 requires its factory")
        if self.status not in {"available", "unavailable"}:
            raise ValueError("acquisition status is invalid")
        _require_sha256(self.manifest_sha256, "manifest_sha256")
        _require_sha256(self.publication_sha256, "publication_sha256")
        _require_sha256(self.audit_publication_sha256, "audit_publication_sha256")
        if not self.audit_publication_path.is_absolute():
            raise ValueError("acquisition audit publication path must be absolute")
        if self.final_scope_attempts or self.final_rows:
            raise ValueError("archive acquisition cannot contain final access")
        if self.unsupported_authorities != _UNSUPPORTED_TAPE_AUTHORITIES:
            raise ValueError("archive acquisition authority exclusions are incomplete")


def acquire_binance_archives_v2(
    *,
    boundary: DevelopmentReadBoundaryV2,
    manifest: BinanceArchiveRequestManifestV2,
    manifest_path: Path,
    audit_ledger_root: Path,
    cache_root: Path,
    output_root: Path,
) -> BinanceArchiveAcquisitionResultV2:
    """Acquire exactly the preregistered objects using private bounded adapters."""

    _verify_manifest_original(manifest, boundary=boundary, publication_path=manifest_path)
    audit_ledger_root = Path(audit_ledger_root)
    output_root = Path(output_root)
    cache_root = Path(cache_root)
    for path in (audit_ledger_root, output_root):
        if path_exists_no_follow(path):
            raise FileExistsError(f"refusing stale acquisition output: {path}")
        require_regular_directory(path.parent)
    require_regular_directory(cache_root)
    audit_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{audit_ledger_root.name}.",
            suffix=".tmp",
            dir=audit_ledger_root.parent,
        )
    )
    output_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.", suffix=".tmp", dir=output_root.parent
        )
    )
    records_dir = audit_stage / "records"
    records_dir.mkdir()
    deadline = time.monotonic() + manifest.budgets.max_runtime_seconds
    initial_disk_bytes = _disk_usage_unique(cache_root, maximum=manifest.budgets.max_files * 4)
    if initial_disk_bytes > manifest.budgets.max_disk_bytes:
        _remove_staged_tree(audit_stage)
        _remove_staged_tree(output_stage)
        raise ArchiveBudgetExceeded("existing cache exceeds frozen disk ceiling")
    totals = {
        "requests": 0,
        "compressed": 0,
        "decompressed": 0,
        "files": 0,
        "rows": 0,
    }
    objects: list[dict[str, object]] = []
    audit_sequence = 0
    audit_prior: str | None = None
    status = "available"
    failure: str | None = None

    def enforce_actual_disk() -> None:
        actual = _acquisition_disk_usage(
            cache_root,
            audit_stage,
            output_stage,
            maximum=manifest.budgets.max_files * 8,
        )
        if actual > manifest.budgets.max_disk_bytes:
            raise ArchiveBudgetExceeded(
                "acquisition cache, audit, and publication exceed disk ceiling"
            )

    try:
        for request in manifest.requests:
            _enforce_deadline(deadline)
            boundary_request = BoundaryRequestV2(
                symbol=request.symbol,
                timeframe="1m",
                start=request.start,
                end=request.end,
                operation_kind=AccessOperationKindV2.NETWORK,
                target_identity=request.request_sha256,
            )
            boundary.authorize(boundary_request)
            checksum_url = _exact_url(manifest.allowed_origin, request.checksum_path)
            object_url = _exact_url(manifest.allowed_origin, request.object_path)

            def record_network(phase: str, target: str, byte_count: int) -> None:
                nonlocal audit_sequence, audit_prior
                audit_sequence += 1
                audit_prior = _append_archive_audit(
                    records_dir,
                    sequence=audit_sequence,
                    phase=phase,
                    boundary_sha256=boundary.boundary_sha256,
                    boundary_request=boundary_request,
                    target=target,
                    byte_count=byte_count,
                    prior=audit_prior,
                    deadline=deadline,
                    disk_guard=enforce_actual_disk,
                )

            checksum_bytes = _download_small_with_retries(
                checksum_url,
                manifest,
                totals,
                maximum=8192,
                deadline=deadline,
                audit=lambda phase, count: record_network(
                    phase, request.checksum_path, count
                ),
            )
            try:
                expected = _parse_checksum(
                    checksum_bytes, Path(request.object_path).name
                )
            except ValueError as error:
                raise ArchiveIntegrityError(str(error)) from error
            archive_tmp = _download_archive_to_temporary(
                object_url,
                manifest,
                totals,
                cache_root,
                deadline=deadline,
                remaining_disk_bytes=(
                    manifest.budgets.max_disk_bytes
                    - _disk_usage_unique(
                        cache_root, maximum=manifest.budgets.max_files * 4
                    )
                    - _disk_usage_unique(
                        audit_stage, maximum=manifest.budgets.max_files * 4
                    )
                    - _disk_usage_unique(
                        output_stage, maximum=manifest.budgets.max_files * 4
                    )
                ),
                audit=lambda phase, count: record_network(
                    phase, request.object_path, count
                ),
            )
            try:
                actual = _sha256_path(archive_tmp, deadline=deadline)
                if actual != expected:
                    raise ArchiveIntegrityError(
                        "Binance checksum sidecar disagrees with local SHA-256"
                    )
                decompressed, rows = _inspect_archive(
                    archive_tmp,
                    request,
                    manifest.budgets,
                    remaining_decompressed=(
                        manifest.budgets.max_total_decompressed_bytes
                        - totals["decompressed"]
                    ),
                    remaining_rows=manifest.budgets.max_rows - totals["rows"],
                    deadline=deadline,
                )
                totals["decompressed"] += decompressed
                totals["rows"] += rows
                totals["files"] += 1
                _enforce_totals(totals, manifest.budgets)
                if (
                    _disk_usage_unique(
                        cache_root, maximum=manifest.budgets.max_files * 4
                    )
                    + _allocated_bytes(archive_tmp)
                    > manifest.budgets.max_disk_bytes
                ):
                    raise ArchiveBudgetExceeded(
                        "temporary archive would exceed actual disk ceiling"
                    )
                object_path = _publish_content_addressed(
                    archive_tmp, cache_root, actual, deadline=deadline
                )
                _enforce_deadline(deadline)
                if (
                    _disk_usage_unique(
                        cache_root, maximum=manifest.budgets.max_files * 4
                    )
                    > manifest.budgets.max_disk_bytes
                ):
                    raise ArchiveBudgetExceeded(
                        "content-addressed cache exceeds frozen disk ceiling"
                    )
                objects.append(
                    {
                        "request_sha256": request.request_sha256,
                        "official_sha256": expected,
                        "local_sha256": actual,
                        "cache_object": object_path.relative_to(cache_root).as_posix(),
                        "compressed_bytes": object_path.stat().st_size,
                        "decompressed_bytes": decompressed,
                        "row_count": rows,
                    }
                )
            finally:
                archive_tmp.unlink(missing_ok=True)
    except ArchiveAcquisitionExpectedError as error:
        status = "unavailable"
        failure = f"{type(error).__name__}:{error}"
    except Exception:
        _remove_staged_tree(audit_stage)
        _remove_staged_tree(output_stage)
        raise
    audit_payload = {
        "schema_version": "phase5-binance-archive-access-audit-publication-v2",
        "boundary_sha256": boundary.boundary_sha256,
        "request_count": totals["requests"],
        "record_count": audit_sequence,
        "terminal_record_sha256": audit_prior,
        "final_scope_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    audit_digest = hash_json("phase5-binance-archive-access-audit-v2", audit_payload)
    audit_publication = {
        **audit_payload,
        "audit_publication_sha256": audit_digest,
    }
    audit_bytes = publication_json_bytes(audit_publication)
    try:
        _write_no_clobber(
            audit_stage / "publication.json", audit_bytes, deadline=deadline
        )
        enforce_actual_disk()
    except Exception:
        _remove_staged_tree(audit_stage)
        _remove_staged_tree(output_stage)
        raise
    payload = {
        "schema_version": "phase5-binance-archive-acquisition-publication-v2",
        "status": status,
        "manifest_sha256": manifest.manifest_sha256,
        "objects": objects,
        "failure": failure,
        "request_count": totals["requests"],
        "compressed_bytes": totals["compressed"],
        "decompressed_bytes": totals["decompressed"],
        "row_count": totals["rows"],
        "final_scope_attempts": 0,
        "final_rows": 0,
        "unsupported_authorities": list(_UNSUPPORTED_TAPE_AUTHORITIES),
        "audit_publication_sha256": audit_digest,
        "audit_publication_path": str(audit_ledger_root / "publication.json"),
    }
    digest = hash_json(_ACQUISITION_DOMAIN, payload)
    publication = {**payload, "publication_sha256": digest}
    publication_path = output_root / "publication.json"
    canonical_bytes = publication_json_bytes(publication)
    try:
        _write_no_clobber(
            output_stage / "publication.json", canonical_bytes, deadline=deadline
        )
        enforce_actual_disk()
        _commit_paired_directories(
            first_stage=output_stage,
            first_destination=output_root,
            second_stage=audit_stage,
            second_destination=audit_ledger_root,
        )
    except Exception:
        _remove_staged_tree(audit_stage)
        _remove_staged_tree(output_stage)
        raise
    result = BinanceArchiveAcquisitionResultV2(
        status=status,
        manifest_sha256=manifest.manifest_sha256,
        object_count=len(objects),
        compressed_bytes=totals["compressed"],
        decompressed_bytes=totals["decompressed"],
        row_count=totals["rows"],
        final_scope_attempts=0,
        final_rows=0,
        unsupported_authorities=_UNSUPPORTED_TAPE_AUTHORITIES,
        publication_sha256=digest,
        publication_path=publication_path,
        audit_publication_sha256=audit_digest,
        audit_publication_path=audit_ledger_root / "publication.json",
        canonical_bytes=canonical_bytes,
        _factory_token=_ACQUISITION_FACTORY,
    )
    _register_acquisition(
        result,
        manifest,
        tuple(
            (
                Path(cache_root) / str(item["cache_object"]),
                str(item["local_sha256"]),
            )
            for item in objects
        ),
        audit_bytes,
    )
    return result


def verify_binance_archive_acquisition_v2(
    result: BinanceArchiveAcquisitionResultV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    manifest: BinanceArchiveRequestManifestV2,
    manifest_path: Path,
) -> BinanceArchiveAcquisitionResultV2:
    """Revalidate original acquisition bytes, manifest bytes, and cached objects."""

    _verify_manifest_original(
        manifest, boundary=boundary, publication_path=manifest_path
    )
    registered = _VERIFIED_ACQUISITIONS.get(id(result))
    if (
        registered is None
        or registered[0]() is not result
        or registered[1] != result.canonical_bytes
        or registered[2] != result.publication_path
        or registered[4] != manifest.canonical_bytes
        or registered[5] != result.audit_publication_path
    ):
        raise ValueError("acquisition is not an exact verified original publication")
    if read_bounded_regular(result.publication_path, _MAX_PUBLICATION_BYTES) != result.canonical_bytes:
        raise ValueError("acquisition original publication bytes changed")
    for path, expected_sha256 in registered[3]:
        if _sha256_path(path) != expected_sha256:
            raise ValueError("acquisition cached object bytes changed")
    audit_bytes = _verify_archive_audit_publication(
        result.audit_publication_path,
        expected_boundary_sha256=boundary.boundary_sha256,
    )
    if audit_bytes != registered[6]:
        raise ValueError("acquisition original audit bytes changed")
    audit_public = json.loads(audit_bytes)
    if audit_public["audit_publication_sha256"] != result.audit_publication_sha256:
        raise ValueError("acquisition does not bind its original audit digest")
    if result.status == "available" and result.object_count != len(manifest.requests):
        raise ValueError("available acquisition does not cover every frozen request")
    return result


def load_binance_archive_acquisition_v2(
    *,
    publication_root: Path,
    cache_root: Path,
    boundary: DevelopmentReadBoundaryV2,
    manifest: BinanceArchiveRequestManifestV2,
    manifest_path: Path,
) -> BinanceArchiveAcquisitionResultV2:
    """Reopen and revalidate original acquisition, audit, and cache bytes."""

    publication_path = Path(publication_root) / "publication.json"
    content = read_bounded_regular(publication_path, _MAX_PUBLICATION_BYTES)
    try:
        public = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("archive acquisition publication is invalid JSON") from error
    expected_fields = {
        "schema_version",
        "status",
        "manifest_sha256",
        "objects",
        "failure",
        "request_count",
        "compressed_bytes",
        "decompressed_bytes",
        "row_count",
        "final_scope_attempts",
        "final_rows",
        "unsupported_authorities",
        "audit_publication_sha256",
        "audit_publication_path",
        "publication_sha256",
    }
    if (
        not isinstance(public, dict)
        or set(public) != expected_fields
        or public["schema_version"]
        != "phase5-binance-archive-acquisition-publication-v2"
        or publication_json_bytes(public) != content
    ):
        raise ValueError("archive acquisition publication schema is invalid")
    identity_payload = {
        key: value for key, value in public.items() if key != "publication_sha256"
    }
    if public["publication_sha256"] != hash_json(
        _ACQUISITION_DOMAIN, identity_payload
    ):
        raise ValueError("archive acquisition publication digest is invalid")
    raw_objects = public["objects"]
    if not isinstance(raw_objects, list):
        raise ValueError("archive acquisition objects must be a list")
    object_bindings: list[tuple[Path, str]] = []
    for item in raw_objects:
        if not isinstance(item, dict) or set(item) != {
            "request_sha256",
            "official_sha256",
            "local_sha256",
            "cache_object",
            "compressed_bytes",
            "decompressed_bytes",
            "row_count",
        }:
            raise ValueError("archive acquisition object schema is invalid")
        relative = Path(item["cache_object"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != item["cache_object"]
        ):
            raise ValueError("archive cache object path is invalid")
        local_sha256 = _require_sha256(item["local_sha256"], "local_sha256")
        if item["official_sha256"] != local_sha256:
            raise ValueError("archive object official/local checksum differs")
        object_bindings.append((Path(cache_root) / relative, local_sha256))
    audit_path = Path(public["audit_publication_path"])
    audit_bytes = _verify_archive_audit_publication(
        audit_path, expected_boundary_sha256=boundary.boundary_sha256
    )
    result = BinanceArchiveAcquisitionResultV2(
        status=public["status"],
        manifest_sha256=public["manifest_sha256"],
        object_count=len(raw_objects),
        compressed_bytes=public["compressed_bytes"],
        decompressed_bytes=public["decompressed_bytes"],
        row_count=public["row_count"],
        final_scope_attempts=public["final_scope_attempts"],
        final_rows=public["final_rows"],
        unsupported_authorities=tuple(public["unsupported_authorities"]),
        publication_sha256=public["publication_sha256"],
        publication_path=publication_path,
        audit_publication_sha256=public["audit_publication_sha256"],
        audit_publication_path=audit_path,
        canonical_bytes=content,
        _factory_token=_ACQUISITION_FACTORY,
    )
    _register_acquisition(
        result,
        manifest,
        tuple(object_bindings),
        audit_bytes,
    )
    return verify_binance_archive_acquisition_v2(
        result,
        boundary=boundary,
        manifest=manifest,
        manifest_path=manifest_path,
    )


def _bounded_periods(
    boundary: DevelopmentReadBoundaryV2,
) -> tuple[tuple[str, datetime, datetime], ...]:
    periods: list[tuple[str, datetime, datetime]] = []
    for interval in boundary.allowed_intervals:
        cursor = interval.start
        while cursor < interval.end:
            next_month = _next_month(cursor)
            if cursor.day == 1 and next_month <= interval.end:
                periods.append(("monthly", cursor, next_month))
                cursor = next_month
            else:
                next_day = min(cursor + timedelta(days=1), interval.end)
                periods.append(("daily", cursor, next_day))
                cursor = next_day
    return tuple(periods)


def _make_request(
    symbol: str, period: str, start: datetime, end: datetime
) -> BinanceArchiveRequestV2:
    stamp = start.strftime("%Y-%m" if period == "monthly" else "%Y-%m-%d")
    filename = f"{symbol}-aggTrades-{stamp}.zip"
    object_path = f"/data/spot/{period}/aggTrades/{symbol}/{filename}"
    payload = {
        "symbol": symbol,
        "archive_kind": "aggTrades",
        "period": period,
        "start": _utc_text(start),
        "end": _utc_text(end),
        "object_path": object_path,
        "checksum_path": f"{object_path}.CHECKSUM",
    }
    return BinanceArchiveRequestV2(
        symbol=symbol,
        archive_kind="aggTrades",
        period=period,
        start=start,
        end=end,
        object_path=object_path,
        checksum_path=f"{object_path}.CHECKSUM",
        request_sha256=hash_json(_REQUEST_DOMAIN, payload),
    )


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=UTC)
    return datetime(value.year, value.month + 1, 1, tzinfo=UTC)


def _validate_policy(
    allowed_origin: str,
    redirect_policy: str,
    max_redirects: int,
    tls_min_version: str,
    require_ca_validation: bool,
    require_binance_sha256_sidecar: bool,
    require_local_sha256: bool,
) -> None:
    parsed = urllib.parse.urlsplit(allowed_origin)
    if (
        allowed_origin != "https://data.binance.vision"
        or parsed.scheme != "https"
        or parsed.hostname != "data.binance.vision"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("allowed origin must be exact https://data.binance.vision")
    if redirect_policy != "same-origin-only" or max_redirects != 1:
        raise ValueError("redirect policy must be one bounded same-origin redirect")
    if tls_min_version != "TLSv1.2":
        raise ValueError("TLS minimum must be TLSv1.2")
    if not all(
        (require_ca_validation, require_binance_sha256_sidecar, require_local_sha256)
    ):
        raise ValueError("CA, official checksum, and local checksum verification are required")


def _validate_archive_path(path: str) -> None:
    parsed = urllib.parse.urlsplit(path)
    if (
        not path.startswith("/data/spot/")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or ".." in Path(parsed.path).parts
    ):
        raise ValueError("archive path is not an exact origin-relative Binance path")


def _verify_boundary_original(boundary: DevelopmentReadBoundaryV2) -> None:
    if not boundary.allowed_symbols or not boundary.allowed_intervals:
        raise ValueError("boundary has no development scope")
    interval = boundary.allowed_intervals[0]
    boundary.authorize(
        BoundaryRequestV2(
            symbol=boundary.allowed_symbols[0],
            timeframe="1m",
            start=interval.start,
            end=interval.end,
            operation_kind=AccessOperationKindV2.NETWORK,
            target_identity="binance-request-freezer-self-check",
        )
    )


def _manifest_from_dict(payload: dict[str, Any]) -> BinanceArchiveRequestManifestV2:
    expected = {
        "schema_version",
        "boundary_sha256",
        "source_availability_sha256",
        "allowed_origin",
        "redirect_policy",
        "max_redirects",
        "tls_min_version",
        "require_ca_validation",
        "require_binance_sha256_sidecar",
        "require_local_sha256",
        "budgets",
        "requests",
        "unsupported_authorities",
        "manifest_sha256",
    }
    if set(payload) != expected or payload["schema_version"] != BinanceArchiveRequestManifestV2._schema:
        raise ValueError("archive manifest schema has unexpected or missing fields")
    requests = tuple(_request_from_dict(item) for item in payload["requests"])
    return BinanceArchiveRequestManifestV2(
        boundary_sha256=payload["boundary_sha256"],
        source_availability_sha256=payload["source_availability_sha256"],
        allowed_origin=payload["allowed_origin"],
        redirect_policy=payload["redirect_policy"],
        max_redirects=payload["max_redirects"],
        tls_min_version=payload["tls_min_version"],
        require_ca_validation=payload["require_ca_validation"],
        require_binance_sha256_sidecar=payload["require_binance_sha256_sidecar"],
        require_local_sha256=payload["require_local_sha256"],
        budgets=ArchiveBudgetsV2.from_dict(payload["budgets"]),
        requests=requests,
        unsupported_authorities=tuple(payload["unsupported_authorities"]),
        manifest_sha256=payload["manifest_sha256"],
        canonical_bytes=publication_json_bytes(payload),
        _factory_token=_MANIFEST_FACTORY,
    )


def _request_from_dict(payload: object) -> BinanceArchiveRequestV2:
    if not isinstance(payload, dict) or set(payload) != {
        "symbol",
        "archive_kind",
        "period",
        "start",
        "end",
        "object_path",
        "checksum_path",
        "request_sha256",
    }:
        raise ValueError("archive request has unexpected or missing fields")
    return BinanceArchiveRequestV2(
        symbol=payload["symbol"],
        archive_kind=payload["archive_kind"],
        period=payload["period"],
        start=_parse_utc(payload["start"]),
        end=_parse_utc(payload["end"]),
        object_path=payload["object_path"],
        checksum_path=payload["checksum_path"],
        request_sha256=payload["request_sha256"],
    )


def _register_manifest(
    manifest: BinanceArchiveRequestManifestV2,
    publication_path: Path,
    source_bytes: bytes,
) -> None:
    identifier = id(manifest)

    def cleanup(reference: weakref.ReferenceType[BinanceArchiveRequestManifestV2]) -> None:
        current = _VERIFIED_MANIFESTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_MANIFESTS.pop(identifier, None)

    reference = weakref.ref(manifest, cleanup)
    _VERIFIED_MANIFESTS[identifier] = (
        reference,
        manifest.canonical_bytes,
        publication_path,
        source_bytes,
    )


def _append_archive_audit(
    records_dir: Path,
    *,
    sequence: int,
    phase: str,
    boundary_sha256: str,
    boundary_request: BoundaryRequestV2,
    target: str,
    byte_count: int,
    prior: str | None,
    deadline: float,
    disk_guard: Callable[[], None],
) -> str:
    _enforce_deadline(deadline)
    payload = {
        "sequence": sequence,
        "phase": phase,
        "boundary_sha256": boundary_sha256,
        "symbol": boundary_request.symbol,
        "timeframe": boundary_request.timeframe,
        "start": _utc_text(boundary_request.start),
        "end": _utc_text(boundary_request.end),
        "operation_kind": boundary_request.operation_kind.value,
        "target": target,
        "allowed": True,
        "row_count": 0,
        "byte_count": byte_count,
        "prior_record_sha256": prior,
    }
    digest = hash_json("phase5-binance-archive-access-attempt-v2", payload)
    _write_no_clobber(
        records_dir / f"{sequence:08d}-{digest}.json",
        publication_json_bytes({**payload, "record_sha256": digest}),
        deadline=deadline,
    )
    disk_guard()
    return digest


def _register_acquisition(
    result: BinanceArchiveAcquisitionResultV2,
    manifest: BinanceArchiveRequestManifestV2,
    objects: tuple[tuple[Path, str], ...],
    audit_bytes: bytes,
) -> None:
    identifier = id(result)

    def cleanup(
        reference: weakref.ReferenceType[BinanceArchiveAcquisitionResultV2],
    ) -> None:
        current = _VERIFIED_ACQUISITIONS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_ACQUISITIONS.pop(identifier, None)

    reference = weakref.ref(result, cleanup)
    _VERIFIED_ACQUISITIONS[identifier] = (
        reference,
        result.canonical_bytes,
        result.publication_path,
        objects,
        manifest.canonical_bytes,
        result.audit_publication_path,
        audit_bytes,
    )


def _verify_archive_audit_publication(
    publication_path: Path,
    *,
    expected_boundary_sha256: str,
) -> bytes:
    content = read_bounded_regular(publication_path, _MAX_PUBLICATION_BYTES)
    try:
        public = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("archive audit publication is invalid JSON") from error
    if not isinstance(public, dict) or publication_json_bytes(public) != content:
        raise ValueError("archive audit publication bytes are not canonical")
    expected_fields = {
        "schema_version",
        "boundary_sha256",
        "request_count",
        "record_count",
        "terminal_record_sha256",
        "final_scope_attempts",
        "final_rows",
        "final_access_records",
        "audit_publication_sha256",
    }
    if (
        set(public) != expected_fields
        or public["schema_version"]
        != "phase5-binance-archive-access-audit-publication-v2"
        or public["boundary_sha256"] != expected_boundary_sha256
        or public["final_scope_attempts"] != 0
        or public["final_rows"] != 0
        or public["final_access_records"] != 0
    ):
        raise ValueError("archive audit publication scope is invalid")
    payload = {
        key: value for key, value in public.items() if key != "audit_publication_sha256"
    }
    if public["audit_publication_sha256"] != hash_json(
        "phase5-binance-archive-access-audit-v2", payload
    ):
        raise ValueError("archive audit publication digest is invalid")
    records = bounded_regular_files(publication_path.parent / "records", maximum=100_000)
    if public["record_count"] != len(records):
        raise ValueError("archive audit record count differs from chain")
    prior: str | None = None
    start_count = 0
    for sequence, relative in enumerate(records, start=1):
        record_bytes = read_bounded_regular(
            publication_path.parent / "records" / relative,
            _MAX_PUBLICATION_BYTES,
        )
        try:
            record = json.loads(record_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("archive audit record is invalid JSON") from error
        if not isinstance(record, dict) or publication_json_bytes(record) != record_bytes:
            raise ValueError("archive audit record bytes are not canonical")
        record_digest = record.get("record_sha256")
        record_payload = {
            key: value for key, value in record.items() if key != "record_sha256"
        }
        if (
            record.get("sequence") != sequence
            or record.get("boundary_sha256") != expected_boundary_sha256
            or record.get("prior_record_sha256") != prior
            or record_digest
            != hash_json("phase5-binance-archive-access-attempt-v2", record_payload)
        ):
            raise ValueError("archive audit record chain is invalid")
        if record.get("phase") == "start":
            start_count += 1
        prior = record_digest  # type: ignore[assignment]
    if public["terminal_record_sha256"] != prior:
        raise ValueError("archive audit terminal digest differs from chain")
    if public["request_count"] != start_count:
        raise ValueError("archive audit request count differs from request starts")
    return content


def _commit_paired_directories(
    *,
    first_stage: Path,
    first_destination: Path,
    second_stage: Path,
    second_destination: Path,
) -> None:
    first_published = False
    try:
        _fsync_directory(first_stage)
        _fsync_directory(second_stage)
        os.rename(first_stage, first_destination)
        first_published = True
        os.rename(second_stage, second_destination)
        _fsync_directory(first_destination.parent)
        if second_destination.parent != first_destination.parent:
            _fsync_directory(second_destination.parent)
    except Exception:
        if first_published and path_exists_no_follow(first_destination):
            os.rename(first_destination, first_stage)
        raise
    finally:
        _remove_staged_tree(first_stage)
        _remove_staged_tree(second_stage)


def _remove_staged_tree(root: Path) -> None:
    if not root.exists():
        return
    for relative in reversed(bounded_regular_files(root, maximum=100_000)):
        (root / relative).unlink()
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.rmdir()
    root.rmdir()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_manifest_original(
    manifest: BinanceArchiveRequestManifestV2,
    *,
    boundary: DevelopmentReadBoundaryV2,
    publication_path: Path,
) -> None:
    _verify_boundary_original(boundary)
    registered = _VERIFIED_MANIFESTS.get(id(manifest))
    if (
        registered is None
        or registered[0]() is not manifest
        or registered[1] != manifest.canonical_bytes
        or registered[2] != Path(publication_path)
    ):
        raise ValueError("archive manifest is not an exact verified original publication")
    if read_bounded_regular(Path(publication_path), _MAX_PUBLICATION_BYTES) != manifest.canonical_bytes:
        raise ValueError("archive manifest original publication bytes changed")
    if manifest.boundary_sha256 != boundary.boundary_sha256:
        raise ValueError("archive manifest is stale for the boundary")
    expected_requests = tuple(
        _make_request(symbol, period, start, end)
        for symbol in boundary.allowed_symbols
        for period, start, end in _bounded_periods(boundary)
    )
    if manifest.requests != expected_requests:
        raise ValueError(
            "archive requests differ from deterministic development-scope paths"
        )
    for request in manifest.requests:
        boundary.authorize(
            BoundaryRequestV2(
                symbol=request.symbol,
                timeframe="1m",
                start=request.start,
                end=request.end,
                operation_kind=AccessOperationKindV2.NETWORK,
                target_identity=request.request_sha256,
            )
        )


def _download_small_with_retries(
    url: str,
    manifest: BinanceArchiveRequestManifestV2,
    totals: dict[str, int],
    *,
    maximum: int,
    deadline: float,
    audit: Callable[[str, int], None],
) -> bytes:
    last: Exception | None = None
    for _ in range(manifest.budgets.retry_ceiling):
        _enforce_deadline(deadline)
        totals["requests"] += 1
        _enforce_totals(totals, manifest.budgets)
        audit("start", 0)
        try:
            payload = _https_get_bytes(
                url, manifest, maximum, deadline=deadline
            )
            audit("completion", len(payload))
            return payload
        except (OSError, urllib.error.URLError, ssl.SSLError) as error:
            audit("failure", 0)
            last = error
        except ArchiveAcquisitionExpectedError:
            audit("failure", 0)
            raise
    raise ArchiveNetworkUnavailable(
        "archive checksum request exhausted retry ceiling"
    ) from last


def _download_archive_to_temporary(
    url: str,
    manifest: BinanceArchiveRequestManifestV2,
    totals: dict[str, int],
    cache_root: Path,
    *,
    deadline: float,
    remaining_disk_bytes: int,
    audit: Callable[[str, int], None],
) -> Path:
    last: Exception | None = None
    for _ in range(manifest.budgets.retry_ceiling):
        _enforce_deadline(deadline)
        totals["requests"] += 1
        _enforce_totals(totals, manifest.budgets)
        audit("start", 0)
        descriptor, name = tempfile.mkstemp(prefix=".archive-", suffix=".tmp", dir=cache_root)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                size = _https_stream_to_file(
                    url,
                    manifest,
                    handle,
                    maximum=min(
                        manifest.budgets.max_compressed_object_bytes,
                        manifest.budgets.max_total_compressed_bytes
                        - totals["compressed"],
                        remaining_disk_bytes,
                    ),
                    chunk_bytes=manifest.budgets.chunk_bytes,
                    deadline=deadline,
                    disk_ceiling=remaining_disk_bytes,
                )
                handle.flush()
                os.fsync(handle.fileno())
            totals["compressed"] += size
            _enforce_totals(totals, manifest.budgets)
            audit("completion", size)
            return path
        except (OSError, urllib.error.URLError, ssl.SSLError) as error:
            last = error
            partial_size = path.stat().st_size
            totals["compressed"] += partial_size
            audit("failure", partial_size)
            path.unlink(missing_ok=True)
            _enforce_totals(totals, manifest.budgets)
        except Exception:
            partial_size = path.stat().st_size
            totals["compressed"] += partial_size
            audit("failure", partial_size)
            path.unlink(missing_ok=True)
            _enforce_totals(totals, manifest.budgets)
            raise
    raise ArchiveNetworkUnavailable(
        "archive object request exhausted retry ceiling"
    ) from last


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_origin: str, maximum: int) -> None:
        self._allowed_origin = allowed_origin
        self._maximum = maximum
        self._count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self._count += 1
        if self._count > self._maximum or _origin(newurl) != self._allowed_origin:
            raise urllib.error.HTTPError(
                newurl, code, "redirect violates same-origin ceiling", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _https_get_bytes(
    url: str,
    manifest: BinanceArchiveRequestManifestV2,
    maximum: int,
    *,
    deadline: float,
) -> bytes:
    _enforce_deadline(deadline)
    opener = _secure_opener(manifest)
    with opener.open(
        urllib.request.Request(url), timeout=_remaining_timeout(deadline)
    ) as response:
        _validate_response_url(response.geturl(), manifest.allowed_origin)
        content_length = _bounded_content_length(response, maximum)
        payload = response.read(content_length)
        _enforce_deadline(deadline)
    if len(payload) != content_length:
        raise ArchiveNetworkUnavailable("HTTP sidecar ended before Content-Length")
    return payload


def _https_stream_to_file(
    url: str,
    manifest: BinanceArchiveRequestManifestV2,
    handle: Any,
    *,
    maximum: int,
    chunk_bytes: int,
    deadline: float,
    disk_ceiling: int,
) -> int:
    if maximum < 1:
        raise ArchiveBudgetExceeded("no compressed/disk budget remains")
    _enforce_deadline(deadline)
    opener = _secure_opener(manifest)
    total = 0
    with opener.open(
        urllib.request.Request(url), timeout=_remaining_timeout(deadline)
    ) as response:
        _validate_response_url(response.geturl(), manifest.allowed_origin)
        content_length = _bounded_content_length(response, maximum)
        while total < content_length:
            _enforce_deadline(deadline)
            chunk = response.read(min(chunk_bytes, content_length - total))
            if not chunk:
                raise ArchiveNetworkUnavailable(
                    "compressed archive ended before Content-Length"
                )
            total += len(chunk)
            handle.write(chunk)
            if int(getattr(os.fstat(handle.fileno()), "st_blocks", 0)) * 512 > disk_ceiling:
                raise ArchiveBudgetExceeded(
                    "temporary archive exceeds actual disk ceiling"
                )
            _enforce_deadline(deadline)
    return total


def _bounded_content_length(response: Any, maximum: int) -> int:
    raw = response.headers.get("Content-Length")
    try:
        content_length = int(raw)
    except (TypeError, ValueError) as error:
        raise ArchiveNetworkUnavailable(
            "official archive response requires bounded Content-Length"
        ) from error
    if content_length < 0 or content_length > maximum:
        raise ArchiveBudgetExceeded("HTTP Content-Length exceeds frozen ceiling")
    return content_length


def _remaining_timeout(deadline: float) -> float:
    _enforce_deadline(deadline)
    return max(0.001, min(30.0, deadline - time.monotonic()))


def _secure_opener(manifest: BinanceArchiveRequestManifestV2) -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        _SameOriginRedirectHandler(manifest.allowed_origin, manifest.max_redirects),
    )


def _exact_url(origin: str, path: str) -> str:
    _validate_archive_path(path)
    url = f"{origin}{path}"
    _validate_response_url(url, origin)
    return url


def _validate_response_url(url: str, origin: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        _origin(url) != origin
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HTTP target is not the exact allowed HTTPS origin")


def _origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port
    suffix = "" if port in (None, 443) else f":{port}"
    return f"{parsed.scheme}://{parsed.hostname}{suffix}"


def _parse_checksum(payload: bytes, expected_filename: str) -> str:
    if len(payload) > 8192:
        raise ValueError("Binance checksum sidecar exceeds bound")
    try:
        parts = payload.decode("ascii").strip().split()
    except UnicodeDecodeError as error:
        raise ValueError("Binance checksum sidecar is not ASCII") from error
    if len(parts) != 2 or parts[1].lstrip("*") != expected_filename:
        raise ValueError("Binance checksum sidecar names a different object")
    return _require_sha256(parts[0].lower(), "Binance sidecar checksum")


def _inspect_archive(
    archive_path: Path,
    request: BinanceArchiveRequestV2,
    budgets: ArchiveBudgetsV2,
    *,
    remaining_decompressed: int,
    remaining_rows: int,
    deadline: float,
) -> tuple[int, int]:
    try:
        _enforce_deadline(deadline)
        decompressed = 0
        rows = 0
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) != 1 or infos[0].is_dir():
                raise ArchiveIntegrityError(
                    "Binance archive must contain exactly one regular CSV"
                )
            info = infos[0]
            if Path(info.filename).name != info.filename or not info.filename.endswith(
                ".csv"
            ):
                raise ArchiveIntegrityError("Binance archive contains an unsafe member")
            if (
                info.file_size > budgets.max_decompressed_object_bytes
                or info.file_size > remaining_decompressed
            ):
                raise ArchiveBudgetExceeded(
                    "decompressed archive exceeds object or total ceiling"
                )
            with archive.open(info) as raw:
                maximum_line_bytes = min(
                    1024 * 1024,
                    budgets.max_decompressed_object_bytes,
                    remaining_decompressed,
                )
                while decompressed < info.file_size:
                    _enforce_deadline(deadline)
                    if rows >= remaining_rows:
                        raise ArchiveBudgetExceeded(
                            "parsed rows would exceed frozen total ceiling"
                        )
                    remaining_file = info.file_size - decompressed
                    line = raw.readline(min(maximum_line_bytes, remaining_file))
                    if not line:
                        raise ArchiveIntegrityError(
                            "decompressed archive ended before ZIP metadata size"
                        )
                    decompressed += len(line)
                    if (
                        not line.endswith(b"\n")
                        and decompressed < info.file_size
                    ):
                        raise ArchiveBudgetExceeded(
                            "archive CSV row exceeds bounded line ceiling"
                        )
                    decoded = line.decode("utf-8")
                    parsed_rows = tuple(csv.reader((decoded,)))
                    if len(parsed_rows) != 1:
                        raise ArchiveIntegrityError(
                            "archive CSV row framing is invalid"
                        )
                    observed = parse_observed_trade_row_v2(
                        tuple(parsed_rows[0]), archive_kind=request.archive_kind
                    )
                    start_ms = int(request.start.timestamp() * 1_000)
                    end_ms = int(request.end.timestamp() * 1_000)
                    if not start_ms <= observed.timestamp_ms < end_ms:
                        raise ArchiveIntegrityError(
                            "archive row exceeds its preregistered development interval"
                        )
                    rows += 1
                _enforce_deadline(deadline)
        return decompressed, rows
    except ArchiveAcquisitionExpectedError:
        raise
    except (UnicodeDecodeError, ValueError, zipfile.BadZipFile, csv.Error) as error:
        raise ArchiveIntegrityError(f"archive content is invalid: {error}") from error


def _publish_content_addressed(
    source: Path, cache_root: Path, digest: str, *, deadline: float
) -> Path:
    _enforce_deadline(deadline)
    directory = cache_root / "sha256" / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / digest
    if path_exists_no_follow(destination):
        if _sha256_path(destination, deadline=deadline) != digest:
            raise FileExistsError("content-addressed cache object differs from its name")
        return destination
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError:
        if _sha256_path(destination, deadline=deadline) != digest:
            raise
    _enforce_deadline(deadline)
    return destination


def _enforce_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise ArchiveBudgetExceeded("archive acquisition exceeds runtime ceiling")


def _enforce_totals(totals: dict[str, int], budgets: ArchiveBudgetsV2) -> None:
    checks = (
        ("requests", budgets.max_requests),
        ("compressed", budgets.max_total_compressed_bytes),
        ("decompressed", budgets.max_total_decompressed_bytes),
        ("files", budgets.max_files),
        ("rows", budgets.max_rows),
    )
    for label, ceiling in checks:
        if totals[label] > ceiling:
            raise ArchiveBudgetExceeded(
                f"archive acquisition exceeds {label} ceiling"
            )


def _write_no_clobber(
    path: Path, content: bytes, *, deadline: float | None = None
) -> None:
    if deadline is not None:
        _enforce_deadline(deadline)
    if path_exists_no_follow(path):
        raise FileExistsError(f"refusing stale or concurrent publication: {path}")
    require_regular_directory(path.parent)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            if deadline is not None:
                _enforce_deadline(deadline)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        if deadline is not None:
            _enforce_deadline(deadline)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_path(path: Path, *, deadline: float | None = None) -> str:
    if not path_exists_no_follow(path):
        raise FileNotFoundError(f"archive object is missing: {path}")
    digest = hashlib.sha256()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    with os.fdopen(descriptor, "rb") as handle:
        while True:
            if deadline is not None:
                _enforce_deadline(deadline)
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if deadline is not None:
                _enforce_deadline(deadline)
    return digest.hexdigest()


def _disk_usage_unique(root: Path, *, maximum: int) -> int:
    seen: set[tuple[int, int]] = set()
    total = 0
    for relative in bounded_regular_files(root, maximum=maximum):
        metadata = (root / relative).stat(follow_symlinks=False)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += int(getattr(metadata, "st_blocks", 0)) * 512
    return total


def _acquisition_disk_usage(
    *roots: Path,
    maximum: int,
) -> int:
    return sum(_disk_usage_unique(root, maximum=maximum) for root in roots)


def _allocated_bytes(path: Path) -> int:
    metadata = path.stat(follow_symlinks=False)
    return int(getattr(metadata, "st_blocks", 0)) * 512


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("archive request timestamp must be ISO-8601 UTC")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("archive request timestamp must be UTC")
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


__all__ = [
    "ArchiveAcquisitionExpectedError",
    "ArchiveBudgetExceeded",
    "ArchiveBudgetsV2",
    "ArchiveIntegrityError",
    "ArchiveNetworkUnavailable",
    "BinanceArchiveAcquisitionResultV2",
    "BinanceArchiveRequestManifestV2",
    "BinanceArchiveRequestV2",
    "ObservedTradeV2",
    "acquire_binance_archives_v2",
    "freeze_binance_archive_requests_v2",
    "load_binance_archive_acquisition_v2",
    "load_binance_archive_request_manifest_v2",
    "load_binance_archive_request_manifest_for_acquisition_v2",
    "parse_observed_trade_row_v2",
    "verify_binance_archive_request_manifest_v2",
    "verify_binance_archive_acquisition_v2",
]
