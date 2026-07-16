from __future__ import annotations

import math
from dataclasses import replace

import pytest

from market_structure_lab.discovery import (
    FeatureMatrix,
    PartitionRole,
    StabilityPolicy,
    StabilityReport,
    fit_pca,
    fit_projected_kmeans,
    freeze_behaviours,
)


def _matrix(values: tuple[tuple[float, float], ...]) -> FeatureMatrix:
    return FeatureMatrix(
        row_ids=tuple(f"row-{index}" for index in range(len(values))),
        feature_names=("auction_location", "volume_change"),
        values=values,
        dropped_null_rows=0,
        partition_role=PartitionRole.DISCOVERY,
    )


def _accepted_report() -> StabilityReport:
    return StabilityReport(
        policy=StabilityPolicy(0.8, 0.8, 0.2, 0.5, 0.5),
        seed_ari=(1.0, 1.0),
        subsample_ari=(1.0, 1.0),
        adjacent_js_distance=0.0,
        asset_coverage=1.0,
        parameter_perturbation_ari=(0.8,),
    )


def _catalogue(
    *,
    matrix: FeatureMatrix | None = None,
    projection=None,
    clustering=None,
    stability: StabilityReport | None = None,
    description: str = "Two neutral auction-shape groups observed without outcomes.",
):
    selected_matrix = matrix or _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)))
    selected_projection = projection or fit_pca(selected_matrix, 1)
    selected_clustering = clustering or fit_projected_kmeans(
        selected_matrix,
        selected_projection,
        clusters=2,
        seed=7,
        max_iterations=50,
        tolerance=1e-12,
    )
    return freeze_behaviours(
        run_id="DR-000401",
        matrix=selected_matrix,
        projection=selected_projection,
        clustering=selected_clustering,
        stability=stability or _accepted_report(),
        event_ids=("EV-1", "EV-2", "EV-3", "EV-4"),
        durations_seconds=(60.0, 120.0, 180.0, 240.0),
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        description=description,
    )


def test_freeze_behaviours_returns_complete_outcome_blind_canonical_definitions() -> None:
    first = _catalogue()
    second = _catalogue()

    assert first == second
    assert len(first) == 2
    assert tuple(behaviour.behaviour_id for behaviour in first) == tuple(
        sorted(behaviour.behaviour_id for behaviour in first)
    )
    for behaviour in first:
        assert behaviour.behaviour_id.startswith("B-")
        assert len(behaviour.cluster_definition_sha256) == 64
        assert behaviour.discovery_run_id == "DR-000401"
        assert behaviour.representative_event_ids
        assert len(behaviour.feature_centroid) == 2
        assert tuple(item.feature_name for item in behaviour.feature_distributions) == (
            "auction_location",
            "volume_change",
        )
        assert behaviour.frequency == 2
        assert behaviour.median_duration_seconds > 0.0
        assert behaviour.asset_coverage == ("BTCUSDT", "ETHUSDT")
        assert behaviour.regime_coverage == ()
        assert behaviour.status == "frozen"
        assert not hasattr(behaviour, "future_return")
        assert not hasattr(behaviour, "profitability")


def test_unstable_clusters_are_rejected_without_catalogue_entries() -> None:
    rejected = replace(
        _accepted_report(),
        policy=StabilityPolicy(1.0, 1.0, 0.0, 1.0, 1.0),
        parameter_perturbation_ari=(0.8,),
    )

    assert _catalogue(stability=rejected) == ()
    assert not hasattr(rejected, "status")

    with pytest.raises(ValueError, match="accepted stability"):
        replace(_catalogue()[0], stability=rejected)


def test_behaviour_id_changes_when_frozen_description_changes() -> None:
    original = _catalogue()
    changed = _catalogue(description="A different neutral description of the frozen groups.")

    assert {item.behaviour_id for item in original}.isdisjoint(
        item.behaviour_id for item in changed
    )

    with pytest.raises(ValueError, match="behaviour_id"):
        replace(original[0], description="Another neutral frozen description.")


def test_cluster_definition_hash_changes_with_consistent_centroid_definition() -> None:
    original = _catalogue()
    changed = _catalogue(matrix=_matrix(((-4.0, 1.0), (0.0, 3.0), (9.0, 5.0), (11.0, 7.0))))

    assert {item.cluster_definition_sha256 for item in original} != {
        item.cluster_definition_sha256 for item in changed
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"event_ids": ("EV-1",)}, "event_ids"),
        ({"event_ids": ("EV-1", "EV-1", "EV-3", "EV-4")}, "unique"),
        ({"durations_seconds": (60.0, 120.0, math.inf, 240.0)}, "duration"),
        ({"symbols": ("BTCUSDT",)}, "symbols"),
        ({"description": "Future returns are profitable."}, "outcome"),
        ({"description": "MFE and MAE imply a target hit."}, "outcome"),
        ({"description": "A future volatility continuation label."}, "outcome"),
        ({"description": "Institutional whale smart-money support."}, "unsupported"),
    ],
)
def test_freeze_behaviours_rejects_invalid_lengths_nonfinite_values_and_outcomes(
    changes: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "run_id": "DR-000401",
        "matrix": _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0))),
        "projection": fit_pca(_matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0))), 1),
        "stability": _accepted_report(),
        "event_ids": ("EV-1", "EV-2", "EV-3", "EV-4"),
        "durations_seconds": (60.0, 120.0, 180.0, 240.0),
        "symbols": ("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        "description": "Neutral grouping description.",
    }
    arguments["clustering"] = fit_projected_kmeans(
        arguments["matrix"],  # type: ignore[arg-type]
        arguments["projection"],  # type: ignore[arg-type]
        clusters=2,
        seed=7,
        max_iterations=50,
        tolerance=1e-12,
    )
    arguments.update(changes)

    with pytest.raises((TypeError, ValueError), match=message):
        freeze_behaviours(**arguments)  # type: ignore[arg-type]


def test_freeze_behaviours_rejects_inconsistent_cluster_identity() -> None:
    matrix = _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)))
    projection = fit_pca(matrix, 1)
    clustering = fit_projected_kmeans(
        matrix, projection, clusters=2, seed=7, max_iterations=50, tolerance=1e-12
    )
    with pytest.raises(ValueError, match="centroid"):
        _catalogue(
            matrix=matrix,
            projection=projection,
            clustering=replace(
                clustering,
                centroids=((clustering.centroids[0][0] - 99.0,), clustering.centroids[1]),
            ),
        )


def test_catalogue_accepts_two_raw_features_clustered_in_one_projected_component() -> None:
    behaviours = _catalogue()

    assert all(len(item.feature_centroid) == 2 for item in behaviours)
    assert all(len(item.feature_distributions) == 2 for item in behaviours)
