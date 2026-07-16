"""Inspectable deterministic principal-component projection."""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.linalg import svd

from market_structure_lab.discovery.matrix import FeatureMatrix


@dataclass(frozen=True, slots=True)
class PCAProjection:
    """PCA fit details and projected rows in deterministic component orientation."""

    means: tuple[float, ...]
    components: tuple[tuple[float, ...], ...]
    explained_variance_ratio: tuple[float, ...]
    scores: tuple[tuple[float, ...], ...]


def fit_pca(matrix: FeatureMatrix, n_components: int) -> PCAProjection:
    """Fit PCA with SciPy SVD and expose all evidence needed to inspect the fit."""

    if not isinstance(matrix, FeatureMatrix):
        raise TypeError("matrix must be a FeatureMatrix")
    rows, width = _validated_values(matrix)
    if isinstance(n_components, bool) or not isinstance(n_components, int):
        raise ValueError("n_components must be a positive integer")
    if n_components < 1 or n_components > min(len(rows), width):
        raise ValueError("n_components exceeds the available matrix rank dimensions")

    means = tuple(math.fsum(row[column] for row in rows) / len(rows) for column in range(width))
    centered = tuple(
        tuple(value - means[column] for column, value in enumerate(row)) for row in rows
    )
    _, singular_values, right_vectors = svd(
        centered,
        full_matrices=False,
        check_finite=True,
        lapack_driver="gesvd",
    )
    squared = tuple(float(value) ** 2 for value in singular_values)
    total_variance = math.fsum(squared)
    if total_variance <= 0.0 or not math.isfinite(total_variance):
        raise ValueError("PCA requires positive finite variance")

    components = tuple(
        _canonical_component(tuple(float(value) for value in right_vectors[index]))
        for index in range(n_components)
    )
    scores = tuple(
        tuple(
            math.fsum(value * loading for value, loading in zip(row, component))
            for component in components
        )
        for row in centered
    )
    return PCAProjection(
        means=means,
        components=components,
        explained_variance_ratio=tuple(
            squared[index] / total_variance for index in range(n_components)
        ),
        scores=scores,
    )


def _validated_values(matrix: FeatureMatrix) -> tuple[tuple[tuple[float, ...], ...], int]:
    if not matrix.values:
        raise ValueError("PCA requires a non-empty matrix")
    if len(matrix.values) < 2:
        raise ValueError("PCA requires at least two rows")
    width = len(matrix.values[0])
    if width < 1:
        raise ValueError("PCA requires at least one feature")
    if len(matrix.row_ids) != len(matrix.values) or len(matrix.feature_names) != width:
        raise ValueError("FeatureMatrix identity dimensions do not match its values")
    rows: list[tuple[float, ...]] = []
    for row in matrix.values:
        if len(row) != width:
            raise ValueError("PCA matrix rows must have equal width")
        converted: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise TypeError("PCA values must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("PCA values must be finite")
            converted.append(numeric)
        rows.append(tuple(converted))
    return tuple(rows), width


def _canonical_component(component: tuple[float, ...]) -> tuple[float, ...]:
    pivot = max(range(len(component)), key=lambda index: (abs(component[index]), -index))
    if component[pivot] < 0.0:
        return tuple(-value for value in component)
    return component
