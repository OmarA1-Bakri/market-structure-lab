"""Registered synthetic-oracle preterminal inference for Phase 5 V2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from types import MappingProxyType
from typing import Any, cast
from weakref import ReferenceType, ref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.controls import (
    ControlKind,
    ControlOpportunity,
    ControlRecord,
    ControlSelection,
    ControlSelectionStatus,
    build_naive_zero_controls,
    build_persistence_controls,
    select_stratified_controls,
)
from market_structure_lab.research.costs import (
    CostAdmissionStatus,
    CostApplication,
    CostCoverageStatus,
    CostEventCoverage,
    CostEventCoveragePublication,
    CostEvidence,
    CostPolicy,
    CostPolicyAssessment,
    CostSide,
    CostRate,
    FundingPolicy,
    TradeCostResult,
    apply_cost_policy,
    assess_cost_application,
    assess_cost_policy,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.statistics import (
    BootstrapEvidence,
    CostOpportunity,
    MdeEvidence,
    WeeklyVectorEvidence,
    WeeklyVectorMean,
    WeeklyVectorObservation,
    aggregate_weekly_vectors,
    bootstrap_weekly_mean,
    derive_mde_evidence,
    deterministic_validation_seed,
)

_AUTHORITY_SEAL = object()
_EVALUATION_SEAL = object()
_SHA256_LENGTH = 64
_MAX_PRIMARY_ROWS = 2_000_000
_CONTRAST_ROLES = ("naive", "unconditional", "persistence")
_SYNTHETIC_DEVELOPMENT_SCOPE = "registered_synthetic_oracle_development"
_MAPPING_PROXY_TYPE: type[object] = type(MappingProxyType({}))


def _require_non_empty(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value.astimezone(UTC)


def _require_price(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    converted = float(value)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted


@dataclass(frozen=True, slots=True)
class RawPrimaryOpportunityRowV2:
    """One legal, publication-bound opportunity before cost and control computation."""

    row_identity: str
    event_id: str
    candidate_id: str
    family: str
    detector_role: str
    detector_parameters: tuple[tuple[str, str], ...]
    control_role: str
    symbol: str
    timeframe: str
    fold_id: str
    utc_week_start: datetime
    direction: int
    horizon_hours: int
    segment_id: int
    feature_time: datetime
    signal_time: datetime
    legal_entry_time: datetime
    label_start: datetime
    label_end: datetime
    programme_id: str
    publication_sha256: str
    component: str
    block_id: str
    venue: str
    entry_price: float
    exit_price: float
    delayed_entry_price: float
    delayed_exit_price: float
    delayed_entry_time: datetime
    delayed_exit_time: datetime
    delay_evidence_sha256: str
    prior_completed_close_return: float | None = None

    def __post_init__(self) -> None:
        for label in (
            "row_identity",
            "event_id",
            "candidate_id",
            "family",
            "detector_role",
            "control_role",
            "symbol",
            "timeframe",
            "fold_id",
            "programme_id",
            "component",
            "block_id",
            "venue",
        ):
            _require_non_empty(getattr(self, label), label)
        if self.family != "A" or self.candidate_id != "HC-A-001":
            raise ValueError("raw primary row must be the canonical A candidate")
        if type(self.detector_parameters) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(value) is not str or value == "" for value in item)
            for item in self.detector_parameters
        ):
            raise TypeError("detector_parameters must be exact non-empty string pairs")
        if self.control_role not in {"candidate", "unconditional", "persistence"}:
            raise ValueError("control_role is unsupported for A/VS-0001")
        if self.direction not in {-1, 1}:
            raise ValueError("direction must be -1 or +1")
        if (
            isinstance(self.horizon_hours, bool)
            or not isinstance(self.horizon_hours, int)
            or self.horizon_hours < 1
        ):
            raise ValueError("horizon_hours must be a positive integer")
        if (
            isinstance(self.segment_id, bool)
            or not isinstance(self.segment_id, int)
            or self.segment_id < 0
        ):
            raise ValueError("segment_id must be a non-negative integer")
        for label in (
            "utc_week_start",
            "feature_time",
            "signal_time",
            "legal_entry_time",
            "label_start",
            "label_end",
            "delayed_entry_time",
            "delayed_exit_time",
        ):
            object.__setattr__(self, label, _require_utc(getattr(self, label), label))
        if self.utc_week_start.weekday() != 0 or self.utc_week_start.hour != 0:
            raise ValueError("utc_week_start must be Monday 00:00 UTC")
        label_week_start = datetime(
            self.label_start.year,
            self.label_start.month,
            self.label_start.day,
            tzinfo=UTC,
        ) - timedelta(days=self.label_start.weekday())
        if self.utc_week_start != label_week_start:
            raise ValueError("utc_week_start must match the label's UTC calendar week")
        if not (
            self.feature_time
            < self.signal_time
            <= self.legal_entry_time
            == self.label_start
            < self.label_end
        ):
            raise ValueError("opportunity timestamps must form a causal half-open path")
        if self.label_end - self.label_start != timedelta(hours=self.horizon_hours):
            raise ValueError("label interval must match horizon_hours")
        if self.delayed_entry_time - self.legal_entry_time != timedelta(hours=1):
            raise ValueError("VS-0001 delayed entry must be exactly one 1h bar later")
        if self.delayed_exit_time - self.delayed_entry_time != timedelta(hours=self.horizon_hours):
            raise ValueError("delayed label interval must preserve the frozen horizon")
        _require_sha256(self.publication_sha256, "publication_sha256")
        object.__setattr__(self, "entry_price", _require_price(self.entry_price, "entry_price"))
        object.__setattr__(self, "exit_price", _require_price(self.exit_price, "exit_price"))
        object.__setattr__(
            self,
            "delayed_entry_price",
            _require_price(self.delayed_entry_price, "delayed_entry_price"),
        )
        object.__setattr__(
            self,
            "delayed_exit_price",
            _require_price(self.delayed_exit_price, "delayed_exit_price"),
        )
        _require_sha256(self.delay_evidence_sha256, "delay_evidence_sha256")
        if self.prior_completed_close_return is not None:
            if (
                isinstance(self.prior_completed_close_return, bool)
                or not isinstance(self.prior_completed_close_return, (int, float))
                or not isfinite(self.prior_completed_close_return)
            ):
                raise ValueError("prior_completed_close_return must be finite")

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class RegisteredSyntheticOpportunityAuthorityV2:
    """Factory-issued authority for registered synthetic-oracle development evidence.

    This adapter is intentionally incompatible with the production runner and receipt
    publisher. Caller-provided rows can exercise the frozen primitives but cannot become
    verifier-rooted source-publication authority.
    """

    slot_id: str
    programme_id: str
    candidate_id: str
    source_publication_sha256: str
    fold_id: str
    expected_assets: tuple[str, ...]
    fold_start: datetime
    fold_end: datetime
    rows: tuple[RawPrimaryOpportunityRowV2, ...]
    cost_policy: CostPolicy
    budget: ValidationWorkBudget
    authority_scope: str = field(init=False, default=_SYNTHETIC_DEVELOPMENT_SCOPE)
    production_eligible: bool = field(init=False, default=False)
    sha256: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _AUTHORITY_SEAL:
            raise TypeError("RegisteredSyntheticOpportunityAuthorityV2 requires its factory")
        _validate_authority_shape(self)
        object.__setattr__(self, "sha256", _authority_content_sha(self))


def _validate_authority_shape(authority: RegisteredSyntheticOpportunityAuthorityV2) -> None:
    for label in ("slot_id", "programme_id", "candidate_id", "fold_id"):
        _require_non_empty(getattr(authority, label), label)
    _require_sha256(authority.source_publication_sha256, "source_publication_sha256")
    _require_utc(authority.fold_start, "fold_start")
    _require_utc(authority.fold_end, "fold_end")
    if type(authority.rows) is not tuple or any(
        type(row) is not RawPrimaryOpportunityRowV2 for row in authority.rows
    ):
        raise TypeError("authority rows must be an exact tuple of raw rows")
    if type(authority.expected_assets) is not tuple or any(
        type(asset) is not str or asset == "" for asset in authority.expected_assets
    ):
        raise TypeError("expected_assets must be an exact tuple of non-empty strings")
    if not authority.expected_assets or len(set(authority.expected_assets)) != len(
        authority.expected_assets
    ):
        raise ValueError("expected_assets must be non-empty and unique")
    if type(authority.cost_policy) is not CostPolicy:
        raise TypeError("authority cost_policy must be the exact CostPolicy type")
    _validate_cost_policy_shape(authority.cost_policy)
    if type(authority.budget) is not ValidationWorkBudget:
        raise TypeError("authority budget must be the exact ValidationWorkBudget type")
    if authority.authority_scope != _SYNTHETIC_DEVELOPMENT_SCOPE:
        raise ValueError("authority scope must remain registered synthetic-oracle development")
    if authority.production_eligible is not False:
        raise ValueError("synthetic authority must never be production eligible")


def _validate_cost_policy_shape(policy: CostPolicy) -> CostPolicy:
    if type(policy.evidence) is not CostEvidence:
        raise TypeError("cost policy evidence must be the exact CostEvidence type")
    if type(policy.funding) is not FundingPolicy:
        raise TypeError("cost policy funding must be the exact FundingPolicy type")
    evidence = policy.evidence
    replayed_rates: list[CostRate] = []
    for rate in (
        evidence.entry_fee,
        evidence.entry_half_spread,
        evidence.entry_slippage,
        evidence.exit_fee,
        evidence.exit_half_spread,
        evidence.exit_slippage,
    ):
        if type(rate) is not CostRate:
            raise TypeError("cost evidence rates must be exact CostRate objects")
        replayed_rates.append(
            CostRate(
                rate=rate.rate,
                units=rate.units,
                provenance=rate.provenance,
                evidence_sha256=rate.evidence_sha256,
            )
        )
    if type(evidence.coverage_status) is not CostCoverageStatus:
        raise TypeError("cost evidence coverage_status must be exact CostCoverageStatus")
    publication = evidence.event_coverage_publication
    replayed_publication: CostEventCoveragePublication | None = None
    if publication is not None:
        if type(publication) is not CostEventCoveragePublication:
            raise TypeError("event coverage publication must be the exact type")
        if type(publication.entries) is not tuple or any(
            type(entry) is not CostEventCoverage for entry in publication.entries
        ):
            raise TypeError("event coverage entries must be an exact tuple of coverage rows")
        replayed_entries = tuple(
            CostEventCoverage(
                event_id=entry.event_id,
                venue=entry.venue,
                symbol=entry.symbol,
                timeframe=entry.timeframe,
                effective_start=entry.effective_start,
                effective_end=entry.effective_end,
                evidence_sha256=entry.evidence_sha256,
            )
            for entry in publication.entries
        )
        replayed_publication = CostEventCoveragePublication(
            source_publication_sha256=publication.source_publication_sha256,
            coverage_count=publication.coverage_count,
            coverage_digest_sha256=publication.coverage_digest_sha256,
            entries=replayed_entries,
        )
    replayed_evidence = CostEvidence(
        evidence_id=evidence.evidence_id,
        symbol=evidence.symbol,
        timeframe=evidence.timeframe,
        source_publication_sha256=evidence.source_publication_sha256,
        entry_fee=replayed_rates[0],
        entry_half_spread=replayed_rates[1],
        entry_slippage=replayed_rates[2],
        exit_fee=replayed_rates[3],
        exit_half_spread=replayed_rates[4],
        exit_slippage=replayed_rates[5],
        fill_probability=evidence.fill_probability,
        coverage_status=evidence.coverage_status,
        coverage_reason=evidence.coverage_reason,
        event_coverage_publication=replayed_publication,
    )
    return CostPolicy(
        policy_id=policy.policy_id,
        base_policy_sha256=policy.base_policy_sha256,
        evidence=replayed_evidence,
        funding=policy.funding,
        funding_rows_sha256=policy.funding_rows_sha256,
        stress_multiplier=policy.stress_multiplier,
        stress_fill_probability_power=policy.stress_fill_probability_power,
        delay_model_sha256=policy.delay_model_sha256,
        max_applications=policy.max_applications,
    )


def _authority_content_sha(authority: RegisteredSyntheticOpportunityAuthorityV2) -> str:
    return hash_json(
        "registered-synthetic-primary-opportunity-authority-v2",
        {
            "slot_id": authority.slot_id,
            "programme_id": authority.programme_id,
            "candidate_id": authority.candidate_id,
            "source_publication_sha256": authority.source_publication_sha256,
            "fold_id": authority.fold_id,
            "expected_assets": list(authority.expected_assets),
            "fold_start": authority.fold_start,
            "fold_end": authority.fold_end,
            "rows": [row.to_dict() for row in authority.rows],
            "cost_policy_sha256": authority.cost_policy.sha256,
            "budget_sha256": authority.budget.sha256,
            "authority_scope": authority.authority_scope,
            "production_eligible": authority.production_eligible,
        },
    )


@dataclass(frozen=True, slots=True)
class PrimaryContrastEvidenceV2:
    """Exact weekly-block bootstrap evidence for one declared primary contrast."""

    role: str
    weekly_values: tuple[float, ...]
    bootstrap: BootstrapEvidence


@dataclass(frozen=True, slots=True)
class CostScenarioEvidenceV2:
    """Exact base, doubled-cost, and declared one-bar-delay results for one row."""

    row_identity: str
    base: TradeCostResult
    stress: TradeCostResult
    delayed: TradeCostResult


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SyntheticPrimaryContrastEvaluationV2:
    """Registered synthetic-development result; never a production receipt authority."""

    authority_sha256: str
    cost_policy_assessment: CostPolicyAssessment
    cost_scenarios: tuple[CostScenarioEvidenceV2, ...]
    naive_selection: ControlSelection
    unconditional_selection: ControlSelection
    persistence_selection: ControlSelection
    weekly_vectors: WeeklyVectorEvidence
    mde_evidence: MdeEvidence
    contrasts: tuple[PrimaryContrastEvidenceV2, ...]
    iut_p_value: float | None
    iut_lower_bound: float | None
    inference_evaluable: bool
    inference_state: str
    inference_reason: str
    holm_effective_p_value: float
    final_holdout_access_count: int
    sha256: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _EVALUATION_SEAL:
            raise TypeError("SyntheticPrimaryContrastEvaluationV2 requires its evaluator")
        _validate_evaluation_shape(self)
        object.__setattr__(self, "sha256", _evaluation_content_sha(self))


def _dataclass_payload(value: object) -> dict[str, object]:
    fields = getattr(type(value), "__dataclass_fields__", None)
    if not isinstance(fields, dict):
        raise TypeError("evidence payload must be a dataclass")
    return {name: getattr(value, name) for name in fields}


def _validate_evaluation_shape(evaluation: SyntheticPrimaryContrastEvaluationV2) -> None:
    _require_sha256(evaluation.authority_sha256, "authority_sha256")
    if type(evaluation.cost_policy_assessment) is not CostPolicyAssessment:
        raise TypeError("cost_policy_assessment must be the exact type")
    CostPolicyAssessment(**cast(Any, _dataclass_payload(evaluation.cost_policy_assessment)))
    if type(evaluation.cost_scenarios) is not tuple:
        raise TypeError("cost_scenarios must be an exact tuple")
    for scenario in evaluation.cost_scenarios:
        if type(scenario) is not CostScenarioEvidenceV2:
            raise TypeError("cost_scenarios must contain exact scenario evidence")
        _require_non_empty(scenario.row_identity, "row_identity")
        if any(
            type(item) is not TradeCostResult
            for item in (scenario.base, scenario.stress, scenario.delayed)
        ):
            raise TypeError("cost scenario results must be exact TradeCostResult objects")
        for result in (scenario.base, scenario.stress, scenario.delayed):
            TradeCostResult(**cast(Any, _dataclass_payload(result)))
    for selection in (
        evaluation.naive_selection,
        evaluation.unconditional_selection,
        evaluation.persistence_selection,
    ):
        if type(selection) is not ControlSelection:
            raise TypeError("control selections must be exact ControlSelection objects")
        if type(selection.kind) is not ControlKind:
            raise TypeError("control selection kind must be exact ControlKind")
        if type(selection.status) is not ControlSelectionStatus:
            raise TypeError("control selection status must be exact ControlSelectionStatus")
        _require_non_empty(selection.reason, "control selection reason")
        if type(selection.shortage_count) is not int or selection.shortage_count < 0:
            raise ValueError("control selection shortage_count must be non-negative")
        if type(selection.records) is not tuple or any(
            type(record) is not ControlRecord for record in selection.records
        ):
            raise TypeError("control records must be an exact tuple of ControlRecord objects")
        for record in selection.records:
            ControlRecord.from_dict(record.to_dict())
        ControlSelection.from_dict(selection.to_dict())
    if type(evaluation.weekly_vectors) is not WeeklyVectorEvidence:
        raise TypeError("weekly_vectors must be the exact WeeklyVectorEvidence type")
    _validate_weekly_vector_evidence(evaluation.weekly_vectors)
    if type(evaluation.mde_evidence) is not MdeEvidence:
        raise TypeError("mde_evidence must be the exact MdeEvidence type")
    if type(evaluation.mde_evidence.tied_row_ids) is not tuple or any(
        type(row_id) is not str for row_id in evaluation.mde_evidence.tied_row_ids
    ):
        raise TypeError("MDE tied_row_ids must be an exact string tuple")
    MdeEvidence(**cast(Any, _dataclass_payload(evaluation.mde_evidence)))
    if type(evaluation.contrasts) is not tuple:
        raise TypeError("contrasts must be an exact tuple")
    for contrast in evaluation.contrasts:
        if type(contrast) is not PrimaryContrastEvidenceV2:
            raise TypeError("contrasts must contain exact primary contrast evidence")
        _require_non_empty(contrast.role, "contrast role")
        if type(contrast.weekly_values) is not tuple or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in contrast.weekly_values
        ):
            raise TypeError("contrast weekly_values must be an exact finite tuple")
        if type(contrast.bootstrap) is not BootstrapEvidence:
            raise TypeError("contrast bootstrap must be exact BootstrapEvidence")
        BootstrapEvidence(**cast(Any, _dataclass_payload(contrast.bootstrap)))
    for label in ("iut_p_value", "iut_lower_bound"):
        value = getattr(evaluation, label)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
        ):
            raise ValueError(f"{label} must be finite or None")
    if type(evaluation.inference_evaluable) is not bool:
        raise TypeError("inference_evaluable must be bool")
    if evaluation.inference_state not in {
        "failed/not_evaluated",
        "completed/inconclusive",
        "completed/evaluable_preterminal",
    }:
        raise ValueError("inference_state is unsupported")
    _require_non_empty(evaluation.inference_reason, "inference_reason")
    if (
        isinstance(evaluation.holm_effective_p_value, bool)
        or not isinstance(evaluation.holm_effective_p_value, (int, float))
        or not isfinite(evaluation.holm_effective_p_value)
        or not 0.0 <= evaluation.holm_effective_p_value <= 1.0
    ):
        raise ValueError("holm_effective_p_value must be in [0, 1]")
    if evaluation.final_holdout_access_count != 0:
        raise ValueError("synthetic development evaluation cannot access the final holdout")


def _require_exact_finite_mapping(value: object, label: str) -> Mapping[object, object]:
    if type(value) not in {dict, _MAPPING_PROXY_TYPE}:
        raise TypeError(f"{label} must be an exact dict or mapping proxy")
    mapping = cast(Mapping[object, object], value)
    if any(
        type(key) is not str
        or isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not isfinite(item)
        for key, item in mapping.items()
    ):
        raise ValueError(f"{label} must contain finite numeric string-keyed values")
    return mapping


def _validate_weekly_vector_evidence(evidence: WeeklyVectorEvidence) -> None:
    if type(evidence.weeks) is not tuple or any(
        type(week) is not WeeklyVectorMean for week in evidence.weeks
    ):
        raise TypeError("weekly evidence weeks must be an exact tuple of weekly means")
    for week in evidence.weeks:
        start = _require_utc(week.week_start, "week_start")
        end = _require_utc(week.week_end, "week_end")
        if end <= start:
            raise ValueError("weekly evidence range must be positive")
        _require_exact_finite_mapping(week.values, "weekly values")
        if type(week.row_ids) is not tuple or any(type(value) is not str for value in week.row_ids):
            raise TypeError("weekly row_ids must be an exact string tuple")
        if type(week.assets) is not tuple or any(type(value) is not str for value in week.assets):
            raise TypeError("weekly assets must be an exact string tuple")
    _require_exact_finite_mapping(evidence.vector_estimates, "weekly vector estimates")
    if type(evidence.excluded_weeks) not in {dict, _MAPPING_PROXY_TYPE}:
        raise TypeError("excluded_weeks must be an exact dict or mapping proxy")
    if any(
        type(timestamp) is not datetime or type(reason) is not str or reason == ""
        for timestamp, reason in evidence.excluded_weeks.items()
    ):
        raise ValueError("excluded_weeks must map datetimes to non-empty reasons")
    if type(evidence.support) is not int or evidence.support != len(evidence.weeks):
        raise ValueError("weekly support must equal the exact week count")
    _require_sha256(evidence.source_publication_sha256, "weekly source publication")


def _evaluation_content_sha(evaluation: SyntheticPrimaryContrastEvaluationV2) -> str:
    return hash_json(
        "synthetic-primary-contrast-evaluation-v2",
        {
            "authority_sha256": evaluation.authority_sha256,
            "cost_policy_assessment": _dataclass_payload(evaluation.cost_policy_assessment),
            "cost_scenarios": [
                {
                    "row_identity": item.row_identity,
                    "base": _trade_cost_payload(item.base),
                    "stress": _trade_cost_payload(item.stress),
                    "delayed": _trade_cost_payload(item.delayed),
                }
                for item in evaluation.cost_scenarios
            ],
            "selections": [
                evaluation.naive_selection.to_dict(),
                evaluation.unconditional_selection.to_dict(),
                evaluation.persistence_selection.to_dict(),
            ],
            "weekly_vectors_sha256": evaluation.weekly_vectors.sha256,
            "mde_evidence": _dataclass_payload(evaluation.mde_evidence),
            "contrasts": [
                {
                    "role": contrast.role,
                    "weekly_values": list(contrast.weekly_values),
                    "bootstrap": _dataclass_payload(contrast.bootstrap),
                }
                for contrast in evaluation.contrasts
            ],
            "iut_p_value": evaluation.iut_p_value,
            "iut_lower_bound": evaluation.iut_lower_bound,
            "inference_evaluable": evaluation.inference_evaluable,
            "inference_state": evaluation.inference_state,
            "inference_reason": evaluation.inference_reason,
            "holm_effective_p_value": evaluation.holm_effective_p_value,
            "final_holdout_access_count": evaluation.final_holdout_access_count,
        },
    )


_AuthorityRegistration = tuple[
    ReferenceType[RegisteredSyntheticOpportunityAuthorityV2],
    str,
    tuple[RawPrimaryOpportunityRowV2, ...],
    tuple[str, ...],
    CostPolicy,
    Mapping[str, object],
    ValidationWorkBudget,
    Mapping[str, int],
]
_VERIFIED_AUTHORITIES: dict[int, _AuthorityRegistration] = {}
_EvaluationRegistration = tuple[
    ReferenceType[SyntheticPrimaryContrastEvaluationV2],
    str,
    RegisteredSyntheticOpportunityAuthorityV2,
]
_VERIFIED_EVALUATIONS: dict[int, _EvaluationRegistration] = {}


def register_synthetic_primary_opportunity_authority_v2(
    rows: Sequence[RawPrimaryOpportunityRowV2],
    *,
    cost_policy: CostPolicy,
    slot_id: str,
    expected_assets: tuple[str, ...],
    fold_start: datetime,
    fold_end: datetime,
    budget: ValidationWorkBudget,
) -> RegisteredSyntheticOpportunityAuthorityV2:
    """Admit and seal a bounded development population before any scientific work."""

    if type(rows) not in {list, tuple}:
        raise TypeError("rows must be an exact bounded list or tuple")
    declared_count = len(rows)
    if declared_count > _MAX_PRIMARY_ROWS:
        raise ValueError("row count exceeds the V2 inference hard ceiling")
    if type(budget) is not ValidationWorkBudget:
        raise TypeError("budget must be the exact ValidationWorkBudget type")
    budget = ValidationWorkBudget(**budget.to_dict())
    budget.preflight(
        ValidationWorkDemand(events=declared_count, outcomes=declared_count),
        deferred_work=rows,
    )
    start = _require_utc(fold_start, "fold_start")
    end = _require_utc(fold_end, "fold_end")
    if start >= end:
        raise ValueError("fold_start must precede fold_end")
    if slot_id != "VS-0001":
        raise ValueError("this adapter is restricted to canonical slot VS-0001")
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == slot_id)
    if type(expected_assets) is not tuple or any(
        type(asset) is not str or asset == "" for asset in expected_assets
    ):
        raise TypeError("expected_assets must be an exact tuple of non-empty strings")
    if not expected_assets or len(set(expected_assets)) != len(expected_assets):
        raise ValueError("expected_assets must be non-empty and unique")

    materialized = tuple(rows)
    if len(materialized) != declared_count:
        raise ValueError("row collection length changed during materialisation")
    if not materialized:
        raise ValueError("primary opportunity population must be non-empty")
    if any(type(row) is not RawPrimaryOpportunityRowV2 for row in materialized):
        raise TypeError("rows must contain exact RawPrimaryOpportunityRowV2 objects")
    materialized = tuple(replace(row) for row in materialized)
    first = materialized[0]
    if any(row.component != "development" for row in materialized):
        raise ValueError("primary opportunity authority is development-only")
    if type(cost_policy) is not CostPolicy:
        raise TypeError("cost_policy must be the exact CostPolicy type")
    cost_policy = _validate_cost_policy_shape(cost_policy)
    assessment = assess_cost_policy(cost_policy)
    if assessment.admission_status is not CostAdmissionStatus.ADMISSIBLE:
        raise ValueError(f"cost policy is not admissible: {assessment.reason}")
    cost_policy.require_application_budget(3 * declared_count)
    if any(row.programme_id != first.programme_id for row in materialized):
        raise ValueError("rows must share one programme_id")
    if any(row.candidate_id != first.candidate_id for row in materialized):
        raise ValueError("rows must share one candidate_id")
    if any(row.fold_id != first.fold_id for row in materialized):
        raise ValueError("rows must share one inner-training fold_id")
    if any(
        row.family != slot.family
        or row.detector_role != slot.role
        or row.detector_parameters != slot.parameters
        or row.timeframe != slot.timeframe
        or row.horizon_hours != slot.horizon_hours
        or ("long" if row.direction == 1 else "short") != slot.direction
        for row in materialized
    ):
        raise ValueError("rows do not match the frozen VS-0001 roster axes")
    if any(row.publication_sha256 != first.publication_sha256 for row in materialized):
        raise ValueError("rows must share one source publication")
    if any(
        row.label_start < start or row.label_end > end or row.delayed_exit_time > end
        for row in materialized
    ):
        raise ValueError("rows must remain inside the development fold")
    if not {row.symbol for row in materialized}.issubset(set(expected_assets)):
        raise ValueError("row symbols must belong to the frozen expected asset universe")
    if cost_policy.evidence.source_publication_sha256 != first.publication_sha256:
        raise ValueError("cost policy is not bound to the opportunity publication")
    if cost_policy.delay_model_sha256 is None:
        raise ValueError("cost policy must declare the frozen delay model")
    if any(row.delay_evidence_sha256 != cost_policy.delay_model_sha256 for row in materialized):
        raise ValueError("row delay evidence is not bound to the cost policy delay model")
    for row in materialized:
        for timestamp in (row.label_start, row.delayed_entry_time):
            event_assessment = assess_cost_application(
                cost_policy,
                event_id=row.event_id,
                event_timestamp=timestamp,
                venue=row.venue,
                symbol=row.symbol,
                timeframe=row.timeframe,
            )
            if event_assessment.admission_status is not CostAdmissionStatus.ADMISSIBLE:
                raise ValueError("synthetic oracle authority requires complete event cost coverage")
    identities = tuple(row.row_identity for row in materialized)
    events = tuple(row.event_id for row in materialized)
    if len(set(identities)) != len(identities) or len(set(events)) != len(events):
        raise ValueError("row and event identities must be unique")
    if not any(row.control_role == "candidate" for row in materialized):
        raise ValueError("candidate opportunities are required")

    authority = RegisteredSyntheticOpportunityAuthorityV2(
        slot_id=slot_id,
        programme_id=first.programme_id,
        candidate_id=first.candidate_id,
        source_publication_sha256=first.publication_sha256,
        fold_id=first.fold_id,
        expected_assets=expected_assets,
        fold_start=start,
        fold_end=end,
        rows=materialized,
        cost_policy=cost_policy,
        budget=budget,
        seal=_AUTHORITY_SEAL,
    )
    identifier = id(authority)

    def cleanup(reference: ReferenceType[RegisteredSyntheticOpportunityAuthorityV2]) -> None:
        current = _VERIFIED_AUTHORITIES.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_AUTHORITIES.pop(identifier, None)

    _VERIFIED_AUTHORITIES[identifier] = (
        ref(authority, cleanup),
        authority.sha256,
        materialized,
        tuple(hash_json("raw-primary-opportunity-row-v2", row.to_dict()) for row in materialized),
        cost_policy,
        MappingProxyType(cost_policy.to_dict()),
        budget,
        MappingProxyType(budget.to_dict()),
    )
    return authority


def verify_registered_synthetic_opportunity_authority_v2(
    authority: RegisteredSyntheticOpportunityAuthorityV2,
) -> None:
    """Revalidate exact object identity, retained rows, cost capability, and budget."""

    if type(authority) is not RegisteredSyntheticOpportunityAuthorityV2:
        raise TypeError("authority must be the exact factory-issued type")
    registered = _VERIFIED_AUTHORITIES.get(id(authority))
    if registered is None or registered[0]() is not authority:
        raise ValueError("authority is not the registered original")
    _validate_authority_shape(authority)
    if authority.sha256 != registered[1] or authority.rows is not registered[2]:
        raise ValueError("authority differs from its registered original")
    replayed_rows = tuple(replace(row) for row in authority.rows)
    current_rows = tuple(
        hash_json("raw-primary-opportunity-row-v2", row.to_dict()) for row in replayed_rows
    )
    if current_rows != registered[3]:
        raise ValueError("authority rows changed from their registered originals")
    if authority.cost_policy is not registered[4]:
        raise ValueError("authority replaced its exact cost policy capability")
    if authority.cost_policy.to_dict() != dict(registered[5]):
        raise ValueError("authority cost policy changed from its registered original")
    if authority.budget is not registered[6] or authority.budget.to_dict() != dict(registered[7]):
        raise ValueError("authority budget changed from its registered original")
    if any(row.component != "development" for row in authority.rows):
        raise ValueError("authority scope changed from development-only")
    if _authority_content_sha(authority) != registered[1]:
        raise ValueError("authority content changed from its registered original")


def _snapshot_verified_authority_v2(
    authority: RegisteredSyntheticOpportunityAuthorityV2,
) -> RegisteredSyntheticOpportunityAuthorityV2:
    """Copy a verified authority into operation-owned immutable inputs."""

    verify_registered_synthetic_opportunity_authority_v2(authority)
    registered = _VERIFIED_AUTHORITIES[id(authority)]
    snapshot = RegisteredSyntheticOpportunityAuthorityV2(
        slot_id=authority.slot_id,
        programme_id=authority.programme_id,
        candidate_id=authority.candidate_id,
        source_publication_sha256=authority.source_publication_sha256,
        fold_id=authority.fold_id,
        expected_assets=authority.expected_assets,
        fold_start=authority.fold_start,
        fold_end=authority.fold_end,
        rows=tuple(replace(row) for row in authority.rows),
        cost_policy=_validate_cost_policy_shape(authority.cost_policy),
        budget=ValidationWorkBudget(**authority.budget.to_dict()),
        seal=_AUTHORITY_SEAL,
    )
    if snapshot.sha256 != registered[1]:
        raise ValueError("authority changed while taking the operation snapshot")
    return snapshot


def _trade_cost_payload(result: TradeCostResult) -> dict[str, object]:
    return {
        name: getattr(result, name).value
        if hasattr(getattr(result, name), "value")
        else getattr(result, name)
        for name in result.__dataclass_fields__
    }


def _cost_and_control_opportunities(
    authority: RegisteredSyntheticOpportunityAuthorityV2,
) -> tuple[tuple[CostScenarioEvidenceV2, ...], tuple[ControlOpportunity, ...]]:
    costs: list[CostScenarioEvidenceV2] = []
    controls: list[ControlOpportunity] = []
    for row in authority.rows:
        base = apply_cost_policy(
            authority.cost_policy,
            side=CostSide.LONG if row.direction == 1 else CostSide.SHORT,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            event_id=row.event_id,
            event_timestamp=row.label_start,
            venue=row.venue,
            symbol=row.symbol,
            timeframe=row.timeframe,
            application=CostApplication.BASE,
        )
        stress = apply_cost_policy(
            authority.cost_policy,
            side=CostSide.LONG if row.direction == 1 else CostSide.SHORT,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            event_id=row.event_id,
            event_timestamp=row.label_start,
            venue=row.venue,
            symbol=row.symbol,
            timeframe=row.timeframe,
            application=CostApplication.STRESS,
        )
        delayed = apply_cost_policy(
            authority.cost_policy,
            side=CostSide.LONG if row.direction == 1 else CostSide.SHORT,
            entry_price=row.delayed_entry_price,
            exit_price=row.delayed_exit_price,
            event_id=row.event_id,
            event_timestamp=row.delayed_entry_time,
            venue=row.venue,
            symbol=row.symbol,
            timeframe=row.timeframe,
            application=CostApplication.DELAYED,
            delay_evidence_sha256=row.delay_evidence_sha256,
        )
        costs.append(
            CostScenarioEvidenceV2(
                row_identity=row.row_identity,
                base=base,
                stress=stress,
                delayed=delayed,
            )
        )
        controls.append(
            ControlOpportunity(
                row_identity=row.row_identity,
                event_id=row.event_id,
                candidate_id=row.candidate_id,
                family=row.family,
                control_role=row.control_role,
                symbol=row.symbol,
                timeframe=row.timeframe,
                fold_id=row.fold_id,
                utc_week_start=row.utc_week_start,
                direction=row.direction,
                horizon_hours=row.horizon_hours,
                segment_id=row.segment_id,
                feature_time=row.feature_time,
                signal_time=row.signal_time,
                legal_entry_time=row.legal_entry_time,
                label_start=row.label_start,
                label_end=row.label_end,
                programme_id=row.programme_id,
                publication_sha256=row.publication_sha256,
                component=row.component,
                block_id=row.block_id,
                gross_return=base.gross_return,
                cost_return=base.expected_net_return,
                prior_completed_close_return=row.prior_completed_close_return,
            )
        )
    return tuple(costs), tuple(controls)


def _selection_by_candidate(selection: ControlSelection) -> Mapping[str, float]:
    return MappingProxyType(
        {record.candidate_row_identity: record.control_cost for record in selection.records}
    )


def evaluate_synthetic_primary_contrasts_v2(
    authority: RegisteredSyntheticOpportunityAuthorityV2,
) -> SyntheticPrimaryContrastEvaluationV2:
    """Execute the exact A/VS-0001 cost, control, weekly and bootstrap primitives."""

    snapshot = _snapshot_verified_authority_v2(authority)
    assessment = assess_cost_policy(snapshot.cost_policy)
    if assessment.admission_status is not CostAdmissionStatus.ADMISSIBLE:
        raise ValueError(f"cost policy is not admissible: {assessment.reason}")
    cost_scenarios, opportunities = _cost_and_control_opportunities(snapshot)
    candidates = tuple(row for row in opportunities if row.control_role == "candidate")
    control_pool = tuple(row for row in opportunities if row.control_role != "candidate")
    naive = build_naive_zero_controls(candidates, budget=snapshot.budget)
    unconditional = select_stratified_controls(
        candidates,
        control_pool,
        kind=ControlKind.UNCONDITIONAL,
        required_control_role="unconditional",
        budget=snapshot.budget,
    )
    persistence = build_persistence_controls(
        candidates,
        control_pool,
        budget=snapshot.budget,
    )
    controls_complete = all(
        selection.status is ControlSelectionStatus.COMPLETE
        for selection in (naive, unconditional, persistence)
    )

    naive_by_candidate = _selection_by_candidate(naive)
    unconditional_by_candidate = _selection_by_candidate(unconditional)
    persistence_by_candidate = _selection_by_candidate(persistence)
    observations = (
        tuple(
            WeeklyVectorObservation(
                row_id=row.row_identity,
                asset=row.symbol,
                timestamp=row.label_start,
                values=MappingProxyType(
                    {
                        "naive": row.cost_return - naive_by_candidate[row.row_identity],
                        "unconditional": (
                            row.cost_return - unconditional_by_candidate[row.row_identity]
                        ),
                        "persistence": (
                            row.cost_return - persistence_by_candidate[row.row_identity]
                        ),
                    }
                ),
                source_publication_sha256=snapshot.source_publication_sha256,
            )
            for row in candidates
        )
        if controls_complete
        else ()
    )
    weekly = aggregate_weekly_vectors(
        observations,
        vector_names=_CONTRAST_ROLES,
        expected_assets=snapshot.expected_assets,
        fold_start=snapshot.fold_start,
        fold_end=snapshot.fold_end,
        source_publication_sha256=snapshot.source_publication_sha256,
    )
    scenario_by_row = {item.row_identity: item for item in cost_scenarios}
    cost_opportunities = tuple(
        CostOpportunity(
            row_id=row.row_identity,
            base_round_trip_cost=(
                scenario_by_row[row.row_identity].base.entry_cost
                + scenario_by_row[row.row_identity].base.exit_cost
            ),
            component=row.component,
            source_sha256=scenario_by_row[row.row_identity].base.evidence_sha256,
        )
        for row in snapshot.rows
        if row.control_role == "candidate"
    )
    mde = derive_mde_evidence(
        opportunities=cost_opportunities,
        programme_id=snapshot.programme_id,
        candidate_id=snapshot.candidate_id,
        fold_id=snapshot.fold_id,
    )
    if mde.mde is None:
        raise ValueError(f"MDE evidence is unavailable: {mde.reason}")

    contrasts: list[PrimaryContrastEvidenceV2] = []
    for role in _CONTRAST_ROLES:
        weekly_values = tuple(week.values[role] for week in weekly.weeks)
        bootstrap = bootstrap_weekly_mean(
            weekly_values=weekly_values,
            seed=deterministic_validation_seed(
                programme_id=snapshot.programme_id,
                candidate_id=snapshot.candidate_id,
                fold_id=snapshot.fold_id,
                purpose=f"VS-0001:{role}",
            ),
            budget=snapshot.budget,
            mde=mde.mde,
            family="A",
            slot_id="VS-0001",
        )
        contrasts.append(
            PrimaryContrastEvidenceV2(role=role, weekly_values=weekly_values, bootstrap=bootstrap)
        )
    p_values = tuple(item.bootstrap.p_value for item in contrasts)
    lower_bounds = tuple(item.bootstrap.ci_lower for item in contrasts)
    inference_failed = any(
        item.bootstrap.status is ExecutionStatus.FAILED
        or item.bootstrap.decision is ScientificDecision.NOT_EVALUATED
        for item in contrasts
    )
    inference_evaluable = (
        controls_complete
        and not inference_failed
        and all(
            item.bootstrap.status is ExecutionStatus.COMPLETED
            and item.bootstrap.decision is ScientificDecision.REJECTED
            and item.bootstrap.p_value is not None
            and item.bootstrap.ci_lower is not None
            for item in contrasts
        )
    )
    if inference_evaluable:
        iut_p_value = max(cast(float, value) for value in p_values)
        iut_lower_bound = min(cast(float, value) for value in lower_bounds)
        inference_reason = "all required contrasts are evaluable"
        holm_effective_p_value = iut_p_value
        inference_state = "completed/evaluable_preterminal"
    elif inference_failed:
        iut_p_value = None
        iut_lower_bound = None
        inference_reason = "at least one required contrast failed"
        holm_effective_p_value = 1.0
        inference_state = "failed/not_evaluated"
    else:
        iut_p_value = None
        iut_lower_bound = None
        inference_reason = (
            "at least one required contrast is inconclusive"
            if controls_complete
            else "at least one required control selection is incomplete"
        )
        holm_effective_p_value = 1.0
        inference_state = "completed/inconclusive"

    evaluation = SyntheticPrimaryContrastEvaluationV2(
        authority_sha256=authority.sha256,
        cost_policy_assessment=assessment,
        cost_scenarios=cost_scenarios,
        naive_selection=naive,
        unconditional_selection=unconditional,
        persistence_selection=persistence,
        weekly_vectors=weekly,
        mde_evidence=mde,
        contrasts=tuple(contrasts),
        iut_p_value=iut_p_value,
        iut_lower_bound=iut_lower_bound,
        inference_evaluable=inference_evaluable,
        inference_state=inference_state,
        inference_reason=inference_reason,
        holm_effective_p_value=holm_effective_p_value,
        final_holdout_access_count=0,
        seal=_EVALUATION_SEAL,
    )
    identifier = id(evaluation)

    def cleanup(reference: ReferenceType[SyntheticPrimaryContrastEvaluationV2]) -> None:
        current = _VERIFIED_EVALUATIONS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_EVALUATIONS.pop(identifier, None)

    _VERIFIED_EVALUATIONS[identifier] = (
        ref(evaluation, cleanup),
        evaluation.sha256,
        authority,
    )
    return evaluation


def verify_synthetic_primary_contrast_evaluation_v2(
    evaluation: SyntheticPrimaryContrastEvaluationV2,
) -> None:
    """Verify exact evaluation identity and its registered synthetic parent authority."""

    if type(evaluation) is not SyntheticPrimaryContrastEvaluationV2:
        raise TypeError("evaluation must be the exact evaluator-issued type")
    registered = _VERIFIED_EVALUATIONS.get(id(evaluation))
    if registered is None or registered[0]() is not evaluation:
        raise ValueError("evaluation is not the registered original")
    _validate_evaluation_shape(evaluation)
    verify_registered_synthetic_opportunity_authority_v2(registered[2])
    if evaluation.authority_sha256 != registered[2].sha256:
        raise ValueError("evaluation replaced its registered authority")
    expected = _evaluation_content_sha(evaluation)
    if evaluation.sha256 != registered[1] or expected != registered[1]:
        raise ValueError("evaluation changed from its registered original")


__all__ = [
    "SyntheticPrimaryContrastEvaluationV2",
    "PrimaryContrastEvidenceV2",
    "CostScenarioEvidenceV2",
    "RawPrimaryOpportunityRowV2",
    "RegisteredSyntheticOpportunityAuthorityV2",
    "evaluate_synthetic_primary_contrasts_v2",
    "register_synthetic_primary_opportunity_authority_v2",
    "verify_registered_synthetic_opportunity_authority_v2",
    "verify_synthetic_primary_contrast_evaluation_v2",
]
