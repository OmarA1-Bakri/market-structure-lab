from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace

import pytest

from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    VALIDATION_SLOT_ROSTER,
    ValidationSlotKind,
    ValidationTerminalState,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
)
from market_structure_lab.research.statistics import (
    ControlStatistic,
    CostOpportunity,
    EvaluationStatistic,
    FamilyDecisionInput,
    WeeklyVectorObservation,
    aggregate_weekly_vectors,
    bootstrap_weekly_mean,
    classify_family_decision,
    deterministic_validation_seed,
    derive_mde_evidence,
    holm_family_correction,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_weekly_means_use_monday_utc_row_unweighted_units() -> None:
    observations = (
        WeeklyVectorObservation(
            "r1", "APTUSDT", _dt("2026-01-05T00:00:00"), {"candidate": 1.0, "control": 0.0}, SHA_A
        ),
        WeeklyVectorObservation(
            "r2", "APTUSDT", _dt("2026-01-05T01:00:00"), {"candidate": 3.0, "control": 2.0}, SHA_A
        ),
        WeeklyVectorObservation(
            "r3", "SOLUSDT", _dt("2026-01-05T00:00:00"), {"candidate": 5.0, "control": 4.0}, SHA_A
        ),
        WeeklyVectorObservation(
            "r4", "SOLUSDT", _dt("2026-01-12T00:00:00"), {"candidate": 9.0, "control": 8.0}, SHA_A
        ),
        WeeklyVectorObservation(
            "r5", "APTUSDT", _dt("2026-01-12T00:00:00"), {"candidate": 7.0, "control": 6.0}, SHA_A
        ),
        # Sunday belongs to the prior half-open UTC week and is outside the declared fold.
        WeeklyVectorObservation(
            "outside",
            "APTUSDT",
            _dt("2026-01-04T23:00:00"),
            {"candidate": 100.0, "control": 100.0},
            SHA_A,
        ),
    )

    evidence = aggregate_weekly_vectors(
        observations,
        vector_names=("candidate", "control"),
        expected_assets=("APTUSDT", "SOLUSDT"),
        fold_start=_dt("2026-01-05T00:00:00"),
        fold_end=_dt("2026-01-19T00:00:00"),
        source_publication_sha256=SHA_A,
    )

    assert [week.week_start for week in evidence.weeks] == [
        _dt("2026-01-05T00:00:00"),
        _dt("2026-01-12T00:00:00"),
    ]
    # Week values are unweighted across all admissible rows, not equal-weighted by asset.
    assert evidence.weeks[0].values["candidate"] == pytest.approx(3.0)
    assert evidence.weeks[1].values["candidate"] == pytest.approx(8.0)
    assert evidence.vector_estimates["candidate"] == pytest.approx(5.5)
    assert evidence.support == 2


def test_weekly_means_drop_incomplete_or_mismatched_publication_weeks() -> None:
    observations = (
        WeeklyVectorObservation(
            "r1", "APTUSDT", _dt("2026-01-05T00:00:00"), {"candidate": 1.0, "control": 0.0}, SHA_A
        ),
        WeeklyVectorObservation(
            "r2", "SOLUSDT", _dt("2026-01-05T00:00:00"), {"candidate": 2.0, "control": 1.0}, SHA_B
        ),
        WeeklyVectorObservation(
            "r3", "APTUSDT", _dt("2026-01-12T00:00:00"), {"candidate": 3.0, "control": 2.0}, SHA_A
        ),
    )

    evidence = aggregate_weekly_vectors(
        observations,
        vector_names=("candidate", "control"),
        expected_assets=("APTUSDT", "SOLUSDT"),
        fold_start=_dt("2026-01-05T00:00:00"),
        fold_end=_dt("2026-01-19T00:00:00"),
        source_publication_sha256=SHA_A,
    )

    assert evidence.support == 0
    assert "publication" in evidence.excluded_weeks[_dt("2026-01-05T00:00:00")]
    assert "missing asset" in evidence.excluded_weeks[_dt("2026-01-12T00:00:00")]


def test_bootstrap_uses_exact_4096_sha_seeded_draws_and_indices() -> None:
    seed = deterministic_validation_seed(
        programme_id="VP-" + "1" * 64,
        candidate_id="CS-TEST-000001",
        fold_id="outer-1-inner-2",
        purpose="weekly-bootstrap",
    )

    evidence = bootstrap_weekly_mean(
        weekly_values=(0.01, 0.02, -0.01, 0.04),
        seed=seed,
        budget=ValidationWorkBudget(),
        mde=0.1,
        family="A",
        slot_id="VS-0001",
    )
    replay = bootstrap_weekly_mean(
        weekly_values=(0.01, 0.02, -0.01, 0.04),
        seed=seed,
        budget=ValidationWorkBudget(),
        mde=0.1,
        family="A",
        slot_id="VS-0001",
    )

    assert evidence.status is ExecutionStatus.COMPLETED
    assert evidence.decision is ScientificDecision.REJECTED
    assert evidence.draw_count == 4096
    assert evidence.ci_lower_index == 102
    assert evidence.ci_upper_index == 3993
    assert evidence.seed == seed
    assert evidence.sample_algorithm == "sha-index-bootstrap-v1"
    assert evidence.samples_sha256 == replay.samples_sha256
    assert evidence.p_value is not None
    assert 0.0 <= evidence.p_value <= 1.0
    assert evidence.sigma_block is not None and evidence.sigma_block > 0.0


@pytest.mark.parametrize(
    "values, reason",
    [
        ((), "fewer than two"),
        ((0.01,), "fewer than two"),
        ((0.01, 0.01), "zero variance"),
        ((0.01, float("nan")), "non-finite"),
    ],
)
def test_bootstrap_rejects_empty_lt2_zero_variance_and_nonfinite_as_inconclusive(
    values: tuple[float, ...], reason: str
) -> None:
    evidence = bootstrap_weekly_mean(
        weekly_values=values,
        seed=1,
        budget=ValidationWorkBudget(),
        mde=0.001,
        family="A",
        slot_id="VS-0001",
    )

    assert evidence.status is ExecutionStatus.COMPLETED
    assert evidence.decision is ScientificDecision.INCONCLUSIVE
    assert reason in evidence.reason
    assert evidence.p_value is None


def test_bootstrap_budget_and_mde_preflight_fail_before_sampling() -> None:
    with pytest.raises(ValidationWorkBudgetViolation, match="bootstrap_draws must equal"):
        bootstrap_weekly_mean(
            weekly_values=(0.01, 0.02),
            seed=1,
            budget=replace(ValidationWorkBudget(), max_bootstrap_draws=4095),
            mde=0.001,
            family="A",
            slot_id="VS-0001",
        )
    invalid = bootstrap_weekly_mean(
        weekly_values=(0.01, 0.02),
        seed=1,
        budget=ValidationWorkBudget(),
        mde=0.0,
        family="A",
        slot_id="VS-0001",
    )
    assert invalid.terminal_state == ValidationTerminalState(
        ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
    )


def test_mde_uses_identity_bound_max_finite_positive_inner_training_base_cost() -> None:
    evidence = derive_mde_evidence(
        opportunities=(
            CostOpportunity("o1", 0.002, "development", SHA_A),
            CostOpportunity("o2", 0.005, "development", SHA_A),
            CostOpportunity("o3", 0.005, "development", SHA_A),
        ),
        programme_id="VP-" + "2" * 64,
        candidate_id="CS-TEST-000001",
        fold_id="outer-1-inner-training",
    )

    assert evidence.mde == pytest.approx(0.005)
    assert evidence.tied_row_ids == ("o2", "o3")
    assert len(evidence.opportunity_set_sha256) == 64
    assert len(evidence.max_identity_sha256) == 64

    failed = derive_mde_evidence(
        opportunities=(CostOpportunity("bad", 0.0, "development", SHA_A),),
        programme_id="VP-" + "2" * 64,
        candidate_id="CS-TEST-000001",
        fold_id="outer-1-inner-training",
    )
    assert failed.terminal_state == ValidationTerminalState(
        ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
    )
    assert failed.mde is None


def test_holm_sets_unevaluable_primary_slots_to_p_one_and_validates_frozen_roster() -> None:
    a_slots = tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "A" and slot.kind is ValidationSlotKind.CORE and slot.primary
    )
    assert len(a_slots) == 24
    stats = tuple(
        EvaluationStatistic(
            slot_id=slot.slot_id,
            family="A",
            p_value=0.0001 if index == 0 else 0.02,
            status=ExecutionStatus.COMPLETED,
            decision=ScientificDecision.REJECTED,
        )
        for index, slot in enumerate(a_slots[:-1])
    ) + (
        EvaluationStatistic(
            slot_id=a_slots[-1].slot_id,
            family="A",
            p_value=0.000001,
            status=ExecutionStatus.COMPLETED,
            decision=ScientificDecision.INCONCLUSIVE,
        ),
    )

    corrected = holm_family_correction(stats, family="A")

    by_slot = {item.slot_id: item for item in corrected}
    assert by_slot[a_slots[-1].slot_id].effective_p_value == 1.0
    assert by_slot[a_slots[0].slot_id].adjusted_p_value < 0.01
    assert by_slot[a_slots[0].slot_id].rejected is True

    tampered = tuple(
        replace(item, slot_id=f"ARBITRARY-{index:02d}") for index, item in enumerate(stats)
    )
    with pytest.raises(ValueError, match="frozen primary roster"):
        holm_family_correction(tampered, family="A")


