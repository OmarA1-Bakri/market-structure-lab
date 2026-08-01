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
    _issue_fixture_outcome_reader_v2,
    apply_global_holm_v2,
    global_holm_pvalues_v2,
    run_slot_roster_v2,
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


def test_exact_roster_invokes_one_distinct_computation_per_slot() -> None:
    results = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    assert len(results) == 1_104
    assert tuple(item.slot_id for item in results) == tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    )
    assert len({item.attempt_sha256 for item in results}) == 1_104
    assert len({item.result_sha256 for item in results}) == 1_104
    assert all(item.metrics for item in results if item.computation_completed)


def test_statistical_insufficiency_stays_inconclusive_with_complete_costs() -> None:
    results = run_slot_roster_v2(
        _inputs(complete_costs=True), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    primary = next(item for item in results if item.slot_id == "VS-0001")
    assert primary.execution_status == "completed"
    assert primary.decision == "inconclusive"
    assert primary.p_value is None
    assert "fewer than two" in primary.reason


def test_global_holm_requires_exact_64_primaries_and_keeps_unevaluable_visible() -> None:
    results = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )
    adjusted = apply_global_holm_v2(results)
    assert len(adjusted) == 64
    assert {item.alpha for item in adjusted} == {0.05}
    assert all(item.effective_p_value == 1.0 for item in adjusted)
    with pytest.raises(ValueError, match="64"):
        apply_global_holm_v2(results[:-1])


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
    assert sum(item.decision == "not_evaluated" for item in first) == b_count
    assert sum(item.computation_completed for item in first) == 1_104 - b_count
    formulas = {
        item.runner_kind: item.metrics.get("formula")
        for item in first
        if item.computation_completed
    }
    assert len(set(formulas.values())) == len(formulas)
    attempt_numbers = {slot.slot_id: 2 for slot in VALIDATION_SLOT_ROSTER}
    retry = run_slot_roster_v2(
        _inputs(), budget=budget, demand=demand, attempt_numbers=attempt_numbers
    )
    assert all(item.attempt_number == 2 for item in retry)
    assert not {item.attempt_sha256 for item in first} & {item.attempt_sha256 for item in retry}


def test_global_holm_known_boundary_ties_and_invalid_values() -> None:
    primary_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER if slot.primary)
    boundary = 0.05 / 64
    pvalues = {slot_id: 1.0 for slot_id in primary_ids}
    pvalues[primary_ids[0]] = boundary
    pvalues[primary_ids[1]] = boundary
    pvalues[primary_ids[2]] = None
    adjusted = global_holm_pvalues_v2(pvalues)
    assert adjusted[0].adjusted_p_value == pytest.approx(0.05)
    assert adjusted[1].adjusted_p_value == pytest.approx(0.05)
    assert adjusted[0].rejected and adjusted[1].rejected
    assert adjusted[2].effective_p_value == 1.0 and not adjusted[2].evaluable
    ordered = sorted(adjusted, key=lambda item: (item.effective_p_value, item.slot_id))
    assert [item.adjusted_p_value for item in ordered] == sorted(
        item.adjusted_p_value for item in ordered
    )
    for invalid in (float("nan"), float("inf"), -0.1, 1.1):
        attacked = dict(pvalues)
        attacked[primary_ids[0]] = invalid
        with pytest.raises(ValueError, match="finite|\\[0, 1\\]"):
            global_holm_pvalues_v2(attacked)


def test_result_verifier_replays_slot_computation_instead_of_trusting_registered_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    result = run_slot_roster_v2(
        _inputs(), budget=ValidationWorkBudget(), demand=ValidationWorkDemand()
    )[0]
    monkeypatch.setattr(
        module,
        "_role_metrics",
        lambda *_args, **_kwargs: ({"attacker_fabricated": 1}, 0.0),
    )

    with pytest.raises(ValueError, match="replay differs"):
        verify_original_validation_slot_result_v2(result)
