"""Bounded, outcome-blind numeric matrices for discovery models."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence

from market_structure_lab.discovery.splits import DiscoveryInput, PartitionRole
from market_structure_lab.features.normalization import RobustNormalizer
from market_structure_lab.features.models import FeatureRow, timeframe_duration
from market_structure_lab.features.registry import (
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
)

_SAFE_POLICY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")


@dataclass(frozen=True, slots=True)
class MissingnessPolicy:
    """Frozen complete-case selection limits and bounded evidence budget."""

    policy_id: str
    maximum_total_drop_fraction: float
    maximum_per_feature_drop_fraction: float
    maximum_evidence_groups: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or _SAFE_POLICY_ID.fullmatch(self.policy_id) is None:
            raise ValueError("policy_id must be a safe non-empty identifier")
        for value, label in (
            (self.maximum_total_drop_fraction, "maximum_total_drop_fraction"),
            (self.maximum_per_feature_drop_fraction, "maximum_per_feature_drop_fraction"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{label} must be finite and between zero and one")
            object.__setattr__(self, label, float(value))
        if (
            isinstance(self.maximum_evidence_groups, bool)
            or not isinstance(self.maximum_evidence_groups, int)
            or self.maximum_evidence_groups < 1
        ):
            raise ValueError("maximum_evidence_groups must be a positive integer")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "schema_version": "missingness-policy-v1",
                    "policy_id": self.policy_id,
                    "maximum_total_drop_fraction": self.maximum_total_drop_fraction,
                    "maximum_per_feature_drop_fraction": (self.maximum_per_feature_drop_fraction),
                    "maximum_evidence_groups": self.maximum_evidence_groups,
                }
            )
        ).hexdigest()


DEFAULT_MISSINGNESS_POLICY = MissingnessPolicy(
    policy_id="complete-case-audit-default-v1",
    maximum_total_drop_fraction=1.0,
    maximum_per_feature_drop_fraction=1.0,
    maximum_evidence_groups=10_000,
)


@dataclass(frozen=True, slots=True)
class MissingnessEvidenceGroup:
    feature_name: str
    asset: str
    partition_role: PartitionRole
    missingness_kind: str
    auction_state: str
    continuity_state: str
    count: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.feature_name, "feature_name"),
            (self.asset, "asset"),
            (self.missingness_kind, "missingness_kind"),
            (self.auction_state, "auction_state"),
            (self.continuity_state, "continuity_state"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} must be non-empty")
        if not isinstance(self.partition_role, PartitionRole):
            raise TypeError("partition_role must be a PartitionRole")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ValueError("count must be a positive integer")


@dataclass(frozen=True, slots=True)
class MissingnessEvidence:
    """Bounded audit of complete-case inclusion and structural null causes."""

    policy_sha256: str
    partition_role: PartitionRole
    input_row_count: int
    selected_row_count: int
    excluded_row_count: int
    selected_row_ids_sha256: str
    excluded_row_ids_sha256: str
    per_feature_dropped: tuple[tuple[str, int], ...]
    groups: tuple[MissingnessEvidenceGroup, ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.policy_sha256) is None:
            raise ValueError("policy_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.partition_role, PartitionRole):
            raise TypeError("partition_role must be a PartitionRole")
        for value, label in (
            (self.input_row_count, "input_row_count"),
            (self.selected_row_count, "selected_row_count"),
            (self.excluded_row_count, "excluded_row_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.input_row_count != self.selected_row_count + self.excluded_row_count:
            raise ValueError("selected and excluded counts must cover input rows")
        for digest_value, label in (
            (self.selected_row_ids_sha256, "selected_row_ids_sha256"),
            (self.excluded_row_ids_sha256, "excluded_row_ids_sha256"),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", digest_value) is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        if self.per_feature_dropped != tuple(sorted(self.per_feature_dropped)):
            raise ValueError("per_feature_dropped must use canonical feature ordering")
        feature_names = tuple(name for name, _ in self.per_feature_dropped)
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("per_feature_dropped feature names must be unique")
        if any(
            not isinstance(name, str)
            or not name
            or isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= self.input_row_count
            for name, count in self.per_feature_dropped
        ):
            raise ValueError("per_feature_dropped entries are invalid")
        if self.groups != tuple(sorted(self.groups, key=_missingness_group_key)):
            raise ValueError("missingness groups must use canonical ordering")
        group_keys = tuple(_missingness_group_key(group) for group in self.groups)
        if len(group_keys) != len(set(group_keys)):
            raise ValueError("missingness groups must have unique dimensional keys")
        grouped_counts: dict[str, int] = {}
        for group in self.groups:
            if not isinstance(group, MissingnessEvidenceGroup):
                raise TypeError("groups must contain MissingnessEvidenceGroup values")
            if group.partition_role is not self.partition_role:
                raise ValueError("group partition role must match evidence")
            grouped_counts[group.feature_name] = (
                grouped_counts.get(group.feature_name, 0) + group.count
            )
        expected_counts = {name: count for name, count in self.per_feature_dropped if count}
        if grouped_counts != expected_counts:
            raise ValueError("missingness groups must reconcile per-feature dropped counts")


class MissingnessPolicyViolation(ValueError):
    """A frozen missingness limit rejected matrix admission before model work."""

    def __init__(self, message: str, evidence: MissingnessEvidence | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence


def _missingness_group_key(group: MissingnessEvidenceGroup) -> tuple[str, ...]:
    return (
        group.feature_name,
        group.asset,
        group.partition_role.value,
        group.missingness_kind,
        group.auction_state,
        group.continuity_state,
    )


@dataclass(frozen=True, slots=True)
class FeatureMatrix:
    """Complete numeric rows selected from a bounded discovery input."""

    row_ids: tuple[str, ...]
    feature_names: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    dropped_null_rows: int
    partition_role: PartitionRole | None = None
    missingness_evidence: MissingnessEvidence | None = None

    @property
    def sha256(self) -> str:
        """Return the canonical identity of this ordered raw numeric matrix."""

        return hashlib.sha256(_canonical_matrix_json(self)).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedFeatureMatrix(FeatureMatrix):
    """Float matrix derived from one raw matrix and one frozen normalizer."""

    source_matrix_sha256: str
    normalizer_artifact_sha256: str
    normalization_algorithm_version: str

    def __post_init__(self) -> None:
        _validate_matrix_shape(self)
        for label, digest_value in (
            ("source_matrix_sha256", self.source_matrix_sha256),
            ("normalizer_artifact_sha256", self.normalizer_artifact_sha256),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", digest_value) is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
        if not self.normalization_algorithm_version:
            raise ValueError("normalization_algorithm_version must be non-empty")
        for row in self.values:
            for normalized_value in row:
                if type(normalized_value) is not float or not math.isfinite(normalized_value):
                    raise TypeError("normalized matrix values must be finite floats")

    @property
    def sha256(self) -> str:
        """Return an identity that includes raw-source and normalizer provenance."""

        payload = _matrix_payload(self)
        payload.update(
            {
                "schema_version": "normalized-feature-matrix-v2",
                "source_matrix_sha256": self.source_matrix_sha256,
                "normalizer_artifact_sha256": self.normalizer_artifact_sha256,
                "normalization_algorithm_version": self.normalization_algorithm_version,
            }
        )
        return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_feature_matrix(
    input: DiscoveryInput,
    registry: FeatureRegistry,
    feature_names: Sequence[str],
    max_rows: int,
    missingness_policy: MissingnessPolicy | None = None,
) -> FeatureMatrix:
    """Select registered numeric features and drop incomplete rows explicitly."""

    if not isinstance(input, DiscoveryInput):
        raise TypeError("input must be a DiscoveryInput")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    if len(input.rows) > max_rows:
        raise ValueError("discovery input exceeds max_rows")
    if input.feature_set_id != registry.feature_set_id or input.registry_id != registry.registry_id:
        raise ValueError("discovery input and registry identity do not match")
    if missingness_policy is None:
        missingness_policy = DEFAULT_MISSINGNESS_POLICY
    if not isinstance(missingness_policy, MissingnessPolicy):
        raise TypeError("missingness_policy must be a MissingnessPolicy")

    selected = _select_numeric_features(registry, feature_names)
    definitions = {definition.name: definition for definition in registry.definitions}
    row_ids: list[str] = []
    excluded_row_ids: list[str] = []
    values: list[tuple[float, ...]] = []
    per_feature_dropped = {name: 0 for name in selected}
    grouped: dict[tuple[str, str, str, str, str], int] = {}
    previous_by_stream: dict[tuple[str, str], FeatureRow] = {}
    run_position_by_stream: dict[tuple[str, str], int] = {}
    for row in input.rows:
        registry.validate_row(row)
        stream = (row.symbol, row.timeframe)
        previous = previous_by_stream.get(stream)
        continuity_state = _continuity_state(previous, row)
        run_position = (
            run_position_by_stream.get(stream, 0) + 1 if continuity_state == "continuous" else 0
        )
        raw = tuple(row.values[name] for name in selected)
        missing = tuple(name for name, value in zip(selected, raw, strict=True) if value is None)
        row_id = _row_id(row.symbol, row.timeframe, row.timestamp)
        if missing:
            excluded_row_ids.append(row_id)
            for name in missing:
                per_feature_dropped[name] += 1
                definition = definitions[name]
                missingness_kind = (
                    "warm_up"
                    if run_position < definition.required_prior_observations
                    else "structural_null"
                )
                key = (
                    name,
                    row.symbol,
                    missingness_kind,
                    _auction_state(row),
                    continuity_state,
                )
                if (
                    key not in grouped
                    and len(grouped) == missingness_policy.maximum_evidence_groups
                ):
                    raise MissingnessPolicyViolation(
                        "missingness evidence groups exceed the frozen bounded limit"
                    )
                grouped[key] = grouped.get(key, 0) + 1
            previous_by_stream[stream] = row
            run_position_by_stream[stream] = run_position
            continue
        numeric = tuple(_as_finite_float(value, name) for name, value in zip(selected, raw))
        row_ids.append(row_id)
        values.append(numeric)
        previous_by_stream[stream] = row
        run_position_by_stream[stream] = run_position

    evidence = _missingness_evidence(
        input=input,
        policy=missingness_policy,
        selected_row_ids=row_ids,
        excluded_row_ids=excluded_row_ids,
        per_feature_dropped=per_feature_dropped,
        grouped=grouped,
    )
    _enforce_missingness_policy(evidence, missingness_policy)
    if not values:
        raise ValueError("feature selection produced no complete rows")
    matrix = FeatureMatrix(
        row_ids=tuple(row_ids),
        feature_names=selected,
        values=tuple(values),
        dropped_null_rows=len(excluded_row_ids),
        partition_role=input.partition.role,
        missingness_evidence=evidence,
    )
    _validate_matrix_shape(matrix)
    return matrix


def normalize_feature_matrix(
    matrix: FeatureMatrix,
    normalizer: RobustNormalizer,
) -> NormalizedFeatureMatrix:
    """Normalize every raw matrix column without manufacturing feature rows."""

    if type(matrix) is not FeatureMatrix:
        raise TypeError("matrix must be an unnormalized FeatureMatrix")
    if not isinstance(normalizer, RobustNormalizer):
        raise TypeError("normalizer must be a RobustNormalizer")
    _validate_matrix_shape(matrix)
    unavailable = set(matrix.feature_names) - set(normalizer.selected_features)
    if unavailable:
        raise ValueError("matrix features must be selected by the normalizer")

    normalized_rows: list[tuple[float, ...]] = []
    for row in matrix.values:
        normalized_rows.append(
            tuple(
                float(
                    (_as_finite_float(value, name) - normalizer.medians[name])
                    / normalizer.scales[name]
                )
                for name, value in zip(matrix.feature_names, row, strict=True)
            )
        )
    return NormalizedFeatureMatrix(
        row_ids=matrix.row_ids,
        feature_names=matrix.feature_names,
        values=tuple(normalized_rows),
        dropped_null_rows=matrix.dropped_null_rows,
        partition_role=matrix.partition_role,
        missingness_evidence=matrix.missingness_evidence,
        source_matrix_sha256=matrix.sha256,
        normalizer_artifact_sha256=normalizer.artifact_sha256,
        normalization_algorithm_version=normalizer.algorithm_version,
    )


def _select_numeric_features(
    registry: FeatureRegistry, feature_names: Sequence[str]
) -> tuple[str, ...]:
    if isinstance(feature_names, (str, bytes)) or not isinstance(feature_names, Sequence):
        raise ValueError("feature_names must be a sequence")
    requested = tuple(feature_names)
    if not requested:
        raise ValueError("feature_names must contain at least one feature")
    if any(not isinstance(name, str) or not name for name in requested):
        raise ValueError("feature_names must contain non-empty strings")
    if len(requested) != len(set(requested)):
        raise ValueError("feature_names must be unique")

    definitions = {definition.name: definition for definition in registry.definitions}
    unknown = set(requested) - definitions.keys()
    if unknown:
        raise ValueError("feature_names must select only registered features")
    for name in requested:
        definition = definitions[name]
        if definition.leakage_class is LeakageClass.OUTCOME_OR_FUTURE:
            raise ValueError("outcome or future features are forbidden in discovery")
        if definition.value_kind not in (FeatureValueKind.FLOAT, FeatureValueKind.INTEGER):
            raise ValueError("feature_names must select only numeric features")
    requested_set = set(requested)
    return tuple(
        definition.name for definition in registry.definitions if definition.name in requested_set
    )


def _as_finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"feature {name!r} must be numeric")
    try:
        numeric = float(value)
    except OverflowError as error:
        raise ValueError(f"feature {name!r} must be representable as a finite float") from error
    if not math.isfinite(numeric):
        raise ValueError(f"feature {name!r} must be finite")
    return numeric


def _continuity_state(previous: FeatureRow | None, current: FeatureRow) -> str:
    if previous is None:
        return "stream_start"
    if current.segment_id != previous.segment_id:
        return "segment_reset"
    if current.timestamp - previous.timestamp != timeframe_duration(current.timeframe):
        return "material_gap"
    return "continuous"


def _auction_state(row: FeatureRow) -> str:
    state = row.values.get("auction_location")
    return state if isinstance(state, str) and state else "unavailable"


def _missingness_evidence(
    *,
    input: DiscoveryInput,
    policy: MissingnessPolicy,
    selected_row_ids: Sequence[str],
    excluded_row_ids: Sequence[str],
    per_feature_dropped: dict[str, int],
    grouped: dict[tuple[str, str, str, str, str], int],
) -> MissingnessEvidence:
    groups = tuple(
        MissingnessEvidenceGroup(
            feature_name=key[0],
            asset=key[1],
            partition_role=input.partition.role,
            missingness_kind=key[2],
            auction_state=key[3],
            continuity_state=key[4],
            count=count,
        )
        for key, count in sorted(grouped.items())
    )
    return MissingnessEvidence(
        policy_sha256=policy.sha256,
        partition_role=input.partition.role,
        input_row_count=len(input.rows),
        selected_row_count=len(selected_row_ids),
        excluded_row_count=len(excluded_row_ids),
        selected_row_ids_sha256=_ordered_ids_sha256(selected_row_ids),
        excluded_row_ids_sha256=_ordered_ids_sha256(excluded_row_ids),
        per_feature_dropped=tuple(sorted(per_feature_dropped.items())),
        groups=groups,
    )


def _enforce_missingness_policy(
    evidence: MissingnessEvidence,
    policy: MissingnessPolicy,
) -> None:
    total_fraction = evidence.excluded_row_count / evidence.input_row_count
    if total_fraction > policy.maximum_total_drop_fraction:
        raise MissingnessPolicyViolation(
            "complete-case total drop fraction exceeds the frozen missingness policy",
            evidence,
        )
    for feature_name, count in evidence.per_feature_dropped:
        if count / evidence.input_row_count > policy.maximum_per_feature_drop_fraction:
            raise MissingnessPolicyViolation(
                f"feature {feature_name!r} drop fraction exceeds the frozen missingness policy",
                evidence,
            )


def _ordered_ids_sha256(row_ids: Sequence[str]) -> str:
    return hashlib.sha256(_canonical_json(list(row_ids))).hexdigest()


def _validate_matrix_shape(matrix: FeatureMatrix) -> None:
    if not matrix.row_ids or len(matrix.row_ids) != len(matrix.values):
        raise ValueError("matrix row identities must match its values")
    if len(set(matrix.row_ids)) != len(matrix.row_ids):
        raise ValueError("matrix row identities must be unique")
    if not matrix.feature_names or len(set(matrix.feature_names)) != len(matrix.feature_names):
        raise ValueError("matrix feature names must be non-empty and unique")
    if any(len(row) != len(matrix.feature_names) for row in matrix.values):
        raise ValueError("matrix feature dimensions must match its values")
    if (
        isinstance(matrix.dropped_null_rows, bool)
        or not isinstance(matrix.dropped_null_rows, int)
        or matrix.dropped_null_rows < 0
    ):
        raise ValueError("dropped_null_rows must be a non-negative integer")
    if matrix.partition_role is not None and not isinstance(matrix.partition_role, PartitionRole):
        raise TypeError("partition_role must be a PartitionRole or None")
    evidence = matrix.missingness_evidence
    if evidence is not None:
        if not isinstance(evidence, MissingnessEvidence):
            raise TypeError("missingness_evidence must be MissingnessEvidence or None")
        if evidence.selected_row_count != len(matrix.row_ids):
            raise ValueError("missingness selected row count must match matrix rows")
        if evidence.selected_row_ids_sha256 != _ordered_ids_sha256(matrix.row_ids):
            raise ValueError("missingness selected row identity digest must match matrix rows")
        if evidence.excluded_row_count != matrix.dropped_null_rows:
            raise ValueError("missingness excluded row count must match dropped rows")
        if evidence.partition_role is not matrix.partition_role:
            raise ValueError("missingness partition role must match matrix partition role")


def _matrix_payload(matrix: FeatureMatrix) -> dict[str, object]:
    _validate_matrix_shape(matrix)
    return {
        "schema_version": "feature-matrix-v2",
        "row_ids": list(matrix.row_ids),
        "feature_names": list(matrix.feature_names),
        "values": [list(row) for row in matrix.values],
        "dropped_null_rows": matrix.dropped_null_rows,
        "partition_role": matrix.partition_role.value if matrix.partition_role else None,
        "missingness_evidence": (
            None
            if matrix.missingness_evidence is None
            else _missingness_evidence_payload(matrix.missingness_evidence)
        ),
    }


def _missingness_evidence_payload(evidence: MissingnessEvidence) -> dict[str, object]:
    return {
        "schema_version": "missingness-evidence-v1",
        "policy_sha256": evidence.policy_sha256,
        "partition_role": evidence.partition_role.value,
        "input_row_count": evidence.input_row_count,
        "selected_row_count": evidence.selected_row_count,
        "excluded_row_count": evidence.excluded_row_count,
        "selected_row_ids_sha256": evidence.selected_row_ids_sha256,
        "excluded_row_ids_sha256": evidence.excluded_row_ids_sha256,
        "per_feature_dropped": [list(item) for item in evidence.per_feature_dropped],
        "groups": [
            {
                "feature_name": group.feature_name,
                "asset": group.asset,
                "partition_role": group.partition_role.value,
                "missingness_kind": group.missingness_kind,
                "auction_state": group.auction_state,
                "continuity_state": group.continuity_state,
                "count": group.count,
            }
            for group in evidence.groups
        ],
    }


def _canonical_matrix_json(matrix: FeatureMatrix) -> bytes:
    return _canonical_json(_matrix_payload(matrix))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _row_id(symbol: str, timeframe: str, timestamp: datetime) -> str:
    instant = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return f"{symbol}|{timeframe}|{instant}"
