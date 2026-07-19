"""Atomic, bounded publication for outcome-blind feature and event datasets."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import secrets
import shutil
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    regular_file_matches,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.builder import (
    FeatureBuildBatch,
    FeatureBuildBatchLease,
    FeatureBuildBatchMetadata,
)
from market_structure_lab.features.registry import (
    FeatureRegistry,
    FeatureRegistrySnapshot,
    FeatureValueKind,
    discovery_dependency_contract_sha256,
    validate_discovery_dependency_contract,
    validate_discovery_field_name,
)

MANIFEST_NAME = "manifest.json"
SUCCESS_NAME = "_SUCCESS"
LEAKAGE_AUDIT_NAME = "leakage-audit.json"
_IDENTITY_NAME = ".publication-identity.json"
_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATASET_ID = re.compile(r"^DS-[0-9]{6}$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
PublicationKind = Literal["features", "events"]
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_LEAKAGE_AUDIT_BYTES = 4 * 1024 * 1024
_MAX_PUBLICATION_ENTRIES = 1_000_000
_MAX_AUDITED_FEATURES = 10_000
_MAX_REVIEWER_ID_LENGTH = 127
_MAX_PUBLICATION_LOCK_BYTES = 4096


class PublicationBusyError(RuntimeError):
    """A publication path already has an active or operator-unresolved owner."""


class PublicationCleanupError(RuntimeError):
    """Owner staging could not be removed, so its recovery lock remains active."""


class LeakageNegativePattern(str, Enum):
    INNOCUOUS_FUTURE_RETURN = "innocuous_future_return"
    FULL_PERIOD_NORMALIZATION = "full_period_normalization"
    CENTERED_WINDOW = "centered_window"
    GLOBAL_MIN_MAX = "global_min_max"
    FUTURE_DEPENDENT_LABEL = "future_dependent_label"
    AFTER_DECLARED_CUTOFF_TIMESTAMP = "after_declared_cutoff_timestamp"


@dataclass(frozen=True, slots=True)
class LeakageAuditApproval:
    """Independent review evidence required before feature rows may be consumed."""

    schema_version: int
    feature_registry_sha256: str
    reviewer_id: str
    review_artifact_sha256: str
    field_test_evidence: tuple[tuple[str, str], ...]
    negative_test_evidence: tuple[tuple[LeakageNegativePattern, str], ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported leakage audit approval schema")
        _require_sha256(self.feature_registry_sha256, "feature_registry_sha256")
        _safe_component(self.reviewer_id, "reviewer_id")
        if len(self.reviewer_id) > _MAX_REVIEWER_ID_LENGTH:
            raise ValueError("reviewer_id is too long")
        _require_sha256(self.review_artifact_sha256, "review_artifact_sha256")
        if not isinstance(self.field_test_evidence, tuple):
            raise TypeError("field_test_evidence must be a tuple")
        if len(self.field_test_evidence) > _MAX_AUDITED_FEATURES:
            raise ValueError("field_test_evidence exceeds the bounded feature limit")
        field_evidence = tuple(sorted(self.field_test_evidence))
        if not field_evidence or len({name for name, _ in field_evidence}) != len(field_evidence):
            raise ValueError("field_test_evidence must uniquely cover audited feature fields")
        for name, digest in field_evidence:
            validate_discovery_field_name(name, field="audited feature field")
            _require_sha256(digest, f"field test evidence for {name}")
        if not isinstance(self.negative_test_evidence, tuple):
            raise TypeError("negative_test_evidence must be a tuple")
        if len(self.negative_test_evidence) != len(LeakageNegativePattern):
            raise ValueError("negative_test_evidence must cover every required leakage pattern")
        if any(
            not isinstance(pattern, LeakageNegativePattern)
            for pattern, _ in self.negative_test_evidence
        ):
            raise TypeError("negative_test_evidence uses an unsupported pattern")
        negative_evidence = tuple(
            sorted(self.negative_test_evidence, key=lambda item: item[0].value)
        )
        if tuple(item[0] for item in negative_evidence) != tuple(
            sorted(LeakageNegativePattern, key=lambda item: item.value)
        ):
            raise ValueError("negative_test_evidence must cover every required leakage pattern")
        for pattern, digest in negative_evidence:
            if not isinstance(pattern, LeakageNegativePattern):
                raise TypeError("negative_test_evidence uses an unsupported pattern")
            _require_sha256(digest, f"negative test evidence for {pattern.value}")
        object.__setattr__(self, "feature_registry_sha256", self.feature_registry_sha256.lower())
        object.__setattr__(self, "review_artifact_sha256", self.review_artifact_sha256.lower())
        object.__setattr__(self, "field_test_evidence", field_evidence)
        object.__setattr__(self, "negative_test_evidence", negative_evidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "feature_registry_sha256": self.feature_registry_sha256,
            "reviewer_id": self.reviewer_id,
            "review_artifact_sha256": self.review_artifact_sha256,
            "field_test_evidence": dict(self.field_test_evidence),
            "negative_test_evidence": {
                pattern.value: digest for pattern, digest in self.negative_test_evidence
            },
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureLeakageFieldEvidence:
    feature_name: str
    source_fields: tuple[str, ...]
    trailing_window: str
    observable_cutoff_rule: str
    warm_up_observations: int
    normalization_requirement: str
    future_outcome_prohibited: bool
    builder_id: str
    builder_version: str
    dependency_contract_sha256: str
    tested_contract_sha256: str

    def __post_init__(self) -> None:
        validate_discovery_field_name(self.feature_name, field="leakage receipt feature")
        if not self.source_fields or self.source_fields != tuple(sorted(set(self.source_fields))):
            raise ValueError("receipt source_fields must be non-empty, unique, and sorted")
        if self.warm_up_observations < 0:
            raise ValueError("receipt warm_up_observations must be non-negative")
        if self.future_outcome_prohibited is not True:
            raise ValueError("receipt must retain the explicit future/outcome prohibition")
        for value, label in (
            (self.trailing_window, "trailing_window"),
            (self.observable_cutoff_rule, "observable_cutoff_rule"),
            (self.normalization_requirement, "normalization_requirement"),
            (self.builder_id, "builder_id"),
            (self.builder_version, "builder_version"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"receipt {label} must be non-empty")
        _require_sha256(self.dependency_contract_sha256, "dependency_contract_sha256")
        _require_sha256(self.tested_contract_sha256, "tested_contract_sha256")
        validate_discovery_dependency_contract(
            feature_name=self.feature_name,
            source_fields=self.source_fields,
            trailing_window=self.trailing_window,
            observable_cutoff_rule=self.observable_cutoff_rule,
            warm_up_observations=self.warm_up_observations,
            normalization_requirement=self.normalization_requirement,
            future_outcome_prohibited=self.future_outcome_prohibited,
            builder_id=self.builder_id,
            builder_version=self.builder_version,
        )
        expected = discovery_dependency_contract_sha256(
            source_fields=self.source_fields,
            trailing_window=self.trailing_window,
            observable_cutoff_rule=self.observable_cutoff_rule,
            warm_up_observations=self.warm_up_observations,
            normalization_requirement=self.normalization_requirement,
            future_outcome_prohibited=self.future_outcome_prohibited,
            builder_id=self.builder_id,
            builder_version=self.builder_version,
        )
        if self.dependency_contract_sha256 != expected:
            raise ValueError("receipt field dependency contract checksum mismatch")


@dataclass(frozen=True, slots=True)
class LeakageAuditReceipt:
    schema_version: int
    feature_registry_sha256: str
    dependency_contract_sha256: str
    approval_sha256: str
    publication_identity_sha256: str
    builder_output_sha256: str
    published_content_sha256: str
    builder_output_row_count: int
    reviewer_id: str
    review_artifact_sha256: str
    negative_test_evidence: tuple[tuple[LeakageNegativePattern, str], ...]
    fields: tuple[FeatureLeakageFieldEvidence, ...]
    metadata_proves_no_leakage: Literal[False]
    residual_manual_review_required: Literal[True]
    receipt_sha256: str

    @classmethod
    def create(
        cls,
        *,
        feature_registry_sha256: str,
        dependency_contract_sha256: str,
        approval_sha256: str,
        publication_identity_sha256: str,
        builder_output_sha256: str,
        published_content_sha256: str,
        builder_output_row_count: int,
        reviewer_id: str,
        review_artifact_sha256: str,
        negative_test_evidence: tuple[tuple[LeakageNegativePattern, str], ...],
        fields: tuple[FeatureLeakageFieldEvidence, ...],
    ) -> LeakageAuditReceipt:
        values: dict[str, object] = {
            "schema_version": 2,
            "feature_registry_sha256": feature_registry_sha256,
            "dependency_contract_sha256": dependency_contract_sha256,
            "approval_sha256": approval_sha256,
            "publication_identity_sha256": publication_identity_sha256,
            "builder_output_sha256": builder_output_sha256,
            "published_content_sha256": published_content_sha256,
            "builder_output_row_count": builder_output_row_count,
            "reviewer_id": reviewer_id,
            "review_artifact_sha256": review_artifact_sha256,
            "negative_test_evidence": negative_test_evidence,
            "fields": fields,
            "metadata_proves_no_leakage": False,
            "residual_manual_review_required": True,
        }
        logical = {
            **values,
            "negative_test_evidence": {
                pattern.value: digest for pattern, digest in negative_test_evidence
            },
            "fields": [asdict(item) for item in fields],
        }
        return cls(
            **values,  # type: ignore[arg-type]
            receipt_sha256=hashlib.sha256(_canonical_json(logical)).hexdigest(),
        )

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("unsupported leakage audit receipt schema")
        for value, label in (
            (self.feature_registry_sha256, "feature_registry_sha256"),
            (self.dependency_contract_sha256, "dependency_contract_sha256"),
            (self.approval_sha256, "approval_sha256"),
            (self.publication_identity_sha256, "publication_identity_sha256"),
            (self.builder_output_sha256, "builder_output_sha256"),
            (self.published_content_sha256, "published_content_sha256"),
            (self.review_artifact_sha256, "review_artifact_sha256"),
            (self.receipt_sha256, "receipt_sha256"),
        ):
            _require_sha256(value, label)
        if self.builder_output_row_count < 0:
            raise ValueError("builder_output_row_count must be non-negative")
        _safe_component(self.reviewer_id, "reviewer_id")
        if len(self.reviewer_id) > _MAX_REVIEWER_ID_LENGTH:
            raise ValueError("reviewer_id is too long")
        if len(self.fields) > _MAX_AUDITED_FEATURES:
            raise ValueError("receipt fields exceed the bounded feature limit")
        if not self.fields:
            raise ValueError("receipt fields cannot be empty")
        if tuple(item.feature_name for item in self.fields) != tuple(
            sorted({item.feature_name for item in self.fields})
        ):
            raise ValueError("receipt fields must be unique and sorted")
        if tuple(pattern for pattern, _ in self.negative_test_evidence) != tuple(
            sorted(LeakageNegativePattern, key=lambda item: item.value)
        ):
            raise ValueError("receipt must retain every required negative leakage test")
        for pattern, digest in self.negative_test_evidence:
            if not isinstance(pattern, LeakageNegativePattern):
                raise TypeError("receipt uses an unsupported negative leakage pattern")
            _require_sha256(digest, f"negative test evidence for {pattern.value}")
        if self.metadata_proves_no_leakage is not False:
            raise ValueError("static metadata cannot prove absence of leakage")
        if self.residual_manual_review_required is not True:
            raise ValueError("leakage receipts must retain the manual-review limitation")
        aggregate = hashlib.sha256(
            _canonical_json(
                {item.feature_name: item.dependency_contract_sha256 for item in self.fields}
            )
        ).hexdigest()
        if self.dependency_contract_sha256 != aggregate:
            raise ValueError("leakage receipt aggregate dependency checksum mismatch")
        expected = hashlib.sha256(_canonical_json(self.logical_dict())).hexdigest()
        if self.receipt_sha256 != expected:
            raise ValueError("leakage audit receipt checksum mismatch")

    def logical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "feature_registry_sha256": self.feature_registry_sha256,
            "dependency_contract_sha256": self.dependency_contract_sha256,
            "approval_sha256": self.approval_sha256,
            "publication_identity_sha256": self.publication_identity_sha256,
            "builder_output_sha256": self.builder_output_sha256,
            "published_content_sha256": self.published_content_sha256,
            "builder_output_row_count": self.builder_output_row_count,
            "reviewer_id": self.reviewer_id,
            "review_artifact_sha256": self.review_artifact_sha256,
            "negative_test_evidence": {
                pattern.value: digest for pattern, digest in self.negative_test_evidence
            },
            "fields": [asdict(item) for item in self.fields],
            "metadata_proves_no_leakage": self.metadata_proves_no_leakage,
            "residual_manual_review_required": self.residual_manual_review_required,
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                {**self.logical_dict(), "receipt_sha256": self.receipt_sha256},
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n"
        )


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
    leakage_audit_approval_sha256: str
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
            "leakage_audit_approval_sha256",
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

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(asdict(self))).hexdigest()


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
    feature_builder_output_sha256: str | None
    feature_dependency_contract_sha256: str | None
    leakage_audit_receipt_sha256: str | None
    leakage_audit_artifact_sha256: str | None
    partitions: tuple[DerivedPartitionRecord, ...]
    content_sha256: str
    publication_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 3:
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
        if self.publication_kind == "features":
            if self.feature_builder_output_sha256 is None:
                raise ValueError("feature publication requires a builder output checksum")
            if self.feature_dependency_contract_sha256 is None:
                raise ValueError("feature publication requires a dependency contract checksum")
            if self.leakage_audit_receipt_sha256 is None:
                raise ValueError("feature publication requires a leakage receipt checksum")
            if self.leakage_audit_artifact_sha256 is None:
                raise ValueError("feature publication requires a leakage artifact checksum")
        elif (
            self.leakage_audit_receipt_sha256 is not None
            or self.leakage_audit_artifact_sha256 is not None
            or self.feature_builder_output_sha256 is not None
            or self.feature_dependency_contract_sha256 is not None
        ):
            raise ValueError("event publication cannot carry a feature leakage receipt")
        for optional_value, label in (
            (self.feature_builder_output_sha256, "feature_builder_output_sha256"),
            (
                self.feature_dependency_contract_sha256,
                "feature_dependency_contract_sha256",
            ),
            (self.leakage_audit_receipt_sha256, "leakage_audit_receipt_sha256"),
            (self.leakage_audit_artifact_sha256, "leakage_audit_artifact_sha256"),
        ):
            if optional_value is not None and _SHA256.fullmatch(optional_value) is None:
                raise ValueError(f"{label} must be a SHA-256 hex digest or None")

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
            "feature_builder_output_sha256": self.feature_builder_output_sha256,
            "feature_dependency_contract_sha256": self.feature_dependency_contract_sha256,
            "leakage_audit_receipt_sha256": self.leakage_audit_receipt_sha256,
            "leakage_audit_artifact_sha256": self.leakage_audit_artifact_sha256,
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


@dataclass(frozen=True, slots=True)
class _PublicationOwnership:
    lock_path: Path
    staging: Path
    lock_payload: bytes


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
    batch: FeatureBuildBatch,
    *,
    output_root: str | Path,
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistry,
    leakage_audit: LeakageAuditApproval,
    max_rows_per_part: int = 100_000,
) -> DerivedPublicationManifest:
    """Publish registered feature rows with a fixed bounded Parquet row buffer."""

    if not isinstance(batch, FeatureBuildBatch):
        raise TypeError("feature publication requires a producer-bound FeatureBuildBatch")
    registry_snapshot = registry.snapshot()
    _validate_registry_identity(identity, registry_snapshot)
    _validate_leakage_audit_approval(identity, registry_snapshot, leakage_audit)
    batch_lease, batch_metadata = _validate_feature_build_batch(batch, registry_snapshot)
    _preflight_leakage_receipt(identity, registry_snapshot, leakage_audit, batch_metadata)
    if identity.normalizer_artifact_sha256 is None:
        raise ValueError("feature publication requires a normalizer artifact SHA-256")
    schema = _feature_schema(registry_snapshot)

    def converted() -> Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]]:
        for row in batch.iter_rows(batch_lease):
            registry_snapshot.validate_row(row)
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
        feature_names=registry_snapshot.names,
        registry_snapshot=registry_snapshot,
        leakage_audit=leakage_audit,
        feature_batch_metadata=batch_metadata,
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

    registry_snapshot = registry.snapshot()
    _validate_registry_identity(identity, registry_snapshot)
    schema = _event_schema(registry_snapshot)
    expected_fields = {item.name for item in fields(MarketEvent)}
    counter = _EventEvidenceCounter()

    def converted() -> Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]]:
        previous_start: tuple[str, str, int, datetime] | None = None
        for event in events:
            if not isinstance(event, MarketEvent):
                raise TypeError("events must contain only MarketEvent values")
            if {item.name for item in fields(event)} != expected_fields:
                raise ValueError("MarketEvent schema does not match the frozen publication schema")
            _validate_event_identity(event, identity, registry_snapshot)
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
        feature_names=registry_snapshot.names,
        event_counter=counter,
    )


def read_derived_manifest(path: str | Path) -> DerivedPublicationManifest:
    try:
        payload = json.loads(read_bounded_regular(Path(path), _MAX_MANIFEST_BYTES))
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
            feature_builder_output_sha256=payload["feature_builder_output_sha256"],
            feature_dependency_contract_sha256=payload["feature_dependency_contract_sha256"],
            leakage_audit_receipt_sha256=payload["leakage_audit_receipt_sha256"],
            leakage_audit_artifact_sha256=payload["leakage_audit_artifact_sha256"],
            partitions=tuple(DerivedPartitionRecord(**item) for item in payload["partitions"]),
            content_sha256=str(payload["content_sha256"]),
            publication_sha256=str(payload["publication_sha256"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid derived publication manifest: {path}") from error
    return manifest


def read_leakage_audit_receipt(path: str | Path) -> LeakageAuditReceipt:
    try:
        payload = json.loads(read_bounded_regular(Path(path), _MAX_LEAKAGE_AUDIT_BYTES))
        receipt = LeakageAuditReceipt(
            schema_version=int(payload["schema_version"]),
            feature_registry_sha256=str(payload["feature_registry_sha256"]),
            dependency_contract_sha256=str(payload["dependency_contract_sha256"]),
            approval_sha256=str(payload["approval_sha256"]),
            publication_identity_sha256=str(payload["publication_identity_sha256"]),
            builder_output_sha256=str(payload["builder_output_sha256"]),
            published_content_sha256=str(payload["published_content_sha256"]),
            builder_output_row_count=int(payload["builder_output_row_count"]),
            reviewer_id=str(payload["reviewer_id"]),
            review_artifact_sha256=str(payload["review_artifact_sha256"]),
            negative_test_evidence=tuple(
                sorted(
                    (
                        LeakageNegativePattern(pattern),
                        str(digest),
                    )
                    for pattern, digest in payload["negative_test_evidence"].items()
                )
            ),
            fields=tuple(
                FeatureLeakageFieldEvidence(
                    **{
                        **item,
                        "source_fields": tuple(item["source_fields"]),
                    }
                )
                for item in payload["fields"]
            ),
            metadata_proves_no_leakage=payload["metadata_proves_no_leakage"],
            residual_manual_review_required=payload["residual_manual_review_required"],
            receipt_sha256=str(payload["receipt_sha256"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid leakage audit receipt: {path}") from error
    return receipt


def verify_derived_publication(
    directory: str | Path,
    manifest: DerivedPublicationManifest | None = None,
    *,
    require_success: bool = True,
) -> None:
    root = Path(directory)
    require_regular_directory(root)
    active = read_derived_manifest(root / MANIFEST_NAME)
    if manifest is not None and manifest != active:
        raise ValueError("supplied manifest does not exactly match the on-disk manifest")
    logical_hash = hashlib.sha256(_canonical_json(active.logical_dict())).hexdigest()
    if active.publication_sha256 != logical_hash:
        raise ValueError("derived publication manifest logical hash mismatch")
    success = root / SUCCESS_NAME
    if require_success and not regular_file_matches(success, f"{logical_hash}\n".encode("utf-8")):
        raise ValueError("derived publication completion marker is absent or invalid")
    expected = {item.path for item in active.partitions}
    actual_files = set(bounded_regular_files(root, maximum=_MAX_PUBLICATION_ENTRIES))
    actual = {path for path in actual_files if path.endswith(".parquet")}
    if actual != expected:
        raise ValueError("publication contains unmanifested or missing partitions")
    expected_files = {*expected, MANIFEST_NAME}
    if active.publication_kind == "features":
        expected_files.add(LEAKAGE_AUDIT_NAME)
    if success.exists():
        expected_files.add(SUCCESS_NAME)
    if not require_success and (root / _IDENTITY_NAME).exists():
        expected_files.add(_IDENTITY_NAME)
    if actual_files != expected_files:
        raise ValueError("publication contains an unmanifested control artifact")
    if active.publication_kind == "features":
        receipt_path = root / LEAKAGE_AUDIT_NAME
        if sha256_regular(receipt_path) != active.leakage_audit_artifact_sha256:
            raise ValueError("leakage audit artifact checksum mismatch")
        receipt = read_leakage_audit_receipt(receipt_path)
        if receipt.receipt_sha256 != active.leakage_audit_receipt_sha256:
            raise ValueError("leakage audit receipt checksum mismatch")
        if (
            receipt.feature_registry_sha256 != active.identity.feature_registry_sha256
            or receipt.dependency_contract_sha256 != active.feature_dependency_contract_sha256
            or receipt.approval_sha256 != active.identity.leakage_audit_approval_sha256
            or receipt.publication_identity_sha256 != active.identity.sha256
            or receipt.builder_output_sha256 != active.feature_builder_output_sha256
            or receipt.published_content_sha256 != active.content_sha256
            or receipt.builder_output_row_count != active.row_count
        ):
            raise ValueError("leakage audit receipt is not bound to the publication")
    counted = 0
    schema = dict(active.parquet_schema)
    for partition in active.partitions:
        path = root / _validated_partition_path(partition.path)
        if sha256_regular(path) != partition.sha256:
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


def feature_partition_records(
    manifest: DerivedPublicationManifest,
    rows: Sequence[FeatureRow],
) -> tuple[DerivedPartitionRecord, ...]:
    """Return the exact checksum records containing the supplied stable row IDs."""

    if manifest.publication_kind != "features":
        raise ValueError("feature rows require a feature publication manifest")
    selected: dict[str, DerivedPartitionRecord] = {}
    for row in rows:
        matches = tuple(
            record
            for record in manifest.partitions
            if record.path.startswith(f"symbol={row.symbol}/timeframe={row.timeframe}/")
            and _parse_utc(record.min_timestamp)
            <= row.timestamp
            <= _parse_utc(record.max_timestamp)
        )
        if len(matches) != 1:
            raise ValueError("feature row is not covered by exactly one publication partition")
        selected[matches[0].path] = matches[0]
    return tuple(selected[path] for path in sorted(selected))


def verify_published_feature_rows(
    directory: str | Path,
    manifest: DerivedPublicationManifest,
    rows: Sequence[FeatureRow],
    registry: FeatureRegistry,
) -> tuple[DerivedPartitionRecord, ...]:
    """Verify bounded supplied rows are byte-backed by checksum-verified Parquet partitions."""

    verify_derived_publication(directory, manifest)
    records = feature_partition_records(manifest, rows)
    if not rows:
        return records
    root = Path(directory)
    paths = [root / record.path for record in records]
    filters: pl.Expr | None = None
    grouped: dict[tuple[str, str], list[datetime]] = {}
    for row in rows:
        grouped.setdefault((row.symbol, row.timeframe), []).append(row.timestamp)
    for (symbol, timeframe), timestamps in grouped.items():
        expression = (
            (pl.col("symbol") == symbol)
            & (pl.col("timeframe") == timeframe)
            & pl.col("timestamp").is_in(timestamps)
        )
        filters = expression if filters is None else filters | expression
    if filters is None:
        raise RuntimeError("feature row verification filter was not constructed")
    frame = pl.scan_parquet(paths).filter(filters).collect(engine="streaming")
    actual: list[FeatureRow] = []
    for payload in frame.iter_rows(named=True):
        actual.append(
            FeatureRow(
                timestamp=cast(datetime, payload["timestamp"]),
                information_cutoff=cast(datetime, payload["information_cutoff"]),
                symbol=cast(str, payload["symbol"]),
                timeframe=cast(str, payload["timeframe"]),
                segment_id=cast(int, payload["segment_id"]),
                dataset_version=cast(str, payload["dataset_version"]),
                config_version=cast(str, payload["config_version"]),
                profile_version=cast(str, payload["profile_version"]),
                window_policy_id=cast(str, payload["window_policy_id"]),
                feature_set_id=cast(str, payload["feature_set_id"]),
                registry_id=cast(str, payload["registry_id"]),
                values={name: payload[name] for name in registry.names},
            )
        )
    expected_json = tuple(sorted(row.canonical_json() for row in rows))
    actual_json = tuple(sorted(row.canonical_json() for row in actual))
    if actual_json != expected_json:
        raise ValueError("published feature row content does not match discovery input")
    return records


def _existing_publication(
    final: Path,
    rows: Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]],
    *,
    identity: DerivedPublicationIdentity,
    kind: PublicationKind,
    max_rows_per_part: int,
    feature_names: tuple[str, ...],
    event_counter: _EventEvidenceCounter | None,
) -> DerivedPublicationManifest | None:
    if not final.exists():
        return None
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


def _acquire_publication_ownership(base: Path, kind: PublicationKind) -> _PublicationOwnership:
    """Atomically own one unique staging tree; crash locks require operator recovery."""

    base.mkdir(parents=True, exist_ok=True)
    require_regular_directory(base)
    token = secrets.token_hex(16)
    lock_path = base / f".{kind}.publish.lock"
    staging = base / f".{kind}.partial.{token}"
    lock_payload = (
        _canonical_json(
            {
                "schema_version": 1,
                "kind": kind,
                "owner_id": token,
                "staging_name": staging.name,
                "created_at": _iso_utc(datetime.now(UTC)),
                "recovery": "verify owner is inactive, then remove lock and named staging",
            }
        )
        + b"\n"
    )
    if len(lock_payload) > _MAX_PUBLICATION_LOCK_BYTES:
        raise RuntimeError("publication ownership metadata exceeds its bounded limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as error:
        raise PublicationBusyError(
            "derived publication has an active or stale owner lock; verify the owner is inactive "
            "before manual recovery"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(lock_payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        lock_path.unlink(missing_ok=True)
        raise
    try:
        staging.mkdir(parents=False, exist_ok=False)
    except FileExistsError as error:
        lock_path.unlink(missing_ok=True)
        raise PublicationBusyError(
            "derived publication unique staging path already exists; manual recovery is required"
        ) from error
    except Exception:
        lock_path.unlink(missing_ok=True)
        raise
    return _PublicationOwnership(
        lock_path=lock_path,
        staging=staging,
        lock_payload=lock_payload,
    )


def _release_publication_ownership(ownership: _PublicationOwnership) -> None:
    """Remove only this owner's staging and lock during normal process unwinding."""

    try:
        if path_exists_no_follow(ownership.staging):
            shutil.rmtree(ownership.staging)
        if path_exists_no_follow(ownership.staging):
            raise OSError("publication staging removal did not remove the owner tree")
    except (OSError, RuntimeError) as error:
        raise PublicationCleanupError(
            "publication staging cleanup failed; recovery-required owner lock retained"
        ) from error
    try:
        owns_lock = regular_file_matches(ownership.lock_path, ownership.lock_payload)
    except (FileNotFoundError, OSError, RuntimeError):
        owns_lock = False
    if owns_lock:
        ownership.lock_path.unlink()


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
    registry_snapshot: FeatureRegistrySnapshot | None = None,
    leakage_audit: LeakageAuditApproval | None = None,
    feature_batch_metadata: FeatureBuildBatchMetadata | None = None,
) -> DerivedPublicationManifest:
    if isinstance(max_rows_per_part, bool) or not isinstance(max_rows_per_part, int):
        raise TypeError("max_rows_per_part must be an integer")
    if max_rows_per_part < 1:
        raise ValueError("max_rows_per_part must be positive")
    final, base = _publication_paths(output_root, identity, kind)
    active = _existing_publication(
        final,
        rows=rows,
        identity=identity,
        kind=kind,
        max_rows_per_part=max_rows_per_part,
        feature_names=feature_names,
        event_counter=event_counter,
    )
    if active is not None:
        return active
    ownership = _acquire_publication_ownership(base, kind)
    try:
        active = _existing_publication(
            final,
            rows,
            identity=identity,
            kind=kind,
            max_rows_per_part=max_rows_per_part,
            feature_names=feature_names,
            event_counter=event_counter,
        )
        if active is not None:
            return active
        return _publish_owned(
            rows,
            final=final,
            staging=ownership.staging,
            identity=identity,
            kind=kind,
            schema=schema,
            max_rows_per_part=max_rows_per_part,
            feature_names=feature_names,
            event_counter=event_counter,
            registry_snapshot=registry_snapshot,
            leakage_audit=leakage_audit,
            feature_batch_metadata=feature_batch_metadata,
        )
    finally:
        _release_publication_ownership(ownership)


