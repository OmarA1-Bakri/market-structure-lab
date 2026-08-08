from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    AggregateRowV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    open_verified_aggregate_series_v2,
)
from market_structure_lab.research import validation_family_a_execution as family_a
from market_structure_lab.research.costs import (
    CostApplication,
    CostCoverageStatus,
    CostEvidence,
    CostEventCoveragePublication,
    CostPolicy,
    CostRate,
    FundingPolicy,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_family_a_execution import (
    FamilyAAggregatePathStatus,
    FamilyACostCoverageStatus,
    FamilyADetectorExecutionEvidence,
    FamilyAOutcomeMaterializationPlan,
    FamilyASeriesBinding,
    classify_family_a_cost_coverage,
    execute_family_a_detector_series,
    execute_family_a_detector_slot,
    execute_family_a_detector_slots,
    family_a_detector_slots,
    prepare_family_a_outcome_materialization,
    resolve_family_a_aggregate_path,
)
from market_structure_lab.research.validation_v3_cost_policy import (
    CostPolicyProvenanceKindV3,
    CostPolicyRequestV3,
    load_cost_policy_provenance_v3,
    publish_cost_policy_provenance_v3,
    seal_cost_policy_authority_v3,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


class HostileValidationWorkBudget(ValidationWorkBudget):
    def preflight(
        self,
        demand: ValidationWorkDemand,
        *,
        deferred_work: Iterable[object] | None = None,
        allocation: Callable[[], object] | None = None,
    ) -> None:
        pytest.fail("hostile subclass preflight must never run")


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
def aggregate_series(aggregate_publication: AggregatePublicationV2) -> VerifiedAggregateSeriesV2:
    key = issue_aggregate_series_key_v2(
        aggregate_publication,
        symbol=aggregate_publication.allowed_symbols[0],
        interval_index=0,
        target_timeframe="1h",
        segment_id=0,
    )
    return open_verified_aggregate_series_v2(aggregate_publication, key, _aggregate_budget())


def _budget(
    series: VerifiedAggregateSeriesV2, *, total_signals: int = 20_000
) -> ValidationWorkBudget:
    return ValidationWorkBudget(
        max_aggregate_bars=series.row_count,
        max_candidates=40,
        max_events=total_signals,
        max_path_cells=100_000_000,
    )


@pytest.fixture(scope="module")
def full_detector_evidence(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> FamilyADetectorExecutionEvidence:
    return execute_family_a_detector_series(
        aggregate_series,
        binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
        work_budget=_budget(aggregate_series),
        declared_event_ceiling=10_000,
    )


@pytest.fixture(scope="module")
def outcome_plan(
    aggregate_series: VerifiedAggregateSeriesV2,
    full_detector_evidence: FamilyADetectorExecutionEvidence,
) -> FamilyAOutcomeMaterializationPlan:
    return prepare_family_a_outcome_materialization(
        full_detector_evidence,
        venue="binance_spot",
        work_budget=_budget(aggregate_series),
    )


def _rate(value: float, *, source: str = SHA_A) -> CostRate:
    return CostRate(
        rate=value,
        units="proportion_of_notional",
        provenance="preregistered_conservative_policy_v1",
        evidence_sha256=source,
    )


def _incomplete_cost_policy(
    plan: FamilyAOutcomeMaterializationPlan,
) -> CostPolicy:
    first = plan.items[0]
    publication = CostEventCoveragePublication(
        source_publication_sha256=SHA_B,
        coverage_count=0,
        coverage_digest_sha256=hash_json("cost-event-coverage-publication-entries-v1", []),
        entries=(),
    )
    evidence = CostEvidence(
        evidence_id="CE-FAMILY-A-PLAN",
        symbol=first.symbol,
        timeframe=first.timeframe,
        source_publication_sha256=SHA_B,
        entry_fee=_rate(0.0010),
        entry_half_spread=_rate(0.0005, source=SHA_B),
        entry_slippage=_rate(0.0002, source=SHA_C),
        exit_fee=_rate(0.0015),
        exit_half_spread=_rate(0.0007, source=SHA_B),
        exit_slippage=_rate(0.0003, source=SHA_C),
        fill_probability=0.8,
        coverage_status=CostCoverageStatus.COMPLETE,
        coverage_reason="complete",
        event_coverage_publication=publication,
    )
    return CostPolicy(
        policy_id="CP-FAMILY-A-PLAN",
        base_policy_sha256=SHA_D,
        evidence=evidence,
        funding=FundingPolicy.SPOT_NOT_APPLICABLE,
        stress_multiplier=2.0,
        stress_fill_probability_power=2,
        delay_model_sha256=SHA_E,
        max_applications=len(plan.required_cost_events),
    )


def _cost_authority(
    plan: FamilyAOutcomeMaterializationPlan,
    output_root: Path,
    *,
    required_events: tuple[CostPolicyRequestV3, ...] | None = None,
) -> object:
    policy = _incomplete_cost_policy(plan)
    provenance_identity = publish_cost_policy_provenance_v3(
        policy=policy,
        kind=CostPolicyProvenanceKindV3.PREREGISTERED_POLICY,
        programme_sha256=SHA_A,
        preregistration_sha256=policy.base_policy_sha256,
        review_sha256=SHA_C,
        prior_freeze_evidence_sha256=SHA_E,
        output_root=output_root,
    )
    provenance = load_cost_policy_provenance_v3(
        publication_root=output_root,
        expected_identity=provenance_identity,
        expected_programme_sha256=SHA_A,
    )
    return seal_cost_policy_authority_v3(
        policy=policy,
        provenance=provenance,
        required_events=plan.required_cost_events if required_events is None else required_events,
    )


def _clock_row(
    *,
    timestamp_offset_hours: int,
    segment_id: int,
) -> AggregateRowV2:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=timestamp_offset_hours)
    payload: dict[str, object] = {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": "ADAUSDT",
        "target_timeframe": "1h",
        "interval_index": 0,
        "segment_id": segment_id,
        "open": "1",
        "high": "2",
        "low": "0.5",
        "close": "1.5",
        "volume": "100",
        "source_row_count": 60,
        "source_first_row_sha256": SHA_A,
        "source_last_row_sha256": SHA_B,
        "ordered_source_rows_sha256": SHA_C,
    }
    payload["row_sha256"] = hash_json("phase5-validation-aggregate-row-v2", payload)
    return AggregateRowV2.from_dict(payload)


def test_executes_every_1h_family_a_core_and_adjacent_detector_independently(
    aggregate_series: VerifiedAggregateSeriesV2,
    full_detector_evidence: FamilyADetectorExecutionEvidence,
) -> None:
    work_budget = _budget(aggregate_series)
    evidence = full_detector_evidence

    assert isinstance(evidence, FamilyADetectorExecutionEvidence)
    assert len(evidence.slots) == 40
    assert sum(item.slot.kind.value == "core" for item in evidence.slots) == 12
    assert sum(item.slot.kind.value == "perturbation" for item in evidence.slots) == 28
    assert tuple(item.slot_id for item in evidence.slots) == tuple(
        slot.slot_id for slot in family_a_detector_slots("1h")
    )
    assert len({item.definition.candidate_id for item in evidence.slots}) == 40
    assert evidence.emitted_signal_count == sum(
        item.emitted_signal_count for item in evidence.slots
    )
    assert evidence.binding.series_sha256 == aggregate_series.series_identity
    assert evidence.binding.publication_sha256 == aggregate_series.aggregate_publication_sha256
    assert evidence.budget_sha256 == work_budget.sha256
    assert evidence.work_demand.aggregate_bars == aggregate_series.row_count
    assert evidence.work_demand.candidates == 40
    assert evidence.work_demand.events == 10_000
    assert evidence.work_demand.path_cells == aggregate_series.row_count * 40
    assert evidence.work_demand.trials == evidence.work_demand.evaluations == 1_104
    assert evidence.work_demand.bootstrap_draws == 4_800
    assert all(
        signal.candidate_slot_id == item.slot_id
        and signal.source_series_sha256 == aggregate_series.series_identity
        and signal.segment_id == aggregate_series.key.segment_id
        for item in evidence.slots
        for signal in item.signals
    )


def test_detector_evidence_reopens_and_verifies_original_parents(
    full_detector_evidence: FamilyADetectorExecutionEvidence,
) -> None:
    assert full_detector_evidence.verify() is full_detector_evidence


def test_single_slot_adapter_delegates_exactly_one_canonical_detector_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = family_a_detector_slots("1h")[0]
    aggregate_series = object()
    binding = cast(FamilyASeriesBinding, object())
    work_budget = ValidationWorkBudget()
    expected = object()

    def delegated(
        series: object,
        slots: object,
        **kwargs: object,
    ) -> object:
        assert series is aggregate_series
        assert slots == (slot,)
        assert kwargs["binding"] is binding
        assert kwargs["work_budget"] is work_budget
        assert kwargs["declared_event_ceiling"] == 10_000
        return expected

    monkeypatch.setattr(family_a, "execute_family_a_detector_slots", delegated)
    result = execute_family_a_detector_slot(
        aggregate_series,
        slot,
        binding=binding,
        work_budget=work_budget,
        declared_event_ceiling=10_000,
    )

    assert result is expected


def test_single_slot_adapter_rejects_invalid_slot_before_delegation_or_source_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate_series = object()
    binding = cast(FamilyASeriesBinding, object())
    work_budget = ValidationWorkBudget()
    canonical = family_a_detector_slots("1h")[0]
    non_a = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.family == "B")
    non_detector = next(
        slot for slot in VALIDATION_SLOT_ROSTER if slot.family == "A" and slot.role == "naive"
    )
    forged = replace(canonical)
    monkeypatch.setattr(
        family_a,
        "execute_family_a_detector_slots",
        lambda *_args, **_kwargs: pytest.fail("invalid slot reached bounded execution"),
    )

    for invalid in (non_a, non_detector):
        with pytest.raises(ValueError, match="only A core/adjacent-lookback"):
            execute_family_a_detector_slot(
                aggregate_series,
                invalid,
                binding=binding,
                work_budget=work_budget,
                declared_event_ceiling=10_000,
            )
    for forged_invalid in (forged, object()):
        with pytest.raises(TypeError, match="exact canonical validation slot"):
            execute_family_a_detector_slot(
                aggregate_series,
                forged_invalid,  # type: ignore[arg-type]
                binding=binding,
                work_budget=work_budget,
                declared_event_ceiling=10_000,
            )


def test_budget_subclass_is_rejected_before_detector_or_planning_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = HostileValidationWorkBudget()
    slot = family_a_detector_slots("1h")[0]
    binding = cast(FamilyASeriesBinding, object())
    forged_evidence = object.__new__(FamilyADetectorExecutionEvidence)
    monkeypatch.setattr(
        family_a,
        "execute_family_a_detector_slots",
        lambda *_args, **_kwargs: pytest.fail("single-slot delegation must not run"),
    )
    monkeypatch.setattr(
        family_a,
        "bridge_verified_aggregate_series_v2",
        lambda _series: pytest.fail("detector bridge must not run"),
    )

    with pytest.raises(TypeError, match="exact frozen validation work budget"):
        execute_family_a_detector_slot(
            object(),
            slot,
            binding=binding,
            work_budget=hostile,
            declared_event_ceiling=1,
        )
    with pytest.raises(TypeError, match="exact frozen validation work budget"):
        execute_family_a_detector_series(
            object(),
            binding=binding,
            work_budget=hostile,
            declared_event_ceiling=1,
        )
    with pytest.raises(TypeError, match="exact frozen validation work budget"):
        execute_family_a_detector_slots(
            object(),
            (slot,),
            binding=binding,
            work_budget=hostile,
            declared_event_ceiling=1,
        )
    with pytest.raises(TypeError, match="exact frozen validation work budget"):
        prepare_family_a_outcome_materialization(
            forged_evidence,
            venue="binance_spot",
            work_budget=hostile,
        )


def test_outcome_plan_binds_exact_signal_cost_and_path_metadata_without_rows(
    full_detector_evidence: FamilyADetectorExecutionEvidence,
    outcome_plan: FamilyAOutcomeMaterializationPlan,
) -> None:
    plan = outcome_plan
    slots_by_id = {item.slot_id: item.slot for item in full_detector_evidence.slots}

    assert plan.verify() is plan
    assert len(plan.items) == full_detector_evidence.emitted_signal_count > 0
    assert len(plan.required_cost_events) == sum(
        item.cost_request is not None for item in plan.items
    ) + sum(item.delayed_cost_request is not None for item in plan.items)
    assert plan.work_demand.events == full_detector_evidence.emitted_signal_count * 2
    assert plan.work_demand.outcomes == full_detector_evidence.emitted_signal_count * 2
    assert plan.work_demand.path_cells == full_detector_evidence.emitted_signal_count * 2 * 24 * 60
    assert plan.work_demand.trials == plan.work_demand.evaluations == 1_104
    assert plan.work_demand.bootstrap_draws == 4_800
    assert not hasattr(family_a, "read_verified_minute_path_v2")
    assert not hasattr(family_a, "attach_development_outcome_v2")
    for item in plan.items:
        assert item.horizon_hours == 24
        assert item.horizon_bars == 24
        assert item.expected_minute_rows == 1_440
        if item.path_status is FamilyAAggregatePathStatus.READY:
            assert item.path_end is not None
            assert (item.path_end - item.legal_entry).total_seconds() == 24 * 60 * 60
            assert len(item.aggregate_path_row_sha256) == item.horizon_bars
            assert item.cost_request is not None
            assert item.cost_request.event_id == item.signal_id
            assert item.cost_request.event_timestamp == item.legal_entry
            assert item.cost_request.venue == "binance_spot"
            assert item.cost_request.symbol == item.symbol
            assert item.cost_request.timeframe == item.timeframe
        else:
            assert item.path_end is None
            assert item.aggregate_path_row_sha256 == ()
            assert item.cost_request is None
        if slots_by_id[item.slot_id].primary:
            assert item.delayed_path_status is not None
            if item.delayed_path_status is FamilyAAggregatePathStatus.READY:
                assert item.delayed_cost_request is not None
                assert item.delayed_path_end is not None
                assert item.path_end is not None
                assert (item.delayed_path_end - item.path_end).total_seconds() == 60 * 60
                assert len(item.delayed_aggregate_path_row_sha256) == item.horizon_bars
                assert item.required_applications == (
                    CostApplication.BASE,
                    CostApplication.STRESS,
                    CostApplication.DELAYED,
                )
            else:
                assert item.delayed_cost_request is None
                assert item.delayed_path_end is None
        else:
            assert item.delayed_path_status is None
            assert item.delayed_cost_request is None
            assert item.delayed_path_end is None
            if item.path_status is FamilyAAggregatePathStatus.READY:
                assert item.required_applications == (
                    CostApplication.BASE,
                    CostApplication.STRESS,
                )
        assert item.missed_fill_required is True


def test_aggregate_clock_rejects_gap_and_segment_crossing_without_claiming_end() -> None:
    rows_with_gap = (
        _clock_row(timestamp_offset_hours=0, segment_id=0),
        _clock_row(timestamp_offset_hours=1, segment_id=0),
        _clock_row(timestamp_offset_hours=3, segment_id=0),
        _clock_row(timestamp_offset_hours=4, segment_id=0),
    )
    rows_crossing_segment = (
        _clock_row(timestamp_offset_hours=0, segment_id=0),
        _clock_row(timestamp_offset_hours=1, segment_id=0),
        _clock_row(timestamp_offset_hours=2, segment_id=1),
        _clock_row(timestamp_offset_hours=3, segment_id=1),
    )
    for rows in (rows_with_gap, rows_crossing_segment):
        resolution = resolve_family_a_aggregate_path(
            rows,
            legal_entry=rows_with_gap[1].timestamp,
            symbol="ADAUSDT",
            timeframe="1h",
            interval_index=0,
            segment_id=0,
            horizon_hours=2,
            entry_index=1,
        )
        assert resolution.status is FamilyAAggregatePathStatus.INCOMPLETE
        assert resolution.reason == "aggregate_path_crosses_gap_or_segment_boundary"
        assert resolution.entry_time is resolution.exit_time is None
        assert resolution.aggregate_path_row_sha256 == ()


def test_outcome_plan_preflights_before_detector_verification(
    aggregate_series: VerifiedAggregateSeriesV2,
    full_detector_evidence: FamilyADetectorExecutionEvidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FamilyADetectorExecutionEvidence,
        "verify",
        lambda _self: pytest.fail("detector evidence verified before work preflight"),
    )
    too_small = replace(
        _budget(aggregate_series), max_aggregate_bars=aggregate_series.row_count - 1
    )
    with pytest.raises(ValueError, match="aggregate_bars exceeds"):
        prepare_family_a_outcome_materialization(
            full_detector_evidence,
            venue="binance_spot",
            work_budget=too_small,
        )


def test_outcome_plan_and_cost_authority_fail_closed_when_forged_or_mismatched(
    outcome_plan: FamilyAOutcomeMaterializationPlan,
    tmp_path: Path,
) -> None:
    plan = outcome_plan
    with pytest.raises(TypeError, match="planner factory"):
        FamilyAOutcomeMaterializationPlan(
            detector_evidence_sha256=plan.detector_evidence_sha256,
            work_budget_sha256=plan.work_budget_sha256,
            venue=plan.venue,
            work_demand=plan.work_demand,
            items=plan.items,
            required_cost_events=plan.required_cost_events,
            plan_sha256=plan.plan_sha256,
        )

    wrong_authority = _cost_authority(
        plan,
        tmp_path / "wrong-population",
        required_events=(
            CostPolicyRequestV3(
                event_id="EV-EXTRA",
                event_timestamp=plan.items[0].legal_entry,
                venue=plan.venue,
                symbol=plan.items[0].symbol,
                timeframe=plan.items[0].timeframe,
            ),
        ),
    )
    with pytest.raises(ValueError, match="event population differs"):
        classify_family_a_cost_coverage(plan, wrong_authority)


def test_empty_admissible_cost_population_is_pre_outcome_ready(
    outcome_plan: FamilyAOutcomeMaterializationPlan,
    tmp_path: Path,
) -> None:
    authority = _cost_authority(
        outcome_plan,
        tmp_path / "missing-event-coverage",
    )
    material = classify_family_a_cost_coverage(outcome_plan, authority)

    assert outcome_plan.required_cost_events == ()
    assert material.status is FamilyACostCoverageStatus.READY
    assert "admissible" in material.reason
    assert material.outcome_count == material.path_row_count == 0
    assert material.net_returns == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("symbol", "WRONGUSDT"),
        ("timeframe", "4h"),
        ("interval_index", 999),
        ("segment_id", 999),
        ("publication_sha256", "0" * 64),
        ("series_sha256", "0" * 64),
    ),
)
def test_rejects_wrong_series_binding_before_detector_bridge(
    aggregate_series: VerifiedAggregateSeriesV2,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    binding = replace(
        FamilyASeriesBinding.from_verified_series(aggregate_series),
        **{field: value},  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        family_a,
        "bridge_verified_aggregate_series_v2",
        lambda _series: pytest.fail("detector bridge ran before binding rejection"),
    )

    with pytest.raises(ValueError, match="exact requested binding"):
        execute_family_a_detector_series(
            aggregate_series,
            binding=binding,
            work_budget=_budget(aggregate_series),
            declared_event_ceiling=10_000,
        )


def test_rejects_non_family_a_non_detector_and_wrong_timeframe_slots(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    binding = FamilyASeriesBinding.from_verified_series(aggregate_series)
    work_budget = _budget(aggregate_series)
    non_a = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.family == "B")
    baseline = next(
        slot for slot in VALIDATION_SLOT_ROSTER if slot.family == "A" and slot.role == "naive"
    )
    wrong_timeframe = family_a_detector_slots("4h")[0]

    with pytest.raises(ValueError, match="only A core/adjacent-lookback"):
        execute_family_a_detector_slots(
            aggregate_series,
            (non_a,),
            binding=binding,
            work_budget=work_budget,
            declared_event_ceiling=10_000,
        )
    with pytest.raises(ValueError, match="only A core/adjacent-lookback"):
        execute_family_a_detector_slots(
            aggregate_series,
            (baseline,),
            binding=binding,
            work_budget=work_budget,
            declared_event_ceiling=10_000,
        )
    with pytest.raises(ValueError, match="timeframe"):
        execute_family_a_detector_slots(
            aggregate_series,
            (wrong_timeframe,),
            binding=binding,
            work_budget=work_budget,
            declared_event_ceiling=10_000,
        )
    with pytest.raises(TypeError, match="exact canonical validation slot"):
        execute_family_a_detector_slots(
            aggregate_series,
            (object(),),  # type: ignore[arg-type]
            binding=binding,
            work_budget=work_budget,
            declared_event_ceiling=10_000,
        )


def test_work_bounds_and_outcome_barrier_fire_before_bridge_or_allocation(
    aggregate_series: VerifiedAggregateSeriesV2,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = FamilyASeriesBinding.from_verified_series(aggregate_series)
    monkeypatch.setattr(
        family_a,
        "bridge_verified_aggregate_series_v2",
        lambda _series: pytest.fail("detector bridge ran before preflight rejection"),
    )
    too_small = replace(
        _budget(aggregate_series), max_aggregate_bars=aggregate_series.row_count - 1
    )
    with pytest.raises(ValueError, match="aggregate_bars exceeds"):
        execute_family_a_detector_series(
            aggregate_series,
            binding=binding,
            work_budget=too_small,
            declared_event_ceiling=10_000,
        )
    with pytest.raises(ValueError, match="outcome inputs are forbidden"):
        execute_family_a_detector_series(
            aggregate_series,
            binding=binding,
            work_budget=_budget(aggregate_series),
            declared_event_ceiling=10_000,
            outcome_inputs=("caller-created-outcome",),
        )


def test_emission_ceiling_fails_closed(aggregate_series: VerifiedAggregateSeriesV2) -> None:
    with pytest.raises(ValueError, match="signals exceed|emission exceeds"):
        execute_family_a_detector_series(
            aggregate_series,
            binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
            work_budget=_budget(aggregate_series, total_signals=1),
            declared_event_ceiling=1,
        )


def test_forged_capability_and_evidence_are_rejected(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    with pytest.raises(TypeError, match="factory-issued aggregate"):
        execute_family_a_detector_series(
            object(),
            binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
            work_budget=_budget(aggregate_series),
            declared_event_ceiling=10_000,
        )
    with pytest.raises(TypeError, match="execution factory"):
        FamilyADetectorExecutionEvidence(
            binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
            budget_sha256="0" * 64,
            work_demand=family_a.ValidationWorkDemand(),
            aggregate_identity="AGGV2-" + "0" * 64,
            aggregate_budget_sha256="0" * 64,
            aggregate_row_count=1,
            slots=(),
            emitted_signal_count=0,
            evidence_sha256="0" * 64,
            detector_series=object(),  # type: ignore[arg-type]
        )
