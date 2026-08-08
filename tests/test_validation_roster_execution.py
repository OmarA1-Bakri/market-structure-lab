from __future__ import annotations

from collections import Counter
from dataclasses import replace
from importlib import import_module
from typing import cast

import pytest

from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlotKind,
    validation_roster_sha256,
)
from market_structure_lab.research.validation_roster_execution import (
    AdapterStatus,
    ValidationRosterExecutionMap,
    build_validation_roster_execution_map,
)


def _resolve(path: str) -> object:
    module_name, attribute = path.rsplit(".", 1)
    return getattr(import_module(module_name), attribute)


def test_execution_map_covers_the_exact_frozen_roster_in_order() -> None:
    execution_map = build_validation_roster_execution_map()

    assert execution_map.schema_version == "validation-roster-execution-map-v1"
    assert execution_map.roster_sha256 == validation_roster_sha256(VALIDATION_SLOT_ROSTER)
    assert len(execution_map.slots) == 1_104
    assert tuple(entry.slot_id for entry in execution_map.slots) == tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    )
    assert len({entry.slot_id for entry in execution_map.slots}) == 1_104


def test_execution_map_preserves_exact_kind_family_and_role_counts() -> None:
    slots = build_validation_roster_execution_map().slots

    assert Counter(entry.kind for entry in slots) == {
        ValidationSlotKind.CORE: 152,
        ValidationSlotKind.BASELINE: 192,
        ValidationSlotKind.NEGATIVE_CONTROL: 192,
        ValidationSlotKind.ROBUSTNESS: 256,
        ValidationSlotKind.PERTURBATION: 184,
        ValidationSlotKind.EXPOSURE: 64,
        ValidationSlotKind.CAPACITY: 64,
    }
    assert Counter(entry.family for entry in slots) == {
        "A": 368,
        "B": 144,
        "G": 168,
        "E": 152,
        "D": 272,
    }
    assert Counter(entry.role for entry in slots) == Counter(
        slot.role for slot in VALIDATION_SLOT_ROSTER
    )


