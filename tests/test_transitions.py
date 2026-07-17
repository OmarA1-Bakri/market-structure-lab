from __future__ import annotations

from importlib.util import find_spec

import market_structure_lab.transitions as transitions
import pytest


def test_canonical_transition_surface_exposes_only_boundary_aware_observations() -> None:
    assert transitions.ClusterObservation
    assert transitions.ClusterTransitionEstimate
    assert transitions.ClusterTransitionMatrix
    assert transitions.ClusterTransitionRow
    assert transitions.TransitionBoundaryEvidence
    assert transitions.compress_dwell_runs
    assert transitions.estimate_cluster_transitions

    prohibited = {
        "Transition",
        "TransitionEnrichmentCandidate",
        "TransitionRow",
        "TransitionSignificance",
        "estimate_transition_matrix",
        "observed_transitions",
        "screen_transition_enrichment",
        "transition_significance",
    }
    assert prohibited.isdisjoint(transitions.__all__)
    assert all(not hasattr(transitions, name) for name in prohibited)


def test_raw_adjacent_transition_matrix_module_is_removed() -> None:
    assert find_spec("market_structure_lab.transitions.matrix") is None


def test_canonical_estimator_rejects_raw_adjacent_minute_states() -> None:
    with pytest.raises(TypeError, match="boundary-aware ClusterObservation"):
        transitions.estimate_cluster_transitions(
            (0, 0, 1, 1),  # type: ignore[arg-type]
            horizon=1,
            max_rows=100,
            seed=7,
            bootstrap_iterations=10,
            block_length=1,
            confidence_level=0.9,
        )
