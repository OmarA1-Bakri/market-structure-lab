from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from market_structure_lab.research import validation_v2_inference as inference_module
from market_structure_lab.research.costs import (
    CostApplication,
    CostCoverageStatus,
    CostEvidence,
    CostEventCoverage,
    CostPolicy,
    CostRate,
    FundingPolicy,
    TradeCostResult,
    freeze_cost_event_coverage_publication,
)
from market_structure_lab.research.controls import ControlOpportunity
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2_inference import (
    CostScenarioEvidenceV2,
    SyntheticPrimaryContrastEvaluationV2,
    RawPrimaryOpportunityRowV2,
    RegisteredSyntheticOpportunityAuthorityV2,
    evaluate_synthetic_primary_contrasts_v2,
    register_synthetic_primary_opportunity_authority_v2,
    verify_synthetic_primary_contrast_evaluation_v2,
    verify_registered_synthetic_opportunity_authority_v2,
)
from market_structure_lab.research.validation_v2_receipts import publish_validation_v2_receipt

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
PROGRAMME_ID = f"VP-{SHA_A}"
START = datetime(2025, 1, 6, tzinfo=UTC)


class HostileRows(list[RawPrimaryOpportunityRowV2]):
    def __len__(self) -> int:
        raise AssertionError("hostile rows were sized")

    def __iter__(self) -> Iterator[RawPrimaryOpportunityRowV2]:
        raise AssertionError("hostile rows were iterated")


class HostileNestedEvidence:
    __dataclass_fields__: dict[str, object] = {"trap": object()}

    @property
    def trap(self) -> object:
        raise AssertionError("hostile nested evidence was accessed")


class HostileCostEvidence:
    @property
    def to_dict(self) -> object:
        raise AssertionError("hostile cost evidence was accessed")


class HostileEnum:
    @property
    def value(self) -> object:
        raise AssertionError("hostile enum was accessed")


class HostileMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise AssertionError("hostile mapping item was accessed")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("hostile mapping was iterated")

    def __len__(self) -> int:
        raise AssertionError("hostile mapping was sized")


class HostileTuple(tuple[object, ...]):
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("hostile nested tuple was iterated")


def _rate(value: float) -> CostRate:
    return CostRate(
        rate=value,
        units="proportion_of_notional",
        provenance="authenticated-test-cost-v1",
        evidence_sha256=SHA_C,
    )


def _rows(
    *,
    component: str = "development",
    week_count: int = 3,
    symbols: tuple[str, ...] = ("SOLUSDT", "ADAUSDT"),
    variation_scale: float = 1.0,
) -> tuple[RawPrimaryOpportunityRowV2, ...]:
    rows: list[RawPrimaryOpportunityRowV2] = []
    for week in range(week_count):
        for asset_index, symbol in enumerate(symbols):
            timestamp = START + timedelta(days=7 * week, hours=1 + asset_index)

            def make_row(role: str, exit_price: float) -> RawPrimaryOpportunityRowV2:
                identity = f"{role}-{week}-{symbol}"
                return RawPrimaryOpportunityRowV2(
                    row_identity=identity,
                    event_id=identity,
                    candidate_id="HC-A-001",
                    family="A",
                    detector_role="moving_average_crossover",
                    detector_parameters=(
                        ("detector", "moving_average_crossover"),
                        ("fast_hours", "24"),
                        ("slow_hours", "72"),
                    ),
                    control_role=role,
                    symbol=symbol,
                    timeframe="1h",
                    fold_id="outer-1/inner-1",
                    utc_week_start=START + timedelta(days=7 * week),
                    direction=1,
                    horizon_hours=24,
                    segment_id=0,
                    feature_time=timestamp - timedelta(hours=2),
                    signal_time=timestamp - timedelta(hours=1),
                    legal_entry_time=timestamp,
                    label_start=timestamp,
                    label_end=timestamp + timedelta(hours=24),
                    programme_id=PROGRAMME_ID,
                    publication_sha256=SHA_B,
                    component=component,
                    block_id=f"week-{week}",
                    venue="binance_spot",
                    entry_price=100.0,
                    exit_price=exit_price,
                    delayed_entry_price=101.0,
                    delayed_exit_price=exit_price,
                    delayed_entry_time=timestamp + timedelta(hours=1),
                    delayed_exit_time=timestamp + timedelta(hours=25),
                    delay_evidence_sha256=SHA_C,
                    prior_completed_close_return=0.01,
                )

            rows.extend(
                (
                    make_row(
                        "candidate",
                        104.0 + variation_scale * (week + asset_index),
                    ),
                    make_row(
                        "unconditional",
                        101.0 + variation_scale * ((0.25 * week) + (0.1 * asset_index)),
                    ),
                    make_row(
                        "persistence",
                        102.0 + variation_scale * ((0.3 * week) + (0.1 * asset_index)),
                    ),
                )
            )
    return tuple(rows)


