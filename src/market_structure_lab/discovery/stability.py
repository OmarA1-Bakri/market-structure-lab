"""Deterministic, outcome-blind cluster stability evidence."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, Sequence

from market_structure_lab.discovery.kmeans import KMeansResult, fit_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.pca import PCAProjection, pca_projection_sha256
from market_structure_lab.discovery.splits import PartitionRole

_MAX_ITERATIONS = 100
_MAX_SEEDS = 128
_MAX_TOTAL_FITS = 258
_TOLERANCE = 1e-12
STABILITY_ALGORITHM_VERSION = "cluster-stability-v2"


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
class AdjacentPeriodStabilityPolicy:
    """Identified frozen gates for adjacent-period structural drift."""

    policy_id: str
    policy_purpose: Literal["software_fixture", "research"]
    maximum_centroid_displacement: float
    maximum_within_cluster_scale_change: float
    maximum_assignment_margin_drift: float
    minimum_cluster_event_support: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if self.policy_purpose not in ("software_fixture", "research"):
            raise ValueError("policy_purpose must be software_fixture or research")
        for value, label in (
            (self.maximum_centroid_displacement, "maximum_centroid_displacement"),
            (
                self.maximum_within_cluster_scale_change,
                "maximum_within_cluster_scale_change",
            ),
            (self.maximum_assignment_margin_drift, "maximum_assignment_margin_drift"),
        ):
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise TypeError(f"{label} must be a finite number")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"{label} must be finite")
            if numeric < 0.0:
                raise ValueError(f"{label} must be non-negative")
        if (
            isinstance(self.minimum_cluster_event_support, bool)
            or not isinstance(self.minimum_cluster_event_support, int)
            or self.minimum_cluster_event_support < 1
        ):
            raise ValueError("minimum_cluster_event_support must be a positive integer")


@dataclass(frozen=True, slots=True)
class ClusterPeriodSupport:
    """Per-period, per-cluster event support with explicit zero-count assets."""

    period: str
    cluster_label: int
    event_count: int
    asset_event_counts: tuple[tuple[str, int], ...]
    asset_coverage: float

    def __post_init__(self) -> None:
        if not isinstance(self.period, str) or not self.period.strip():
            raise ValueError("period must be a non-empty string")
        if (
            isinstance(self.cluster_label, bool)
            or not isinstance(self.cluster_label, int)
            or self.cluster_label < 0
        ):
            raise ValueError("cluster_label must be a non-negative integer")
        if (
            isinstance(self.event_count, bool)
            or not isinstance(self.event_count, int)
            or self.event_count < 0
        ):
            raise ValueError("event_count must be a non-negative integer")
        if not self.asset_event_counts:
            raise ValueError("asset_event_counts must be non-empty")
        names = tuple(name for name, _ in self.asset_event_counts)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError("asset_event_counts must use unique canonical asset order")
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("asset_event_counts must use non-empty asset names")
        counts = tuple(count for _, count in self.asset_event_counts)
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts
        ):
            raise ValueError("asset event counts must be non-negative integers")
        if sum(counts) != self.event_count:
            raise ValueError("asset event counts must sum to event_count")
        _require_bounded(self.asset_coverage, "asset_coverage", 0.0, 1.0)
        expected_coverage = sum(count > 0 for count in counts) / len(counts)
        if not math.isclose(self.asset_coverage, expected_coverage, abs_tol=1e-12):
            raise ValueError("asset_coverage must match the explicit asset event counts")


@dataclass(frozen=True, slots=True)
class AdjacentClusterEvidence:
    """Structural drift for one frozen cluster across an adjacent period pair."""

    cluster_label: int
    centroid_displacement: float | None
    within_cluster_scale_change: float | None
    assignment_margin_drift: float | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.cluster_label, bool)
            or not isinstance(self.cluster_label, int)
            or self.cluster_label < 0
        ):
            raise ValueError("cluster_label must be a non-negative integer")
        for value, label in (
            (self.centroid_displacement, "centroid_displacement"),
            (self.within_cluster_scale_change, "within_cluster_scale_change"),
            (self.assignment_margin_drift, "assignment_margin_drift"),
        ):
            if value is not None and _require_finite(value, label) < 0.0:
                raise ValueError(f"{label} must be non-negative")
        present = tuple(
            value is not None
            for value in (
                self.centroid_displacement,
                self.within_cluster_scale_change,
                self.assignment_margin_drift,
            )
        )
        if any(present) and not all(present):
            raise ValueError("adjacent cluster drift metrics must be complete or all absent")


@dataclass(frozen=True, slots=True)
class AdjacentPeriodEvidence:
    """Frequency and structural drift for one chronological adjacent-period pair."""

    earlier_period: str
    later_period: str
    frequency_js_distance: float
    clusters: tuple[AdjacentClusterEvidence, ...]

    def __post_init__(self) -> None:
        for value, label in (
            (self.earlier_period, "earlier_period"),
            (self.later_period, "later_period"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if self.earlier_period == self.later_period:
            raise ValueError("adjacent periods must be distinct")
        _require_bounded(self.frequency_js_distance, "frequency_js_distance", 0.0, 1.0)
        labels = tuple(item.cluster_label for item in self.clusters)
        if not self.clusters or labels != tuple(range(len(self.clusters))):
            raise ValueError("adjacent cluster evidence must use complete canonical labels")


@dataclass(frozen=True, slots=True)
class StabilityReport:
    """Complete evidence used to accept or reject a cluster definition."""

    policy: StabilityPolicy
    seed_ari: tuple[float, ...]
    subsample_ari: tuple[float, ...]
    adjacent_js_distance: float
    asset_coverage: float
    parameter_perturbation_ari: tuple[float, ...]
    accepted: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.policy, StabilityPolicy):
            raise TypeError("policy must be a StabilityPolicy")
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
        object.__setattr__(self, "accepted", _meets_policy(self, self.policy))


@dataclass(frozen=True, slots=True)
class StructuralStabilityReport(StabilityReport):
    """Versioned adjacent-period structural evidence for new stability evaluations."""

    adjacent_period_policy: AdjacentPeriodStabilityPolicy
    cluster_period_support: tuple[ClusterPeriodSupport, ...]
    adjacent_period_evidence: tuple[AdjacentPeriodEvidence, ...]
    algorithm_version: str = field(default=STABILITY_ALGORITHM_VERSION, init=False)

    def __post_init__(self) -> None:
        super(StructuralStabilityReport, self).__post_init__()
        if not isinstance(self.adjacent_period_policy, AdjacentPeriodStabilityPolicy):
            raise TypeError("adjacent_period_policy must be an AdjacentPeriodStabilityPolicy")
        if not self.cluster_period_support:
            raise ValueError("cluster_period_support must be non-empty")
        if not self.adjacent_period_evidence:
            raise ValueError("adjacent_period_evidence must be non-empty")
        if self.algorithm_version != STABILITY_ALGORITHM_VERSION:
            raise ValueError("algorithm_version must match the implemented stability algorithm")
        object.__setattr__(
            self,
            "accepted",
            self.accepted and _meets_adjacent_period_policy(self, self.adjacent_period_policy),
        )

    @property
    def minimum_cluster_event_support(self) -> int:
        return min(item.event_count for item in self.cluster_period_support)

    @property
    def maximum_centroid_displacement(self) -> float | None:
        return _maximum_complete_adjacent_metric(
            self.adjacent_period_evidence, "centroid_displacement"
        )

    @property
    def maximum_within_cluster_scale_change(self) -> float | None:
        return _maximum_complete_adjacent_metric(
            self.adjacent_period_evidence, "within_cluster_scale_change"
        )

    @property
    def maximum_assignment_margin_drift(self) -> float | None:
        return _maximum_complete_adjacent_metric(
            self.adjacent_period_evidence, "assignment_margin_drift"
        )


def evaluate_cluster_stability(
    *,
    discovery: FeatureMatrix,
    development: FeatureMatrix,
    projection: PCAProjection,
    base_result: KMeansResult,
    symbols: Sequence[str],
    asset_universe: Sequence[str],
    periods: Sequence[str],
    period_order: Sequence[str],
    seeds: Sequence[int],
    subsample_fraction: float,
    policy: StabilityPolicy,
    adjacent_period_policy: AdjacentPeriodStabilityPolicy,
    max_iterations: int = _MAX_ITERATIONS,
) -> StructuralStabilityReport:
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
    if not isinstance(adjacent_period_policy, AdjacentPeriodStabilityPolicy):
        raise TypeError("adjacent_period_policy must be an AdjacentPeriodStabilityPolicy")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or not 1 <= max_iterations <= _MAX_ITERATIONS
    ):
        raise ValueError("max_iterations must be within the stability safety bound")
    seed_values = _validated_seeds(seeds)
    total_fit_budget = len(seed_values) * 2 + 2
    if total_fit_budget > _MAX_TOTAL_FITS:
        raise ValueError("aggregate stability fits exceed the safety bound")

    projection_digest = pca_projection_sha256(discovery, projection)
    scores = _validated_projection(projection, discovery_rows, feature_width)
    cluster_count = _validated_clustering(base_result, scores, projection_digest)
    development_scores = _project(development_rows, projection.means, projection.components)
    development_labels = _assign(development_scores, base_result.centroids)
    symbol_values = _validated_names(symbols, len(development_rows), "symbols")
    asset_values = _validated_asset_universe(asset_universe)
    unknown_symbols = tuple(sorted(set(symbol_values) - set(asset_values)))
    if unknown_symbols:
        raise ValueError("observed symbols must belong to the configured asset_universe")
    period_values = _validated_names(periods, len(development_rows), "periods")
    ordered_periods = _validated_period_order(period_order, period_values)
    fraction = _validated_fraction(subsample_fraction)

    seed_ari = tuple(
        adjusted_rand_index(
            base_result.assignments,
            fit_kmeans(
                scores,
                clusters=cluster_count,
                seed=seed,
                max_iterations=max_iterations,
                tolerance=_TOLERANCE,
            ).assignments,
        )
        for seed in seed_values
    )
    subsample_ari = tuple(
        _subsample_ari(
            scores,
            base_result.assignments,
            cluster_count,
            seed,
            fraction,
            max_iterations,
        )
        for seed in seed_values
    )
    cluster_period_support = _cluster_period_support(
        development_labels,
        symbol_values,
        asset_values,
        period_values,
        ordered_periods,
        cluster_count,
    )
    adjacent_period_evidence = _adjacent_period_evidence(
        development_scores,
        development_labels,
        period_values,
        ordered_periods,
        base_result.centroids,
    )
    adjacent_js_distance = max(
        evidence.frequency_js_distance for evidence in adjacent_period_evidence
    )
    asset_coverage = min(support.asset_coverage for support in cluster_period_support)
    parameter_perturbation_ari = _parameter_perturbation_ari(
        scores,
        base_result.assignments,
        cluster_count,
        seed_values[0],
        max_iterations,
    )
    return StructuralStabilityReport(
        policy=policy,
        seed_ari=seed_ari,
        subsample_ari=subsample_ari,
        adjacent_js_distance=adjacent_js_distance,
        asset_coverage=asset_coverage,
        parameter_perturbation_ari=parameter_perturbation_ari,
        adjacent_period_policy=adjacent_period_policy,
        cluster_period_support=cluster_period_support,
        adjacent_period_evidence=adjacent_period_evidence,
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


def _validated_clustering(
    result: KMeansResult,
    scores: tuple[tuple[float, ...], ...],
    projection_digest: str,
) -> int:
    if not isinstance(result, KMeansResult):
        raise TypeError("base_result must be a KMeansResult")
    if not result.centroids:
        raise ValueError("base_result must contain centroids")
    if result.projection_sha256 != projection_digest:
        raise ValueError("base_result must come from the reviewed PCA projection")
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
    if centroids != tuple(sorted(centroids)):
        raise ValueError("base_result centroids must use canonical label ordering")
    for label, centroid in enumerate(centroids):
        members = tuple(
            row for row, assignment in zip(scores, labels, strict=True) if assignment == label
        )
        expected = tuple(
            math.fsum(row[column] for row in members) / len(members) for column in range(width)
        )
        if any(
            not math.isclose(value, mean, rel_tol=1e-12, abs_tol=1e-12)
            for value, mean in zip(centroid, expected, strict=True)
        ):
            raise ValueError("base_result centroid must equal its assigned-row mean")
    if labels != _assign(scores, centroids):
        raise ValueError("base_result assignments must use nearest-centroid canonical ties")
    expected_inertia = math.fsum(
        math.fsum((left - right) ** 2 for left, right in zip(row, centroids[label], strict=True))
        for row, label in zip(scores, labels, strict=True)
    )
    if (
        not math.isfinite(result.inertia)
        or result.inertia < 0.0
        or not math.isclose(result.inertia, expected_inertia, rel_tol=1e-12, abs_tol=1e-12)
    ):
        raise ValueError("base_result inertia must be finite and non-negative")
    if (
        isinstance(result.iterations, bool)
        or not isinstance(result.iterations, int)
        or result.iterations < 1
    ):
        raise ValueError("base_result iterations must be a positive integer")
    if isinstance(result.seed, bool) or not isinstance(result.seed, int):
        raise TypeError("base_result seed must be an integer")
    if (
        isinstance(result.max_iterations, bool)
        or not isinstance(result.max_iterations, int)
        or result.max_iterations < result.iterations
    ):
        raise ValueError("base_result max_iterations must cover its iterations")
    if not math.isfinite(result.tolerance) or result.tolerance < 0.0:
        raise ValueError("base_result tolerance must be finite and non-negative")
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


def _validated_asset_universe(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("asset_universe must be an explicit sequence")
    names = tuple(values)
    if not names or any(not isinstance(value, str) or not value.strip() for value in names):
        raise ValueError("asset_universe must contain non-empty strings")
    if names != tuple(sorted(names)) or len(set(names)) != len(names):
        raise ValueError("asset_universe must use unique canonical order")
    return names


def _validated_period_order(
    period_order: Sequence[str], periods: tuple[str, ...]
) -> tuple[str, ...]:
    if isinstance(period_order, (str, bytes)) or not isinstance(period_order, Sequence):
        raise TypeError("period_order must be an explicit chronological sequence")
    order = tuple(period_order)
    if len(order) < 2 or any(not isinstance(value, str) or not value.strip() for value in order):
        raise ValueError("period_order must contain at least two named chronological periods")
    if len(set(order)) != len(order) or set(order) != set(periods):
        raise ValueError("period_order must uniquely and completely cover periods")
    return order


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
    if len(values) > _MAX_SEEDS:
        raise ValueError("seeds exceed the stability safety bound")
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
    max_iterations: int,
) -> float:
    sample_size = max(cluster_count, math.floor(len(scores) * fraction))
    sample_size = min(sample_size, len(scores))
    randomizer = random.Random(seed ^ 0x5A17)
    indices = tuple(sorted(randomizer.sample(range(len(scores)), sample_size)))
    sampled_base_assignments = tuple(base_assignments[index] for index in indices)
    if len(set(sampled_base_assignments)) < cluster_count:
        return -1.0
    sampled_scores = tuple(scores[index] for index in indices)
    sampled = fit_kmeans(
        sampled_scores,
        clusters=cluster_count,
        seed=seed,
        max_iterations=max_iterations,
        tolerance=_TOLERANCE,
    )
    return adjusted_rand_index(
        sampled_base_assignments,
        sampled.assignments,
    )


def _cluster_period_support(
    labels: tuple[int, ...],
    symbols: tuple[str, ...],
    assets: tuple[str, ...],
    periods: tuple[str, ...],
    ordered_periods: tuple[str, ...],
    cluster_count: int,
) -> tuple[ClusterPeriodSupport, ...]:
    return tuple(
        _period_cluster_support(
            period,
            cluster_label,
            labels,
            symbols,
            periods,
            assets,
        )
        for period in ordered_periods
        for cluster_label in range(cluster_count)
    )


def _period_cluster_support(
    period: str,
    cluster_label: int,
    labels: tuple[int, ...],
    symbols: tuple[str, ...],
    periods: tuple[str, ...],
    assets: tuple[str, ...],
) -> ClusterPeriodSupport:
    counts = Counter(
        symbol
        for label, symbol, row_period in zip(labels, symbols, periods, strict=True)
        if label == cluster_label and row_period == period
    )
    asset_event_counts = tuple((asset, counts[asset]) for asset in assets)
    event_count = sum(count for _, count in asset_event_counts)
    return ClusterPeriodSupport(
        period=period,
        cluster_label=cluster_label,
        event_count=event_count,
        asset_event_counts=asset_event_counts,
        asset_coverage=sum(count > 0 for _, count in asset_event_counts) / len(assets),
    )


def _adjacent_period_evidence(
    scores: tuple[tuple[float, ...], ...],
    labels: tuple[int, ...],
    periods: tuple[str, ...],
    ordered_periods: tuple[str, ...],
    frozen_centroids: Sequence[Sequence[float]],
) -> tuple[AdjacentPeriodEvidence, ...]:
    cluster_count = len(frozen_centroids)
    period_rows = {
        period: tuple(
            (score, label)
            for score, label, row_period in zip(scores, labels, periods, strict=True)
            if row_period == period
        )
        for period in ordered_periods
    }
    period_summaries = {
        period: tuple(
            _cluster_period_summary(rows, cluster_label, frozen_centroids)
            for cluster_label in range(cluster_count)
        )
        for period, rows in period_rows.items()
    }
    return tuple(
        AdjacentPeriodEvidence(
            earlier_period=earlier,
            later_period=later,
            frequency_js_distance=_jensen_shannon_distance(
                _label_distribution(
                    tuple(label for _, label in period_rows[earlier]), cluster_count
                ),
                _label_distribution(tuple(label for _, label in period_rows[later]), cluster_count),
            ),
            clusters=tuple(
                _adjacent_cluster_evidence(
                    cluster_label,
                    period_summaries[earlier][cluster_label],
                    period_summaries[later][cluster_label],
                )
                for cluster_label in range(cluster_count)
            ),
        )
        for earlier, later in zip(ordered_periods, ordered_periods[1:])
    )


def _cluster_period_summary(
    rows: tuple[tuple[tuple[float, ...], int], ...],
    cluster_label: int,
    frozen_centroids: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], float, float] | None:
    members = tuple(score for score, label in rows if label == cluster_label)
    if not members:
        return None
    width = len(members[0])
    centroid = tuple(
        math.fsum(row[column] for row in members) / len(members) for column in range(width)
    )
    scale = math.sqrt(math.fsum(_squared_distance(row, centroid) for row in members) / len(members))
    mean_margin = math.fsum(_assignment_margin(row, frozen_centroids) for row in members) / len(
        members
    )
    return centroid, scale, mean_margin


def _adjacent_cluster_evidence(
    cluster_label: int,
    earlier: tuple[tuple[float, ...], float, float] | None,
    later: tuple[tuple[float, ...], float, float] | None,
) -> AdjacentClusterEvidence:
    if earlier is None or later is None:
        return AdjacentClusterEvidence(cluster_label, None, None, None)
    earlier_centroid, earlier_scale, earlier_margin = earlier
    later_centroid, later_scale, later_margin = later
    return AdjacentClusterEvidence(
        cluster_label=cluster_label,
        centroid_displacement=math.sqrt(_squared_distance(earlier_centroid, later_centroid)),
        within_cluster_scale_change=abs(earlier_scale - later_scale),
        assignment_margin_drift=abs(earlier_margin - later_margin),
    )


def _assignment_margin(row: Sequence[float], frozen_centroids: Sequence[Sequence[float]]) -> float:
    distances = sorted(math.sqrt(_squared_distance(row, centroid)) for centroid in frozen_centroids)
    if len(distances) == 1:
        return 0.0
    return distances[1] - distances[0]


def _squared_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(
        (left_value - right_value) ** 2 for left_value, right_value in zip(left, right, strict=True)
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


def _parameter_perturbation_ari(
    scores: tuple[tuple[float, ...], ...],
    assignments: tuple[int, ...],
    cluster_count: int,
    seed: int,
    max_iterations: int,
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
                max_iterations=max_iterations,
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


def _meets_policy(report: StabilityReport, policy: StabilityPolicy) -> bool:
    return (
        min(report.seed_ari) >= policy.minimum_seed_ari
        and min(report.subsample_ari) >= policy.minimum_subsample_ari
        and report.adjacent_js_distance <= policy.maximum_adjacent_js_distance
        and report.asset_coverage >= policy.minimum_asset_coverage
        and min(report.parameter_perturbation_ari) >= policy.minimum_parameter_perturbation_ari
    )


def _maximum_complete_adjacent_metric(
    evidence: tuple[AdjacentPeriodEvidence, ...],
    field_name: str,
) -> float | None:
    values = tuple(
        getattr(cluster, field_name) for period in evidence for cluster in period.clusters
    )
    if any(value is None for value in values):
        return None
    return max(value for value in values if value is not None)


def _meets_adjacent_period_policy(
    report: StructuralStabilityReport, policy: AdjacentPeriodStabilityPolicy
) -> bool:
    centroid_displacement = report.maximum_centroid_displacement
    scale_change = report.maximum_within_cluster_scale_change
    margin_drift = report.maximum_assignment_margin_drift
    return (
        centroid_displacement is not None
        and scale_change is not None
        and margin_drift is not None
        and centroid_displacement <= policy.maximum_centroid_displacement
        and scale_change <= policy.maximum_within_cluster_scale_change
        and margin_drift <= policy.maximum_assignment_margin_drift
        and report.minimum_cluster_event_support >= policy.minimum_cluster_event_support
    )
