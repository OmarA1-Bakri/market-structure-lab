from __future__ import annotations

from importlib.util import find_spec

import market_structure_lab.transitions as transitions


def test_independence_based_transition_significance_module_is_removed() -> None:
    assert find_spec("market_structure_lab.transitions.significance") is None
    assert "transition_significance" not in transitions.__all__
    assert "TransitionSignificance" not in transitions.__all__