def _policy(
    rows: tuple[RawPrimaryOpportunityRowV2, ...],
    *,
    omit_event_id: str | None = None,
) -> CostPolicy:
    coverage = tuple(
        CostEventCoverage(
            event_id=row.event_id,
            venue=row.venue,
            symbol=row.symbol,
            timeframe=row.timeframe,
            effective_start=row.label_start - timedelta(minutes=1),
            effective_end=row.delayed_entry_time + timedelta(minutes=1),
            evidence_sha256=SHA_D,
        )
        for row in rows
        if row.event_id != omit_event_id
    )
    publication = freeze_cost_event_coverage_publication(
        coverage,
        source_publication_sha256=SHA_B,
        budget=ValidationWorkBudget(max_events=64),
        demand=ValidationWorkDemand(events=len(coverage)),
    )
    evidence = CostEvidence(
        evidence_id="CE-V2-INFERENCE-001",
        symbol="MULTI",
        timeframe="1h",
        source_publication_sha256=SHA_B,
        entry_fee=_rate(0.001),
        entry_half_spread=_rate(0.0005),
        entry_slippage=_rate(0.0002),
        exit_fee=_rate(0.001),
        exit_half_spread=_rate(0.0005),
        exit_slippage=_rate(0.0002),
        fill_probability=0.8,
        coverage_status=CostCoverageStatus.COMPLETE,
        coverage_reason="complete",
        event_coverage_publication=publication,
    )
    return CostPolicy(
        policy_id="CP-V2-INFERENCE-001",
        base_policy_sha256=SHA_D,
        evidence=evidence,
        funding=FundingPolicy.SPOT_NOT_APPLICABLE,
        delay_model_sha256=SHA_C,
        max_applications=3 * len(rows),
    )


def _authority(
    *, week_count: int = 3, variation_scale: float = 1.0
) -> RegisteredSyntheticOpportunityAuthorityV2:
    rows = _rows(week_count=week_count, variation_scale=variation_scale)
    return register_synthetic_primary_opportunity_authority_v2(
        rows,
        cost_policy=_policy(rows),
        slot_id="VS-0001",
        expected_assets=("ADAUSDT", "SOLUSDT"),
        fold_start=START,
        fold_end=START + timedelta(days=7 * week_count),
        budget=ValidationWorkBudget(
            max_events=max(64, len(rows)),
            max_outcomes=max(64, len(rows)),
        ),
    )


