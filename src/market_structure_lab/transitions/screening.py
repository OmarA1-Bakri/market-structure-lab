from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from collections.abc import Sequence

from market_structure_lab.transitions.matrix import State, observed_transitions
from market_structure_lab.transitions.significance import transition_significance


@dataclass(frozen=True)
class TransitionEnrichmentCandidate:
    current_state: State
    next_state: State
    transition_count: int
    current_state_observations: int
    observed_probability: float
    base_probability: float
    lift: float
    p_value: float
    adjusted_p_value: float
    significant: bool


def screen_transition_enrichment(
    states: Sequence[State],
    *,
    alpha: float = 0.05,
    min_support: int = 20,
) -> list[TransitionEnrichmentCandidate]:
    """Screen all observed transitions for enrichment versus next-state base rates."""
    if not 0 < alpha < 1:
        raise ValueError("alpha must be greater than 0 and less than 1")
    if min_support < 1:
        raise ValueError("min_support must be positive")

    transition_counts = observed_transitions(states)
    current_counts: Counter[State] = Counter(states[:-1])
    next_counts: Counter[State] = Counter(states[1:])
    total_next_observations = len(states) - 1

    tested = [
        (
            transition,
            transition_significance(
                current_state=transition.current,
                next_state=transition.next,
                transition_count=count,
                current_state_observations=current_counts[transition.current],
                next_state_base_observations=next_counts[transition.next],
                total_next_state_observations=total_next_observations,
                alpha=alpha,
            ),
        )
        for transition, count in transition_counts.items()
        if count >= min_support
    ]

    adjusted = _benjamini_hochberg([significance.p_value for _, significance in tested])
    candidates = [
        TransitionEnrichmentCandidate(
            current_state=significance.current_state,
            next_state=significance.next_state,
            transition_count=significance.transition_count,
            current_state_observations=significance.current_state_observations,
            observed_probability=significance.observed_probability,
            base_probability=significance.base_probability,
            lift=significance.lift,
            p_value=significance.p_value,
            adjusted_p_value=adjusted_p_value,
            significant=adjusted_p_value < alpha,
        )
        for (_, significance), adjusted_p_value in zip(tested, adjusted, strict=True)
    ]

    return sorted(
        [candidate for candidate in candidates if candidate.significant],
        key=lambda candidate: (
            candidate.adjusted_p_value,
            -candidate.lift,
            str(candidate.current_state),
        ),
    )


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    if not p_values:
        return []

    ranked = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted_by_rank = [0.0] * len(ranked)
    running_min = 1.0

    for reverse_rank, (original_index, p_value) in enumerate(reversed(ranked), start=1):
        rank = len(ranked) - reverse_rank + 1
        adjusted = min(running_min, p_value * len(ranked) / rank)
        running_min = adjusted
        adjusted_by_rank[original_index] = min(adjusted, 1.0)

    return adjusted_by_rank
