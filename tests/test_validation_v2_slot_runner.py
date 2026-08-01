from copy import copy, deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2 import (
    SlotRunnerInputsV2,
    ValidationProgrammeRunV2,
    _issue_fixture_outcome_reader_v2,
    _issue_validation_programme_run_v2,
    apply_family_holm_v2,
    run_slot_roster_v2,
    verify_original_validation_programme_run_v2,
    verify_original_validation_slot_result_v2,
    verify_slot_results_v2,
)


def _inputs(
    *, complete_costs: bool = False, precision_available: bool = True
) -> SlotRunnerInputsV2:
    by_slot = {
        slot.slot_id: [
            {
                "event_id": f"event-{slot.slot_id}",
                "timestamp": datetime(2025, 1, 6, tzinfo=UTC).isoformat(),
                "symbol": "SOLUSDT",
                "timeframe": slot.timeframe,
                "entry_price": 100.0,
                "exit_price": 100.0,
                "volume": 10.0,
                "detector_metric": 1.0,
                "partition_role": "inner_train",
            }
        ]
        for slot in VALIDATION_SLOT_ROSTER
    }
    reader = _issue_fixture_outcome_reader_v2(
        json.dumps(
            {"schema_version": "validation-v2-outcome-fixture-v1", "slots": by_slot}, sort_keys=True
        ).encode(),
        programme_id="VPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER),
    )
    return SlotRunnerInputsV2(
        programme_id="VPV2-" + "1" * 64,
        outcome_reader=reader,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        promotion_grade_costs_complete=complete_costs,
        precision_available=precision_available,
        runner_version="slot-runner-v2",
    )


def test_outcome_fixture_rejects_missing_and_extra_slot_keys() -> None:
    payload = json.dumps(
        {"schema_version": "validation-v2-outcome-fixture-v1", "slots": {}}
    ).encode()
    with pytest.raises(ValueError, match="missing|extra"):
        _issue_fixture_outcome_reader_v2(
            payload,
            programme_id="VPV2-" + "1" * 64,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            source_publication_sha256="e" * 64,
            aggregate_publication_sha256="f" * 64,
            expected_slot_ids=("VS-0001",),
        )


def test_exact_fixture_roster_issues_one_distinct_terminal_result_per_slot() -> None:
    results = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    assert len(results) == 1_104
    assert tuple(item.slot_id for item in results) == tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    )
    assert len({item.attempt_sha256 for item in results}) == 1_104
    assert len({item.result_sha256 for item in results}) == 1_104
    assert not any(item.computation_completed for item in results)
    assert all(not item.metrics for item in results)


