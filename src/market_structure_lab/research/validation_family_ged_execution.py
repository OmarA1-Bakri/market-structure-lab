"""Outcome-blind single-slot execution adapters for frozen Families G, E, and D.

The adapters consume only factory-issued aggregate/candidate capabilities and
authenticated detector parents.  They deliberately stop before outcome
attachment, cost application, inference, and terminal scientific decisions.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self, Sequence, cast
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.candidates import (
    CandidateSignal,
    VerifiedCandidateSeriesV2,
    detect_candidate_signals,
    verify_candidate_series_v2,
    verify_candidate_signal,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    CandidateDefinition,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    candidate_definition_for_verified_series,
)
from market_structure_lab.research.validation_family_a_execution import (
    FamilyADetectorExecutionEvidence,
    FamilyADetectorSlotEvidence,
)

_E_PARENT_FACTORY = object()
_EXECUTION_FACTORY = object()
_PLAN_FACTORY = object()


class FamilyGEDPlanStatus(StrEnum):
    """Outcome-blind readiness status for a control/comparator plan."""

    COMPLETE = "complete"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class FamilyDPseudoLevelReference:
    """One current D event bound to the historical window it actually used."""

    signal_id: str
    event_information_cutoff: datetime
    reference_start: datetime
    reference_end_exclusive: datetime
    reference_series_sha256: str
    reference_sha256: str


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyEDonchianParentEvidence:
    """One authenticated Family-A Donchian definition and opportunity population."""

    family_a_evidence_sha256: str
    parent_slot_id: str
    parent_candidate_id: str
    publication_sha256: str
    series_sha256: str
    timeframe: str
    direction: int
    opportunity_ids_sha256: str
    parent_sha256: str
    definition: CandidateDefinition
    opportunities: tuple[CandidateSignal, ...]
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _E_PARENT_FACTORY:
            raise TypeError("Family E parent evidence requires its binding factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_e_donchian_parent(self))


@dataclass(frozen=True, slots=True)
class _EParentRegistration:
    parent: weakref.ReferenceType[FamilyEDonchianParentEvidence]
    family_a: FamilyADetectorExecutionEvidence
    slot_evidence: FamilyADetectorSlotEvidence
    work_budget: ValidationWorkBudget
    snapshot: tuple[object, ...]


_E_PARENTS: dict[int, _EParentRegistration] = {}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyGEDDetectorExecutionEvidence:
    """Factory-issued signals for one exact frozen G/E/D detector slot."""

    slot: ValidationSlot
    definition: CandidateDefinition
    signals: tuple[CandidateSignal, ...]
    signal_ids_sha256: str
    publication_sha256: str
    series_sha256: str
    parent_sha256: str | None
    work_budget_sha256: str
    work_demand: ValidationWorkDemand
    d_pseudo_level_references: tuple[FamilyDPseudoLevelReference, ...]
    execution_sha256: str
    outcome_rows: int
    final_access_records: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _EXECUTION_FACTORY:
            raise TypeError("G/E/D detector evidence requires its execution factory")
        if self.outcome_rows or self.final_access_records:
            raise ValueError("G/E/D detector evidence cannot contain outcomes or final access")

    def verify(self) -> Self:
        return cast(Self, verify_family_ged_detector_execution(self))


@dataclass(frozen=True, slots=True)
class _ExecutionRegistration:
    execution: weakref.ReferenceType[FamilyGEDDetectorExecutionEvidence]
    series: VerifiedCandidateSeriesV2
    family_e_parent: FamilyEDonchianParentEvidence | None
    family_d_primary: FamilyGEDDetectorExecutionEvidence | None
    work_budget: ValidationWorkBudget
    snapshot: tuple[object, ...]


_EXECUTIONS: dict[int, _ExecutionRegistration] = {}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyGIntersectionUnionPlan:
    """Bind the primary to both controls; inference must use the worse control."""

    primary_execution_sha256: str
    atr_control_execution_sha256: str
    donchian_control_execution_sha256: str
    method: str
    status: FamilyGEDPlanStatus
    reason: str
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PLAN_FACTORY:
            raise TypeError("Family G control plan requires its factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_ged_plan(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyEIncrementalEstimandPlan:
    """Bind E's common A opportunity population and exact comparator triplet."""

    price_only_execution_sha256: str
    primary_execution_sha256: str
    placebo_execution_sha256: str
    parent_sha256: str
    estimand: str
    status: FamilyGEDPlanStatus
    reason: str
    shortage_count: int
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PLAN_FACTORY:
            raise TypeError("Family E estimand plan requires its factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_ged_plan(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyDPseudoLevelDonorPlan:
    """Bind D's confirmed clock and exact prior-week pseudo-level donor search."""

    primary_execution_sha256: str
    failed_donchian_execution_sha256: str
    pseudo_level_execution_sha256: str
    confirmation_bars: int
    information_cutoff_clock: str
    legal_entry_clock: str
    delayed_entry_bars: int
    donor_rule: str
    matched_donor_count: int
    shortage_count: int
    status: FamilyGEDPlanStatus
    reason: str
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PLAN_FACTORY:
            raise TypeError("Family D donor plan requires its factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_ged_plan(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyGEDAdjacentPerturbationPlan:
    """Bind one adjacent detector to its exact core scientific-control plan."""

    perturbation_execution_sha256: str
    parent_primary_execution_sha256: str
    parent_slot_id: str
    control_plan_sha256: str
    detector_role: str
    status: FamilyGEDPlanStatus
    reason: str
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PLAN_FACTORY:
            raise TypeError("adjacent perturbation plan requires its factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_ged_plan(self))


FamilyGEDPlan = (
    FamilyGIntersectionUnionPlan
    | FamilyEIncrementalEstimandPlan
    | FamilyDPseudoLevelDonorPlan
    | FamilyGEDAdjacentPerturbationPlan
)


@dataclass(frozen=True, slots=True)
class _PlanRegistration:
    plan: weakref.ReferenceType[FamilyGEDPlan]
    execution_parents: tuple[FamilyGEDDetectorExecutionEvidence, ...]
    plan_parents: tuple[FamilyGEDPlan, ...]
    snapshot: tuple[object, ...]


_PLANS: dict[int, _PlanRegistration] = {}


def family_ged_detector_slots(family: str, timeframe: str) -> tuple[ValidationSlot, ...]:
    """Return every frozen G/E/D core or adjacent-lookback slot for one timeframe."""

    if family not in ("G", "E", "D"):
        raise ValueError("detector family must be G, E, or D")
    if timeframe not in ("1h", "4h"):
        raise ValueError("detector timeframe must be 1h or 4h")
    return tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == family
        and slot.timeframe == timeframe
        and slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )


def bind_family_e_donchian_parent(
    family_a_evidence: object,
    parent_slot: object,
    *,
    work_budget: ValidationWorkBudget,
) -> FamilyEDonchianParentEvidence:
    """Bind one exact, already-computed A Donchian opportunity population."""

    if type(family_a_evidence) is not FamilyADetectorExecutionEvidence:
        raise TypeError("Family E parent binding requires exact Family A evidence")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("Family E parent binding requires the frozen work budget")
    canonical = _require_canonical_slot(parent_slot, families=("A",))
    if dict(canonical.parameters).get("detector") != "donchian_breakout":
        raise ValueError("Family E requires an exact A Donchian opportunity parent")
    evidence = cast(FamilyADetectorExecutionEvidence, family_a_evidence)
    work_budget.preflight(
        ValidationWorkDemand(candidates=len(evidence.slots), events=evidence.emitted_signal_count),
        deferred_work=evidence.slots,
    )
    evidence.verify()
    selected = next((item for item in evidence.slots if item.slot is canonical), None)
    if selected is None:
        raise ValueError("Family E parent slot is absent from the authenticated A execution")
    opportunity_digest = hash_json(
        "phase5-family-e-parent-opportunity-order-v1",
        [signal.signal_id for signal in selected.signals],
    )
    payload = {
        "family_a_evidence_sha256": evidence.evidence_sha256,
        "parent_slot_id": canonical.slot_id,
        "parent_candidate_id": selected.definition.candidate_id,
        "publication_sha256": evidence.binding.publication_sha256,
        "series_sha256": evidence.binding.series_sha256,
        "timeframe": canonical.timeframe,
        "direction": selected.definition.direction,
        "opportunity_ids_sha256": opportunity_digest,
        "outcome_rows": 0,
        "final_access_records": 0,
    }
    parent = FamilyEDonchianParentEvidence(
        family_a_evidence_sha256=evidence.evidence_sha256,
        parent_slot_id=canonical.slot_id,
        parent_candidate_id=selected.definition.candidate_id,
        publication_sha256=evidence.binding.publication_sha256,
        series_sha256=evidence.binding.series_sha256,
        timeframe=canonical.timeframe,
        direction=selected.definition.direction,
        opportunity_ids_sha256=opportunity_digest,
        parent_sha256=hash_json("phase5-family-e-donchian-parent-v1", payload),
        definition=selected.definition,
        opportunities=selected.signals,
        _factory_token=_E_PARENT_FACTORY,
    )
    identifier = id(parent)

    def cleanup(reference: weakref.ReferenceType[FamilyEDonchianParentEvidence]) -> None:
        current = _E_PARENTS.get(identifier)
        if current is not None and current.parent is reference:
            _E_PARENTS.pop(identifier, None)

    _E_PARENTS[identifier] = _EParentRegistration(
        parent=weakref.ref(parent, cleanup),
        family_a=evidence,
        slot_evidence=selected,
        work_budget=work_budget,
        snapshot=_parent_snapshot(parent),
    )
    return parent


def verify_family_e_donchian_parent(parent: object) -> FamilyEDonchianParentEvidence:
    """Reopen the exact A evidence and reject copied or mutated E parents."""

    if type(parent) is not FamilyEDonchianParentEvidence:
        raise TypeError("Family E parent must be exact and factory-issued")
    typed = cast(FamilyEDonchianParentEvidence, parent)
    registration = _E_PARENTS.get(id(typed))
    if (
        registration is None
        or registration.parent() is not typed
        or registration.snapshot != _parent_snapshot(typed)
    ):
        raise ValueError("Family E parent is unregistered or mutated")
    registration.family_a.verify()
    for signal in typed.opportunities:
        verify_candidate_signal(signal)
    if typed.opportunity_ids_sha256 != hash_json(
        "phase5-family-e-parent-opportunity-order-v1",
        [signal.signal_id for signal in typed.opportunities],
    ):
        raise ValueError("Family E parent opportunity order differs")
    return typed


def execute_family_ged_detector_slot(
    series: object,
    slot: object,
    *,
    work_budget: ValidationWorkBudget,
    declared_event_ceiling: int,
    family_e_parent: object | None = None,
    family_d_primary: object | None = None,
    outcome_inputs: object | None = None,
) -> FamilyGEDDetectorExecutionEvidence:
    """Execute one exact G/E/D detector without source, outcome, or final access."""

    canonical = _require_canonical_slot(slot, families=("G", "E", "D"))
    if type(series) is not VerifiedCandidateSeriesV2:
        raise TypeError("G/E/D execution requires a factory-issued candidate series")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("G/E/D execution requires the frozen work budget")
    if outcome_inputs is not None:
        raise ValueError("outcome inputs are forbidden during G/E/D detector execution")
    if (
        isinstance(declared_event_ceiling, bool)
        or not isinstance(declared_event_ceiling, int)
        or declared_event_ceiling < 0
    ):
        raise ValueError("declared_event_ceiling must be a non-negative integer")
    parent: FamilyEDonchianParentEvidence | None = None
    d_primary: FamilyGEDDetectorExecutionEvidence | None = None
    declared_events = declared_event_ceiling
    if canonical.family == "E":
        if type(family_e_parent) is not FamilyEDonchianParentEvidence:
            raise TypeError("Family E execution requires an authenticated A Donchian parent")
        parent = cast(FamilyEDonchianParentEvidence, family_e_parent)
        if len(parent.opportunities) > declared_event_ceiling:
            raise ValueError("Family E opportunities exceed the declared event ceiling")
        declared_events = len(parent.opportunities)
    elif family_e_parent is not None:
        raise ValueError("Family E parent evidence is valid only for Family E")
    if canonical.family == "D" and _detector_role_for_slot(canonical) == "pseudo_level_control":
        if type(family_d_primary) is not FamilyGEDDetectorExecutionEvidence:
            raise TypeError("Family D pseudo-level execution requires its exact primary execution")
        d_primary = cast(FamilyGEDDetectorExecutionEvidence, family_d_primary).verify()
        if (
            d_primary.slot.family != "D"
            or d_primary.definition.role != "candidate_primary"
            or d_primary.slot.kind is not ValidationSlotKind.CORE
        ):
            raise ValueError("Family D pseudo-level parent must be its exact core primary")
        declared_events = len(d_primary.signals)
    elif family_d_primary is not None:
        raise ValueError("Family D primary evidence is valid only for pseudo-level execution")
    typed_series = cast(VerifiedCandidateSeriesV2, series)
    demand = ValidationWorkDemand(
        aggregate_bars=len(typed_series.bars),
        symbols=1,
        ranges=1,
        candidates=1,
        events=declared_events,
    )
    work_budget.preflight(demand, deferred_work=(slot,))
    verified_series = verify_candidate_series_v2(typed_series)
    if canonical.timeframe != verified_series.target_timeframe:
        raise ValueError("slot timeframe does not match the authenticated candidate series")
    if parent is not None:
        parent.verify()
        if (
            parent.publication_sha256 != verified_series.publication_sha256
            or parent.series_sha256 != verified_series.series_sha256
            or parent.timeframe != canonical.timeframe
            or parent.direction != (1 if canonical.direction == "long" else -1)
        ):
            raise ValueError("Family E parent axes do not match the requested detector slot")
    if d_primary is not None and (
        d_primary.publication_sha256 != verified_series.publication_sha256
        or d_primary.series_sha256 != verified_series.series_sha256
        or d_primary.slot.timeframe != canonical.timeframe
        or d_primary.slot.direction != canonical.direction
        or d_primary.slot.horizon_hours != canonical.horizon_hours
        or d_primary.slot.parameters != canonical.parameters
    ):
        raise ValueError("Family D pseudo-level parent axes differ")
    definition = candidate_definition_for_verified_series(
        canonical,
        verified_series,
        parent_a_candidate=None if parent is None else parent.definition,
    )
    signals = (
        ()
        if d_primary is not None
        else detect_candidate_signals(
            definition,
            verified_series,
            a_opportunities=() if parent is None else parent.opportunities,
            max_emitted_signals=(declared_event_ceiling if parent is None else None),
        )
    )
    if len(signals) > declared_event_ceiling:
        raise ValueError("detector emission exceeds the declared event ceiling")
    d_references = _bind_d_pseudo_level_references(
        canonical,
        verified_series,
        d_primary.signals if d_primary is not None else signals,
    )
    signal_digest = hash_json(
        "phase5-family-ged-slot-signal-order-v1",
        [signal.signal_id for signal in signals],
    )
    payload = {
        "slot_id": canonical.slot_id,
        "candidate_id": definition.candidate_id,
        "publication_sha256": verified_series.publication_sha256,
        "series_sha256": verified_series.series_sha256,
        "parent_sha256": (
            parent.parent_sha256
            if parent is not None
            else d_primary.execution_sha256
            if d_primary is not None
            else None
        ),
        "work_budget_sha256": work_budget.sha256,
        "signal_ids_sha256": signal_digest,
        "signal_count": len(signals),
        "d_pseudo_level_references": [
            _d_reference_payload(reference) for reference in d_references
        ],
        "outcome_rows": 0,
        "final_access_records": 0,
    }
    execution = FamilyGEDDetectorExecutionEvidence(
        slot=canonical,
        definition=definition,
        signals=signals,
        signal_ids_sha256=signal_digest,
        publication_sha256=verified_series.publication_sha256,
        series_sha256=verified_series.series_sha256,
        parent_sha256=(
            parent.parent_sha256
            if parent is not None
            else d_primary.execution_sha256
            if d_primary is not None
            else None
        ),
        work_budget_sha256=work_budget.sha256,
        work_demand=demand,
        d_pseudo_level_references=d_references,
        execution_sha256=hash_json("phase5-family-ged-detector-execution-v1", payload),
        outcome_rows=0,
        final_access_records=0,
        _factory_token=_EXECUTION_FACTORY,
    )
    identifier = id(execution)

    def cleanup(reference: weakref.ReferenceType[FamilyGEDDetectorExecutionEvidence]) -> None:
        current = _EXECUTIONS.get(identifier)
        if current is not None and current.execution is reference:
            _EXECUTIONS.pop(identifier, None)

    _EXECUTIONS[identifier] = _ExecutionRegistration(
        execution=weakref.ref(execution, cleanup),
        series=verified_series,
        family_e_parent=parent,
        family_d_primary=d_primary,
        work_budget=work_budget,
        snapshot=_execution_snapshot(execution),
    )
    return execution


def verify_family_ged_detector_execution(
    execution: object,
) -> FamilyGEDDetectorExecutionEvidence:
    """Reject copied/mutated evidence and reopen every retained parent."""

    if type(execution) is not FamilyGEDDetectorExecutionEvidence:
        raise TypeError("G/E/D detector evidence must be exact and factory-issued")
    typed = cast(FamilyGEDDetectorExecutionEvidence, execution)
    registration = _EXECUTIONS.get(id(typed))
    if (
        registration is None
        or registration.execution() is not typed
        or registration.snapshot != _execution_snapshot(typed)
    ):
        raise ValueError("G/E/D detector evidence is unregistered or mutated")
    series = verify_candidate_series_v2(registration.series)
    if (
        typed.publication_sha256 != series.publication_sha256
        or typed.series_sha256 != series.series_sha256
        or typed.work_budget_sha256 != registration.work_budget.sha256
    ):
        raise ValueError("G/E/D detector parent identity differs")
    if registration.family_e_parent is not None:
        parent = registration.family_e_parent.verify()
        if typed.parent_sha256 != parent.parent_sha256:
            raise ValueError("Family E detector parent identity differs")
    if registration.family_d_primary is not None:
        primary = registration.family_d_primary.verify()
        if typed.parent_sha256 != primary.execution_sha256:
            raise ValueError("Family D pseudo-level parent identity differs")
    for signal in typed.signals:
        verify_candidate_signal(signal)
    if typed.signal_ids_sha256 != hash_json(
        "phase5-family-ged-slot-signal-order-v1",
        [signal.signal_id for signal in typed.signals],
    ):
        raise ValueError("G/E/D detector signal order differs")
    expected_references = _bind_d_pseudo_level_references(
        typed.slot,
        series,
        (
            registration.family_d_primary.signals
            if registration.family_d_primary is not None
            else typed.signals
        ),
    )
    if typed.d_pseudo_level_references != expected_references:
        raise ValueError("Family D pseudo-level historical reference identity differs")
    return typed


def plan_family_g_intersection_union(
    primary: object,
    atr_control: object,
    donchian_control: object,
) -> FamilyGIntersectionUnionPlan:
    """Bind exact G controls under the frozen worst-control intersection-union rule."""

    primary_e, atr_e, donchian_e = _verified_triplet(
        primary, atr_control, donchian_control, family="G"
    )
    if primary_e.definition.role != "candidate_primary":
        raise ValueError("Family G primary execution role differs")
    if atr_e.definition.role != "atr_only_control":
        raise ValueError("Family G ATR control execution role differs")
    if donchian_e.definition.role != "donchian_only_control":
        raise ValueError("Family G Donchian control execution role differs")
    _require_same_core_axes(primary_e, atr_e, donchian_e)
    shortage = bool(primary_e.signals) and (not atr_e.signals or not donchian_e.signals)
    status = FamilyGEDPlanStatus.INCONCLUSIVE if shortage else FamilyGEDPlanStatus.COMPLETE
    reason = "legal_control_shortage" if shortage else "complete"
    payload = {
        "primary": primary_e.execution_sha256,
        "atr": atr_e.execution_sha256,
        "donchian": donchian_e.execution_sha256,
        "method": "intersection_union_worst_control",
        "status": status.value,
        "reason": reason,
    }
    plan = FamilyGIntersectionUnionPlan(
        primary_execution_sha256=primary_e.execution_sha256,
        atr_control_execution_sha256=atr_e.execution_sha256,
        donchian_control_execution_sha256=donchian_e.execution_sha256,
        method="intersection_union_worst_control",
        status=status,
        reason=reason,
        plan_sha256=hash_json("phase5-family-g-intersection-union-plan-v1", payload),
        _factory_token=_PLAN_FACTORY,
    )
    return cast(
        FamilyGIntersectionUnionPlan,
        _register_plan(plan, execution_parents=(primary_e, atr_e, donchian_e)),
    )


def plan_family_e_incremental_estimand(
    price_only: object,
    primary: object,
    placebo: object,
) -> FamilyEIncrementalEstimandPlan:
    """Bind E's identical population and incremental lift estimand."""

    price_e, primary_e, placebo_e = _verified_triplet(price_only, primary, placebo, family="E")
    if price_e.definition.role != "price_only":
        raise ValueError("Family E price-only execution role differs")
    if primary_e.definition.role != "volume_filtered_primary":
        raise ValueError("Family E primary execution role differs")
    if placebo_e.definition.role != "rate_matched_placebo":
        raise ValueError("Family E placebo execution role differs")
    _require_same_core_axes(price_e, primary_e, placebo_e)
    parents = {price_e.parent_sha256, primary_e.parent_sha256, placebo_e.parent_sha256}
    if None in parents or len(parents) != 1:
        raise ValueError("Family E comparator triplet must share one exact A parent")
    shortage = max(0, len(primary_e.signals) - len(placebo_e.signals))
    status = FamilyGEDPlanStatus.INCONCLUSIVE if shortage else FamilyGEDPlanStatus.COMPLETE
    reason = "rate_matched_placebo_shortage" if shortage else "complete"
    parent_sha = cast(str, primary_e.parent_sha256)
    payload = {
        "price_only": price_e.execution_sha256,
        "primary": primary_e.execution_sha256,
        "placebo": placebo_e.execution_sha256,
        "parent_sha256": parent_sha,
        "estimand": "volume_filtered_minus_identical_price_only_on_exact_a_donchian_population",
        "status": status.value,
        "reason": reason,
        "shortage_count": shortage,
    }
    plan = FamilyEIncrementalEstimandPlan(
        price_only_execution_sha256=price_e.execution_sha256,
        primary_execution_sha256=primary_e.execution_sha256,
        placebo_execution_sha256=placebo_e.execution_sha256,
        parent_sha256=parent_sha,
        estimand="volume_filtered_minus_identical_price_only_on_exact_a_donchian_population",
        status=status,
        reason=reason,
        shortage_count=shortage,
        plan_sha256=hash_json("phase5-family-e-incremental-estimand-plan-v1", payload),
        _factory_token=_PLAN_FACTORY,
    )
    return cast(
        FamilyEIncrementalEstimandPlan,
        _register_plan(plan, execution_parents=(price_e, primary_e, placebo_e)),
    )


def plan_family_d_pseudo_level_donors(
    primary: object,
    failed_donchian: object,
    pseudo_level: object,
) -> FamilyDPseudoLevelDonorPlan:
    """Bind D's confirmation clock and each event's historical reference window."""

    primary_e, failed_e, pseudo_e = _verified_triplet(
        primary, failed_donchian, pseudo_level, family="D"
    )
    if primary_e.definition.role != "candidate_primary":
        raise ValueError("Family D primary execution role differs")
    if failed_e.definition.role != "failed_donchian_control":
        raise ValueError("Family D failed-Donchian execution role differs")
    if pseudo_e.definition.role != "pseudo_level_control":
        raise ValueError("Family D pseudo-level execution role differs")
    _require_same_core_axes(primary_e, failed_e, pseudo_e)
    if pseudo_e.parent_sha256 != primary_e.execution_sha256:
        raise ValueError("Family D pseudo-level execution does not bind the current primary clock")
    matched = len(pseudo_e.d_pseudo_level_references)
    shortage = len(primary_e.signals) - matched
    status = FamilyGEDPlanStatus.INCONCLUSIVE if shortage else FamilyGEDPlanStatus.COMPLETE
    reason = "prior_week_pseudo_level_donor_shortage" if shortage else "complete"
    payload = {
        "primary": primary_e.execution_sha256,
        "failed_donchian": failed_e.execution_sha256,
        "pseudo_level": pseudo_e.execution_sha256,
        "confirmation_bars": 1,
        "information_cutoff_clock": "breach_bar_plus_one_completed_confirmation",
        "legal_entry_clock": "confirmation_bar_close",
        "delayed_entry_bars": 1,
        "donor_rule": "same_current_event_clock_bound_historical_prior_week_reference",
        "matched_donor_count": matched,
        "shortage_count": shortage,
        "status": status.value,
        "reason": reason,
    }
    plan = FamilyDPseudoLevelDonorPlan(
        primary_execution_sha256=primary_e.execution_sha256,
        failed_donchian_execution_sha256=failed_e.execution_sha256,
        pseudo_level_execution_sha256=pseudo_e.execution_sha256,
        confirmation_bars=1,
        information_cutoff_clock="breach_bar_plus_one_completed_confirmation",
        legal_entry_clock="confirmation_bar_close",
        delayed_entry_bars=1,
        donor_rule="same_current_event_clock_bound_historical_prior_week_reference",
        matched_donor_count=matched,
        shortage_count=shortage,
        status=status,
        reason=reason,
        plan_sha256=hash_json("phase5-family-d-pseudo-level-donor-plan-v1", payload),
        _factory_token=_PLAN_FACTORY,
    )
    return cast(
        FamilyDPseudoLevelDonorPlan,
        _register_plan(plan, execution_parents=(primary_e, failed_e, pseudo_e)),
    )


def bind_family_ged_adjacent_perturbation_plan(
    perturbation: object,
    parent_primary: object,
    control_plan: object,
) -> FamilyGEDAdjacentPerturbationPlan:
    """Bind G/E/D adjacent execution to its exact core plan without new inference."""

    if (
        type(perturbation) is not FamilyGEDDetectorExecutionEvidence
        or type(parent_primary) is not FamilyGEDDetectorExecutionEvidence
    ):
        raise TypeError("adjacent planning requires exact detector evidence")
    adjacent = cast(FamilyGEDDetectorExecutionEvidence, perturbation).verify()
    primary = cast(FamilyGEDDetectorExecutionEvidence, parent_primary).verify()
    if adjacent.slot.kind is not ValidationSlotKind.PERTURBATION:
        raise ValueError("adjacent planning requires a perturbation slot")
    if primary.slot.kind is not ValidationSlotKind.CORE or not primary.slot.primary:
        raise ValueError("adjacent planning requires its exact core primary execution")
    if adjacent.slot.parent_slot_id != primary.slot.slot_id:
        raise ValueError("adjacent slot parent identity differs from the core execution")
    if (
        adjacent.slot.family != primary.slot.family
        or adjacent.slot.timeframe != primary.slot.timeframe
        or adjacent.slot.direction != primary.slot.direction
        or adjacent.slot.horizon_hours != primary.slot.horizon_hours
        or adjacent.publication_sha256 != primary.publication_sha256
        or adjacent.series_sha256 != primary.series_sha256
        or adjacent.definition.role != primary.definition.role
    ):
        raise ValueError("adjacent execution does not preserve its parent detector axes")
    expected_type: type[object]
    if adjacent.slot.family == "G":
        expected_type = FamilyGIntersectionUnionPlan
        expected_primary_sha = (
            cast(FamilyGIntersectionUnionPlan, control_plan).primary_execution_sha256
            if type(control_plan) is expected_type
            else None
        )
    elif adjacent.slot.family == "E":
        expected_type = FamilyEIncrementalEstimandPlan
        expected_primary_sha = (
            cast(FamilyEIncrementalEstimandPlan, control_plan).primary_execution_sha256
            if type(control_plan) is expected_type
            else None
        )
    else:
        expected_type = FamilyDPseudoLevelDonorPlan
        expected_primary_sha = (
            cast(FamilyDPseudoLevelDonorPlan, control_plan).primary_execution_sha256
            if type(control_plan) is expected_type
            else None
        )
    if type(control_plan) is not expected_type or expected_primary_sha != primary.execution_sha256:
        raise ValueError("adjacent execution does not bind its exact family control plan")
    typed_plan = cast(
        FamilyGIntersectionUnionPlan | FamilyEIncrementalEstimandPlan | FamilyDPseudoLevelDonorPlan,
        control_plan,
    )
    verified_control_plan = verify_family_ged_plan(typed_plan)
    payload = {
        "perturbation_execution_sha256": adjacent.execution_sha256,
        "parent_primary_execution_sha256": primary.execution_sha256,
        "parent_slot_id": primary.slot.slot_id,
        "control_plan_sha256": typed_plan.plan_sha256,
        "detector_role": adjacent.definition.role,
        "status": typed_plan.status.value,
        "reason": typed_plan.reason,
    }
    plan = FamilyGEDAdjacentPerturbationPlan(
        perturbation_execution_sha256=adjacent.execution_sha256,
        parent_primary_execution_sha256=primary.execution_sha256,
        parent_slot_id=primary.slot.slot_id,
        control_plan_sha256=typed_plan.plan_sha256,
        detector_role=adjacent.definition.role,
        status=typed_plan.status,
        reason=typed_plan.reason,
        plan_sha256=hash_json("phase5-family-ged-adjacent-plan-v1", payload),
        _factory_token=_PLAN_FACTORY,
    )
    return cast(
        FamilyGEDAdjacentPerturbationPlan,
        _register_plan(
            plan,
            execution_parents=(adjacent, primary),
            plan_parents=(verified_control_plan,),
        ),
    )


def verify_family_ged_plan(plan: object) -> FamilyGEDPlan:
    """Reopen every exact plan parent and reject copies, rehashes, and mutation."""

    if type(plan) not in (
        FamilyGIntersectionUnionPlan,
        FamilyEIncrementalEstimandPlan,
        FamilyDPseudoLevelDonorPlan,
        FamilyGEDAdjacentPerturbationPlan,
    ):
        raise TypeError("G/E/D plan must be exact and factory-issued")
    typed = cast(FamilyGEDPlan, plan)
    registration = _PLANS.get(id(typed))
    if (
        registration is None
        or registration.plan() is not typed
        or registration.snapshot != _plan_snapshot(typed)
    ):
        raise ValueError("G/E/D plan is unregistered or mutated")
    for execution in registration.execution_parents:
        execution.verify()
    for parent in registration.plan_parents:
        verify_family_ged_plan(parent)
    if typed.plan_sha256 != _plan_digest(typed):
        raise ValueError("G/E/D plan identity differs")
    return typed


def _register_plan(
    plan: FamilyGEDPlan,
    *,
    execution_parents: tuple[FamilyGEDDetectorExecutionEvidence, ...],
    plan_parents: tuple[FamilyGEDPlan, ...] = (),
) -> FamilyGEDPlan:
    identifier = id(plan)

    def cleanup(reference: weakref.ReferenceType[FamilyGEDPlan]) -> None:
        current = _PLANS.get(identifier)
        if current is not None and current.plan is reference:
            _PLANS.pop(identifier, None)

    _PLANS[identifier] = _PlanRegistration(
        plan=weakref.ref(plan, cleanup),
        execution_parents=execution_parents,
        plan_parents=plan_parents,
        snapshot=_plan_snapshot(plan),
    )
    return plan


def _plan_digest(plan: FamilyGEDPlan) -> str:
    if type(plan) is FamilyGIntersectionUnionPlan:
        typed = cast(FamilyGIntersectionUnionPlan, plan)
        return hash_json(
            "phase5-family-g-intersection-union-plan-v1",
            {
                "primary": typed.primary_execution_sha256,
                "atr": typed.atr_control_execution_sha256,
                "donchian": typed.donchian_control_execution_sha256,
                "method": typed.method,
                "status": typed.status.value,
                "reason": typed.reason,
            },
        )
    if type(plan) is FamilyEIncrementalEstimandPlan:
        typed_e = cast(FamilyEIncrementalEstimandPlan, plan)
        return hash_json(
            "phase5-family-e-incremental-estimand-plan-v1",
            {
                "price_only": typed_e.price_only_execution_sha256,
                "primary": typed_e.primary_execution_sha256,
                "placebo": typed_e.placebo_execution_sha256,
                "parent_sha256": typed_e.parent_sha256,
                "estimand": typed_e.estimand,
                "status": typed_e.status.value,
                "reason": typed_e.reason,
                "shortage_count": typed_e.shortage_count,
            },
        )
    if type(plan) is FamilyDPseudoLevelDonorPlan:
        typed_d = cast(FamilyDPseudoLevelDonorPlan, plan)
        return hash_json(
            "phase5-family-d-pseudo-level-donor-plan-v1",
            {
                "primary": typed_d.primary_execution_sha256,
                "failed_donchian": typed_d.failed_donchian_execution_sha256,
                "pseudo_level": typed_d.pseudo_level_execution_sha256,
                "confirmation_bars": typed_d.confirmation_bars,
                "information_cutoff_clock": typed_d.information_cutoff_clock,
                "legal_entry_clock": typed_d.legal_entry_clock,
                "delayed_entry_bars": typed_d.delayed_entry_bars,
                "donor_rule": typed_d.donor_rule,
                "matched_donor_count": typed_d.matched_donor_count,
                "shortage_count": typed_d.shortage_count,
                "status": typed_d.status.value,
                "reason": typed_d.reason,
            },
        )
    typed_adjacent = cast(FamilyGEDAdjacentPerturbationPlan, plan)
    return hash_json(
        "phase5-family-ged-adjacent-plan-v1",
        {
            "perturbation_execution_sha256": typed_adjacent.perturbation_execution_sha256,
            "parent_primary_execution_sha256": (typed_adjacent.parent_primary_execution_sha256),
            "parent_slot_id": typed_adjacent.parent_slot_id,
            "control_plan_sha256": typed_adjacent.control_plan_sha256,
            "detector_role": typed_adjacent.detector_role,
            "status": typed_adjacent.status.value,
            "reason": typed_adjacent.reason,
        },
    )


def _plan_snapshot(plan: FamilyGEDPlan) -> tuple[object, ...]:
    return tuple(
        getattr(plan, field) for field in plan.__dataclass_fields__ if field != "_factory_token"
    )


def _bind_d_pseudo_level_references(
    slot: ValidationSlot,
    series: VerifiedCandidateSeriesV2,
    signals: tuple[CandidateSignal, ...],
) -> tuple[FamilyDPseudoLevelReference, ...]:
    if slot.family != "D" or _detector_role_for_slot(slot) != "pseudo_level_control":
        return ()
    parameters = dict(slot.parameters)
    step_hours = 1 if slot.timeframe == "1h" else 4
    lookback = int(parameters["level_hours"]) // step_hours
    if slot.kind is ValidationSlotKind.PERTURBATION:
        lookback = int(parameters["candidate_bars"])
    week = (24 * 7) // step_hours
    by_close = {bar.bar_close: index for index, bar in enumerate(series.bars)}
    references: list[FamilyDPseudoLevelReference] = []
    for signal in signals:
        cutoff_index = by_close.get(signal.information_cutoff)
        event_index = None if cutoff_index is None else cutoff_index - 1
        reference_end_index = None if event_index is None else event_index - week
        reference_start_index = (
            None if reference_end_index is None else reference_end_index - lookback
        )
        if (
            cutoff_index is None
            or reference_end_index is None
            or reference_start_index is None
            or reference_start_index < 0
        ):
            raise ValueError("Family D pseudo-level historical reference is unavailable")
        payload = {
            "signal_id": signal.signal_id,
            "event_information_cutoff": signal.information_cutoff,
            "reference_start": series.bars[reference_start_index].timestamp,
            "reference_end_exclusive": series.bars[reference_end_index].timestamp,
            "reference_series_sha256": series.series_sha256,
        }
        references.append(
            FamilyDPseudoLevelReference(
                signal_id=signal.signal_id,
                event_information_cutoff=signal.information_cutoff,
                reference_start=series.bars[reference_start_index].timestamp,
                reference_end_exclusive=series.bars[reference_end_index].timestamp,
                reference_series_sha256=series.series_sha256,
                reference_sha256=hash_json("phase5-family-d-pseudo-level-reference-v1", payload),
            )
        )
    return tuple(references)


def _detector_role_for_slot(slot: ValidationSlot) -> str:
    if slot.kind is ValidationSlotKind.CORE:
        return slot.role
    parent = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == slot.parent_slot_id)
    return parent.role


def _d_reference_payload(reference: FamilyDPseudoLevelReference) -> dict[str, object]:
    return {
        "signal_id": reference.signal_id,
        "event_information_cutoff": reference.event_information_cutoff,
        "reference_start": reference.reference_start,
        "reference_end_exclusive": reference.reference_end_exclusive,
        "reference_series_sha256": reference.reference_series_sha256,
        "reference_sha256": reference.reference_sha256,
    }


def _require_canonical_slot(slot: object, *, families: Sequence[str]) -> ValidationSlot:
    if type(slot) is not ValidationSlot or not any(
        slot is canonical for canonical in VALIDATION_SLOT_ROSTER
    ):
        raise TypeError("execution requires an exact identity-canonical validation slot")
    typed = cast(ValidationSlot, slot)
    if typed.family not in families or typed.kind not in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    ):
        raise ValueError("execution slot family or kind is unsupported")
    return typed


