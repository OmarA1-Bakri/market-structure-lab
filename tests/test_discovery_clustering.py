from __future__ import annotations

import math
from dataclasses import replace

import pytest

from market_structure_lab.discovery import kmeans as kmeans_module
from market_structure_lab.discovery import pca as pca_module
from market_structure_lab.discovery import (
    FeatureMatrix,
    KMeansResult,
    PartitionRole,
    fit_kmeans,
    fit_pca,
    fit_projected_kmeans,
    pca_projection_sha256,
)


def _matrix(values: tuple[tuple[float, ...], ...]) -> FeatureMatrix:
    width = len(values[0]) if values else 0
    return FeatureMatrix(
        row_ids=tuple(f"row-{index}" for index in range(len(values))),
        feature_names=tuple(f"feature_{index}" for index in range(width)),
        values=values,
        dropped_null_rows=0,
        partition_role=PartitionRole.DISCOVERY,
    )


def _assert_consistent_kmeans(values: tuple[tuple[float, ...], ...], result: KMeansResult) -> None:
    for label, centroid in enumerate(result.centroids):
        members = tuple(
            row for row, assignment in zip(values, result.assignments) if assignment == label
        )
        assert members
        means = tuple(
            math.fsum(row[column] for row in members) / len(members)
            for column in range(len(centroid))
        )
        assert centroid == pytest.approx(means)

    for row, assignment in zip(values, result.assignments):
        nearest = min(
            range(len(result.centroids)),
            key=lambda label: math.dist(row, result.centroids[label]),
        )
        assert assignment == nearest


def test_pca_exposes_means_canonical_signs_variance_and_scores() -> None:
    projection = fit_pca(
        _matrix(((1.0, 2.0), (2.0, 4.0), (3.0, 6.0), (4.0, 8.0))),
        n_components=1,
    )

    assert projection.means == pytest.approx((2.5, 5.0))
    assert projection.components[0] == pytest.approx((1 / math.sqrt(5), 2 / math.sqrt(5)))
    for component in projection.components:
        pivot = max(range(len(component)), key=lambda index: (abs(component[index]), -index))
        assert component[pivot] >= 0.0
    assert projection.explained_variance_ratio == pytest.approx((1.0,), abs=1e-15)
    assert sum(score[0] for score in projection.scores) == pytest.approx(0.0, abs=1e-14)
    assert projection.scores[0][0] < projection.scores[-1][0]


def test_pca_canonicalizes_last_bit_svd_backend_variation(monkeypatch) -> None:
    matrix = _matrix(((-10.0, -1.0), (-9.0, -0.9), (9.0, 0.9), (10.0, 1.0)))
    baseline = fit_pca(matrix, 1)
    baseline_sha256 = pca_projection_sha256(matrix, baseline)
    scipy_svd = pca_module.svd

    def varied_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied_right_vectors = right_vectors.copy()
        varied_right_vectors[0, 1] = math.nextafter(
            math.nextafter(float(varied_right_vectors[0, 1]), 0.0),
            0.0,
        )
        return left_vectors, singular_values, varied_right_vectors

    monkeypatch.setattr(pca_module, "svd", varied_svd)
    varied = fit_pca(matrix, 1)

    assert varied == baseline
    assert pca_projection_sha256(matrix, varied) == baseline_sha256


def test_pca_orients_canonical_component_when_backend_noise_changes_raw_pivot(
    monkeypatch,
) -> None:
    matrix = _matrix(((-2.0, 2.0), (-1.0, 1.0), (1.0, -1.0), (2.0, -2.0)))
    scipy_svd = pca_module.svd
    loading = 1 / math.sqrt(2)

    def tied_pivot_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied = right_vectors.copy()
        varied[0, 0], varied[0, 1] = loading, -loading
        return left_vectors, singular_values, varied

    monkeypatch.setattr(pca_module, "svd", tied_pivot_svd)
    tied = fit_pca(matrix, 1)

    def noisy_pivot_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied = right_vectors.copy()
        varied[0, 0] = loading
        varied[0, 1] = -math.nextafter(loading, math.inf)
        return left_vectors, singular_values, varied

    monkeypatch.setattr(pca_module, "svd", noisy_pivot_svd)
    noisy = fit_pca(matrix, 1)

    assert noisy == tied
    assert pca_projection_sha256(matrix, noisy) == pca_projection_sha256(matrix, tied)


def test_pca_discards_scale_relative_near_zero_component_noise(monkeypatch) -> None:
    matrix = _matrix(((-2.0, 0.0), (-1.0, 0.0), (1.0, 0.0), (2.0, 0.0)))
    scipy_svd = pca_module.svd

    def noisy_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied = right_vectors.copy()
        varied[0, 1] = 8 * math.ulp(1.0)
        return left_vectors, singular_values, varied

    baseline = fit_pca(matrix, 1)
    monkeypatch.setattr(pca_module, "svd", noisy_svd)
    noisy = fit_pca(matrix, 1)

    assert noisy == baseline
    assert noisy.components == ((1.0, 0.0),)


