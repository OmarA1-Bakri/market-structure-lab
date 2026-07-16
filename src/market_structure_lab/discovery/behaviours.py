"""Canonical outcome-blind behaviour definitions."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass
from typing import Literal, Sequence

from market_structure_lab.discovery.kmeans import KMeansResult
from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.stability import StabilityReport

_MAX_REPRESENTATIVES = 5
_FORBIDDEN_OUTCOME_TEXT = re.compile(
    r"\b(?:future returns?|forward returns?|MFE|MAE|profit(?:able|ability)?|"
    r"target[- ]hits?|trade outcomes?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FeatureDistribution:
    """Deterministic five-number summary for one frozen feature."""

    feature_name: str
    minimum: float
    first_quartile: float
    median: float
    third_quartile: float
    maximum: float

    def __post_init__(self) -> None:
        _require_text(self.feature_name, "feature_name")
        values = tuple(
            _require_finite(getattr(self, field), field)
            for field in (
                "minimum",
                "first_quartile",
                "median",
                "third_quartile",
                "maximum",
            )
        )
        if values != tuple(sorted(values)):
            raise ValueError("feature distribution quantiles must be ordered")


@dataclass(frozen=True, slots=True)
class FrozenBehaviour:
    """A stable cluster frozen before any future outcome is attached."""

    behaviour_id: str
    discovery_run_id: str
    cluster_definition_sha256: str
    representative_event_ids: tuple[str, ...]
    feature_centroid: tuple[float, ...]
    feature_distributions: tuple[FeatureDistribution, ...]
    stability: StabilityReport
    frequency: int
    median_duration_seconds: float
    asset_coverage: tuple[str, ...]
    regime_coverage: tuple[str, ...]
    description: str
    status: Literal["frozen"]

    def __post_init__(self) -> None:
        if not self.behaviour_id.startswith("B-"):
            raise ValueError("behaviour_id must use the B- prefix")
        _require_text(self.discovery_run_id, "discovery_run_id")
        if not re.fullmatch(r"[0-9a-f]{64}", self.cluster_definition_sha256):
            raise ValueError("cluster_definition_sha256 must be a lowercase SHA-256")
        if not self.representative_event_ids:
            raise ValueError("representative_event_ids must be non-empty")
        if not self.feature_centroid or len(self.feature_centroid) != len(
            self.feature_distributions
        ):
            raise ValueError("feature centroid and distributions must have equal non-zero width")
        for value in self.feature_centroid:
            _require_finite(value, "feature_centroid")
        if not isinstance(self.stability, StabilityReport) or not self.stability.accepted:
            raise ValueError("frozen behaviours require accepted stability evidence")
        if isinstance(self.frequency, bool) or not isinstance(self.frequency, int):
            raise TypeError("frequency must be a positive integer")
        if self.frequency < 1:
            raise ValueError("frequency must be a positive integer")
        _require_non_negative(self.median_duration_seconds, "median_duration_seconds")
        _require_unique_names(self.asset_coverage, "asset_coverage", require_non_empty=True)
        _require_unique_names(self.regime_coverage, "regime_coverage")
        _validate_description(self.description)
        if self.status != "frozen":
            raise ValueError("catalogued behaviour status must be frozen")


def freeze_behaviours(
    *,
    run_id: str,
    matrix: FeatureMatrix,
    clustering: KMeansResult,
    stability: StabilityReport,
    event_ids: Sequence[str],
    durations_seconds: Sequence[float],
    symbols: Sequence[str],
    description: str,
) -> tuple[FrozenBehaviour, ...]:
    """Freeze accepted clusters and return no entries for an unstable definition."""

    _require_text(run_id, "run_id")
    rows, width = _validated_matrix(matrix)
    labels, centroids = _validated_clustering(clustering, rows, width)
    if not isinstance(stability, StabilityReport):
        raise TypeError("stability must be a StabilityReport")
    events = _validated_identifiers(event_ids, len(rows), "event_ids")
    durations = _validated_durations(durations_seconds, len(rows))
    symbol_values = _validated_symbols(symbols, len(rows))
    _validate_description(description)
    if not stability.accepted:
        return ()

    behaviours: list[FrozenBehaviour] = []
    for label, centroid in enumerate(centroids):
        member_indices = tuple(
            index for index, assignment in enumerate(labels) if assignment == label
        )
        definition_sha256 = _cluster_definition_sha256(matrix.feature_names, centroids, label)
        representatives = tuple(
            events[index]
            for index in sorted(
                member_indices,
                key=lambda index: (_squared_distance(rows[index], centroid), events[index]),
            )[:_MAX_REPRESENTATIVES]
        )
        distributions = tuple(
            _distribution(
                feature_name,
                tuple(rows[index][column] for index in member_indices),
            )
            for column, feature_name in enumerate(matrix.feature_names)
        )
        assets = tuple(sorted({symbol_values[index] for index in member_indices}))
        median_duration_seconds = float(
            statistics.median(durations[index] for index in member_indices)
        )
        behaviour_payload = {
            "discovery_run_id": run_id,
            "cluster_definition_sha256": definition_sha256,
            "representative_event_ids": representatives,
            "feature_centroid": centroid,
            "feature_distributions": tuple(asdict(item) for item in distributions),
            "stability": asdict(stability),
            "frequency": len(member_indices),
            "median_duration_seconds": median_duration_seconds,
            "asset_coverage": assets,
            "regime_coverage": (),
            "description": description,
            "status": "frozen",
        }
        digest = hashlib.sha256(_canonical_json(behaviour_payload)).hexdigest()
        behaviours.append(
            FrozenBehaviour(
                behaviour_id=f"B-{digest[:16].upper()}",
                discovery_run_id=run_id,
                cluster_definition_sha256=definition_sha256,
                representative_event_ids=representatives,
                feature_centroid=centroid,
                feature_distributions=distributions,
                stability=stability,
                frequency=len(member_indices),
                median_duration_seconds=median_duration_seconds,
                asset_coverage=assets,
                regime_coverage=(),
                description=description,
                status="frozen",
            )
        )
    return tuple(sorted(behaviours, key=lambda item: item.behaviour_id))


def _validated_matrix(
    matrix: FeatureMatrix,
) -> tuple[tuple[tuple[float, ...], ...], int]:
    if not isinstance(matrix, FeatureMatrix):
        raise TypeError("matrix must be a FeatureMatrix")
    if not matrix.values:
        raise ValueError("matrix must be non-empty")
    width = len(matrix.values[0])
    if width < 1 or len(matrix.feature_names) != width:
        raise ValueError("matrix feature identity is inconsistent")
    if len(matrix.row_ids) != len(matrix.values) or len(set(matrix.row_ids)) != len(matrix.row_ids):
        raise ValueError("matrix row identity is inconsistent")
    if len(set(matrix.feature_names)) != len(matrix.feature_names):
        raise ValueError("matrix feature names must be unique")
    rows: list[tuple[float, ...]] = []
    for row in matrix.values:
        if len(row) != width:
            raise ValueError("matrix rows must have equal width")
        rows.append(tuple(_require_finite(value, "matrix value") for value in row))
    return tuple(rows), width


def _validated_clustering(
    clustering: KMeansResult,
    rows: tuple[tuple[float, ...], ...],
    width: int,
) -> tuple[tuple[int, ...], tuple[tuple[float, ...], ...]]:
    if not isinstance(clustering, KMeansResult):
        raise TypeError("clustering must be a KMeansResult")
    if not clustering.centroids:
        raise ValueError("clustering must contain centroids")
    centroids = tuple(
        tuple(_require_finite(value, "centroid") for value in centroid)
        for centroid in clustering.centroids
    )
    if any(len(centroid) != width for centroid in centroids):
        raise ValueError("centroid width must match matrix features")
    labels = tuple(clustering.assignments)
    if len(labels) != len(rows):
        raise ValueError("clustering assignments must match matrix rows")
    if any(isinstance(label, bool) or not isinstance(label, int) for label in labels):
        raise TypeError("clustering assignments must be integer labels")
    if any(label < 0 or label >= len(centroids) for label in labels):
        raise ValueError("clustering assignment references an unknown centroid")
    if set(labels) != set(range(len(centroids))):
        raise ValueError("every centroid must have assigned rows")
    for label, centroid in enumerate(centroids):
        members = tuple(
            row for row, assignment in zip(rows, labels, strict=True) if assignment == label
        )
        expected = tuple(
            math.fsum(row[column] for row in members) / len(members) for column in range(width)
        )
        if any(
            not math.isclose(value, mean, rel_tol=1e-12, abs_tol=1e-12)
            for value, mean in zip(centroid, expected, strict=True)
        ):
            raise ValueError("clustering centroid must equal its assigned-row mean")
    _require_non_negative(clustering.inertia, "clustering inertia")
    if isinstance(clustering.iterations, bool) or not isinstance(clustering.iterations, int):
        raise TypeError("clustering iterations must be a positive integer")
    if clustering.iterations < 1:
        raise ValueError("clustering iterations must be a positive integer")
    return labels, centroids


def _validated_identifiers(values: Sequence[str], expected: int, label: str) -> tuple[str, ...]:
    identifiers = _validated_string_sequence(values, expected, label)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{label} must be unique")
    return identifiers


def _validated_symbols(values: Sequence[str], expected: int) -> tuple[str, ...]:
    return _validated_string_sequence(values, expected, "symbols")


def _validated_string_sequence(values: Sequence[str], expected: int, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{label} must be a sequence")
    result = tuple(values)
    if len(result) != expected:
        raise ValueError(f"{label} length must match matrix rows")
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError(f"{label} must contain non-empty strings")
    return result


def _validated_durations(values: Sequence[float], expected: int) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("durations_seconds must be a sequence")
    if len(values) != expected:
        raise ValueError("durations_seconds length must match matrix rows")
    return tuple(_require_non_negative(value, "duration") for value in values)


def _cluster_definition_sha256(
    feature_names: tuple[str, ...],
    centroids: tuple[tuple[float, ...], ...],
    label: int,
) -> str:
    payload = {
        "algorithm": "deterministic_kmeans_v1",
        "feature_names": feature_names,
        "centroids": centroids,
        "cluster_label": label,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _distribution(feature_name: str, values: tuple[float, ...]) -> FeatureDistribution:
    ordered = tuple(sorted(values))
    return FeatureDistribution(
        feature_name=feature_name,
        minimum=ordered[0],
        first_quartile=_quantile(ordered, 0.25),
        median=_quantile(ordered, 0.5),
        third_quartile=_quantile(ordered, 0.75),
        maximum=ordered[-1],
    )


def _quantile(values: tuple[float, ...], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _squared_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return math.fsum(
        (left_value - right_value) ** 2 for left_value, right_value in zip(left, right, strict=True)
    )


def _validate_description(value: str) -> None:
    _require_text(value, "description")
    if _FORBIDDEN_OUTCOME_TEXT.search(value):
        raise ValueError("description must not contain outcome or profitability fields")


def _require_unique_names(
    values: tuple[str, ...], label: str, *, require_non_empty: bool = False
) -> None:
    if not isinstance(values, tuple) or (require_non_empty and not values):
        raise ValueError(f"{label} must be a tuple with the required coverage")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique and sorted")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _require_non_negative(value: object, label: str) -> float:
    numeric = _require_finite(value, label)
    if numeric < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return numeric


def _require_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