def _verified_triplet(
    first: object, second: object, third: object, *, family: str
) -> tuple[
    FamilyGEDDetectorExecutionEvidence,
    FamilyGEDDetectorExecutionEvidence,
    FamilyGEDDetectorExecutionEvidence,
]:
    items: list[FamilyGEDDetectorExecutionEvidence] = []
    for item in (first, second, third):
        if type(item) is not FamilyGEDDetectorExecutionEvidence:
            raise TypeError("control planning requires exact detector evidence")
        verified = cast(FamilyGEDDetectorExecutionEvidence, item).verify()
        if verified.slot.family != family:
            raise ValueError("control planning family differs")
        items.append(verified)
    return items[0], items[1], items[2]


def _require_same_core_axes(
    primary: FamilyGEDDetectorExecutionEvidence,
    first_control: FamilyGEDDetectorExecutionEvidence,
    second_control: FamilyGEDDetectorExecutionEvidence,
) -> None:
    if any(
        item.slot.kind is not ValidationSlotKind.CORE
        for item in (primary, first_control, second_control)
    ):
        raise ValueError("control triplet planning requires frozen core slots")
    axes = {
        (
            item.slot.family,
            item.slot.timeframe,
            item.slot.direction,
            item.slot.horizon_hours,
            item.slot.parameters,
            item.publication_sha256,
            item.series_sha256,
        )
        for item in (primary, first_control, second_control)
    }
    if len(axes) != 1:
        raise ValueError("control triplet does not share exact source and frozen axes")


def _parent_snapshot(parent: FamilyEDonchianParentEvidence) -> tuple[object, ...]:
    return (
        parent.family_a_evidence_sha256,
        parent.parent_slot_id,
        parent.parent_candidate_id,
        parent.publication_sha256,
        parent.series_sha256,
        parent.timeframe,
        parent.direction,
        parent.opportunity_ids_sha256,
        parent.parent_sha256,
        parent.definition,
        parent.opportunities,
    )


def _execution_snapshot(
    execution: FamilyGEDDetectorExecutionEvidence,
) -> tuple[object, ...]:
    return (
        execution.slot,
        execution.definition,
        execution.signals,
        execution.signal_ids_sha256,
        execution.publication_sha256,
        execution.series_sha256,
        execution.parent_sha256,
        execution.work_budget_sha256,
        execution.work_demand,
        execution.d_pseudo_level_references,
        execution.execution_sha256,
        execution.outcome_rows,
        execution.final_access_records,
    )
