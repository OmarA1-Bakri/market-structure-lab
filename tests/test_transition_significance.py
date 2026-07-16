from __future__ import annotations

import pytest

from market_structure_lab.transitions import TransitionSignificance, transition_significance


def test_transition_significance_flags_enriched_transition_against_base_rate() -> None:
    result = transition_significance(
        current_state="below_value",
        next_state="point_of_control",
        transition_count=8,
        current_state_observations=10,
        next_state_base_observations=20,
        total_next_state_observations=100,
    )

    assert result == TransitionSignificance(
        current_state="below_value",
        next_state="point_of_control",
        transition_count=8,
        current_state_observations=10,
        observed_probability=0.8,
        base_probability=0.2,
        lift=4.0,
        p_value=result.p_value,
        significant=True,
    )
    assert result.p_value == pytest.approx(0.0000779264)


def test_transition_significance_does_not_flag_low_lift_transition() -> None:
    result = transition_significance(
        current_state="upper_value",
        next_state="above_value",
        transition_count=3,
        current_state_observations=10,
        next_state_base_observations=30,
        total_next_state_observations=100,
    )

    assert result.observed_probability == 0.3
    assert result.base_probability == 0.3
    assert result.lift == 1.0
    assert result.p_value > 0.05
    assert result.significant is False


def test_transition_significance_requires_valid_counts() -> None:
    with pytest.raises(ValueError, match="transition_count"):
        transition_significance(
            current_state="a",
            next_state="b",
            transition_count=11,
            current_state_observations=10,
            next_state_base_observations=1,
            total_next_state_observations=10,
        )

    with pytest.raises(ValueError, match="total_next_state_observations"):
        transition_significance(
            current_state="a",
            next_state="b",
            transition_count=1,
            current_state_observations=10,
            next_state_base_observations=1,
            total_next_state_observations=0,
        )
