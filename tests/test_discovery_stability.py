from __future__ import annotations

import math
from dataclasses import replace

import pytest

from market_structure_lab.discovery import (
    FeatureMatrix,
    PartitionRole,
    StabilityPolicy,
    StabilityReport,
    adjusted_rand_index,
    evaluate_cluster_stability,
    fit_pca,
    fit_projected_kmeans,
)


def _matrix(prefix: str, values: tuple[tuple[float, ...], ...]) -> FeatureMatrix:
    role = PartitionRole.DEVELOPMENT if prefix == "development" else PartitionRole.DISCOVERY
    return FeatureMatrix(
        row_ids=tuple(f"{prefix}-{index}" for index in range(len(values))),
        feature_names=("location",),
        values=values,
        dropped_null_rows=0,
        partition_role=role,
    )


def _stability_fixture():
    discovery = _matrix(
        "discovery",
        tuple(
            (value,)
            for value in (-10.3, -10.2, -10.1, -10.0, -9.9, -9.8, 9.8, 9.9, 10.0, 10.1, 10.2, 10.3)
        ),
    )
    development = _matrix(
        "development",
        tuple((value,) for value in (-10.2, -9.8, 9.8, 10.2, -10.1, -9.9, 9.9, 10.1)),
    )
    projection = fit_pca(discovery, 1)
    clustering = fit_projected_kmeans(
        discovery,
        projection,
        clusters=2,
        seed=17,
        max_iterations=100,
        tolerance=1e-12,
    )
    symbols = ("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT") * 2
    periods = ("2025-02",) * 4 + ("2025-03",) * 4
    return discovery, development, projection, clustering, symbols, periods


def test_adjusted_rand_index_is_exact_and_label_permutation_invariant() -> None:
    assert adjusted_rand_index((0, 0, 1, 1), (9, 9, 4, 4)) == 1.0
    assert adjusted_rand_index((0, 0, 1, 1), (0, 0, 0, 1)) == 0.0
    assert adjusted_rand_index((0, 0, 1, 1), (0, 1, 0, 1)) == -0.5
    assert adjusted_rand_index((0,), (99,)) == 1.0


@pytest.mark.parametrize(
    ("left", "right", "message"),
    [
        ((), (), "non-empty"),
        ((0,), (0, 1), "same length"),
        ((0, True), (0, 1), "integer"),
        ((0, -1), (0, 1), "non-negative"),
    ],
)
def test_adjusted_rand_index_rejects_invalid_partitions(
    left: tuple[object, ...], right: tuple[object, ...], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        adjusted_rand_index(left, right)  # type: ignore[arg-type]


def test_stability_policy_validates_every_frozen_threshold() -> None:
    policy = StabilityPolicy(
        minimum_seed_ari=0.8,
        minimum_subsample_ari=0.7,
        maximum_adjacent_js_distance=0.2,
        minimum_asset_coverage=0.5,
        minimum_parameter_perturbation_ari=0.4,
    )

    assert policy.minimum_seed_ari == 0.8

    for field, value in (
        ("minimum_seed_ari", math.nan),
        ("minimum_subsample_ari", 1.1),
        ("maximum_adjacent_js_distance", -0.1),
        ("minimum_asset_coverage", True),
        ("minimum_parameter_perturbation_ari", math.inf),
    ):
        values = {
            "minimum_seed_ari": 0.8,
            "minimum_subsample_ari": 0.7,
            "maximum_adjacent_js_distance": 0.2,
            "minimum_asset_coverage": 0.5,
            "minimum_parameter_perturbation_ari": 0.4,
        }
        values[field] = value
        with pytest.raises((TypeError, ValueError), match=field):
            StabilityPolicy(**values)  # type: ignore[arg-type]


def test_stability_evaluation_records_seed_subsample_period_asset_and_perturbation_evidence() -> (
    None
):
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()
    policy = StabilityPolicy(
        minimum_seed_ari=0.95,
        minimum_subsample_ari=0.95,
        maximum_adjacent_js_distance=0.01,
        minimum_asset_coverage=1.0,
        minimum_parameter_perturbation_ari=0.5,
    )

    first = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3, 11, 29),
        subsample_fraction=0.75,
        policy=policy,
    )
    second = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3, 11, 29),
        subsample_fraction=0.75,
        policy=policy,
    )

    assert first == second
    assert first.seed_ari == pytest.approx((1.0, 1.0, 1.0))
    assert first.subsample_ari == pytest.approx((1.0, 1.0, 1.0))
    assert first.adjacent_js_distance == pytest.approx(0.0)
    assert first.asset_coverage == pytest.approx(1.0)
    assert first.parameter_perturbation_ari
    assert all(math.isfinite(value) for value in first.parameter_perturbation_ari)
    assert first.accepted


