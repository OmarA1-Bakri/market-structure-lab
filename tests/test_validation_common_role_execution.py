from __future__ import annotations

from collections import Counter
from copy import copy
import inspect
import json

import pytest

from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_common_role_execution import (
    COMMON_CHILD_ROLES,
    CommonRoleOutcomeBlindPlan,
    execute_or_defer_common_role,
    plan_common_role_execution,
    verify_common_role_outcome_blind_plan,
)
from market_structure_lab.research.validation_v2 import (
    SlotRunnerInputsV2,
    ValidationSlotComputationResultV2,
    _issue_fixture_outcome_reader_v2,
    run_slot_roster_v2,
)


@pytest.fixture(scope="module")
def registered_results() -> dict[str, ValidationSlotComputationResultV2]:
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    reader = _issue_fixture_outcome_reader_v2(
        json.dumps(
            {
                "schema_version": "validation-v2-outcome-fixture-v1",
                "slots": {slot_id: [] for slot_id in slot_ids},
            },
            sort_keys=True,
        ).encode(),
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    results = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=True,
            source_available=False,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )
    return {result.slot_id: result for result in results}


def test_all_768_common_children_have_independent_outcome_blind_plans(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    plans = []
    for child in VALIDATION_SLOT_ROSTER:
        if child.role not in COMMON_CHILD_ROLES:
            continue
        assert child.parent_slot_id is not None
        plans.append(
            plan_common_role_execution(
                child,
                parent_result=registered_results[child.parent_slot_id],
                work_budget=ValidationWorkBudget(),
            )
        )

    assert len(plans) == 768
    assert len({plan.child_slot_id for plan in plans}) == 768
    assert Counter(plan.role for plan in plans) == {role: 64 for role in COMMON_CHILD_ROLES}
    assert all(plan.execution_kind == "outcome_blind_plan_only" for plan in plans)
    assert all(plan.source_bound is False for plan in plans)
    assert all(plan.outcome_access_count == 0 for plan in plans)
    assert all(verify_common_role_outcome_blind_plan(plan) is plan for plan in plans)


def test_plan_derives_parent_identity_without_caller_hash_count_or_status_fields(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    child = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.role == "naive")
    assert child.parent_slot_id is not None
    parent = registered_results[child.parent_slot_id]
    plan = plan_common_role_execution(
        child,
        parent_result=parent,
        work_budget=ValidationWorkBudget(),
    )

    assert plan.parent_attempt_sha256 == parent.attempt_sha256
    assert plan.parent_result_sha256 == parent.result_sha256
    assert plan.parent_evidence_sha256 == parent.evidence_sha256
    assert plan.parent_input_sha256 == parent.input_sha256
    parameters = set(inspect.signature(plan_common_role_execution).parameters)
    assert parameters == {"child_slot", "parent_result", "work_budget", "outcome_inputs"}


class _ForgedCallableResult:
    def __call__(self) -> _ForgedCallableResult:
        return self

    slot_id = "VS-0001"
    attempt_sha256 = "a" * 64
    result_sha256 = "b" * 64
    evidence_sha256 = "c" * 64
    input_sha256 = "d" * 64


def test_forged_callable_and_equal_object_parent_attacks_fail_closed(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    child = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.role == "naive")
    assert child.parent_slot_id is not None
    parent = registered_results[child.parent_slot_id]
    with pytest.raises(TypeError, match="exact registered computation result"):
        plan_common_role_execution(
            child,
            parent_result=_ForgedCallableResult(),  # type: ignore[arg-type]
            work_budget=ValidationWorkBudget(),
        )
    with pytest.raises(ValueError, match="registered original"):
        plan_common_role_execution(
            child,
            parent_result=copy(parent),
            work_budget=ValidationWorkBudget(),
        )


def test_wrong_parent_and_outcome_material_are_rejected(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    child = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.role == "naive")
    assert child.parent_slot_id is not None
    wrong_parent = next(
        result for slot_id, result in registered_results.items() if slot_id != child.parent_slot_id
    )
    with pytest.raises(ValueError, match="exact frozen parent"):
        plan_common_role_execution(
            child,
            parent_result=wrong_parent,
            work_budget=ValidationWorkBudget(),
        )
    with pytest.raises(ValueError, match="outcome inputs are forbidden"):
        plan_common_role_execution(
            child,
            parent_result=registered_results[child.parent_slot_id],
            work_budget=ValidationWorkBudget(),
            outcome_inputs=(),
        )


def test_executor_explicitly_defers_without_factory_issued_outcome_material(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    child = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.role == "doubled_cost")
    assert child.parent_slot_id is not None
    plan = plan_common_role_execution(
        child,
        parent_result=registered_results[child.parent_slot_id],
        work_budget=ValidationWorkBudget(),
    )
    deferred = execute_or_defer_common_role(plan)

    assert deferred is plan
    assert isinstance(deferred, CommonRoleOutcomeBlindPlan)
    assert not hasattr(deferred, "p_value")
    assert not hasattr(deferred, "decision")
    with pytest.raises(TypeError, match="factory-issued material executor"):
        execute_or_defer_common_role(plan, material_evidence=object())


def test_capacity_shortage_rule_is_promotion_only(
    registered_results: dict[str, ValidationSlotComputationResultV2],
) -> None:
    capacity = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.role == "capacity_diagnostic")
    assert capacity.parent_slot_id is not None
    plan = plan_common_role_execution(
        capacity,
        parent_result=registered_results[capacity.parent_slot_id],
        work_budget=ValidationWorkBudget(),
    )

    assert plan.promotion_only is True
    assert plan.shortage_execution_status == "completed"
    assert plan.shortage_scientific_state == "unchanged_parent_scientific_decision"
    assert plan.promotion_eligibility_if_shortage == "inconclusive_capacity"
