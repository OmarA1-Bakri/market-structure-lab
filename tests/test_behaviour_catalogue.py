from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import pytest

from market_structure_lab.discovery import (
    BehaviourEventBinding,
    FeatureMatrix,
    PartitionRole,
    StabilityPolicy,
    StabilityReport,
    fit_pca,
    fit_projected_kmeans,
    freeze_behaviours,
)


_EVENT_PUBLICATION_SHA256 = "e" * 64


def _event_id(index: int) -> str:
    return f"EV-{hashlib.sha256(f'event-{index}'.encode()).hexdigest().upper()}"


def _bindings(row_ids: tuple[str, ...]) -> tuple[BehaviourEventBinding, ...]:
    return tuple(
        BehaviourEventBinding(
            row_id=row_id,
            event_id=_event_id(index + 1),
            duration_seconds=float((index + 1) * 60),
            event_publication_sha256=_EVENT_PUBLICATION_SHA256,
        )
        for index, row_id in enumerate(row_ids)
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
        event_bindings=_bindings(selected_matrix.row_ids),
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
        ({"event_bindings": _bindings(("row-0",))}, "coverage"),
        (
            {
                "event_bindings": (
                    *_bindings(("row-0", "row-1", "row-2")),
                    BehaviourEventBinding(
                        row_id="row-3",
                        event_id=_event_id(1),
                        duration_seconds=240.0,
                        event_publication_sha256=_EVENT_PUBLICATION_SHA256,
                    ),
                )
            },
            "duplicate event",
        ),
        ({"symbols": ("BTCUSDT",)}, "symbols"),
        ({"description": "Future returns are profitable."}, "outcome"),
        ({"description": "MFE and MAE imply a target hit."}, "outcome"),
        ({"description": "A future volatility continuation label."}, "outcome"),
        ({"description": "Institutional whale smart-money support."}, "unsupported"),
        ({"description": "Next-period PnL reveals winning alpha."}, "outcome"),
        ({"description": "Market-maker accumulation and large-player support."}, "unsupported"),
        (
            {
                "description": (
                    "Subsequent positive returns attributed to informed-trader accumulation."
                )
            },
            "outcome|unsupported",
        ),
        (
            {"description": "Later gains reveal professional-operator support."},
            "outcome|unsupported",
        ),
        (
            {"description": "Future outperformance is attributed to informed desks."},
            "outcome|unsupported|neutral evidence",
        ),
        (
            {"description": ("The state forecasts price appreciation from dealer accumulation.")},
            "outcome|unsupported|neutral evidence",
        ),
        (
            {"description": ("Expected drawdown improves after specialist participation.")},
            "outcome|unsupported|neutral evidence",
        ),
        (
            {"description": "Compression around value precedes a directional move."},
            "neutral evidence",
        ),
        (
            {
                "description": (
                    "Neutral observable auction evidence forecasts profitable future returns."
                )
            },
            "outcome",
        ),
        (
            {"description": ("Observable state promises wealth from hedge-fund accumulation.")},
            "neutral vocabulary",
        ),
        (
            {"description": ("Neutral structure guarantees riches through coordinated funds.")},
            "neutral vocabulary",
        ),
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
        "event_bindings": _bindings(("row-0", "row-1", "row-2", "row-3")),
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


def test_event_binding_rejects_nonfinite_duration() -> None:
    with pytest.raises(ValueError, match="duration"):
        BehaviourEventBinding(
            row_id="row-0",
            event_id=_event_id(1),
            duration_seconds=math.inf,
            event_publication_sha256=_EVENT_PUBLICATION_SHA256,
        )


@pytest.mark.parametrize(
    ("event_id", "duration", "message"),
    (("EV-1", 60.0, "canonical"), (_event_id(1), 0.0, "positive")),
)
def test_event_binding_requires_canonical_event_id_and_positive_duration(
    event_id: str,
    duration: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BehaviourEventBinding(
            row_id="row-0",
            event_id=event_id,
            duration_seconds=duration,
            event_publication_sha256=_EVENT_PUBLICATION_SHA256,
        )


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


def test_event_bindings_are_keyed_by_row_id_not_parallel_position() -> None:
    matrix = _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)))
    projection = fit_pca(matrix, 1)
    clustering = fit_projected_kmeans(
        matrix, projection, clusters=2, seed=7, max_iterations=50, tolerance=1e-12
    )
    arguments = {
        "run_id": "DR-000401",
        "matrix": matrix,
        "projection": projection,
        "clustering": clustering,
        "stability": _accepted_report(),
        "symbols": ("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        "description": "Neutral grouping description.",
    }
    bindings = _bindings(matrix.row_ids)

    canonical = freeze_behaviours(event_bindings=bindings, **arguments)
    reordered = freeze_behaviours(event_bindings=tuple(reversed(bindings)), **arguments)

    assert reordered == canonical
    assert {item.event_bindings_sha256 for item in canonical} == {
        canonical[0].event_bindings_sha256
    }


@pytest.mark.parametrize(
    ("bindings", "message"),
    [
        (_bindings(("row-0", "row-1", "row-2")), "coverage"),
        (_bindings(("row-0", "row-1", "row-2", "row-extra")), "coverage"),
        (_bindings(("row-0", "row-0", "row-2", "row-3")), "duplicate row"),
        (
            (
                *_bindings(("row-0", "row-1", "row-2")),
                BehaviourEventBinding(
                    row_id="row-3",
                    event_id=_event_id(1),
                    duration_seconds=240.0,
                    event_publication_sha256=_EVENT_PUBLICATION_SHA256,
                ),
            ),
            "duplicate event",
        ),
        (
            (
                *_bindings(("row-0", "row-1", "row-2")),
                BehaviourEventBinding(
                    row_id="row-3",
                    event_id=_event_id(4),
                    duration_seconds=240.0,
                    event_publication_sha256="f" * 64,
                ),
            ),
            "publication",
        ),
    ],
)
def test_event_bindings_require_exact_unique_row_and_publication_identity(
    bindings: tuple[BehaviourEventBinding, ...], message: str
) -> None:
    matrix = _matrix(((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)))
    projection = fit_pca(matrix, 1)

    with pytest.raises((TypeError, ValueError), match=message):
        freeze_behaviours(
            run_id="DR-000401",
            matrix=matrix,
            projection=projection,
            clustering=fit_projected_kmeans(
                matrix,
                projection,
                clusters=2,
                seed=7,
                max_iterations=50,
                tolerance=1e-12,
            ),
            stability=_accepted_report(),
            event_bindings=bindings,
            symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
            description="Neutral grouping description.",
        )


def test_catalogue_allows_neutral_observable_large_volume_language() -> None:
    behaviours = _catalogue(
        description="Large volume expansion near observable value-area support."
    )

    assert behaviours