def test_stability_evaluation_rejects_when_a_frozen_threshold_fails() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3, 11),
        subsample_fraction=0.75,
        policy=StabilityPolicy(
            minimum_seed_ari=1.0,
            minimum_subsample_ari=1.0,
            maximum_adjacent_js_distance=0.0,
            minimum_asset_coverage=1.0,
            minimum_parameter_perturbation_ari=1.0,
        ),
    )

    assert not report.accepted
    assert min(report.parameter_perturbation_ari) < 1.0


def test_stability_evaluation_uses_stratified_subsamples_with_duplicate_points() -> None:
    discovery = _matrix(
        "discovery",
        ((-10.0,),) * 10 + ((10.0,),) * 2,
    )
    development = _matrix(
        "development",
        ((-10.0,), (10.0,), (-10.0,), (10.0,)),
    )
    projection = fit_pca(discovery, 1)
    clustering = fit_projected_kmeans(
        discovery,
        projection,
        clusters=2,
        seed=17,
        max_iterations=100,
        tolerance=1e-12,
    )

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        periods=("p1", "p1", "p2", "p2"),
        period_order=("p1", "p2"),
        seeds=(0,),
        subsample_fraction=0.5,
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
    )

    assert report.subsample_ari == (1.0,)


def test_stability_evaluation_requires_discovery_and_development_provenance() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    with pytest.raises(ValueError, match="development.*provenance"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=replace(development, partition_role=PartitionRole.DISCOVERY),
            projection=projection,
            base_result=clustering,
            symbols=symbols,
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        )


def test_stability_evaluation_rejects_nonfinite_projection_evidence() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    with pytest.raises(ValueError, match="explained variance"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=development,
            projection=replace(projection, explained_variance_ratio=(math.nan,)),
            base_result=clustering,
            symbols=symbols,
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"symbols": ("BTCUSDT",)}, "symbols"),
        ({"periods": ("2025-02",)}, "periods"),
        ({"seeds": ()}, "seeds"),
        ({"seeds": (1, 1)}, "unique"),
        ({"subsample_fraction": 0.0}, "subsample_fraction"),
    ],
)
def test_stability_evaluation_rejects_invalid_identity_lengths_and_bounds(
    changes: dict[str, object], message: str
) -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()
    arguments: dict[str, object] = {
        "discovery": discovery,
        "development": development,
        "projection": projection,
        "base_result": clustering,
        "symbols": symbols,
        "periods": periods,
        "period_order": ("2025-02", "2025-03"),
        "seeds": (3, 11),
        "subsample_fraction": 0.75,
        "policy": StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
    }
    arguments.update(changes)

    with pytest.raises((TypeError, ValueError), match=message):
        evaluate_cluster_stability(**arguments)  # type: ignore[arg-type]


def test_stability_evaluation_groups_reappearing_period_labels_across_assets() -> None:
    discovery, development, projection, clustering, symbols, _ = _stability_fixture()

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        periods=("p1", "p1", "p2", "p2", "p1", "p1", "p2", "p2"),
        period_order=("p1", "p2"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
    )

    assert math.isfinite(report.adjacent_js_distance)


def test_stability_requires_explicit_complete_chronological_period_vocabulary() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    with pytest.raises(ValueError, match="period_order"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=development,
            projection=projection,
            base_result=clustering,
            symbols=symbols,
            periods=periods,
            period_order=("2025-02",),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        )


def test_stability_report_derives_acceptance_from_its_frozen_policy() -> None:
    report = StabilityReport(
        policy=StabilityPolicy(1.0, 1.0, 0.0, 1.0, 1.0),
        seed_ari=(1.0,),
        subsample_ari=(1.0,),
        adjacent_js_distance=0.0,
        asset_coverage=1.0,
        parameter_perturbation_ari=(0.5,),
    )

    assert not report.accepted
    with pytest.raises(TypeError, match="init=False"):
        replace(report, accepted=True)


@pytest.mark.parametrize("field", ["centroids", "assignments", "inertia"])
def test_stability_rejects_inconsistent_base_kmeans_evidence(field: str) -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()
    if field == "centroids":
        bad = replace(
            clustering,
            centroids=((clustering.centroids[0][0] - 1.0,), clustering.centroids[1]),
        )
    elif field == "assignments":
        bad = replace(
            clustering,
            assignments=(1 - clustering.assignments[0],) + clustering.assignments[1:],
        )
    else:
        bad = replace(clustering, inertia=clustering.inertia + 1.0)

    with pytest.raises(ValueError, match="centroid|nearest|inertia"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=development,
            projection=projection,
            base_result=bad,
            symbols=symbols,
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        )
