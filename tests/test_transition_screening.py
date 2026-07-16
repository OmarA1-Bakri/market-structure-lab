from __future__ import annotations

import pytest

from market_structure_lab.transitions import screen_transition_enrichment


def test_screen_transition_enrichment_applies_fdr_and_ranks_candidates() -> None:
    states = (
        ["setup", "breakout"] * 18
        + ["setup", "failure"] * 2
        + ["drift", "breakout"] * 2
        + ["drift", "failure"] * 18
    )

    candidates = screen_transition_enrichment(states, alpha=0.05, min_support=5)

    assert [(candidate.current_state, candidate.next_state) for candidate in candidates] == [
        ("failure", "drift"),
        ("breakout", "setup"),
        ("drift", "failure"),
        ("setup", "breakout"),
    ]

    setup_breakout = next(
        candidate
        for candidate in candidates
        if candidate.current_state == "setup" and candidate.next_state == "breakout"
    )
    assert setup_breakout.transition_count == 18
    assert setup_breakout.current_state_observations == 20
    assert setup_breakout.base_probability == pytest.approx(0.2531645570)
    assert setup_breakout.observed_probability == 0.9
    assert setup_breakout.lift == pytest.approx(3.555)
    assert setup_breakout.adjusted_p_value < 0.05
    assert setup_breakout.significant is True


def test_screen_transition_enrichment_filters_low_support_candidates() -> None:
    states = ["rare", "breakout", "common", "failure", "common", "failure"]

    assert screen_transition_enrichment(states, min_support=2) == []


def test_screen_transition_enrichment_rejects_invalid_screening_parameters() -> None:
    with pytest.raises(ValueError, match="min_support"):
        screen_transition_enrichment(["a", "b"], min_support=0)

    with pytest.raises(ValueError, match="alpha"):
        screen_transition_enrichment(["a", "b"], alpha=1.5)
