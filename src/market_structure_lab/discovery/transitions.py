"""Boundary-aware dwell-run transition evidence with block-bootstrap intervals."""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Iterable, Literal, Protocol, Sequence

_MAX_ROWS = 1_000_000
_MAX_BOOTSTRAP_ITERATIONS = 10_000
_MAX_BLOCK_LENGTH = 4_096
_MAX_BOOTSTRAP_PROBABILITY_SAMPLES = 1_000_000
_TRANSITION_ALGORITHM_VERSION: Literal["boundary-aware-dwell-transitions-v2"] = (
    "boundary-aware-dwell-transitions-v2"
)


class BoundaryObservation(Protocol):
    """Shared observable fields defining one canonical contiguous sequence boundary."""

    @property
    def timestamp(self) -> datetime: ...

    @property
    def information_cutoff(self) -> datetime: ...

    @property
    def symbol(self) -> str: ...

    @property
    def timeframe(self) -> str: ...

    @property
    def segment_id(self) -> int: ...

    @property
    def session_id(self) -> str: ...


def is_contiguous_observation(left: BoundaryObservation, right: BoundaryObservation) -> bool:
    """Return whether two ordered observations share identity and causal time continuity."""

    return _sequence_key(left) == _sequence_key(right) and (
        left.information_cutoff == right.timestamp
    )


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
        for value, field_name in (
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
            (self.session_id, "session_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
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
    effective_support: int = field(init=False)

    def __post_init__(self) -> None:
        _non_negative_integer(self.destination_label, "destination_label")
        _bounded_integer(self.count, "count", minimum=1)
        probability = _unit_interval(self.probability, "probability")
        confidence_low = _unit_interval(self.confidence_low, "confidence_low")
        confidence_high = _unit_interval(self.confidence_high, "confidence_high")
        if confidence_low > probability or probability > confidence_high:
            raise ValueError("confidence bounds must contain the observed transition probability")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "confidence_low", confidence_low)
        object.__setattr__(self, "confidence_high", confidence_high)
        object.__setattr__(self, "effective_support", self.count)


@dataclass(frozen=True, slots=True)
class ClusterTransitionRow:
    """A row-stochastic transition estimate for one source cluster."""

    source_label: int
    support: int
    destinations: tuple[ClusterTransitionEstimate, ...]
    effective_support: int = field(init=False)

    def __post_init__(self) -> None:
        _non_negative_integer(self.source_label, "source_label")
        _bounded_integer(self.support, "support", minimum=1)
        if not isinstance(self.destinations, tuple) or not self.destinations:
            raise TypeError("destinations must be a non-empty tuple")
        if any(not isinstance(item, ClusterTransitionEstimate) for item in self.destinations):
            raise TypeError("destinations must contain ClusterTransitionEstimate values")
        labels = tuple(item.destination_label for item in self.destinations)
        if labels != tuple(sorted(set(labels))):
            raise ValueError("destination labels must be unique and sorted")
        if any(item.effective_support != item.count for item in self.destinations):
            raise ValueError("destination effective support must equal its transition count")
        if sum(item.count for item in self.destinations) != self.support:
            raise ValueError("destination counts must sum to row support")
        if any(
            not math.isclose(
                item.probability,
                item.count / self.support,
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
            for item in self.destinations
        ):
            raise ValueError("destination probabilities must match count divided by support")
        object.__setattr__(self, "effective_support", self.support)


@dataclass(frozen=True, slots=True)
class TransitionBoundaryEvidence:
    """Counts proving the observation stream was compressed and partitioned."""

    raw_observation_count: int
    dwell_run_count: int
    contiguous_sequence_count: int
    boundary_break_count: int
    symbol_break_count: int
    timeframe_break_count: int
    segment_break_count: int
    session_break_count: int
    non_contiguous_time_break_count: int

    def __post_init__(self) -> None:
        counts = (
            (self.raw_observation_count, "raw_observation_count"),
            (self.dwell_run_count, "dwell_run_count"),
            (self.contiguous_sequence_count, "contiguous_sequence_count"),
            (self.boundary_break_count, "boundary_break_count"),
            (self.symbol_break_count, "symbol_break_count"),
            (self.timeframe_break_count, "timeframe_break_count"),
            (self.segment_break_count, "segment_break_count"),
            (self.session_break_count, "session_break_count"),
            (self.non_contiguous_time_break_count, "non_contiguous_time_break_count"),
        )
        for value, label in counts:
            _non_negative_integer(value, label)
        if self.dwell_run_count > self.raw_observation_count:
            raise ValueError("dwell_run_count cannot exceed raw_observation_count")
        if self.raw_observation_count > 0 and self.dwell_run_count == 0:
            raise ValueError("non-empty observations require at least one dwell run")
        if self.contiguous_sequence_count > self.dwell_run_count:
            raise ValueError("contiguous_sequence_count cannot exceed dwell_run_count")
        if self.dwell_run_count > 0 and self.contiguous_sequence_count == 0:
            raise ValueError("non-empty dwell runs require at least one contiguous sequence")
        expected_breaks = max(self.contiguous_sequence_count - 1, 0)
        if self.boundary_break_count != expected_breaks:
            raise ValueError("boundary_break_count must equal contiguous sequence breaks")
        reasons = (
            self.symbol_break_count,
            self.timeframe_break_count,
            self.segment_break_count,
            self.session_break_count,
            self.non_contiguous_time_break_count,
        )
        if any(value > self.boundary_break_count for value in reasons):
            raise ValueError("reason-specific break counts cannot exceed total boundary breaks")
        if sum(reasons) < self.boundary_break_count:
            raise ValueError("every boundary break must have at least one recorded reason")


@dataclass(frozen=True, slots=True)
class ClusterTransitionMatrix:
    """Run-level evidence; adjacent minute rows are never claimed independent."""

    algorithm_version: Literal["boundary-aware-dwell-transitions-v2"]
    horizon: int
    rows: tuple[ClusterTransitionRow, ...]
    total_transitions: int
    sample_unit: Literal["dwell_run"]
    confidence_method: Literal["boundary_block_bootstrap"]
    seed: int
    bootstrap_iterations: int
    block_length: int
    confidence_level: float
    boundary_evidence: TransitionBoundaryEvidence

    def __post_init__(self) -> None:
        if self.algorithm_version != _TRANSITION_ALGORITHM_VERSION:
            raise ValueError("algorithm_version must use the canonical transition algorithm")
        _bounded_integer(self.horizon, "horizon", minimum=1)
        if not isinstance(self.rows, tuple):
            raise TypeError("rows must be a tuple")
        if any(not isinstance(row, ClusterTransitionRow) for row in self.rows):
            raise TypeError("rows must contain ClusterTransitionRow values")
        source_labels = tuple(row.source_label for row in self.rows)
        if source_labels != tuple(sorted(set(source_labels))):
            raise ValueError("transition row source labels must be unique and sorted")
        if any(row.effective_support != row.support for row in self.rows):
            raise ValueError("row effective support must equal row support")
        _non_negative_integer(self.total_transitions, "total_transitions")
        if sum(row.support for row in self.rows) != self.total_transitions:
            raise ValueError("total_transitions must equal summed row support")
        if self.sample_unit != "dwell_run":
            raise ValueError("sample_unit must be dwell_run")
        if self.confidence_method != "boundary_block_bootstrap":
            raise ValueError("confidence_method must be boundary_block_bootstrap")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        iterations = _bounded_integer(
            self.bootstrap_iterations,
            "bootstrap_iterations",
            minimum=1,
        )
        if iterations > _MAX_BOOTSTRAP_ITERATIONS:
            raise ValueError(f"bootstrap_iterations cannot exceed {_MAX_BOOTSTRAP_ITERATIONS}")
        block_length = _bounded_integer(self.block_length, "block_length", minimum=1)
        if block_length > _MAX_BLOCK_LENGTH:
            raise ValueError(f"block_length cannot exceed {_MAX_BLOCK_LENGTH}")
        confidence_level = _finite_number(self.confidence_level, "confidence_level")
        if not 0.0 < confidence_level < 1.0:
            raise ValueError("confidence_level must be strictly between zero and one")
        if not isinstance(self.boundary_evidence, TransitionBoundaryEvidence):
            raise TypeError("boundary_evidence must be TransitionBoundaryEvidence")
        sequence_count = self.boundary_evidence.contiguous_sequence_count
        maximum_transitions = (
            max(
                self.boundary_evidence.dwell_run_count - sequence_count - self.horizon + 1,
                0,
            )
            if sequence_count > 0
            else 0
        )
        if self.total_transitions > maximum_transitions:
            raise ValueError("total_transitions exceeds boundary-safe dwell-run capacity")
        object.__setattr__(self, "confidence_level", confidence_level)


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
    if block_size > _MAX_BLOCK_LENGTH:
        raise ValueError(f"block_length cannot exceed {_MAX_BLOCK_LENGTH}")
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
    probability_sample_count = len(counts) * iterations
    if probability_sample_count > _MAX_BOOTSTRAP_PROBABILITY_SAMPLES:
        raise ValueError(
            "bootstrap probability storage exceeds "
            f"{_MAX_BOOTSTRAP_PROBABILITY_SAMPLES} bounded samples"
        )
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
        algorithm_version=_TRANSITION_ALGORITHM_VERSION,
        horizon=horizon_value,
        rows=matrix_rows,
        total_transitions=len(pairs),
        sample_unit="dwell_run",
        confidence_method="boundary_block_bootstrap",
        seed=seed,
        bootstrap_iterations=iterations,
        block_length=block_size,
        confidence_level=confidence,
        boundary_evidence=_boundary_evidence(observations, compressed, sequences),
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
            raise TypeError("rows must contain boundary-aware ClusterObservation values")
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
        if (
            compressed
            and is_contiguous_observation(compressed[-1], row)
            and compressed[-1].label == row.label
        ):
            compressed[-1] = replace(compressed[-1], information_cutoff=row.information_cutoff)
        else:
            compressed.append(row)
    return tuple(compressed)


def _contiguous_sequences(
    rows: Sequence[ClusterObservation],
) -> tuple[tuple[ClusterObservation, ...], ...]:
    sequences: list[list[ClusterObservation]] = []
    for row in rows:
        if not sequences or not is_contiguous_observation(sequences[-1][-1], row):
            sequences.append([row])
        else:
            sequences[-1].append(row)
    return tuple(tuple(sequence) for sequence in sequences)


def _sequence_key(row: BoundaryObservation) -> tuple[str, str, int, str]:
    return row.symbol, row.timeframe, row.segment_id, row.session_id


def _boundary_evidence(
    observations: Sequence[ClusterObservation],
    compressed: Sequence[ClusterObservation],
    sequences: Sequence[Sequence[ClusterObservation]],
) -> TransitionBoundaryEvidence:
    adjacent = tuple(zip(observations, observations[1:]))
    return TransitionBoundaryEvidence(
        raw_observation_count=len(observations),
        dwell_run_count=len(compressed),
        contiguous_sequence_count=len(sequences),
        boundary_break_count=sum(
            not is_contiguous_observation(left, right) for left, right in adjacent
        ),
        symbol_break_count=sum(left.symbol != right.symbol for left, right in adjacent),
        timeframe_break_count=sum(left.timeframe != right.timeframe for left, right in adjacent),
        segment_break_count=sum(left.segment_id != right.segment_id for left, right in adjacent),
        session_break_count=sum(left.session_id != right.session_id for left, right in adjacent),
        non_contiguous_time_break_count=sum(
            left.information_cutoff != right.timestamp for left, right in adjacent
        ),
    )


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
    eligible = tuple(
        (
            sequence,
            min(block_length, len(sequence)),
            len(sequence) - min(block_length, len(sequence)) + 1,
        )
        for sequence in sequences
        if sequence
    )
    cumulative_starts: list[int] = []
    total_starts = 0
    for _, _, start_count in eligible:
        total_starts += start_count
        cumulative_starts.append(total_starts)
    if total_starts == 0:
        raise ValueError("block_length exceeds every available transition sequence")
    target_size = sum(counts.values())
    randomizer = random.Random(seed)
    samples: dict[tuple[int, int], list[float]] = defaultdict(list)
    for _ in range(iterations):
        replicate: Counter[tuple[int, int]] = Counter()
        replicate_support: Counter[int] = Counter()
        selected_count = 0
        while selected_count < target_size:
            flat_start = randomizer.randrange(total_starts)
            sequence_index = bisect_right(cumulative_starts, flat_start)
            previous_total = cumulative_starts[sequence_index - 1] if sequence_index else 0
            sequence, effective_block_length, _ = eligible[sequence_index]
            start = flat_start - previous_total
            take = min(effective_block_length, target_size - selected_count)
            for pair in sequence[start : start + take]:
                replicate[pair] += 1
                replicate_support[pair[0]] += 1
            selected_count += take
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


def _unit_interval(value: object, label: str) -> float:
    numeric = _finite_number(value, label)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{label} must be between zero and one")
    return numeric


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value
