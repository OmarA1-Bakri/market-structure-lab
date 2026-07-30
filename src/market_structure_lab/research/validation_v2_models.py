"""Typed, domain-separated identities for Phase 5 validation V2."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any, ClassVar, Self
import weakref

from market_structure_lab.core.identity import hash_json

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_SOURCE_COVERAGE_FACTORY = object()
_VERIFIED_COVERAGE_OBJECTS: dict[
    int, tuple[weakref.ReferenceType[SourceCoveragePublicationV2], bytes]
] = {}


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a non-empty canonical string")
    return value


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value


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
    return _require_utc(parsed, label)


def _require_minute_aligned(value: datetime, label: str) -> None:
    _require_utc(value, label)
    if value.second or value.microsecond:
        raise ValueError(f"{label} must be minute-aligned")


@dataclass(frozen=True, slots=True)
class _TypedIdentityV2:
    """A content address whose prefix makes publication types non-interchangeable."""

    value: str
    _prefix: ClassVar[str]
    _domain: ClassVar[str]

    def __post_init__(self) -> None:
        expected = f"{self._prefix}-"
        if not isinstance(self.value, str) or not self.value.startswith(expected):
            raise ValueError(f"{type(self).__name__} must start with {expected}")
        _require_sha256(self.value.removeprefix(expected), type(self).__name__)

    @classmethod
    def from_payload(cls, payload: object) -> Self:
        return cls(f"{cls._prefix}-{hash_json(cls._domain, payload)}")

    def __str__(self) -> str:
        return self.value


class SourceCoverageIdentityV2(_TypedIdentityV2):
    _prefix = "SCV2"
    _domain = "phase5-validation-source-coverage-v2"


class DevelopmentSplitIdentityV2(_TypedIdentityV2):
    _prefix = "DSV2"
    _domain = "phase5-validation-development-split-v2"


class SourcePublicationIdentityV2(_TypedIdentityV2):
    _prefix = "SRCV2"
    _domain = "phase5-validation-development-source-v2"


class AggregatePublicationIdentityV2(_TypedIdentityV2):
    _prefix = "AGGV2"
    _domain = "phase5-validation-aggregate-publication-v2"


class PrecisionAuthorityIdentityV2(_TypedIdentityV2):
    _prefix = "PRCV2"
    _domain = "phase5-validation-precision-authority-v2"


class CostAuthorityIdentityV2(_TypedIdentityV2):
    _prefix = "CSTV2"
    _domain = "phase5-validation-cost-authority-v2"


class ValidationRosterIdentityV2(_TypedIdentityV2):
    _prefix = "RSTV2"
    _domain = "phase5-validation-roster-v2"


class AccessAuditLedgerIdentityV2(_TypedIdentityV2):
    _prefix = "AALV2"
    _domain = "phase5-validation-access-ledger-v2"


@dataclass(frozen=True, slots=True)
class RawDumpIdentityV2:
    """Path-independent raw-dump and pg_restore-list provenance metadata."""

    dump_sha256: str
    byte_count: int
    pg_restore_list_sha256: str
    candle_table_toc_identity: str
    source_mapping_version: str

    def __post_init__(self) -> None:
        _require_sha256(self.dump_sha256, "dump_sha256")
        _require_sha256(self.pg_restore_list_sha256, "pg_restore_list_sha256")
        if (
            isinstance(self.byte_count, bool)
            or not isinstance(self.byte_count, int)
            or self.byte_count < 1
        ):
            raise ValueError("byte_count must be a positive integer")
        _require_string(self.candle_table_toc_identity, "candle_table_toc_identity")
        _require_string(self.source_mapping_version, "source_mapping_version")

    @property
    def identity_sha256(self) -> str:
        return hash_json("phase5-validation-raw-dump-identity-v2", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "dump_sha256": self.dump_sha256,
            "byte_count": self.byte_count,
            "pg_restore_list_sha256": self.pg_restore_list_sha256,
            "candle_table_toc_identity": self.candle_table_toc_identity,
            "source_mapping_version": self.source_mapping_version,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "dump_sha256",
                "byte_count",
                "pg_restore_list_sha256",
                "candle_table_toc_identity",
                "source_mapping_version",
            },
            "raw_dump",
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ReconciliationAuthorityV2:
    """Immutable RR-000008 metadata authority, without a row-read capability."""

    promotion_receipt_sha256: str
    work_unit_manifest_sha256: tuple[str, ...]
    comparison_part_sha256: tuple[str, ...]
    replacement_source_policy: str

    def __post_init__(self) -> None:
        _require_sha256(self.promotion_receipt_sha256, "promotion_receipt_sha256")
        _require_digest_tuple(self.work_unit_manifest_sha256, "work_unit_manifest_sha256")
        _require_digest_tuple(self.comparison_part_sha256, "comparison_part_sha256")
        _require_string(self.replacement_source_policy, "replacement_source_policy")

    @property
    def identity_sha256(self) -> str:
        return hash_json("phase5-validation-reconciliation-authority-v2", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "promotion_receipt_sha256": self.promotion_receipt_sha256,
            "work_unit_manifest_sha256": list(self.work_unit_manifest_sha256),
            "comparison_part_sha256": list(self.comparison_part_sha256),
            "replacement_source_policy": self.replacement_source_policy,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "promotion_receipt_sha256",
                "work_unit_manifest_sha256",
                "comparison_part_sha256",
                "replacement_source_policy",
            },
            "reconciliation",
        )
        return cls(
            promotion_receipt_sha256=values["promotion_receipt_sha256"],  # type: ignore[arg-type]
            work_unit_manifest_sha256=_string_tuple(
                values["work_unit_manifest_sha256"], "work_unit_manifest_sha256"
            ),
            comparison_part_sha256=_string_tuple(
                values["comparison_part_sha256"], "comparison_part_sha256"
            ),
            replacement_source_policy=values["replacement_source_policy"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class SourceCoverageEntryV2:
    """Outcome-blind symbol coverage and compatibility metadata."""

    symbol: str
    complete_start: datetime
    complete_end: datetime
    timeframes: tuple[str, ...]
    source_conflict: bool
    mapping_compatible: bool
    metadata_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or not self.symbol
            or self.symbol != self.symbol.upper()
        ):
            raise ValueError("coverage symbol must be a non-empty upper-case identifier")
        _require_utc(self.complete_start, "complete_start")
        _require_utc(self.complete_end, "complete_end")
        _require_minute_aligned(self.complete_start, "complete_start")
        _require_minute_aligned(self.complete_end, "complete_end")
        if self.complete_end <= self.complete_start:
            raise ValueError("coverage interval must be positive")
        if (
            not isinstance(self.timeframes, tuple)
            or not self.timeframes
            or len(self.timeframes) != len(set(self.timeframes))
        ):
            raise ValueError("timeframes must be a non-empty unique tuple")
        for timeframe in self.timeframes:
            _require_string(timeframe, "timeframe")
        if not isinstance(self.source_conflict, bool):
            raise TypeError("source_conflict must be boolean")
        if not isinstance(self.mapping_compatible, bool):
            raise TypeError("mapping_compatible must be boolean")
        _require_sha256(self.metadata_sha256, "metadata_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "complete_start": _utc_text(self.complete_start),
            "complete_end": _utc_text(self.complete_end),
            "timeframes": list(self.timeframes),
            "source_conflict": self.source_conflict,
            "mapping_compatible": self.mapping_compatible,
            "metadata_sha256": self.metadata_sha256,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "symbol",
                "complete_start",
                "complete_end",
                "timeframes",
                "source_conflict",
                "mapping_compatible",
                "metadata_sha256",
            },
            "coverage entry",
        )
        return cls(
            symbol=values["symbol"],  # type: ignore[arg-type]
            complete_start=_parse_utc(values["complete_start"], "complete_start"),
            complete_end=_parse_utc(values["complete_end"], "complete_end"),
            timeframes=_string_tuple(values["timeframes"], "timeframes"),
            source_conflict=values["source_conflict"],  # type: ignore[arg-type]
            mapping_compatible=values["mapping_compatible"],  # type: ignore[arg-type]
            metadata_sha256=values["metadata_sha256"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceCoveragePublicationV2:
    """Self-addressed metadata-only coverage publication."""

    raw_dump: RawDumpIdentityV2
    reconciliation: ReconciliationAuthorityV2
    compatibility_metadata_sha256: str
    entries: tuple[SourceCoverageEntryV2, ...]
    coverage_identity: SourceCoverageIdentityV2
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SOURCE_COVERAGE_FACTORY:
            raise TypeError("SourceCoveragePublicationV2 requires its freeze factory")
        if not isinstance(self.raw_dump, RawDumpIdentityV2):
            raise TypeError("raw_dump must be RawDumpIdentityV2")
        if not isinstance(self.reconciliation, ReconciliationAuthorityV2):
            raise TypeError("reconciliation must be ReconciliationAuthorityV2")
        _require_sha256(self.compatibility_metadata_sha256, "compatibility_metadata_sha256")
        if (
            not isinstance(self.entries, tuple)
            or not self.entries
            or any(not isinstance(item, SourceCoverageEntryV2) for item in self.entries)
        ):
            raise TypeError("entries must be a non-empty tuple of coverage entries")
        symbols = tuple(item.symbol for item in self.entries)
        if symbols != tuple(sorted(set(symbols))):
            raise ValueError("coverage entries must be uniquely sorted by symbol")
        if self.coverage_identity != SourceCoverageIdentityV2.from_payload(
            self._identity_payload()
        ):
            raise ValueError("coverage identity does not match metadata")
        if self.canonical_bytes != publication_json_bytes(self.to_dict()):
            raise ValueError("coverage canonical bytes do not match publication")
        _register_verified_coverage(self)

    @classmethod
    def freeze(
        cls,
        *,
        raw_dump: RawDumpIdentityV2,
        reconciliation: ReconciliationAuthorityV2,
        compatibility_metadata_sha256: str,
        entries: tuple[SourceCoverageEntryV2, ...],
    ) -> Self:
        ordered = tuple(sorted(entries, key=lambda item: item.symbol))
        payload = {
            "raw_dump": raw_dump.to_dict(),
            "reconciliation": reconciliation.to_dict(),
            "compatibility_metadata_sha256": compatibility_metadata_sha256,
            "entries": [item.to_dict() for item in ordered],
        }
        identity = SourceCoverageIdentityV2.from_payload(payload)
        public = {
            "schema_version": "phase5-validation-source-coverage-v2",
            **payload,
            "coverage_identity": identity.value,
        }
        return cls(
            raw_dump=raw_dump,
            reconciliation=reconciliation,
            compatibility_metadata_sha256=compatibility_metadata_sha256,
            entries=ordered,
            coverage_identity=identity,
            canonical_bytes=publication_json_bytes(public),
            _factory_token=_SOURCE_COVERAGE_FACTORY,
        )

    def _identity_payload(self) -> dict[str, object]:
        return {
            "raw_dump": self.raw_dump.to_dict(),
            "reconciliation": self.reconciliation.to_dict(),
            "compatibility_metadata_sha256": self.compatibility_metadata_sha256,
            "entries": [item.to_dict() for item in self.entries],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase5-validation-source-coverage-v2",
            **self._identity_payload(),
            "coverage_identity": self.coverage_identity.value,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        values = _exact_mapping(
            payload,
            {
                "schema_version",
                "raw_dump",
                "reconciliation",
                "compatibility_metadata_sha256",
                "entries",
                "coverage_identity",
            },
            "source coverage publication",
        )
        if values["schema_version"] != "phase5-validation-source-coverage-v2":
            raise ValueError("source coverage schema_version is not V2")
        raw_entries = values["entries"]
        if not isinstance(raw_entries, list):
            raise TypeError("coverage entries must be a list")
        publication = cls.freeze(
            raw_dump=RawDumpIdentityV2.from_dict(values["raw_dump"]),
            reconciliation=ReconciliationAuthorityV2.from_dict(values["reconciliation"]),
            compatibility_metadata_sha256=values[  # type: ignore[arg-type]
                "compatibility_metadata_sha256"
            ],
            entries=tuple(SourceCoverageEntryV2.from_dict(item) for item in raw_entries),
        )
        if values["coverage_identity"] != publication.coverage_identity.value:
            raise ValueError("coverage_identity does not match publication")
        return publication


def verified_source_coverage_bytes(
    publication: SourceCoveragePublicationV2,
) -> bytes:
    """Return bytes only for the exact registry-issued publication object."""

    registered = _VERIFIED_COVERAGE_OBJECTS.get(id(publication))
    if registered is None or registered[0]() is not publication:
        raise ValueError("source coverage is not an exact verified original publication")
    if registered[1] != publication.canonical_bytes:
        raise ValueError("source coverage original publication bytes changed")
    return registered[1]


@dataclass(frozen=True, slots=True)
class ValidationProgrammeConfigV2:
    """Typed V2 config whose programme ID is derived, never supplied."""

    implementation_checkpoint: str
    coverage_identity: SourceCoverageIdentityV2
    split_identity: DevelopmentSplitIdentityV2
    source_identity: SourcePublicationIdentityV2
    aggregate_identity: AggregatePublicationIdentityV2
    precision_identity: PrecisionAuthorityIdentityV2
    cost_identity: CostAuthorityIdentityV2
    roster_identity: ValidationRosterIdentityV2
    access_ledger_identity: AccessAuditLedgerIdentityV2
    policy_identities: tuple[tuple[str, str], ...]
    work_budget_sha256: str
    programme_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.implementation_checkpoint, str)
            or _COMMIT.fullmatch(self.implementation_checkpoint) is None
        ):
            raise ValueError("implementation_checkpoint must be a 40-character commit")
        identity_fields = {
            "coverage_identity": SourceCoverageIdentityV2,
            "split_identity": DevelopmentSplitIdentityV2,
            "source_identity": SourcePublicationIdentityV2,
            "aggregate_identity": AggregatePublicationIdentityV2,
            "precision_identity": PrecisionAuthorityIdentityV2,
            "cost_identity": CostAuthorityIdentityV2,
            "roster_identity": ValidationRosterIdentityV2,
            "access_ledger_identity": AccessAuditLedgerIdentityV2,
        }
        for field_name, expected_type in identity_fields.items():
            if not isinstance(getattr(self, field_name), expected_type):
                raise TypeError(f"{field_name} must be its concrete V2 identity type")
        _require_pair_tuple(self.policy_identities, "policy_identities", digest_values=True)
        _require_sha256(self.work_budget_sha256, "work_budget_sha256")
        _require_pair_tuple(self.programme_metadata, "programme_metadata")

    @property
    def config_sha256(self) -> str:
        return hash_json("phase5-validation-programme-config-v2", self.to_config_dict())

    @property
    def programme_id(self) -> str:
        return f"VPV2-{hash_json('phase5-validation-programme-id-v2', self.to_config_dict())}"

    def to_config_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase5-validation-programme-config-v2",
            "implementation_checkpoint": self.implementation_checkpoint,
            "coverage_identity": self.coverage_identity.value,
            "split_identity": self.split_identity.value,
            "source_identity": self.source_identity.value,
            "aggregate_identity": self.aggregate_identity.value,
            "precision_identity": self.precision_identity.value,
            "cost_identity": self.cost_identity.value,
            "roster_identity": self.roster_identity.value,
            "access_ledger_identity": self.access_ledger_identity.value,
            "policy_identities": [list(item) for item in self.policy_identities],
            "work_budget_sha256": self.work_budget_sha256,
            "programme_metadata": [list(item) for item in self.programme_metadata],
        }

    def to_publication_dict(self) -> dict[str, object]:
        return {
            **self.to_config_dict(),
            "config_sha256": self.config_sha256,
            "programme_id": self.programme_id,
        }

    @classmethod
    def from_config_dict(cls, payload: object) -> Self:
        if not isinstance(payload, dict):
            raise TypeError("V2 config preimage must be an object")
        if "programme_id" in payload:
            raise ValueError("programme_id cannot be supplied in the config preimage")
        expected = {
            "schema_version",
            "implementation_checkpoint",
            "coverage_identity",
            "split_identity",
            "source_identity",
            "aggregate_identity",
            "precision_identity",
            "cost_identity",
            "roster_identity",
            "access_ledger_identity",
            "policy_identities",
            "work_budget_sha256",
            "programme_metadata",
        }
        if set(payload) != expected:
            raise ValueError("V2 config preimage has unexpected or missing fields")
        if payload["schema_version"] != "phase5-validation-programme-config-v2":
            raise ValueError("V2 config schema_version is invalid")
        return cls(
            implementation_checkpoint=payload["implementation_checkpoint"],  # type: ignore[arg-type]
            coverage_identity=SourceCoverageIdentityV2(payload["coverage_identity"]),  # type: ignore[arg-type]
            split_identity=DevelopmentSplitIdentityV2(payload["split_identity"]),  # type: ignore[arg-type]
            source_identity=SourcePublicationIdentityV2(payload["source_identity"]),  # type: ignore[arg-type]
            aggregate_identity=AggregatePublicationIdentityV2(
                payload["aggregate_identity"]  # type: ignore[arg-type]
            ),
            precision_identity=PrecisionAuthorityIdentityV2(
                payload["precision_identity"]  # type: ignore[arg-type]
            ),
            cost_identity=CostAuthorityIdentityV2(payload["cost_identity"]),  # type: ignore[arg-type]
            roster_identity=ValidationRosterIdentityV2(payload["roster_identity"]),  # type: ignore[arg-type]
            access_ledger_identity=AccessAuditLedgerIdentityV2(
                payload["access_ledger_identity"]  # type: ignore[arg-type]
            ),
            policy_identities=_pair_tuple(payload["policy_identities"], "policy_identities"),
            work_budget_sha256=payload["work_budget_sha256"],  # type: ignore[arg-type]
            programme_metadata=_pair_tuple(payload["programme_metadata"], "programme_metadata"),
        )


def load_validation_programme_config_v2(payload: object) -> ValidationProgrammeConfigV2:
    """Load and verify a public V2 config wrapper."""

    if not isinstance(payload, dict):
        raise TypeError("V2 config publication must be an object")
    if payload.get("schema_version") != "phase5-validation-programme-config-v2":
        raise ValueError("V2 config schema_version is invalid")
    preimage = {
        key: value for key, value in payload.items() if key not in {"config_sha256", "programme_id"}
    }
    config = ValidationProgrammeConfigV2.from_config_dict(preimage)
    expected_keys = set(config.to_config_dict()) | {"config_sha256", "programme_id"}
    if set(payload) != expected_keys:
        raise ValueError("V2 config publication has unexpected or missing fields")
    if payload["config_sha256"] != config.config_sha256:
        raise ValueError("config_sha256 does not match the canonical V2 config")
    if payload["programme_id"] != config.programme_id:
        raise ValueError("programme_id does not match the canonical V2 config")
    return config


def verify_v2_slot_computation_count(
    publications: object,
    *,
    expected_count: int,
) -> int:
    """Count only distinct V2 slot-computation results, never V1 receipt wrappers."""

    if not isinstance(publications, tuple):
        raise TypeError("V2 slot computation publications must be a tuple")
    identities: set[str] = set()
    for publication in publications:
        if (
            not isinstance(publication, dict)
            or publication.get("schema_version") != "validation-slot-computation-result-v2"
        ):
            raise ValueError("publication is not a V2 slot computation result")
        identity = _require_sha256(
            publication.get("slot_computation_result_sha256"),
            "slot_computation_result_sha256",
        )
        identities.add(identity)
    if len(publications) != expected_count or len(identities) != expected_count:
        raise ValueError("V2 slot computation count is incomplete or duplicated")
    return len(identities)


def publication_json_bytes(payload: object) -> bytes:
    """Return deterministic public JSON bytes used by immutable V2 publications."""

    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _register_verified_coverage(publication: SourceCoveragePublicationV2) -> None:
    identifier = id(publication)

    def cleanup(reference: weakref.ReferenceType[SourceCoveragePublicationV2]) -> None:
        current = _VERIFIED_COVERAGE_OBJECTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_COVERAGE_OBJECTS.pop(identifier, None)

    reference = weakref.ref(publication, cleanup)
    _VERIFIED_COVERAGE_OBJECTS[identifier] = (reference, publication.canonical_bytes)


def _exact_mapping(payload: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be an object")
    if set(payload) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")
    return payload


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{label} must be a string sequence")
    return tuple(value)


def _require_digest_tuple(value: object, label: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise TypeError(f"{label} must be a non-empty tuple")
    for item in value:
        _require_sha256(item, label)


def _pair_tuple(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be a pair sequence")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or not all(isinstance(part, str) for part in item)
        ):
            raise TypeError(f"{label} must contain string pairs")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def _require_pair_tuple(value: object, label: str, *, digest_values: bool = False) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be a tuple")
    pairs = _pair_tuple(value, label)
    if pairs != tuple(sorted(set(pairs))):
        raise ValueError(f"{label} must be uniquely sorted")
    for key, item in pairs:
        _require_string(key, f"{label} key")
        if digest_values:
            _require_sha256(item, f"{label} value")
        else:
            _require_string(item, f"{label} value")


__all__ = [
    "AccessAuditLedgerIdentityV2",
    "AggregatePublicationIdentityV2",
    "CostAuthorityIdentityV2",
    "DevelopmentSplitIdentityV2",
    "PrecisionAuthorityIdentityV2",
    "RawDumpIdentityV2",
    "ReconciliationAuthorityV2",
    "SourceCoverageEntryV2",
    "SourceCoverageIdentityV2",
    "SourceCoveragePublicationV2",
    "SourcePublicationIdentityV2",
    "ValidationProgrammeConfigV2",
    "ValidationRosterIdentityV2",
    "load_validation_programme_config_v2",
    "publication_json_bytes",
    "verified_source_coverage_bytes",
    "verify_v2_slot_computation_count",
]