def test_exact_primary_inference_is_deterministic_and_uses_frozen_primitives() -> None:
    authority = _authority()

    first = evaluate_synthetic_primary_contrasts_v2(authority)
    second = evaluate_synthetic_primary_contrasts_v2(authority)

    assert first.sha256 == second.sha256
    assert first.authority_sha256 == authority.sha256
    assert first.cost_policy_assessment.reason == "cost policy complete"
    assert [item.role for item in first.contrasts] == ["naive", "unconditional", "persistence"]
    assert first.naive_selection.reason == "complete"
    assert first.unconditional_selection.reason == "complete"
    assert first.persistence_selection.reason == "complete"
    assert first.weekly_vectors.support == 3
    assert first.mde_evidence.mde == pytest.approx(0.003519)
    assert first.mde_evidence.support == 6
    assert first.cost_scenarios[0].base.expected_net_return == pytest.approx(0.0292256)
    assert first.cost_scenarios[0].stress.entry_cost == pytest.approx(0.0034)
    assert first.cost_scenarios[0].delayed.delay_evidence_sha256 == SHA_C
    assert first.cost_scenarios[0].delayed.gross_return == pytest.approx((104.0 / 101.0) - 1.0)
    assert first.weekly_vectors.weeks[0].values["unconditional"] == pytest.approx(0.02755308)
    assert all(item.bootstrap.p_value is not None for item in first.contrasts)
    assert all(item.bootstrap.ci_lower is not None for item in first.contrasts)
    assert first.inference_evaluable is False
    assert first.inference_state == "completed/inconclusive"
    assert first.inference_reason == "at least one required contrast is inconclusive"
    assert first.iut_p_value is None
    assert first.iut_lower_bound is None
    assert first.holm_effective_p_value == 1.0
    assert first.final_holdout_access_count == 0
    assert authority.authority_scope == "registered_synthetic_oracle_development"
    assert authority.production_eligible is False
    verify_synthetic_primary_contrast_evaluation_v2(first)


def test_known_effect_synthetic_oracle_proves_evaluable_preterminal_iut() -> None:
    evaluation = evaluate_synthetic_primary_contrasts_v2(
        _authority(week_count=8, variation_scale=0.001)
    )

    assert evaluation.inference_evaluable is True
    assert evaluation.inference_state == "completed/evaluable_preterminal"
    assert evaluation.iut_p_value == max(
        cast(float, item.bootstrap.p_value) for item in evaluation.contrasts
    )
    assert evaluation.iut_lower_bound == min(
        cast(float, item.bootstrap.ci_lower) for item in evaluation.contrasts
    )
    assert evaluation.holm_effective_p_value == evaluation.iut_p_value
    assert evaluation.iut_lower_bound is not None
    assert evaluation.iut_lower_bound > 0.0
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)


def test_missing_frozen_asset_is_inconclusive_instead_of_shrinking_the_universe() -> None:
    rows = tuple(row for row in _rows() if row.symbol == "SOLUSDT")
    authority = register_synthetic_primary_opportunity_authority_v2(
        rows,
        cost_policy=_policy(rows),
        slot_id="VS-0001",
        expected_assets=("ADAUSDT", "SOLUSDT"),
        fold_start=START,
        fold_end=START + timedelta(days=21),
        budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
    )

    evaluation = evaluate_synthetic_primary_contrasts_v2(authority)

    assert evaluation.weekly_vectors.support == 0
    assert evaluation.inference_state == "completed/inconclusive"
    assert evaluation.holm_effective_p_value == 1.0


def test_control_shortage_is_sealed_inconclusive_with_effective_p_one() -> None:
    rows = list(_rows())
    rows.remove(next(row for row in rows if row.control_role == "persistence"))
    frozen_rows = tuple(rows)
    authority = register_synthetic_primary_opportunity_authority_v2(
        frozen_rows,
        cost_policy=_policy(frozen_rows),
        slot_id="VS-0001",
        expected_assets=("ADAUSDT", "SOLUSDT"),
        fold_start=START,
        fold_end=START + timedelta(days=21),
        budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
    )

    evaluation = evaluate_synthetic_primary_contrasts_v2(authority)

    assert evaluation.persistence_selection.status.value == "inconclusive"
    assert evaluation.inference_state == "completed/inconclusive"
    assert evaluation.inference_reason == "at least one required control selection is incomplete"
    assert evaluation.holm_effective_p_value == 1.0


