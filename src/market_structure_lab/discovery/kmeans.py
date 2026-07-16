"""Bounded deterministic K-means for baseline behaviour discovery."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from typing import Sequence

from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.pca import PCAProjection, pca_projection_sha256

_MAX_ROWS = 1_000_000
_MAX_FEATURES = 10_000
_MAX_CELLS = 10_000_000


@dataclass(frozen=True, slots=True)
class KMeansResult:
    """Canonical cluster centroids, row labels, and fit diagnostics."""

    centroids: tuple[tuple[float, ...], ...]
    assignments: tuple[int, ...]
    inertia: float
    iterations: int
    seed: int
    max_iterations: int
    tolerance: float
    projection_sha256: str | None = None


def fit_kmeans(
    values: Sequence[Sequence[float]],
    *,
    clusters: int,
    seed: int,
    max_iterations: int,
    tolerance: float,
) -> KMeansResult:
    """Fit seeded farthest-first K-means with stable ties and canonical labels."""

    rows, width = _validated_values(values)
    if isinstance(clusters, bool) or not isinstance(clusters, int) or clusters < 1:
        raise ValueError("clusters must be a positive integer")
    if clusters > len(rows):
        raise ValueError("clusters cannot exceed the number of rows")
    if len(set(rows)) < clusters:
        raise ValueError("clusters cannot exceed the number of distinct rows")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise ValueError("max_iterations must be a positive integer")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (float, int)):
        raise TypeError("tolerance must be a finite non-negative number")
    tolerance_value = float(tolerance)
    if not math.isfinite(tolerance_value) or tolerance_value < 0.0:
        raise ValueError("tolerance must be a finite non-negative number")

    centroids = _initial_centroids(rows, clusters, seed)
    for iterations in range(1, max_iterations + 1):
        assignments = _assign(rows, centroids)
        updated = _updated_centroids(rows, centroids, assignments, width)
        shift = max(_squared_distance(old, new) for old, new in zip(centroids, updated))
        updated_assignments = _assign(rows, updated)
        centroids = updated
        if shift <= tolerance_value * tolerance_value and updated_assignments == assignments:
            return _result(
                rows,
                centroids,
                updated_assignments,
                iterations,
                seed,
                max_iterations,
                tolerance_value,
            )

    raise RuntimeError(
        f"K-means did not converge to consistent assignments within {max_iterations} iterations"
    )


def _result(
    rows: tuple[tuple[float, ...], ...],
    centroids: tuple[tuple[float, ...], ...],
    assignments: tuple[int, ...],
    iterations: int,
    seed: int,
    max_iterations: int,
    tolerance: float,
) -> KMeansResult:
    canonical_centroids, remapping = _canonical_labels(centroids)
    canonical_assignments = tuple(remapping[label] for label in assignments)
    inertia = math.fsum(
        _squared_distance(row, canonical_centroids[label])
        for row, label in zip(rows, canonical_assignments)
    )
    return KMeansResult(
        centroids=canonical_centroids,
        assignments=canonical_assignments,
        inertia=inertia,
        iterations=iterations,
        seed=seed,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )


def fit_projected_kmeans(
    matrix: FeatureMatrix,
    projection: PCAProjection,
    *,
    clusters: int,
    seed: int,
    max_iterations: int,
    tolerance: float,
) -> KMeansResult:
    """Fit K-means only on a reviewed discovery PCA projection."""

    projection_digest = pca_projection_sha256(matrix, projection)
    result = fit_kmeans(
        projection.scores,
        clusters=clusters,
        seed=seed,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    return replace(result, projection_sha256=projection_digest)


def _validated_values(
    values: Sequence[Sequence[float]],
) -> tuple[tuple[tuple[float, ...], ...], int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("values must be a bounded sequence of rows")
    row_count = len(values)
    if row_count < 1:
        raise ValueError("K-means requires at least one row")
    if row_count > _MAX_ROWS:
        raise ValueError("K-means row count exceeds its safety bound")
    first = values[0]
    if isinstance(first, (str, bytes)) or not isinstance(first, Sequence):
        raise TypeError("K-means rows must be bounded numeric sequences")
    width = len(first)
    if width < 1:
        raise ValueError("K-means requires at least one feature")
    if width > _MAX_FEATURES or row_count * width > _MAX_CELLS:
        raise ValueError("K-means matrix exceeds its safety bound")

    rows: list[tuple[float, ...]] = []
    for row in values:
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise TypeError("K-means rows must be bounded numeric sequences")
        if len(row) != width:
            raise ValueError("K-means rows must have equal width")
        converted: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise TypeError("K-means values must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("K-means values must be finite")
            converted.append(numeric)
        rows.append(tuple(converted))
    return tuple(rows), width


def _initial_centroids(
    rows: tuple[tuple[float, ...], ...], clusters: int, seed: int
) -> tuple[tuple[float, ...], ...]:
    first_index = random.Random(seed).randrange(len(rows))
    indices = [first_index]
    selected = {first_index}
    while len(indices) < clusters:
        candidate = max(
            (index for index in range(len(rows)) if index not in selected),
            key=lambda index: (
                min(_squared_distance(rows[index], rows[chosen]) for chosen in indices),
                -index,
            ),
        )
        indices.append(candidate)
        selected.add(candidate)
    return tuple(rows[index] for index in indices)


def _assign(
    rows: tuple[tuple[float, ...], ...],
    centroids: tuple[tuple[float, ...], ...],
) -> tuple[int, ...]:
    return tuple(
        min(range(len(centroids)), key=lambda label: _squared_distance(row, centroids[label]))
        for row in rows
    )


def _updated_centroids(
    rows: tuple[tuple[float, ...], ...],
    centroids: tuple[tuple[float, ...], ...],
    assignments: tuple[int, ...],
    width: int,
) -> tuple[tuple[float, ...], ...]:
    members = [
        [row for row, assignment in zip(rows, assignments) if assignment == label]
        for label in range(len(centroids))
    ]
    if any(not cluster_rows for cluster_rows in members):
        raise RuntimeError(
            "K-means encountered an empty cluster; deterministic recovery is not defined"
        )
    return tuple(
        tuple(
            math.fsum(row[column] for row in cluster_rows) / len(cluster_rows)
            for column in range(width)
        )
        for cluster_rows in members
    )


def _canonical_labels(
    centroids: tuple[tuple[float, ...], ...],
) -> tuple[tuple[tuple[float, ...], ...], tuple[int, ...]]:
    order = tuple(sorted(range(len(centroids)), key=lambda label: (centroids[label], label)))
    remapping_list = [0] * len(order)
    for canonical, original in enumerate(order):
        remapping_list[original] = canonical
    return tuple(centroids[label] for label in order), tuple(remapping_list)


def _squared_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return math.fsum(
        (left_value - right_value) ** 2 for left_value, right_value in zip(left, right)
    )
