"""Pure Phase 5 statistical evidence and decision contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import ceil, isfinite
import re
from statistics import NormalDist, stdev

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    ValidationTerminalState,
    VALIDATION_SLOT_ROSTER,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VP-[a-f0-9]{64}$")
_BOOTSTRAP_POLICIES = {
    (4_096, 4_096, 262_144): (102, 3_993),
    (4_800, 4_800, 307_200): (119, 4_679),
}
_FAMILY_ALPHA = 0.01
_EXPECTED_PRIMARY_COUNTS = {"A": 24, "B": 8, "G": 8, "E": 8, "D": 16}
_REQUIRED_CONTROLS = {
    "A": ("naive", "unconditional", "persistence"),
    "B": ("price_baseline", "structure_only"),
    "G": ("atr_only", "donchian_only"),
    "E": ("price_only", "rate_matched_placebo"),
    "D": ("failed_donchian", "pseudo_level"),
}


def _require_non_empty(value: object, label: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _require_positive(value: object, label: str) -> float:
    converted = _require_finite(value, label)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value.astimezone(UTC)


def _week_start(timestamp: datetime) -> datetime:
    utc = _require_utc(timestamp, "timestamp")
    start_of_day = datetime(utc.year, utc.month, utc.day, tzinfo=UTC)
    return start_of_day - timedelta(days=start_of_day.weekday())


class FamilyInferenceRole(StrEnum):
    """Whether one statistic is allowed to participate in promotion decisions."""

    PRIMARY = "primary"
    CONTROL = "control"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, slots=True)
class WeeklyVectorObservation:
    """One outcome-blind vector observation before weekly inferential compression."""

    row_id: str
    asset: str
    timestamp: datetime
    values: Mapping[str, float]
    source_publication_sha256: str

    def __post_init__(self) -> None:
        _require_non_empty(self.row_id, "row_id")
        _require_non_empty(self.asset, "asset")
        _require_utc(self.timestamp, "timestamp")
        if not self.values:
            raise ValueError("values must be non-empty")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")


@dataclass(frozen=True, slots=True)
class WeeklyVectorMean:
    """Synchronized, equal-asset weekly vector mean."""

    week_start: datetime
    week_end: datetime
    values: Mapping[str, float]
    row_ids: tuple[str, ...]
    assets: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hash_json(
            "weekly-vector-mean-v1",
            {
                "week_start": self.week_start.isoformat(),
                "week_end": self.week_end.isoformat(),
                "values": dict(sorted(self.values.items())),
                "row_ids": list(self.row_ids),
                "assets": list(self.assets),
            },
        )


@dataclass(frozen=True, slots=True)
class WeeklyVectorEvidence:
    """Bounded evidence for weekly aggregation."""

    weeks: tuple[WeeklyVectorMean, ...]
    vector_estimates: Mapping[str, float]
    support: int
    excluded_weeks: Mapping[datetime, str]
    source_publication_sha256: str

    @property
    def sha256(self) -> str:
        return hash_json(
            "weekly-vector-evidence-v1",
            {
                "weeks": [week.sha256 for week in self.weeks],
                "vector_estimates": dict(sorted(self.vector_estimates.items())),
                "support": self.support,
                "excluded_weeks": {
                    key.isoformat(): value for key, value in sorted(self.excluded_weeks.items())
                },
                "source_publication_sha256": self.source_publication_sha256,
            },
        )


@dataclass(frozen=True, slots=True)
class BootstrapEvidence:
    """Completed or inconclusive weekly-block bootstrap evidence."""

    status: ExecutionStatus
    decision: ScientificDecision
    reason: str
    support: int
    estimate: float | None
    sigma_block: float | None
    ci_lower: float | None
    ci_upper: float | None
    p_value: float | None
    required_support: int | None
    draw_count: int
    ci_lower_index: int
    ci_upper_index: int
    seed: int
    samples_sha256: str | None
    sample_algorithm: str = "sha-index-bootstrap-v1"

    def __post_init__(self) -> None:
        ValidationTerminalState(self.status, self.decision)

    @property
    def terminal_state(self) -> ValidationTerminalState:
        return ValidationTerminalState(self.status, self.decision)


@dataclass(frozen=True, slots=True)
class CostOpportunity:
    """One admissible inner-training base round-trip cost observation."""

    row_id: str
    base_round_trip_cost: float
    component: str
    source_sha256: str

    def __post_init__(self) -> None:
        _require_non_empty(self.row_id, "row_id")
        if isinstance(self.base_round_trip_cost, bool) or not isinstance(
            self.base_round_trip_cost, (int, float)
        ):
            raise ValueError("base_round_trip_cost must be numeric")
        object.__setattr__(self, "base_round_trip_cost", float(self.base_round_trip_cost))
        _require_non_empty(self.component, "component")
        _require_sha256(self.source_sha256, "source_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "base_round_trip_cost": self.base_round_trip_cost,
            "component": self.component,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class MdeEvidence:
    """Identity-bound minimum-detectable-effect evidence from inner training costs."""

    mde: float | None
    tied_row_ids: tuple[str, ...]
    opportunity_set_sha256: str
    max_identity_sha256: str
    support: int
    status: ExecutionStatus = ExecutionStatus.COMPLETED
    decision: ScientificDecision = ScientificDecision.REJECTED
    reason: str = "mde evidence completed"

    def __post_init__(self) -> None:
        ValidationTerminalState(self.status, self.decision)

    @property
    def terminal_state(self) -> ValidationTerminalState:
        return ValidationTerminalState(self.status, self.decision)


@dataclass(frozen=True, slots=True)
class EvaluationStatistic:
    """One family primary p-value before multiplicity correction."""

    slot_id: str
    family: str
    p_value: float | None
    status: ExecutionStatus
    decision: ScientificDecision
    role: FamilyInferenceRole = FamilyInferenceRole.PRIMARY

    def __post_init__(self) -> None:
        _require_non_empty(self.slot_id, "slot_id")
        _require_family(self.family)
        if self.p_value is not None:
            value = _require_finite(self.p_value, "p_value")
            if value < 0.0 or value > 1.0:
                raise ValueError("p_value must be in [0, 1]")
        ValidationTerminalState(self.status, self.decision)
        if not isinstance(self.role, FamilyInferenceRole):
            raise TypeError("role must be FamilyInferenceRole")

    @property
    def evaluable(self) -> bool:
        return (
            self.status is ExecutionStatus.COMPLETED
            and self.decision is ScientificDecision.REJECTED
            and self.p_value is not None
            and self.role is FamilyInferenceRole.PRIMARY
        )


@dataclass(frozen=True, slots=True)
class HolmAdjustedStatistic:
    """One Holm-corrected family statistic."""

    slot_id: str
    family: str
    raw_p_value: float | None
    effective_p_value: float
    adjusted_p_value: float
    rejected: bool
    status: ExecutionStatus
    decision: ScientificDecision


@dataclass(frozen=True, slots=True)
class ControlStatistic:
    """Corrected control evidence for intersection-union family decisions."""

    role: str
    corrected_p_value: float
    corrected_lower_bound: float
    support_sufficient: bool
    inference_role: FamilyInferenceRole = FamilyInferenceRole.CONTROL

    def __post_init__(self) -> None:
        _require_non_empty(self.role, "role")
        p_value = _require_finite(self.corrected_p_value, "corrected_p_value")
        if p_value < 0.0 or p_value > 1.0:
            raise ValueError("corrected_p_value must be in [0, 1]")
        _require_finite(self.corrected_lower_bound, "corrected_lower_bound")
        if not isinstance(self.support_sufficient, bool):
            raise TypeError("support_sufficient must be bool")
        if not isinstance(self.inference_role, FamilyInferenceRole):
            raise TypeError("inference_role must be FamilyInferenceRole")


@dataclass(frozen=True, slots=True)
class FamilyDecisionInput:
    """All non-final gates needed to classify one family in development."""

    family: str
    controls: tuple[ControlStatistic, ...]
    policy_valid: bool = True
    cost_coverage_complete: bool = True
    support_sufficient: bool = True
    interval_width_ok: bool = True
    robustness_gate: bool = False
    exposure_gate: bool = False
    diagnostic_only: bool = False

    def __post_init__(self) -> None:
        _require_family(self.family)
        for label in (
            "policy_valid",
            "cost_coverage_complete",
            "support_sufficient",
            "interval_width_ok",
            "robustness_gate",
            "exposure_gate",
            "diagnostic_only",
        ):
            if not isinstance(getattr(self, label), bool):
                raise TypeError(f"{label} must be bool")


@dataclass(frozen=True, slots=True)
class FamilyDecisionEvidence:
    """Terminal scientific decision and IUT diagnostics for one family."""

    input: FamilyDecisionInput
    terminal_state: ValidationTerminalState
    reason: str
    worst_p_value: float | None
    worst_lower_bound: float | None
    participating_roles: tuple[str, ...]


def _require_family(family: str) -> None:
    if family not in _EXPECTED_PRIMARY_COUNTS:
        raise ValueError("family must be one of A, B, G, E, D")


def deterministic_validation_seed(
    *, programme_id: str, candidate_id: str, fold_id: str, purpose: str
) -> int:
    """Return the first unsigned 64 bits of the domain-separated validation seed hash."""

    if _PROGRAMME_ID.fullmatch(programme_id) is None:
        raise ValueError("programme_id must be a VP identity")
    _require_non_empty(candidate_id, "candidate_id")
    _require_non_empty(fold_id, "fold_id")
    _require_non_empty(purpose, "purpose")
    digest = hash_json(
        "validation-seed-v1",
        {
            "programme_id": programme_id,
            "candidate_id": candidate_id,
            "fold_id": fold_id,
            "purpose": purpose,
        },
    )
    return int(digest[:16], 16)


def aggregate_weekly_vectors(
    observations: Iterable[WeeklyVectorObservation],
    *,
    vector_names: Sequence[str],
    expected_assets: Sequence[str],
    fold_start: datetime,
    fold_end: datetime,
    source_publication_sha256: str,
) -> WeeklyVectorEvidence:
    """Aggregate finite paired vectors into Monday 00:00 UTC half-open weekly units."""

    if not vector_names:
        raise ValueError("vector_names must be non-empty")
    if not expected_assets:
        raise ValueError("expected_assets must be non-empty")
    vectors = tuple(vector_names)
    assets = tuple(expected_assets)
    if len(set(vectors)) != len(vectors):
        raise ValueError("vector_names must be unique")
    if len(set(assets)) != len(assets):
        raise ValueError("expected_assets must be unique")
    for vector in vectors:
        _require_non_empty(vector, "vector_name")
    for asset in assets:
        _require_non_empty(asset, "asset")
    start = _require_utc(fold_start, "fold_start")
    end = _require_utc(fold_end, "fold_end")
    if start >= end:
        raise ValueError("fold_start must be before fold_end")
    _require_sha256(source_publication_sha256, "source_publication_sha256")

    grouped: dict[datetime, list[WeeklyVectorObservation]] = {}
    for observation in observations:
        if not isinstance(observation, WeeklyVectorObservation):
            raise TypeError("observations must contain WeeklyVectorObservation")
        week_start = _week_start(observation.timestamp)
        week_end = week_start + timedelta(days=7)
        if week_start < start or week_end > end:
            continue
        grouped.setdefault(week_start, []).append(observation)

    weeks: list[WeeklyVectorMean] = []
    excluded: dict[datetime, str] = {}
    for week_start in sorted(grouped):
        rows = grouped[week_start]
        if any(row.source_publication_sha256 != source_publication_sha256 for row in rows):
            excluded[week_start] = "publication mismatch"
            continue
        by_asset: dict[str, list[WeeklyVectorObservation]] = {asset: [] for asset in assets}
        for row in rows:
            if row.asset in by_asset:
                by_asset[row.asset].append(row)
        missing_asset = next(
            (asset for asset, asset_rows in by_asset.items() if not asset_rows), None
        )
        if missing_asset is not None:
            excluded[week_start] = f"missing asset {missing_asset}"
            continue

        vector_values: dict[str, float] = {}
        reason: str | None = None
        for vector in vectors:
            finite_values: list[float] = []
            for asset in assets:
                asset_has_value = False
                for row in by_asset[asset]:
                    if vector not in row.values:
                        continue
                    value = row.values[vector]
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    converted = float(value)
                    if isfinite(converted):
                        finite_values.append(converted)
                        asset_has_value = True
                if not asset_has_value:
                    reason = f"missing finite vector {vector} for {asset}"
                    break
            if reason is not None:
                break
            vector_values[vector] = sum(finite_values) / len(finite_values)
        if reason is not None:
            excluded[week_start] = reason
            continue
        weeks.append(
            WeeklyVectorMean(
                week_start=week_start,
                week_end=week_start + timedelta(days=7),
                values=vector_values,
                row_ids=tuple(sorted(row.row_id for row in rows)),
                assets=assets,
            )
        )

    estimates = {
        vector: sum(week.values[vector] for week in weeks) / len(weeks)
        for vector in vectors
        if weeks
    }
    return WeeklyVectorEvidence(
        weeks=tuple(weeks),
        vector_estimates=estimates,
        support=len(weeks),
        excluded_weeks=excluded,
        source_publication_sha256=source_publication_sha256,
    )


def _inconclusive_bootstrap(
    *,
    reason: str,
    support: int,
    seed: int,
    draw_count: int,
    ci_lower_index: int,
    ci_upper_index: int,
) -> BootstrapEvidence:
    return BootstrapEvidence(
        status=ExecutionStatus.COMPLETED,
        decision=ScientificDecision.INCONCLUSIVE,
        reason=reason,
        support=support,
        estimate=None,
        sigma_block=None,
        ci_lower=None,
        ci_upper=None,
        p_value=None,
        required_support=None,
        draw_count=draw_count,
        ci_lower_index=ci_lower_index,
        ci_upper_index=ci_upper_index,
        seed=seed,
        samples_sha256=None,
    )


def _primary_slots_for_family(family: str) -> tuple[str, ...]:
    _require_family(family)
    return tuple(
        slot.slot_id
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == family and slot.kind is ValidationSlotKind.CORE and slot.primary
    )


def _validate_primary_slot(family: str, slot_id: str) -> int:
    slots = _primary_slots_for_family(family)
    if slot_id not in slots:
        raise ValueError("slot_id must be in the frozen primary roster for family")
    return len(slots)


def _bootstrap_index(*, seed: int, draw_index: int, cell_index: int, support: int) -> int:
    digest = hash_json(
        "bootstrap-sample-index-v1",
        {
            "algorithm": "sha-index-bootstrap-v1",
            "seed": seed,
            "draw_index": draw_index,
            "cell_index": cell_index,
            "support": support,
        },
    )
    return int(digest[:16], 16) % support


def bootstrap_weekly_mean(
    *,
    weekly_values: Sequence[float],
    seed: int,
    budget: ValidationWorkBudget,
    mde: float,
    family: str,
    slot_id: str,
) -> BootstrapEvidence:
    """Compute deterministic weekly-block bootstrap evidence or an explicit inconclusive branch."""

    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be ValidationWorkBudget")
    bootstrap_profile = (
        budget.bootstrap_draws,
        budget.max_bootstrap_draws,
        budget.max_bootstrap_cells,
    )
    try:
        ci_lower_index, ci_upper_index = _BOOTSTRAP_POLICIES[bootstrap_profile]
    except KeyError as error:
        raise ValueError("bootstrap budget profile is not a frozen policy") from error
    draw_count = budget.bootstrap_draws
    if (
        isinstance(mde, bool)
        or not isinstance(mde, (int, float))
        or not isfinite(mde)
        or mde <= 0.0
    ):
        return BootstrapEvidence(
            status=ExecutionStatus.FAILED,
            decision=ScientificDecision.NOT_EVALUATED,
            reason="mde must be finite and positive",
            support=len(weekly_values),
            estimate=None,
            sigma_block=None,
            ci_lower=None,
            ci_upper=None,
            p_value=None,
            required_support=None,
            draw_count=draw_count,
            ci_lower_index=ci_lower_index,
            ci_upper_index=ci_upper_index,
            seed=seed,
            samples_sha256=None,
        )
    effect = float(mde)
    m_family = _validate_primary_slot(family, slot_id)
    support = len(weekly_values)
    budget.preflight(
        ValidationWorkDemand(
            bootstrap_draws=draw_count,
            bootstrap_cells=draw_count * support,
            bootstrap_blocks=support,
        )
    )
    if support < 2:
        return _inconclusive_bootstrap(
            reason="fewer than two admissible weeks",
            support=support,
            seed=seed,
            draw_count=draw_count,
            ci_lower_index=ci_lower_index,
            ci_upper_index=ci_upper_index,
        )
    observed_values: list[float] = []
    for value in weekly_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return _inconclusive_bootstrap(
                reason="non-finite weekly value",
                support=support,
                seed=seed,
                draw_count=draw_count,
                ci_lower_index=ci_lower_index,
                ci_upper_index=ci_upper_index,
            )
        observed_values.append(float(value))
    observed = tuple(observed_values)
    sigma = stdev(observed)
    if sigma == 0.0:
        return _inconclusive_bootstrap(
            reason="zero variance weekly means",
            support=support,
            seed=seed,
            draw_count=draw_count,
            ci_lower_index=ci_lower_index,
            ci_upper_index=ci_upper_index,
        )

    alpha_power = _FAMILY_ALPHA / m_family
    normal = NormalDist()
    required_support = ceil(
        ((normal.inv_cdf(1.0 - alpha_power / 2.0) + normal.inv_cdf(0.80)) * sigma / effect) ** 2
    )
    estimate = sum(observed) / support
    samples = tuple(
        sum(
            observed[_bootstrap_index(seed=seed, draw_index=draw, cell_index=cell, support=support)]
            for cell in range(support)
        )
        / support
        for draw in range(draw_count)
    )
    ordered = tuple(sorted(samples))
    ci_lower = ordered[ci_lower_index]
    ci_upper = ordered[ci_upper_index]
    n_le_zero = sum(1 for value in samples if value <= 0.0)
    n_ge_zero = sum(1 for value in samples if value >= 0.0)
    denominator = draw_count + 1
    p_value = min(
        1.0,
        2.0 * min((1 + n_le_zero) / denominator, (1 + n_ge_zero) / denominator),
    )
    samples_sha = hash_json("bootstrap-samples-v1", {"seed": seed, "samples": list(samples)})

    if support < required_support:
        decision = ScientificDecision.INCONCLUSIVE
        reason = "insufficient weekly support for configured MDE"
    elif (ci_upper - ci_lower) > 2.0 * effect:
        decision = ScientificDecision.INCONCLUSIVE
        reason = "confidence interval width exceeds two MDE"
    else:
        decision = ScientificDecision.REJECTED
        reason = "bootstrap evidence completed"
    return BootstrapEvidence(
        status=ExecutionStatus.COMPLETED,
        decision=decision,
        reason=reason,
        support=support,
        estimate=estimate,
        sigma_block=sigma,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        p_value=p_value,
        required_support=required_support,
        draw_count=draw_count,
        ci_lower_index=ci_lower_index,
        ci_upper_index=ci_upper_index,
        seed=seed,
        samples_sha256=samples_sha,
    )


def derive_mde_evidence(
    *, opportunities: Sequence[CostOpportunity], programme_id: str, candidate_id: str, fold_id: str
) -> MdeEvidence:
    """Derive MDE from the maximum finite positive development base round-trip cost."""

    if _PROGRAMME_ID.fullmatch(programme_id) is None:
        raise ValueError("programme_id must be a VP identity")
    _require_non_empty(candidate_id, "candidate_id")
    _require_non_empty(fold_id, "fold_id")
    canonical: list[tuple[str, float, str, str]] = []
    development: list[CostOpportunity] = []
    for opportunity in opportunities:
        if not isinstance(opportunity, CostOpportunity):
            raise TypeError("opportunities must contain CostOpportunity")
        canonical.append(
            (
                opportunity.row_id,
                opportunity.base_round_trip_cost,
                opportunity.component,
                opportunity.source_sha256,
            )
        )
        if opportunity.component == "development":
            development.append(opportunity)
    opportunity_set_sha = hash_json(
        "mde-opportunity-set-v1",
        {
            "programme_id": programme_id,
            "candidate_id": candidate_id,
            "fold_id": fold_id,
            "opportunities": [
                {
                    "row_id": row_id,
                    "base_round_trip_cost": cost,
                    "component": component,
                    "source_sha256": source_sha256,
                }
                for row_id, cost, component, source_sha256 in sorted(canonical)
            ],
        },
    )
    if not development or any(
        not isfinite(item.base_round_trip_cost) or item.base_round_trip_cost <= 0.0
        for item in development
    ):
        return MdeEvidence(
            mde=None,
            tied_row_ids=(),
            opportunity_set_sha256=opportunity_set_sha,
            max_identity_sha256=hash_json(
                "mde-max-v1",
                {"opportunity_set_sha256": opportunity_set_sha, "mde": None, "tied_row_ids": []},
            ),
            support=len(development),
            status=ExecutionStatus.FAILED,
            decision=ScientificDecision.NOT_EVALUATED,
            reason="development costs must be finite and positive",
        )
    max_cost = max(item.base_round_trip_cost for item in development)
    tied = tuple(
        sorted(item.row_id for item in development if item.base_round_trip_cost == max_cost)
    )
    max_identity = hash_json(
        "mde-max-v1",
        {
            "opportunity_set_sha256": opportunity_set_sha,
            "mde": max_cost,
            "tied_row_ids": list(tied),
        },
    )
    return MdeEvidence(
        mde=max_cost,
        tied_row_ids=tied,
        opportunity_set_sha256=opportunity_set_sha,
        max_identity_sha256=max_identity,
        support=len(development),
    )


def holm_family_correction(
    statistics: Sequence[EvaluationStatistic],
    *,
    family: str,
) -> tuple[HolmAdjustedStatistic, ...]:
    """Apply deterministic Holm correction within one fixed-size family."""

    _require_family(family)
    expected = _primary_slots_for_family(family)
    expected_count = len(expected)
    if expected_count != _EXPECTED_PRIMARY_COUNTS[family]:
        raise RuntimeError("frozen primary roster count does not match family policy")
    if len(statistics) != expected_count:
        raise ValueError(f"family {family} must contain exactly {expected_count} statistics")
    by_slot: dict[str, EvaluationStatistic] = {}
    for statistic in statistics:
        if not isinstance(statistic, EvaluationStatistic):
            raise TypeError("statistics must contain EvaluationStatistic")
        if statistic.family != family:
            raise ValueError("statistic family does not match correction family")
        if statistic.slot_id in by_slot:
            raise ValueError("duplicate statistic slot_id")
        by_slot[statistic.slot_id] = statistic
    if set(by_slot) != set(expected):
        raise ValueError("statistics must exactly cover the frozen primary roster")

    effective_by_slot = {slot_id: _effective_p_value(by_slot[slot_id]) for slot_id in expected}
    ordered = sorted(
        ((slot_id, effective_by_slot[slot_id]) for slot_id in expected),
        key=lambda item: (item[1], item[0]),
    )
    adjusted_by_slot: dict[str, float] = {}
    running = 0.0
    m = expected_count
    for rank, (slot_id, p_value) in enumerate(ordered, start=1):
        adjusted = min(1.0, (m - rank + 1) * p_value)
        running = max(running, adjusted)
        adjusted_by_slot[slot_id] = running

    return tuple(
        HolmAdjustedStatistic(
            slot_id=slot_id,
            family=family,
            raw_p_value=by_slot[slot_id].p_value,
            effective_p_value=effective_by_slot[slot_id],
            adjusted_p_value=adjusted_by_slot[slot_id],
            rejected=adjusted_by_slot[slot_id] <= _FAMILY_ALPHA,
            status=by_slot[slot_id].status,
            decision=by_slot[slot_id].decision,
        )
        for slot_id in expected
    )


def _effective_p_value(statistic: EvaluationStatistic) -> float:
    if not statistic.evaluable or statistic.p_value is None:
        return 1.0
    return statistic.p_value


def classify_family_decision(evidence: FamilyDecisionInput) -> FamilyDecisionEvidence:
    """Classify development evidence with fixed intersection-union and non-final gates."""

    if not isinstance(evidence, FamilyDecisionInput):
        raise TypeError("evidence must be FamilyDecisionInput")
    if not evidence.policy_valid:
        return FamilyDecisionEvidence(
            input=evidence,
            terminal_state=ValidationTerminalState(
                ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
            ),
            reason="missing or invalid policy",
            worst_p_value=None,
            worst_lower_bound=None,
            participating_roles=(),
        )
    required_roles = _REQUIRED_CONTROLS[evidence.family]
    by_role = {control.role: control for control in evidence.controls}
    if tuple(sorted(by_role)) != tuple(sorted(required_roles)) or len(by_role) != len(
        evidence.controls
    ):
        return _inconclusive_family(evidence, "missing required control evidence")
    controls = tuple(by_role[role] for role in required_roles)
    if evidence.diagnostic_only or any(
        control.inference_role is FamilyInferenceRole.DIAGNOSTIC for control in controls
    ):
        return _rejected_family(evidence, controls, "diagnostic controls cannot promote")
    if (
        not evidence.cost_coverage_complete
        or not evidence.support_sufficient
        or not evidence.interval_width_ok
        or any(not control.support_sufficient for control in controls)
    ):
        return _inconclusive_family(
            evidence, "insufficient support, cost coverage, or interval width"
        )
    worst_p = max(control.corrected_p_value for control in controls)
    worst_lower = min(control.corrected_lower_bound for control in controls)
    if worst_lower <= 0.0:
        return _rejected_family(
            evidence, controls, "corrected supported lower bound is not positive"
        )
    if worst_p > _FAMILY_ALPHA:
        return _rejected_family(evidence, controls, "corrected p-value fails family alpha")
    if not evidence.robustness_gate or not evidence.exposure_gate:
        return _inconclusive_family(evidence, "non-final robustness or exposure gate is incomplete")
    return FamilyDecisionEvidence(
        input=evidence,
        terminal_state=ValidationTerminalState(
            ExecutionStatus.COMPLETED, ScientificDecision.SUPPORTED_DEVELOPMENT
        ),
        reason="all non-final gates pass",
        worst_p_value=worst_p,
        worst_lower_bound=worst_lower,
        participating_roles=tuple(control.role for control in controls),
    )


def _inconclusive_family(evidence: FamilyDecisionInput, reason: str) -> FamilyDecisionEvidence:
    return FamilyDecisionEvidence(
        input=evidence,
        terminal_state=ValidationTerminalState(
            ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
        ),
        reason=reason,
        worst_p_value=None,
        worst_lower_bound=None,
        participating_roles=tuple(control.role for control in evidence.controls),
    )


def _rejected_family(
    evidence: FamilyDecisionInput, controls: Sequence[ControlStatistic], reason: str
) -> FamilyDecisionEvidence:
    return FamilyDecisionEvidence(
        input=evidence,
        terminal_state=ValidationTerminalState(
            ExecutionStatus.COMPLETED, ScientificDecision.REJECTED
        ),
        reason=reason,
        worst_p_value=max(control.corrected_p_value for control in controls),
        worst_lower_bound=min(control.corrected_lower_bound for control in controls),
        participating_roles=tuple(control.role for control in controls),
    )


__all__ = [
    "BootstrapEvidence",
    "ControlStatistic",
    "CostOpportunity",
    "EvaluationStatistic",
    "FamilyDecisionEvidence",
    "FamilyDecisionInput",
    "FamilyInferenceRole",
    "HolmAdjustedStatistic",
    "MdeEvidence",
    "WeeklyVectorEvidence",
    "WeeklyVectorMean",
    "WeeklyVectorObservation",
    "aggregate_weekly_vectors",
    "bootstrap_weekly_mean",
    "classify_family_decision",
    "derive_mde_evidence",
    "deterministic_validation_seed",
    "holm_family_correction",
]
