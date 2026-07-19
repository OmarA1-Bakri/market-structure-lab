"""Bounded, deterministic, outcome-blind sequence motif discovery."""

from __future__ import annotations

import heapq
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Sequence

from market_structure_lab.discovery.transitions import is_contiguous_observation

_MAX_WINDOWS = 10_000
_MAX_WINDOW_LENGTH = 10_000
_MAX_TOP_K = 10_000
_MAX_COMPARISONS = 5_000_000
_MAX_POLICY_WINDOW_LENGTHS = 8
_MAX_POLICY_EXCLUSION_ZONES = 8
_MAX_POLICY_TIE_SEEDS = 16
_MAX_POLICY_TIE_POLICIES = 2
_MAX_POLICY_DISTANCE_MULTIPLIERS = 8
_MAX_POLICY_WINDOW_LENGTH = 1_024
_MAX_POLICY_EXCLUSION_ZONE = 1_024
_MAX_POLICY_CANDIDATES = 1_000
_MAX_POLICY_PERTURBATIONS = 1_024
_MAX_SERIALIZED_PERTURBATION_EVIDENCE = 100_000
_MAX_SERIALIZED_MOTIF_EVIDENCE = 1_000_000
# Fixed scalar, string, and container entries are deliberately overestimated here;
# variable-length identifier and universe entries are charged separately below.
_SERIALIZED_REPORT_FIXED_ENTRIES = 32
_SERIALIZED_CANDIDATE_FIXED_ENTRIES = 32
_SERIALIZED_PERTURBATION_FIXED_ENTRIES = 16
_SERIALIZED_RECURRENCE_FIXED_ENTRIES = 8
_SERIALIZED_UNIVERSE_FIXED_ENTRIES = 4
MOTIF_ALGORITHM_VERSION = "boundary-safe-multivariate-motifs-v3"
UNIVARIATE_BASELINE_ALGORITHM_VERSION = "univariate-index-baseline-v1"
MOTIF_REGIME_ASSIGNMENT_VERSION = "outcome-blind-regime-assignments-v1"
MotifTiePolicy = Literal["canonical", "seeded_hash"]
MotifInformationPolicy = Literal["contemporaneous", "trailing_only"]
MotifEvidenceKind = Literal["seed_tie", "subsample", "parameter"]
MotifUniverseDimension = Literal["asset", "period", "regime"]
_CandidateKey = tuple[float, str, str, str, str, str, int, int]
_FORBIDDEN_REGIME_TERMS = (
    "future",
    "forward_return",
    "profit",
    "target_hit",
    "mfe",
    "mae",
    "outcome",
)


@dataclass(frozen=True, slots=True)
class MotifRegimeAssignmentContract:
    """Frozen outcome-blind row-to-regime identity separate from FeatureRow."""

    contract_id: str
    contract_version: str
    algorithm_version: str
    information_policy: MotifInformationPolicy
    outcome_policy: Literal["outcome_blind"]
    regime_universe: tuple[str, ...]
    assignments: tuple[tuple[str, str], ...]
    sha256: str

    def __post_init__(self) -> None:
        _validate_regime_assignment_fields(
            contract_id=self.contract_id,
            contract_version=self.contract_version,
            algorithm_version=self.algorithm_version,
            information_policy=self.information_policy,
            outcome_policy=self.outcome_policy,
            regime_universe=self.regime_universe,
            assignments=self.assignments,
        )
        expected = _regime_assignment_sha256(
            contract_id=self.contract_id,
            contract_version=self.contract_version,
            algorithm_version=self.algorithm_version,
            information_policy=self.information_policy,
            outcome_policy=self.outcome_policy,
            regime_universe=self.regime_universe,
            assignments=self.assignments,
        )
        if self.sha256 != expected:
            raise ValueError("regime assignment SHA-256 does not match canonical content")


def freeze_motif_regime_assignments(
    *,
    contract_id: str,
    algorithm_version: str,
    information_policy: MotifInformationPolicy,
    outcome_policy: Literal["outcome_blind"],
    regime_universe: Sequence[str],
    assignments: Sequence[tuple[str, str]],
) -> MotifRegimeAssignmentContract:
    """Freeze an exact contemporaneous/trailing row-to-regime assignment contract."""

    canonical_universe = tuple(sorted(regime_universe))
    canonical_assignments = tuple(sorted(assignments))
    digest = _regime_assignment_sha256(
        contract_id=contract_id,
        contract_version=MOTIF_REGIME_ASSIGNMENT_VERSION,
        algorithm_version=algorithm_version,
        information_policy=information_policy,
        outcome_policy=outcome_policy,
        regime_universe=canonical_universe,
        assignments=canonical_assignments,
    )
    return MotifRegimeAssignmentContract(
        contract_id=contract_id,
        contract_version=MOTIF_REGIME_ASSIGNMENT_VERSION,
        algorithm_version=algorithm_version,
        information_policy=information_policy,
        outcome_policy=outcome_policy,
        regime_universe=canonical_universe,
        assignments=canonical_assignments,
        sha256=digest,
    )


class _WorstFirstKey:
    """Heap key whose smallest item is the worst retained canonical candidate."""

    __slots__ = ("key",)

    def __init__(self, key: _CandidateKey) -> None:
        self.key = key

    def __lt__(self, other: _WorstFirstKey) -> bool:
        return self.key > other.key


