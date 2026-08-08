"""Exact source-independent execution map for the frozen validation roster."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    freeze_validation_slot_roster,
    validation_roster_sha256,
)

_SCHEMA_VERSION = "validation-roster-execution-map-v1"
_VS0001_ADAPTER = "market_structure_lab.research.validation_v2._real_vs0001_result_v2"
_FAMILY_A_DETECTOR_ADAPTER = (
    "market_structure_lab.research.validation_family_a_execution.execute_family_a_detector_slot"
)
_FAMILY_A_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_a_execution."
    "prepare_family_a_outcome_materialization"
)
_FAMILY_B_DETECTOR_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_b_execution.plan_family_b_detector_slot"
)
_FAMILY_GED_DETECTOR_ADAPTER = (
    "market_structure_lab.research.validation_family_ged_execution.execute_family_ged_detector_slot"
)
_FAMILY_G_CORE_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_ged_execution.plan_family_g_intersection_union"
)
_FAMILY_E_CORE_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_ged_execution."
    "plan_family_e_incremental_estimand"
)
_FAMILY_D_CORE_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_ged_execution."
    "plan_family_d_pseudo_level_donors"
)
_FAMILY_GED_ADJACENT_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_family_ged_execution."
    "bind_family_ged_adjacent_perturbation_plan"
)
_COMMON_ROLE_PLANNING_ADAPTER = (
    "market_structure_lab.research.validation_common_role_execution.plan_common_role_execution"
)

_CORE_PRIMITIVES = (
    "market_structure_lab.research.models.candidate_definition_for_slot",
    "market_structure_lab.research.candidates.detect_candidate_signals",
    "market_structure_lab.research.outcomes.attach_development_outcome_v2",
    "market_structure_lab.research.validation_v3_cost_policy.apply_verified_cost_policy_v3",
    "market_structure_lab.research.statistics.aggregate_weekly_vectors",
    "market_structure_lab.research.statistics.bootstrap_weekly_mean",
)
_INFERENCE_PRIMITIVES = (
    "market_structure_lab.research.statistics.aggregate_weekly_vectors",
    "market_structure_lab.research.statistics.bootstrap_weekly_mean",
)
_ROLE_PRIMITIVES: Mapping[str, tuple[str, ...]] = {
    "naive": (
        "market_structure_lab.research.controls.build_naive_zero_controls",
        *_INFERENCE_PRIMITIVES,
    ),
    "unconditional": (
        "market_structure_lab.research.controls.build_legal_donor_pool_evidence",
        "market_structure_lab.research.controls.select_stratified_controls",
        *_INFERENCE_PRIMITIVES,
    ),
    "persistence": (
        "market_structure_lab.research.controls.build_persistence_controls",
        *_INFERENCE_PRIMITIVES,
    ),
    "label_shuffle": (
        "market_structure_lab.research.controls.shuffle_labels_within_legal_strata",
        *_INFERENCE_PRIMITIVES,
    ),
    "one_week_time_shift": (
        "market_structure_lab.research.controls.shift_opportunities_one_utc_week",
        *_INFERENCE_PRIMITIVES,
    ),
    "random_feature": (
        "market_structure_lab.research.controls.random_feature_selection",
        *_INFERENCE_PRIMITIVES,
    ),
    "doubled_cost": (
        "market_structure_lab.research.validation_v3_cost_policy.apply_verified_cost_policy_v3",
        *_INFERENCE_PRIMITIVES,
    ),
    "one_bar_delay": (
        "market_structure_lab.research.validation_v3_cost_policy.apply_verified_cost_policy_v3",
        *_INFERENCE_PRIMITIVES,
    ),
    "exclude_strongest_asset": (
        "market_structure_lab.research.robustness.select_ex_strongest_development_exclusions",
        *_INFERENCE_PRIMITIVES,
    ),
    "exclude_strongest_utc_year": (
        "market_structure_lab.research.robustness.select_ex_strongest_development_exclusions",
        *_INFERENCE_PRIMITIVES,
    ),
    "exposure_residual": ("market_structure_lab.research.robustness.evaluate_exposure_ols",),
    "capacity_diagnostic": (
        "market_structure_lab.research.robustness.evaluate_capacity_promotion",
    ),
}

_CORE_CAPABILITIES = (
    "verified_aggregate_series",
    "development_event_assignment",
    "verified_minute_path",
    "verified_cost_policy",
)
_ROLE_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "naive": ("parent_slot_outcomes",),
    "unconditional": ("parent_slot_outcomes", "complete_legal_donor_population"),
    "persistence": ("parent_slot_outcomes", "prior_completed_bar_returns"),
    "label_shuffle": ("parent_slot_outcomes", "complete_legal_donor_population"),
    "one_week_time_shift": ("parent_slot_signals", "shifted_legal_outcome_paths"),
    "random_feature": ("parent_slot_outcomes", "complete_legal_opportunity_population"),
    "doubled_cost": ("parent_slot_outcomes", "verified_cost_policy"),
    "one_bar_delay": ("parent_slot_signals", "verified_delayed_outcome_paths"),
    "exclude_strongest_asset": ("parent_slot_outcomes", "development_asset_strength_rows"),
    "exclude_strongest_utc_year": (
        "parent_slot_outcomes",
        "development_utc_year_strength_rows",
    ),
    "exposure_residual": ("parent_slot_outcomes", "inner_trained_market_factor_rows"),
    "capacity_diagnostic": ("parent_slot_decision", "capacity_evidence"),
}


class AdapterStatus(StrEnum):
    """Whether a source-bound slot adapter currently exists."""

    IMPLEMENTED = "implemented"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ValidationSlotExecutionEntry:
    """One exact roster slot plus its production primitive and adapter dependencies."""

    slot_id: str
    family: str
    kind: ValidationSlotKind
    role: str
    timeframe: str
    horizon_hours: int
    direction: str
    parameters: tuple[tuple[str, str], ...]
    primary: bool
    parent_slot_id: str | None
    family_prerequisites: tuple[str, ...]
    family_a_identity_slot_ids: tuple[str, ...]
    candidate_parent_slot_ids: tuple[str, ...]
    detector_or_control_formula: str
    required_capabilities: tuple[str, ...]
    required_aggregate_timeframe: str
    candidate_population: str
    legal_entry_path: str
    legal_outcome_path: str
    cost_application: str
    statistic: str
    terminal_state_rules: tuple[str, ...]
    receipt_factory: str
    work_budget_demand: tuple[str, ...]
    production_primitives: tuple[str, ...]
    adapter_status: AdapterStatus
    adapter_path: str | None
    detector_adapter_path: str | None
    planning_adapter_path: str | None
    result_adapter: str | None
    missing_adapter_key: str | None

    def __post_init__(self) -> None:
        if self.adapter_status is AdapterStatus.IMPLEMENTED:
            if (
                self.adapter_path is None
                or self.result_adapter is None
                or self.missing_adapter_key is not None
            ):
                raise ValueError("implemented adapter entry is inconsistent")
        elif self.adapter_status is AdapterStatus.MISSING:
            if (
                self.adapter_path is not None
                or self.result_adapter is not None
                or not self.missing_adapter_key
            ):
                raise ValueError("missing adapter entry is inconsistent")
        else:
            raise ValueError("adapter status is invalid")
        if self.detector_adapter_path is not None and not self.detector_adapter_path:
            raise ValueError("detector adapter path must be non-empty when supplied")
        if self.planning_adapter_path is not None and not self.planning_adapter_path:
            raise ValueError("planning adapter path must be non-empty when supplied")
        for values, label in (
            (self.family_prerequisites, "family prerequisites"),
            (self.family_a_identity_slot_ids, "family A identity slots"),
            (self.candidate_parent_slot_ids, "candidate parent slots"),
            (self.required_capabilities, "required capabilities"),
            (self.terminal_state_rules, "terminal state rules"),
            (self.work_budget_demand, "work budget demand"),
            (self.production_primitives, "production primitives"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "slot_id": self.slot_id,
            "family": self.family,
            "kind": self.kind.value,
            "role": self.role,
            "timeframe": self.timeframe,
            "horizon_hours": self.horizon_hours,
            "direction": self.direction,
            "parameters": [list(item) for item in self.parameters],
            "primary": self.primary,
            "parent_slot_id": self.parent_slot_id,
            "family_prerequisites": list(self.family_prerequisites),
            "family_a_identity_slot_ids": list(self.family_a_identity_slot_ids),
            "candidate_parent_slot_ids": list(self.candidate_parent_slot_ids),
            "detector_or_control_formula": self.detector_or_control_formula,
            "required_capabilities": list(self.required_capabilities),
            "required_aggregate_timeframe": self.required_aggregate_timeframe,
            "candidate_population": self.candidate_population,
            "legal_entry_path": self.legal_entry_path,
            "legal_outcome_path": self.legal_outcome_path,
            "cost_application": self.cost_application,
            "statistic": self.statistic,
            "terminal_state_rules": list(self.terminal_state_rules),
            "receipt_factory": self.receipt_factory,
            "work_budget_demand": list(self.work_budget_demand),
            "production_primitives": list(self.production_primitives),
            "adapter_status": self.adapter_status.value,
            "adapter_path": self.adapter_path,
            "detector_adapter_path": self.detector_adapter_path,
            "planning_adapter_path": self.planning_adapter_path,
            "result_adapter": self.result_adapter,
            "missing_adapter_key": self.missing_adapter_key,
        }


@dataclass(frozen=True, slots=True)
class ValidationRosterExecutionMap:
    """Canonical dependency and execution metadata for all frozen slots."""

    schema_version: str
    roster_sha256: str
    slots: tuple[ValidationSlotExecutionEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("validation roster execution-map schema is invalid")
        if len(self.slots) != 1_104:
            raise ValueError("validation roster execution map must contain exactly 1,104 slots")
        if tuple(entry.slot_id for entry in self.slots) != tuple(
            slot.slot_id for slot in VALIDATION_SLOT_ROSTER
        ):
            raise ValueError("validation roster execution map order is not canonical")
        if self.roster_sha256 != validation_roster_sha256(VALIDATION_SLOT_ROSTER):
            raise ValueError("validation roster execution map has the wrong roster identity")
        a_primary_slots = tuple(
            slot for slot in VALIDATION_SLOT_ROSTER if slot.family == "A" and slot.primary
        )
        canonical_slots = tuple(
            _execution_entry(slot, a_primary_slots) for slot in VALIDATION_SLOT_ROSTER
        )
        if self.slots != canonical_slots:
            raise ValueError("validation roster execution-map slot metadata is not canonical")

    @property
    def sha256(self) -> str:
        return hash_json("phase5-validation-roster-execution-map-v1", self._identity_payload())

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "roster_sha256": self.roster_sha256,
            "slots": [entry.to_dict() for entry in self.slots],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._identity_payload(), "execution_map_sha256": self.sha256}


def build_validation_roster_execution_map() -> ValidationRosterExecutionMap:
    """Return the exact frozen roster map without reading any market or outcome source."""

    roster = freeze_validation_slot_roster(VALIDATION_SLOT_ROSTER)
    a_primary_slots = tuple(slot for slot in roster if slot.family == "A" and slot.primary)
    entries = tuple(_execution_entry(slot, a_primary_slots) for slot in roster)
    return ValidationRosterExecutionMap(
        schema_version=_SCHEMA_VERSION,
        roster_sha256=validation_roster_sha256(roster),
        slots=entries,
    )


def _execution_entry(
    slot: ValidationSlot,
    a_primary_slots: tuple[ValidationSlot, ...],
) -> ValidationSlotExecutionEntry:
    family_prerequisites = () if slot.family == "A" else ("A",)
    family_a_identity_slot_ids = (
        tuple(item.slot_id for item in a_primary_slots)
        if slot.kind is ValidationSlotKind.CORE and slot.family != "A"
        else ()
    )
    candidate_parent_slot_ids = _candidate_parent_slot_ids(slot, a_primary_slots)
    required_capabilities = _required_capabilities(slot)
    production_primitives = _production_primitives(slot)
    family_a_detector = slot.family == "A" and slot.kind in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    )
    family_ged_detector = slot.family in ("G", "E", "D") and slot.kind in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    )
    detector_adapter_path = (
        _FAMILY_A_DETECTOR_ADAPTER
        if family_a_detector
        else _FAMILY_GED_DETECTOR_ADAPTER
        if family_ged_detector
        else None
    )
    planning_adapter_path: str | None
    if slot.slot_id == "VS-0001":
        adapter_status = AdapterStatus.IMPLEMENTED
        adapter_path = _VS0001_ADAPTER
        planning_adapter_path = _FAMILY_A_PLANNING_ADAPTER
        result_adapter = _VS0001_ADAPTER
        missing_adapter_key = None
    else:
        adapter_status = AdapterStatus.MISSING
        adapter_path = None
        if family_a_detector:
            planning_adapter_path = _FAMILY_A_PLANNING_ADAPTER
        elif slot.family == "B" and slot.kind in (
            ValidationSlotKind.CORE,
            ValidationSlotKind.PERTURBATION,
        ):
            planning_adapter_path = _FAMILY_B_DETECTOR_PLANNING_ADAPTER
        elif family_ged_detector and slot.kind is ValidationSlotKind.PERTURBATION:
            planning_adapter_path = _FAMILY_GED_ADJACENT_PLANNING_ADAPTER
        elif family_ged_detector:
            planning_adapter_path = {
                "G": _FAMILY_G_CORE_PLANNING_ADAPTER,
                "E": _FAMILY_E_CORE_PLANNING_ADAPTER,
                "D": _FAMILY_D_CORE_PLANNING_ADAPTER,
            }[slot.family]
        else:
            planning_adapter_path = (
                _COMMON_ROLE_PLANNING_ADAPTER if slot.role in _ROLE_PRIMITIVES else None
            )
        result_adapter = None
        missing_adapter_key = f"{slot.kind.value}:{slot.family}:{slot.role}:source-bound-v2-adapter"
    return ValidationSlotExecutionEntry(
        slot_id=slot.slot_id,
        family=slot.family,
        kind=slot.kind,
        role=slot.role,
        timeframe=slot.timeframe,
        horizon_hours=slot.horizon_hours,
        direction=slot.direction,
        parameters=slot.parameters,
        primary=slot.primary,
        parent_slot_id=slot.parent_slot_id,
        family_prerequisites=family_prerequisites,
        family_a_identity_slot_ids=family_a_identity_slot_ids,
        candidate_parent_slot_ids=candidate_parent_slot_ids,
        detector_or_control_formula=_detector_or_control_formula(slot),
        required_capabilities=required_capabilities,
        required_aggregate_timeframe=slot.timeframe,
        candidate_population=_candidate_population(slot),
        legal_entry_path=_legal_entry_path(slot),
        legal_outcome_path=_legal_outcome_path(slot),
        cost_application=_cost_application(slot),
        statistic=_statistic(slot),
        terminal_state_rules=(
            "failed:not_evaluated=invalid_common_policy_or_authority_or_precomputation_failure",
            "completed:inconclusive=missing_family_local_coverage_or_support_or_precision",
            "completed:rejected=complete_frozen_gate_failure",
            "completed:supported_development=all_non_final_gates_pass",
        ),
        receipt_factory=(
            "market_structure_lab.research.validation_v2_receipts.publish_validation_v2_receipt"
        ),
        work_budget_demand=_work_budget_demand(slot),
        production_primitives=production_primitives,
        adapter_status=adapter_status,
        adapter_path=adapter_path,
        detector_adapter_path=detector_adapter_path,
        planning_adapter_path=planning_adapter_path,
        result_adapter=result_adapter,
        missing_adapter_key=missing_adapter_key,
    )


def _candidate_parent_slot_ids(
    slot: ValidationSlot,
    a_primary_slots: tuple[ValidationSlot, ...],
) -> tuple[str, ...]:
    if slot.kind is not ValidationSlotKind.CORE:
        return ()
    if slot.family == "B":
        return tuple(
            item.slot_id
            for item in a_primary_slots
            if item.timeframe == slot.timeframe and item.direction == slot.direction
        )
    if slot.family == "E":
        donchian_hours = dict(slot.parameters)["donchian_hours"]
        return tuple(
            item.slot_id
            for item in a_primary_slots
            if item.role == "donchian_breakout"
            and item.timeframe == slot.timeframe
            and item.direction == slot.direction
            and dict(item.parameters).get("lookback_hours") == donchian_hours
        )
    return ()


def _required_capabilities(slot: ValidationSlot) -> tuple[str, ...]:
    if slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        capabilities: list[str] = list(_CORE_CAPABILITIES)
        if slot.family != "A":
            capabilities.append("frozen_family_a_selector_grid")
        if slot.family == "B":
            capabilities.extend(
                ("verified_historical_source_price_precision", "verified_profile_stream")
            )
        if slot.family == "E":
            capabilities.append("family_a_donchian_opportunities")
        if slot.kind is ValidationSlotKind.PERTURBATION:
            capabilities.append("parent_slot_inputs")
        return tuple(capabilities)
    try:
        return _ROLE_CAPABILITIES[slot.role]
    except KeyError as error:
        raise RuntimeError(f"unmapped validation role capabilities: {slot.role}") from error


def _production_primitives(slot: ValidationSlot) -> tuple[str, ...]:
    if slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        primitives: list[str] = list(_CORE_PRIMITIVES)
        if slot.family == "B":
            primitives.insert(
                1,
                "market_structure_lab.research.candidates.build_verified_profile_stream",
            )
        return tuple(primitives)
    try:
        return _ROLE_PRIMITIVES[slot.role]
    except KeyError as error:
        raise RuntimeError(f"unmapped validation role primitives: {slot.role}") from error


def _detector_or_control_formula(slot: ValidationSlot) -> str:
    parameters = ",".join(f"{name}={value}" for name, value in slot.parameters)
    return f"{slot.family}:{slot.role}" + (f":{parameters}" if parameters else "")


def _candidate_population(slot: ValidationSlot) -> str:
    if slot.kind not in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        return "exact_parent_slot_opportunity_population"
    return {
        "A": "complete_verified_aggregate_series",
        "B": "authenticated_fold_selected_family_a_opportunities",
        "G": "complete_legal_aggregate_bar_opportunities",
        "E": "authenticated_family_a_donchian_opportunities",
        "D": "complete_legal_frozen_donchian_level_opportunities",
    }[slot.family]


def _legal_entry_path(slot: ValidationSlot) -> str:
    if slot.role == "one_bar_delay":
        return "one_additional_contiguous_aggregate_bar_open"
    if slot.family == "D" and slot.kind in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    ):
        return "open_after_separate_completed_confirmation_bar"
    if slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        return "next_contiguous_aggregate_bar_open"
    return "inherit_exact_parent_entry_clock"


def _legal_outcome_path(slot: ValidationSlot) -> str:
    return (
        f"signed_{slot.horizon_hours}h_entry_to_exit_open_return;"
        "complete_one_minute_half_open_path_[entry,exit);mfe_mae"
    )


def _cost_application(slot: ValidationSlot) -> str:
    if slot.role == "naive":
        return "no_trade_zero_vector_no_execution_cost"
    if slot.role == "doubled_cost":
        return "v3_doubled_rates_squared_fill_missed_fill_zero"
    if slot.role == "one_bar_delay":
        return "v3_base_rates_delayed_entry_missed_fill_zero"
    if slot.kind is ValidationSlotKind.CAPACITY:
        return "promotion_only_capacity_diagnostic_no_scientific_decision_change"
    return "v3_base_positive_rates_expected_net_missed_fill_zero"


def _statistic(slot: ValidationSlot) -> str:
    if slot.kind is ValidationSlotKind.EXPOSURE:
        return "inner_trained_ols_residual_weekly_mean_bootstrap"
    if slot.kind is ValidationSlotKind.CAPACITY:
        return "finite_volume_relative_capacity_and_impact_diagnostic"
    if slot.kind in (ValidationSlotKind.ROBUSTNESS, ValidationSlotKind.PERTURBATION):
        return "parent_contrast_weekly_mean_bootstrap_lower_bound_gate"
    if slot.primary:
        return "family_worst_control_intersection_union_weekly_mean_bootstrap"
    return "diagnostic_weekly_mean_bootstrap_or_exact_matched_control"


def _work_budget_demand(slot: ValidationSlot) -> tuple[str, ...]:
    common = ("trials", "evaluations", "events", "outcomes", "bootstrap_draws")
    if slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        return ("aggregate_bars", "candidates", "path_cells", *common)
    if slot.kind in (ValidationSlotKind.BASELINE, ValidationSlotKind.NEGATIVE_CONTROL):
        return ("controls", "placebos", *common)
    if slot.kind is ValidationSlotKind.ROBUSTNESS:
        return ("robustness_reruns", "path_cells", *common)
    if slot.kind is ValidationSlotKind.EXPOSURE:
        return ("model_evaluations", "bootstrap_cells", *common)
    if slot.kind is ValidationSlotKind.CAPACITY:
        return ("events", "outcomes", "evaluations")
    raise RuntimeError(f"unmapped validation work demand: {slot.kind.value}")
