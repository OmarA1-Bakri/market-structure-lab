"""Deterministic, outcome-blind cluster stability evidence."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence

from market_structure_lab.discovery.kmeans import KMeansResult, fit_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.pca import PCAProjection
from market_structure_lab.discovery.splits import PartitionRole

_MAX_ITERATIONS = 100
_TOLERANCE = 1e-12


def adjusted_rand_index(left: Sequence[int], right: Sequence[int]) -> float:
    """Return the exact pair-count adjusted Rand index for two partitions."""

    left_labels = _validated_labels(left, "left")
    right_labels = _validated_labels(right, "right")
    if len(left_labels) != len(right_labels):
        raise ValueError("partitions must have the same length")

    pair_count = math.comb(len(left_labels), 2)
    if pair_count == 0:
        return 1.0
    left_counts = Counter(left_labels)
    right_counts = Counter(right_labels)
    joint_counts = Counter(zip(left_labels, right_labels, strict=True))
    same_joint = sum(math.comb(count, 2) for count in joint_counts.values())
    same_left = sum(math.comb(count, 2) for count in left_counts.values())
    same_right = sum(math.comb(count, 2) for count in right_counts.values())

    numerator = 2 * (same_joint * pair_count - same_left * same_right)
    denominator = (same_left + same_right) * pair_count - 2 * same_left * same_right
    if denominator == 0:
        return 1.0
    return float(Fraction(numerator, denominator))


@dataclass(frozen=True, slots=True)
class StabilityPolicy:
    """Thresholds frozen before cluster stability is evaluated."""

    minimum_seed_ari: float
    minimum_subsample_ari: float
    maximum_adjacent_js_distance: float
    minimum_asset_coverage: float
    minimum_parameter_perturbation_ari: float = 0.0

    def __post_init__(self) -> None:
        _require_bounded(self.minimum_seed_ari, "minimum_seed_ari", -1.0, 1.0)
        _require_bounded(self.minimum_subsample_ari, "minimum_subsample_ari", -1.0, 1.0)
        _require_bounded(
            self.maximum_adjacent_js_distance,
            "maximum_adjacent_js_distance",
            0.0,
            1.0,
        )
        _require_bounded(self.minimum_asset_coverage, "minimum_asset_coverage", 0.0, 1.0)
        _require_bounded(
            self.minimum_parameter_perturbation_ari,
            "minimum_parameter_perturbation_ari",
            -1.0,
            1.0,
        )


@dataclass(frozen=True, slots=True)
class StabilityReport:
    """Complete evidence used to accept or reject a cluster definition."""

    seed_ari: tuple[float, ...]
    subsample_ari: tuple[float, ...]
    adjacent_js_distance: float
    asset_coverage: float
    parameter_perturbation_ari: tuple[float, ...]
    accepted: bool

    def __post_init__(self) -> None:
        _require_metric_tuple(self.seed_ari, "seed_ari", -1.0, 1.0)
        _require_metric_tuple(self.subsample_ari, "subsample_ari", -1.0, 1.0)
        _require_bounded(
            self.adjacent_js_distance,
            "adjacent_js_distance",
            0.0,
            1.0,
        )
        _require_bounded(self.asset_coverage, "asset_coverage", 0.0, 1.0)
        _require_metric_tuple(
            self.parameter_perturbation_ari,
            "parameter_perturbation_ari",
            -1.0,
            1.0,
        )
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a bool")


def evaluate_cluster_stability(
    *,
    discovery: FeatureMatrix,
    development: FeatureMatrix,
    projection: PCAProjection,
    base_result: KMeansResult,
    symbols: Sequence[str],
    periods: Sequence[str],
    seeds: Sequence[int],
    subsample_fraction: float,
    policy: StabilityPolicy,
) -> StabilityReport:
    """Evaluate a frozen fit on bounded discovery and development matrices."""

    if not isinstance(discovery, FeatureMatrix) or not isinstance(development, FeatureMatrix):
        raise TypeError("discovery and development must be FeatureMatrix values")
    if discovery.partition_role is not PartitionRole.DISCOVERY:
        raise ValueError("discovery matrix must carry discovery partition provenance")
    if development.partition_role is not PartitionRole.DEVELOPMENT:
        raise ValueError("development matrix must carry development partition provenance")
    discovery_rows, feature_width = _validated_matrix(discovery, "discovery")
    development_rows, development_width = _validated_matrix(development, "development")
    if discovery.feature_names != development.feature_names or feature_width != development_width:
        raise ValueError("discovery and development feature identity must match")
    if set(discovery.row_ids) & set(development.row_ids):
        raise ValueError("discovery and development row identities must not overlap")
    if not isinstance(policy, StabilityPolicy):
        raise TypeError("policy must be a StabilityPolicy")

    scores = _validated_projection(projection, discovery_rows, feature_width)
    cluster_count = _validated_clustering(base_result, scores)
    development_scores = _project(development_rows, projection.means, projection.components)
    development_labels = _assign(development_scores, base_result.centroids)
    symbol_values = _validated_names(symbols, len(development_rows), "symbols")
    period_values = _validated_names(periods, len(development_rows), "periods")
    ordered_periods = _period_order(period_values)
    if len(ordered_periods) < 2:
        raise ValueError("periods must contain at least two adjacent periods")
    seed_values = _validated_seeds(seeds)
    fraction = _validated_fraction(subsample_fraction)

    seed_ari = tuple(
        adjusted_rand_index(
            base_result.assignments,
            fit_kmeans(
                scores,
                clusters=cluster_count,
                seed=seed,
                max_iterations=_MAX_ITERATIONS,
                tolerance=_TOLERANCE,
            ).assignments,
        )
        for seed in seed_values
    )
    subsample_ari = tuple(
        _subsample_ari(scores, base_result.assignments, cluster_count, seed, fraction)
        for seed in seed_values
    )
    adjacent_js_distance = _maximum_adjacent_js_distance(
        development_labels,
        period_values,
        ordered_periods,
        cluster_count,
    )
    asset_coverage = _minimum_cluster_asset_coverage(
        development_labels,
        symbol_values,
        cluster_count,
    )
    parameter_perturbation_ari = _parameter_perturbation_ari(
        scores,
        base_result.assignments,
        cluster_count,
        seed_values[0],
    )
    accepted = (
        min(seed_ari) >= policy.minimum_seed_ari
        and min(subsample_ari) >= policy.minimum_subsample_ari
        and adjacent_js_distance <= policy.maximum_adjacent_js_distance
        and asset_coverage >= policy.minimum_asset_coverage
        and min(parameter_perturbation_ari) >= policy.minimum_parameter_perturbation_ari
    )
    return StabilityReport(
        seed_ari=seed_ari,
        subsample_ari=subsample_ari,
        adjacent_js_distance=adjacent_js_distance,
        asset_coverage=asset_coverage,
        parameter_perturbation_ari=parameter_perturbation_ari,
        accepted=accepted,
    )


def _validated_labels(values: Sequence[int], label: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{label} partition must be a sequence of integer labels")
    labels = tuple(values)
    if not labels:
        raise ValueError("partitions must be non-empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in labels):
        raise TypeError("partition labels must be integer values")
    if any(value < 0 for value in labels):
        raise ValueError("partition labels must be non-negative")
    return labels


def _validated_matrix(
    matrix: FeatureMatrix, label: str
) -> tuple[tuple[tuple[float, ...], ...], int]:
    if not isinstance(matrix, FeatureMatrix):
        raise TypeError(f"{label} must be a FeatureMatrix")
    if not matrix.values:
        raise ValueError(f"{label} matrix must be non-empty")
    width = len(matrix.values[0])
    if width < 1 or len(matrix.feature_names) != width:
        raise ValueError(f"{label} matrix feature identity is inconsistent")
    if len(matrix.row_ids) != len(matrix.values) or len(set(matrix.row_ids)) != len(matrix.row_ids):
        raise ValueError(f"{label} matrix row identity is inconsistent")
    if len(set(matrix.feature_names)) != len(matrix.feature_names):
        raise ValueError(f"{label} matrix feature names must be unique")
    rows: list[tuple[float, ...]] = []
    for row in matrix.values:
        if len(row) != width:
            raise ValueError(f"{label} matrix rows must have equal width")
        rows.append(tuple(_require_finite(value, f"{label} matrix value") for value in row))
    return tuple(rows), width


def _validated_projection(
    projection: PCAProjection,
    discovery_rows: tuple[tuple[float, ...], ...],
    feature_width: int,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(projection, PCAProjection):
        raise TypeError("projection must be a PCAProjection")
    if len(projection.means) != feature_width or not projection.components:
        raise ValueError("projection feature identity does not match discovery")
    means = tuple(_require_finite(value, "projection mean") for value in projection.means)
    components = tuple(
        tuple(_require_finite(value, "projection component") for value in component)
        for component in projection.components
    )
    if any(len(component) != feature_width for component in components):
        raise ValueError("projection component width does not match discovery")
    explained_variance = tuple(
        _require_finite(value, "projection explained variance")
        for value in projection.explained_variance_ratio
    )
    if len(explained_variance) != len(components) or any(
        value < 0.0 or value > 1.0 for value in explained_variance
    ):
        raise ValueError("projection explained variance must match components and use [0, 1]")
    variance_total = math.fsum(explained_variance)
    if variance_total <= 0.0 or variance_total > 1.0 + 1e-12:
        raise ValueError("projection explained variance total must be in (0, 1]")
    expected_scores = _project(discovery_rows, means, components)
    if len(projection.scores) != len(expected_scores):
        raise ValueError("projection scores do not match discovery rows")
    supplied_scores = tuple(
        tuple(_require_finite(value, "projection score") for value in row)
        for row in projection.scores
    )
    if any(len(row) != len(components) for row in supplied_scores):
        raise ValueError("projection score width is inconsistent")
    for supplied, expected in zip(supplied_scores, expected_scores, strict=True):
        if any(
            not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
            for left, right in zip(supplied, expected, strict=True)
        ):
            raise ValueError("projection scores do not match the frozen projection definition")
    return supplied_scores


def _validated_clustering(result: KMeansResult, scores: tuple[tuple[float, ...], ...]) -> int:
    if not isinstance(result, KMeansResult):
        raise TypeError("base_result must be a KMeansResult")
    if not result.centroids:
        raise ValueError("base_result must contain centroids")
    width = len(scores[0])
    centroids = tuple(
        tuple(_require_finite(value, "cluster centroid") for value in centroid)
        for centroid in result.centroids
    )
    if any(len(centroid) != width for centroid in centroids):
        raise ValueError("cluster centroid width does not match projection scores")
    labels = _validated_labels(result.assignments, "base_result")
    if len(labels) != len(scores):
        raise ValueError("base_result assignments must match discovery rows")
    if any(label >= len(centroids) for label in labels):
        raise ValueError("base_result assignment references an unknown centroid")
    if set(labels) != set(range(len(centroids))):
        raise ValueError("every base_result centroid must have assigned rows")
    if not math.isfinite(result.inertia) or result.inertia < 0.0:
        raise ValueError("base_result inertia must be finite and non-negative")
    if (
        isinstance(result.iterations, bool)
        or not isinstance(result.iterations, int)
        or result.iterations < 1
    ):
        raise ValueError("base_result iterations must be a positive integer")
    return len(centroids)


def _project(
    rows: tuple[tuple[float, ...], ...],
    means: Sequence[float],
    components: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(
            math.fsum((value - means[index]) * component[index] for index, value in enumerate(row))
            for component in components
        )
        for row in rows
    )


def _assign(
    rows: Sequence[Sequence[float]], centroids: Sequence[Sequence[float]]
) -> tuple[int, ...]:
    return tuple(
        min(
            range(len(centroids)),
            key=lambda label: math.fsum(
                (left - right) ** 2 for left, right in zip(row, centroids[label], strict=True)
            ),
        )
        for row in rows
    )


def _validated_names(values: Sequence[str], expected: int, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{label} must be a sequence")
    names = tuple(values)
    if len(names) != expected:
        raise ValueError(f"{label} length must match development rows")
    if any(not isinstance(value, str) or not value.strip() for value in names):
        raise ValueError(f"{label} must contain non-empty strings")
    return names


def _period_order(periods: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(periods))


def _validated_seeds(seeds: Sequence[int]) -> tuple[int, ...]:
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, Sequence):
        raise TypeError("seeds must be a sequence of integers")
    values = tuple(seeds)
    if not values:
        raise ValueError("seeds must be non-empty")
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in values):
        raise TypeError("seeds must contain integers")
    if len(set(values)) != len(values):
        raise ValueError("seeds must be unique")
    return values


def _validated_fraction(value: float) -> float:
    fraction = _require_finite(value, "subsample_fraction")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("subsample_fraction must be greater than zero and at most one")
    return fraction


def _subsample_ari(
    scores: tuple[tuple[float, ...], ...],
    base_assignments: tuple[int, ...],
    cluster_count: int,
    seed: int,
    fraction: float,
) -> float:
    sample_size = max(cluster_count, math.floor(len(scores) * fraction))
    sample_size = min(sample_size, len(scores))
    randomizer = random.Random(seed ^ 0x5A17)
    by_cluster = tuple(
        tuple(index for index, label in enumerate(base_assignments) if label == cluster_label)
        for cluster_label in range(cluster_count)
    )
    selected = {randomizer.choice(indices) for indices in by_cluster}
    remaining = tuple(index for index in range(len(scores)) if index not in selected)
    selected.update(randomizer.sample(remaining, sample_size - len(selected)))
    indices = tuple(sorted(selected))
    sampled_scores = tuple(scores[index] for index in indices)
    sampled = fit_kmeans(
        sampled_scores,
        clusters=cluster_count,
        seed=seed,
        max_iterations=_MAX_ITERATIONS,
        tolerance=_TOLERANCE,
    )
    return adjusted_rand_index(
        tuple(base_assignments[index] for index in indices),
        sampled.assignments,
    )


def _maximum_adjacent_js_distance(
    labels: tuple[int, ...],
    periods: tuple[str, ...],
    ordered_periods: tuple[str, ...],
    cluster_count: int,
) -> float:
    distributions = tuple(
        _label_distribution(
            tuple(
                label
                for label, row_period in zip(labels, periods, strict=True)
                if row_period == period
            ),
            cluster_count,
        )
        for period in ordered_periods
    )
    return max(
        _jensen_shannon_distance(left, right)
        for left, right in zip(distributions, distributions[1:])
    )


def _label_distribution(labels: tuple[int, ...], cluster_count: int) -> tuple[float, ...]:
    counts = Counter(labels)
    return tuple(counts[label] / len(labels) for label in range(cluster_count))


def _jensen_shannon_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    midpoint = tuple(
        (left_value + right_value) / 2.0 for left_value, right_value in zip(left, right)
    )

    def divergence(values: tuple[float, ...]) -> float:
        return math.fsum(
            value * math.log2(value / middle)
            for value, middle in zip(values, midpoint, strict=True)
            if value > 0.0
        )

    return math.sqrt(max(0.0, (divergence(left) + divergence(right)) / 2.0))


def _minimum_cluster_asset_coverage(
    labels: tuple[int, ...], symbols: tuple[str, ...], cluster_count: int
) -> float:
    universe = set(symbols)
    return min(
        len(
            {
                symbol
                for label, symbol in zip(labels, symbols, strict=True)
                if label == cluster_label
            }
        )
        / len(universe)
        for cluster_label in range(cluster_count)
    )


def _parameter_perturbation_ari(
    scores: tuple[tuple[float, ...], ...],
    assignments: tuple[int, ...],
    cluster_count: int,
    seed: int,
) -> tuple[float, ...]:
    distinct = len(set(scores))
    candidates: list[int] = []
    if cluster_count > 2:
        candidates.append(cluster_count - 1)
    if cluster_count < distinct:
        candidates.append(cluster_count + 1)
    if not candidates:
        candidates.append(cluster_count)
    return tuple(
        adjusted_rand_index(
            assignments,
            fit_kmeans(
                scores,
                clusters=candidate,
                seed=seed,
                max_iterations=_MAX_ITERATIONS,
                tolerance=_TOLERANCE,
            ).assignments,
        )
        for candidate in candidates
    )


def _require_metric_tuple(
    values: tuple[float, ...], label: str, minimum: float, maximum: float
) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    for value in values:
        _require_bounded(value, label, minimum, maximum)


def _require_bounded(value: float, label: str, minimum: float, maximum: float) -> None:
    numeric = _require_finite(value, label)
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


def _require_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric
