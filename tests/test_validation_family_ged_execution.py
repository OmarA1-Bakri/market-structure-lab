from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import cast

import pytest

from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    open_verified_aggregate_series_v2,
)
from market_structure_lab.research.candidates import bridge_verified_aggregate_series_v2
from market_structure_lab.research import validation_family_ged_execution as ged_module
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
)
from market_structure_lab.research.validation_family_a_execution import (
    FamilyASeriesBinding,
    execute_family_a_detector_slot,
)
from market_structure_lab.research.validation_family_ged_execution import (
    FamilyGEDPlanStatus,
    bind_family_e_donchian_parent,
    bind_family_ged_adjacent_perturbation_plan,
    execute_family_ged_detector_slot,
    family_ged_detector_slots,
    plan_family_d_pseudo_level_donors,
    plan_family_e_incremental_estimand,
    plan_family_g_intersection_union,
    verify_family_ged_plan,
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


def _budget(series: VerifiedAggregateSeriesV2) -> ValidationWorkBudget:
    return ValidationWorkBudget(
        max_aggregate_bars=series.row_count,
        max_candidates=256,
        max_events=20_000,
    )


def _core_triplet(family: str) -> tuple[ValidationSlot, ValidationSlot, ValidationSlot]:
    roles = {
        "G": ("candidate_primary", "atr_only_control", "donchian_only_control"),
        "E": ("price_only", "volume_filtered_primary", "rate_matched_placebo"),
        "D": ("candidate_primary", "failed_donchian_control", "pseudo_level_control"),
    }[family]
    candidates = [
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == family
        and slot.kind is ValidationSlotKind.CORE
        and slot.timeframe == "1h"
        and slot.direction == "long"
    ]
    first = next(slot for slot in candidates if slot.role == roles[0])
    controls = tuple(
        next(
            slot
            for slot in candidates
            if slot.role == role
            and slot.parameters == first.parameters
            and slot.horizon_hours == first.horizon_hours
        )
        for role in roles[1:]
    )
    return first, controls[0], controls[1]


def test_inventory_covers_all_208_frozen_g_e_d_detector_paths() -> None:
    slots = tuple(
        slot
        for family in ("G", "E", "D")
        for timeframe in ("1h", "4h")
        for slot in family_ged_detector_slots(family, timeframe)
    )

    assert len(slots) == 208
    assert len({slot.slot_id for slot in slots}) == 208
    assert Counter(slot.family for slot in slots) == {"G": 72, "E": 56, "D": 80}
    assert Counter(slot.kind for slot in slots) == {
        ValidationSlotKind.CORE: 96,
        ValidationSlotKind.PERTURBATION: 112,
    }


def test_executes_exact_g_and_d_triplets_and_binds_frozen_plans(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    series = bridge_verified_aggregate_series_v2(aggregate_series)
    budget = _budget(aggregate_series)
    g_slots = _core_triplet("G")
    d_slots = _core_triplet("D")

    g = tuple(
        execute_family_ged_detector_slot(
            series,
            slot,
            work_budget=budget,
            declared_event_ceiling=10_000,
        )
        for slot in g_slots
    )
    d_primary = execute_family_ged_detector_slot(
        series,
        d_slots[0],
        work_budget=budget,
        declared_event_ceiling=10_000,
    )
    d_failed = execute_family_ged_detector_slot(
        series,
        d_slots[1],
        work_budget=budget,
        declared_event_ceiling=10_000,
    )
    d_pseudo = execute_family_ged_detector_slot(
        series,
        d_slots[2],
        work_budget=budget,
        declared_event_ceiling=10_000,
        family_d_primary=d_primary,
    )
    d = (d_primary, d_failed, d_pseudo)
    adjacent_g = execute_family_ged_detector_slot(
        series,
        next(
            slot
            for slot in family_ged_detector_slots("G", "1h")
            if slot.kind is ValidationSlotKind.PERTURBATION
        ),
        work_budget=budget,
        declared_event_ceiling=10_000,
    )
    adjacent_d = execute_family_ged_detector_slot(
        series,
        next(
            slot
            for slot in family_ged_detector_slots("D", "1h")
            if slot.kind is ValidationSlotKind.PERTURBATION
        ),
        work_budget=budget,
        declared_event_ceiling=10_000,
    )

    assert all(item.verify() is item for item in (*g, *d, adjacent_g, adjacent_d))
    assert len({item.execution_sha256 for item in (*g, *d, adjacent_g, adjacent_d)}) == 8
    assert all(
        item.outcome_rows == item.final_access_records == 0
        for item in (*g, *d, adjacent_g, adjacent_d)
    )
    assert adjacent_g.definition.role == "candidate_primary"
    assert adjacent_d.definition.role == "candidate_primary"
    g_plan = plan_family_g_intersection_union(*g)
    assert g_plan.method == "intersection_union_worst_control"
    assert g_plan.verify() is g_plan
    assert g_plan.status in (FamilyGEDPlanStatus.COMPLETE, FamilyGEDPlanStatus.INCONCLUSIVE)
    d_plan = plan_family_d_pseudo_level_donors(*d)
    assert d_plan.confirmation_bars == 1
    assert d_plan.information_cutoff_clock == "breach_bar_plus_one_completed_confirmation"
    assert d_plan.legal_entry_clock == "confirmation_bar_close"
    assert d_plan.delayed_entry_bars == 1
    assert d_plan.donor_rule == ("same_current_event_clock_bound_historical_prior_week_reference")
    assert d_plan.verify() is d_plan
    assert d_plan.shortage_count == 0
    assert len(d[2].d_pseudo_level_references) == len(d[0].signals)
    assert all(
        reference.event_information_cutoff == signal.information_cutoff
        and reference.reference_end_exclusive < reference.event_information_cutoff
        for reference, signal in zip(d[2].d_pseudo_level_references, d[0].signals, strict=True)
    )
    g_adjacent_plan = bind_family_ged_adjacent_perturbation_plan(adjacent_g, g[0], g_plan)
    d_adjacent_plan = bind_family_ged_adjacent_perturbation_plan(adjacent_d, d[0], d_plan)
    assert g_adjacent_plan.parent_slot_id == g[0].slot.slot_id
    assert d_adjacent_plan.parent_slot_id == d[0].slot.slot_id
    assert g_adjacent_plan.verify() is g_adjacent_plan
    assert d_adjacent_plan.verify() is d_adjacent_plan
    with pytest.raises(TypeError, match="requires its factory"):
        replace(g_plan)
    with pytest.raises(TypeError, match="requires its factory"):
        replace(d_plan)
    with pytest.raises(TypeError, match="requires its factory"):
        replace(g_adjacent_plan)
    object.__setattr__(d_plan, "reason", "coherent-forgery")
    object.__setattr__(d_plan, "plan_sha256", ged_module._plan_digest(d_plan))  # noqa: SLF001
    with pytest.raises(ValueError, match="unregistered or mutated"):
        verify_family_ged_plan(d_plan)


def test_family_e_uses_exact_authenticated_a_donchian_population(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    series = bridge_verified_aggregate_series_v2(aggregate_series)
    budget = _budget(aggregate_series)
    e_slots = _core_triplet("E")
    donchian_hours = dict(e_slots[0].parameters)["donchian_hours"]
    a_slot = next(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "A"
        and slot.kind is ValidationSlotKind.CORE
        and slot.primary
        and slot.timeframe == "1h"
        and slot.direction == "long"
        and dict(slot.parameters).get("detector") == "donchian_breakout"
        and dict(slot.parameters).get("lookback_hours") == donchian_hours
    )
    family_a = execute_family_a_detector_slot(
        aggregate_series,
        a_slot,
        binding=FamilyASeriesBinding.from_verified_series(aggregate_series),
        work_budget=budget,
        declared_event_ceiling=10_000,
    )
    parent = bind_family_e_donchian_parent(family_a, a_slot, work_budget=budget)

    executions = tuple(
        execute_family_ged_detector_slot(
            series,
            slot,
            work_budget=budget,
            declared_event_ceiling=10_000,
            family_e_parent=parent,
        )
        for slot in e_slots
    )
    adjacent_slot = next(
        slot
        for slot in family_ged_detector_slots("E", "1h")
        if slot.kind is ValidationSlotKind.PERTURBATION
        and dict(slot.parameters).get("perturbed_parameter") == "volume_median_hours"
        and dict(slot.parameters).get("donchian_hours") == donchian_hours
        and slot.direction == "long"
    )
    adjacent = execute_family_ged_detector_slot(
        series,
        adjacent_slot,
        work_budget=budget,
        declared_event_ceiling=10_000,
        family_e_parent=parent,
    )
    plan = plan_family_e_incremental_estimand(*executions)

    assert parent.verify() is parent
    assert all(item.parent_sha256 == parent.parent_sha256 for item in executions)
    assert all(
        item.definition.parent_a_candidate_id == parent.parent_candidate_id for item in executions
    )
    assert plan.estimand == (
        "volume_filtered_minus_identical_price_only_on_exact_a_donchian_population"
    )
    assert plan.status in (FamilyGEDPlanStatus.COMPLETE, FamilyGEDPlanStatus.INCONCLUSIVE)
    assert plan.shortage_count >= 0
    assert plan.verify() is plan
    assert adjacent.definition.role == "volume_filtered_primary"
    assert adjacent.parent_sha256 == parent.parent_sha256
    adjacent_plan = bind_family_ged_adjacent_perturbation_plan(adjacent, executions[1], plan)
    assert adjacent_plan.parent_slot_id == executions[1].slot.slot_id
    assert adjacent_plan.control_plan_sha256 == plan.plan_sha256
    assert adjacent_plan.verify() is adjacent_plan
    with pytest.raises(TypeError, match="requires its factory"):
        replace(plan)
    with pytest.raises(TypeError, match="requires its factory"):
        replace(adjacent_plan)


def test_rejects_copied_slot_and_preflights_before_detector_iteration(
    aggregate_series: VerifiedAggregateSeriesV2,
) -> None:
    series = bridge_verified_aggregate_series_v2(aggregate_series)
    slot = _core_triplet("G")[0]

    with pytest.raises(TypeError, match="identity-canonical"):
        execute_family_ged_detector_slot(
            series,
            replace(slot),
            work_budget=_budget(aggregate_series),
            declared_event_ceiling=10_000,
        )

    with pytest.raises(ValidationWorkBudgetViolation, match="aggregate_bars exceeds"):
        execute_family_ged_detector_slot(
            series,
            slot,
            work_budget=ValidationWorkBudget(max_aggregate_bars=0),
            declared_event_ceiling=10_000,
        )


def test_adjacent_slots_resolve_exact_parent_detector_roles() -> None:
    for family, expected_role in (
        ("G", "candidate_primary"),
        ("E", "volume_filtered_primary"),
        ("D", "candidate_primary"),
    ):
        slots = tuple(
            slot
            for slot in family_ged_detector_slots(family, "1h")
            if slot.kind is ValidationSlotKind.PERTURBATION
        )
        assert slots
        for slot in slots:
            parent = next(
                canonical
                for canonical in VALIDATION_SLOT_ROSTER
                if canonical.slot_id == slot.parent_slot_id
            )
            assert parent.role == expected_role
            assert parent.primary
            assert parent.kind is ValidationSlotKind.CORE
