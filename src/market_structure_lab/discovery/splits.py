"""Frozen chronological split policy and fail-closed discovery input guards."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Literal, Mapping, Sequence

from market_structure_lab.data.derived import (
    DerivedPartitionRecord,
    DerivedPublicationManifest,
    feature_partition_records,
)
from market_structure_lab.data.export import SnapshotManifest
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.normalization import (
    PartitionRole as NormalizerPartitionRole,
    RobustNormalizer,
)
from market_structure_lab.features.registry import FeatureRegistry

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_ROW_SOURCE_IDENTITY_FIELDS = ("config_version", "profile_version", "window_policy_id")
_DISCOVERY_INPUT_FACTORY = object()
MAX_DISCOVERY_INPUT_ROWS = 1_000_000
_DISCOVERY_PROVENANCE_FACTORY = object()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")


class PartitionRole(StrEnum):
    """Purpose assigned to a frozen chronological partition."""

    DISCOVERY = "discovery"
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


@dataclass(frozen=True, slots=True)
class TimePartition:
    """A UTC, half-open time interval over an explicit asset universe."""

    role: PartitionRole
    start: datetime
    end: datetime
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.role, PartitionRole):
            raise TypeError("role must be a PartitionRole")
        start = _require_utc(self.start, "partition start")
        end = _require_utc(self.end, "partition end")
        if start >= end:
            raise ValueError("partition start must precede end")
        symbols = _canonical_names(self.symbols, "symbols", require_non_empty=True)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "symbols", symbols)

    def contains(self, information_cutoff: datetime) -> bool:
        """Return whether a UTC information cutoff falls in this half-open interval."""

        cutoff = _require_utc(information_cutoff, "information_cutoff")
        return self.start <= cutoff < self.end

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "start": _format_utc(self.start),
            "end": _format_utc(self.end),
            "symbols": list(self.symbols),
        }


@dataclass(frozen=True, slots=True)
class FrozenDiscoverySplit:
    """Canonical chronological split policy identified by its full SHA-256."""

    split_id: str
    discovery: TimePartition
    development: TimePartition
    holdout: TimePartition
    asset_holdouts: tuple[str, ...]
    sha256: str

    def __post_init__(self) -> None:
        canonical_holdouts = _validate_split_policy(
            self.split_id,
            self.discovery,
            self.development,
            self.holdout,
            self.asset_holdouts,
        )
        if canonical_holdouts != self.asset_holdouts:
            raise ValueError("asset_holdouts must use canonical deterministic ordering")
        if _SHA256.fullmatch(self.sha256) is None or self.sha256 != _split_sha256(
            self.split_id,
            self.discovery,
            self.development,
            self.holdout,
            canonical_holdouts,
        ):
            raise ValueError("frozen split SHA-256 does not match canonical content")


@dataclass(frozen=True, slots=True)
class DiscoveryInput:
    """Validated, ordered feature rows admitted for one discovery purpose."""

    partition: TimePartition
    rows: tuple[FeatureRow, ...]
    dataset_version: str
    feature_set_id: str
    registry_id: str
    publication_sha256: str | None = None
    partition_records: tuple[DerivedPartitionRecord, ...] = ()
    content_sha256: str | None = None
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _DISCOVERY_INPUT_FACTORY:
            raise TypeError("DiscoveryInput must be created by make_discovery_input")


@dataclass(frozen=True, slots=True)
class DiscoveryProvenance:
    """One factory-verified identity for every artifact admitted to discovery."""

    snapshot_manifest_sha256: str
    derived_publication_sha256: str
    registry_sha256: str
    normalizer_sha256: str
    feature_names: tuple[str, ...]
    split_sha256: str
    code_commit: str
    lock_sha256: str
    motif_regime_assignment_sha256: str
    feature_partitions: tuple[tuple[str, str, str], ...]
    discovery_input_sha256: str
    development_input_sha256: str
    sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _DISCOVERY_PROVENANCE_FACTORY:
            raise TypeError("DiscoveryProvenance must be created by freeze_discovery_provenance")


def freeze_discovery_provenance(
    *,
    snapshot_manifest: SnapshotManifest,
    feature_publication: DerivedPublicationManifest,
    registry: FeatureRegistry,
    normalizer_artifact: bytes,
    split: FrozenDiscoverySplit,
    feature_names: tuple[str, ...],
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    code_commit: str,
    lockfile_bytes: bytes,
    motif_regime_assignment_sha256: str,
) -> DiscoveryProvenance:
    """Validate and freeze the complete snapshot-to-discovery artifact chain."""

    if not isinstance(snapshot_manifest, SnapshotManifest):
        raise TypeError("snapshot_manifest must be a SnapshotManifest")
    if not isinstance(feature_publication, DerivedPublicationManifest):
        raise TypeError("feature_publication must be a DerivedPublicationManifest")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    if not isinstance(normalizer_artifact, bytes):
        raise TypeError("normalizer artifact bytes are required")
    normalizer = RobustNormalizer.from_json(normalizer_artifact)
    if normalizer.canonical_json() != normalizer_artifact:
        raise ValueError("normalizer artifact bytes are not canonical")
    if not isinstance(split, FrozenDiscoverySplit):
        raise TypeError("split must be a FrozenDiscoverySplit")
    if (
        not isinstance(feature_names, tuple)
        or not feature_names
        or len(set(feature_names)) != len(feature_names)
    ):
        raise ValueError("feature_names must be a unique non-empty tuple")
    if _COMMIT.fullmatch(code_commit) is None:
        raise ValueError("code commit must be a clean committed hexadecimal identity")
    if not isinstance(lockfile_bytes, bytes):
        raise TypeError("lockfile bytes are required")
    lock_sha256 = hashlib.sha256(lockfile_bytes).hexdigest()
    if _SHA256.fullmatch(motif_regime_assignment_sha256) is None:
        raise ValueError("motif regime assignment SHA-256 is invalid")
    identity = feature_publication.identity
    if feature_publication.publication_kind != "features":
        raise ValueError("discovery requires a feature publication")
    if snapshot_manifest.identity.dataset_version != identity.dataset_version:
        raise ValueError("snapshot manifest and feature publication dataset identity mismatch")
    if snapshot_manifest.snapshot_sha256 != identity.dataset_snapshot_sha256:
        raise ValueError("snapshot manifest hash does not match feature publication")
    if snapshot_manifest.identity.code_commit != code_commit:
        raise ValueError("snapshot code commit is uncommitted, dirty, or mismatched")
    if identity.code_commit != code_commit:
        raise ValueError("feature publication code commit mismatch")
    if identity.uv_lock_sha256 != lock_sha256:
        raise ValueError("feature publication lock hash mismatch")
    if identity.feature_set_id != registry.feature_set_id or identity.feature_registry_sha256 != (
        registry.sha256
    ):
        raise ValueError("feature publication registry identity mismatch")
    if normalizer.dataset_snapshot_id != identity.dataset_version:
        raise ValueError("normalizer snapshot identity mismatch")
    if (
        normalizer.feature_set_id != registry.feature_set_id
        or normalizer.registry_id != registry.registry_id
        or normalizer.registry_sha256 != registry.sha256
    ):
        raise ValueError("normalizer registry identity mismatch")
    if identity.normalizer_artifact_sha256 != normalizer.artifact_sha256:
        raise ValueError("feature publication normalizer identity mismatch")
    if normalizer.selected_features != feature_names:
        raise ValueError("normalizer selected feature list does not match discovery order")
    if (
        normalizer.partition.role is not NormalizerPartitionRole.TRAINING
        or normalizer.partition.split_id != split.split_id
        or normalizer.partition.start != split.discovery.start
        or normalizer.partition.end != split.discovery.end
        or normalizer.partition.symbols != split.discovery.symbols
    ):
        raise ValueError("normalizer training partition does not match discovery partition")
    normalizer.verify_training_rows(discovery.rows, registry)
    if discovery.partition != split.discovery or development.partition != split.development:
        raise ValueError("discovery inputs do not match the frozen split")
    if discovery.publication_sha256 != feature_publication.publication_sha256 or (
        development.publication_sha256 != feature_publication.publication_sha256
    ):
        raise ValueError("discovery input feature publication mismatch")
    expected_discovery = feature_partition_records(feature_publication, discovery.rows)
    expected_development = feature_partition_records(feature_publication, development.rows)
    if discovery.partition_records != expected_discovery or (
        development.partition_records != expected_development
    ):
        raise ValueError("discovery input feature partition hashes mismatch")
    if discovery.content_sha256 != _input_sha256(discovery) or (
        development.content_sha256 != _input_sha256(development)
    ):
        raise ValueError("discovery input row content identity mismatch")
    feature_partitions = tuple(
        sorted(
            (role, record.path, record.sha256)
            for role, records in (
                ("discovery", expected_discovery),
                ("development", expected_development),
            )
            for record in records
        )
    )
    payload: dict[str, object] = {
        "snapshot_manifest_sha256": snapshot_manifest.snapshot_sha256,
        "derived_publication_sha256": feature_publication.publication_sha256,
        "registry_sha256": registry.sha256,
        "normalizer_sha256": normalizer.artifact_sha256,
        "feature_names": list(feature_names),
        "split_sha256": split.sha256,
        "code_commit": code_commit,
        "lock_sha256": lock_sha256,
        "motif_regime_assignment_sha256": motif_regime_assignment_sha256,
        "feature_partitions": [list(item) for item in feature_partitions],
        "discovery_input_sha256": discovery.content_sha256,
        "development_input_sha256": development.content_sha256,
    }
    return DiscoveryProvenance(
        snapshot_manifest_sha256=snapshot_manifest.snapshot_sha256,
        derived_publication_sha256=feature_publication.publication_sha256,
        registry_sha256=registry.sha256,
        normalizer_sha256=normalizer.artifact_sha256,
        feature_names=feature_names,
        split_sha256=split.sha256,
        code_commit=code_commit,
        lock_sha256=lock_sha256,
        motif_regime_assignment_sha256=motif_regime_assignment_sha256,
        feature_partitions=feature_partitions,
        discovery_input_sha256=discovery.content_sha256,
        development_input_sha256=development.content_sha256,
        sha256=hashlib.sha256(_canonical_json(payload)).hexdigest(),
        _factory_token=_DISCOVERY_PROVENANCE_FACTORY,
    )


def freeze_split(
    *,
    split_id: str,
    discovery: TimePartition,
    development: TimePartition,
    holdout: TimePartition,
    asset_holdouts: Sequence[str] = (),
) -> FrozenDiscoverySplit:
    """Validate and freeze chronological and asset holdouts into a canonical policy."""

    canonical_holdouts = _validate_split_policy(
        split_id,
        discovery,
        development,
        holdout,
        tuple(asset_holdouts),
    )
    digest = _split_sha256(
        split_id,
        discovery,
        development,
        holdout,
        canonical_holdouts,
    )
    return FrozenDiscoverySplit(
        split_id=split_id,
        discovery=discovery,
        development=development,
        holdout=holdout,
        asset_holdouts=canonical_holdouts,
        sha256=digest,
    )


def _validate_split_policy(
    split_id: str,
    discovery: TimePartition,
    development: TimePartition,
    holdout: TimePartition,
    asset_holdouts: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(split_id, str) or _SAFE_ID.fullmatch(split_id) is None:
        raise ValueError("split_id must be a safe, non-empty identifier")
    partitions = (discovery, development, holdout)
    if any(not isinstance(partition, TimePartition) for partition in partitions):
        raise TypeError("split partitions must be TimePartition values")
    expected_roles = (
        (discovery, PartitionRole.DISCOVERY, "discovery"),
        (development, PartitionRole.DEVELOPMENT, "development"),
        (holdout, PartitionRole.HOLDOUT, "holdout"),
    )
    for partition, role, label in expected_roles:
        if partition.role is not role:
            raise ValueError(f"{label} partition role must be {role.value}")
    if not (discovery.end <= development.start and development.end <= holdout.start):
        raise ValueError("split partitions must be chronological and non-overlapping")
    if not (discovery.symbols == development.symbols == holdout.symbols):
        raise ValueError("split partitions must use the same symbols")
    canonical_holdouts = _canonical_names(asset_holdouts, "asset_holdouts")
    if set(canonical_holdouts) & set(discovery.symbols):
        raise ValueError("asset holdouts must not appear in temporal partition symbols")
    return canonical_holdouts


def _split_sha256(
    split_id: str,
    discovery: TimePartition,
    development: TimePartition,
    holdout: TimePartition,
    asset_holdouts: tuple[str, ...],
) -> str:
    payload = {
        "split_id": split_id,
        "discovery": discovery.to_dict(),
        "development": development.to_dict(),
        "holdout": holdout.to_dict(),
        "asset_holdouts": list(asset_holdouts),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def make_discovery_input(
    *,
    partition: TimePartition,
    rows: Iterable[FeatureRow],
    registry: FeatureRegistry,
    purpose: Literal["fit", "stability"],
    max_rows: int,
    publication_manifest: DerivedPublicationManifest | None = None,
) -> DiscoveryInput:
    """Admit bounded, identity-consistent rows without ever reading holdout rows."""

    if not isinstance(partition, TimePartition):
        raise TypeError("partition must be a TimePartition")
    _guard_partition_role(partition, purpose)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    if max_rows > MAX_DISCOVERY_INPUT_ROWS:
        raise ValueError("max_rows exceeds the discovery materialization safety bound")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")

    accepted: list[FeatureRow] = []
    dataset_version: str | None = None
    source_identity: tuple[str, ...] | None = None
    previous_key: tuple[str, str, datetime] | None = None
    for row in rows:
        if len(accepted) == max_rows:
            raise ValueError("discovery input exceeds max_rows")
        if not isinstance(row, FeatureRow):
            raise TypeError("rows must contain only FeatureRow values")
        registry.validate_row(row)
        if row.symbol not in partition.symbols:
            raise ValueError("feature row symbol is outside the partition symbols")
        if not partition.contains(row.information_cutoff):
            raise ValueError("feature row information cutoff is outside the partition")
        if dataset_version is None:
            dataset_version = row.dataset_version
        elif row.dataset_version != dataset_version:
            raise ValueError("feature row dataset version changed within discovery input")
        row_source_identity = tuple(getattr(row, field) for field in _ROW_SOURCE_IDENTITY_FIELDS)
        if source_identity is None:
            source_identity = row_source_identity
        elif row_source_identity != source_identity:
            changed = next(
                field
                for field, expected, actual in zip(
                    _ROW_SOURCE_IDENTITY_FIELDS,
                    source_identity,
                    row_source_identity,
                    strict=True,
                )
                if actual != expected
            )
            raise ValueError(f"feature row {changed} changed within discovery input")
        key = (row.symbol, row.timeframe, row.timestamp)
        if previous_key is not None:
            if key == previous_key:
                raise ValueError("duplicate feature row identity in discovery input")
            if key < previous_key:
                raise ValueError(
                    "feature rows must be ordered by symbol, timeframe, and UTC timestamp"
                )
        previous_key = key
        accepted.append(row)

    if dataset_version is None:
        raise ValueError("discovery input requires at least one feature row")
    partition_records = (
        feature_partition_records(publication_manifest, accepted)
        if publication_manifest is not None
        else ()
    )
    result = DiscoveryInput(
        partition=partition,
        rows=tuple(accepted),
        dataset_version=dataset_version,
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
        publication_sha256=(
            publication_manifest.publication_sha256 if publication_manifest is not None else None
        ),
        partition_records=partition_records,
        content_sha256=None,
        _factory_token=_DISCOVERY_INPUT_FACTORY,
    )
    object.__setattr__(result, "content_sha256", _input_sha256(result))
    return result


def _guard_partition_role(
    partition: TimePartition, purpose: Literal["fit", "stability"] | str
) -> None:
    if partition.role is PartitionRole.HOLDOUT:
        raise ValueError("final holdout data cannot be used for discovery input")
    if purpose not in ("fit", "stability"):
        raise ValueError("purpose must be 'fit' or 'stability'")
    if purpose == "fit" and partition.role is not PartitionRole.DISCOVERY:
        raise ValueError("fit purpose requires the discovery partition")
    if purpose == "stability" and partition.role is not PartitionRole.DEVELOPMENT:
        raise ValueError("stability purpose requires the development partition")


def _canonical_names(
    values: Sequence[str], label: str, *, require_non_empty: bool = False
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of names")
    names = tuple(values)
    if require_non_empty and not names:
        raise ValueError(f"{label} must be non-empty")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} must contain unique names")
    return tuple(sorted(names))


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _input_sha256(input_value: DiscoveryInput) -> str:
    payload: dict[str, object] = {
        "partition": input_value.partition.to_dict(),
        "dataset_version": input_value.dataset_version,
        "feature_set_id": input_value.feature_set_id,
        "registry_id": input_value.registry_id,
        "publication_sha256": input_value.publication_sha256,
        "partition_records": [
            {"path": record.path, "sha256": record.sha256}
            for record in input_value.partition_records
        ],
        "rows": [json.loads(row.canonical_json()) for row in input_value.rows],
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()
