from __future__ import annotations

import math

import pytest

from market_structure_lab.discovery import kmeans as kmeans_module
from market_structure_lab.discovery import (
    FeatureMatrix,
    KMeansResult,
    fit_kmeans,
    fit_pca,
)


def _matrix(values: tuple[tuple[float, ...], ...]) -> FeatureMatrix:
    width = len(values[0]) if values else 0
    return FeatureMatrix(
        row_ids=tuple(f"row-{index}" for index in range(len(values))),
        feature_names=tuple(f"feature_{index}" for index in range(width)),
        values=values,
        dropped_null_rows=0,
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
        n_components=2,
    )

    assert projection.means == pytest.approx((2.5, 5.0))
    assert projection.components[0] == pytest.approx((1 / math.sqrt(5), 2 / math.sqrt(5)))
    for component in projection.components:
        pivot = max(range(len(component)), key=lambda index: (abs(component[index]), -index))
        assert component[pivot] >= 0.0
    assert projection.explained_variance_ratio == pytest.approx((1.0, 0.0), abs=1e-15)
    assert tuple(
        sum(score[index] for score in projection.scores) for index in range(2)
    ) == pytest.approx((0.0, 0.0), abs=1e-14)
    assert projection.scores[0][0] < projection.scores[-1][0]


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