def test_caller_cost_boolean_cannot_replace_authenticated_primitive_authority() -> None:
    results = run_slot_roster_v2(
        _inputs(complete_costs=True), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    primary = next(item for item in results if item.slot_id == "VS-0001")
    assert primary.execution_status == "failed"
    assert primary.decision == "not_evaluated"
    assert not primary.computation_completed
    assert primary.p_value is None
    assert "exact quantitative primitive inputs are unavailable" in primary.reason


def test_fixture_boolean_cannot_authorize_quantitative_proxy_execution() -> None:
    results = run_slot_roster_v2(
        _inputs(complete_costs=True),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )

    assert all(item.execution_status == "failed" for item in results)
    assert all(item.decision == "not_evaluated" for item in results)
    assert all(not item.computation_completed for item in results)
    assert all(not item.metrics and item.p_value is None for item in results)


def test_family_holm_requires_exact_64_primaries_and_keeps_unevaluable_visible() -> None:
    results = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    adjusted = apply_family_holm_v2(results)
    assert len(adjusted) == 64
    assert {item.alpha for item in adjusted} == {0.01}
    assert {family: sum(item.family == family for item in adjusted) for family in "ABGED"} == {
        "A": 24,
        "B": 8,
        "G": 8,
        "E": 8,
        "D": 16,
    }
    assert all(item.effective_p_value == 1.0 for item in adjusted)
    with pytest.raises(ValueError, match="64"):
        apply_family_holm_v2(results[:-1])


def test_verifier_rejects_fanout_and_wrong_runner() -> None:
    results = list(
        run_slot_roster_v2(_inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand())
    )
    original_attempt = results[1].attempt_sha256
    object.__setattr__(results[1], "attempt_sha256", results[0].attempt_sha256)
    try:
        with pytest.raises(ValueError, match="immutable|attempt"):
            verify_slot_results_v2(tuple(results), runner_version="slot-runner-v2")
    finally:
        object.__setattr__(results[1], "attempt_sha256", original_attempt)

    original_runner = results[1].runner_version
    object.__setattr__(results[1], "runner_version", "wrong-runner-v2")
    try:
        with pytest.raises(ValueError, match="immutable|runner"):
            verify_slot_results_v2(tuple(results), runner_version="slot-runner-v2")
    finally:
        object.__setattr__(results[1], "runner_version", original_runner)


def test_result_factory_rejects_replacement_before_fanout_verification() -> None:
    results = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    with pytest.raises(TypeError, match="factory"):
        replace(results[1], attempt_sha256=results[0].attempt_sha256)


def test_exact_counts_role_dispatch_and_additive_retry_identities() -> None:
    budget = ValidationWorkBudget()
    demand = ValidationWorkDemand()
    first = run_slot_roster_v2(_inputs(precision_available=False), budget=budget, demand=demand)
    b_count = sum(slot.family == "B" for slot in VALIDATION_SLOT_ROSTER)
    assert sum(item.decision == "not_evaluated" for item in first) == 1_104
    assert not any(item.computation_completed for item in first)
    assert sum("historical precision" in item.reason for item in first) == b_count
    assert all("formula" not in item.metrics for item in first)
    attempt_numbers = {slot.slot_id: 2 for slot in VALIDATION_SLOT_ROSTER}
    retry = run_slot_roster_v2(
        _inputs(), budget=budget, demand=demand, attempt_numbers=attempt_numbers
    )
    assert all(item.attempt_number == 2 for item in retry)
    assert not {item.attempt_sha256 for item in first} & {item.attempt_sha256 for item in retry}


def test_validation_programme_run_rejects_direct_construction_before_nested_access() -> None:
    with pytest.raises(TypeError, match="computation factory"):
        ValidationProgrammeRunV2(
            results=(),
            holm=(),
            execution_scope="development-only-full-roster",
            planned_slot_count=1_104,
            executed_slot_count=1_104,
            roster_complete=True,
            scientific_terminal=True,
            execution_status="failed",
            decision="not_evaluated",
            terminal_reason="forged",
            final_holdout_access_count=0,
        )


def test_validation_programme_run_rejects_copy_replacement_and_nested_mutation() -> None:
    results = run_slot_roster_v2(
        _inputs(),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )
    run = _issue_validation_programme_run_v2(
        results=results,
        execution_scope="development-only-full-roster",
        planned_slot_count=1_104,
        executed_slot_count=1_104,
        roster_complete=True,
        scientific_terminal=True,
        execution_status="failed",
        decision="not_evaluated",
        terminal_reason="exact primitives unavailable",
        final_holdout_access_count=0,
    )
    assert verify_original_validation_programme_run_v2(run) is run

    with pytest.raises(ValueError, match="registered original"):
        verify_original_validation_programme_run_v2(copy(run))
    with pytest.raises((TypeError, ValueError)):
        verify_original_validation_programme_run_v2(deepcopy(run))
    with pytest.raises(ValueError, match="registered original"):
        verify_original_validation_programme_run_v2(object.__new__(ValidationProgrammeRunV2))

    original_decision = run.decision
    object.__setattr__(run, "decision", "supported_development")
    try:
        with pytest.raises(ValueError, match="immutable computation"):
            verify_original_validation_programme_run_v2(run)
    finally:
        object.__setattr__(run, "decision", original_decision)

    original_holm = run.holm
    object.__setattr__(run, "holm", (replace(run.holm[0], alpha=0.99), *run.holm[1:]))
    try:
        with pytest.raises(ValueError, match="replaced its registered evidence"):
            verify_original_validation_programme_run_v2(run)
    finally:
        object.__setattr__(run, "holm", original_holm)

    first_holm = run.holm[0]
    original_alpha = first_holm.alpha
    object.__setattr__(first_holm, "alpha", 0.99)
    try:
        with pytest.raises(ValueError, match="immutable computation"):
            verify_original_validation_programme_run_v2(run)
    finally:
        object.__setattr__(first_holm, "alpha", original_alpha)

    first_result = run.results[0]
    original_reason = first_result.reason
    object.__setattr__(first_result, "reason", "forged")
    try:
        with pytest.raises(ValueError):
            verify_original_validation_programme_run_v2(run)
    finally:
        object.__setattr__(first_result, "reason", original_reason)


def test_result_verifier_replays_slot_computation_instead_of_trusting_registered_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    result = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )[0]
    original_compute = module._compute_slot_result_material_v2

    def attacked_compute(**kwargs: object):  # type: ignore[no-untyped-def]
        return replace(original_compute(**kwargs), reason="attacker changed replay material")

    monkeypatch.setattr(
        module,
        "_compute_slot_result_material_v2",
        attacked_compute,
    )

    with pytest.raises(ValueError, match="replay differs"):
        verify_original_validation_slot_result_v2(result)
