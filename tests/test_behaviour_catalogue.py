from __future__ import annotations

import math
from dataclasses import replace

import pytest

from market_structure_lab.discovery import (
    FeatureMatrix,
    KMeansResult,
    StabilityReport,
    freeze_behaviours,
)


def _matrix(values: tuple[tuple[float, float], ...]) -> FeatureMatrix:
    return FeatureMatrix(
        row_ids=tuple(f"row-{index}" for index in range(len(values))),
        feature_names=("auction_location", "volume_change"),
        values=values,
        dropped_null_rows=0,
    )


def _accepted_report() -> StabilityReport:
    return StabilityReport(
        seed_ari=(1.0, 1.0),
        subsample_ari=(1.0, 1.0),
        adjacent_js_distance=0.0,
        asset_coverage=1.0,
        parameter_perturbation_ari=(0.8,),
        accepted=True,
    )


def _catalogue(
    *,
    matrix: FeatureMatrix | None = None,
    clustering: KMeansResult | None = None,
    stability: StabilityReport | None = None,
    description: str = "Two neutral auction-shape groups observed without outcomes.",
):
    selected_matrix = matrix or _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)))
    selected_clustering = clustering or KMeansResult(
        centroids=((-1.5, 2.0), (10.0, 6.0)),
        assignments=(0, 0, 1, 1),
        inertia=4.0,
        iterations=2,
    )
    return freeze_behaviours(
        run_id="DR-000401",
        matrix=selected_matrix,
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
    rejected = replace(_accepted_report(), accepted=False)

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


def test_cluster_definition_hash_changes_with_consistent_centroid_definition() -> None:
    original = _catalogue()
    changed = _catalogue(
        matrix=_matrix(((-4.0, 1.0), (0.0, 3.0), (9.0, 5.0), (11.0, 7.0))),
        clustering=KMeansResult(
            centroids=((-2.0, 2.0), (10.0, 6.0)),
            assignments=(0, 0, 1, 1),
            inertia=12.0,
            iterations=2,
        ),
    )

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
    ],
)
def test_freeze_behaviours_rejects_invalid_lengths_nonfinite_values_and_outcomes(
    changes: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "run_id": "DR-000401",
        "matrix": _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0))),
        "clustering": KMeansResult(
            centroids=((-1.5, 2.0), (10.0, 6.0)),
            assignments=(0, 0, 1, 1),
            inertia=4.0,
            iterations=2,
        ),
        "stability": _accepted_report(),
        "event_ids": ("EV-1", "EV-2", "EV-3", "EV-4"),
        "durations_seconds": (60.0, 120.0, 180.0, 240.0),
        "symbols": ("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        "description": "Neutral grouping description.",
    }
    arguments.update(changes)

    with pytest.raises((TypeError, ValueError), match=message):
        freeze_behaviours(**arguments)  # type: ignore[arg-type]


def test_freeze_behaviours_rejects_inconsistent_cluster_identity() -> None:
    with pytest.raises(ValueError, match="centroid"):
        _catalogue(
            clustering=KMeansResult(
                centroids=((-99.0, 2.0), (10.0, 6.0)),
                assignments=(0, 0, 1, 1),
                inertia=4.0,
                iterations=2,
            )
        )
