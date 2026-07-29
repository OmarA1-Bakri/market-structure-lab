"""Deterministic Phase 5 robustness, exposure, capacity, and retirement gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from statistics import median, stdev
import re

import numpy as np

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    ValidationTerminalState,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VP-[a-f0-9]{64}$")
_EVALUATION_ID = re.compile(r"^VR-[a-f0-9]{64}$")
_PASS = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.SUPPORTED_DEVELOPMENT)
_REJECTED = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.REJECTED)
_INCONCLUSIVE = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE)
_REGIMES = ("up_low_vol", "up_high_vol", "down_low_vol", "down_high_vol")
_CONTINUOUS_COLUMNS = ("market_return", "market_trend", "volatility_rank", "turnover_rank")
_ALLOWED_LOOKBACK_PARAMETERS = {
    "A": ("ma_fast", "ma_slow", "donchian", "atr", "tsmom"),
    "B": ("profile",),
    "G": ("atr_short", "atr_long", "donchian"),
    "E": ("donchian", "volume_median"),
    "D": ("level",),
}
_PERTURBATION_DIRECTIONS = ("n_L-1", "n_L+1")
_OLS_INTERVAL_METHOD = "normal-95-validation-residual-mean-v1"


def _require_non_empty(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_programme_id(value: object) -> str:
    if not isinstance(value, str) or _PROGRAMME_ID.fullmatch(value) is None:
        raise ValueError("programme_id must be a VP identity")
    return value


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    converted = value.astimezone(UTC)
    if converted.utcoffset() != value.utcoffset():
        raise ValueError(f"{label} must be UTC-aware")
    return converted


def _finite_or_none(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric or None")
    converted = float(value)
    if not isfinite(converted):
        return None
    return converted


def _require_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _identity_number(value: float | None) -> object:
    if value is None:
        return None
    if not isfinite(value):
        return "nonfinite"
    return value


@dataclass(frozen=True, slots=True)
class ExclusionStrengthObservation:
    """One development-only asset/year base-cost contrast used for strongest exclusions."""

    asset: str
    year: int
    weekly_mean_base_contrast: float | None
    development: bool
    event_count: int
    source_publication_sha256: str

    def __post_init__(self) -> None:
        _require_non_empty(self.asset, "asset")
        if isinstance(self.year, bool) or not isinstance(self.year, int):
            raise ValueError("year must be an integer")
        object.__setattr__(
            self,
            "weekly_mean_base_contrast",
            _finite_or_none(self.weekly_mean_base_contrast, "weekly_mean_base_contrast"),
        )
        if not isinstance(self.development, bool):
            raise TypeError("development must be bool")
        if (
            isinstance(self.event_count, bool)
            or not isinstance(self.event_count, int)
            or self.event_count < 0
        ):
            raise ValueError("event_count must be a non-negative integer")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")

    @property
    def finite_strength(self) -> float | None:
        if not self.development or self.event_count == 0:
            return None
        return self.weekly_mean_base_contrast

    def to_dict(self) -> dict[str, object]:
        return {
            "asset": self.asset,
            "year": self.year,
            "weekly_mean_base_contrast": self.weekly_mean_base_contrast,
            "development": self.development,
            "event_count": self.event_count,
            "source_publication_sha256": self.source_publication_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExStrongestSelection:
    """Identity-bound ex-strongest asset/year selector output."""

    programme_id: str
    candidate_id: str
    asset: str | None
    year: int | None
    asset_strength: float | None
    year_strength: float | None
    score_table_sha256: str
    no_event_strata: tuple[str, ...]
    terminal_state: ValidationTerminalState
    reason: str
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_programme_id(self.programme_id)
        _require_non_empty(self.candidate_id, "candidate_id")
        if self.asset is not None:
            _require_non_empty(self.asset, "asset")
        if self.year is not None and (
            isinstance(self.year, bool) or not isinstance(self.year, int)
        ):
            raise ValueError("year must be an integer")
        _require_sha256(self.score_table_sha256, "score_table_sha256")
        if not isinstance(self.terminal_state, ValidationTerminalState):
            raise TypeError("terminal_state must be ValidationTerminalState")
        _require_non_empty(self.reason, "reason")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def replay_sha256(self) -> str:
        return self.score_table_sha256

    @property
    def sha256(self) -> str:
        return hash_json("ex-strongest-selection-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "programme_id": self.programme_id,
            "candidate_id": self.candidate_id,
            "asset": self.asset,
            "year": self.year,
            "asset_strength": self.asset_strength,
            "year_strength": self.year_strength,
            "score_table_sha256": self.score_table_sha256,
            "no_event_strata": list(self.no_event_strata),
            "decision": self.terminal_state.decision.value,
            "reason": self.reason,
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("ex-strongest selection identity does not match payload")


def select_ex_strongest_development_exclusions(
    observations: Sequence[ExclusionStrengthObservation], *, programme_id: str, candidate_id: str
) -> ExStrongestSelection:
    """Select development-only strongest asset and UTC year with deterministic tie rules."""

    _require_programme_id(programme_id)
    _require_non_empty(candidate_id, "candidate_id")
    rows: list[ExclusionStrengthObservation] = []
    for row in observations:
        if not isinstance(row, ExclusionStrengthObservation):
            raise TypeError("observations must contain ExclusionStrengthObservation")
        if not row.development:
            raise ValueError("ex-strongest selector accepts development-only observations")
        rows.append(row)
    score_table_sha = hash_json(
        "ex-strongest-score-table-v1",
        {
            "programme_id": programme_id,
            "candidate_id": candidate_id,
            "observations": [
                row.to_dict() for row in sorted(rows, key=lambda item: (item.asset, item.year))
            ],
        },
    )
    development_assets = sorted({row.asset for row in rows if row.development})
    development_years = sorted({row.year for row in rows if row.development})
    no_events = tuple(
        f"{row.asset}:{row.year}"
        for row in sorted(rows, key=lambda item: (item.asset, item.year))
        if row.development and row.finite_strength is None
    )
    if len(development_assets) < 2:
        return ExStrongestSelection(
            programme_id,
            candidate_id,
            None,
            None,
            None,
            None,
            score_table_sha,
            no_events,
            _INCONCLUSIVE,
            "one asset is insufficient for ex-strongest asset exclusion",
        )
    if len(development_years) < 2:
        return ExStrongestSelection(
            programme_id,
            candidate_id,
            None,
            None,
            None,
            None,
            score_table_sha,
            no_events,
            _INCONCLUSIVE,
            "one year is insufficient for ex-strongest UTC-year exclusion",
        )
    finite_rows = [row for row in rows if row.finite_strength is not None]
    if not finite_rows:
        return ExStrongestSelection(
            programme_id,
            candidate_id,
            None,
            None,
            None,
            None,
            score_table_sha,
            no_events,
            _INCONCLUSIVE,
            "no finite development strata",
        )
    asset_scores: dict[str, float] = {}
    for asset_name in development_assets:
        strengths = tuple(row.finite_strength for row in finite_rows if row.asset == asset_name)
        finite_strengths = tuple(value for value in strengths if value is not None)
        if finite_strengths:
            asset_scores[asset_name] = max(finite_strengths)
    year_scores: dict[int, float] = {}
    for utc_year in development_years:
        strengths = tuple(row.finite_strength for row in finite_rows if row.year == utc_year)
        finite_strengths = tuple(value for value in strengths if value is not None)
        if finite_strengths:
            year_scores[utc_year] = max(finite_strengths)
    asset, asset_strength = sorted(asset_scores.items(), key=lambda item: (-item[1], item[0]))[0]
    year, year_strength = sorted(year_scores.items(), key=lambda item: (-item[1], item[0]))[0]
    return ExStrongestSelection(
        programme_id,
        candidate_id,
        asset,
        year,
        asset_strength,
        year_strength,
        score_table_sha,
        no_events,
        _PASS,
        "development-only strongest asset and UTC year selected",
    )


@dataclass(frozen=True, slots=True)
class AdjacentLookbackPerturbation:
    """One exact adjacent one-at-a-time lookback perturbation."""

    parameter: str
    original_lookback: int
    perturbed_lookback: int
    direction: str
    corrected_lower_bound: float

    def __post_init__(self) -> None:
        _require_non_empty(self.parameter, "parameter")
        for field_name in ("original_lookback", "perturbed_lookback"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.direction not in _PERTURBATION_DIRECTIONS:
            raise ValueError("direction must be n_L-1 or n_L+1")
        expected = (
            self.original_lookback - 1 if self.direction == "n_L-1" else self.original_lookback + 1
        )
        if self.perturbed_lookback != expected:
            raise ValueError("perturbed_lookback must be the exact adjacent lookback")
        object.__setattr__(
            self,
            "corrected_lower_bound",
            _require_finite(self.corrected_lower_bound, "corrected_lower_bound"),
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.parameter, self.direction)

    def to_dict(self) -> dict[str, object]:
        return {
            "parameter": self.parameter,
            "original_lookback": self.original_lookback,
            "perturbed_lookback": self.perturbed_lookback,
            "direction": self.direction,
            "corrected_lower_bound": self.corrected_lower_bound,
        }


@dataclass(frozen=True, slots=True)
class AdjacentLookbackPerturbationSet:
    """Exact typed adjacent perturbation roster for one candidate/family slot."""

    candidate_id: str
    family: str
    slot_id: str
    original_lookbacks: Mapping[str, int]
    perturbations: tuple[AdjacentLookbackPerturbation, ...]
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.candidate_id, "candidate_id")
        _require_non_empty(self.slot_id, "slot_id")
        if self.family not in _ALLOWED_LOOKBACK_PARAMETERS:
            raise ValueError("family must be one of A, B, G, E, D")
        if not self.original_lookbacks:
            raise ValueError("original_lookbacks must be non-empty")
        allowed = set(_ALLOWED_LOOKBACK_PARAMETERS[self.family])
        originals: dict[str, int] = {}
        for parameter, original in self.original_lookbacks.items():
            _require_non_empty(parameter, "lookback parameter")
            if parameter not in allowed:
                raise ValueError("unknown lookback parameter for family")
            if isinstance(original, bool) or not isinstance(original, int) or original < 2:
                raise ValueError("original lookback must be an integer >= 2")
            originals[parameter] = original
        if len(originals) != len(self.original_lookbacks):
            raise ValueError("duplicate original lookback parameter")
        expected_keys = {
            (parameter, direction)
            for parameter in originals
            for direction in _PERTURBATION_DIRECTIONS
        }
        seen: dict[tuple[str, str], AdjacentLookbackPerturbation] = {}
        for perturbation in self.perturbations:
            if not isinstance(perturbation, AdjacentLookbackPerturbation):
                raise TypeError("perturbations must contain AdjacentLookbackPerturbation")
            if perturbation.parameter not in originals:
                raise ValueError("unexpected adjacent-lookback perturbation")
            if perturbation.original_lookback != originals[perturbation.parameter]:
                raise ValueError("perturbation original lookback does not match roster")
            if perturbation.key in seen:
                raise ValueError("duplicate adjacent-lookback perturbation")
            seen[perturbation.key] = perturbation
        missing = sorted(expected_keys.difference(seen))
        extra = sorted(set(seen).difference(expected_keys))
        if missing:
            parameter, direction = missing[0]
            raise ValueError(f"missing adjacent-lookback perturbation {parameter}:{direction}")
        if extra:
            raise ValueError("unexpected adjacent-lookback perturbation")
        object.__setattr__(self, "original_lookbacks", dict(sorted(originals.items())))
        object.__setattr__(
            self, "perturbations", tuple(sorted(self.perturbations, key=lambda item: item.key))
        )
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def lower_bounds(self) -> tuple[float, ...]:
        return tuple(item.corrected_lower_bound for item in self.perturbations)

    @property
    def sha256(self) -> str:
        return hash_json("adjacent-lookback-perturbation-set-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "slot_id": self.slot_id,
            "original_lookbacks": dict(sorted(self.original_lookbacks.items())),
            "perturbations": [item.to_dict() for item in self.perturbations],
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("adjacent-lookback perturbation identity does not match payload")


@dataclass(frozen=True, slots=True)
class RobustnessLowerBounds:
    """All mandatory lower-bound and fold-sign robustness gates for a primary slot."""

    base: float
    doubled_cost: float
    one_bar_delay: float
    ex_strongest_asset: float
    ex_strongest_year: float
    adjacent_lookback_perturbations: AdjacentLookbackPerturbationSet
    outer_fold_point_estimates: tuple[float, float, float, float]
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "base",
            "doubled_cost",
            "one_bar_delay",
            "ex_strongest_asset",
            "ex_strongest_year",
        ):
            object.__setattr__(
                self, field_name, _require_finite(getattr(self, field_name), field_name)
            )
        if not isinstance(self.adjacent_lookback_perturbations, AdjacentLookbackPerturbationSet):
            raise TypeError(
                "adjacent_lookback_perturbations must be AdjacentLookbackPerturbationSet"
            )
        object.__setattr__(
            self,
            "outer_fold_point_estimates",
            tuple(
                _require_finite(value, "outer_fold_point_estimate")
                for value in self.outer_fold_point_estimates
            ),
        )
        if len(self.outer_fold_point_estimates) != 4:
            raise ValueError("outer_fold_point_estimates must contain exactly four folds")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json("robustness-lower-bounds-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "base": self.base,
            "doubled_cost": self.doubled_cost,
            "one_bar_delay": self.one_bar_delay,
            "ex_strongest_asset": self.ex_strongest_asset,
            "ex_strongest_year": self.ex_strongest_year,
            "adjacent_lookback_perturbations_sha256": self.adjacent_lookback_perturbations.sha256,
            "outer_fold_point_estimates": list(self.outer_fold_point_estimates),
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("robustness lower bounds identity does not match payload")


@dataclass(frozen=True, slots=True)
class RobustnessGateEvidence:
    terminal_state: ValidationTerminalState
    reason: str
    base_positive: bool
    stress_positive: bool
    delay_positive: bool
    ex_strongest_asset_positive: bool
    ex_strongest_year_positive: bool
    perturbations_positive: bool
    fold_signs_positive_3_of_4_with_fold4: bool
    lower_bounds_sha256: str | None = None
    minimum_positive_lower_bound: float = 0.0
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_state, ValidationTerminalState):
            raise TypeError("terminal_state must be ValidationTerminalState")
        _require_non_empty(self.reason, "reason")
        for field_name in (
            "base_positive",
            "stress_positive",
            "delay_positive",
            "ex_strongest_asset_positive",
            "ex_strongest_year_positive",
            "perturbations_positive",
            "fold_signs_positive_3_of_4_with_fold4",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        if self.lower_bounds_sha256 is not None:
            _require_sha256(self.lower_bounds_sha256, "lower_bounds_sha256")
        object.__setattr__(
            self,
            "minimum_positive_lower_bound",
            _require_finite(self.minimum_positive_lower_bound, "minimum_positive_lower_bound"),
        )
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json("robustness-gate-evidence-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "decision": self.terminal_state.decision.value,
            "reason": self.reason,
            "base_positive": self.base_positive,
            "stress_positive": self.stress_positive,
            "delay_positive": self.delay_positive,
            "ex_strongest_asset_positive": self.ex_strongest_asset_positive,
            "ex_strongest_year_positive": self.ex_strongest_year_positive,
            "perturbations_positive": self.perturbations_positive,
            "fold_signs_positive_3_of_4_with_fold4": self.fold_signs_positive_3_of_4_with_fold4,
            "lower_bounds_sha256": self.lower_bounds_sha256,
            "minimum_positive_lower_bound": self.minimum_positive_lower_bound,
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("robustness gate evidence identity does not match payload")


def evaluate_robustness_gates(
    bounds: RobustnessLowerBounds, *, minimum_positive_lower_bound: float = 0.0
) -> RobustnessGateEvidence:
    """Evaluate completed robustness gates; non-positive completed criteria reject."""

    if not isinstance(bounds, RobustnessLowerBounds):
        raise TypeError("bounds must be RobustnessLowerBounds")
    margin = _require_finite(minimum_positive_lower_bound, "minimum_positive_lower_bound")
    values = {
        "base": bounds.base,
        "doubled cost": bounds.doubled_cost,
        "one-bar delay": bounds.one_bar_delay,
        "ex-strongest asset": bounds.ex_strongest_asset,
        "ex-strongest UTC year": bounds.ex_strongest_year,
    }
    try:
        finite_values = {key: _require_finite(value, key) for key, value in values.items()}
        perturbations = bounds.adjacent_lookback_perturbations.lower_bounds
        folds = tuple(
            _require_finite(value, "outer_fold_point_estimate")
            for value in bounds.outer_fold_point_estimates
        )
    except ValueError as error:
        return RobustnessGateEvidence(
            _INCONCLUSIVE,
            str(error),
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            bounds.sha256,
            margin,
        )
    positives = {key: value > margin for key, value in finite_values.items()}
    perturbations_positive = all(value > margin for value in perturbations)
    fold_gate = sum(value > 0.0 for value in folds) >= 3 and folds[3] > 0.0
    if not all(positives.values()) or not perturbations_positive or not fold_gate:
        return RobustnessGateEvidence(
            _REJECTED,
            "completed robustness lower-bound, perturbation, or fold-sign gate is non-positive",
            positives["base"],
            positives["doubled cost"],
            positives["one-bar delay"],
            positives["ex-strongest asset"],
            positives["ex-strongest UTC year"],
            perturbations_positive,
            fold_gate,
            bounds.sha256,
            margin,
        )
    return RobustnessGateEvidence(
        _PASS,
        "all robustness gates pass",
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        bounds.sha256,
        margin,
    )


@dataclass(frozen=True, slots=True)
class MarketFeatureObservation:
    asset: str
    timestamp: datetime
    return_value: float
    trend_value: float
    volatility_24h: float
    turnover_24h: float

    def __post_init__(self) -> None:
        _require_non_empty(self.asset, "asset")
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        for field_name in ("return_value", "trend_value", "volatility_24h", "turnover_24h"):
            object.__setattr__(
                self, field_name, _require_finite(getattr(self, field_name), field_name)
            )


@dataclass(frozen=True, slots=True)
class TargetExcludedMarketFeatures:
    target_asset: str
    timestamp: datetime
    market_return: float
    market_trend: float
    target_volatility_rank: float
    target_turnover_rank: float
    non_target_asset_count: int
    non_target_assets: tuple[str, ...]


def _rank_against_non_targets(target: float, non_targets: Sequence[float]) -> float:
    if not non_targets:
        raise ValueError("non-target ranks require observations")
    strictly_less = sum(1 for value in non_targets if value < target)
    equal = sum(1 for value in non_targets if value == target)
    return (strictly_less + 0.5 * equal) / len(non_targets)


def target_excluded_market_features(
    target_asset: str, timestamp: datetime, observations: Sequence[MarketFeatureObservation]
) -> TargetExcludedMarketFeatures:
    """Compute target-excluded equal-weight market and prior-24h rank factors."""

    _require_non_empty(target_asset, "target_asset")
    stamp = _require_utc(timestamp, "timestamp")
    rows = [row for row in observations if row.timestamp == stamp]
    row_assets = [row.asset for row in rows]
    if len(row_assets) != len(set(row_assets)):
        raise ValueError("duplicate asset identity for timestamp")
    target_rows = [row for row in rows if row.asset == target_asset]
    if len(target_rows) != 1:
        raise ValueError("exactly one target observation is required")
    non_targets = sorted(
        (row for row in rows if row.asset != target_asset), key=lambda row: row.asset
    )
    if len(non_targets) < 3:
        raise ValueError("at least three non-target assets are required")
    return TargetExcludedMarketFeatures(
        target_asset=target_asset,
        timestamp=stamp,
        market_return=sum(row.return_value for row in non_targets) / len(non_targets),
        market_trend=sum(row.trend_value for row in non_targets) / len(non_targets),
        target_volatility_rank=_rank_against_non_targets(
            target_rows[0].volatility_24h, [row.volatility_24h for row in non_targets]
        ),
        target_turnover_rank=_rank_against_non_targets(
            target_rows[0].turnover_24h, [row.turnover_24h for row in non_targets]
        ),
        non_target_asset_count=len(non_targets),
        non_target_assets=tuple(row.asset for row in non_targets),
    )


@dataclass(frozen=True, slots=True)
class RegimePolicy:
    volatility_rank_median: float
    support: int
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "volatility_rank_median",
            _require_finite(self.volatility_rank_median, "volatility_rank_median"),
        )
        if isinstance(self.support, bool) or not isinstance(self.support, int) or self.support < 1:
            raise ValueError("support must be a positive integer")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json(
            "regime-policy-v1",
            {"volatility_rank_median": self.volatility_rank_median, "support": self.support},
        )

    def classify(self, *, market_trend: float, volatility_rank: float) -> str | None:
        trend = _require_finite(market_trend, "market_trend")
        vol = _require_finite(volatility_rank, "volatility_rank")
        if trend == 0.0:
            return None
        trend_label = "up" if trend > 0.0 else "down"
        vol_label = "low_vol" if vol <= self.volatility_rank_median else "high_vol"
        return f"{trend_label}_{vol_label}"


def fit_inner_training_regime_policy(volatility_ranks: Sequence[float]) -> RegimePolicy:
    """Fit the regime volatility split from inner-training ranks only."""

    values = tuple(_require_finite(value, "volatility_rank") for value in volatility_ranks)
    if not values:
        raise ValueError("inner training volatility ranks must be non-empty")
    return RegimePolicy(float(median(values)), len(values))


@dataclass(frozen=True, slots=True)
class WeeklyRegimeObservation:
    """One UTC-week regime return observation with source identity."""

    week_start: datetime
    value: float
    source_publication_sha256: str

    def __post_init__(self) -> None:
        week_start = _require_utc(self.week_start, "week_start")
        if (
            week_start.hour != 0
            or week_start.minute != 0
            or week_start.second != 0
            or week_start.microsecond != 0
            or week_start.weekday() != 0
        ):
            raise ValueError("week_start must be Monday 00:00 UTC")
        object.__setattr__(self, "week_start", week_start)
        object.__setattr__(self, "value", _require_finite(self.value, "weekly regime value"))
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")

    @property
    def sha256(self) -> str:
        return hash_json("weekly-regime-observation-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "week_start": self.week_start,
            "value": self.value,
            "source_publication_sha256": self.source_publication_sha256,
        }


@dataclass(frozen=True, slots=True)
class RegimeReturnEvidence:
    regime: str
    weekly_observations: tuple[WeeklyRegimeObservation, ...]
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.regime not in _REGIMES:
            raise ValueError("regime must be one of the four non-zero-trend regimes")
        for observation in self.weekly_observations:
            if not isinstance(observation, WeeklyRegimeObservation):
                raise TypeError("weekly_observations must contain WeeklyRegimeObservation")
        week_starts = [observation.week_start for observation in self.weekly_observations]
        if len(week_starts) != len(set(week_starts)):
            raise ValueError("duplicate UTC week in regime evidence")
        object.__setattr__(
            self,
            "weekly_observations",
            tuple(sorted(self.weekly_observations, key=lambda item: item.week_start)),
        )
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def support(self) -> int:
        return len(self.weekly_observations)

    @property
    def mean(self) -> float | None:
        if not self.weekly_observations:
            return None
        return sum(observation.value for observation in self.weekly_observations) / len(
            self.weekly_observations
        )

    @property
    def sha256(self) -> str:
        return hash_json("regime-return-evidence-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "regime": self.regime,
            "weekly_observations": [
                observation.to_dict() for observation in self.weekly_observations
            ],
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("regime return evidence identity does not match payload")


@dataclass(frozen=True, slots=True)
class RegimeGateEvidence:
    terminal_state: ValidationTerminalState
    reason: str
    supported_positive_regimes: tuple[str, ...]
    supported_negative_regimes: tuple[str, ...]
    unsupported_regimes: tuple[str, ...]
    minimum_weeks: int = 2
    negative_threshold: float = 0.0
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_state, ValidationTerminalState):
            raise TypeError("terminal_state must be ValidationTerminalState")
        _require_non_empty(self.reason, "reason")
        for regime in (
            *self.supported_positive_regimes,
            *self.supported_negative_regimes,
            *self.unsupported_regimes,
        ):
            if regime not in _REGIMES:
                raise ValueError("regime evidence contains an unknown regime")
        if isinstance(self.minimum_weeks, bool) or not isinstance(self.minimum_weeks, int):
            raise ValueError("minimum_weeks must be an integer")
        object.__setattr__(
            self,
            "negative_threshold",
            _require_finite(self.negative_threshold, "negative_threshold"),
        )
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json("regime-gate-evidence-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "decision": self.terminal_state.decision.value,
            "reason": self.reason,
            "supported_positive_regimes": list(self.supported_positive_regimes),
            "supported_negative_regimes": list(self.supported_negative_regimes),
            "unsupported_regimes": list(self.unsupported_regimes),
            "minimum_weeks": self.minimum_weeks,
            "negative_threshold": self.negative_threshold,
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("regime gate evidence identity does not match payload")


def evaluate_regime_gate(
    regimes: Sequence[RegimeReturnEvidence],
    *,
    minimum_weeks: int = 2,
    negative_threshold: float = 0.0,
) -> RegimeGateEvidence:
    """Require at least two supported positive regimes and reject clearly negative support."""

    if isinstance(minimum_weeks, bool) or not isinstance(minimum_weeks, int) or minimum_weeks < 1:
        raise ValueError("minimum_weeks must be a positive integer")
    threshold = _require_finite(negative_threshold, "negative_threshold")
    by_regime = {item.regime: item for item in regimes}
    if len(by_regime) != len(regimes):
        raise ValueError("duplicate regime evidence")
    supported_positive: list[str] = []
    supported_negative: list[str] = []
    unsupported: list[str] = []
    for name in _REGIMES:
        evidence = by_regime.get(name, RegimeReturnEvidence(name, ()))
        if evidence.support < minimum_weeks:
            unsupported.append(name)
            continue
        mean_value = evidence.mean
        if mean_value is None:
            unsupported.append(name)
        elif mean_value <= threshold:
            supported_negative.append(name)
        elif mean_value > 0.0:
            supported_positive.append(name)
    if supported_negative:
        return RegimeGateEvidence(
            _REJECTED,
            "clearly negative supported regime",
            tuple(supported_positive),
            tuple(supported_negative),
            tuple(unsupported),
            minimum_weeks,
            threshold,
        )
    if len(supported_positive) >= 2:
        return RegimeGateEvidence(
            _PASS,
            "at least two supported positive regimes",
            tuple(supported_positive),
            (),
            tuple(unsupported),
            minimum_weeks,
            threshold,
        )
    if len(supported_positive) == 1:
        return RegimeGateEvidence(
            _INCONCLUSIVE,
            "exactly one supported positive regime",
            tuple(supported_positive),
            (),
            tuple(unsupported),
            minimum_weeks,
            threshold,
        )
    return RegimeGateEvidence(
        _INCONCLUSIVE,
        "insufficient supported positive regimes",
        (),
        (),
        tuple(unsupported),
        minimum_weeks,
        threshold,
    )


@dataclass(frozen=True, slots=True)
class OlsExposureEvent:
    row_id: str
    asset: str
    year: int
    target_return: float
    market_return: float
    market_trend: float
    volatility_rank: float
    turnover_rank: float

    def __post_init__(self) -> None:
        _require_non_empty(self.row_id, "row_id")
        _require_non_empty(self.asset, "asset")
        if isinstance(self.year, bool) or not isinstance(self.year, int):
            raise ValueError("year must be an integer")
        for field_name in _CONTINUOUS_COLUMNS + ("target_return",):
            object.__setattr__(
                self, field_name, _require_finite(getattr(self, field_name), field_name)
            )


@dataclass(frozen=True, slots=True)
class OlsDesignEvidence:
    column_names: tuple[str, ...]
    intercept_count: int
    reference_asset: str
    reference_year: int
    means: Mapping[str, float]
    scales: Mapping[str, float]
    rank: int
    column_count: int
    scaling_sha256: str
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.column_names:
            raise ValueError("column_names must be non-empty")
        if self.column_names.count("intercept") != self.intercept_count:
            raise ValueError("intercept_count must match column_names")
        _require_non_empty(self.reference_asset, "reference_asset")
        if isinstance(self.reference_year, bool) or not isinstance(self.reference_year, int):
            raise ValueError("reference_year must be an integer")
        means = {key: _require_finite(value, f"mean {key}") for key, value in self.means.items()}
        scales = {key: _require_finite(value, f"scale {key}") for key, value in self.scales.items()}
        if set(means) != set(_CONTINUOUS_COLUMNS) or set(scales) != set(_CONTINUOUS_COLUMNS):
            raise ValueError("means and scales must cover all continuous exposure columns")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 0:
            raise ValueError("rank must be a non-negative integer")
        if (
            isinstance(self.column_count, bool)
            or not isinstance(self.column_count, int)
            or self.column_count != len(self.column_names)
        ):
            raise ValueError("column_count must match column_names")
        _require_sha256(self.scaling_sha256, "scaling_sha256")
        object.__setattr__(self, "means", dict(sorted(means.items())))
        object.__setattr__(self, "scales", dict(sorted(scales.items())))
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json("ols-design-evidence-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "column_names": list(self.column_names),
            "intercept_count": self.intercept_count,
            "reference_asset": self.reference_asset,
            "reference_year": self.reference_year,
            "means": dict(sorted(self.means.items())),
            "scales": dict(sorted(self.scales.items())),
            "rank": self.rank,
            "column_count": self.column_count,
            "scaling_sha256": self.scaling_sha256,
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("OLS design evidence identity does not match payload")


@dataclass(frozen=True, slots=True)
class OlsExposureEvidence:
    terminal_state: ValidationTerminalState
    reason: str
    design: OlsDesignEvidence
    residual_mean: float | None
    residual_lower_bound: float | None
    validation_design_rows: tuple[tuple[float, ...], ...]
    residual_support: int = 0
    interval_method: str = _OLS_INTERVAL_METHOD
    residuals_sha256: str | None = None
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_state, ValidationTerminalState):
            raise TypeError("terminal_state must be ValidationTerminalState")
        _require_non_empty(self.reason, "reason")
        if not isinstance(self.design, OlsDesignEvidence):
            raise TypeError("design must be OlsDesignEvidence")
        object.__setattr__(
            self, "residual_mean", _finite_or_none(self.residual_mean, "residual_mean")
        )
        object.__setattr__(
            self,
            "residual_lower_bound",
            _finite_or_none(self.residual_lower_bound, "residual_lower_bound"),
        )
        if isinstance(self.residual_support, bool) or not isinstance(self.residual_support, int):
            raise ValueError("residual_support must be an integer")
        if self.interval_method != _OLS_INTERVAL_METHOD:
            raise ValueError(f"interval_method must be {_OLS_INTERVAL_METHOD}")
        if self.residuals_sha256 is not None:
            _require_sha256(self.residuals_sha256, "residuals_sha256")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def replay_scaling_sha256(self) -> str:
        return hash_json(
            "ols-exposure-scaling-v1",
            {
                "reference_asset": self.design.reference_asset,
                "reference_year": self.design.reference_year,
                "means": dict(sorted(self.design.means.items())),
                "scales": dict(sorted(self.design.scales.items())),
                "columns": list(self.design.column_names),
            },
        )

    @property
    def sha256(self) -> str:
        return hash_json("ols-exposure-evidence-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "decision": self.terminal_state.decision.value,
            "reason": self.reason,
            "design_sha256": self.design.sha256,
            "residual_mean": self.residual_mean,
            "residual_lower_bound": self.residual_lower_bound,
            "residual_support": self.residual_support,
            "interval_method": self.interval_method,
            "residuals_sha256": self.residuals_sha256,
            "validation_design_rows": [list(row) for row in self.validation_design_rows],
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("OLS exposure evidence identity does not match payload")


def evaluate_exposure_ols(
    train_events: Sequence[OlsExposureEvent],
    validation_events: Sequence[OlsExposureEvent],
) -> OlsExposureEvidence:
    """Fit inner-training-only OLS residualization with fixed treatment coding."""

    if len(train_events) < 2 or not validation_events:
        raise ValueError("train and validation events must be non-empty")
    train_row_ids = [event.row_id for event in train_events]
    validation_row_ids = [event.row_id for event in validation_events]
    if len(train_row_ids) != len(set(train_row_ids)):
        raise ValueError("duplicate train row_id")
    if len(validation_row_ids) != len(set(validation_row_ids)):
        raise ValueError("duplicate validation row_id")
    if set(train_row_ids).intersection(validation_row_ids):
        raise ValueError("train and validation row_id sets must be disjoint")
    reference_asset = sorted({event.asset for event in train_events})[0]
    reference_year = sorted({event.year for event in train_events})[0]
    asset_levels = tuple(
        asset
        for asset in sorted({event.asset for event in train_events})
        if asset != reference_asset
    )
    year_levels = tuple(
        year for year in sorted({event.year for event in train_events}) if year != reference_year
    )
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for column in _CONTINUOUS_COLUMNS:
        values = [getattr(event, column) for event in train_events]
        mean_value = sum(values) / len(values)
        scale = (sum((value - mean_value) ** 2 for value in values) / len(values)) ** 0.5
        means[column] = mean_value
        scales[column] = scale if scale > 0.0 else 1.0
    column_names = (
        "intercept",
        *_CONTINUOUS_COLUMNS,
        *(f"asset_{asset}" for asset in asset_levels),
        *(f"year_{year}" for year in year_levels),
    )
    train_matrix = np.asarray(
        [
            _encode_ols_row(event, means, scales, asset_levels, year_levels)
            for event in train_events
        ],
        dtype=float,
    )
    validation_matrix = np.asarray(
        [
            _encode_ols_row(event, means, scales, asset_levels, year_levels)
            for event in validation_events
        ],
        dtype=float,
    )
    y_train = np.asarray([event.target_return for event in train_events], dtype=float)
    rank = int(np.linalg.matrix_rank(train_matrix))
    scaling_sha = hash_json(
        "ols-exposure-scaling-v1",
        {
            "reference_asset": reference_asset,
            "reference_year": reference_year,
            "means": dict(sorted(means.items())),
            "scales": dict(sorted(scales.items())),
            "columns": list(column_names),
        },
    )
    design = OlsDesignEvidence(
        column_names=column_names,
        intercept_count=column_names.count("intercept"),
        reference_asset=reference_asset,
        reference_year=reference_year,
        means=means,
        scales=scales,
        rank=rank,
        column_count=len(column_names),
        scaling_sha256=scaling_sha,
    )
    if design.intercept_count != 1:
        return OlsExposureEvidence(
            _INCONCLUSIVE,
            "design must contain exactly one intercept",
            design,
            None,
            None,
            tuple(map(tuple, validation_matrix.tolist())),
            len(validation_events),
        )
    if rank < len(column_names):
        return OlsExposureEvidence(
            _INCONCLUSIVE,
            "rank deficient exposure design",
            design,
            None,
            None,
            tuple(map(tuple, validation_matrix.tolist())),
            len(validation_events),
        )
    beta, *_ = np.linalg.lstsq(train_matrix, y_train, rcond=None)
    residuals = np.asarray(
        [event.target_return for event in validation_events], dtype=float
    ) - validation_matrix.dot(beta)
    residual_values = tuple(float(value) for value in residuals.tolist())
    residual_mean = float(np.mean(residuals))
    residuals_sha = hash_json(
        "ols-validation-residuals-v1",
        {
            "row_ids": [event.row_id for event in validation_events],
            "residuals": list(residual_values),
        },
    )
    if len(residual_values) < 2:
        return OlsExposureEvidence(
            _INCONCLUSIVE,
            "fewer than two validation residuals",
            design,
            residual_mean,
            None,
            tuple(map(tuple, validation_matrix.tolist())),
            len(residual_values),
            _OLS_INTERVAL_METHOD,
            residuals_sha,
        )
    sigma = stdev(residual_values)
    lower_bound = residual_mean - 1.96 * sigma / (len(residual_values) ** 0.5)
    decision = (
        ScientificDecision.SUPPORTED_DEVELOPMENT
        if lower_bound > 0.0
        else ScientificDecision.REJECTED
    )
    state = ValidationTerminalState(ExecutionStatus.COMPLETED, decision)
    reason = (
        "exposure residual lower bound is positive"
        if lower_bound > 0.0
        else "exposure residual lower bound is not positive"
    )
    return OlsExposureEvidence(
        state,
        reason,
        design,
        residual_mean,
        lower_bound,
        tuple(map(tuple, validation_matrix.tolist())),
        len(residual_values),
        _OLS_INTERVAL_METHOD,
        residuals_sha,
    )


def _encode_ols_row(
    event: OlsExposureEvent,
    means: Mapping[str, float],
    scales: Mapping[str, float],
    asset_levels: Sequence[str],
    year_levels: Sequence[int],
) -> tuple[float, ...]:
    continuous = tuple(
        (getattr(event, column) - means[column]) / scales[column] for column in _CONTINUOUS_COLUMNS
    )
    assets = tuple(1.0 if event.asset == asset else 0.0 for asset in asset_levels)
    years = tuple(1.0 if event.year == year else 0.0 for year in year_levels)
    return (1.0, *continuous, *assets, *years)


class PromotionEligibilityStatus(StrEnum):
    PROMOTABLE = "promotable"
    INCONCLUSIVE_CAPACITY = "inconclusive_capacity"
    NOT_ELIGIBLE = "not_eligible"


@dataclass(frozen=True, slots=True)
class CapacityEvidence:
    candidate_id: str
    volume_relative_capacity: float | None
    market_impact_bps: float | None
    evidence_sha256: str
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.candidate_id, "candidate_id")
        if self.volume_relative_capacity is not None and isinstance(
            self.volume_relative_capacity, bool
        ):
            raise ValueError("volume_relative_capacity must be numeric")
        if self.market_impact_bps is not None and isinstance(self.market_impact_bps, bool):
            raise ValueError("market_impact_bps must be numeric")
        object.__setattr__(
            self,
            "volume_relative_capacity",
            None if self.volume_relative_capacity is None else float(self.volume_relative_capacity),
        )
        object.__setattr__(
            self,
            "market_impact_bps",
            None if self.market_impact_bps is None else float(self.market_impact_bps),
        )
        _require_sha256(self.evidence_sha256, "capacity evidence_sha256")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def complete_and_finite(self) -> bool:
        return (
            self.volume_relative_capacity is not None
            and self.market_impact_bps is not None
            and isfinite(self.volume_relative_capacity)
            and isfinite(self.market_impact_bps)
        )

    @property
    def sha256(self) -> str:
        return hash_json(
            "capacity-evidence-v1",
            {
                "candidate_id": self.candidate_id,
                "volume_relative_capacity": _identity_number(self.volume_relative_capacity),
                "market_impact_bps": _identity_number(self.market_impact_bps),
                "evidence_sha256": self.evidence_sha256,
            },
        )

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("capacity evidence identity does not match payload")


@dataclass(frozen=True, slots=True)
class PromotionEligibility:
    validation_decision: ScientificDecision
    status: PromotionEligibilityStatus
    reason: str
    capacity_evidence: CapacityEvidence | None
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.validation_decision, ScientificDecision):
            raise TypeError("validation_decision must be ScientificDecision")
        if not isinstance(self.status, PromotionEligibilityStatus):
            raise TypeError("status must be PromotionEligibilityStatus")
        _require_non_empty(self.reason, "reason")
        if self.capacity_evidence is not None and not isinstance(
            self.capacity_evidence, CapacityEvidence
        ):
            raise TypeError("capacity_evidence must be CapacityEvidence or None")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json("promotion-eligibility-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "validation_decision": self.validation_decision.value,
            "status": self.status.value,
            "reason": self.reason,
            "capacity_evidence_sha256": (
                None if self.capacity_evidence is None else self.capacity_evidence.sha256
            ),
        }

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("promotion eligibility identity does not match payload")


def evaluate_capacity_promotion(
    validation_decision: ScientificDecision, capacity: CapacityEvidence | None
) -> PromotionEligibility:
    """Promotion requires validated research plus finite capacity/impact evidence."""

    if not isinstance(validation_decision, ScientificDecision):
        raise TypeError("validation_decision must be ScientificDecision")
    if validation_decision in {
        ScientificDecision.REJECTED,
        ScientificDecision.INCONCLUSIVE,
        ScientificDecision.NOT_EVALUATED,
    }:
        return PromotionEligibility(
            validation_decision,
            PromotionEligibilityStatus.NOT_ELIGIBLE,
            "candidate scientific decision is not eligible for promotion",
            capacity,
        )
    if validation_decision is ScientificDecision.SUPPORTED_DEVELOPMENT:
        if capacity is None or not capacity.complete_and_finite:
            return PromotionEligibility(
                validation_decision,
                PromotionEligibilityStatus.INCONCLUSIVE_CAPACITY,
                "capacity evidence missing or non-finite",
                capacity,
            )
        return PromotionEligibility(
            validation_decision,
            PromotionEligibilityStatus.NOT_ELIGIBLE,
            "supported development candidate is not validated",
            capacity,
        )
    if validation_decision is not ScientificDecision.VALIDATED:
        return PromotionEligibility(
            validation_decision,
            PromotionEligibilityStatus.NOT_ELIGIBLE,
            "candidate scientific decision is not eligible for promotion",
            capacity,
        )
    if capacity is None or not capacity.complete_and_finite:
        return PromotionEligibility(
            validation_decision,
            PromotionEligibilityStatus.INCONCLUSIVE_CAPACITY,
            "capacity evidence missing or non-finite",
            capacity,
        )
    return PromotionEligibility(
        validation_decision,
        PromotionEligibilityStatus.PROMOTABLE,
        "validated candidate has finite capacity evidence",
        capacity,
    )


@dataclass(frozen=True, slots=True)
class RetirementRecord:
    candidate_id: str
    reason: str
    effective_time: datetime
    superseding_identity: str | None
    invalidation_evidence_sha256: str
    identity_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.candidate_id, "candidate_id")
        _require_non_empty(self.reason, "reason")
        object.__setattr__(
            self, "effective_time", _require_utc(self.effective_time, "effective_time")
        )
        if (
            self.superseding_identity is not None
            and _EVALUATION_ID.fullmatch(self.superseding_identity) is None
        ):
            raise ValueError("superseding_identity must be a VR identity or None")
        _require_sha256(self.invalidation_evidence_sha256, "invalidation_evidence_sha256")
        if self.identity_sha256 is None:
            object.__setattr__(self, "identity_sha256", self.sha256)

    @property
    def sha256(self) -> str:
        return hash_json(
            "retirement-record-v1",
            {
                "candidate_id": self.candidate_id,
                "reason": self.reason,
                "effective_time": self.effective_time,
                "superseding_identity": self.superseding_identity,
                "invalidation_evidence_sha256": self.invalidation_evidence_sha256,
            },
        )

    def verify_identity(self) -> None:
        if self.identity_sha256 != self.sha256:
            raise ValueError("retirement record identity does not match payload")


__all__ = [
    "AdjacentLookbackPerturbation",
    "AdjacentLookbackPerturbationSet",
    "CapacityEvidence",
    "ExStrongestSelection",
    "ExclusionStrengthObservation",
    "MarketFeatureObservation",
    "OlsDesignEvidence",
    "OlsExposureEvent",
    "OlsExposureEvidence",
    "PromotionEligibility",
    "PromotionEligibilityStatus",
    "RegimeGateEvidence",
    "RegimePolicy",
    "RegimeReturnEvidence",
    "RetirementRecord",
    "RobustnessGateEvidence",
    "RobustnessLowerBounds",
    "TargetExcludedMarketFeatures",
    "WeeklyRegimeObservation",
    "evaluate_capacity_promotion",
    "evaluate_exposure_ols",
    "evaluate_regime_gate",
    "evaluate_robustness_gates",
    "fit_inner_training_regime_policy",
    "select_ex_strongest_development_exclusions",
    "target_excluded_market_features",
]
