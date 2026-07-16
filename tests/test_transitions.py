from __future__ import annotations

import pytest

from market_structure_lab.auction import AuctionLocation
from market_structure_lab.transitions import (
    Transition,
    estimate_transition_matrix,
    observed_transitions,
)


def test_observed_transitions_counts_adjacent_state_changes() -> None:
    states = [
        AuctionLocation.BELOW_VALUE,
        AuctionLocation.LOWER_VALUE,
        AuctionLocation.POINT_OF_CONTROL,
        AuctionLocation.LOWER_VALUE,
        AuctionLocation.POINT_OF_CONTROL,
    ]

    transitions = observed_transitions(states)

    assert transitions == {
        Transition(AuctionLocation.BELOW_VALUE, AuctionLocation.LOWER_VALUE): 1,
        Transition(AuctionLocation.LOWER_VALUE, AuctionLocation.POINT_OF_CONTROL): 2,
        Transition(AuctionLocation.POINT_OF_CONTROL, AuctionLocation.LOWER_VALUE): 1,
    }


def test_estimate_transition_matrix_returns_conditional_probabilities_and_support() -> None:
    states = [
        AuctionLocation.LOWER_VALUE,
        AuctionLocation.POINT_OF_CONTROL,
        AuctionLocation.LOWER_VALUE,
        AuctionLocation.POINT_OF_CONTROL,
        AuctionLocation.UPPER_VALUE,
    ]

    matrix = estimate_transition_matrix(states)

    lower_row = matrix[AuctionLocation.LOWER_VALUE]
    assert lower_row.total_observations == 2
    assert lower_row.probabilities == {AuctionLocation.POINT_OF_CONTROL: 1.0}

    poc_row = matrix[AuctionLocation.POINT_OF_CONTROL]
    assert poc_row.total_observations == 2
    assert poc_row.probabilities == {
        AuctionLocation.LOWER_VALUE: 0.5,
        AuctionLocation.UPPER_VALUE: 0.5,
    }


def test_estimate_transition_matrix_supports_string_states_for_research_features() -> None:
    matrix = estimate_transition_matrix(["balance", "imbalance_up", "balance"])

    assert matrix["balance"].probabilities == {"imbalance_up": 1.0}
    assert matrix["imbalance_up"].probabilities == {"balance": 1.0}


def test_transition_analysis_requires_at_least_two_states() -> None:
    with pytest.raises(ValueError, match="at least two"):
        observed_transitions([AuctionLocation.BELOW_VALUE])

    with pytest.raises(ValueError, match="at least two"):
        estimate_transition_matrix([])