@dataclass(frozen=True, slots=True)
class MotifObservation:
    """One outcome-blind feature row retaining identity and canonical boundaries."""

    row_id: str
    timestamp: datetime
    information_cutoff: datetime
    symbol: str
    timeframe: str
    segment_id: int
    session_id: str
    period_id: str
    regime_id: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        for value, label in (
            (self.row_id, "row_id"),
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
            (self.session_id, "session_id"),
            (self.period_id, "period_id"),
            (self.regime_id, "regime_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        _require_utc(self.timestamp, "timestamp")
        _require_utc(self.information_cutoff, "information_cutoff")
        if self.information_cutoff <= self.timestamp:
            raise ValueError("information_cutoff must be after timestamp")
        if isinstance(self.segment_id, bool) or not isinstance(self.segment_id, int):
            raise TypeError("segment_id must be a non-negative integer")
        if self.segment_id < 0:
            raise ValueError("segment_id must be a non-negative integer")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("values must be a non-empty tuple")
        for motif_value in self.values:
            _finite_number(motif_value, "motif value")


@dataclass(frozen=True, slots=True)
class MotifSequence:
    """One explicitly contiguous multivariate feature sequence."""

    sequence_id: str
    feature_names: tuple[str, ...]
    observations: tuple[MotifObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.sequence_id, str) or not self.sequence_id.strip():
            raise ValueError("sequence_id must be non-empty")
        if (
            not isinstance(self.feature_names, tuple)
            or not self.feature_names
            or self.feature_names != tuple(dict.fromkeys(self.feature_names))
            or any(not isinstance(name, str) or not name.strip() for name in self.feature_names)
        ):
            raise ValueError("feature_names must be a unique non-empty tuple")
        if not isinstance(self.observations, tuple) or not self.observations:
            raise ValueError("observations must be a non-empty tuple")
        if any(not isinstance(row, MotifObservation) for row in self.observations):
            raise TypeError("observations must contain MotifObservation values")
        if any(len(row.values) != len(self.feature_names) for row in self.observations):
            raise ValueError("observation values must match feature_names")
        if any(
            not _is_contiguous_motif_observation(left, right)
            for left, right in zip(self.observations, self.observations[1:])
        ):
            raise ValueError("motif sequence observations must be contiguous")


@dataclass(frozen=True, slots=True)
class MultivariateMotifMatch:
    """A boundary-safe pair of matching multivariate windows."""

    left_sequence_id: str
    right_sequence_id: str
    left_row_id: str
    right_row_id: str
    left_timestamp: datetime
    right_timestamp: datetime
    distance: float
    window_length: int
    feature_names: tuple[str, ...]
    tie_seed: int
    tie_policy: MotifTiePolicy
    algorithm_version: str = MOTIF_ALGORITHM_VERSION

    def __post_init__(self) -> None:
        for value, label in (
            (self.left_sequence_id, "left_sequence_id"),
            (self.right_sequence_id, "right_sequence_id"),
            (self.left_row_id, "left_row_id"),
            (self.right_row_id, "right_row_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        _require_utc(self.left_timestamp, "left_timestamp")
        _require_utc(self.right_timestamp, "right_timestamp")
        _finite_number(self.distance, "distance")
        if self.distance < 0.0:
            raise ValueError("distance must be non-negative")
        _positive_integer(self.window_length, "window_length", minimum=2)
        if not self.feature_names:
            raise ValueError("feature_names must be non-empty")
        if isinstance(self.tie_seed, bool) or not isinstance(self.tie_seed, int):
            raise TypeError("tie_seed must be an integer")
        if self.tie_policy not in ("canonical", "seeded_hash"):
            raise ValueError("tie_policy must be canonical or seeded_hash")
        if self.algorithm_version != MOTIF_ALGORITHM_VERSION:
            raise ValueError("algorithm_version must match the implemented motif algorithm")


@dataclass(frozen=True, slots=True)
class MotifStabilityPolicy:
    """Frozen, explicitly identified perturbation and support policy for motif evidence."""

    policy_id: str
    policy_purpose: str
    window_lengths: tuple[int, ...]
    exclusion_zones: tuple[int, ...]
    tie_seeds: tuple[int, ...]
    tie_policies: tuple[MotifTiePolicy, ...]
    subsample_fraction: float
    distance_multipliers: tuple[float, ...]
    maximum_distance: float
    max_windows: int
    top_k: int
    minimum_seed_rank_agreement: float
    minimum_subsample_agreement: float
    minimum_parameter_agreement: float
    minimum_recurrence_support: int
    minimum_asset_support: int
    minimum_period_support: int
    minimum_regime_support: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.policy_id, "policy_id"),
            (self.policy_purpose, "policy_purpose"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        _canonical_integer_tuple(self.window_lengths, "window_lengths", minimum=2)
        _canonical_integer_tuple(self.exclusion_zones, "exclusion_zones", minimum=0)
        _axis_cap(self.window_lengths, "window_lengths", _MAX_POLICY_WINDOW_LENGTHS)
        _axis_cap(
            self.exclusion_zones,
            "exclusion_zones",
            _MAX_POLICY_EXCLUSION_ZONES,
        )
        if max(self.window_lengths) > _MAX_POLICY_WINDOW_LENGTH:
            raise ValueError("policy window length exceeds its conservative cap")
        if max(self.exclusion_zones) > _MAX_POLICY_EXCLUSION_ZONE:
            raise ValueError("policy exclusion zone exceeds its conservative cap")
        if not self.tie_seeds or len(set(self.tie_seeds)) != len(self.tie_seeds):
            raise ValueError("tie_seeds must be a unique non-empty tuple")
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.tie_seeds):
            raise TypeError("tie_seeds must contain integers")
        _axis_cap(self.tie_seeds, "tie_seeds", _MAX_POLICY_TIE_SEEDS)
        if (
            not isinstance(self.tie_policies, tuple)
            or not self.tie_policies
            or self.tie_policies != tuple(sorted(set(self.tie_policies)))
            or any(policy not in ("canonical", "seeded_hash") for policy in self.tie_policies)
        ):
            raise ValueError("tie_policies must use unique canonical supported values")
        _axis_cap(self.tie_policies, "tie_policies", _MAX_POLICY_TIE_POLICIES)
        fraction = _finite_number(self.subsample_fraction, "subsample_fraction")
        if not 0.0 < fraction <= 1.0:
            raise ValueError("subsample_fraction must be in (0, 1]")
        if not self.distance_multipliers:
            raise ValueError("distance_multipliers must be non-empty")
        for multiplier in self.distance_multipliers:
            if _finite_number(multiplier, "distance_multiplier") <= 0.0:
                raise ValueError("distance_multiplier must be positive")
        if tuple(sorted(set(self.distance_multipliers))) != self.distance_multipliers:
            raise ValueError("distance_multipliers must use unique canonical order")
        _axis_cap(
            self.distance_multipliers,
            "distance_multipliers",
            _MAX_POLICY_DISTANCE_MULTIPLIERS,
        )
        if _finite_number(self.maximum_distance, "maximum_distance") < 0.0:
            raise ValueError("maximum_distance must be non-negative")
        _positive_integer(self.max_windows, "max_windows", minimum=1)
        _positive_integer(self.top_k, "top_k", minimum=1)
        if self.max_windows > _MAX_WINDOWS:
            raise ValueError("policy max_windows exceeds its conservative cap")
        if self.top_k > _MAX_POLICY_CANDIDATES:
            raise ValueError("policy candidate limit exceeds its conservative cap")
        perturbation_count = self.perturbation_count
        if perturbation_count > _MAX_POLICY_PERTURBATIONS:
            raise ValueError("policy Cartesian perturbation count exceeds its cap")
        if self.top_k * perturbation_count > _MAX_SERIALIZED_PERTURBATION_EVIDENCE:
            raise ValueError("policy serialized perturbation evidence exceeds its cap")
        for threshold, label in (
            (self.minimum_seed_rank_agreement, "minimum_seed_rank_agreement"),
            (self.minimum_subsample_agreement, "minimum_subsample_agreement"),
            (self.minimum_parameter_agreement, "minimum_parameter_agreement"),
        ):
            numeric = _finite_number(threshold, label)
            if not 0.0 <= numeric <= 1.0:
                raise ValueError(f"{label} must be in [0, 1]")
        for support_count, label in (
            (self.minimum_recurrence_support, "minimum_recurrence_support"),
            (self.minimum_asset_support, "minimum_asset_support"),
            (self.minimum_period_support, "minimum_period_support"),
            (self.minimum_regime_support, "minimum_regime_support"),
        ):
            _positive_integer(support_count, label, minimum=1)

    @property
    def perturbation_count(self) -> int:
        seed_tie_count = len(self.tie_seeds) * len(self.tie_policies)
        parameter_count = (
            len(self.window_lengths)
            * len(self.exclusion_zones)
            * len(self.distance_multipliers)
        )
        return seed_tie_count * 2 + parameter_count


@dataclass(frozen=True, slots=True)
class MotifRecurrenceSupport:
    """Nearest development recurrence for one explicit asset/period/regime sequence."""

    sequence_id: str
    asset: str
    period_id: str
    regime_id: str
    nearest_distance: float | None
    recurrent: bool

    def __post_init__(self) -> None:
        for value, label in (
            (self.sequence_id, "sequence_id"),
            (self.asset, "asset"),
            (self.period_id, "period_id"),
            (self.regime_id, "regime_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if self.nearest_distance is not None:
            distance = _finite_number(self.nearest_distance, "nearest_distance")
            if distance < 0.0:
                raise ValueError("nearest_distance must be non-negative")
        if not isinstance(self.recurrent, bool):
            raise TypeError("recurrent must be a boolean")


@dataclass(frozen=True, slots=True)
class MotifPerturbationEvidence:
    """One separately retained configured perturbation result for a candidate."""

    evidence_kind: MotifEvidenceKind
    tie_seed: int
    tie_policy: MotifTiePolicy
    selected_sequence_ids: tuple[str, ...]
    window_length: int
    exclusion_zone: int
    distance_multiplier: float
    rank: int | None
    shape_distance: float | None
    supported: bool
    passed: bool
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.evidence_kind not in ("seed_tie", "subsample", "parameter"):
            raise ValueError("evidence_kind is unsupported")
        if isinstance(self.tie_seed, bool) or not isinstance(self.tie_seed, int):
            raise TypeError("tie_seed must be an integer")
        if self.tie_policy not in ("canonical", "seeded_hash"):
            raise ValueError("tie_policy is unsupported")
        if self.selected_sequence_ids != tuple(sorted(set(self.selected_sequence_ids))):
            raise ValueError("selected_sequence_ids must use unique canonical order")
        _positive_integer(self.window_length, "window_length", minimum=2)
        _positive_integer(self.exclusion_zone, "exclusion_zone", minimum=0)
        if _finite_number(self.distance_multiplier, "distance_multiplier") <= 0.0:
            raise ValueError("distance_multiplier must be positive")
        if self.rank is not None:
            _positive_integer(self.rank, "rank", minimum=0)
        if self.shape_distance is not None:
            distance = _finite_number(self.shape_distance, "shape_distance")
            if distance < 0.0:
                raise ValueError("shape_distance must be non-negative")
        if not isinstance(self.supported, bool) or not isinstance(self.passed, bool):
            raise TypeError("supported and passed must be booleans")
        if self.passed and (not self.supported or self.rejection_reasons):
            raise ValueError("passed evidence must be supported without rejection reasons")
        if not self.passed and not self.rejection_reasons:
            raise ValueError("failed evidence must retain rejection reasons")


@dataclass(frozen=True, slots=True)
class MotifUniverseSupport:
    """Explicit support, including zero support, for one frozen universe member."""

    dimension: MotifUniverseDimension
    member_id: str
    sequence_count: int
    recurrent_sequence_count: int

    def __post_init__(self) -> None:
        if self.dimension not in ("asset", "period", "regime"):
            raise ValueError("dimension must be asset, period, or regime")
        if not isinstance(self.member_id, str) or not self.member_id.strip():
            raise ValueError("member_id must be non-empty")
        _positive_integer(self.sequence_count, "sequence_count", minimum=0)
        _positive_integer(
            self.recurrent_sequence_count,
            "recurrent_sequence_count",
            minimum=0,
        )
        if self.recurrent_sequence_count > self.sequence_count:
            raise ValueError("recurrent_sequence_count cannot exceed sequence_count")


@dataclass(frozen=True, slots=True)
class MotifCandidateEvidence:
    """Independent stability and support evidence for one discovery motif candidate."""

    candidate_id: str
    match: MultivariateMotifMatch
    seed_rank_agreement: float
    subsample_agreement: float
    parameter_agreement: float
    seed_tie_evidence: tuple[MotifPerturbationEvidence, ...]
    subsample_evidence: tuple[MotifPerturbationEvidence, ...]
    parameter_evidence: tuple[MotifPerturbationEvidence, ...]
    recurrence_support: tuple[MotifRecurrenceSupport, ...]
    universe_support: tuple[MotifUniverseSupport, ...]
    accepted: bool
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.startswith("MC-"):
            raise ValueError("candidate_id must use the canonical MC- prefix")
        if not isinstance(self.match, MultivariateMotifMatch):
            raise TypeError("match must be a MultivariateMotifMatch")
        for value, label in (
            (self.seed_rank_agreement, "seed_rank_agreement"),
            (self.subsample_agreement, "subsample_agreement"),
            (self.parameter_agreement, "parameter_agreement"),
        ):
            numeric = _finite_number(value, label)
            if not 0.0 <= numeric <= 1.0:
                raise ValueError(f"{label} must be in [0, 1]")
        if any(not isinstance(item, MotifRecurrenceSupport) for item in self.recurrence_support):
            raise TypeError("recurrence_support must contain MotifRecurrenceSupport values")
        for evidence, kind in (
            (self.seed_tie_evidence, "seed_tie"),
            (self.subsample_evidence, "subsample"),
            (self.parameter_evidence, "parameter"),
        ):
            if any(
                not isinstance(item, MotifPerturbationEvidence)
                or item.evidence_kind != kind
                for item in evidence
            ):
                raise TypeError(f"{kind} evidence contains invalid values")
        if any(not isinstance(item, MotifUniverseSupport) for item in self.universe_support):
            raise TypeError("universe_support must contain MotifUniverseSupport values")
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a boolean")
        if self.accepted == bool(self.rejection_reasons):
            raise ValueError("accepted candidates cannot have rejection reasons")

    @property
    def perturbations(self) -> tuple[MotifPerturbationEvidence, ...]:
        return self.seed_tie_evidence + self.subsample_evidence + self.parameter_evidence


@dataclass(frozen=True, slots=True)
class MotifDiscoveryReport:
    """Complete accepted and rejected motif evidence, independent of cluster stability."""

    algorithm_version: str
    feature_names: tuple[str, ...]
    policy: MotifStabilityPolicy
    discovery_sequence_ids: tuple[str, ...]
    development_sequence_ids: tuple[str, ...]
    development_asset_universe: tuple[str, ...]
    development_period_universe: tuple[str, ...]
    development_regime_universe: tuple[str, ...]
    candidates: tuple[MotifCandidateEvidence, ...]

    def __post_init__(self) -> None:
        if self.algorithm_version != MOTIF_ALGORITHM_VERSION:
            raise ValueError("algorithm_version must match the implemented motif algorithm")
        if not isinstance(self.policy, MotifStabilityPolicy):
            raise TypeError("policy must be a MotifStabilityPolicy")
        if not self.feature_names:
            raise ValueError("feature_names must be non-empty")
        for universe, label in (
            (self.development_asset_universe, "development_asset_universe"),
            (self.development_period_universe, "development_period_universe"),
            (self.development_regime_universe, "development_regime_universe"),
        ):
            if universe != tuple(sorted(set(universe))) or not universe:
                raise ValueError(f"{label} must use unique canonical order")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("motif candidates must use unique canonical IDs")

    @property
    def published_count(self) -> int:
        return sum(candidate.accepted for candidate in self.candidates)

    @property
    def rejected_count(self) -> int:
        return sum(not candidate.accepted for candidate in self.candidates)


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
    """Run the explicitly versioned univariate index-only baseline."""

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


def build_contiguous_motif_sequences(
    observations: Sequence[MotifObservation],
    *,
    feature_names: Sequence[str],
) -> tuple[MotifSequence, ...]:
    """Validate canonical order and split rows at every shared sequence boundary."""

    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise TypeError("observations must be a bounded sequence")
    rows = tuple(observations)
    if not rows:
        return ()
    if any(not isinstance(row, MotifObservation) for row in rows):
        raise TypeError("observations must contain MotifObservation values")
    names = tuple(feature_names)
    if (
        not names
        or names != tuple(dict.fromkeys(names))
        or any(not isinstance(name, str) or not name.strip() for name in names)
    ):
        raise ValueError("feature_names must be a unique non-empty sequence")
    if any(len(row.values) != len(names) for row in rows):
        raise ValueError("observation values must match feature_names")
    _validate_observation_order(rows)

    groups: list[list[MotifObservation]] = []
    for row in rows:
        if not groups or not _is_contiguous_motif_observation(groups[-1][-1], row):
            groups.append([row])
        else:
            groups[-1].append(row)
    return tuple(
        MotifSequence(
            sequence_id=_sequence_id(group),
            feature_names=names,
            observations=tuple(group),
        )
        for group in groups
    )


def discover_multivariate_motifs(
    sequences: Sequence[MotifSequence],
    *,
    window_length: int,
    exclusion_zone: int,
    max_windows: int,
    top_k: int,
    tie_seed: int,
    tie_policy: MotifTiePolicy = "seeded_hash",
) -> tuple[MultivariateMotifMatch, ...]:
    """Return closest multivariate windows without ever crossing sequence boundaries."""

    if isinstance(sequences, (str, bytes)) or not isinstance(sequences, Sequence):
        raise TypeError("sequences must be a bounded sequence")
    sequence_values = tuple(sequences)
    if any(not isinstance(sequence, MotifSequence) for sequence in sequence_values):
        raise TypeError("sequences must contain MotifSequence values")
    if not sequence_values:
        return ()
    feature_names = sequence_values[0].feature_names
    if any(sequence.feature_names != feature_names for sequence in sequence_values):
        raise ValueError("all motif sequences must use the same feature definition")
    window_size = _positive_integer(window_length, "window_length", minimum=2)
    zone = _positive_integer(exclusion_zone, "exclusion_zone", minimum=0)
    window_cap = _positive_integer(max_windows, "max_windows", minimum=1)
    result_cap = _positive_integer(top_k, "top_k", minimum=1)
    if isinstance(tie_seed, bool) or not isinstance(tie_seed, int):
        raise TypeError("tie_seed must be an integer")
    if tie_policy not in ("canonical", "seeded_hash"):
        raise ValueError("tie_policy must be canonical or seeded_hash")
    if window_cap > _MAX_WINDOWS:
        raise ValueError(f"max_windows cannot exceed {_MAX_WINDOWS}")
    if window_size > _MAX_WINDOW_LENGTH:
        raise ValueError(f"window_length cannot exceed {_MAX_WINDOW_LENGTH}")
    if result_cap > _MAX_TOP_K:
        raise ValueError(f"top_k cannot exceed {_MAX_TOP_K}")

    windows: list[tuple[MotifSequence, int, tuple[float, ...]]] = []
    for sequence in sequence_values:
        count = max(len(sequence.observations) - window_size + 1, 0)
        if len(windows) + count > window_cap:
            raise ValueError("sequence window count exceeds max_windows")
        for start in range(count):
            normalized = _normalized_multivariate_window(
                sequence.observations[start : start + window_size]
            )
            if normalized is not None:
                windows.append((sequence, start, normalized))
    if len(windows) < 2:
        return ()

    retained: list[tuple[_WorstFirstKey, MultivariateMotifMatch]] = []
    comparisons = 0
    for left_index, (left_sequence, left_start, left) in enumerate(windows):
        for right_sequence, right_start, right in windows[left_index + 1 :]:
            if (
                left_sequence.sequence_id == right_sequence.sequence_id
                and right_start - left_start <= zone
            ):
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
            left_row = left_sequence.observations[left_start]
            right_row = right_sequence.observations[right_start]
            match = MultivariateMotifMatch(
                left_sequence_id=left_sequence.sequence_id,
                right_sequence_id=right_sequence.sequence_id,
                left_row_id=left_row.row_id,
                right_row_id=right_row.row_id,
                left_timestamp=left_row.timestamp,
                right_timestamp=right_row.timestamp,
                distance=distance,
                window_length=window_size,
                feature_names=feature_names,
                tie_seed=tie_seed,
                tie_policy=tie_policy,
            )
            tie_key = _motif_tie_key(match, tie_seed, tie_policy)
            candidate_key = (
                distance,
                tie_key,
                left_sequence.sequence_id,
                right_sequence.sequence_id,
                left_row.row_id,
                right_row.row_id,
                left_start,
                right_start,
            )
            entry = (_WorstFirstKey(candidate_key), match)
            if len(retained) < result_cap:
                heapq.heappush(retained, entry)
            elif candidate_key < retained[0][0].key:
                heapq.heapreplace(retained, entry)
    return tuple(
        item[1] for item in sorted(retained, key=lambda item: item[0].key)
    )


def evaluate_motif_stability(
    *,
    discovery_sequences: Sequence[MotifSequence],
    development_sequences: Sequence[MotifSequence],
    development_asset_universe: Sequence[str],
    development_period_universe: Sequence[str],
    development_regime_universe: Sequence[str],
    policy: MotifStabilityPolicy,
) -> MotifDiscoveryReport:
    """Evaluate discovery candidates under frozen perturbations and untouched development rows."""

    discovery = _validated_sequences(discovery_sequences, "discovery_sequences")
    development = _validated_sequences(development_sequences, "development_sequences")
    if not isinstance(policy, MotifStabilityPolicy):
        raise TypeError("policy must be a MotifStabilityPolicy")
    asset_universe = _canonical_universe(development_asset_universe, "asset universe")
    period_universe = _canonical_universe(development_period_universe, "period universe")
    regime_universe = _canonical_universe(development_regime_universe, "regime universe")
    _validate_development_universes(
        development,
        asset_universe=asset_universe,
        period_universe=period_universe,
        regime_universe=regime_universe,
    )
    feature_names = _shared_feature_names((*discovery, *development))
    universe_member_count = (
        len(asset_universe) + len(period_universe) + len(regime_universe)
    )
    policy_axis_entry_count = (
        len(policy.window_lengths)
        + len(policy.exclusion_zones)
        + len(policy.tie_seeds)
        + len(policy.tie_policies)
        + len(policy.distance_multipliers)
    )
    report_entry_bound = (
        _SERIALIZED_REPORT_FIXED_ENTRIES
        + len(feature_names)
        + policy_axis_entry_count
        + len(discovery)
        + len(development)
        + universe_member_count
    )
    perturbation_entry_bound = (
        _SERIALIZED_PERTURBATION_FIXED_ENTRIES + len(discovery)
    )
    recurrence_entry_bound = _SERIALIZED_RECURRENCE_FIXED_ENTRIES + 4
    universe_entry_bound = _SERIALIZED_UNIVERSE_FIXED_ENTRIES + 2
    candidate_entry_bound = (
        _SERIALIZED_CANDIDATE_FIXED_ENTRIES
        + len(feature_names)
        + policy.perturbation_count * perturbation_entry_bound
        + len(development) * recurrence_entry_bound
        + universe_member_count * universe_entry_bound
    )
    serialized_evidence_bound = (
        report_entry_bound + policy.top_k * candidate_entry_bound
    )
    if serialized_evidence_bound > _MAX_SERIALIZED_MOTIF_EVIDENCE:
        raise ValueError("serialized motif evidence exceeds its conservative cap")
    primary_window = policy.window_lengths[0]
    primary_zone = policy.exclusion_zones[0]
    primary_seed = policy.tie_seeds[0]
    primary_tie_policy = policy.tie_policies[0]
    primary = discover_multivariate_motifs(
        discovery,
        window_length=primary_window,
        exclusion_zone=primary_zone,
        max_windows=policy.max_windows,
        top_k=policy.top_k,
        tie_seed=primary_seed,
        tie_policy=primary_tie_policy,
    )
    seed_rankings = tuple(
        (
            seed,
            tie_policy,
            discover_multivariate_motifs(
                discovery,
                window_length=primary_window,
                exclusion_zone=primary_zone,
                max_windows=policy.max_windows,
                top_k=policy.top_k,
                tie_seed=seed,
                tie_policy=tie_policy,
            ),
        )
        for seed in policy.tie_seeds
        for tie_policy in policy.tie_policies
    )
    subsample_rankings = tuple(
        (
            seed,
            tie_policy,
            selected,
            discover_multivariate_motifs(
                selected,
                window_length=primary_window,
                exclusion_zone=primary_zone,
                max_windows=policy.max_windows,
                top_k=policy.top_k,
                tie_seed=seed,
                tie_policy=tie_policy,
            ),
        )
        for seed in policy.tie_seeds
        for tie_policy in policy.tie_policies
        for selected in (_deterministic_subsample(discovery, policy.subsample_fraction, seed),)
    )
    parameter_rankings = tuple(
        (
            window_length,
            exclusion_zone,
            multiplier,
            discover_multivariate_motifs(
                discovery,
                window_length=window_length,
                exclusion_zone=exclusion_zone,
                max_windows=policy.max_windows,
                top_k=policy.top_k,
                tie_seed=primary_seed,
                tie_policy=primary_tie_policy,
            ),
        )
        for window_length in policy.window_lengths
        for exclusion_zone in policy.exclusion_zones
        for multiplier in policy.distance_multipliers
    )

    candidates: list[MotifCandidateEvidence] = []
    for base_rank, match in enumerate(primary):
        key = _multivariate_match_key(match)
        seed_evidence = tuple(
            _perturbation_evidence(
                evidence_kind="seed_tie",
                key=key,
                matches=ranking,
                tie_seed=seed,
                tie_policy=tie_policy,
                selected_sequence_ids=tuple(sequence.sequence_id for sequence in discovery),
                window_length=primary_window,
                exclusion_zone=primary_zone,
                distance_multiplier=1.0,
                maximum_distance=policy.maximum_distance,
            )
            for seed, tie_policy, ranking in seed_rankings
        )
        subsample_evidence = tuple(
            _perturbation_evidence(
                evidence_kind="subsample",
                key=key,
                matches=ranking,
                tie_seed=seed,
                tie_policy=tie_policy,
                selected_sequence_ids=tuple(sequence.sequence_id for sequence in selected),
                window_length=primary_window,
                exclusion_zone=primary_zone,
                distance_multiplier=1.0,
                maximum_distance=policy.maximum_distance,
            )
            for seed, tie_policy, selected, ranking in subsample_rankings
        )
        parameter_evidence = tuple(
            _perturbation_evidence(
                evidence_kind="parameter",
                key=key,
                matches=ranking,
                tie_seed=primary_seed,
                tie_policy=primary_tie_policy,
                selected_sequence_ids=tuple(sequence.sequence_id for sequence in discovery),
                window_length=window_length,
                exclusion_zone=exclusion_zone,
                distance_multiplier=multiplier,
                maximum_distance=policy.maximum_distance * multiplier,
            )
            for window_length, exclusion_zone, multiplier, ranking in parameter_rankings
        )
        seed_agreement = math.fsum(
            _retained_rank_agreement(item, base_rank, policy.top_k)
            for item in seed_evidence
        ) / len(seed_evidence)
        subsample_agreement = math.fsum(
            item.passed for item in subsample_evidence
        ) / len(subsample_evidence)
        parameter_agreement = math.fsum(
            item.passed for item in parameter_evidence
        ) / len(parameter_evidence)
        recurrence = _development_recurrence(
            match=match,
            discovery_sequences=discovery,
            development_sequences=development,
            maximum_distance=policy.maximum_distance,
        )
        recurrent = tuple(item for item in recurrence if item.recurrent)
        universe_support = _universe_support(
            development_sequences=development,
            recurrence_support=recurrence,
            asset_universe=asset_universe,
            period_universe=period_universe,
            regime_universe=regime_universe,
        )
        reasons: list[str] = []
        if seed_agreement < policy.minimum_seed_rank_agreement:
            reasons.append("seed_rank_agreement_below_policy")
        if subsample_agreement < policy.minimum_subsample_agreement:
            reasons.append("subsample_agreement_below_policy")
        if parameter_agreement < policy.minimum_parameter_agreement:
            reasons.append("parameter_agreement_below_policy")
        if len(recurrent) < policy.minimum_recurrence_support:
            reasons.append("development_recurrence_below_policy")
        if _supported_universe_count(universe_support, "asset") < policy.minimum_asset_support:
            reasons.append("asset_support_below_policy")
        if _supported_universe_count(universe_support, "period") < policy.minimum_period_support:
            reasons.append("period_support_below_policy")
        if _supported_universe_count(universe_support, "regime") < policy.minimum_regime_support:
            reasons.append("regime_support_below_policy")
        candidates.append(
            MotifCandidateEvidence(
                candidate_id=_candidate_id(match),
                match=match,
                seed_rank_agreement=seed_agreement,
                subsample_agreement=subsample_agreement,
                parameter_agreement=parameter_agreement,
                seed_tie_evidence=seed_evidence,
                subsample_evidence=subsample_evidence,
                parameter_evidence=parameter_evidence,
                recurrence_support=recurrence,
                universe_support=universe_support,
                accepted=not reasons,
                rejection_reasons=tuple(reasons),
            )
        )
    return MotifDiscoveryReport(
        algorithm_version=MOTIF_ALGORITHM_VERSION,
        feature_names=feature_names,
        policy=policy,
        discovery_sequence_ids=tuple(sequence.sequence_id for sequence in discovery),
        development_sequence_ids=tuple(sequence.sequence_id for sequence in development),
        development_asset_universe=asset_universe,
        development_period_universe=period_universe,
        development_regime_universe=regime_universe,
        candidates=tuple(sorted(candidates, key=lambda item: item.candidate_id)),
    )


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


def _is_contiguous_motif_observation(
    left: MotifObservation, right: MotifObservation
) -> bool:
    return (
        is_contiguous_observation(left, right)
        and left.period_id == right.period_id
        and left.regime_id == right.regime_id
    )


def _validated_sequences(
    sequences: Sequence[MotifSequence], label: str
) -> tuple[MotifSequence, ...]:
    if isinstance(sequences, (str, bytes)) or not isinstance(sequences, Sequence):
        raise TypeError(f"{label} must be a bounded sequence")
    result = tuple(sequences)
    if any(not isinstance(sequence, MotifSequence) for sequence in result):
        raise TypeError(f"{label} must contain MotifSequence values")
    ids = tuple(sequence.sequence_id for sequence in result)
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} must use unique sequence IDs")
    return tuple(sorted(result, key=lambda sequence: sequence.sequence_id))


def _shared_feature_names(sequences: Sequence[MotifSequence]) -> tuple[str, ...]:
    if not sequences:
        raise ValueError("motif stability requires at least one sequence")
    feature_names = sequences[0].feature_names
    if any(sequence.feature_names != feature_names for sequence in sequences):
        raise ValueError("all motif sequences must use the same feature definition")
    return feature_names


def _canonical_universe(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{label} must be a bounded sequence")
    result = tuple(values)
    if (
        not result
        or any(not isinstance(value, str) or not value.strip() for value in result)
        or result != tuple(sorted(set(result)))
    ):
        raise ValueError(f"{label} must use unique non-empty canonical values")
    return result


def _validate_development_universes(
    sequences: Sequence[MotifSequence],
    *,
    asset_universe: tuple[str, ...],
    period_universe: tuple[str, ...],
    regime_universe: tuple[str, ...],
) -> None:
    for sequence in sequences:
        first = sequence.observations[0]
        if first.symbol not in asset_universe:
            raise ValueError("development sequence asset is outside the frozen universe")
        if first.period_id not in period_universe:
            raise ValueError("development sequence period is outside the frozen universe")
        if first.regime_id not in regime_universe:
            raise ValueError("development sequence regime is outside the frozen universe")


def _deterministic_subsample(
    sequences: tuple[MotifSequence, ...], fraction: float, seed: int
) -> tuple[MotifSequence, ...]:
    if not sequences:
        return ()
    count = max(1, math.ceil(len(sequences) * fraction))
    ranked = sorted(
        sequences,
        key=lambda sequence: hashlib.sha256(
            f"{seed}|{sequence.sequence_id}".encode("utf-8")
        ).hexdigest(),
    )
    return tuple(sorted(ranked[:count], key=lambda sequence: sequence.sequence_id))


def _multivariate_match_key(match: MultivariateMotifMatch) -> tuple[str, str]:
    return match.left_row_id, match.right_row_id


def _perturbation_evidence(
    *,
    evidence_kind: MotifEvidenceKind,
    key: tuple[str, str],
    matches: Sequence[MultivariateMotifMatch],
    tie_seed: int,
    tie_policy: MotifTiePolicy,
    selected_sequence_ids: tuple[str, ...],
    window_length: int,
    exclusion_zone: int,
    distance_multiplier: float,
    maximum_distance: float,
) -> MotifPerturbationEvidence:
    located = next(
        (
            (rank, match)
            for rank, match in enumerate(matches)
            if _multivariate_match_key(match) == key
        ),
        None,
    )
    reasons: tuple[str, ...]
    if located is None:
        rank = None
        distance = None
        supported = False
        passed = False
        reasons = ("candidate_absent",)
    else:
        rank, matched = located
        distance = matched.distance
        supported = True
        passed = distance <= maximum_distance
        reasons = () if passed else ("shape_distance_above_threshold",)
    return MotifPerturbationEvidence(
        evidence_kind=evidence_kind,
        tie_seed=tie_seed,
        tie_policy=tie_policy,
        selected_sequence_ids=tuple(sorted(selected_sequence_ids)),
        window_length=window_length,
        exclusion_zone=exclusion_zone,
        distance_multiplier=distance_multiplier,
        rank=rank,
        shape_distance=distance,
        supported=supported,
        passed=passed,
        rejection_reasons=reasons,
    )


def _retained_rank_agreement(
    evidence: MotifPerturbationEvidence, base_rank: int, top_k: int
) -> float:
    if not evidence.passed or evidence.rank is None:
        return 0.0
    denominator = max(top_k - 1, 1)
    return max(0.0, 1.0 - abs(evidence.rank - base_rank) / denominator)


def _universe_support(
    *,
    development_sequences: Sequence[MotifSequence],
    recurrence_support: Sequence[MotifRecurrenceSupport],
    asset_universe: tuple[str, ...],
    period_universe: tuple[str, ...],
    regime_universe: tuple[str, ...],
) -> tuple[MotifUniverseSupport, ...]:
    sequence_dimensions = tuple(
        (
            sequence.observations[0].symbol,
            sequence.observations[0].period_id,
            sequence.observations[0].regime_id,
        )
        for sequence in development_sequences
    )
    recurrent_ids = {
        item.sequence_id for item in recurrence_support if item.recurrent
    }
    rows: list[MotifUniverseSupport] = []
    dimensions: tuple[
        tuple[MotifUniverseDimension, tuple[str, ...], int], ...
    ] = (
        ("asset", asset_universe, 0),
        ("period", period_universe, 1),
        ("regime", regime_universe, 2),
    )
    for dimension, members, index in dimensions:
        for member in members:
            matching_ids = tuple(
                sequence.sequence_id
                for sequence, values in zip(
                    development_sequences, sequence_dimensions, strict=True
                )
                if values[index] == member
            )
            rows.append(
                MotifUniverseSupport(
                    dimension=dimension,
                    member_id=member,
                    sequence_count=len(matching_ids),
                    recurrent_sequence_count=sum(
                        sequence_id in recurrent_ids for sequence_id in matching_ids
                    ),
                )
            )
    return tuple(rows)


def _supported_universe_count(
    support: Sequence[MotifUniverseSupport], dimension: MotifUniverseDimension
) -> int:
    return sum(
        item.dimension == dimension and item.recurrent_sequence_count > 0
        for item in support
    )


def _development_recurrence(
    *,
    match: MultivariateMotifMatch,
    discovery_sequences: tuple[MotifSequence, ...],
    development_sequences: tuple[MotifSequence, ...],
    maximum_distance: float,
) -> tuple[MotifRecurrenceSupport, ...]:
    source = next(
        sequence
        for sequence in discovery_sequences
        if sequence.sequence_id == match.left_sequence_id
    )
    source_start = next(
        index
        for index, row in enumerate(source.observations)
        if row.row_id == match.left_row_id
    )
    source_window = _normalized_multivariate_window(
        source.observations[source_start : source_start + match.window_length]
    )
    if source_window is None:
        raise RuntimeError("published motif candidate has no normalized source window")
    support: list[MotifRecurrenceSupport] = []
    for sequence in development_sequences:
        distances: list[float] = []
        count = max(len(sequence.observations) - match.window_length + 1, 0)
        for start in range(count):
            candidate = _normalized_multivariate_window(
                sequence.observations[start : start + match.window_length]
            )
            if candidate is not None:
                distances.append(
                    math.sqrt(
                        math.fsum(
                            (left - right) ** 2
                            for left, right in zip(source_window, candidate, strict=True)
                        )
                    )
                )
        nearest = min(distances) if distances else None
        first = sequence.observations[0]
        support.append(
            MotifRecurrenceSupport(
                sequence_id=sequence.sequence_id,
                asset=first.symbol,
                period_id=first.period_id,
                regime_id=first.regime_id,
                nearest_distance=nearest,
                recurrent=nearest is not None and nearest <= maximum_distance,
            )
        )
    return tuple(support)


def _candidate_id(match: MultivariateMotifMatch) -> str:
    payload = "|".join(
        (
            match.algorithm_version,
            match.left_sequence_id,
            match.right_sequence_id,
            match.left_row_id,
            match.right_row_id,
            str(match.window_length),
        )
    )
    return f"MC-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16].upper()}"


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


def _normalized_multivariate_window(
    observations: Sequence[MotifObservation],
) -> tuple[float, ...] | None:
    width = len(observations[0].values)
    normalized_features: list[tuple[float, ...]] = []
    for column in range(width):
        values = tuple(row.values[column] for row in observations)
        mean = math.fsum(values) / len(values)
        centered = tuple(value - mean for value in values)
        squared_scale = math.fsum(value**2 for value in centered)
        if squared_scale == 0.0:
            normalized_features.append((0.0,) * len(values))
            continue
        standard_deviation = math.sqrt(squared_scale / len(values))
        normalized_features.append(tuple(value / standard_deviation for value in centered))
    if all(all(value == 0.0 for value in feature) for feature in normalized_features):
        return None
    return tuple(
        normalized_features[column][row]
        for row in range(len(observations))
        for column in range(width)
    )


def _validate_observation_order(rows: Sequence[MotifObservation]) -> None:
    previous: MotifObservation | None = None
    previous_key: tuple[str, str, int, str] | None = None
    seen_keys: set[tuple[str, str, int, str]] = set()
    seen_row_ids: set[str] = set()
    for row in rows:
        if row.row_id in seen_row_ids:
            raise ValueError("motif observations must use unique row IDs in canonical order")
        key = (row.symbol, row.timeframe, row.segment_id, row.session_id)
        if previous is not None and previous_key is not None:
            if key < previous_key or (key == previous_key and row.timestamp <= previous.timestamp):
                raise ValueError("motif observations must use canonical order")
            if key != previous_key and key in seen_keys:
                raise ValueError("motif observation boundary groups must use canonical order")
        seen_keys.add(key)
        seen_row_ids.add(row.row_id)
        previous = row
        previous_key = key


def _sequence_id(rows: Sequence[MotifObservation]) -> str:
    first = rows[0]
    last = rows[-1]
    payload = "|".join(
        (
            first.symbol,
            first.timeframe,
            str(first.segment_id),
            first.session_id,
            first.row_id,
            last.row_id,
        )
    )
    return f"MS-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16].upper()}"


def _motif_tie_key(
    match: MultivariateMotifMatch, seed: int, policy: MotifTiePolicy
) -> str:
    if policy == "canonical":
        return "|".join(
            (
                match.left_sequence_id,
                match.right_sequence_id,
                match.left_row_id,
                match.right_row_id,
            )
        )
    payload = "|".join(
        (
            str(seed),
            match.left_sequence_id,
            match.right_sequence_id,
            match.left_row_id,
            match.right_row_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}")
    return value


def _canonical_integer_tuple(values: object, label: str, *, minimum: int) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise TypeError(f"{label} must contain integers")
    if any(value < minimum for value in values):
        raise ValueError(f"{label} values must be at least {minimum}")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must use unique canonical order")


def _axis_cap(values: Sequence[object], label: str, maximum: int) -> None:
    if len(values) > maximum:
        raise ValueError(f"{label} exceeds its conservative axis cap of {maximum}")


def _validate_regime_assignment_fields(
    *,
    contract_id: str,
    contract_version: str,
    algorithm_version: str,
    information_policy: object,
    outcome_policy: object,
    regime_universe: tuple[str, ...],
    assignments: tuple[tuple[str, str], ...],
) -> None:
    for value, label in (
        (contract_id, "contract_id"),
        (contract_version, "contract_version"),
        (algorithm_version, "algorithm_version"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be non-empty")
    if contract_version != MOTIF_REGIME_ASSIGNMENT_VERSION:
        raise ValueError("contract_version must match the implemented regime contract")
    if information_policy not in ("contemporaneous", "trailing_only"):
        raise ValueError("information_policy must be contemporaneous or trailing_only")
    if outcome_policy != "outcome_blind":
        raise ValueError("outcome_policy must explicitly prohibit outcome or future labels")
    _validate_regime_names(regime_universe, "regime_universe")
    if not assignments or assignments != tuple(sorted(assignments)):
        raise ValueError("assignments must use non-empty canonical order")
    row_ids = tuple(row_id for row_id, _ in assignments)
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("assignments must map each row ID exactly once")
    for row_id, regime_id in assignments:
        if not isinstance(row_id, str) or not row_id.strip():
            raise ValueError("assignment row IDs must be non-empty")
        if regime_id not in regime_universe:
            raise ValueError("assignment regime must belong to regime_universe")


def _validate_regime_names(values: tuple[str, ...], label: str) -> None:
    if (
        not values
        or values != tuple(sorted(set(values)))
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError(f"{label} must use unique non-empty canonical values")
    for value in values:
        lowered = value.lower()
        if any(term in lowered for term in _FORBIDDEN_REGIME_TERMS):
            raise ValueError("regime labels must explicitly prohibit outcome or future semantics")


def _regime_assignment_sha256(
    *,
    contract_id: str,
    contract_version: str,
    algorithm_version: str,
    information_policy: object,
    outcome_policy: object,
    regime_universe: tuple[str, ...],
    assignments: tuple[tuple[str, str], ...],
) -> str:
    _validate_regime_assignment_fields(
        contract_id=contract_id,
        contract_version=contract_version,
        algorithm_version=algorithm_version,
        information_policy=information_policy,
        outcome_policy=outcome_policy,
        regime_universe=regime_universe,
        assignments=assignments,
    )
    payload = {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "algorithm_version": algorithm_version,
        "information_policy": information_policy,
        "outcome_policy": outcome_policy,
        "regime_universe": list(regime_universe),
        "assignments": [list(item) for item in assignments],
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _require_utc(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(
        value
    ):
        raise ValueError(f"{label} must be timezone-aware UTC")


def _match_key(match: MotifMatch) -> tuple[float, int, int]:
    return match.distance, match.left_start, match.right_start