def _publish_owned(
    rows: Iterable[tuple[dict[str, object], datetime, tuple[object, ...]]],
    *,
    final: Path,
    staging: Path,
    identity: DerivedPublicationIdentity,
    kind: PublicationKind,
    schema: pl.Schema,
    max_rows_per_part: int,
    feature_names: tuple[str, ...],
    event_counter: _EventEvidenceCounter | None,
    registry_snapshot: FeatureRegistrySnapshot | None,
    leakage_audit: LeakageAuditApproval | None,
    feature_batch_metadata: FeatureBuildBatchMetadata | None,
) -> DerivedPublicationManifest:
    identity_path = staging / _IDENTITY_NAME
    identity_text = _json_text(asdict(identity)) + "\n"
    _atomic_write_text(identity_path, identity_text)

    records: list[DerivedPartitionRecord] = []
    buffer: list[dict[str, object]] = []
    current_partition: tuple[str, str, int, int] | None = None
    part_numbers: Counter[tuple[str, str, int, int]] = Counter()
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
        symbol, timeframe, year, month = current_partition
        relative = (
            Path(f"symbol={_safe_component(symbol, 'symbol')}")
            / f"timeframe={_safe_component(timeframe, 'timeframe')}"
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
            cast(str, payload["timeframe"]),
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
    content_sha256 = content_digest.hexdigest()
    receipt_sha256: str | None = None
    receipt_artifact_sha256: str | None = None
    if kind == "features":
        if registry_snapshot is None or leakage_audit is None or feature_batch_metadata is None:
            raise RuntimeError("feature publication lost its leakage audit contract")
        receipt = _build_leakage_audit_receipt(
            identity=identity,
            registry=registry_snapshot,
            approval=leakage_audit,
            builder_output_sha256=feature_batch_metadata.content_sha256,
            published_content_sha256=content_sha256,
            builder_output_row_count=total,
        )
        _atomic_write_text(
            staging / LEAKAGE_AUDIT_NAME,
            receipt.to_json(),
            maximum_bytes=_MAX_LEAKAGE_AUDIT_BYTES,
        )
        receipt_sha256 = receipt.receipt_sha256
        receipt_artifact_sha256 = _sha256_file(staging / LEAKAGE_AUDIT_NAME)
    schema_pairs = tuple(sorted((name, str(dtype)) for name, dtype in schema.items()))
    provisional = DerivedPublicationManifest(
        schema_version=3,
        publication_kind=kind,
        identity=identity,
        row_count=total,
        min_timestamp=_iso_utc(minimum) if minimum is not None else None,
        max_timestamp=_iso_utc(maximum) if maximum is not None else None,
        max_rows_per_part=max_rows_per_part,
        max_buffered_rows=max_buffered,
        parquet_schema=schema_pairs,
        evidence=evidence,
        feature_builder_output_sha256=(
            feature_batch_metadata.content_sha256 if feature_batch_metadata is not None else None
        ),
        feature_dependency_contract_sha256=(
            registry_snapshot.dependency_contract_sha256 if registry_snapshot is not None else None
        ),
        leakage_audit_receipt_sha256=receipt_sha256,
        leakage_audit_artifact_sha256=receipt_artifact_sha256,
        partitions=ordered,
        content_sha256=content_sha256,
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
    identity_path.unlink()
    verify_derived_publication(staging, manifest)
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.replace(final)
    except FileExistsError:
        raise FileExistsError("derived publication was published concurrently") from None
    return manifest


def _feature_schema(registry: FeatureRegistrySnapshot) -> pl.Schema:
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


def _event_schema(registry: FeatureRegistrySnapshot) -> pl.Schema:
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
    identity: DerivedPublicationIdentity, registry: FeatureRegistrySnapshot
) -> None:
    registry.audit_discovery()
    if identity.feature_set_id != registry.feature_set_id:
        raise ValueError("publication feature_set_id does not match the registry")
    if identity.feature_registry_sha256 != registry.sha256:
        raise ValueError("publication feature_registry_sha256 does not match the registry")


def _validate_leakage_audit_approval(
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistrySnapshot,
    approval: LeakageAuditApproval,
) -> None:
    if not isinstance(approval, LeakageAuditApproval):
        raise TypeError("leakage_audit must be a LeakageAuditApproval")
    if approval.feature_registry_sha256 != registry.sha256:
        raise ValueError("leakage audit approval targets a different feature registry")
    if identity.leakage_audit_approval_sha256 != approval.sha256:
        raise ValueError("publication identity does not bind the leakage audit approval")
    if tuple(name for name, _ in approval.field_test_evidence) != registry.names:
        raise ValueError("leakage audit field tests must exactly cover the registry")


def _validate_feature_build_batch(
    batch: FeatureBuildBatch,
    registry: FeatureRegistrySnapshot,
) -> tuple[FeatureBuildBatchLease, FeatureBuildBatchMetadata]:
    lease = FeatureBuildBatch.verify_issued(batch)
    metadata = FeatureBuildBatch.resolve_lease(batch, lease)
    if (
        metadata.feature_registry_sha256 != registry.sha256
        or metadata.registry_id != registry.registry_id
        or metadata.dependency_contract_sha256 != registry.dependency_contract_sha256
    ):
        raise ValueError("feature build batch does not match the registry dependency identity")
    if any(
        definition.builder_id != metadata.builder_id
        or definition.builder_version != metadata.builder_version
        for definition in registry.definitions
    ):
        raise ValueError("feature build batch producer does not match every field contract")
    return lease, metadata


def _preflight_leakage_receipt(
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistrySnapshot,
    approval: LeakageAuditApproval,
    metadata: FeatureBuildBatchMetadata,
) -> None:
    receipt = _build_leakage_audit_receipt(
        identity=identity,
        registry=registry,
        approval=approval,
        builder_output_sha256=metadata.content_sha256,
        published_content_sha256="0" * 64,
        builder_output_row_count=metadata.row_count,
    )
    if len(receipt.to_json().encode("utf-8")) > _MAX_LEAKAGE_AUDIT_BYTES:
        raise ValueError("leakage audit receipt exceeds the bounded artifact limit")


def _build_leakage_audit_receipt(
    *,
    identity: DerivedPublicationIdentity,
    registry: FeatureRegistrySnapshot,
    approval: LeakageAuditApproval,
    builder_output_sha256: str,
    published_content_sha256: str,
    builder_output_row_count: int,
) -> LeakageAuditReceipt:
    tested = dict(approval.field_test_evidence)
    return LeakageAuditReceipt.create(
        feature_registry_sha256=registry.sha256,
        dependency_contract_sha256=registry.dependency_contract_sha256,
        approval_sha256=approval.sha256,
        publication_identity_sha256=identity.sha256,
        builder_output_sha256=builder_output_sha256,
        published_content_sha256=published_content_sha256,
        builder_output_row_count=builder_output_row_count,
        reviewer_id=approval.reviewer_id,
        review_artifact_sha256=approval.review_artifact_sha256,
        negative_test_evidence=approval.negative_test_evidence,
        fields=tuple(
            FeatureLeakageFieldEvidence(
                feature_name=definition.name,
                source_fields=definition.source_fields,
                trailing_window=definition.trailing_window,
                observable_cutoff_rule=definition.observable_cutoff_rule.value,
                warm_up_observations=definition.required_prior_observations,
                normalization_requirement=definition.normalization_requirement.value,
                future_outcome_prohibited=definition.future_outcome_prohibited,
                builder_id=definition.builder_id,
                builder_version=definition.builder_version,
                dependency_contract_sha256=definition.dependency_contract_sha256,
                tested_contract_sha256=tested[definition.name],
            )
            for definition in registry.definitions
        ),
    )


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
    event: Any, identity: DerivedPublicationIdentity, registry: FeatureRegistrySnapshot
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
    return base / kind, base


def _audit_names(values: Mapping[str, object]) -> None:
    for name, value in values.items():
        validate_discovery_field_name(name, field="publication field")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite value in field: {name}")


def _timestamp_column(kind: PublicationKind) -> str:
    return "timestamp" if kind == "features" else "information_cutoff"


def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or _PATH_COMPONENT.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe path component")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


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


def _atomic_write_text(
    path: Path,
    value: str,
    *,
    maximum_bytes: int | None = None,
) -> None:
    payload = value.encode("utf-8")
    if maximum_bytes is not None and len(payload) > maximum_bytes:
        raise ValueError("atomic text artifact exceeds the bounded byte limit")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