def test_pca_canonicalizes_singular_value_backend_ulp_variation(monkeypatch) -> None:
    matrix = _matrix(((0.0, 0.0), (1.0, 2.0), (3.0, 1.0), (4.0, 4.0)))
    baseline = fit_pca(matrix, 1)
    scipy_svd = pca_module.svd

    def varied_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied = singular_values.copy()
        varied[0] = math.nextafter(float(varied[0]), math.inf)
        return left_vectors, varied, right_vectors

    monkeypatch.setattr(pca_module, "svd", varied_svd)
    varied = fit_pca(matrix, 1)

    assert varied == baseline
    assert pca_projection_sha256(matrix, varied) == pca_projection_sha256(matrix, baseline)


@pytest.mark.parametrize(
    ("n_components", "second_singular_value"),
    [
        (2, 4.0),
        (2, 4.0 * (1.0 - 5e-13)),
        (1, 4.0),
        (1, 4.0 * (1.0 - 5e-13)),
    ],
)
def test_pca_rejects_repeated_or_near_repeated_selected_subspaces(
    monkeypatch,
    n_components: int,
    second_singular_value: float,
) -> None:
    matrix = _matrix(((0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (3.0, 1.0, 2.0), (4.0, 4.0, 1.0)))
    scipy_svd = pca_module.svd

    def repeated_svd(*args, **kwargs):
        left_vectors, singular_values, right_vectors = scipy_svd(*args, **kwargs)
        varied = singular_values.copy()
        varied[0], varied[1], varied[2] = 4.0, second_singular_value, 1.0
        return left_vectors, varied, right_vectors

    monkeypatch.setattr(pca_module, "svd", repeated_svd)

    with pytest.raises(ValueError, match="uniquely identifiable"):
        fit_pca(matrix, n_components)


def test_pca_rejects_selected_implicit_wide_null_space() -> None:
    matrix = _matrix(((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)))

    with pytest.raises(ValueError, match="uniquely identifiable"):
        fit_pca(matrix, 2)


def test_pca_projection_identity_requires_current_algorithm_version() -> None:
    matrix = _matrix(((0.0, 0.0), (1.0, 2.0), (2.0, 4.0), (4.0, 8.0)))
    projection = fit_pca(matrix, 1)

    assert projection.algorithm_version == "deterministic-pca-v2"
    with pytest.raises(ValueError, match="PCA algorithm version"):
        pca_projection_sha256(
            matrix,
            replace(projection, algorithm_version="deterministic-pca-v1"),
        )


@pytest.mark.parametrize(
    "role",
    [None, PartitionRole.DEVELOPMENT, PartitionRole.HOLDOUT],
)
def test_pca_rejects_non_discovery_matrix_provenance(role: PartitionRole | None) -> None:
    with pytest.raises(ValueError, match="discovery.*provenance"):
        fit_pca(replace(_matrix(((1.0,), (2.0,))), partition_role=role), 1)


def test_projected_kmeans_pins_projection_and_fit_parameters() -> None:
    matrix = _matrix(((0.0, 0.0), (0.0, 1.0), (10.0, 10.0), (10.0, 11.0)))
    projection = fit_pca(matrix, 1)

    result = fit_projected_kmeans(
        matrix,
        projection,
        clusters=2,
        seed=7,
        max_iterations=50,
        tolerance=1e-12,
    )

    assert result.projection_sha256 is not None
    assert (result.seed, result.max_iterations, result.tolerance) == (7, 50, 1e-12)


@pytest.mark.parametrize("forgery", ["means", "components", "variance"])
def test_projection_hash_rejects_self_consistent_non_pca_evidence(forgery: str) -> None:
    matrix = _matrix(((0.0, 0.0), (1.0, 2.0), (2.0, 4.0), (4.0, 8.0)))
    fitted = fit_pca(matrix, 1)
    means = fitted.means
    components = fitted.components
    variance = fitted.explained_variance_ratio
    if forgery == "means":
        means = (means[0] + 1.0, means[1])
    elif forgery == "components":
        components = ((1.0, 0.0),)
    else:
        variance = (0.5,)
    scores = tuple(
        tuple(
            math.fsum(
                (value - means[column]) * component[column] for column, value in enumerate(row)
            )
            for component in components
        )
        for row in matrix.values
    )
    forged = replace(
        fitted,
        means=means,
        components=components,
        explained_variance_ratio=variance,
        scores=scores,
    )

    with pytest.raises(ValueError, match="deterministic PCA fit"):
        pca_projection_sha256(matrix, forged)

    with pytest.raises(ValueError, match="deterministic PCA fit"):
        fit_projected_kmeans(
            matrix,
            forged,
            clusters=2,
            seed=7,
            max_iterations=50,
            tolerance=1e-12,
        )


@pytest.mark.parametrize("components", [0, -1, True, 3])
def test_pca_rejects_invalid_component_count(components: object) -> None:
    with pytest.raises(ValueError, match="n_components"):
        fit_pca(_matrix(((1.0, 2.0), (2.0, 3.0))), components)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    [
        (),
        ((1.0, 2.0),),
        ((1.0, math.inf), (2.0, 3.0)),
        ((1.0, math.nan), (2.0, 3.0)),
        ((1.0,), (2.0, 3.0)),
    ],
)
def test_pca_rejects_empty_insufficient_nonfinite_or_ragged_values(
    values: tuple[tuple[float, ...], ...],
) -> None:
    with pytest.raises(ValueError):
        fit_pca(_matrix(values), 1)


