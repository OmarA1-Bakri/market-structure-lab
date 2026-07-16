"""Empirical transition-matrix primitives."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from collections.abc import Sequence
from typing import TypeAlias

State: TypeAlias = str | Enum


@dataclass(frozen=True)
class Transition:
    current: State
    next: State


@dataclass(frozen=True)
class TransitionRow:
    total_observations: int
    probabilities: dict[State, float]


def observed_transitions(states: Sequence[State]) -> dict[Transition, int]:
    """Count adjacent observed state transitions."""
    _require_sequence(states)

    counts: dict[Transition, int] = {}
    for current, next_state in zip(states, states[1:]):
        transition = Transition(current=current, next=next_state)
        counts[transition] = counts.get(transition, 0) + 1

    return counts


def estimate_transition_matrix(states: Sequence[State]) -> dict[State, TransitionRow]:
    """Estimate P(next_state | current_state) from observed adjacent transitions."""
    counts = observed_transitions(states)
    row_counts: dict[State, dict[State, int]] = defaultdict(dict)

    for transition, count in counts.items():
        row_counts[transition.current][transition.next] = count

    matrix: dict[State, TransitionRow] = {}
    for current, next_counts in row_counts.items():
        total = sum(next_counts.values())
        probabilities = {
            next_state: count / total
            for next_state, count in sorted(next_counts.items(), key=lambda item: str(item[0]))
        }
        matrix[current] = TransitionRow(total_observations=total, probabilities=probabilities)

    return matrix


def _require_sequence(states: Sequence[State]) -> None:
    if len(states) < 2:
        raise ValueError("transition analysis requires at least two states")
