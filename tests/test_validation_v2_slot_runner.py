from dataclasses import replace
from datetime import UTC, datetime

import pytest

from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2 import (
    AttachedDevelopmentOutcomeV2,
    SlotRunnerInputsV2,
    apply_global_holm_v2,
    run_slot_roster_v2,
    verify_slot_results_v2,
)

SHA = "a" * 64


def _inputs() -> SlotRunnerInputsV2:
    by_slot = {
        slot.slot_id: (
            AttachedDevelopmentOutcomeV2(
                event_id=f"event-{slot.slot_id}",
                slot_id=slot.slot_id,
                fold_id="outer-1",
                symbol="SOLUSDT",
                timeframe=slot.timeframe,
                timestamp=datetime(2025, 1, 6, tzinfo=UTC),
                net_return=0.0,
                source_partition_sha256=SHA,
                aggregate_row_sha256="b" * 64,
                split_sha256="c" * 64,
                cost_authority_sha256="d" * 64,
            ),
        )
        for slot in VALIDATION_SLOT_ROSTER
    }
    return SlotRunnerInputsV2(
        programme_id="VPV2-" + "1" * 64,
        outcomes_by_slot=by_slot,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        promotion_grade_costs_complete=False,
        precision_available=True,
        runner_version="slot-runner-v2",
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
    assert all(item.decision == "inconclusive" for item in results if item.computation_completed)


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
    results[1] = replace(results[1], attempt_sha256=results[0].attempt_sha256)
    with pytest.raises(ValueError, match="attempt"):
        verify_slot_results_v2(tuple(results), runner_version="slot-runner-v2")
