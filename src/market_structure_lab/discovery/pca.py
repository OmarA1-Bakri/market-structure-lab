"""Inspectable deterministic principal-component projection."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass

from scipy.linalg import svd

from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.splits import PartitionRole

_CANONICAL_SIGNIFICANT_DIGITS = 14
_COMPONENT_ZERO_RELATIVE_TOLERANCE = 64 * math.ulp(1.0)
_PCA_ALGORITHM_VERSION = "deterministic-pca-v2"
_SINGULAR_SUBSPACE_RELATIVE_GAP = 1e-12


@dataclass(frozen=True, slots=True)
class PCAProjection:
    """PCA fit details and projected rows in deterministic component orientation."""

    algorithm_version: str
    means: tuple[float, ...]
    components: tuple[tuple[float, ...], ...]
    explained_variance_ratio: tuple[float, ...]
    scores: tuple[tuple[float, ...], ...]


def fit_pca(matrix: FeatureMatrix, n_components: int) -> PCAProjection:
    """Fit PCA with SciPy SVD and expose all evidence needed to inspect the fit."""

    if not isinstance(matrix, FeatureMatrix):
        raise TypeError("matrix must be a FeatureMatrix")
    if matrix.partition_role is not PartitionRole.DISCOVERY:
        raise ValueError("PCA fit requires discovery partition provenance")
    rows, width = _validated_values(matrix)
    if isinstance(n_components, bool) or not isinstance(n_components, int):
        raise ValueError("n_components must be a positive integer")
    if n_components < 1 or n_components > min(len(rows), width):
        raise ValueError("n_components exceeds the available matrix rank dimensions")

    fitted_means = tuple(
        math.fsum(row[column] for row in rows) / len(rows) for column in range(width)
    )
    centered = tuple(
        tuple(value - fitted_means[column] for column, value in enumerate(row)) for row in rows
    )
    _, singular_values, right_vectors = svd(
        centered,
        full_matrices=False,
        check_finite=True,
        lapack_driver="gesvd",
    )
    _require_identifiable_subspace(singular_values, n_components)
    squared = tuple(float(value) ** 2 for value in singular_values)
    total_variance = math.fsum(squared)
    if total_variance <= 0.0 or not math.isfinite(total_variance):
        raise ValueError("PCA requires positive finite variance")

    means = tuple(_canonical_float(value) for value in fitted_means)
    components = tuple(
        _canonical_component(tuple(float(value) for value in right_vectors[index]))
        for index in range(n_components)
    )
    scores = tuple(
        tuple(
            _canonical_float(
                math.fsum(
                    (value - means[column]) * component[column]
                    for column, value in enumerate(row)
                )
            )
            for component in components
        )
        for row in rows
    )
    return PCAProjection(
        algorithm_version=_PCA_ALGORITHM_VERSION,
        means=means,
        components=components,
        explained_variance_ratio=tuple(
            _canonical_float(squared[index] / total_variance) for index in range(n_components)
        ),
        scores=scores,
    )


def pca_projection_sha256(matrix: FeatureMatrix, projection: PCAProjection) -> str:
    """Validate and hash a discovery matrix together with its PCA evidence."""

    if not isinstance(matrix, FeatureMatrix):
        raise TypeError("matrix must be a FeatureMatrix")
    if matrix.partition_role is not PartitionRole.DISCOVERY:
        raise ValueError("PCA projection requires discovery partition provenance")
    rows, width = _validated_values(matrix)
    if not isinstance(projection, PCAProjection):
        raise TypeError("projection must be a PCAProjection")
    if projection.algorithm_version != _PCA_ALGORITHM_VERSION:
        raise ValueError("PCA algorithm version must match the current deterministic fit")
    if len(projection.means) != width or not projection.components:
        raise ValueError("PCA projection dimensions do not match its source matrix")
    means = tuple(_finite(value, "PCA mean") for value in projection.means)
    components = tuple(
        tuple(_finite(value, "PCA component") for value in component)
        for component in projection.components
    )
    if any(len(component) != width for component in components):
        raise ValueError("PCA component width does not match its source matrix")
    variance = tuple(
        _finite(value, "PCA explained variance") for value in projection.explained_variance_ratio
    )
    if len(variance) != len(components) or any(value < 0.0 or value > 1.0 for value in variance):
        raise ValueError("PCA explained variance must match components and use [0, 1]")
    if not 0.0 < math.fsum(variance) <= 1.0 + 1e-12:
        raise ValueError("PCA explained variance total must be in (0, 1]")
    expected_scores = tuple(
        tuple(
            math.fsum(
                (value - means[column]) * component[column] for column, value in enumerate(row)
            )
            for component in components
        )
        for row in rows
    )
    scores = tuple(tuple(_finite(value, "PCA score") for value in row) for row in projection.scores)
    if len(scores) != len(expected_scores) or any(len(row) != len(components) for row in scores):
        raise ValueError("PCA scores do not match source matrix dimensions")
    for supplied, expected in zip(scores, expected_scores, strict=True):
        if any(
            not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
            for left, right in zip(supplied, expected, strict=True)
        ):
            raise ValueError("PCA scores do not match the frozen projection definition")
    deterministic = fit_pca(matrix, len(components))
    if projection != deterministic:
        raise ValueError("PCA projection must exactly match the deterministic PCA fit")
    payload = {
        "matrix": {
            "row_ids": matrix.row_ids,
            "feature_names": matrix.feature_names,
            "values": rows,
            "partition_role": matrix.partition_role.value,
        },
        "projection": {
            "algorithm_version": projection.algorithm_version,
            "means": means,
            "components": components,
            "explained_variance_ratio": variance,
            "scores": scores,
        },
    }
    canonical = json.dumps(
        payload, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    scale = max(abs(value) for value in component)
    zero_threshold = scale * _COMPONENT_ZERO_RELATIVE_TOLERANCE
    canonical = tuple(
        0.0 if abs(value) <= zero_threshold else _canonical_float(value) for value in component
    )
    pivot = max(range(len(canonical)), key=lambda index: (abs(canonical[index]), -index))
    direction = -1.0 if canonical[pivot] < 0.0 else 1.0
    return tuple(_canonical_float(direction * value) for value in canonical)


def _require_identifiable_subspace(
    singular_values: Iterable[float], n_components: int
) -> None:
    values = tuple(float(value) for value in singular_values)
    leading = values[0]
    numerical_rank = sum(
        value > leading * _SINGULAR_SUBSPACE_RELATIVE_GAP for value in values
    )
    if n_components > numerical_rank:
        raise ValueError(
            "selected PCA singular-value subspace must be uniquely identifiable"
        )
    comparison_count = n_components if n_components < len(values) else n_components - 1
    for index in range(comparison_count):
        left, right = values[index], values[index + 1]
        scale = max(abs(left), abs(right))
        if scale == 0.0 or abs(left - right) <= scale * _SINGULAR_SUBSPACE_RELATIVE_GAP:
            raise ValueError(
                "selected PCA singular-value subspace must be uniquely identifiable"
            )


def _canonical_float(value: float) -> float:
    canonical = float(format(value, f".{_CANONICAL_SIGNIFICANT_DIGITS}g"))
    return 0.0 if canonical == 0.0 else canonical


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric
