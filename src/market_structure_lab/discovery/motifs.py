"""Bounded, deterministic, outcome-blind sequence motif discovery."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Sequence

_MAX_WINDOWS = 10_000
_MAX_WINDOW_LENGTH = 10_000
_MAX_TOP_K = 10_000
_MAX_COMPARISONS = 5_000_000


@dataclass(frozen=True, slots=True)
class MotifMatch:
    """A canonical pair of matching z-normalized subsequence windows."""

    left_start: int
    right_start: int
    distance: float
    window_length: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.left_start, "left_start"),
            (self.right_start, "right_start"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.left_start >= self.right_start:
            raise ValueError("motif starts must be strictly ordered")
        if not math.isfinite(self.distance) or self.distance < 0.0:
            raise ValueError("distance must be finite and non-negative")
        if (
            isinstance(self.window_length, bool)
            or not isinstance(self.window_length, int)
            or self.window_length < 2
        ):
            raise ValueError("window_length must be an integer of at least two")


def discover_motifs(
    sequence: Sequence[float | None],
    *,
    window_length: int,
    exclusion_zone: int,
    max_windows: int,
    top_k: int,
) -> tuple[MotifMatch, ...]:
    """Return the closest valid normalized windows without filling missing data."""

    values = _validated_sequence(sequence)
    window_size = _positive_integer(window_length, "window_length", minimum=2)
    zone = _positive_integer(exclusion_zone, "exclusion_zone", minimum=0)
    window_cap = _positive_integer(max_windows, "max_windows", minimum=1)
    result_cap = _positive_integer(top_k, "top_k", minimum=1)
    if window_cap > _MAX_WINDOWS:
        raise ValueError(f"max_windows cannot exceed {_MAX_WINDOWS}")
    if window_size > _MAX_WINDOW_LENGTH:
        raise ValueError(f"window_length cannot exceed {_MAX_WINDOW_LENGTH}")
    if result_cap > _MAX_TOP_K:
        raise ValueError(f"top_k cannot exceed {_MAX_TOP_K}")
    if window_size > len(values):
        raise ValueError("window_length cannot exceed the sequence length")
    window_count = len(values) - window_size + 1
    if window_count > window_cap:
        raise ValueError("sequence window count exceeds max_windows")

    valid_windows: list[tuple[int, tuple[float, ...]]] = []
    for start in range(window_count):
        normalized = _normalized_window(values[start : start + window_size])
        if normalized is not None:
            valid_windows.append((start, normalized))
    if len(valid_windows) < 2:
        return ()

    best: list[tuple[float, int, int, MotifMatch]] = []
    comparisons = 0
    for left_index, (left_start, left) in enumerate(valid_windows):
        for right_start, right in valid_windows[left_index + 1 :]:
            if right_start - left_start <= zone:
                continue
            comparisons += 1
            if comparisons > _MAX_COMPARISONS:
                raise ValueError("motif comparison count exceeds its safety bound")
            distance = math.sqrt(
                math.fsum(
                    (left_value - right_value) ** 2
                    for left_value, right_value in zip(left, right, strict=True)
                )
            )
            match = MotifMatch(left_start, right_start, distance, window_size)
            candidate = (distance, left_start, right_start)
            if len(best) < result_cap:
                heapq.heappush(best, (-distance, -left_start, -right_start, match))
                continue
            worst = (-best[0][0], -best[0][1], -best[0][2])
            if candidate < worst:
                heapq.heapreplace(best, (-distance, -left_start, -right_start, match))
    return tuple(sorted((entry[3] for entry in best), key=_match_key))


def _validated_sequence(sequence: Sequence[float | None]) -> tuple[float | None, ...]:
    if isinstance(sequence, (str, bytes)) or not isinstance(sequence, Sequence):
        raise TypeError("sequence must be a bounded sequence")
    if not sequence:
        raise ValueError("sequence must be non-empty")
    values: list[float | None] = []
    for value in sequence:
        if value is None:
            values.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise TypeError("sequence values must be finite numbers or None")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("sequence values must be finite numbers or None")
        values.append(numeric)
    return tuple(values)


def _normalized_window(values: Sequence[float | None]) -> tuple[float, ...] | None:
    if any(value is None for value in values):
        return None
    complete = tuple(value for value in values if value is not None)
    anchored = tuple(value - complete[0] for value in complete)
    mean = math.fsum(anchored) / len(anchored)
    centered = tuple(value - mean for value in anchored)
    squared_scale = math.fsum(value**2 for value in centered)
    if squared_scale == 0.0:
        return None
    standard_deviation = math.sqrt(squared_scale / len(complete))
    return tuple(value / standard_deviation for value in centered)


def _positive_integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}")
    return value


def _match_key(match: MotifMatch) -> tuple[float, int, int]:
    return match.distance, match.left_start, match.right_start
