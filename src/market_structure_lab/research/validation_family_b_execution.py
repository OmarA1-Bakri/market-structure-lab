"""Fail-closed, source-independent planning authority for Family B.

Family B may be planned only after an authenticated inner-fold Family A
computation set performs the frozen selector.  The repository still has no
factory-issued V2 rolling-profile capability, so this module deliberately emits
no Family B detector result, outcome, p-value, decision, or final-access record.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from math import isfinite
from typing import Self, Sequence, cast
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    FROZEN_A_SELECTOR_GRID,
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    validation_roster_sha256,
)
from market_structure_lab.research.validation_family_a_execution import (
    FamilyADetectorExecutionEvidence,
    FamilyADetectorSlotEvidence,
)
from market_structure_lab.research.validation_v2 import (
    ValidationSlotComputationResultV2,
    verify_original_validation_slot_result_v2,
)
from market_structure_lab.research.validation_v2_models import (
    PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID,
    PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256,
)

_SELECTION_FACTORY = object()
_EVALUATION_ISSUANCE: dict[int, object] = {}
_SLOT_PLAN_FACTORY = object()
_PRECISION_PLAN_FACTORY = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyAInnerFoldEvaluationEvidence:
    """One exact core result bound to replayable inner-fold selection context."""

    programme_id: str
    slot_id: str
    core_result_sha256: str
    candidate_id: str
    opportunity_population_sha256: str
    inner_validation_score: float
    outer_fold_id: str
    inner_fold_id: str
    development_split_sha256: str
    purge_sha256: str
    embargo_sha256: str
    source_publication_sha256: str
    cost_policy_sha256: str
    control_policy_sha256: str
    statistical_policy_sha256: str
    amendment_id: str
    amendment_sha256: str
    evaluation_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        token = _EVALUATION_ISSUANCE.pop(id(_factory_token), None)
        if _factory_token is None or token is not _factory_token:
            raise TypeError("Family A inner-fold evaluation requires its computation factory")
        if type(self.inner_validation_score) is not float or not isfinite(
            self.inner_validation_score
        ):
            raise ValueError("inner-validation score must be a finite exact float")

    def verify(self) -> Self:
        return cast(Self, verify_family_a_inner_fold_evaluation(self))


@dataclass(frozen=True, slots=True)
class _EvaluationRegistration:
    evaluation: weakref.ReferenceType[FamilyAInnerFoldEvaluationEvidence]
    core_result: ValidationSlotComputationResultV2
    snapshot: tuple[object, ...]


_EVALUATIONS: dict[int, _EvaluationRegistration] = {}


def issue_family_a_inner_fold_evaluation(
    core_result: object,
    *,
    candidate_id: str,
    opportunity_population_sha256: str,
    inner_validation_score: float,
    outer_fold_id: str,
    inner_fold_id: str,
    development_split_sha256: str,
    purge_sha256: str,
    embargo_sha256: str,
    source_publication_sha256: str,
    cost_policy_sha256: str,
    control_policy_sha256: str,
    statistical_policy_sha256: str,
    amendment_id: str,
    amendment_sha256: str,
) -> FamilyAInnerFoldEvaluationEvidence:
    """Bind replayable selector material to one exact completed A core result."""

    if type(core_result) is not ValidationSlotComputationResultV2:
        raise TypeError("inner-fold evaluation requires an exact core result")
    result = cast(ValidationSlotComputationResultV2, core_result)
    verify_original_validation_slot_result_v2(result)
    slot = next((item for item in VALIDATION_SLOT_ROSTER if item.slot_id == result.slot_id), None)
    if (
        slot is None
        or slot.family != "A"
        or slot.kind is not ValidationSlotKind.CORE
        or not slot.primary
        or result.runner_kind != ValidationSlotKind.CORE.value
    ):
        raise ValueError("inner-fold evaluation requires a frozen primary A core result")
    if result.execution_status != "completed" or not result.computation_completed:
        raise ValueError("inner-fold evaluation requires a completed core computation")
    if type(candidate_id) is not str or not candidate_id:
        raise TypeError("candidate_id must be a non-empty exact string")
    if type(outer_fold_id) is not str or not outer_fold_id:
        raise TypeError("outer_fold_id must be a non-empty exact string")
    if type(inner_fold_id) is not str or not inner_fold_id:
        raise TypeError("inner_fold_id must be a non-empty exact string")
    if type(inner_validation_score) is not float or not isfinite(inner_validation_score):
        raise ValueError("inner-validation score must be a finite exact float")
    for value, label in (
        (opportunity_population_sha256, "opportunity_population_sha256"),
        (development_split_sha256, "development_split_sha256"),
        (purge_sha256, "purge_sha256"),
        (embargo_sha256, "embargo_sha256"),
        (source_publication_sha256, "source_publication_sha256"),
        (cost_policy_sha256, "cost_policy_sha256"),
        (control_policy_sha256, "control_policy_sha256"),
        (statistical_policy_sha256, "statistical_policy_sha256"),
        (amendment_sha256, "amendment_sha256"),
    ):
        _require_sha256(value, label)
    if (
        amendment_id != PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID
        or amendment_sha256 != PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256
    ):
        raise ValueError("inner-fold evaluation must bind MSL-P5-SR-001")
    payload = {
        "programme_id": result.programme_id,
        "slot_id": result.slot_id,
        "core_result_sha256": result.result_sha256,
        "candidate_id": candidate_id,
        "opportunity_population_sha256": opportunity_population_sha256,
        "inner_validation_score": inner_validation_score,
        "outer_fold_id": outer_fold_id,
        "inner_fold_id": inner_fold_id,
        "development_split_sha256": development_split_sha256,
        "purge_sha256": purge_sha256,
        "embargo_sha256": embargo_sha256,
        "source_publication_sha256": source_publication_sha256,
        "cost_policy_sha256": cost_policy_sha256,
        "control_policy_sha256": control_policy_sha256,
        "statistical_policy_sha256": statistical_policy_sha256,
        "amendment_id": amendment_id,
        "amendment_sha256": amendment_sha256,
    }
    token = object()
    _EVALUATION_ISSUANCE[id(token)] = token
    try:
        evaluation = FamilyAInnerFoldEvaluationEvidence(
            **payload,  # type: ignore[arg-type]
            evaluation_sha256=hash_json("phase5-family-a-inner-fold-evaluation-v1", payload),
            _factory_token=token,
        )
    finally:
        _EVALUATION_ISSUANCE.pop(id(token), None)
    identifier = id(evaluation)

    def cleanup(reference: weakref.ReferenceType[FamilyAInnerFoldEvaluationEvidence]) -> None:
        current = _EVALUATIONS.get(identifier)
        if current is not None and current.evaluation is reference:
            _EVALUATIONS.pop(identifier, None)

    _EVALUATIONS[identifier] = _EvaluationRegistration(
        evaluation=weakref.ref(evaluation, cleanup),
        core_result=result,
        snapshot=_evaluation_snapshot(evaluation),
    )
    return evaluation


def verify_family_a_inner_fold_evaluation(
    evaluation: object,
) -> FamilyAInnerFoldEvaluationEvidence:
    """Reject copied/equal evaluations and replay the exact core-result authority."""

    if type(evaluation) is not FamilyAInnerFoldEvaluationEvidence:
        raise TypeError("Family A inner-fold evaluation must be exact and factory-issued")
    typed = cast(FamilyAInnerFoldEvaluationEvidence, evaluation)
    registration = _EVALUATIONS.get(id(typed))
    if (
        registration is None
        or registration.evaluation() is not typed
        or registration.snapshot != _evaluation_snapshot(typed)
    ):
        raise ValueError("Family A inner-fold evaluation is copied, unregistered, or mutated")
    verify_original_validation_slot_result_v2(registration.core_result)
    if typed.core_result_sha256 != registration.core_result.result_sha256:
        raise ValueError("Family A inner-fold evaluation core result differs")
    return typed


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyAInnerFoldSelectionEvidence:
    """Factory-issued frozen selection from complete authenticated A evaluations."""

    programme_id: str
    outer_fold_id: str
    inner_fold_id: str
    development_split_sha256: str
    purge_sha256: str
    embargo_sha256: str
    source_publication_sha256: str
    source_series_sha256: str
    cost_policy_sha256: str
    control_policy_sha256: str
    statistical_policy_sha256: str
    amendment_id: str
    amendment_sha256: str
    selector_grid_sha256: str
    evaluation_result_ids_sha256: str
    opportunity_population_ids_sha256: str
    selected_parent_slot_id: str
    selected_parent_candidate_id: str
    selected_parent_opportunity_population_sha256: str
    timeframe: str
    direction: int
    selection_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SELECTION_FACTORY:
            raise TypeError("Family A inner-fold selection requires its computation factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_a_inner_fold_selection(self))


@dataclass(frozen=True, slots=True)
class _SelectionRegistration:
    selection: weakref.ReferenceType[FamilyAInnerFoldSelectionEvidence]
    family_a: FamilyADetectorExecutionEvidence
    evaluations: tuple[FamilyAInnerFoldEvaluationEvidence, ...]
    budget: ValidationWorkBudget
    snapshot: tuple[object, ...]


_SELECTIONS: dict[int, _SelectionRegistration] = {}


def family_b_detector_slots(timeframe: str) -> tuple[ValidationSlot, ...]:
    """Return all frozen Family B core/adjacent-profile slots for one timeframe."""

    if timeframe not in ("1h", "4h"):
        raise ValueError("Family B detector timeframe must be 1h or 4h")
    return tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "B"
        and slot.timeframe == timeframe
        and slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )


def issue_family_a_inner_fold_selection(
    family_a_evidence: object,
    evaluations: Sequence[FamilyAInnerFoldEvaluationEvidence],
    *,
    work_budget: ValidationWorkBudget,
) -> FamilyAInnerFoldSelectionEvidence:
    """Perform frozen selection over exact factory-issued inner-fold A results."""

    if type(family_a_evidence) is not FamilyADetectorExecutionEvidence:
        raise TypeError("Family B selection requires exact Family A detector evidence")
    if type(evaluations) not in {tuple, list}:
        raise TypeError("Family A evaluations must be an exact bounded sequence")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("Family B selection requires the frozen work budget")
    family_a = cast(FamilyADetectorExecutionEvidence, family_a_evidence)
    declared_count = len(evaluations)
    work_budget.preflight(
        ValidationWorkDemand(candidates=declared_count, evaluations=1_104),
        deferred_work=evaluations,
    )
    family_a.verify()
    primary_items = _complete_primary_a_grid(family_a)
    if declared_count != len(primary_items):
        raise ValueError("Family B selection requires every and only relevant A primary result")
    frozen_evaluations = tuple(evaluations)
    if len({id(item) for item in frozen_evaluations}) != len(frozen_evaluations):
        raise ValueError("Family A evaluation objects must be unique exact originals")
    for evaluation in frozen_evaluations:
        verify_family_a_inner_fold_evaluation(evaluation)

    item_by_slot = {item.slot_id: item for item in primary_items}
    if tuple(item.slot_id for item in frozen_evaluations) != tuple(item_by_slot):
        raise ValueError("Family A evaluations do not match the complete ordered grid")
    programme_ids = {item.programme_id for item in frozen_evaluations}
    if len(programme_ids) != 1:
        raise ValueError("Family A evaluation results must bind one programme")

    common_context: dict[str, str] | None = None
    scored: list[tuple[float, str, FamilyADetectorSlotEvidence]] = []
    result_ids: list[str] = []
    population_ids: list[str] = []
    for evaluation in frozen_evaluations:
        item = item_by_slot[evaluation.slot_id]
        context = {
            "outer_fold_id": evaluation.outer_fold_id,
            "inner_fold_id": evaluation.inner_fold_id,
            "development_split_sha256": evaluation.development_split_sha256,
            "purge_sha256": evaluation.purge_sha256,
            "embargo_sha256": evaluation.embargo_sha256,
            "source_publication_sha256": evaluation.source_publication_sha256,
            "cost_policy_sha256": evaluation.cost_policy_sha256,
            "control_policy_sha256": evaluation.control_policy_sha256,
            "statistical_policy_sha256": evaluation.statistical_policy_sha256,
            "amendment_id": evaluation.amendment_id,
            "amendment_sha256": evaluation.amendment_sha256,
        }
        if common_context is None:
            common_context = context
        elif context != common_context:
            raise ValueError("Family A inner-fold evaluation context differs across the grid")
        if context["source_publication_sha256"] != family_a.binding.publication_sha256:
            raise ValueError("Family A evaluation source differs from detector evidence")
        if (
            context["amendment_id"] != PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID
            or context["amendment_sha256"] != PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256
        ):
            raise ValueError("Family A evaluation does not bind MSL-P5-SR-001")
        candidate_id = evaluation.candidate_id
        population_sha256 = evaluation.opportunity_population_sha256
        expected_population = hash_json(
            "phase5-family-a-slot-opportunity-population-v1",
            [signal.signal_id for signal in item.signals],
        )
        if candidate_id != item.definition.candidate_id or population_sha256 != expected_population:
            raise ValueError("Family A result does not bind its exact opportunity population")
        score = evaluation.inner_validation_score
        scored.append((score, candidate_id, item))
        result_ids.append(evaluation.evaluation_sha256)
        population_ids.append(population_sha256)
    assert common_context is not None
    _, _, selected = max(scored, key=lambda item: (item[0], item[1]))
    selector_grid_sha256 = hash_json(
        "phase5-family-b-complete-a-selector-grid-v1", list(FROZEN_A_SELECTOR_GRID)
    )
    payload = {
        "programme_id": next(iter(programme_ids)),
        **common_context,
        "source_series_sha256": family_a.binding.series_sha256,
        "selector_grid_sha256": selector_grid_sha256,
        "evaluation_result_ids_sha256": hash_json(
            "phase5-family-a-inner-fold-result-order-v1", result_ids
        ),
        "opportunity_population_ids_sha256": hash_json(
            "phase5-family-a-inner-fold-opportunity-populations-v1", population_ids
        ),
        "selected_parent_slot_id": selected.slot_id,
        "selected_parent_candidate_id": selected.definition.candidate_id,
        "selected_parent_opportunity_population_sha256": hash_json(
            "phase5-family-a-slot-opportunity-population-v1",
            [signal.signal_id for signal in selected.signals],
        ),
        "timeframe": selected.slot.timeframe,
        "direction": selected.definition.direction,
        "outcome_rows_emitted": 0,
        "final_access_records": 0,
    }
    selection = FamilyAInnerFoldSelectionEvidence(
        programme_id=cast(str, payload["programme_id"]),
        outer_fold_id=common_context["outer_fold_id"],
        inner_fold_id=common_context["inner_fold_id"],
        development_split_sha256=common_context["development_split_sha256"],
        purge_sha256=common_context["purge_sha256"],
        embargo_sha256=common_context["embargo_sha256"],
        source_publication_sha256=common_context["source_publication_sha256"],
        source_series_sha256=family_a.binding.series_sha256,
        cost_policy_sha256=common_context["cost_policy_sha256"],
        control_policy_sha256=common_context["control_policy_sha256"],
        statistical_policy_sha256=common_context["statistical_policy_sha256"],
        amendment_id=common_context["amendment_id"],
        amendment_sha256=common_context["amendment_sha256"],
        selector_grid_sha256=selector_grid_sha256,
        evaluation_result_ids_sha256=cast(str, payload["evaluation_result_ids_sha256"]),
        opportunity_population_ids_sha256=cast(str, payload["opportunity_population_ids_sha256"]),
        selected_parent_slot_id=selected.slot_id,
        selected_parent_candidate_id=selected.definition.candidate_id,
        selected_parent_opportunity_population_sha256=cast(
            str, payload["selected_parent_opportunity_population_sha256"]
        ),
        timeframe=selected.slot.timeframe,
        direction=selected.definition.direction,
        selection_sha256=hash_json("phase5-family-a-inner-fold-selection-v1", payload),
        _factory_token=_SELECTION_FACTORY,
    )
    identifier = id(selection)

    def cleanup(reference: weakref.ReferenceType[FamilyAInnerFoldSelectionEvidence]) -> None:
        current = _SELECTIONS.get(identifier)
        if current is not None and current.selection is reference:
            _SELECTIONS.pop(identifier, None)

    _SELECTIONS[identifier] = _SelectionRegistration(
        selection=weakref.ref(selection, cleanup),
        family_a=family_a,
        evaluations=frozen_evaluations,
        budget=work_budget,
        snapshot=_selection_snapshot(selection),
    )
    return selection


def verify_family_a_inner_fold_selection(
    selection: object,
) -> FamilyAInnerFoldSelectionEvidence:
    """Reverify exact object identity and every retained computation parent."""

    if type(selection) is not FamilyAInnerFoldSelectionEvidence:
        raise TypeError("Family A inner-fold selection must be exact and factory-issued")
    typed = cast(FamilyAInnerFoldSelectionEvidence, selection)
    registration = _SELECTIONS.get(id(typed))
    if (
        registration is None
        or registration.selection() is not typed
        or registration.snapshot != _selection_snapshot(typed)
    ):
        raise ValueError("Family A inner-fold selection is copied, unregistered, or mutated")
    registration.family_a.verify()
    for evaluation in registration.evaluations:
        verify_family_a_inner_fold_evaluation(evaluation)
    return typed


@dataclass(frozen=True, slots=True)
class FamilyBDetectorSlotPlan:
    """One source-independent slot plan; detector execution remains unavailable."""

    selection_sha256: str
    slot_id: str
    required_v2_profile_evidence: str
    readiness: str
    reason: str
    plan_sha256: str
    outcome_rows_emitted: int
    final_access_records: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SLOT_PLAN_FACTORY:
            raise TypeError("Family B detector slot plan requires its planning factory")
        if self.outcome_rows_emitted or self.final_access_records:
            raise ValueError("Family B planning cannot emit outcomes or final access")


def plan_family_b_detector_slot(
    selection: object,
    slot: object,
    *,
    work_budget: ValidationWorkBudget,
) -> FamilyBDetectorSlotPlan:
    """Bind one exact B slot and fail closed on the absent V2 profile capability."""

    canonical = _require_canonical_family_b_detector_slot(slot)
    if type(selection) is not FamilyAInnerFoldSelectionEvidence:
        raise TypeError("Family B planning requires authenticated inner-fold A selection")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("Family B planning requires the frozen work budget")
    typed = cast(FamilyAInnerFoldSelectionEvidence, selection)
    work_budget.preflight(ValidationWorkDemand(candidates=1), deferred_work=(slot,))
    typed.verify()
    _require_matching_axes(typed, canonical)
    payload = {
        "selection_sha256": typed.selection_sha256,
        "slot_id": canonical.slot_id,
        "required_v2_profile_evidence": "factory-issued-verified-profile-stream-v2",
        "readiness": "blocked",
        "reason": "factory_issued_v2_profile_evidence_unavailable",
        "outcome_rows_emitted": 0,
        "final_access_records": 0,
    }
    return FamilyBDetectorSlotPlan(
        selection_sha256=typed.selection_sha256,
        slot_id=canonical.slot_id,
        required_v2_profile_evidence="factory-issued-verified-profile-stream-v2",
        readiness="blocked",
        reason="factory_issued_v2_profile_evidence_unavailable",
        plan_sha256=hash_json("phase5-family-b-detector-slot-plan-v1", payload),
        outcome_rows_emitted=0,
        final_access_records=0,
        _factory_token=_SLOT_PLAN_FACTORY,
    )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyBPrecisionUnavailablePlan:
    """Outcome-blind instructions for later authenticated B family correction."""

    selection_sha256: str
    programme_id: str
    outer_fold_id: str
    inner_fold_id: str
    development_split_sha256: str
    roster_sha256: str
    amendment_id: str
    amendment_sha256: str
    work_budget_sha256: str
    precision_unavailability_sha256: str
    correction_set_sha256: str
    affected_slot_ids: tuple[str, ...]
    affected_primary_slot_ids: tuple[str, ...]
    later_family_holm_instruction: str
    plan_sha256: str
    outcome_rows_emitted: int
    final_access_records: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PRECISION_PLAN_FACTORY:
            raise TypeError("Family B precision-unavailable plan requires its factory")
        if self.outcome_rows_emitted or self.final_access_records:
            raise ValueError("precision-unavailable planning cannot emit scientific evidence")

    def verify(self) -> Self:
        return cast(Self, verify_family_b_precision_unavailable_plan(self))


@dataclass(frozen=True, slots=True)
class _PrecisionPlanRegistration:
    plan: weakref.ReferenceType[FamilyBPrecisionUnavailablePlan]
    selection: FamilyAInnerFoldSelectionEvidence
    budget: ValidationWorkBudget
    snapshot: tuple[object, ...]


_PRECISION_PLANS: dict[int, _PrecisionPlanRegistration] = {}


def plan_family_b_precision_unavailable(
    selection: object,
    *,
    work_budget: ValidationWorkBudget,
    precision_evidence: object | None = None,
) -> FamilyBPrecisionUnavailablePlan:
    """Freeze every-and-only affected slots; emit no decision or p-value."""

    if precision_evidence is not None:
        raise TypeError(
            "current metadata or caller precision attestation is not historical authority"
        )
    if type(selection) is not FamilyAInnerFoldSelectionEvidence:
        raise TypeError("precision-unavailable planning requires authenticated A selection")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("precision-unavailable planning requires the frozen work budget")
    typed = cast(FamilyAInnerFoldSelectionEvidence, selection)
    affected = tuple(
        slot
        for slot in family_b_detector_slots(typed.timeframe)
        if (1 if slot.direction == "long" else -1) == typed.direction
    )
    work_budget.preflight(
        ValidationWorkDemand(candidates=len(affected), evaluations=1_104),
        deferred_work=affected,
    )
    typed.verify()
    correction_primaries = tuple(
        slot.slot_id
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "B" and slot.kind is ValidationSlotKind.CORE and slot.primary
    )
    affected_primary_ids = tuple(slot.slot_id for slot in affected if slot.primary)
    precision_unavailability_sha256 = hash_json(
        "phase5-family-b-historical-precision-unavailability-v1",
        {
            "source_publication_sha256": typed.source_publication_sha256,
            "development_split_sha256": typed.development_split_sha256,
            "timeframe": typed.timeframe,
            "direction": typed.direction,
            "authority_state": "no_factory_issued_dated_historical_precision",
        },
    )
    payload = {
        "selection_sha256": typed.selection_sha256,
        "programme_id": typed.programme_id,
        "outer_fold_id": typed.outer_fold_id,
        "inner_fold_id": typed.inner_fold_id,
        "development_split_sha256": typed.development_split_sha256,
        "roster_sha256": validation_roster_sha256(VALIDATION_SLOT_ROSTER),
        "amendment_id": typed.amendment_id,
        "amendment_sha256": typed.amendment_sha256,
        "work_budget_sha256": work_budget.sha256,
        "precision_unavailability_sha256": precision_unavailability_sha256,
        "correction_set_sha256": hash_json(
            "phase5-family-b-holm-primary-correction-set-v1", list(correction_primaries)
        ),
        "affected_slot_ids": [slot.slot_id for slot in affected],
        "affected_primary_slot_ids": list(affected_primary_ids),
        "later_family_holm_instruction": (
            "later_authenticated_family_holm_assigns_effective_p_one_to_unevaluable_primaries"
        ),
        "outcome_rows_emitted": 0,
        "final_access_records": 0,
    }
    plan = FamilyBPrecisionUnavailablePlan(
        selection_sha256=typed.selection_sha256,
        programme_id=typed.programme_id,
        outer_fold_id=typed.outer_fold_id,
        inner_fold_id=typed.inner_fold_id,
        development_split_sha256=typed.development_split_sha256,
        roster_sha256=cast(str, payload["roster_sha256"]),
        amendment_id=typed.amendment_id,
        amendment_sha256=typed.amendment_sha256,
        work_budget_sha256=work_budget.sha256,
        precision_unavailability_sha256=precision_unavailability_sha256,
        correction_set_sha256=cast(str, payload["correction_set_sha256"]),
        affected_slot_ids=tuple(slot.slot_id for slot in affected),
        affected_primary_slot_ids=affected_primary_ids,
        later_family_holm_instruction=cast(str, payload["later_family_holm_instruction"]),
        plan_sha256=hash_json("phase5-family-b-precision-unavailable-plan-v1", payload),
        outcome_rows_emitted=0,
        final_access_records=0,
        _factory_token=_PRECISION_PLAN_FACTORY,
    )
    identifier = id(plan)

    def cleanup(reference: weakref.ReferenceType[FamilyBPrecisionUnavailablePlan]) -> None:
        current = _PRECISION_PLANS.get(identifier)
        if current is not None and current.plan is reference:
            _PRECISION_PLANS.pop(identifier, None)

    _PRECISION_PLANS[identifier] = _PrecisionPlanRegistration(
        plan=weakref.ref(plan, cleanup),
        selection=typed,
        budget=work_budget,
        snapshot=_precision_plan_snapshot(plan),
    )
    return plan


def verify_family_b_precision_unavailable_plan(
    plan: object,
) -> FamilyBPrecisionUnavailablePlan:
    """Reject copied/equal plans and reverify the authenticated A selection."""

    if type(plan) is not FamilyBPrecisionUnavailablePlan:
        raise TypeError("Family B precision plan must be exact and factory-issued")
    typed = cast(FamilyBPrecisionUnavailablePlan, plan)
    registration = _PRECISION_PLANS.get(id(typed))
    if (
        registration is None
        or registration.plan() is not typed
        or registration.snapshot != _precision_plan_snapshot(typed)
    ):
        raise ValueError("Family B precision plan is copied, unregistered, or mutated")
    registration.selection.verify()
    if typed.work_budget_sha256 != registration.budget.sha256:
        raise ValueError("Family B precision plan budget identity differs")
    return typed


def _require_sha256(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _complete_primary_a_grid(
    evidence: FamilyADetectorExecutionEvidence,
) -> tuple[FamilyADetectorSlotEvidence, ...]:
    expected = tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "A"
        and slot.timeframe == evidence.binding.timeframe
        and slot.kind is ValidationSlotKind.CORE
        and slot.primary
    )
    observed = tuple(
        item
        for item in evidence.slots
        if item.slot.kind is ValidationSlotKind.CORE and item.slot.primary
    )
    if tuple(item.slot for item in observed) != expected:
        raise ValueError("Family B requires the complete ordered A primary identity grid")
    return observed


def _require_canonical_family_b_detector_slot(slot: object) -> ValidationSlot:
    if type(slot) is not ValidationSlot or not any(
        slot is canonical for canonical in VALIDATION_SLOT_ROSTER
    ):
        raise TypeError("Family B planning requires an exact canonical validation slot")
    typed = cast(ValidationSlot, slot)
    if typed.family != "B" or typed.kind not in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    ):
        raise ValueError("Family B planning accepts only B core or adjacent-profile slots")
    return typed


def _require_matching_axes(
    selection: FamilyAInnerFoldSelectionEvidence,
    slot: ValidationSlot,
) -> None:
    if (
        slot.timeframe != selection.timeframe
        or (1 if slot.direction == "long" else -1) != selection.direction
    ):
        raise ValueError("Family B slot does not match selected A parent axes")


def _selection_snapshot(selection: FamilyAInnerFoldSelectionEvidence) -> tuple[object, ...]:
    return tuple(
        getattr(selection, name)
        for name in selection.__dataclass_fields__
        if name != "_factory_token"
    )


def _evaluation_snapshot(
    evaluation: FamilyAInnerFoldEvaluationEvidence,
) -> tuple[object, ...]:
    return tuple(
        getattr(evaluation, name)
        for name in evaluation.__dataclass_fields__
        if name != "_factory_token"
    )


def _precision_plan_snapshot(plan: FamilyBPrecisionUnavailablePlan) -> tuple[object, ...]:
    return tuple(
        getattr(plan, name) for name in plan.__dataclass_fields__ if name != "_factory_token"
    )


__all__ = [
    "FamilyAInnerFoldEvaluationEvidence",
    "FamilyAInnerFoldSelectionEvidence",
    "FamilyBDetectorSlotPlan",
    "FamilyBPrecisionUnavailablePlan",
    "family_b_detector_slots",
    "issue_family_a_inner_fold_evaluation",
    "issue_family_a_inner_fold_selection",
    "plan_family_b_detector_slot",
    "plan_family_b_precision_unavailable",
    "verify_family_a_inner_fold_evaluation",
    "verify_family_a_inner_fold_selection",
    "verify_family_b_precision_unavailable_plan",
]
