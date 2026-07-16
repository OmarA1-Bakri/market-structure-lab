"""Train-only robust normalization for frozen feature rows."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import struct
import sys
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Any, BinaryIO, Iterable, Mapping, Self

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry, FeatureValueKind


_ALGORITHM_VERSION = "robust-iqr-external-v1"
_DEFAULT_MAX_ROWS_PER_RUN = 50_000
_MERGE_FAN_IN = 64
_FLOAT64 = struct.Struct("<d")
_IO_FLOATS = 8_192
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
    max_rows_per_run: int
    max_buffered_rows: int
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
        if (
            isinstance(self.max_rows_per_run, bool)
            or not isinstance(self.max_rows_per_run, int)
            or self.max_rows_per_run < 1
        ):
            raise ValueError("max_rows_per_run must be a positive integer")
        if (
            isinstance(self.max_buffered_rows, bool)
            or not isinstance(self.max_buffered_rows, int)
            or self.max_buffered_rows < 1
            or self.max_buffered_rows > self.max_rows_per_run
            or self.max_buffered_rows > self.fit_row_count
        ):
            raise ValueError(
                "max_buffered_rows must be positive and cannot exceed the run or fit row count"
            )
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
            "max_rows_per_run": self.max_rows_per_run,
            "max_buffered_rows": self.max_buffered_rows,
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
                max_rows_per_run=int(payload["max_rows_per_run"]),
                max_buffered_rows=int(payload["max_buffered_rows"]),
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
    *,
    max_rows_per_run: int = _DEFAULT_MAX_ROWS_PER_RUN,
) -> RobustNormalizer:
    """Fit exact median/IQR parameters with bounded in-memory row runs."""

    if partition.role is not PartitionRole.TRAINING:
        raise ValueError("normalization may only be fitted on a training partition")
    if not _SAFE_ID.fullmatch(dataset_snapshot_id):
        raise ValueError("dataset_snapshot_id must be a safe, non-empty identifier")
    if isinstance(max_rows_per_run, bool) or not isinstance(max_rows_per_run, int):
        raise ValueError("max_rows_per_run must be a positive integer")
    if max_rows_per_run < 1:
        raise ValueError("max_rows_per_run must be a positive integer")

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

    with TemporaryDirectory(prefix="market-structure-lab-normalizer-") as directory:
        run_directory = Path(directory)
        run_paths: dict[str, list[Path]] = {name: [] for name in names}
        observations: dict[str, list[float]] = {name: [] for name in names}
        observation_counts = {name: 0 for name in names}
        fit_row_count = 0
        buffered_rows = 0
        max_buffered_rows = 0
        run_number = 0

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
            buffered_rows += 1
            max_buffered_rows = max(max_buffered_rows, buffered_rows)
            for name in names:
                value = row.values.get(name)
                if value is not None:
                    observations[name].append(_numeric_value(value, name))
                    observation_counts[name] += 1
            if buffered_rows == max_rows_per_run:
                _flush_runs(observations, run_paths, run_directory, run_number)
                run_number += 1
                buffered_rows = 0

        if fit_row_count == 0:
            raise ValueError("cannot fit a normalizer without training rows")
        if buffered_rows:
            _flush_runs(observations, run_paths, run_directory, run_number)

        medians: dict[str, float] = {}
        iqrs: dict[str, float] = {}
        scales: dict[str, float] = {}
        for name_index, name in enumerate(names):
            count = observation_counts[name]
            if count == 0:
                raise ValueError(f"feature has no non-null training observations: {name}")
            sorted_path = _merge_to_one_run(run_paths[name], run_directory, name_index=name_index)
            q25, median, q75 = _file_quantiles(sorted_path, count)
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
        max_rows_per_run=max_rows_per_run,
        max_buffered_rows=max_buffered_rows,
        selected_features=names,
        medians=medians,
        iqrs=iqrs,
        scales=scales,
    )


def _flush_runs(
    observations: dict[str, list[float]],
    run_paths: dict[str, list[Path]],
    directory: Path,
    run_number: int,
) -> None:
    for name_index, (name, values) in enumerate(observations.items()):
        if not values:
            continue
        values.sort()
        path = directory / f"feature-{name_index:06d}-run-{run_number:012d}.bin"
        with path.open("wb") as stream:
            stream.write(_float_bytes(values))
        run_paths[name].append(path)
        values.clear()


def _merge_to_one_run(paths: list[Path], directory: Path, *, name_index: int) -> Path:
    generation = 0
    current = paths
    while len(current) > 1:
        merged: list[Path] = []
        for group_index, offset in enumerate(range(0, len(current), _MERGE_FAN_IN)):
            group = current[offset : offset + _MERGE_FAN_IN]
            output = directory / (
                f"feature-{name_index:06d}-merge-{generation:06d}-{group_index:012d}.bin"
            )
            _merge_run_group(group, output)
            merged.append(output)
            for path in group:
                path.unlink()
        current = merged
        generation += 1
    return current[0]


def _merge_run_group(paths: list[Path], output: Path) -> None:
    iterators = [_iter_run(path) for path in paths]
    buffer: list[float] = []
    with output.open("wb") as destination:
        for value in heapq.merge(*iterators):
            buffer.append(value)
            if len(buffer) == _IO_FLOATS:
                destination.write(_float_bytes(buffer))
                buffer.clear()
        if buffer:
            destination.write(_float_bytes(buffer))


def _iter_run(path: Path) -> Iterable[float]:
    with path.open("rb") as stream:
        while encoded := stream.read(_FLOAT64.size * _IO_FLOATS):
            if len(encoded) % _FLOAT64.size:
                raise ValueError("corrupt temporary normalization run")
            values = array("d")
            values.frombytes(encoded)
            if sys.byteorder != "little":
                values.byteswap()
            yield from values


def _float_bytes(values: Iterable[float]) -> bytes:
    encoded = array("d", values)
    if sys.byteorder != "little":
        encoded.byteswap()
    return encoded.tobytes()


def _read_float(stream: BinaryIO) -> float | None:
    encoded = stream.read(_FLOAT64.size)
    if not encoded:
        return None
    if len(encoded) != _FLOAT64.size:
        raise ValueError("corrupt temporary normalization run")
    return _FLOAT64.unpack(encoded)[0]


def _file_quantiles(path: Path, count: int) -> tuple[float, float, float]:
    positions = tuple((count - 1) * probability for probability in (0.25, 0.5, 0.75))
    required_indices = sorted(
        {index for position in positions for index in (math.floor(position), math.ceil(position))}
    )
    values: dict[int, float] = {}
    with path.open("rb") as stream:
        for index in required_indices:
            stream.seek(index * _FLOAT64.size, os.SEEK_SET)
            value = _read_float(stream)
            if value is None:
                raise ValueError("corrupt temporary normalization run")
            values[index] = value
    return (
        _interpolate_quantile(values, positions[0]),
        _interpolate_quantile(values, positions[1]),
        _interpolate_quantile(values, positions[2]),
    )


def _interpolate_quantile(values: Mapping[int, float], position: float) -> float:
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


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