def test_kmeans_is_seed_deterministic_and_canonicalizes_cluster_labels() -> None:
    values = ((10.0, 10.0), (0.0, 1.0), (10.0, 11.0), (0.0, 0.0))

    first = fit_kmeans(values, clusters=2, seed=7, max_iterations=50, tolerance=1e-12)
    second = fit_kmeans(values, clusters=2, seed=7, max_iterations=50, tolerance=1e-12)

    assert first == second
    assert first.centroids[0] == pytest.approx((0.0, 0.5))
    assert first.centroids[1] == pytest.approx((10.0, 10.5))
    assert first.assignments == (1, 0, 1, 0)
    assert first.inertia == pytest.approx(1.0)
    assert 1 <= first.iterations <= 50
    _assert_consistent_kmeans(values, first)


def test_kmeans_fails_loudly_when_iteration_cap_prevents_consistent_result() -> None:
    values = ((-3.0,), (-3.0,), (-2.0,), (-1.0,), (1.0,))

    with pytest.raises(RuntimeError, match="did not converge"):
        fit_kmeans(
            values,
            clusters=2,
            seed=0,
            max_iterations=1,
            tolerance=0.0,
        )


def test_kmeans_uses_stable_lowest_label_for_equal_distance_ties() -> None:
    result = fit_kmeans(
        ((-1.0,), (0.0,), (1.0,)),
        clusters=2,
        seed=1,
        max_iterations=2,
        tolerance=0.0,
    )

    assert result.centroids == ((-0.5,), (1.0,))
    assert result.assignments == (0, 0, 1)
    _assert_consistent_kmeans(((-1.0,), (0.0,), (1.0,)), result)


def test_kmeans_handles_duplicate_points_without_empty_clusters() -> None:
    result = fit_kmeans(
        ((0.0,), (0.0,), (10.0,), (10.0,)),
        clusters=2,
        seed=3,
        max_iterations=20,
        tolerance=0.0,
    )

    assert result.centroids == ((0.0,), (10.0,))
    assert result.assignments == (0, 0, 1, 1)
    assert result.inertia == 0.0


def test_kmeans_fails_loudly_if_an_assignment_empties_a_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kmeans_module,
        "_initial_centroids",
        lambda rows, clusters, seed: ((0.0,), (0.0,)),
    )

    with pytest.raises(RuntimeError, match="empty cluster"):
        fit_kmeans(
            ((0.0,), (10.0,)),
            clusters=2,
            seed=0,
            max_iterations=10,
            tolerance=0.0,
        )


@pytest.mark.parametrize(
    "values",
    [
        (),
        ((1.0,), (2.0, 3.0)),
        ((1.0,), (math.inf,)),
        ((1.0,), (math.nan,)),
    ],
)
def test_kmeans_rejects_empty_ragged_or_nonfinite_values(
    values: tuple[tuple[float, ...], ...],
) -> None:
    with pytest.raises(ValueError):
        fit_kmeans(values, clusters=1, seed=0, max_iterations=10, tolerance=0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"clusters": 0, "seed": 0, "max_iterations": 10, "tolerance": 0.0},
        {"clusters": 3, "seed": 0, "max_iterations": 10, "tolerance": 0.0},
        {"clusters": True, "seed": 0, "max_iterations": 10, "tolerance": 0.0},
        {"clusters": 1, "seed": True, "max_iterations": 10, "tolerance": 0.0},
        {"clusters": 1, "seed": 0, "max_iterations": 0, "tolerance": 0.0},
        {"clusters": 1, "seed": 0, "max_iterations": True, "tolerance": 0.0},
        {"clusters": 1, "seed": 0, "max_iterations": 10, "tolerance": -1.0},
        {"clusters": 1, "seed": 0, "max_iterations": 10, "tolerance": math.inf},
    ],
)
def test_kmeans_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        fit_kmeans(((1.0,), (2.0,)), **kwargs)  # type: ignore[arg-type]
