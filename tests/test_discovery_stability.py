from __future__ import annotations

import math
from dataclasses import replace

import pytest
import market_structure_lab.discovery.stability as stability_module

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
from market_structure_lab.discovery.stability import (
    STABILITY_ALGORITHM_VERSION,
    AdjacentPeriodStabilityPolicy,
    StructuralStabilityReport,
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


def _software_fixture_policy(**changes: object) -> AdjacentPeriodStabilityPolicy:
    values: dict[str, object] = {
        "policy_id": "software-test-adjacent-period-v1",
        "policy_purpose": "software_fixture",
        "maximum_centroid_displacement": 1.0,
        "maximum_within_cluster_scale_change": 1.0,
        "maximum_assignment_margin_drift": 1.0,
        "minimum_cluster_event_support": 2,
    }
    values.update(changes)
    return AdjacentPeriodStabilityPolicy(**values)  # type: ignore[arg-type]


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_id", ""),
        ("policy_purpose", "automatic"),
        ("maximum_centroid_displacement", math.nan),
        ("maximum_within_cluster_scale_change", -0.1),
        ("maximum_assignment_margin_drift", True),
        ("minimum_cluster_event_support", 0),
    ],
)
def test_adjacent_period_software_fixture_policy_validates_every_threshold(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "policy_id": "software-test-adjacent-period-v1",
        "policy_purpose": "software_fixture",
        "maximum_centroid_displacement": 1.0,
        "maximum_within_cluster_scale_change": 1.0,
        "maximum_assignment_margin_drift": 1.0,
        "minimum_cluster_event_support": 2,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=field):
        AdjacentPeriodStabilityPolicy(**values)  # type: ignore[arg-type]


def test_stability_evaluation_requires_explicit_adjacent_period_policy() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    with pytest.raises(TypeError, match="adjacent_period_policy"):
        evaluate_cluster_stability(  # type: ignore[call-arg]
            discovery=discovery,
            development=development,
            projection=projection,
            base_result=clustering,
            symbols=symbols,
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(-1.0, -1.0, 1.0, 0.0, -1.0),
        )


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
        asset_universe=tuple(sorted(set(symbols))),
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3, 11, 29),
        subsample_fraction=0.75,
        policy=policy,
        adjacent_period_policy=_software_fixture_policy(),
    )
    second = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        asset_universe=tuple(sorted(set(symbols))),
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3, 11, 29),
        subsample_fraction=0.75,
        policy=policy,
        adjacent_period_policy=_software_fixture_policy(),
    )

    assert first == second
    assert first.seed_ari == pytest.approx((1.0, 1.0, 1.0))
    assert first.subsample_ari == pytest.approx((1.0, 1.0, 1.0))
    assert first.adjacent_js_distance == pytest.approx(0.0)
    assert first.asset_coverage == pytest.approx(1.0)
    assert first.parameter_perturbation_ari
    assert all(math.isfinite(value) for value in first.parameter_perturbation_ari)
    assert isinstance(first, StructuralStabilityReport)
    assert first.algorithm_version == STABILITY_ALGORITHM_VERSION
    assert first.minimum_cluster_event_support == 2
    assert len(first.cluster_period_support) == 4
    assert all(
        support.asset_event_counts == (("BTCUSDT", 1), ("ETHUSDT", 1))
        for support in first.cluster_period_support
    )
    assert first.adjacent_period_evidence[0].frequency_js_distance == pytest.approx(0.0)
    assert all(
        cluster.assignment_margin_drift > 0.0
        for cluster in first.adjacent_period_evidence[0].clusters
    )
    assert first.accepted


