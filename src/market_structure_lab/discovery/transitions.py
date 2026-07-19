"""Boundary-aware dwell-run transition evidence with block-bootstrap intervals."""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from statistics import median
from typing import Iterable, Literal, Protocol, Sequence

_MAX_ROWS = 1_000_000
_MAX_BOOTSTRAP_ITERATIONS = 10_000
_MAX_BLOCK_LENGTH = 4_096
# CPU guard: total transition pairs drawn across the primary and sensitivity bootstraps.
_MAX_BOOTSTRAP_TRANSITION_DRAWS = 10_000_000
_MAX_BOOTSTRAP_PROBABILITY_SAMPLES = 1_000_000
_MAX_TRANSITION_OUTPUT_ESTIMATES = 100_000
_MAX_SENSITIVITY_OFFSET = 8
_TRANSITION_ALGORITHM_VERSION: Literal["boundary-aware-dwell-transitions-v3"] = (
    "boundary-aware-dwell-transitions-v3"
)
TransitionBlockLengthRule = Literal["fixed", "cube_root_transition_support"]
TransitionIntervalWidthAction = Literal["report_only", "reject"]
TransitionEvidenceStatus = Literal["descriptive", "descriptive_only", "rejected"]


@dataclass(frozen=True, slots=True)
class TransitionUncertaintyPolicy:
    """Frozen descriptive uncertainty settings declared before recurrence estimation."""

    policy_id: str
    policy_purpose: str
    horizon: int
    bootstrap_seed: int
    bootstrap_iterations: int
    confidence_level: float
    block_length_rule: TransitionBlockLengthRule
    block_length_value: int
    minimum_effective_support: int
    interval_width_action: TransitionIntervalWidthAction
    maximum_interval_width: float
    sensitivity_offsets: tuple[int, ...]
    maximum_sensitivity_endpoint_delta: float

    def __post_init__(self) -> None:
        for value, label in (
            (self.policy_id, "policy_id"),
            (self.policy_purpose, "policy_purpose"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        _bounded_integer(self.horizon, "horizon", minimum=1)
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise TypeError("bootstrap_seed must be an integer")
        iterations = _bounded_integer(
            self.bootstrap_iterations,
            "bootstrap_iterations",
            minimum=1,
        )
        if iterations > _MAX_BOOTSTRAP_ITERATIONS:
            raise ValueError(f"bootstrap_iterations cannot exceed {_MAX_BOOTSTRAP_ITERATIONS}")
        confidence = _finite_number(self.confidence_level, "confidence_level")
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence_level must be strictly between zero and one")
        if self.block_length_rule not in ("fixed", "cube_root_transition_support"):
            raise ValueError("block_length_rule must be predeclared and supported")
        block_length = _bounded_integer(
            self.block_length_value,
            "block_length_value",
            minimum=1,
        )
        if block_length > _MAX_BLOCK_LENGTH:
            raise ValueError(f"block_length_value cannot exceed {_MAX_BLOCK_LENGTH}")
        support = _bounded_integer(
            self.minimum_effective_support,
            "minimum_effective_support",
            minimum=1,
        )
        if support > _MAX_ROWS:
            raise ValueError(f"minimum_effective_support cannot exceed {_MAX_ROWS}")
        if self.interval_width_action not in ("report_only", "reject"):
            raise ValueError("interval_width_action must be report_only or reject")
        maximum_width = _unit_interval(
            self.maximum_interval_width,
            "maximum_interval_width",
        )
        if (
            not isinstance(self.sensitivity_offsets, tuple)
            or not self.sensitivity_offsets
            or self.sensitivity_offsets != tuple(sorted(set(self.sensitivity_offsets)))
            or any(
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset == 0
                or abs(offset) > _MAX_SENSITIVITY_OFFSET
                for offset in self.sensitivity_offsets
            )
        ):
            raise ValueError("sensitivity_offsets must be unique ordered non-zero nearby integers")
        maximum_delta = _unit_interval(
            self.maximum_sensitivity_endpoint_delta,
            "maximum_sensitivity_endpoint_delta",
        )
        object.__setattr__(self, "confidence_level", confidence)
        object.__setattr__(self, "maximum_interval_width", maximum_width)
        object.__setattr__(self, "maximum_sensitivity_endpoint_delta", maximum_delta)


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
class TransitionSensitivityEstimate:
    """Conditional recurrence interval under one predeclared nearby block length."""

    block_length: int
    confidence_low: float
    confidence_high: float
    interval_width: float = field(init=False)

    def __post_init__(self) -> None:
        _bounded_integer(self.block_length, "block_length", minimum=1)
        low = _unit_interval(self.confidence_low, "confidence_low")
        high = _unit_interval(self.confidence_high, "confidence_high")
        if low > high:
            raise ValueError("confidence_low cannot exceed confidence_high")
        object.__setattr__(self, "confidence_low", low)
        object.__setattr__(self, "confidence_high", high)
        object.__setattr__(self, "interval_width", high - low)


@dataclass(frozen=True, slots=True)
class ClusterTransitionEstimate:
    """One descriptive conditional recurrence estimate with uncertainty evidence."""

    destination_label: int
    count: int
    probability: float
    confidence_low: float
    confidence_high: float
    effective_support: int
    estimate_semantics: Literal["conditional_recurrence_estimate"]
    sensitivity: tuple[TransitionSensitivityEstimate, ...]
    maximum_sensitivity_endpoint_delta: float
    evidence_status: TransitionEvidenceStatus
    rejection_reasons: tuple[str, ...]
    interval_width: float = field(init=False)

    def __post_init__(self) -> None:
        _non_negative_integer(self.destination_label, "destination_label")
        _non_negative_integer(self.count, "count")
        probability = _unit_interval(self.probability, "probability")
        confidence_low = _unit_interval(self.confidence_low, "confidence_low")
        confidence_high = _unit_interval(self.confidence_high, "confidence_high")
        if confidence_low > probability or probability > confidence_high:
            raise ValueError("confidence bounds must contain the observed transition probability")
        _bounded_integer(self.effective_support, "effective_support", minimum=1)
        if self.count > self.effective_support:
            raise ValueError("count cannot exceed effective_support")
        if self.estimate_semantics != "conditional_recurrence_estimate":
            raise ValueError("transition estimates must remain conditional recurrence estimates")
        if not isinstance(self.sensitivity, tuple) or any(
            not isinstance(item, TransitionSensitivityEstimate) for item in self.sensitivity
        ):
            raise TypeError("sensitivity must contain TransitionSensitivityEstimate values")
        sensitivity_lengths = tuple(item.block_length for item in self.sensitivity)
        if sensitivity_lengths != tuple(sorted(set(sensitivity_lengths))):
            raise ValueError("sensitivity block lengths must be unique and sorted")
        if any(
            item.confidence_low > probability or probability > item.confidence_high
            for item in self.sensitivity
        ):
            raise ValueError("sensitivity intervals must contain the observed probability")
        maximum_delta = _unit_interval(
            self.maximum_sensitivity_endpoint_delta,
            "maximum_sensitivity_endpoint_delta",
        )
        expected_delta = _maximum_endpoint_delta(
            (confidence_low, confidence_high),
            tuple((item.confidence_low, item.confidence_high) for item in self.sensitivity),
        )
        if not math.isclose(maximum_delta, expected_delta, rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError("maximum_sensitivity_endpoint_delta must match sensitivity evidence")
        if self.evidence_status not in ("descriptive", "descriptive_only", "rejected"):
            raise ValueError("evidence_status is unsupported")
        if (
            not isinstance(self.rejection_reasons, tuple)
            or self.rejection_reasons != tuple(sorted(set(self.rejection_reasons)))
            or any(not isinstance(reason, str) or not reason for reason in self.rejection_reasons)
        ):
            raise ValueError("rejection_reasons must use unique canonical text")
        if self.evidence_status == "descriptive" and self.rejection_reasons:
            raise ValueError("descriptive estimates cannot have rejection reasons")
        if self.evidence_status != "descriptive" and not self.rejection_reasons:
            raise ValueError("limited or rejected estimates require reasons")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "confidence_low", confidence_low)
        object.__setattr__(self, "confidence_high", confidence_high)
        object.__setattr__(self, "maximum_sensitivity_endpoint_delta", maximum_delta)
        object.__setattr__(self, "interval_width", confidence_high - confidence_low)


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
        if any(item.effective_support != self.support for item in self.destinations):
            raise ValueError("destination effective support must equal source row support")
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
class TransitionDependenceDiagnostics:
    """Pre-outcome diagnostics used to justify deterministic block selection."""

    longest_raw_dwell_run: int
    median_raw_dwell_run: float
    dwell_run_to_observation_ratio: float
    transition_sequence_count: int
    transition_sequence_lengths: tuple[int, ...]
    maximum_transition_sequence_length: int
    median_transition_sequence_length: float
    adjacent_transition_overlap_fraction: float
    selected_block_length: int
    sensitivity_block_lengths: tuple[int, ...]

    def __post_init__(self) -> None:
        _non_negative_integer(self.longest_raw_dwell_run, "longest_raw_dwell_run")
        median_dwell = _finite_number(self.median_raw_dwell_run, "median_raw_dwell_run")
        if median_dwell < 0.0:
            raise ValueError("median_raw_dwell_run must be non-negative")
        dwell_ratio = _unit_interval(
            self.dwell_run_to_observation_ratio,
            "dwell_run_to_observation_ratio",
        )
        _non_negative_integer(self.transition_sequence_count, "transition_sequence_count")
        if (
            not isinstance(self.transition_sequence_lengths, tuple)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in self.transition_sequence_lengths
            )
            or len(self.transition_sequence_lengths) != self.transition_sequence_count
        ):
            raise ValueError(
                "transition_sequence_lengths must exactly enumerate non-empty sequences"
            )
        _non_negative_integer(
            self.maximum_transition_sequence_length,
            "maximum_transition_sequence_length",
        )
        if self.maximum_transition_sequence_length != max(
            self.transition_sequence_lengths,
            default=0,
        ):
            raise ValueError("maximum_transition_sequence_length must match enumerated sequences")
        median_sequence = _finite_number(
            self.median_transition_sequence_length,
            "median_transition_sequence_length",
        )
        if median_sequence < 0.0:
            raise ValueError("median_transition_sequence_length must be non-negative")
        expected_median_sequence = (
            float(median(self.transition_sequence_lengths))
            if self.transition_sequence_lengths
            else 0.0
        )
        if not math.isclose(
            median_sequence,
            expected_median_sequence,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise ValueError("median_transition_sequence_length must match enumerated sequences")
        overlap = _unit_interval(
            self.adjacent_transition_overlap_fraction,
            "adjacent_transition_overlap_fraction",
        )
        _bounded_integer(self.selected_block_length, "selected_block_length", minimum=1)
        if (
            not isinstance(self.sensitivity_block_lengths, tuple)
            or self.sensitivity_block_lengths != tuple(sorted(set(self.sensitivity_block_lengths)))
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in self.sensitivity_block_lengths
            )
            or self.selected_block_length in self.sensitivity_block_lengths
        ):
            raise ValueError("sensitivity_block_lengths must be unique nearby alternatives")
        object.__setattr__(self, "median_raw_dwell_run", median_dwell)
        object.__setattr__(self, "dwell_run_to_observation_ratio", dwell_ratio)
        object.__setattr__(self, "median_transition_sequence_length", median_sequence)
        object.__setattr__(self, "adjacent_transition_overlap_fraction", overlap)


@dataclass(frozen=True, slots=True)
class ClusterTransitionMatrix:
    """Run-level evidence; adjacent minute rows are never claimed independent."""

    algorithm_version: Literal["boundary-aware-dwell-transitions-v3"]
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
    uncertainty_policy: TransitionUncertaintyPolicy
    dependence_diagnostics: TransitionDependenceDiagnostics
    estimate_semantics: Literal["conditional_recurrence_estimate"] = (
        "conditional_recurrence_estimate"
    )

    def __post_init__(self) -> None:
        if self.algorithm_version != _TRANSITION_ALGORITHM_VERSION:
            raise ValueError("algorithm_version must use the canonical transition algorithm")
        if self.estimate_semantics != "conditional_recurrence_estimate":
            raise ValueError("transition matrix must remain descriptive conditional recurrence")
        if not isinstance(self.uncertainty_policy, TransitionUncertaintyPolicy):
            raise TypeError("uncertainty_policy must be a TransitionUncertaintyPolicy")
        if not isinstance(self.dependence_diagnostics, TransitionDependenceDiagnostics):
            raise TypeError("dependence_diagnostics must be TransitionDependenceDiagnostics")
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
        expected_dwell_ratio = (
            self.boundary_evidence.dwell_run_count / self.boundary_evidence.raw_observation_count
            if self.boundary_evidence.raw_observation_count
            else 0.0
        )
        if not math.isclose(
            self.dependence_diagnostics.dwell_run_to_observation_ratio,
            expected_dwell_ratio,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise ValueError("dwell dependence ratio must match boundary evidence")
        if (
            self.dependence_diagnostics.transition_sequence_count
            > self.boundary_evidence.contiguous_sequence_count
        ):
            raise ValueError("transition sequences cannot exceed contiguous boundaries")
        if (
            self.dependence_diagnostics.longest_raw_dwell_run
            > self.boundary_evidence.raw_observation_count
        ):
            raise ValueError("longest raw dwell cannot exceed observations")
        if self.horizon != self.uncertainty_policy.horizon:
            raise ValueError("horizon must match frozen uncertainty policy")
        if self.seed != self.uncertainty_policy.bootstrap_seed:
            raise ValueError("seed must match frozen uncertainty policy")
        if self.bootstrap_iterations != self.uncertainty_policy.bootstrap_iterations:
            raise ValueError("bootstrap_iterations must match frozen uncertainty policy")
        if self.confidence_level != self.uncertainty_policy.confidence_level:
            raise ValueError("confidence_level must match frozen uncertainty policy")
        if self.block_length != self.dependence_diagnostics.selected_block_length:
            raise ValueError("block_length must match dependence diagnostics")
        if self.total_transitions != sum(self.dependence_diagnostics.transition_sequence_lengths):
            raise ValueError("total_transitions must match dependence sequence lengths")
        expected_block_length = _selected_block_length(
            self.uncertainty_policy,
            transition_count=self.total_transitions,
            maximum_sequence_length=(
                self.dependence_diagnostics.maximum_transition_sequence_length
            ),
        )
        if self.block_length != expected_block_length:
            raise ValueError("block_length must match the predeclared selection rule")
        expected_sensitivity = _sensitivity_block_lengths(
            self.uncertainty_policy,
            selected=self.block_length,
            maximum_sequence_length=(
                self.dependence_diagnostics.maximum_transition_sequence_length
            ),
        )
        if self.dependence_diagnostics.sensitivity_block_lengths != expected_sensitivity:
            raise ValueError("sensitivity block lengths must match the frozen policy")
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
    max_rows: int,
    policy: TransitionUncertaintyPolicy,
) -> ClusterTransitionMatrix:
    """Estimate descriptive recurrence with a frozen dependence-calibrated policy."""

    if not isinstance(policy, TransitionUncertaintyPolicy):
        raise TypeError("policy must be a TransitionUncertaintyPolicy")
    horizon_value = policy.horizon
    row_cap = _bounded_integer(max_rows, "max_rows", minimum=1)
    if row_cap > _MAX_ROWS:
        raise ValueError(f"max_rows cannot exceed {_MAX_ROWS}")

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
    source_labels = tuple(sorted(support))
    destination_labels = tuple(sorted({row.label for row in compressed}))
    maximum_sequence_length = max(map(len, transition_sequences), default=0)
    block_size = _selected_block_length(
        policy,
        transition_count=len(pairs),
        maximum_sequence_length=maximum_sequence_length,
    )
    sensitivity_block_lengths = _sensitivity_block_lengths(
        policy,
        selected=block_size,
        maximum_sequence_length=maximum_sequence_length,
    )
    _bounded_bootstrap_transition_draw_work(
        transition_count=len(pairs),
        bootstrap_iterations=policy.bootstrap_iterations,
        sensitivity_block_count=len(sensitivity_block_lengths),
    )
    estimate_pair_count = len(source_labels) * len(destination_labels)
    output_estimate_count = estimate_pair_count * (1 + len(sensitivity_block_lengths))
    if output_estimate_count > _MAX_TRANSITION_OUTPUT_ESTIMATES:
        raise ValueError("transition sensitivity output exceeds its conservative cap")
    probability_sample_count = output_estimate_count * policy.bootstrap_iterations
    if probability_sample_count > _MAX_BOOTSTRAP_PROBABILITY_SAMPLES:
        raise ValueError(
            "bootstrap probability storage exceeds "
            f"{_MAX_BOOTSTRAP_PROBABILITY_SAMPLES} bounded samples"
        )
    estimate_pairs = _materialized_estimate_pairs(source_labels, destination_labels)
    for pair in estimate_pairs:
        counts[pair] += 0
    intervals = _bootstrap_intervals(
        transition_sequences,
        counts,
        support,
        seed=policy.bootstrap_seed,
        iterations=policy.bootstrap_iterations,
        block_length=block_size,
        confidence_level=policy.confidence_level,
    )
    sensitivity_intervals = {
        sensitivity_block_length: _bootstrap_intervals(
            transition_sequences,
            counts,
            support,
            seed=policy.bootstrap_seed,
            iterations=policy.bootstrap_iterations,
            block_length=sensitivity_block_length,
            confidence_level=policy.confidence_level,
        )
        for sensitivity_block_length in sensitivity_block_lengths
    }
    matrix_rows = tuple(
        ClusterTransitionRow(
            source_label=source,
            support=support[source],
            destinations=tuple(
                _conditional_recurrence_estimate(
                    source=source,
                    destination=destination,
                    counts=counts,
                    support=support,
                    intervals=intervals,
                    sensitivity_intervals=sensitivity_intervals,
                    sensitivity_block_lengths=sensitivity_block_lengths,
                    policy=policy,
                )
                for destination in destination_labels
            ),
        )
        for source in source_labels
    )
    return ClusterTransitionMatrix(
        algorithm_version=_TRANSITION_ALGORITHM_VERSION,
        estimate_semantics="conditional_recurrence_estimate",
        uncertainty_policy=policy,
        horizon=horizon_value,
        rows=matrix_rows,
        total_transitions=len(pairs),
        sample_unit="dwell_run",
        confidence_method="boundary_block_bootstrap",
        seed=policy.bootstrap_seed,
        bootstrap_iterations=policy.bootstrap_iterations,
        block_length=block_size,
        confidence_level=policy.confidence_level,
        boundary_evidence=_boundary_evidence(observations, compressed, sequences),
        dependence_diagnostics=_dependence_diagnostics(
            observations=observations,
            compressed=compressed,
            transition_sequences=transition_sequences,
            selected_block_length=block_size,
            sensitivity_block_lengths=sensitivity_block_lengths,
        ),
    )


def _materialized_estimate_pairs(
    source_labels: Sequence[int],
    destination_labels: Sequence[int],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (source, destination) for source in source_labels for destination in destination_labels
    )


def _bounded_bootstrap_transition_draw_work(
    *,
    transition_count: int,
    bootstrap_iterations: int,
    sensitivity_block_count: int,
) -> int:
    """Return predeclared draw work or reject before any bootstrap allocation."""

    transitions = _non_negative_integer(transition_count, "transition_count")
    iterations = _bounded_integer(
        bootstrap_iterations,
        "bootstrap_iterations",
        minimum=1,
    )
    sensitivity_count = _non_negative_integer(
        sensitivity_block_count,
        "sensitivity_block_count",
    )
    cap = _bounded_integer(
        _MAX_BOOTSTRAP_TRANSITION_DRAWS,
        "bootstrap transition draw work cap",
        minimum=1,
    )
    if transitions == 0:
        return 0
    if iterations > cap // transitions:
        raise ValueError(f"bootstrap transition draw work exceeds {cap} conservative draws")
    primary_draws = transitions * iterations
    bootstrap_count = 1 + sensitivity_count
    if bootstrap_count > cap // primary_draws:
        raise ValueError(f"bootstrap transition draw work exceeds {cap} conservative draws")
    return primary_draws * bootstrap_count


def _conditional_recurrence_estimate(
    *,
    source: int,
    destination: int,
    counts: Counter[tuple[int, int]],
    support: Counter[int],
    intervals: dict[tuple[int, int], tuple[float, float]],
    sensitivity_intervals: dict[
        int,
        dict[tuple[int, int], tuple[float, float]],
    ],
    sensitivity_block_lengths: tuple[int, ...],
    policy: TransitionUncertaintyPolicy,
) -> ClusterTransitionEstimate:
    pair = (source, destination)
    interval = intervals[pair]
    nearby_intervals = tuple(
        sensitivity_intervals[block_length][pair] for block_length in sensitivity_block_lengths
    )
    status, reasons = _evidence_status(
        count=counts[pair],
        support=support[source],
        interval=interval,
        sensitivity_intervals=nearby_intervals,
        policy=policy,
    )
    return ClusterTransitionEstimate(
        destination_label=destination,
        count=counts[pair],
        probability=counts[pair] / support[source],
        confidence_low=interval[0],
        confidence_high=interval[1],
        effective_support=support[source],
        estimate_semantics="conditional_recurrence_estimate",
        sensitivity=tuple(
            TransitionSensitivityEstimate(
                block_length=block_length,
                confidence_low=nearby_interval[0],
                confidence_high=nearby_interval[1],
            )
            for block_length, nearby_interval in zip(
                sensitivity_block_lengths,
                nearby_intervals,
                strict=True,
            )
        ),
        maximum_sensitivity_endpoint_delta=_maximum_endpoint_delta(
            interval,
            nearby_intervals,
        ),
        evidence_status=status,
        rejection_reasons=reasons,
    )


def _selected_block_length(
    policy: TransitionUncertaintyPolicy,
    *,
    transition_count: int,
    maximum_sequence_length: int,
) -> int:
    if transition_count == 0 or maximum_sequence_length == 0:
        return 1
    if policy.block_length_rule == "fixed":
        selected = policy.block_length_value
    else:
        selected = math.ceil(transition_count ** (1.0 / 3.0))
        selected = min(selected, policy.block_length_value)
    return max(1, min(selected, maximum_sequence_length))


def _sensitivity_block_lengths(
    policy: TransitionUncertaintyPolicy,
    *,
    selected: int,
    maximum_sequence_length: int,
) -> tuple[int, ...]:
    if maximum_sequence_length == 0:
        return ()
    return tuple(
        sorted(
            {
                max(1, min(selected + offset, maximum_sequence_length))
                for offset in policy.sensitivity_offsets
            }
            - {selected}
        )
    )


def _maximum_endpoint_delta(
    interval: tuple[float, float],
    sensitivity_intervals: Sequence[tuple[float, float]],
) -> float:
    return max(
        (
            max(abs(low - interval[0]), abs(high - interval[1]))
            for low, high in sensitivity_intervals
        ),
        default=0.0,
    )


def _evidence_status(
    *,
    count: int,
    support: int,
    interval: tuple[float, float],
    sensitivity_intervals: Sequence[tuple[float, float]],
    policy: TransitionUncertaintyPolicy,
) -> tuple[TransitionEvidenceStatus, tuple[str, ...]]:
    reasons: list[str] = []
    rejected = False
    if support < policy.minimum_effective_support:
        reasons.append("effective_support_below_policy")
        rejected = True
    if count == 0:
        reasons.append("zero_destination_count")
    if not sensitivity_intervals:
        reasons.append("block_length_sensitivity_unavailable")
    width = interval[1] - interval[0]
    if width > policy.maximum_interval_width:
        if policy.interval_width_action == "reject":
            reasons.append("interval_width_above_policy")
            rejected = True
        else:
            reasons.append("interval_width_report_only")
    if (
        _maximum_endpoint_delta(interval, sensitivity_intervals)
        > policy.maximum_sensitivity_endpoint_delta
    ):
        reasons.append("block_length_sensitivity_above_policy")
        rejected = True
    canonical_reasons = tuple(sorted(reasons))
    if rejected:
        return "rejected", canonical_reasons
    if canonical_reasons:
        return "descriptive_only", canonical_reasons
    return "descriptive", ()


def _raw_dwell_lengths(
    observations: Sequence[ClusterObservation],
) -> tuple[int, ...]:
    lengths: list[int] = []
    current_length = 0
    previous: ClusterObservation | None = None
    for row in observations:
        if (
            previous is not None
            and is_contiguous_observation(previous, row)
            and previous.label == row.label
        ):
            current_length += 1
        else:
            if current_length:
                lengths.append(current_length)
            current_length = 1
        previous = row
    if current_length:
        lengths.append(current_length)
    return tuple(lengths)


def _dependence_diagnostics(
    *,
    observations: Sequence[ClusterObservation],
    compressed: Sequence[ClusterObservation],
    transition_sequences: Sequence[Sequence[tuple[int, int]]],
    selected_block_length: int,
    sensitivity_block_lengths: tuple[int, ...],
) -> TransitionDependenceDiagnostics:
    dwell_lengths = _raw_dwell_lengths(observations)
    sequence_lengths = tuple(len(sequence) for sequence in transition_sequences if sequence)
    transition_count = sum(sequence_lengths)
    adjacent_overlap_count = sum(max(length - 1, 0) for length in sequence_lengths)
    return TransitionDependenceDiagnostics(
        longest_raw_dwell_run=max(dwell_lengths, default=0),
        median_raw_dwell_run=float(median(dwell_lengths)) if dwell_lengths else 0.0,
        dwell_run_to_observation_ratio=(
            len(compressed) / len(observations) if observations else 0.0
        ),
        transition_sequence_count=len(sequence_lengths),
        transition_sequence_lengths=sequence_lengths,
        maximum_transition_sequence_length=max(sequence_lengths, default=0),
        median_transition_sequence_length=(
            float(median(sequence_lengths)) if sequence_lengths else 0.0
        ),
        adjacent_transition_overlap_fraction=(
            adjacent_overlap_count / transition_count if transition_count else 0.0
        ),
        selected_block_length=selected_block_length,
        sensitivity_block_lengths=sensitivity_block_lengths,
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
