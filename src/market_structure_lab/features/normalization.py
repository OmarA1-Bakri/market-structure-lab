"""Train-only robust normalization for frozen feature rows."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Self

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry, FeatureValueKind


_ALGORITHM_VERSION = "robust-iqr-v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")


class PartitionRole(str, Enum):
    """Purpose assigned to a chronological data partition."""

    TRAINING = "training"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


@dataclass(frozen=True, slots=True)
class TrainingPartition:
    """Frozen half-open information-cutoff interval used for fitting."""

    split_id: str
    start: datetime
    end: datetime
    role: PartitionRole

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.split_id):
            raise ValueError("split_id must be a safe, non-empty versioned identifier")
        start = _as_utc(self.start, "partition start")
        end = _as_utc(self.end, "partition end")
        if start >= end:
            raise ValueError("partition start must precede end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, information_cutoff: datetime) -> bool:
        cutoff = _as_utc(information_cutoff, "information_cutoff")
        return self.start <= cutoff < self.end

    def to_dict(self) -> dict[str, str]:
        return {
            "split_id": self.split_id,
            "start": _format_utc(self.start),
            "end": _format_utc(self.end),
            "role": self.role.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            split_id=str(value["split_id"]),
            start=_parse_datetime(value["start"], "partition start"),
            end=_parse_datetime(value["end"], "partition end"),
            role=PartitionRole(str(value["role"])),
        )


@dataclass(frozen=True, slots=True)
class RobustNormalizer:
    """Immutable median/IQR parameters fitted on one training partition."""

    dataset_snapshot_id: str
    feature_set_id: str
    registry_id: str
    partition: TrainingPartition
    fit_row_count: int
    selected_features: tuple[str, ...]
    medians: Mapping[str, float]
    iqrs: Mapping[str, float]
    scales: Mapping[str, float]
    algorithm_version: str = _ALGORITHM_VERSION

    def __post_init__(self) -> None:
        for label, value in (
            ("dataset_snapshot_id", self.dataset_snapshot_id),
            ("feature_set_id", self.feature_set_id),
            ("registry_id", self.registry_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{label} must be a safe, non-empty identifier")
        if self.algorithm_version != _ALGORITHM_VERSION:
            raise ValueError(f"unsupported normalization algorithm: {self.algorithm_version}")
        if self.partition.role is not PartitionRole.TRAINING:
            raise ValueError("normalizer partition must have the training role")
        if self.fit_row_count < 1:
            raise ValueError("fit_row_count must be positive")
        names = tuple(sorted(self.selected_features))
        if not names or len(set(names)) != len(names):
            raise ValueError("selected_features must be non-empty and unique")
        expected = set(names)
        for label, values in (
            ("medians", self.medians),
            ("iqrs", self.iqrs),
            ("scales", self.scales),
        ):
            if set(values) != expected:
                raise ValueError(f"{label} must contain exactly the selected features")
            if not all(math.isfinite(float(item)) for item in values.values()):
                raise ValueError(f"{label} must contain finite values")
        if any(float(self.iqrs[name]) < 0.0 for name in names):
            raise ValueError("IQR values cannot be negative")
        if any(float(self.scales[name]) <= 0.0 for name in names):
            raise ValueError("scales must be positive")
        object.__setattr__(self, "selected_features", names)
        object.__setattr__(self, "medians", _frozen_float_mapping(names, self.medians))
        object.__setattr__(self, "iqrs", _frozen_float_mapping(names, self.iqrs))
        object.__setattr__(self, "scales", _frozen_float_mapping(names, self.scales))

    def transform_values(self, row: FeatureRow) -> Mapping[str, float | None]:
        """Transform selected values without changing or refitting this artifact."""

        _validate_row_identity(
            row,
            dataset_snapshot_id=self.dataset_snapshot_id,
            feature_set_id=self.feature_set_id,
            registry_id=self.registry_id,
        )
        transformed: dict[str, float | None] = {}
        for name in self.selected_features:
            value = row.values.get(name)
            if value is None:
                transformed[name] = None
                continue
            number = _numeric_value(value, name)
            transformed[name] = (number - self.medians[name]) / self.scales[name]
        return MappingProxyType(transformed)

    @property
    def artifact_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self._payload_without_hash())).hexdigest()

    def _payload_without_hash(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_set_id": self.feature_set_id,
            "registry_id": self.registry_id,
            "partition": self.partition.to_dict(),
            "fit_row_count": self.fit_row_count,
            "selected_features": list(self.selected_features),
            "medians": dict(self.medians),
            "iqrs": dict(self.iqrs),
            "scales": dict(self.scales),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_without_hash()
        payload["artifact_sha256"] = self.artifact_sha256
        return payload

    def canonical_json(self) -> bytes:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str | bytes) -> Self:
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise ValueError("invalid normalizer JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("normalizer JSON must contain an object")
        supplied_hash = payload.pop("artifact_sha256", None)
        try:
            normalizer = cls(
                algorithm_version=str(payload["algorithm_version"]),
                dataset_snapshot_id=str(payload["dataset_snapshot_id"]),
                feature_set_id=str(payload["feature_set_id"]),
                registry_id=str(payload["registry_id"]),
                partition=TrainingPartition.from_dict(payload["partition"]),
                fit_row_count=int(payload["fit_row_count"]),
                selected_features=tuple(str(name) for name in payload["selected_features"]),
                medians=_number_mapping(payload["medians"], "medians"),
                iqrs=_number_mapping(payload["iqrs"], "iqrs"),
                scales=_number_mapping(payload["scales"], "scales"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid normalizer artifact") from error
        if supplied_hash != normalizer.artifact_sha256:
            raise ValueError("normalizer artifact SHA-256 mismatch")
        return normalizer


def fit_robust_normalizer(
    rows: Iterable[FeatureRow],
    registry: FeatureRegistry,
    partition: TrainingPartition,
    dataset_snapshot_id: str,
    selected_features: Iterable[str] | None = None,
) -> RobustNormalizer:
    """Fit deterministic median/IQR parameters using training rows only."""

    if partition.role is not PartitionRole.TRAINING:
        raise ValueError("normalization may only be fitted on a training partition")
    if not _SAFE_ID.fullmatch(dataset_snapshot_id):
        raise ValueError("dataset_snapshot_id must be a safe, non-empty identifier")

    definitions = {definition.name: definition for definition in registry.definitions}
    if selected_features is None:
        names = tuple(
            sorted(
                name
                for name, definition in definitions.items()
                if definition.value_kind in (FeatureValueKind.FLOAT, FeatureValueKind.INTEGER)
            )
        )
    else:
        requested = tuple(selected_features)
        if len(set(requested)) != len(requested):
            raise ValueError("selected_features cannot contain duplicates")
        names = tuple(sorted(requested))
    if not names:
        raise ValueError("at least one numeric feature must be selected")
    for name in names:
        definition = definitions.get(name)
        if definition is None:
            raise ValueError(f"unknown selected feature: {name}")
        if definition.value_kind is FeatureValueKind.CATEGORY:
            raise ValueError(f"categorical feature cannot be normalized: {name}")

    observations: dict[str, list[float]] = {name: [] for name in names}
    fit_row_count = 0
    for row in rows:
        _validate_row_identity(
            row,
            dataset_snapshot_id=dataset_snapshot_id,
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
        )
        if not partition.contains(row.information_cutoff):
            raise ValueError("all fitting rows must fall inside the training partition")
        registry.validate_row(row)
        fit_row_count += 1
        for name in names:
            value = row.values.get(name)
            if value is not None:
                observations[name].append(_numeric_value(value, name))
    if fit_row_count == 0:
        raise ValueError("cannot fit a normalizer without training rows")

    medians: dict[str, float] = {}
    iqrs: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in names:
        values = sorted(observations[name])
        if not values:
            raise ValueError(f"feature has no non-null training observations: {name}")
        q25 = _quantile(values, 0.25)
        median = _quantile(values, 0.5)
        q75 = _quantile(values, 0.75)
        iqr = q75 - q25
        medians[name] = median
        iqrs[name] = iqr
        scales[name] = iqr if iqr > 0.0 else 1.0

    return RobustNormalizer(
        dataset_snapshot_id=dataset_snapshot_id,
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
        partition=partition,
        fit_row_count=fit_row_count,
        selected_features=names,
        medians=medians,
        iqrs=iqrs,
        scales=scales,
    )


def _quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _validate_row_identity(
    row: FeatureRow,
    *,
    dataset_snapshot_id: str,
    feature_set_id: str,
    registry_id: str,
) -> None:
    if row.dataset_version != dataset_snapshot_id:
        raise ValueError("feature row dataset version does not match the normalizer snapshot")
    if row.feature_set_id != feature_set_id:
        raise ValueError("feature row feature_set_id does not match the registry")
    if row.registry_id != registry_id:
        raise ValueError("feature row registry_id does not match the registry")


def _numeric_value(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"feature {name!r} must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"feature {name!r} must be finite")
    return number


def _frozen_float_mapping(
    names: tuple[str, ...], values: Mapping[str, float]
) -> Mapping[str, float]:
    return MappingProxyType({name: float(values[name]) for name in names})


def _number_mapping(value: object, label: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return {str(name): float(number) for name, number in value.items()}


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _as_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must use UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    return _as_utc(parsed, label)
