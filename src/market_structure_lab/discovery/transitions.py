"""Boundary-aware dwell-run transition evidence with block-bootstrap intervals."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Literal, Sequence

_MAX_ROWS = 1_000_000
_MAX_BOOTSTRAP_ITERATIONS = 10_000


@dataclass(frozen=True, slots=True)
class ClusterObservation:
    """One outcome-blind cluster observation and its information boundary."""

    label: int
    timestamp: datetime
    information_cutoff: datetime
    symbol: str
    timeframe: str
    segment_id: int
    session_id: str

    def __post_init__(self) -> None:
        if isinstance(self.label, bool) or not isinstance(self.label, int):
            raise TypeError("label must be a non-negative integer")
        if self.label < 0:
            raise ValueError("label must be a non-negative integer")
        _require_utc(self.timestamp, "timestamp")
        _require_utc(self.information_cutoff, "information_cutoff")
        if self.information_cutoff <= self.timestamp:
            raise ValueError("information_cutoff must be after timestamp")
        for value, field in (
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
            (self.session_id, "session_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty")
        if isinstance(self.segment_id, bool) or not isinstance(self.segment_id, int):
            raise TypeError("segment_id must be a non-negative integer")
        if self.segment_id < 0:
            raise ValueError("segment_id must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ClusterTransitionEstimate:
    """One destination count, probability, and seeded bootstrap interval."""

    destination_label: int
    count: int
    probability: float
    confidence_low: float
    confidence_high: float


@dataclass(frozen=True, slots=True)
class ClusterTransitionRow:
    """A row-stochastic transition estimate for one source cluster."""

    source_label: int
    support: int
    destinations: tuple[ClusterTransitionEstimate, ...]


@dataclass(frozen=True, slots=True)
class ClusterTransitionMatrix:
    """Run-level evidence; adjacent minute rows are never claimed independent."""

    horizon: int
    rows: tuple[ClusterTransitionRow, ...]
    total_transitions: int
    sample_unit: Literal["dwell_run"]
    confidence_method: Literal["boundary_block_bootstrap"]
    seed: int
    bootstrap_iterations: int
    block_length: int
    confidence_level: float


def compress_dwell_runs(rows: Iterable[ClusterObservation]) -> tuple[ClusterObservation, ...]:
    """Compress equal labels only inside one contiguous declared sequence boundary."""

    observations = _bounded_observations(rows, _MAX_ROWS, "observation safety bound")
    return _compress_validated(observations)


def estimate_cluster_transitions(
    rows: Iterable[ClusterObservation],
    *,
    horizon: int,
    max_rows: int,
    seed: int,
    bootstrap_iterations: int,
    block_length: int,
    confidence_level: float,
) -> ClusterTransitionMatrix:
    """Estimate dwell-run transitions and boundary-preserving block-bootstrap intervals."""

    horizon_value = _bounded_integer(horizon, "horizon", minimum=1)
    row_cap = _bounded_integer(max_rows, "max_rows", minimum=1)
    if row_cap > _MAX_ROWS:
        raise ValueError(f"max_rows cannot exceed {_MAX_ROWS}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    iterations = _bounded_integer(
        bootstrap_iterations,
        "bootstrap_iterations",
        minimum=1,
    )
    if iterations > _MAX_BOOTSTRAP_ITERATIONS:
        raise ValueError(f"bootstrap_iterations cannot exceed {_MAX_BOOTSTRAP_ITERATIONS}")
    block_size = _bounded_integer(block_length, "block_length", minimum=1)
    confidence = _finite_number(confidence_level, "confidence_level")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence_level must be strictly between zero and one")

    observations = _bounded_observations(rows, row_cap, "max_rows")
    compressed = _compress_validated(observations)
    sequences = _contiguous_sequences(compressed)
    transition_sequences = tuple(
        tuple(
            (sequence[index].label, sequence[index + horizon_value].label)
            for index in range(len(sequence) - horizon_value)
        )
        for sequence in sequences
        if len(sequence) > horizon_value
    )
    pairs = tuple(pair for sequence in transition_sequences for pair in sequence)
    counts = Counter(pairs)
    support = Counter(source for source, _ in pairs)
    intervals = _bootstrap_intervals(
        transition_sequences,
        counts,
        support,
        seed=seed,
        iterations=iterations,
        block_length=block_size,
        confidence_level=confidence,
    )
    matrix_rows = tuple(
        ClusterTransitionRow(
            source_label=source,
            support=support[source],
            destinations=tuple(
                ClusterTransitionEstimate(
                    destination_label=destination,
                    count=counts[(source, destination)],
                    probability=counts[(source, destination)] / support[source],
                    confidence_low=intervals[(source, destination)][0],
                    confidence_high=intervals[(source, destination)][1],
                )
                for destination in sorted(
                    destination for row_source, destination in counts if row_source == source
                )
            ),
        )
        for source in sorted(support)
    )
    return ClusterTransitionMatrix(
        horizon=horizon_value,
        rows=matrix_rows,
        total_transitions=len(pairs),
        sample_unit="dwell_run",
        confidence_method="boundary_block_bootstrap",
        seed=seed,
        bootstrap_iterations=iterations,
        block_length=block_size,
        confidence_level=confidence,
    )


def _bounded_observations(
    rows: Iterable[ClusterObservation], limit: int, label: str
) -> tuple[ClusterObservation, ...]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Iterable):
        raise TypeError("rows must be an iterable of ClusterObservation values")
    observations: list[ClusterObservation] = []
    for row in rows:
        if len(observations) >= limit:
            raise ValueError(f"observation count exceeds {label}")
        if not isinstance(row, ClusterObservation):
            raise TypeError("rows must contain ClusterObservation values")
        observations.append(row)
    _validate_canonical_order(observations)
    return tuple(observations)


def _validate_canonical_order(rows: Sequence[ClusterObservation]) -> None:
    previous: ClusterObservation | None = None
    previous_key: tuple[str, str, int, str] | None = None
    seen_keys: set[tuple[str, str, int, str]] = set()
    for row in rows:
        key = _sequence_key(row)
        if previous is not None and previous_key is not None:
            if key < previous_key:
                raise ValueError("cluster observations must use canonical order")
            if key == previous_key and row.timestamp <= previous.timestamp:
                raise ValueError("cluster observations must use canonical order")
            if key != previous_key and key in seen_keys:
                raise ValueError("cluster observation boundary groups must be contiguous")
        seen_keys.add(key)
        previous = row
        previous_key = key


def _compress_validated(
    observations: Sequence[ClusterObservation],
) -> tuple[ClusterObservation, ...]:
    compressed: list[ClusterObservation] = []
    for row in observations:
        if compressed and _is_contiguous(compressed[-1], row) and compressed[-1].label == row.label:
            compressed[-1] = replace(compressed[-1], information_cutoff=row.information_cutoff)
        else:
            compressed.append(row)
    return tuple(compressed)


def _contiguous_sequences(
    rows: Sequence[ClusterObservation],
) -> tuple[tuple[ClusterObservation, ...], ...]:
    sequences: list[list[ClusterObservation]] = []
    for row in rows:
        if not sequences or not _is_contiguous(sequences[-1][-1], row):
            sequences.append([row])
        else:
            sequences[-1].append(row)
    return tuple(tuple(sequence) for sequence in sequences)


def _is_contiguous(left: ClusterObservation, right: ClusterObservation) -> bool:
    return _sequence_key(left) == _sequence_key(right) and (
        left.information_cutoff == right.timestamp
    )


def _sequence_key(row: ClusterObservation) -> tuple[str, str, int, str]:
    return row.symbol, row.timeframe, row.segment_id, row.session_id


def _bootstrap_intervals(
    sequences: Sequence[Sequence[tuple[int, int]]],
    counts: Counter[tuple[int, int]],
    support: Counter[int],
    *,
    seed: int,
    iterations: int,
    block_length: int,
    confidence_level: float,
) -> dict[tuple[int, int], tuple[float, float]]:
    if not counts:
        return {}
    blocks = tuple(
        tuple(sequence[start : start + block_length])
        for sequence in sequences
        for start in range(len(sequence))
    )
    target_size = sum(counts.values())
    randomizer = random.Random(seed)
    samples: dict[tuple[int, int], list[float]] = defaultdict(list)
    for _ in range(iterations):
        sampled: list[tuple[int, int]] = []
        while len(sampled) < target_size:
            sampled.extend(randomizer.choice(blocks))
        selected = sampled[:target_size]
        replicate = Counter(selected)
        replicate_support = Counter(source for source, _ in selected)
        for pair in counts:
            source, _ = pair
            if replicate_support[source]:
                samples[pair].append(replicate[pair] / replicate_support[source])

    tail = (1.0 - confidence_level) / 2.0
    intervals: dict[tuple[int, int], tuple[float, float]] = {}
    for pair, count in counts.items():
        point = count / support[pair[0]]
        values = tuple(sorted(samples[pair]))
        if not values:
            intervals[pair] = (point, point)
            continue
        intervals[pair] = (
            min(point, _quantile(values, tail)),
            max(point, _quantile(values, 1.0 - tail)),
        )
    return intervals


def _quantile(values: Sequence[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")
    return value


def _bounded_integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number")
    return numeric