def test_stability_refits_use_the_frozen_iteration_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()
    observed: list[int] = []
    original = stability_module.fit_kmeans

    def record_fit(*args, **kwargs):
        observed.append(kwargs["max_iterations"])
        return original(*args, **kwargs)

    monkeypatch.setattr(stability_module, "fit_kmeans", record_fit)
    with pytest.raises(RuntimeError, match="within 1 iterations"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=development,
            projection=projection,
            base_result=clustering,
            symbols=symbols,
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3, 11),
            subsample_fraction=0.75,
            policy=StabilityPolicy(-1.0, -1.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(),
            max_iterations=1,
        )

    assert observed
    assert set(observed) == {1}


def test_stability_evaluation_rejects_when_a_frozen_threshold_fails() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        asset_universe=tuple(sorted(set(symbols))),
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
        adjacent_period_policy=_software_fixture_policy(),
    )

    assert not report.accepted
    assert min(report.parameter_perturbation_ari) < 1.0


def test_stability_evaluation_uses_deterministic_unstratified_subsamples() -> None:
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
        asset_universe=("BTCUSDT", "ETHUSDT"),
        periods=("p1", "p1", "p2", "p2"),
        period_order=("p1", "p2"),
        seeds=(5,),
        subsample_fraction=0.5,
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
    )
    replay = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        asset_universe=("BTCUSDT", "ETHUSDT"),
        periods=("p1", "p1", "p2", "p2"),
        period_order=("p1", "p2"),
        seeds=(5,),
        subsample_fraction=0.5,
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
    )

    assert report == replay
    assert report.subsample_ari == (-1.0,)


def test_stability_rejects_total_fit_budget_before_refitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery = _matrix("discovery", ((-2.0,), (-1.0,), (1.0,), (2.0,)))
    development = _matrix("development", ((-2.0,), (-1.0,), (1.0,), (2.0,)))
    projection = fit_pca(discovery, 1)
    clustering = fit_projected_kmeans(
        discovery,
        projection,
        clusters=2,
        seed=7,
        max_iterations=20,
        tolerance=1e-12,
    )
    monkeypatch.setattr(stability_module, "_MAX_TOTAL_FITS", 2)
    monkeypatch.setattr(
        stability_module,
        "fit_kmeans",
        lambda *args, **kwargs: pytest.fail("fit ran before aggregate budget preflight"),
    )

    with pytest.raises(ValueError, match="stability fits"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=development,
            projection=projection,
            base_result=clustering,
            symbols=("BTCUSDT",) * 4,
            asset_universe=("BTCUSDT",),
            periods=("p1", "p1", "p2", "p2"),
            period_order=("p1", "p2"),
            seeds=(3, 11),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(),
        )


@pytest.mark.parametrize(
    ("later_values", "policy_changes", "metric", "expected"),
    [
        (
            (-6.2, -5.8, 5.8, 6.2),
            {"maximum_centroid_displacement": 1.0},
            "maximum_centroid_displacement",
            4.0,
        ),
        (
            (-14.0, -6.0, 6.0, 14.0),
            {"maximum_within_cluster_scale_change": 1.0},
            "maximum_within_cluster_scale_change",
            3.8,
        ),
        (
            (-6.2, -5.8, 5.8, 6.2),
            {"maximum_assignment_margin_drift": 1.0},
            "maximum_assignment_margin_drift",
            7.85,
        ),
    ],
)
def test_constant_frequency_adjacent_structural_drift_fails_closed(
    later_values: tuple[float, ...],
    policy_changes: dict[str, object],
    metric: str,
    expected: float,
) -> None:
    discovery, _, projection, clustering, _, _ = _stability_fixture()
    development = _matrix(
        "development",
        tuple((value,) for value in (-10.2, -9.8, 9.8, 10.2, *later_values)),
    )
    permissive = {
        "maximum_centroid_displacement": 100.0,
        "maximum_within_cluster_scale_change": 100.0,
        "maximum_assignment_margin_drift": 100.0,
        "minimum_cluster_event_support": 2,
    }
    permissive.update(policy_changes)

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT") * 2,
        asset_universe=("BTCUSDT", "ETHUSDT"),
        periods=("p1",) * 4 + ("p2",) * 4,
        period_order=("p1", "p2"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(-1.0, -1.0, 0.0, 1.0, -1.0),
        adjacent_period_policy=AdjacentPeriodStabilityPolicy(
            policy_id="software-test-drift-v1",
            policy_purpose="software_fixture",
            **permissive,  # type: ignore[arg-type]
        ),
    )

    assert report.adjacent_js_distance == pytest.approx(0.0)
    assert report.asset_coverage == pytest.approx(1.0)
    assert getattr(report, metric) == pytest.approx(expected)
    assert not report.accepted