def test_execution_map_distinguishes_existing_primitives_from_missing_adapters() -> None:
    slots = build_validation_roster_execution_map().slots
    implemented = tuple(
        entry for entry in slots if entry.adapter_status is AdapterStatus.IMPLEMENTED
    )
    missing = tuple(entry for entry in slots if entry.adapter_status is AdapterStatus.MISSING)

    a_detectors = tuple(
        entry
        for entry in slots
        if entry.family == "A"
        and entry.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )
    assert tuple(entry.slot_id for entry in implemented) == ("VS-0001",)
    assert len(a_detectors) == 80
    assert all(
        entry.detector_adapter_path
        == (
            "market_structure_lab.research.validation_family_a_execution."
            "execute_family_a_detector_slot"
        )
        for entry in a_detectors
    )
    assert all(
        entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_family_a_execution."
            "prepare_family_a_outcome_materialization"
        )
        for entry in a_detectors
    )
    assert implemented[0].adapter_path == (
        "market_structure_lab.research.validation_v2._real_vs0001_result_v2"
    )
    assert len(missing) == 1_103
    assert sum(entry.family == "A" for entry in missing) == 367
    assert all(entry.adapter_path is None for entry in missing)
    planned_family_b = tuple(
        entry
        for entry in missing
        if entry.family == "B"
        and entry.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )
    assert len(planned_family_b) == 48
    assert all(entry.adapter_status is AdapterStatus.MISSING for entry in planned_family_b)
    assert all(entry.adapter_path is None for entry in planned_family_b)
    assert all(entry.result_adapter is None for entry in planned_family_b)
    assert all(
        entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_family_b_execution."
            "plan_family_b_detector_slot"
        )
        for entry in planned_family_b
    )
    planned_ged = tuple(
        entry
        for entry in missing
        if entry.family in {"G", "E", "D"}
        and entry.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )
    assert len(planned_ged) == 208
    assert all(entry.adapter_status is AdapterStatus.MISSING for entry in planned_ged)
    assert all(entry.adapter_path is None for entry in planned_ged)
    assert all(entry.result_adapter is None for entry in planned_ged)
    assert all(
        entry.detector_adapter_path
        == (
            "market_structure_lab.research.validation_family_ged_execution."
            "execute_family_ged_detector_slot"
        )
        for entry in planned_ged
    )
    assert all(
        entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_family_ged_execution."
            "bind_family_ged_adjacent_perturbation_plan"
        )
        for entry in planned_ged
        if entry.kind is ValidationSlotKind.PERTURBATION
    )
    expected_core_plans = {
        "G": "plan_family_g_intersection_union",
        "E": "plan_family_e_incremental_estimand",
        "D": "plan_family_d_pseudo_level_donors",
    }
    assert all(
        entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_family_ged_execution."
            f"{expected_core_plans[entry.family]}"
        )
        for entry in planned_ged
        if entry.kind is ValidationSlotKind.CORE
    )
    planned_common = tuple(
        entry
        for entry in missing
        if entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_common_role_execution."
            "plan_common_role_execution"
        )
    )
    assert len(planned_common) == 768
    assert all(
        entry.planning_adapter_path
        == (
            "market_structure_lab.research.validation_common_role_execution."
            "plan_common_role_execution"
        )
        for entry in planned_common
    )
    assert all(
        entry.role
        in {
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
        for entry in planned_common
    )
    assert sum(entry.planning_adapter_path is None for entry in missing) == 0
    assert all(entry.missing_adapter_key for entry in missing)
    assert all(
        entry.missing_adapter_key is not None and "fixture" not in entry.missing_adapter_key
        for entry in missing
    )

    primitive_paths = {primitive for entry in slots for primitive in entry.production_primitives}
    assert primitive_paths
    assert all("fixture" not in path for path in primitive_paths)
    adapter_paths = {
        path
        for entry in slots
        for path in (
            entry.adapter_path,
            entry.detector_adapter_path,
            entry.planning_adapter_path,
            entry.result_adapter,
        )
        if path is not None
    }
    for path in (*primitive_paths, *adapter_paths):
        assert path is not None
        assert callable(_resolve(path))
    vs0001 = next(entry for entry in implemented if entry.slot_id == "VS-0001")
    assert vs0001.result_adapter == (
        "market_structure_lab.research.validation_v2._real_vs0001_result_v2"
    )
    assert all(
        entry.detector_adapter_path is None
        for entry in slots
        if entry not in a_detectors and entry not in planned_ged
    )
    assert all(callable(_resolve(entry.receipt_factory)) for entry in slots)


def test_execution_map_binds_exact_roster_dependencies() -> None:
    slots = build_validation_roster_execution_map().slots
    by_id = {entry.slot_id: entry for entry in slots}
    a_primary_ids = tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER if slot.family == "A" and slot.primary
    )

    for entry in slots:
        frozen = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == entry.slot_id)
        assert entry.parent_slot_id == frozen.parent_slot_id
        if entry.parent_slot_id is not None:
            assert int(entry.parent_slot_id.removeprefix("VS-")) < int(
                entry.slot_id.removeprefix("VS-")
            )
            assert by_id[entry.parent_slot_id].primary
        if entry.kind is ValidationSlotKind.CORE and entry.family != "A":
            assert entry.family_a_identity_slot_ids == a_primary_ids
        else:
            assert entry.family_a_identity_slot_ids == ()
        assert entry.family_prerequisites == (() if entry.family == "A" else ("A",))

    assert all(
        "market_structure_lab.research.validation_v3_cost_policy.apply_verified_cost_policy_v3"
        in entry.production_primitives
        for entry in slots
        if entry.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
        or entry.role in {"doubled_cost", "one_bar_delay"}
    )

    b_core = tuple(
        entry for entry in slots if entry.kind is ValidationSlotKind.CORE and entry.family == "B"
    )
    assert {len(entry.candidate_parent_slot_ids) for entry in b_core} == {6}
    assert all(
        all(
            by_id[parent].family == "A"
            and by_id[parent].timeframe == entry.timeframe
            and by_id[parent].direction == entry.direction
            for parent in entry.candidate_parent_slot_ids
        )
        for entry in b_core
    )

    e_core = tuple(
        entry for entry in slots if entry.kind is ValidationSlotKind.CORE and entry.family == "E"
    )
    assert {len(entry.candidate_parent_slot_ids) for entry in e_core} == {1}
    assert all(
        by_id[entry.candidate_parent_slot_ids[0]].role == "donchian_breakout" for entry in e_core
    )


def test_execution_map_is_canonical_machine_readable_and_identity_bound() -> None:
    first = build_validation_roster_execution_map()
    second = build_validation_roster_execution_map()

    assert first == second
    assert first.sha256 == second.sha256
    assert first.sha256 == "d1c548174f740d96ddb73f87c18f0113af3156abce6a67de78003d86c693016b"
    payload = first.to_dict()
    assert payload["schema_version"] == first.schema_version
    assert payload["roster_sha256"] == first.roster_sha256
    assert payload["execution_map_sha256"] == first.sha256
    payload_slots = cast(list[dict[str, object]], payload["slots"])
    assert len(payload_slots) == 1_104
    assert payload_slots[0]["slot_id"] == "VS-0001"
    assert payload_slots[-1]["slot_id"] == "VS-1104"
    required_execution_fields = {
        "detector_or_control_formula",
        "required_aggregate_timeframe",
        "candidate_population",
        "legal_entry_path",
        "legal_outcome_path",
        "cost_application",
        "statistic",
        "terminal_state_rules",
        "receipt_factory",
        "work_budget_demand",
        "planning_adapter_path",
    }
    assert all(required_execution_fields <= set(item) for item in payload_slots)


def test_execution_map_rejects_self_consistent_noncanonical_slot_metadata() -> None:
    execution_map = build_validation_roster_execution_map()
    forged_slots = (
        replace(execution_map.slots[0], role="forged_role"),
        *execution_map.slots[1:],
    )

    with pytest.raises(ValueError, match="slot metadata is not canonical"):
        ValidationRosterExecutionMap(
            schema_version=execution_map.schema_version,
            roster_sha256=execution_map.roster_sha256,
            slots=forged_slots,
        )
