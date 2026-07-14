from __future__ import annotations

from dataclasses import dataclass
from math import comb

from src.transitions.matrix import State


@dataclass(frozen=True)
class TransitionSignificance:
    current_state: State
    next_state: State
    transition_count: int
    current_state_observations: int
    observed_probability: float
    base_probability: float
    lift: float
    p_value: float
    significant: bool


def test_transition_significance(
    *,
    current_state: State,
    next_state: State,
    transition_count: int,
    current_state_observations: int,
    next_state_base_observations: int,
    total_next_state_observations: int,
    alpha: float = 0.05,
) -> TransitionSignificance:
    """Test whether one observed transition is enriched versus the next-state base rate."""
    _validate_counts(
        transition_count=transition_count,
        current_state_observations=current_state_observations,
        next_state_base_observations=next_state_base_observations,
        total_next_state_observations=total_next_state_observations,
    )
    if not 0 < alpha < 1:
        raise ValueError("alpha must be greater than 0 and less than 1")

    observed_probability = transition_count / current_state_observations
    base_probability = next_state_base_observations / total_next_state_observations
    lift = observed_probability / base_probability if base_probability > 0 else float("inf")
    p_value = _binomial_upper_tail(
        successes=transition_count,
        trials=current_state_observations,
        probability=base_probability,
    )

    return TransitionSignificance(
        current_state=current_state,
        next_state=next_state,
        transition_count=transition_count,
        current_state_observations=current_state_observations,
        observed_probability=observed_probability,
        base_probability=base_probability,
        lift=lift,
        p_value=p_value,
        significant=p_value < alpha,
    )


def _validate_counts(
    *,
    transition_count: int,
    current_state_observations: int,
    next_state_base_observations: int,
    total_next_state_observations: int,
) -> None:
    if current_state_observations <= 0:
        raise ValueError("current_state_observations must be positive")
    if total_next_state_observations <= 0:
        raise ValueError("total_next_state_observations must be positive")
    if transition_count < 0 or transition_count > current_state_observations:
        raise ValueError("transition_count must be between 0 and current_state_observations")
    if next_state_base_observations < 0:
        raise ValueError("next_state_base_observations must be greater than or equal to 0")
    if next_state_base_observations > total_next_state_observations:
        raise ValueError("next_state_base_observations cannot exceed total_next_state_observations")


def _binomial_upper_tail(*, successes: int, trials: int, probability: float) -> float:
    if probability == 0:
        return 0.0 if successes > 0 else 1.0
    if probability == 1:
        return 1.0

    return sum(
        comb(trials, observed) * (probability**observed) * ((1 - probability) ** (trials - observed))
        for observed in range(successes, trials + 1)
    )


globals()["test_transition_significance"].__test__ = False