def test_minimum_event_support_is_independent_from_asset_coverage() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        asset_universe=tuple(sorted(set(symbols))),
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(-1.0, -1.0, 1.0, 1.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=3),
    )

    assert report.asset_coverage == pytest.approx(1.0)
    assert report.minimum_cluster_event_support == 2
    assert not report.accepted


def test_configured_asset_without_rows_is_recorded_and_reduces_coverage() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=symbols,
        asset_universe=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        periods=periods,
        period_order=("2025-02", "2025-03"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(-1.0, -1.0, 1.0, 1.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(),
    )

    assert report.asset_coverage == pytest.approx(2 / 3)
    assert all(
        support.asset_event_counts[-1] == ("SOLUSDT", 0)
        for support in report.cluster_period_support
    )
    assert not report.accepted


def test_empty_adjacent_period_cluster_has_explicit_zero_support_and_fails_closed() -> None:
    discovery, _, projection, clustering, _, _ = _stability_fixture()
    development = _matrix(
        "development",
        tuple((value,) for value in (-10.2, -9.8, 9.8, 10.2, -10.3, -10.1, -9.9, -9.7)),
    )

    report = evaluate_cluster_stability(
        discovery=discovery,
        development=development,
        projection=projection,
        base_result=clustering,
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT") * 2,
        asset_universe=("BTCUSDT", "ETHUSDT"),
        periods=("p1",) * 4 + ("p2",) * 4,
        period_order=("p1", "p2"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(-1.0, -1.0, 1.0, 0.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
    )

    empty = next(
        support
        for support in report.cluster_period_support
        if support.period == "p2" and support.cluster_label == 1
    )
    assert empty.event_count == 0
    assert empty.asset_event_counts == (("BTCUSDT", 0), ("ETHUSDT", 0))
    assert report.minimum_cluster_event_support == 0
    assert report.adjacent_period_evidence[0].clusters[1].centroid_displacement is None
    assert not report.accepted


def test_stability_evaluation_requires_discovery_and_development_provenance() -> None:
    discovery, development, projection, clustering, symbols, periods = _stability_fixture()

    with pytest.raises(ValueError, match="development.*provenance"):
        evaluate_cluster_stability(
            discovery=discovery,
            development=replace(development, partition_role=PartitionRole.DISCOVERY),
            projection=projection,
            base_result=clustering,
            symbols=symbols,
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
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
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"symbols": ("BTCUSDT",)}, "symbols"),
        ({"asset_universe": ("BTCUSDT",)}, "asset_universe"),
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
        "asset_universe": tuple(sorted(set(symbols))),
        "periods": periods,
        "period_order": ("2025-02", "2025-03"),
        "seeds": (3, 11),
        "subsample_fraction": 0.75,
        "policy": StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        "adjacent_period_policy": _software_fixture_policy(minimum_cluster_event_support=1),
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
        asset_universe=tuple(sorted(set(symbols))),
        periods=("p1", "p1", "p2", "p2", "p1", "p1", "p2", "p2"),
        period_order=("p1", "p2"),
        seeds=(3,),
        subsample_fraction=0.75,
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
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
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02",),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
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
            asset_universe=tuple(sorted(set(symbols))),
            periods=periods,
            period_order=("2025-02", "2025-03"),
            seeds=(3,),
            subsample_fraction=0.75,
            policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
            adjacent_period_policy=_software_fixture_policy(minimum_cluster_event_support=1),
        )
