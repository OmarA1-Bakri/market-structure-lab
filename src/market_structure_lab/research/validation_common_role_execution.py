"""Outcome-blind planning boundary for the frozen Phase 5 common child roles.

Only exact registered computation results may bind a parent.  This module does
not accept caller identities, counts, statuses, evidence callbacks, or material
capabilities.  Until a role-specific factory issues exact outcome material, the
public executor deliberately returns the authenticated plan without claiming a
source-bound computation.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from typing import ClassVar
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_roster_execution import (
    ValidationSlotExecutionEntry,
    build_validation_roster_execution_map,
)
from market_structure_lab.research.validation_v2 import (
    ValidationSlotComputationResultV2,
    verify_original_validation_slot_result_v2,
)

COMMON_CHILD_ROLES = frozenset(
    {
        "naive",
        "unconditional",
        "persistence",
        "label_shuffle",
        "one_week_time_shift",
        "random_feature",
        "doubled_cost",
        "one_bar_delay",
        "exclude_strongest_asset",
        "exclude_strongest_utc_year",
        "exposure_residual",
        "capacity_diagnostic",
    }
)
_PLAN_FACTORY = object()
_SLOTS_BY_ID = {slot.slot_id: slot for slot in VALIDATION_SLOT_ROSTER}
_EXECUTION_ENTRIES = {
    entry.slot_id: entry for entry in build_validation_roster_execution_map().slots
}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CommonRoleOutcomeBlindPlan:
    """Authenticated parent binding with no source-bound outcome material."""

    schema_version: ClassVar[str] = "validation-common-role-outcome-blind-plan-v2"
    execution_kind: str
    child_slot_id: str
    parent_slot_id: str
    family: str
    role: str
    parent_attempt_sha256: str
    parent_result_sha256: str
    parent_evidence_sha256: str
    parent_input_sha256: str
    required_capabilities: tuple[str, ...]
    primitive_paths: tuple[str, ...]
    result_factory: str
    work_budget_sha256: str
    work_demand: ValidationWorkDemand
    source_bound: bool
    outcome_access_count: int
    shortage_execution_status: str
    shortage_scientific_state: str
    promotion_only: bool
    promotion_eligibility_if_shortage: str | None
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PLAN_FACTORY:
            raise TypeError("outcome-blind plans require their exact planning factory")

    def verify(self) -> CommonRoleOutcomeBlindPlan:
        return verify_common_role_outcome_blind_plan(self)


@dataclass(frozen=True, slots=True)
class _PlanRegistration:
    reference: weakref.ReferenceType[CommonRoleOutcomeBlindPlan]
    snapshot: tuple[object, ...]
    parent_result: ValidationSlotComputationResultV2
    work_budget: ValidationWorkBudget


_PLANS: dict[int, _PlanRegistration] = {}


def plan_common_role_execution(
    child_slot: ValidationSlot,
    *,
    parent_result: ValidationSlotComputationResultV2,
    work_budget: ValidationWorkBudget,
    outcome_inputs: object | None = None,
) -> CommonRoleOutcomeBlindPlan:
    """Bind an exact parent result and freeze a plan without opening outcomes."""

    if outcome_inputs is not None:
        raise ValueError("outcome inputs are forbidden in outcome-blind common-role planning")
    canonical = _require_common_child(child_slot)
    if type(parent_result) is not ValidationSlotComputationResultV2:
        raise TypeError("parent must be the exact registered computation result")
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("planning requires the exact frozen validation work budget")

    demand = ValidationWorkDemand()
    work_budget.preflight(demand)
    parent = verify_original_validation_slot_result_v2(parent_result)
    if canonical.parent_slot_id != parent.slot_id:
        raise ValueError("common child does not bind its exact frozen parent")

    entry = _entry(canonical)
    promotion_only = canonical.role == "capacity_diagnostic"
    shortage_scientific_state = (
        "unchanged_parent_scientific_decision" if promotion_only else "inconclusive"
    )
    payload = {
        "schema_version": CommonRoleOutcomeBlindPlan.schema_version,
        "execution_kind": "outcome_blind_plan_only",
        "child_slot_id": canonical.slot_id,
        "parent_slot_id": parent.slot_id,
        "family": canonical.family,
        "role": canonical.role,
        "parent_attempt_sha256": parent.attempt_sha256,
        "parent_result_sha256": parent.result_sha256,
        "parent_evidence_sha256": parent.evidence_sha256,
        "parent_input_sha256": parent.input_sha256,
        "required_capabilities": list(entry.required_capabilities),
        "primitive_paths": list(entry.production_primitives),
        "result_factory": entry.receipt_factory,
        "work_budget_sha256": work_budget.sha256,
        "work_demand": _demand_payload(demand),
        "source_bound": False,
        "outcome_access_count": 0,
        "shortage_execution_status": "completed",
        "shortage_scientific_state": shortage_scientific_state,
        "promotion_only": promotion_only,
        "promotion_eligibility_if_shortage": ("inconclusive_capacity" if promotion_only else None),
    }
    plan = CommonRoleOutcomeBlindPlan(
        execution_kind="outcome_blind_plan_only",
        child_slot_id=canonical.slot_id,
        parent_slot_id=parent.slot_id,
        family=canonical.family,
        role=canonical.role,
        parent_attempt_sha256=parent.attempt_sha256,
        parent_result_sha256=parent.result_sha256,
        parent_evidence_sha256=parent.evidence_sha256,
        parent_input_sha256=parent.input_sha256,
        required_capabilities=entry.required_capabilities,
        primitive_paths=entry.production_primitives,
        result_factory=entry.receipt_factory,
        work_budget_sha256=work_budget.sha256,
        work_demand=demand,
        source_bound=False,
        outcome_access_count=0,
        shortage_execution_status="completed",
        shortage_scientific_state=shortage_scientific_state,
        promotion_only=promotion_only,
        promotion_eligibility_if_shortage=("inconclusive_capacity" if promotion_only else None),
        plan_sha256=hash_json("phase5-common-role-outcome-blind-plan-v2", payload),
        _factory_token=_PLAN_FACTORY,
    )
    _register_plan(plan, parent, work_budget)
    return plan


def execute_or_defer_common_role(
    plan: CommonRoleOutcomeBlindPlan,
    *,
    material_evidence: object | None = None,
) -> CommonRoleOutcomeBlindPlan:
    """Return only the plan until an exact role-specific material factory exists.

    Arbitrary material is rejected rather than wrapped in an apparently verified
    capability.  Future source-bound executors must consume their exact factory
    types and invoke the primitive paths frozen in this plan.
    """

    verified = verify_common_role_outcome_blind_plan(plan)
    if material_evidence is not None:
        raise TypeError("material requires a role-specific factory-issued material executor")
    return verified


def verify_common_role_outcome_blind_plan(
    plan: CommonRoleOutcomeBlindPlan,
) -> CommonRoleOutcomeBlindPlan:
    """Reverify the registered plan, exact parent result, roster, and work budget."""

    if type(plan) is not CommonRoleOutcomeBlindPlan:
        raise TypeError("plan must be the exact outcome-blind plan type")
    registration = _PLANS.get(id(plan))
    if registration is None or registration.reference() is not plan:
        raise ValueError("outcome-blind plan is not the registered original")
    if _plan_snapshot(plan) != registration.snapshot:
        raise ValueError("outcome-blind plan differs from its factory snapshot")

    parent = verify_original_validation_slot_result_v2(registration.parent_result)
    canonical = _require_common_child(_SLOTS_BY_ID[plan.child_slot_id])
    entry = _entry(canonical)
    registration.work_budget.preflight(plan.work_demand)
    if (
        plan.parent_slot_id != parent.slot_id
        or canonical.parent_slot_id != parent.slot_id
        or plan.parent_attempt_sha256 != parent.attempt_sha256
        or plan.parent_result_sha256 != parent.result_sha256
        or plan.parent_evidence_sha256 != parent.evidence_sha256
        or plan.parent_input_sha256 != parent.input_sha256
        or plan.required_capabilities != entry.required_capabilities
        or plan.primitive_paths != entry.production_primitives
        or plan.result_factory != entry.receipt_factory
        or plan.execution_kind != "outcome_blind_plan_only"
        or plan.source_bound
        or plan.outcome_access_count != 0
    ):
        raise ValueError("outcome-blind plan differs from its exact frozen dependencies")
    if plan.plan_sha256 != _plan_sha256(plan):
        raise ValueError("outcome-blind plan identity differs")
    return plan


def _entry(slot: ValidationSlot) -> ValidationSlotExecutionEntry:
    return _EXECUTION_ENTRIES[slot.slot_id]


def _require_common_child(slot: ValidationSlot) -> ValidationSlot:
    if type(slot) is not ValidationSlot:
        raise TypeError("slot must be the exact ValidationSlot type")
    canonical = _SLOTS_BY_ID.get(slot.slot_id)
    if canonical is None or canonical is not slot:
        raise ValueError("slot must be the registered canonical slot")
    if canonical.role not in COMMON_CHILD_ROLES or canonical.parent_slot_id is None:
        raise ValueError("slot is not a frozen common child role")
    return canonical


def _demand_payload(demand: ValidationWorkDemand) -> dict[str, int]:
    return {name: getattr(demand, name) for name in demand.__dataclass_fields__}


def _plan_payload(plan: CommonRoleOutcomeBlindPlan) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "execution_kind": plan.execution_kind,
        "child_slot_id": plan.child_slot_id,
        "parent_slot_id": plan.parent_slot_id,
        "family": plan.family,
        "role": plan.role,
        "parent_attempt_sha256": plan.parent_attempt_sha256,
        "parent_result_sha256": plan.parent_result_sha256,
        "parent_evidence_sha256": plan.parent_evidence_sha256,
        "parent_input_sha256": plan.parent_input_sha256,
        "required_capabilities": list(plan.required_capabilities),
        "primitive_paths": list(plan.primitive_paths),
        "result_factory": plan.result_factory,
        "work_budget_sha256": plan.work_budget_sha256,
        "work_demand": _demand_payload(plan.work_demand),
        "source_bound": plan.source_bound,
        "outcome_access_count": plan.outcome_access_count,
        "shortage_execution_status": plan.shortage_execution_status,
        "shortage_scientific_state": plan.shortage_scientific_state,
        "promotion_only": plan.promotion_only,
        "promotion_eligibility_if_shortage": plan.promotion_eligibility_if_shortage,
    }


def _plan_sha256(plan: CommonRoleOutcomeBlindPlan) -> str:
    return hash_json("phase5-common-role-outcome-blind-plan-v2", _plan_payload(plan))


def _plan_snapshot(plan: CommonRoleOutcomeBlindPlan) -> tuple[object, ...]:
    return (
        plan.execution_kind,
        plan.child_slot_id,
        plan.parent_slot_id,
        plan.family,
        plan.role,
        plan.parent_attempt_sha256,
        plan.parent_result_sha256,
        plan.parent_evidence_sha256,
        plan.parent_input_sha256,
        plan.required_capabilities,
        plan.primitive_paths,
        plan.result_factory,
        plan.work_budget_sha256,
        tuple(_demand_payload(plan.work_demand).items()),
        plan.source_bound,
        plan.outcome_access_count,
        plan.shortage_execution_status,
        plan.shortage_scientific_state,
        plan.promotion_only,
        plan.promotion_eligibility_if_shortage,
        plan.plan_sha256,
    )


def _register_plan(
    plan: CommonRoleOutcomeBlindPlan,
    parent_result: ValidationSlotComputationResultV2,
    work_budget: ValidationWorkBudget,
) -> None:
    identifier = id(plan)

    def cleanup(reference: weakref.ReferenceType[CommonRoleOutcomeBlindPlan]) -> None:
        current = _PLANS.get(identifier)
        if current is not None and current.reference is reference:
            _PLANS.pop(identifier, None)

    _PLANS[identifier] = _PlanRegistration(
        reference=weakref.ref(plan, cleanup),
        snapshot=_plan_snapshot(plan),
        parent_result=parent_result,
        work_budget=work_budget,
    )