def test_synthetic_oracle_rejects_missing_event_cost_coverage_before_registration() -> None:
    rows = _rows()
    missing_event_id = rows[0].event_id

    with pytest.raises(ValueError, match="complete event cost coverage"):
        register_synthetic_primary_opportunity_authority_v2(
            rows,
            cost_policy=_policy(rows, omit_event_id=missing_event_id),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=21),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )


def test_evaluation_rejects_direct_construction_replacement_lookalike_and_nested_mutation() -> None:
    authority = _authority()
    evaluation = evaluate_synthetic_primary_contrasts_v2(authority)
    constructor = {
        name: getattr(evaluation, name)
        for name in evaluation.__dataclass_fields__
        if name not in {"seal", "sha256"}
    }
    with pytest.raises(TypeError, match="evaluator"):
        SyntheticPrimaryContrastEvaluationV2(**constructor)
    with pytest.raises(TypeError, match="evaluator"):
        replace(evaluation)
    lookalike = object.__new__(SyntheticPrimaryContrastEvaluationV2)
    for name in evaluation.__dataclass_fields__:
        if name != "seal":
            object.__setattr__(lookalike, name, getattr(evaluation, name))
    with pytest.raises(ValueError, match="registered original"):
        verify_synthetic_primary_contrast_evaluation_v2(lookalike)

    original_mde = evaluation.mde_evidence.mde
    object.__setattr__(evaluation.mde_evidence, "mde", 999.0)
    with pytest.raises(ValueError, match="evaluation changed"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(evaluation.mde_evidence, "mde", original_mde)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)

    original_mde_evidence = evaluation.mde_evidence
    object.__setattr__(evaluation, "mde_evidence", HostileNestedEvidence())
    with pytest.raises(TypeError, match="exact MdeEvidence"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(evaluation, "mde_evidence", original_mde_evidence)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)

    selection = evaluation.unconditional_selection
    original_records = selection.records
    object.__setattr__(selection, "records", HostileTuple(original_records))
    with pytest.raises(TypeError, match="exact tuple of ControlRecord"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(selection, "records", original_records)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)

    original_kind = selection.kind
    object.__setattr__(selection, "kind", HostileEnum())
    with pytest.raises(TypeError, match="exact ControlKind"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(selection, "kind", original_kind)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)

    record = evaluation.unconditional_selection.records[0]
    original_control_cost = record.control_cost
    object.__setattr__(record, "control_cost", -999.0)
    with pytest.raises(ValueError, match="differs from canonical content"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(record, "control_cost", original_control_cost)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    with pytest.raises(ValueError, match="registered original"):
        verify_synthetic_primary_contrast_evaluation_v2(copy(evaluation))
    original_net = evaluation.cost_scenarios[0].base.expected_net_return
    object.__setattr__(evaluation.cost_scenarios[0].base, "expected_net_return", -1.0)
    with pytest.raises(ValueError, match="evaluation changed"):
        verify_synthetic_primary_contrast_evaluation_v2(evaluation)
    object.__setattr__(evaluation.cost_scenarios[0].base, "expected_net_return", original_net)
    verify_synthetic_primary_contrast_evaluation_v2(evaluation)


def test_synthetic_evaluation_cannot_be_published_as_a_production_receipt(tmp_path: Path) -> None:
    evaluation = evaluate_synthetic_primary_contrasts_v2(_authority())
    receipt_root = tmp_path / "receipts"

    with pytest.raises(TypeError, match="exact factory-issued registered result"):
        publish_validation_v2_receipt(receipt_root, cast(object, evaluation))  # type: ignore[arg-type]

    assert not receipt_root.exists()


def test_evaluation_uses_an_operation_owned_snapshot_during_temporary_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority()
    expected = evaluate_synthetic_primary_contrasts_v2(authority)
    original_function = inference_module._cost_and_control_opportunities
    original_exit = authority.rows[0].exit_price

    def mutate_caller_owned_authority(
        snapshot: RegisteredSyntheticOpportunityAuthorityV2,
    ) -> tuple[tuple[CostScenarioEvidenceV2, ...], tuple[ControlOpportunity, ...]]:
        object.__setattr__(authority.rows[0], "exit_price", 999.0)
        try:
            return original_function(snapshot)
        finally:
            object.__setattr__(authority.rows[0], "exit_price", original_exit)

    monkeypatch.setattr(
        inference_module,
        "_cost_and_control_opportunities",
        mutate_caller_owned_authority,
    )

    observed = evaluate_synthetic_primary_contrasts_v2(authority)

    assert observed.sha256 == expected.sha256
    verify_registered_synthetic_opportunity_authority_v2(authority)
    verify_synthetic_primary_contrast_evaluation_v2(observed)


def test_cost_scenarios_use_base_and_delayed_entry_clocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority()
    original_apply = inference_module.apply_cost_policy
    observed: list[tuple[CostApplication, datetime]] = []

    def record_application(*args: object, **kwargs: object) -> TradeCostResult:
        application = cast(CostApplication, kwargs["application"])
        event_timestamp = cast(datetime, kwargs["event_timestamp"])
        observed.append((application, event_timestamp))
        return original_apply(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(inference_module, "apply_cost_policy", record_application)

    evaluate_synthetic_primary_contrasts_v2(authority)

    first = authority.rows[0]
    assert observed[:3] == [
        (CostApplication.BASE, first.label_start),
        (CostApplication.STRESS, first.label_start),
        (CostApplication.DELAYED, first.delayed_entry_time),
    ]


def test_final_scope_is_rejected_before_cost_application() -> None:
    rows = _rows(component="final_temporal")
    with pytest.raises(ValueError, match="development-only"):
        register_synthetic_primary_opportunity_authority_v2(
            rows,
            cost_policy=_policy(rows),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )


def test_authority_rejects_direct_construction_replacement_copy_and_lookalike() -> None:
    authority = _authority()
    with pytest.raises(TypeError, match="factory"):
        RegisteredSyntheticOpportunityAuthorityV2(
            slot_id=authority.slot_id,
            programme_id=authority.programme_id,
            candidate_id=authority.candidate_id,
            source_publication_sha256=authority.source_publication_sha256,
            fold_id=authority.fold_id,
            expected_assets=authority.expected_assets,
            fold_start=authority.fold_start,
            fold_end=authority.fold_end,
            rows=authority.rows,
            cost_policy=authority.cost_policy,
            budget=authority.budget,
        )
    with pytest.raises(TypeError, match="factory"):
        replace(authority)
    with pytest.raises(ValueError, match="registered original"):
        verify_registered_synthetic_opportunity_authority_v2(copy(authority))
    lookalike = object.__new__(RegisteredSyntheticOpportunityAuthorityV2)
    for name in authority.__dataclass_fields__:
        if name != "seal":
            object.__setattr__(lookalike, name, getattr(authority, name))
    with pytest.raises(ValueError, match="registered original"):
        verify_registered_synthetic_opportunity_authority_v2(lookalike)


def test_authority_detects_retained_row_and_cost_capability_mutation() -> None:
    authority = _authority()
    object.__setattr__(authority, "production_eligible", True)
    with pytest.raises(ValueError, match="production eligible"):
        verify_registered_synthetic_opportunity_authority_v2(authority)
    object.__setattr__(authority, "production_eligible", False)

    row = authority.rows[0]
    original_exit = row.exit_price
    object.__setattr__(row, "exit_price", 999.0)
    with pytest.raises(ValueError, match="rows changed"):
        verify_registered_synthetic_opportunity_authority_v2(authority)
    object.__setattr__(row, "exit_price", original_exit)
    verify_registered_synthetic_opportunity_authority_v2(authority)

    original_evidence = authority.cost_policy.evidence
    object.__setattr__(authority.cost_policy, "evidence", HostileCostEvidence())
    with pytest.raises(TypeError, match="exact CostEvidence"):
        verify_registered_synthetic_opportunity_authority_v2(authority)
    object.__setattr__(authority.cost_policy, "evidence", original_evidence)
    verify_registered_synthetic_opportunity_authority_v2(authority)

    publication = authority.cost_policy.evidence.event_coverage_publication
    assert publication is not None
    coverage = publication.entries[0]
    original_event_id = coverage.event_id
    object.__setattr__(coverage, "event_id", HostileMapping())
    with pytest.raises(ValueError, match="event_id"):
        verify_registered_synthetic_opportunity_authority_v2(authority)
    object.__setattr__(coverage, "event_id", original_event_id)
    verify_registered_synthetic_opportunity_authority_v2(authority)

    original_limit = authority.cost_policy.max_applications
    object.__setattr__(authority.cost_policy, "max_applications", 0)
    with pytest.raises(ValueError, match="cost policy changed"):
        evaluate_synthetic_primary_contrasts_v2(authority)
    object.__setattr__(authority.cost_policy, "max_applications", original_limit)


def test_budget_rejects_declared_rows_before_materialisation() -> None:
    rows = _rows()
    with pytest.raises(ValidationWorkBudgetViolation, match="events exceeds"):
        register_synthetic_primary_opportunity_authority_v2(
            rows,
            cost_policy=_policy(rows),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=1, max_outcomes=1),
        )


def test_exact_container_and_element_types_reject_before_hostile_access() -> None:
    rows = _rows()
    policy = _policy(rows)
    with pytest.raises(TypeError, match="exact bounded"):
        register_synthetic_primary_opportunity_authority_v2(
            HostileRows(rows),
            cost_policy=policy,
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )
    with pytest.raises(TypeError, match="exact RawPrimary"):
        register_synthetic_primary_opportunity_authority_v2(
            cast(list[RawPrimaryOpportunityRowV2], [object()]),
            cost_policy=policy,
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )


def test_authority_replays_raw_row_invariants_before_registration() -> None:
    rows = _rows()
    forged = object.__new__(RawPrimaryOpportunityRowV2)
    for name in rows[0].__dataclass_fields__:
        object.__setattr__(forged, name, getattr(rows[0], name))
    object.__setattr__(forged, "direction", 0)
    forged_rows = (forged, *rows[1:])

    with pytest.raises(ValueError, match="direction"):
        register_synthetic_primary_opportunity_authority_v2(
            forged_rows,
            cost_policy=_policy(forged_rows),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )


def test_authority_rejects_rows_that_differ_from_frozen_vs0001_axes() -> None:
    rows = _rows()
    wrong = replace(
        rows[0],
        horizon_hours=1,
        label_end=rows[0].label_start + timedelta(hours=1),
        delayed_exit_time=rows[0].delayed_entry_time + timedelta(hours=1),
    )
    wrong_rows = (wrong, *rows[1:])

    with pytest.raises(ValueError, match="frozen VS-0001 roster axes"):
        register_synthetic_primary_opportunity_authority_v2(
            wrong_rows,
            cost_policy=_policy(wrong_rows),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=28),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )


def test_authority_rejects_false_utc_week_strata() -> None:
    rows = _rows()
    wrong = object.__new__(RawPrimaryOpportunityRowV2)
    for name in rows[0].__dataclass_fields__:
        object.__setattr__(wrong, name, getattr(rows[0], name))
    object.__setattr__(wrong, "utc_week_start", rows[0].utc_week_start - timedelta(days=7))
    wrong_rows = (wrong, *rows[1:])

    with pytest.raises(ValueError, match="UTC calendar week"):
        register_synthetic_primary_opportunity_authority_v2(
            wrong_rows,
            cost_policy=_policy(wrong_rows),
            slot_id="VS-0001",
            expected_assets=("ADAUSDT", "SOLUSDT"),
            fold_start=START,
            fold_end=START + timedelta(days=21),
            budget=ValidationWorkBudget(max_events=64, max_outcomes=64),
        )
