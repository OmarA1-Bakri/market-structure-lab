"""Bounded, outcome-blind numeric matrices for discovery models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence

from market_structure_lab.discovery.splits import DiscoveryInput, PartitionRole
from market_structure_lab.features.registry import (
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
)


@dataclass(frozen=True, slots=True)
class FeatureMatrix:
    """Complete numeric rows selected from a bounded discovery input."""

    row_ids: tuple[str, ...]
    feature_names: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    dropped_null_rows: int
    partition_role: PartitionRole | None = None


def build_feature_matrix(
    input: DiscoveryInput,
    registry: FeatureRegistry,
    feature_names: Sequence[str],
    max_rows: int,
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

    selected = _select_numeric_features(registry, feature_names)
    row_ids: list[str] = []
    values: list[tuple[float, ...]] = []
    dropped = 0
    for row in input.rows:
        registry.validate_row(row)
        raw = tuple(row.values[name] for name in selected)
        if any(value is None for value in raw):
            dropped += 1
            continue
        numeric = tuple(_as_finite_float(value, name) for name, value in zip(selected, raw))
        row_ids.append(_row_id(row.symbol, row.timeframe, row.timestamp))
        values.append(numeric)

    if not values:
        raise ValueError("feature selection produced no complete rows")
    return FeatureMatrix(
        row_ids=tuple(row_ids),
        feature_names=selected,
        values=tuple(values),
        dropped_null_rows=dropped,
        partition_role=input.partition.role,
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


def _row_id(symbol: str, timeframe: str, timestamp: datetime) -> str:
    instant = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return f"{symbol}|{timeframe}|{instant}"
