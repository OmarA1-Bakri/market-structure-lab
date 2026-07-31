"""Immutable Phase 5 validation programme identities and work contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from math import isfinite
import re
from types import MappingProxyType
from typing import cast

from market_structure_lab.core.identity import evaluation_id, hash_json, programme_id

EXPECTED_FAMILIES = ("A", "B", "G", "E", "D")

_TASK14_CLOSEOUT = "5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d"
_TASK14_EVIDENCE = "3482882c864f1471ec1dd682631544d0c404c542"
_TASK15_PLAN = "807ac28ac5616fb837c1ccea1e2bc47572ae3984"
_TASK15_EVIDENCE = "afce8e889aa645cd9e48e90631698b87866f287f"
_IMPLEMENTATION_PLAN_CHECKPOINT = "e655152ab729b3e1e530f1bc044febca3509a6fd"
_IMPLEMENTATION_PLAN_EVIDENCE = "6da307a0d4756c61ddcabdf01f00d828afb55d7e"
_IMPLEMENTATION_PLAN_DOCUMENT = "d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111"
_GIT_SHA = re.compile(r"^[a-f0-9]{40}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VP-[a-f0-9]{64}$")
_CANDIDATE_DEFINITION_FACTORY_TOKEN = object()


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _freeze_config_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_config_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_config_value(item) for item in value)
    return value


def _copy_config_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _copy_config_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_copy_config_value(item) for item in value]
    return value


class ExecutionStatus(StrEnum):
    """Whether one immutable validation execution completed operationally."""

    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ScientificDecision(StrEnum):
    """Scientific conclusion kept orthogonal to operational execution status."""

    NOT_EVALUATED = "not_evaluated"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    SUPPORTED_DEVELOPMENT = "supported_development"
    VALIDATED = "validated"
    PROMOTED = "promoted"


class OutcomeComponent(StrEnum):
    """The preregistered data component on which an outcome would be read."""

    DEVELOPMENT = "development"
    FINAL_TEMPORAL = "final_temporal"
    FINAL_ASSET = "final_asset"


@dataclass(frozen=True, slots=True)
class OutcomePolicy:
    """Frozen horizon and publication identities for one outcome attachment."""

    horizon_hours: int
    component: OutcomeComponent
    aggregate_publication_sha256: str
    minute_publication_sha256: str
    programme_id: str | None = None
    final_batch_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.horizon_hours, bool)
            or not isinstance(self.horizon_hours, int)
            or self.horizon_hours < 1
        ):
            raise ValueError("outcome horizon_hours must be a positive integer")
        if not isinstance(self.component, OutcomeComponent):
            raise TypeError("outcome component must be an OutcomeComponent")
        _require_sha256(
            self.aggregate_publication_sha256,
            "outcome aggregate_publication_sha256",
        )
        _require_sha256(
            self.minute_publication_sha256,
            "outcome minute_publication_sha256",
        )
        if self.component is OutcomeComponent.DEVELOPMENT:
            if self.programme_id is not None or self.final_batch_sha256 is not None:
                raise ValueError("development outcome policy cannot bind final access")
        else:
            if self.programme_id is None or self.final_batch_sha256 is None:
                raise ValueError("final outcome policy must bind programme and batch identities")
            if _PROGRAMME_ID.fullmatch(self.programme_id) is None:
                raise ValueError("final outcome programme_id must be a VP identity")
            _require_sha256(self.final_batch_sha256, "final_batch_sha256")

    @property
    def sha256(self) -> str:
        return hash_json("outcome-policy-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "horizon_hours": self.horizon_hours,
            "component": self.component.value,
            "aggregate_publication_sha256": self.aggregate_publication_sha256,
            "minute_publication_sha256": self.minute_publication_sha256,
        }
        if self.programme_id is not None:
            payload["programme_id"] = self.programme_id
        if self.final_batch_sha256 is not None:
            payload["final_batch_sha256"] = self.final_batch_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ValidationTerminalState:
    """Validated compatible execution and scientific terminal states."""

    execution_status: ExecutionStatus
    decision: ScientificDecision

    def __post_init__(self) -> None:
        if not isinstance(self.execution_status, ExecutionStatus):
            raise TypeError("execution_status must be an ExecutionStatus")
        if not isinstance(self.decision, ScientificDecision):
            raise TypeError("decision must be a ScientificDecision")
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision is ScientificDecision.NOT_EVALUATED:
                raise ValueError("completed execution cannot be not_evaluated")
            return
        if self.decision is not ScientificDecision.NOT_EVALUATED:
            raise ValueError(f"{self.execution_status.value} execution requires not_evaluated")


@dataclass(frozen=True, slots=True)
class ValidationWorkDemand:
    """Declared programme work, checked before any iterable or allocation is touched."""

    source_rows: int = 0
    source_bytes: int = 0
    aggregate_bars: int = 0
    profile_stream_count: int = 0
    profile_active_bin_cells: int = 0
    profile_source_id_bytes: int = 0
    profile_config_bytes: int = 0
    profile_serialized_bytes: int = 0
    symbols: int = 0
    ranges: int = 0
    candidates: int = 0
    trials: int = 1_104
    events: int = 0
    outcomes: int = 0
    path_cells: int = 0
    outer_folds: int = 0
    inner_folds: int = 0
    evaluations: int = 1_104
    bootstrap_draws: int = 4_096
    bootstrap_cells: int = 0
    bootstrap_blocks: int = 0
    controls: int = 384
    placebos: int = 192
    perturbations: int = 184
    artifacts: int = 0
    artifact_bytes: int = 0
    dashboard_bytes: int = 0
    final_holdout_candidates: int = 0
    final_batch_candidates: int = 0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


class ValidationWorkBudgetViolation(ValueError):
    """A declared workload exceeded or contradicted its frozen budget."""

    def __init__(self, stage: str, observed: int, limit: int, *, exact: bool = False) -> None:
        qualifier = "must equal" if exact else "exceeds"
        super().__init__(f"{stage} {qualifier} the validation work limit {limit}: {observed}")
        self.stage = stage
        self.observed = observed
        self.limit = limit
        self.exact = exact


@dataclass(frozen=True, slots=True)
class ValidationWorkBudget:
    """Frozen independent admission limits for one Phase 5 validation programme."""

    evaluation_count: int = 1_104
    core_count: int = 152
    primary_count: int = 64
    baseline_count: int = 192
    negative_control_count: int = 192
    robustness_count: int = 256
    perturbation_count: int = 184
    exposure_count: int = 64
    capacity_count: int = 64
    bootstrap_draws: int = 4_096
    max_source_rows: int = 25_000_000
    max_source_bytes: int = 8 * 1024 * 1024 * 1024
    max_aggregate_bars: int = 1_000_000
    max_profile_stream_count: int = 1_000_000
    max_profile_active_bin_cells: int = 100_000_000
    max_profile_source_id_bytes: int = 2_000_000_000
    max_profile_config_bytes: int = 64 * 1024
    max_profile_serialized_bytes: int = 256 * 1024 * 1024
    max_symbols: int = 64
    max_ranges: int = 256
    max_candidates: int = 256
    max_trials: int = 1_104
    max_events: int = 2_000_000
    max_outcomes: int = 2_000_000
    max_path_cells: int = 100_000_000
    max_outer_folds: int = 4
    max_inner_folds: int = 3
    max_evaluations: int = 1_104
    max_bootstrap_draws: int = 4_096
    max_bootstrap_cells: int = 262_144
    max_bootstrap_blocks: int = 64
    max_controls: int = 384
    max_placebos: int = 192
    max_perturbations: int = 184
    max_artifacts: int = 20_000
    max_artifact_bytes: int = 64 * 1024 * 1024
    max_dashboard_bytes: int = 8 * 1024 * 1024
    max_final_holdout_candidates: int = 64
    max_final_batch_candidates: int = 64

    def __post_init__(self) -> None:
        exact = {
            "evaluation_count": 1_104,
            "core_count": 152,
            "primary_count": 64,
            "baseline_count": 192,
            "negative_control_count": 192,
            "robustness_count": 256,
            "perturbation_count": 184,
            "exposure_count": 64,
            "capacity_count": 64,
            "bootstrap_draws": 4_096,
        }
        for field_name, required in exact.items():
            if getattr(self, field_name) != required:
                raise ValueError(f"{field_name} must remain exactly {required}")
        if self.primary_count > self.core_count:
            raise ValueError("primary_count must be a subset of core_count")
        distinct_total = (
            self.core_count
            + self.baseline_count
            + self.negative_control_count
            + self.robustness_count
            + self.perturbation_count
            + self.exposure_count
            + self.capacity_count
        )
        if distinct_total != self.evaluation_count:
            raise ValueError("validation evaluation family counts must total exactly 1,104")
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        self._validate_authoritative_ceilings()

    def _validate_authoritative_ceilings(self) -> None:
        ceilings = {
            "max_source_rows": 25_000_000,
            "max_source_bytes": 8 * 1024 * 1024 * 1024,
            "max_aggregate_bars": 1_000_000,
            "max_profile_stream_count": 1_000_000,
            "max_profile_active_bin_cells": 100_000_000,
            "max_profile_source_id_bytes": 2_000_000_000,
            "max_profile_config_bytes": 64 * 1024,
            "max_profile_serialized_bytes": 256 * 1024 * 1024,
            "max_symbols": 64,
            "max_ranges": 256,
            "max_candidates": 256,
            "max_trials": 1_104,
            "max_events": 2_000_000,
            "max_outcomes": 2_000_000,
            "max_path_cells": 100_000_000,
            "max_outer_folds": 4,
            "max_inner_folds": 3,
            "max_evaluations": 1_104,
            "max_bootstrap_draws": 4_096,
            "max_bootstrap_cells": 262_144,
            "max_bootstrap_blocks": 64,
            "max_controls": 384,
            "max_placebos": 192,
            "max_perturbations": 184,
            "max_artifacts": 20_000,
            "max_artifact_bytes": 64 * 1024 * 1024,
            "max_dashboard_bytes": 8 * 1024 * 1024,
            "max_final_holdout_candidates": 64,
            "max_final_batch_candidates": 64,
        }
        for field_name, ceiling in ceilings.items():
            if getattr(self, field_name) > ceiling:
                raise ValueError(f"{field_name} exceeds the authoritative ceiling {ceiling}")

    @property
    def sha256(self) -> str:
        return hash_json("validation-work-budget", self.to_dict())

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def preflight(
        self,
        demand: ValidationWorkDemand,
        *,
        deferred_work: Iterable[object] | None = None,
        allocation: Callable[[], object] | None = None,
    ) -> None:
        """Validate declarations without iterating or allocating the deferred work."""

        limits = {
            "source_rows": self.max_source_rows,
            "source_bytes": self.max_source_bytes,
            "aggregate_bars": self.max_aggregate_bars,
            "profile_stream_count": self.max_profile_stream_count,
            "profile_active_bin_cells": self.max_profile_active_bin_cells,
            "profile_source_id_bytes": self.max_profile_source_id_bytes,
            "profile_config_bytes": self.max_profile_config_bytes,
            "profile_serialized_bytes": self.max_profile_serialized_bytes,
            "symbols": self.max_symbols,
            "ranges": self.max_ranges,
            "candidates": self.max_candidates,
            "trials": self.max_trials,
            "events": self.max_events,
            "outcomes": self.max_outcomes,
            "path_cells": self.max_path_cells,
            "outer_folds": self.max_outer_folds,
            "inner_folds": self.max_inner_folds,
            "evaluations": self.max_evaluations,
            "bootstrap_draws": self.max_bootstrap_draws,
            "bootstrap_cells": self.max_bootstrap_cells,
            "bootstrap_blocks": self.max_bootstrap_blocks,
            "controls": self.max_controls,
            "placebos": self.max_placebos,
            "perturbations": self.max_perturbations,
            "artifacts": self.max_artifacts,
            "artifact_bytes": self.max_artifact_bytes,
            "dashboard_bytes": self.max_dashboard_bytes,
            "final_holdout_candidates": self.max_final_holdout_candidates,
            "final_batch_candidates": self.max_final_batch_candidates,
        }
        for field_name, limit in limits.items():
            observed = getattr(demand, field_name)
            if observed > limit:
                raise ValidationWorkBudgetViolation(field_name, observed, limit)
        exact = {
            "evaluations": self.evaluation_count,
            "bootstrap_draws": self.bootstrap_draws,
        }
        for field_name, required in exact.items():
            observed = getattr(demand, field_name)
            if observed != required:
                raise ValidationWorkBudgetViolation(field_name, observed, required, exact=True)
        _ = deferred_work, allocation


class ValidationSlotKind(StrEnum):
    """Non-overlapping categories that together form the 1,104-entry ledger."""

    CORE = "core"
    BASELINE = "baseline"
    NEGATIVE_CONTROL = "negative_control"
    ROBUSTNESS = "robustness"
    PERTURBATION = "perturbation"
    EXPOSURE = "exposure"
    CAPACITY = "capacity"


@dataclass(frozen=True, slots=True)
class ValidationSlot:
    """One exactly positioned evaluation in the frozen programme roster."""

    slot_id: str
    family: str
    kind: ValidationSlotKind
    role: str
    timeframe: str
    horizon_hours: int
    direction: str
    parameters: tuple[tuple[str, str], ...]
    primary: bool
    parent_slot_id: str | None = None

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
        }


def _parameters(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple((name, str(value)) for name, value in values.items())


def _core_specs() -> list[dict[str, object]]:
    specifications: list[dict[str, object]] = []
    for timeframe in ("1h", "4h"):
        for direction in ("long", "short"):
            specifications.append(
                {
                    "family": "A",
                    "role": "moving_average_crossover",
                    "timeframe": timeframe,
                    "horizon_hours": 24,
                    "direction": direction,
                    "parameters": _parameters(
                        detector="moving_average_crossover", fast_hours=24, slow_hours=72
                    ),
                }
            )
            for lookback in (24, 72):
                specifications.append(
                    {
                        "family": "A",
                        "role": "donchian_breakout",
                        "timeframe": timeframe,
                        "horizon_hours": 24,
                        "direction": direction,
                        "parameters": _parameters(
                            detector="donchian_breakout", lookback_hours=lookback
                        ),
                    }
                )
            specifications.append(
                {
                    "family": "A",
                    "role": "atr_breakout",
                    "timeframe": timeframe,
                    "horizon_hours": 24,
                    "direction": direction,
                    "parameters": _parameters(detector="atr_breakout", atr_hours=24),
                }
            )
            for lookback in (24, 72):
                specifications.append(
                    {
                        "family": "A",
                        "role": "time_series_momentum",
                        "timeframe": timeframe,
                        "horizon_hours": 24,
                        "direction": direction,
                        "parameters": _parameters(
                            detector="time_series_momentum", momentum_hours=lookback
                        ),
                    }
                )
    for timeframe in ("1h", "4h"):
        for direction in ("long", "short"):
            for lookback in (24, 72):
                common = {
                    "family": "B",
                    "timeframe": timeframe,
                    "horizon_hours": 24,
                    "direction": direction,
                    "parameters": _parameters(
                        detector="value_migration_acceptance", profile_hours=lookback
                    ),
                }
                for role in (
                    "price_baseline",
                    "structure_only",
                    "combined_primary",
                    "rate_matched_placebo",
                ):
                    specifications.append(
                        {**common, "role": role, "primary": role == "combined_primary"}
                    )
    for timeframe in ("1h", "4h"):
        for direction in ("long", "short"):
            for horizon in (8, 24):
                common = {
                    "family": "G",
                    "timeframe": timeframe,
                    "horizon_hours": horizon,
                    "direction": direction,
                    "parameters": _parameters(
                        detector="compression_expansion",
                        atr_short_hours=8,
                        atr_long_hours=24,
                        donchian_hours=24,
                        compression_run=3,
                    ),
                }
                for role in ("candidate_primary", "atr_only_control", "donchian_only_control"):
                    specifications.append(
                        {**common, "role": role, "primary": role == "candidate_primary"}
                    )
    for timeframe in ("1h", "4h"):
        for direction in ("long", "short"):
            for lookback in (24, 72):
                common = {
                    "family": "E",
                    "timeframe": timeframe,
                    "horizon_hours": 24,
                    "direction": direction,
                    "parameters": _parameters(
                        detector="volume_confirmation",
                        donchian_hours=lookback,
                        volume_median_hours=24,
                    ),
                }
                for role in ("price_only", "volume_filtered_primary", "rate_matched_placebo"):
                    specifications.append(
                        {**common, "role": role, "primary": role == "volume_filtered_primary"}
                    )
    for timeframe in ("1h", "4h"):
        for direction in ("long", "short"):
            for lookback in (24, 72):
                for horizon in (8, 24):
                    common = {
                        "family": "D",
                        "timeframe": timeframe,
                        "horizon_hours": horizon,
                        "direction": direction,
                        "parameters": _parameters(
                            detector="failed_break_reclaim",
                            level_hours=lookback,
                            confirmation_bars=1,
                        ),
                    }
                    for role in (
                        "candidate_primary",
                        "failed_donchian_control",
                        "pseudo_level_control",
                    ):
                        specifications.append(
                            {**common, "role": role, "primary": role == "candidate_primary"}
                        )
    return specifications


def _target_bars(hours: int, timeframe: str) -> int:
    width = 1 if timeframe == "1h" else 4
    if hours % width:
        raise RuntimeError("frozen roster contains a non-integral timeframe parameter")
    return hours // width


def _perturbed_parameters(slot: ValidationSlot) -> tuple[tuple[str, int], ...]:
    detector = dict(slot.parameters)["detector"]
    names = {
        "moving_average_crossover": ("fast_hours", "slow_hours"),
        "donchian_breakout": ("lookback_hours",),
        "atr_breakout": ("atr_hours",),
        "time_series_momentum": ("momentum_hours",),
        "value_migration_acceptance": ("profile_hours",),
        "compression_expansion": (
            "atr_short_hours",
            "atr_long_hours",
            "donchian_hours",
        ),
        "volume_confirmation": ("donchian_hours", "volume_median_hours"),
        "failed_break_reclaim": ("level_hours",),
    }[detector]
    source = dict(slot.parameters)
    return tuple((name, _target_bars(int(source[name]), slot.timeframe)) for name in names)


def _build_validation_slot_roster() -> tuple[ValidationSlot, ...]:
    slots: list[ValidationSlot] = []

    def append(
        *,
        family: str,
        kind: ValidationSlotKind,
        role: str,
        timeframe: str,
        horizon_hours: int,
        direction: str,
        parameters: tuple[tuple[str, str], ...],
        primary: bool,
        parent_slot_id: str | None = None,
    ) -> ValidationSlot:
        slot = ValidationSlot(
            slot_id=f"VS-{len(slots) + 1:04d}",
            family=family,
            kind=kind,
            role=role,
            timeframe=timeframe,
            horizon_hours=horizon_hours,
            direction=direction,
            parameters=parameters,
            primary=primary,
            parent_slot_id=parent_slot_id,
        )
        slots.append(slot)
        return slot

    for specification in _core_specs():
        append(
            family=str(specification["family"]),
            kind=ValidationSlotKind.CORE,
            role=str(specification["role"]),
            timeframe=str(specification["timeframe"]),
            horizon_hours=cast(int, specification["horizon_hours"]),
            direction=str(specification["direction"]),
            parameters=cast(tuple[tuple[str, str], ...], specification["parameters"]),
            primary=bool(specification.get("primary", specification["family"] == "A")),
        )
    primaries = tuple(slot for slot in slots if slot.primary)
    for kind, roles in (
        (ValidationSlotKind.BASELINE, ("naive", "unconditional", "persistence")),
        (
            ValidationSlotKind.NEGATIVE_CONTROL,
            ("label_shuffle", "one_week_time_shift", "random_feature"),
        ),
        (
            ValidationSlotKind.ROBUSTNESS,
            (
                "doubled_cost",
                "one_bar_delay",
                "exclude_strongest_asset",
                "exclude_strongest_utc_year",
            ),
        ),
    ):
        for primary in primaries:
            for role in roles:
                append(
                    family=primary.family,
                    kind=kind,
                    role=role,
                    timeframe=primary.timeframe,
                    horizon_hours=primary.horizon_hours,
                    direction=primary.direction,
                    parameters=primary.parameters,
                    primary=False,
                    parent_slot_id=primary.slot_id,
                )
    for primary in primaries:
        for parameter, original_bars in _perturbed_parameters(primary):
            for offset in (-1, 1):
                append(
                    family=primary.family,
                    kind=ValidationSlotKind.PERTURBATION,
                    role="adjacent_lookback",
                    timeframe=primary.timeframe,
                    horizon_hours=primary.horizon_hours,
                    direction=primary.direction,
                    parameters=primary.parameters
                    + _parameters(
                        perturbed_parameter=parameter,
                        original_bars=original_bars,
                        candidate_bars=original_bars + offset,
                    ),
                    primary=False,
                    parent_slot_id=primary.slot_id,
                )
    for primary in primaries:
        append(
            family=primary.family,
            kind=ValidationSlotKind.EXPOSURE,
            role="exposure_residual",
            timeframe=primary.timeframe,
            horizon_hours=primary.horizon_hours,
            direction=primary.direction,
            parameters=primary.parameters,
            primary=False,
            parent_slot_id=primary.slot_id,
        )
    for primary in primaries:
        append(
            family=primary.family,
            kind=ValidationSlotKind.CAPACITY,
            role="capacity_diagnostic",
            timeframe=primary.timeframe,
            horizon_hours=primary.horizon_hours,
            direction=primary.direction,
            parameters=primary.parameters,
            primary=False,
            parent_slot_id=primary.slot_id,
        )
    if len(slots) != 1_104:
        raise RuntimeError("frozen validation slot roster must contain exactly 1,104 entries")
    return tuple(slots)


VALIDATION_SLOT_ROSTER = _build_validation_slot_roster()

FROZEN_A_SELECTOR_GRID = tuple(
    hash_json("A-selector-grid-entry-v1", slot.to_dict())
    for slot in VALIDATION_SLOT_ROSTER
    if slot.kind is ValidationSlotKind.CORE and slot.family == "A" and slot.primary
)


def freeze_validation_slot_roster(slots: Iterable[ValidationSlot]) -> tuple[ValidationSlot, ...]:
    """Accept only the exact frozen roster, including order and every field."""

    observed: list[ValidationSlot] = []
    for slot in slots:
        if len(observed) == 1_104:
            raise ValueError("validation requires the exact frozen 1,104-slot roster")
        observed.append(slot)
    frozen = tuple(observed)
    if frozen != VALIDATION_SLOT_ROSTER:
        raise ValueError("validation requires the exact frozen 1,104-slot roster")
    return frozen


def validation_roster_sha256(slots: Sequence[ValidationSlot]) -> str:
    """Return the canonical identity of an exact frozen slot roster."""

    frozen = freeze_validation_slot_roster(slots)
    return hash_json("validation-slot-roster", [slot.to_dict() for slot in frozen])


@dataclass(frozen=True, slots=True)
class CandidateDefinition:
    """One immutable outcome-blind detector/comparator identity."""

    slot: ValidationSlot
    source_publication_sha256: str
    source_series_sha256: str
    source_segment_id: int
    aggregate_config_version: str
    a_selector_grid: tuple[str, ...]
    origin: str = "human_origin"
    profile_bin_step: float | None = None
    profile_definition_id: str | None = None
    profile_stream_sha256: str | None = None
    parent_a_candidate_id: str | None = None
    parent_a_slot_id: str | None = None
    candidate_id: str = field(init=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _CANDIDATE_DEFINITION_FACTORY_TOKEN:
            raise TypeError("CandidateDefinition requires the canonical candidate factory token")
        if self.slot not in VALIDATION_SLOT_ROSTER:
            raise ValueError("candidate slot must belong to the frozen validation roster")
        if self.slot.kind not in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
            raise ValueError("candidate definitions require a CORE or PERTURBATION detector slot")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_sha256(self.source_series_sha256, "source_series_sha256")
        if (
            isinstance(self.source_segment_id, bool)
            or not isinstance(self.source_segment_id, int)
            or self.source_segment_id < 0
        ):
            raise ValueError("source_segment_id must be a non-negative integer")
        if not self.aggregate_config_version:
            raise ValueError("aggregate_config_version must not be empty")
        if self.origin != "human_origin":
            raise ValueError("Task 14 advanced no behaviour; candidate origin must be human_origin")
        if self.slot.family == "A":
            if self.a_selector_grid:
                raise ValueError("family A cannot claim a subordinate A selector grid")
        elif self.a_selector_grid != FROZEN_A_SELECTOR_GRID:
            raise ValueError("subordinate candidates require the full frozen A selector grid")
        if len(self.a_selector_grid) != len(set(self.a_selector_grid)):
            raise ValueError("A selector grid entries must be unique")
        for identity in self.a_selector_grid:
            _require_sha256(identity, "A selector grid identity")
        if self.slot.family == "B":
            if (
                self.profile_bin_step is None
                or not isfinite(self.profile_bin_step)
                or self.profile_bin_step <= 0
            ):
                raise ValueError("family B requires a positive verified profile bin step")
            parameters = dict(self.slot.parameters)
            profile_bars = _target_bars(int(parameters["profile_hours"]), self.slot.timeframe)
            if (
                self.slot.kind is ValidationSlotKind.PERTURBATION
                and parameters.get("perturbed_parameter") == "profile_hours"
            ):
                profile_bars = int(parameters["candidate_bars"])
            window_hours = profile_bars * (1 if self.slot.timeframe == "1h" else 4)
            expected_profile = (
                "rolling-1m:uniform-touched-v1:value-area=0.70:"
                f"window-hours={window_hours}:fixed-step={self.profile_bin_step}:"
                f"source-config={self.aggregate_config_version}"
            )
            if self.profile_definition_id != expected_profile:
                raise ValueError("family B profile definition does not match its frozen parameters")
            if self.profile_stream_sha256 is None:
                raise ValueError("family B requires a verified profile stream identity")
            _require_sha256(self.profile_stream_sha256, "profile_stream_sha256")
        elif self.profile_bin_step is not None or self.profile_definition_id is not None:
            raise ValueError("profile configuration belongs only to family B")
        elif self.profile_stream_sha256 is not None:
            raise ValueError("profile stream identity belongs only to family B")
        if self.slot.family in ("B", "E"):
            if self.parent_a_candidate_id is None or self.parent_a_slot_id is None:
                raise ValueError("B/E candidates require an exact registered parent A candidate")
            if not self.parent_a_candidate_id.startswith("HC-"):
                raise ValueError("parent A candidate identity is invalid")
        elif self.parent_a_candidate_id is not None or self.parent_a_slot_id is not None:
            raise ValueError("only B/E candidates bind an A opportunity parent")
        object.__setattr__(
            self,
            "candidate_id",
            f"HC-{hash_json('human-origin-candidate-v1', self.to_dict())}",
        )

    @property
    def family(self) -> str:
        return self.slot.family

    @property
    def family_order(self) -> int:
        return EXPECTED_FAMILIES.index(self.family) + 1

    @property
    def role(self) -> str:
        return self.detector_role

    @property
    def evaluation_role(self) -> str:
        return self.slot.role

    @property
    def detector_role(self) -> str:
        if self.slot.kind is not ValidationSlotKind.PERTURBATION:
            return self.slot.role
        parent = next(
            (item for item in VALIDATION_SLOT_ROSTER if item.slot_id == self.slot.parent_slot_id),
            None,
        )
        if parent is None or not parent.primary or parent.kind is not ValidationSlotKind.CORE:
            raise ValueError("adjacent-lookback candidate requires its frozen primary parent slot")
        return parent.role

    @property
    def timeframe(self) -> str:
        return self.slot.timeframe

    @property
    def direction(self) -> int:
        return 1 if self.slot.direction == "long" else -1

    @property
    def horizon_hours(self) -> int:
        return self.slot.horizon_hours

    @property
    def parameters(self) -> tuple[tuple[str, str], ...]:
        return self.slot.parameters

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "origin": self.origin,
            "family_order": self.family_order,
            "slot": self.slot.to_dict(),
            "source_publication_sha256": self.source_publication_sha256,
            "source_series_sha256": self.source_series_sha256,
            "source_segment_id": self.source_segment_id,
            "aggregate_config_version": self.aggregate_config_version,
            "a_selector_grid": list(self.a_selector_grid),
            "profile_bin_step": self.profile_bin_step,
            "profile_definition_id": self.profile_definition_id,
            "profile_stream_sha256": self.profile_stream_sha256,
            "evaluation_role": self.evaluation_role,
            "detector_role": self.detector_role,
            "parent_a_candidate_id": self.parent_a_candidate_id,
            "parent_a_slot_id": self.parent_a_slot_id,
            "task14_closeout_commit": _TASK14_CLOSEOUT,
            "task14_evidence_commit": _TASK14_EVIDENCE,
            "task15_plan_commit": _TASK15_PLAN,
            "task15_evidence_commit": _TASK15_EVIDENCE,
        }


def candidate_definition_for_slot(
    slot: ValidationSlot,
    series: object,
    *,
    parent_a_candidate: CandidateDefinition | None = None,
    profile_stream: object | None = None,
    a_selector_grid: tuple[str, ...] = FROZEN_A_SELECTOR_GRID,
) -> CandidateDefinition:
    """Issue one sealed definition after resolving its exact verified inputs and parent."""

    from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
    from market_structure_lab.data.price_precision import read_source_price_precision_manifest
    from market_structure_lab.research.candidates import (
        VerifiedProfileStream,
        verify_profile_stream,
    )

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("candidate definition requires a VerifiedAggregateSeries capability")
    if slot not in VALIDATION_SLOT_ROSTER:
        raise ValueError("candidate slot must belong to the frozen validation roster")
    if slot.kind not in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        raise ValueError("candidate definitions require a CORE or PERTURBATION detector slot")
    selector_grid = () if slot.family == "A" else a_selector_grid
    if slot.family != "A" and selector_grid != FROZEN_A_SELECTOR_GRID:
        raise ValueError(
            "subordinate candidate A selector grid does not match the frozen full grid"
        )

    profile_definition_id = None
    profile_bin_step = None
    profile_stream_sha256 = None
    if slot.family == "B":
        read_source_price_precision_manifest(series)
        profile_bars = _candidate_parameter_bars(slot, "profile_hours")
        window_hours = profile_bars * (1 if slot.timeframe == "1h" else 4)
        if type(profile_stream) is not VerifiedProfileStream:
            raise TypeError(
                "family B requires an exact factory-issued registered profile stream"
            )
        verify_profile_stream(profile_stream)
        if (
            profile_stream.aggregate_series_sha256 != series.series_sha256
            or profile_stream.window_hours != window_hours
        ):
            raise ValueError("family B requires a matching verified profile stream")
        profile_bin_step = profile_stream.bin_step
        profile_stream_sha256 = profile_stream.stream_sha256
        profile_definition_id = (
            "rolling-1m:uniform-touched-v1:value-area=0.70:"
            f"window-hours={window_hours}:fixed-step={profile_bin_step}:"
            f"source-config={series.manifest.config_version}"
        )
    elif profile_stream is not None:
        raise ValueError("profile stream is valid only for family B")

    parent_id = None
    parent_slot_id = None
    if slot.family in ("B", "E"):
        _validate_candidate_parent(
            slot,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            source_segment_id=series.segment_id,
            parent=parent_a_candidate,
        )
        assert parent_a_candidate is not None
        parent_id = parent_a_candidate.candidate_id
        parent_slot_id = parent_a_candidate.slot.slot_id

    return CandidateDefinition(
        slot=slot,
        source_publication_sha256=series.publication_sha256,
        source_series_sha256=series.series_sha256,
        source_segment_id=series.segment_id,
        aggregate_config_version=series.manifest.config_version,
        a_selector_grid=selector_grid,
        profile_bin_step=profile_bin_step,
        profile_definition_id=profile_definition_id,
        profile_stream_sha256=profile_stream_sha256,
        parent_a_candidate_id=parent_id,
        parent_a_slot_id=parent_slot_id,
        factory_token=_CANDIDATE_DEFINITION_FACTORY_TOKEN,
    )


def candidate_definition_for_verified_series(
    slot: ValidationSlot,
    series: object,
    *,
    parent_a_candidate: CandidateDefinition | None = None,
    profile_stream: object | None = None,
    a_selector_grid: tuple[str, ...] = FROZEN_A_SELECTOR_GRID,
) -> CandidateDefinition:
    """Issue a definition bound to one registered V2 detector-series capability."""

    from market_structure_lab.research.candidates import (
        VerifiedCandidateSeriesV2,
        VerifiedProfileStream,
        verify_profile_stream,
    )

    if type(series) is not VerifiedCandidateSeriesV2:
        raise TypeError(
            "candidate definition requires a factory-issued "
            "VerifiedCandidateSeriesV2 capability"
        )
    series.verify()
    if slot not in VALIDATION_SLOT_ROSTER:
        raise ValueError("candidate slot must belong to the frozen validation roster")
    if slot.kind not in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION):
        raise ValueError("candidate definitions require a CORE or PERTURBATION detector slot")
    if slot.timeframe != series.target_timeframe:
        raise ValueError("candidate slot timeframe does not match the verified series")
    selector_grid = () if slot.family == "A" else a_selector_grid
    if slot.family != "A" and selector_grid != FROZEN_A_SELECTOR_GRID:
        raise ValueError(
            "subordinate candidate A selector grid does not match the frozen full grid"
        )

    config_version = (
        f"{series.aggregate_schema_version}:{series.aggregate_identity}:"
        f"{series.aggregate_budget_sha256}"
    )
    profile_definition_id = None
    profile_bin_step = None
    profile_stream_sha256 = None
    if slot.family == "B":
        profile_bars = _candidate_parameter_bars(slot, "profile_hours")
        window_hours = profile_bars * (1 if slot.timeframe == "1h" else 4)
        if type(profile_stream) is not VerifiedProfileStream:
            raise TypeError(
                "family B requires an exact factory-issued registered profile stream"
            )
        verify_profile_stream(profile_stream)
        if (
            profile_stream.aggregate_series_sha256 != series.series_sha256
            or profile_stream.window_hours != window_hours
        ):
            raise ValueError("family B requires a matching verified profile stream")
        profile_bin_step = profile_stream.bin_step
        profile_stream_sha256 = profile_stream.stream_sha256
        profile_definition_id = (
            "rolling-1m:uniform-touched-v1:value-area=0.70:"
            f"window-hours={window_hours}:fixed-step={profile_bin_step}:"
            f"source-config={config_version}"
        )
    elif profile_stream is not None:
        raise ValueError("profile stream is valid only for family B")

    parent_id = None
    parent_slot_id = None
    if slot.family in ("B", "E"):
        _validate_candidate_parent(
            slot,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            source_segment_id=series.segment_id,
            parent=parent_a_candidate,
        )
        assert parent_a_candidate is not None
        parent_id = parent_a_candidate.candidate_id
        parent_slot_id = parent_a_candidate.slot.slot_id

    return CandidateDefinition(
        slot=slot,
        source_publication_sha256=series.publication_sha256,
        source_series_sha256=series.series_sha256,
        source_segment_id=series.segment_id,
        aggregate_config_version=config_version,
        a_selector_grid=selector_grid,
        profile_bin_step=profile_bin_step,
        profile_definition_id=profile_definition_id,
        profile_stream_sha256=profile_stream_sha256,
        parent_a_candidate_id=parent_id,
        parent_a_slot_id=parent_slot_id,
        factory_token=_CANDIDATE_DEFINITION_FACTORY_TOKEN,
    )


def _validate_candidate_parent(
    slot: ValidationSlot,
    *,
    source_publication_sha256: str,
    source_series_sha256: str,
    source_segment_id: int,
    parent: CandidateDefinition | None,
) -> None:
    if parent is None or parent.family != "A":
        raise ValueError("B/E candidates require a registered parent family A candidate")
    if (
        parent.source_series_sha256 != source_series_sha256
        or parent.source_publication_sha256 != source_publication_sha256
        or parent.source_segment_id != source_segment_id
        or parent.timeframe != slot.timeframe
        or parent.slot.direction != slot.direction
    ):
        raise ValueError("parent A candidate does not match the subordinate source series/grid")
    if slot.family == "E":
        parameters = dict(parent.parameters)
        if parameters.get("detector") != "donchian_breakout":
            raise ValueError("family E requires an exact A Donchian opportunity parent")
        if _candidate_parameter_bars(parent.slot, "lookback_hours") != (
            _candidate_parameter_bars(slot, "donchian_hours")
        ):
            raise ValueError(
                "family E parent Donchian lookback does not match its opportunity grid"
            )
    elif hash_json("A-selector-grid-entry-v1", parent.slot.to_dict()) not in FROZEN_A_SELECTOR_GRID:
        raise ValueError("family B parent must be one exact registered A selector-grid candidate")


def _candidate_parameter_bars(slot: ValidationSlot, name: str) -> int:
    parameters = dict(slot.parameters)
    bars = _target_bars(int(parameters[name]), slot.timeframe)
    if (
        slot.kind is ValidationSlotKind.PERTURBATION
        and parameters.get("perturbed_parameter") == name
    ):
        return int(parameters["candidate_bars"])
    return bars


@dataclass(frozen=True, slots=True)
class ValidationProgrammeConfig:
    """Every immutable prerequisite and policy bound into one VP identity."""

    task14_closeout_commit: str
    task14_evidence_commit: str
    task15_plan_commit: str
    task15_evidence_commit: str
    implementation_plan_checkpoint: str
    implementation_plan_evidence_commit: str
    implementation_plan_document_sha256: str
    code_commit: str
    lockfile_sha256: str
    dataset_sha256: str
    cost_policy_sha256: str
    control_policy_sha256: str
    split_sha256: str
    profile_config_sha256: str
    source_price_precision_sha256: str
    families: tuple[str, ...]
    roster: tuple[ValidationSlot, ...]
    work_budget: ValidationWorkBudget
    _roster_sha256: str = field(init=False, repr=False, compare=False)
    _config_dict: Mapping[str, object] = field(init=False, repr=False, compare=False)
    _config_sha256: str = field(init=False, repr=False, compare=False)
    _programme_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        authoritative = {
            "task14_closeout_commit": _TASK14_CLOSEOUT,
            "task14_evidence_commit": _TASK14_EVIDENCE,
            "task15_plan_commit": _TASK15_PLAN,
            "task15_evidence_commit": _TASK15_EVIDENCE,
            "implementation_plan_checkpoint": _IMPLEMENTATION_PLAN_CHECKPOINT,
            "implementation_plan_evidence_commit": _IMPLEMENTATION_PLAN_EVIDENCE,
            "implementation_plan_document_sha256": _IMPLEMENTATION_PLAN_DOCUMENT,
        }
        for field_name, expected in authoritative.items():
            if getattr(self, field_name) != expected:
                raise ValueError(f"{field_name} does not match the authoritative Phase 5 lineage")
        for field_name in (
            "task14_closeout_commit",
            "task14_evidence_commit",
            "task15_plan_commit",
            "task15_evidence_commit",
            "implementation_plan_checkpoint",
            "implementation_plan_evidence_commit",
            "code_commit",
        ):
            if _GIT_SHA.fullmatch(getattr(self, field_name)) is None:
                raise ValueError(f"{field_name} must be a lower-case 40-character Git SHA")
        for field_name in (
            "implementation_plan_document_sha256",
            "lockfile_sha256",
            "dataset_sha256",
            "cost_policy_sha256",
            "control_policy_sha256",
            "split_sha256",
            "profile_config_sha256",
            "source_price_precision_sha256",
        ):
            if _SHA256.fullmatch(getattr(self, field_name)) is None:
                raise ValueError(f"{field_name} must be a lower-case SHA-256")
        if self.families != EXPECTED_FAMILIES:
            raise ValueError("validation families must remain ordered A, B, G, E, D")
        freeze_validation_slot_roster(self.roster)
        if not isinstance(self.work_budget, ValidationWorkBudget):
            raise TypeError("validation work budget must be a ValidationWorkBudget")
        self.work_budget._validate_authoritative_ceilings()
        roster_sha256 = validation_roster_sha256(self.roster)
        object.__setattr__(self, "_roster_sha256", roster_sha256)
        object.__setattr__(
            self,
            "_config_dict",
            {
                "task14_closeout_commit": self.task14_closeout_commit,
                "task14_evidence_commit": self.task14_evidence_commit,
                "task15_plan_commit": self.task15_plan_commit,
                "task15_evidence_commit": self.task15_evidence_commit,
                "implementation_plan_checkpoint": self.implementation_plan_checkpoint,
                "implementation_plan_evidence_commit": self.implementation_plan_evidence_commit,
                "implementation_plan_document_sha256": self.implementation_plan_document_sha256,
                "code_commit": self.code_commit,
                "lockfile_sha256": self.lockfile_sha256,
                "dataset_sha256": self.dataset_sha256,
                "cost_policy_sha256": self.cost_policy_sha256,
                "control_policy_sha256": self.control_policy_sha256,
                "split_sha256": self.split_sha256,
                "profile_config_sha256": self.profile_config_sha256,
                "source_price_precision_sha256": self.source_price_precision_sha256,
                "families": self.families,
                "roster": tuple(_freeze_config_value(slot.to_dict()) for slot in self.roster),
                "roster_sha256": roster_sha256,
                "work_budget": _freeze_config_value(self.work_budget.to_dict()),
                "work_budget_sha256": self.work_budget.sha256,
            },
        )
        object.__setattr__(
            self, "_config_sha256", hash_json("validation-programme-config", self.to_dict())
        )
        object.__setattr__(self, "_programme_id", programme_id(self.to_dict()))

    @property
    def sha256(self) -> str:
        return self._config_sha256

    @property
    def roster_sha256(self) -> str:
        return self._roster_sha256

    @property
    def programme_id(self) -> str:
        return self._programme_id

    def to_dict(self) -> dict[str, object]:
        raw_roster = cast(tuple[Mapping[str, object], ...], self._config_dict["roster"])
        raw_work_budget = cast(Mapping[str, int], self._config_dict["work_budget"])
        return {
            "task14_closeout_commit": self.task14_closeout_commit,
            "task14_evidence_commit": self.task14_evidence_commit,
            "task15_plan_commit": self.task15_plan_commit,
            "task15_evidence_commit": self.task15_evidence_commit,
            "implementation_plan_checkpoint": self.implementation_plan_checkpoint,
            "implementation_plan_evidence_commit": self.implementation_plan_evidence_commit,
            "implementation_plan_document_sha256": self.implementation_plan_document_sha256,
            "code_commit": self.code_commit,
            "lockfile_sha256": self.lockfile_sha256,
            "dataset_sha256": self.dataset_sha256,
            "cost_policy_sha256": self.cost_policy_sha256,
            "control_policy_sha256": self.control_policy_sha256,
            "split_sha256": self.split_sha256,
            "profile_config_sha256": self.profile_config_sha256,
            "source_price_precision_sha256": self.source_price_precision_sha256,
            "families": list(self.families),
            "roster": [cast(dict[str, object], _copy_config_value(item)) for item in raw_roster],
            "roster_sha256": self._roster_sha256,
            "work_budget": dict(raw_work_budget),
            "work_budget_sha256": self._config_dict["work_budget_sha256"],
        }


def evaluation_id_for_slot(
    config: ValidationProgrammeConfig,
    slot: ValidationSlot,
) -> str:
    """Return one VR identity only for a slot owned by the exact programme roster."""

    if slot not in config.roster:
        raise ValueError("evaluation slot is not owned by the validation programme")
    return evaluation_id(config.programme_id, slot.to_dict())


__all__ = [
    "CandidateDefinition",
    "EXPECTED_FAMILIES",
    "FROZEN_A_SELECTOR_GRID",
    "VALIDATION_SLOT_ROSTER",
    "ExecutionStatus",
    "ScientificDecision",
    "ValidationProgrammeConfig",
    "ValidationSlot",
    "ValidationSlotKind",
    "ValidationTerminalState",
    "ValidationWorkBudget",
    "ValidationWorkBudgetViolation",
    "ValidationWorkDemand",
    "candidate_definition_for_slot",
    "candidate_definition_for_verified_series",
    "evaluation_id_for_slot",
    "freeze_validation_slot_roster",
    "validation_roster_sha256",
]
