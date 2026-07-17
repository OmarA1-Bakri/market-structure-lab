from __future__ import annotations

from importlib.util import find_spec

import market_structure_lab.transitions as transitions


def test_independence_based_transition_screening_module_is_removed() -> None:
    assert find_spec("market_structure_lab.transitions.screening") is None
    assert "screen_transition_enrichment" not in transitions.__all__
    assert "TransitionEnrichmentCandidate" not in transitions.__all__