def test_family_decisions_use_required_intersection_union_controls() -> None:
    supported = (
        ControlStatistic("naive", 0.001, 0.002, True),
        ControlStatistic("unconditional", 0.002, 0.003, True),
        ControlStatistic("persistence", 0.003, 0.004, True),
    )
    decision = classify_family_decision(
        FamilyDecisionInput(
            family="A",
            controls=supported,
            cost_coverage_complete=True,
            support_sufficient=True,
            interval_width_ok=True,
            robustness_gate=True,
            exposure_gate=True,
            diagnostic_only=False,
        )
    )
    assert decision.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.SUPPORTED_DEVELOPMENT
    )
    assert decision.worst_p_value == pytest.approx(0.003)
    assert decision.worst_lower_bound == pytest.approx(0.002)

    rejected = classify_family_decision(
        replace(
            decision.input,
            controls=(
                supported[0],
                supported[1],
                ControlStatistic("persistence", 0.001, 0.0, True),
            ),
        )
    )
    assert rejected.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.REJECTED
    )

    inconclusive = classify_family_decision(replace(decision.input, controls=supported[:2]))
    assert inconclusive.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
    )


def test_subordinate_families_require_exact_two_controls_and_diagnostics_cannot_promote() -> None:
    controls = (
        ControlStatistic("price_baseline", 0.001, 0.002, True),
        ControlStatistic("structure_only", 0.002, 0.003, True),
    )
    decision = classify_family_decision(
        FamilyDecisionInput(
            family="B",
            controls=controls,
            cost_coverage_complete=True,
            support_sufficient=True,
            interval_width_ok=True,
            robustness_gate=True,
            exposure_gate=True,
            diagnostic_only=False,
        )
    )
    assert decision.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.SUPPORTED_DEVELOPMENT
    )

    diagnostic = classify_family_decision(replace(decision.input, diagnostic_only=True))
    assert diagnostic.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.REJECTED
    )

    missing_policy = classify_family_decision(replace(decision.input, policy_valid=False))
    assert missing_policy.terminal_state == ValidationTerminalState(
        ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
    )
