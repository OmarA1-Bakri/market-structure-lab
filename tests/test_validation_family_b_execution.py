from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from copy import copy
from dataclasses import replace
from typing import cast

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research import validation_family_b_execution as family_b_module
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    open_verified_aggregate_series_v2,
)
from market_structure_lab.research.models import (
    ValidationSlotKind,
    ValidationWorkBudget,
)
from market_structure_lab.research.validation_family_a_execution import (
    FamilyADetectorExecutionEvidence,
    FamilyASeriesBinding,
    execute_family_a_detector_slots,
    family_a_detector_slots,
)
from market_structure_lab.research.validation_family_b_execution import (
    FamilyAInnerFoldEvaluationEvidence,
    FamilyAInnerFoldSelectionEvidence,
    family_b_detector_slots,
    issue_family_a_inner_fold_evaluation,
    issue_family_a_inner_fold_selection,
    plan_family_b_detector_slot,
    plan_family_b_precision_unavailable,
)
from market_structure_lab.research.validation_v2 import (
    ValidationSlotComputationResultV2,
    _issue_validation_slot_result_v2,
    _result_factory_issuance_snapshot_v2,
)
from market_structure_lab.research import validation_v2 as validation_v2_module
from market_structure_lab.research.validation_v2_models import (
    PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID,
    PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _aggregate_budget() -> AggregatePublicationBudgetV2:
    return AggregatePublicationBudgetV2(
        max_source_rows=100_000,
        max_source_bytes=100_000_000,
        max_parent_partitions=10_000,
        max_source_rows_per_chunk=480,
        max_members=1_000,
        max_aggregate_rows=20_000,
        max_rows_per_partition=31,
        max_output_bytes=100_000_000,
        max_output_files=10_000,
    )


@pytest.fixture(scope="module")
def aggregate_publication(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[AggregatePublicationV2]:
    from test_validation_v2_detector_bridge import aggregate_publication as source_fixture

    source_factory = cast(
        Callable[[pytest.TempPathFactory], Iterator[AggregatePublicationV2]],
        getattr(source_fixture, "__wrapped__"),
    )
    yield from source_factory(tmp_path_factory)


@pytest.fixture(scope="module")
def aggregate_series(aggregate_publication: AggregatePublicationV2) -> VerifiedAggregateSeriesV2:
    key = issue_aggregate_series_key_v2(
        aggregate_publication,
        symbol=aggregate_publication.allowed_symbols[0],
        interval_index=0,
        target_timeframe="1h",
        segment_id=0,
    )
    return open_verified_aggregate_series_v2(aggregate_publication, key, _aggregate_budget())


def _budget(series: VerifiedAggregateSeriesV2) -> ValidationWorkBudget:
    return ValidationWorkBudget(
        max_aggregate_bars=series.row_count,
        max_candidates=48,
        max_events=10_000,
        max_path_cells=100_000_000,
    )


@pytest.fixture(scope="module")
def family_a_evidence(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> FamilyADetectorExecutionEvidence:
    primary_slots = tuple(
        slot
        for slot in family_a_detector_slots("1h")
        if slot.kind is ValidationSlotKind.CORE and slot.primary
    )
    return execute_family_a_detector_slots(
        aggregate_series,
        primary_slots,
        binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
        work_budget=_budget(aggregate_series),
        declared_event_ceiling=10_000,
    )


def _issue_completed_core_result(slot_id: str) -> ValidationSlotComputationResultV2:
    input_sha256 = hash_json("test-family-a-inner-fold-input", slot_id)
    fields: dict[str, object] = {
        "schema_version": "validation-slot-computation-result-v2",
        "programme_id": "TVPV2-" + "1" * 64,
        "slot_id": slot_id,
        "attempt_number": 1,
        "runner_kind": "core",
        "runner_version": "test-family-a-inner-fold-core-v1",
        "attempt_sha256": hash_json(
            "phase5-validation-slot-attempt-v2",
            {"slot_id": slot_id, "input_sha256": input_sha256, "attempt_number": 1},
        ),
        "input_sha256": input_sha256,
        "evidence_sha256": hash_json("test-family-a-inner-fold-evidence", slot_id),
        "parent_attempt_sha256": None,
        "parent_result_sha256": None,
        "execution_status": "completed",
        "decision": "inconclusive",
        "computation_completed": True,
        "reason": "test-owned completed inner-fold core computation",
        "p_value": None,
        "metrics": {"test_owned_inner_fold_core": 1},
    }
    retained_evidence = object()

    def verify_evidence() -> object:
        return retained_evidence

    token = object()
    validation_v2_module._RESULT_FACTORY_ISSUANCE[id(token)] = (  # noqa: SLF001
        token,
        _result_factory_issuance_snapshot_v2(
            evidence_verifier=verify_evidence,
            retained_evidence=retained_evidence,
            computational_parent=None,
            evidence_authority=None,
            evidence_authority_verifier=None,
            receipt_authority_config=None,
            fields=fields,
        ),
    )
    return _issue_validation_slot_result_v2(
        evidence_verifier=verify_evidence,
        retained_evidence=retained_evidence,
        computational_parent=None,
        _computation_factory_token=token,
        **fields,
    )


def _evaluations(
    evidence: FamilyADetectorExecutionEvidence,
) -> tuple[FamilyAInnerFoldEvaluationEvidence, ...]:
    primary = tuple(
        item
        for item in evidence.slots
        if item.slot.kind is ValidationSlotKind.CORE and item.slot.primary
    )
    return tuple(
        issue_family_a_inner_fold_evaluation(
            _issue_completed_core_result(item.slot_id),
            candidate_id=item.definition.candidate_id,
            opportunity_population_sha256=hash_json(
                "phase5-family-a-slot-opportunity-population-v1",
                [signal.signal_id for signal in item.signals],
            ),
            inner_validation_score=float(int(item.slot_id.removeprefix("VS-"))),
            outer_fold_id="outer-01",
            inner_fold_id="inner-01",
            development_split_sha256=SHA_C,
            purge_sha256=SHA_D,
            embargo_sha256=SHA_E,
            source_publication_sha256=evidence.binding.publication_sha256,
            cost_policy_sha256=SHA_F,
            control_policy_sha256=SHA_D,
            statistical_policy_sha256=SHA_E,
            amendment_id=PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID,
            amendment_sha256=PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256,
        )
        for item in primary
    )


def _selection(
    evidence: FamilyADetectorExecutionEvidence,
    series: VerifiedAggregateSeriesV2,
) -> FamilyAInnerFoldSelectionEvidence:
    return issue_family_a_inner_fold_selection(
        evidence,
        _evaluations(evidence),
        work_budget=_budget(series),
    )


def test_frozen_family_b_inventory_covers_48_plannable_slots() -> None:
    slots = family_b_detector_slots("1h") + family_b_detector_slots("4h")
    assert len(slots) == 48
    assert Counter(slot.kind for slot in slots) == {
        ValidationSlotKind.CORE: 32,
        ValidationSlotKind.PERTURBATION: 16,
    }


def test_factory_results_perform_selection_and_bind_full_inner_fold_context(
    family_a_evidence: FamilyADetectorExecutionEvidence,
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    selection = _selection(family_a_evidence, aggregate_series)

    assert selection.verify() is selection
    assert selection.programme_id == "TVPV2-" + "1" * 64
    assert selection.outer_fold_id == "outer-01"
    assert selection.inner_fold_id == "inner-01"
    assert selection.development_split_sha256 == SHA_C
    assert selection.purge_sha256 == SHA_D
    assert selection.embargo_sha256 == SHA_E
    assert selection.source_publication_sha256 == family_a_evidence.binding.publication_sha256
    assert selection.cost_policy_sha256 == SHA_F
    assert selection.control_policy_sha256 == SHA_D
    assert selection.statistical_policy_sha256 == SHA_E
    assert selection.amendment_id == PHASE5_BOOTSTRAP_HOLM_AMENDMENT_ID
    assert selection.amendment_sha256 == PHASE5_BOOTSTRAP_HOLM_AMENDMENT_SHA256
    assert selection.selected_parent_slot_id == "VS-0012"


def test_direct_caller_selection_and_copied_equal_results_are_rejected(
    family_a_evidence: FamilyADetectorExecutionEvidence,
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    evaluations = _evaluations(family_a_evidence)
    forged = (*evaluations[:-1], copy(evaluations[-1]))

    with pytest.raises((TypeError, ValueError), match="factory|registered"):
        issue_family_a_inner_fold_selection(
            family_a_evidence,
            forged,
            work_budget=_budget(aggregate_series),
        )
    assert not hasattr(family_b_module, "bind_family_b_parent_selection")
    assert not hasattr(family_b_module, "family_b_selector_inputs")


def test_v2_profile_path_fails_closed_as_a_plan_not_a_result(
    family_a_evidence: FamilyADetectorExecutionEvidence,
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    selection = _selection(family_a_evidence, aggregate_series)
    slot = next(slot for slot in family_b_detector_slots("1h") if slot.direction == "short")

    plan = plan_family_b_detector_slot(selection, slot, work_budget=_budget(aggregate_series))

    assert plan.readiness == "blocked"
    assert plan.reason == "factory_issued_v2_profile_evidence_unavailable"
    assert plan.required_v2_profile_evidence == "factory-issued-verified-profile-stream-v2"
    assert plan.outcome_rows_emitted == plan.final_access_records == 0
    assert not hasattr(family_b_module, "execute_family_b_detector_slot")


def test_precision_unavailability_is_registered_outcome_blind_instruction_only(
    family_a_evidence: FamilyADetectorExecutionEvidence,
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    selection = _selection(family_a_evidence, aggregate_series)

    plan = plan_family_b_precision_unavailable(selection, work_budget=_budget(aggregate_series))

    assert plan.verify() is plan
    assert len(plan.affected_slot_ids) == 12
    assert len(plan.affected_primary_slot_ids) == 2
    assert "effective_p_one" in plan.later_family_holm_instruction
    assert plan.outcome_rows_emitted == plan.final_access_records == 0
    assert not hasattr(plan, "decision")
    assert not hasattr(plan, "p_value")
    with pytest.raises(TypeError, match="requires its factory"):
        replace(plan)
    with pytest.raises(TypeError, match="caller precision attestation"):
        plan_family_b_precision_unavailable(
            selection,
            work_budget=_budget(aggregate_series),
            precision_evidence={"current_tick_size": "0.01"},
        )
