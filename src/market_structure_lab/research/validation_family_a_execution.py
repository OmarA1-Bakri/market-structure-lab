"""Bounded detector execution and outcome-blind planning for Family A slots.

The plan proves aggregate-bar clocks and exact cost requests but opens no minute
path and attaches no outcome. Inference, controls, and terminal decisions remain
outside this adapter.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self, Sequence, cast
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregateRowV2,
    VerifiedAggregateSeriesV2,
)
from market_structure_lab.research.candidates import (
    CandidateSignal,
    VerifiedCandidateSeriesV2,
    bridge_verified_aggregate_series_v2,
    detect_candidate_signals,
    verify_candidate_signal,
)
from market_structure_lab.research.costs import CostApplication, CostSide
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    CandidateDefinition,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    candidate_definition_for_verified_series,
)
from market_structure_lab.research.validation_v3_cost_policy import (
    CostPolicyRequestV3,
    VerifiedCostPolicyAuthorityV3,
    verify_cost_policy_authority_v3,
)

_EVIDENCE_FACTORY = object()
_SLOT_EVIDENCE_FACTORY = object()
_OUTCOME_PLAN_FACTORY = object()
_OUTCOME_PLAN_ITEM_FACTORY = object()
_COST_COVERAGE_FACTORY = object()


@dataclass(frozen=True, slots=True)
class FamilyASeriesBinding:
    """Expected identity of the one authenticated aggregate series."""

    symbol: str
    timeframe: str
    interval_index: int
    segment_id: int
    publication_sha256: str
    series_sha256: str

    @classmethod
    def from_verified_series(cls, series: object) -> Self:
        if type(series) is not VerifiedAggregateSeriesV2:
            raise TypeError("Family A binding requires a factory-issued aggregate series")
        verified = cast(VerifiedAggregateSeriesV2, series).verify_original()
        return cls(
            symbol=verified.key.symbol,
            timeframe=verified.key.target_timeframe,
            interval_index=verified.key.interval_index,
            segment_id=verified.key.segment_id,
            publication_sha256=verified.aggregate_publication_sha256,
            series_sha256=verified.series_identity,
        )


@dataclass(frozen=True, slots=True)
class FamilyADetectorSlotEvidence:
    """Factory-issued signals for one exact frozen Family A detector slot."""

    slot: ValidationSlot
    definition: CandidateDefinition
    signals: tuple[CandidateSignal, ...]
    signal_ids_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SLOT_EVIDENCE_FACTORY:
            raise TypeError("Family A slot evidence requires its execution factory")

    @property
    def slot_id(self) -> str:
        return self.slot.slot_id

    @property
    def emitted_signal_count(self) -> int:
        return len(self.signals)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyADetectorExecutionEvidence:
    """Factory-issued, source-bound output of the detector-only stage."""

    binding: FamilyASeriesBinding
    budget_sha256: str
    work_demand: ValidationWorkDemand
    aggregate_identity: str
    aggregate_budget_sha256: str
    aggregate_row_count: int
    slots: tuple[FamilyADetectorSlotEvidence, ...]
    emitted_signal_count: int
    evidence_sha256: str
    detector_series: VerifiedCandidateSeriesV2
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _EVIDENCE_FACTORY:
            raise TypeError("Family A detector evidence requires its execution factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_a_detector_execution(self))


@dataclass(frozen=True, slots=True)
class _EvidenceRegistration:
    evidence: weakref.ReferenceType[FamilyADetectorExecutionEvidence]
    aggregate: VerifiedAggregateSeriesV2
    work_budget: ValidationWorkBudget
    work_demand: ValidationWorkDemand
    snapshot: tuple[object, ...]


_EVIDENCE_REGISTRY: dict[int, _EvidenceRegistration] = {}


def family_a_detector_slots(timeframe: str) -> tuple[ValidationSlot, ...]:
    """Return all frozen Family A core/adjacent-lookback slots for a timeframe."""

    if timeframe not in ("1h", "4h"):
        raise ValueError("Family A detector timeframe must be 1h or 4h")
    return tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "A"
        and slot.timeframe == timeframe
        and slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
    )


def execute_family_a_detector_series(
    aggregate_series: object,
    *,
    binding: FamilyASeriesBinding,
    work_budget: ValidationWorkBudget,
    declared_event_ceiling: int,
    outcome_inputs: object | None = None,
) -> FamilyADetectorExecutionEvidence:
    """Traverse every applicable frozen Family A detector slot for one series."""

    _require_exact_work_budget(work_budget)
    if type(aggregate_series) is not VerifiedAggregateSeriesV2:
        raise TypeError("Family A execution requires a factory-issued aggregate series")
    series = cast(VerifiedAggregateSeriesV2, aggregate_series)
    return execute_family_a_detector_slots(
        series,
        family_a_detector_slots(series.key.target_timeframe),
        binding=binding,
        work_budget=work_budget,
        declared_event_ceiling=declared_event_ceiling,
        outcome_inputs=outcome_inputs,
    )


def execute_family_a_detector_slot(
    aggregate_series: object,
    slot: ValidationSlot,
    *,
    binding: FamilyASeriesBinding,
    work_budget: ValidationWorkBudget,
    declared_event_ceiling: int,
    outcome_inputs: object | None = None,
) -> FamilyADetectorExecutionEvidence:
    """Execute exactly one canonical Family A core or adjacent-lookback slot."""

    _require_exact_work_budget(work_budget)
    _require_canonical_family_a_detector_slot(slot)
    return execute_family_a_detector_slots(
        aggregate_series,
        (slot,),
        binding=binding,
        work_budget=work_budget,
        declared_event_ceiling=declared_event_ceiling,
        outcome_inputs=outcome_inputs,
    )


def execute_family_a_detector_slots(
    aggregate_series: object,
    slots: Sequence[ValidationSlot],
    *,
    binding: FamilyASeriesBinding,
    work_budget: ValidationWorkBudget,
    declared_event_ceiling: int,
    outcome_inputs: object | None = None,
) -> FamilyADetectorExecutionEvidence:
    """Execute an exact bounded Family A detector subset without attaching outcomes."""

    if outcome_inputs is not None:
        raise ValueError("outcome inputs are forbidden before the outcome-attachment stage")
    _require_exact_work_budget(work_budget)
    if type(aggregate_series) is not VerifiedAggregateSeriesV2:
        raise TypeError("Family A execution requires a factory-issued aggregate series")
    if not isinstance(binding, FamilyASeriesBinding):
        raise TypeError("Family A execution requires an exact series binding")
    if not isinstance(slots, (tuple, list)):
        raise TypeError("Family A detector slots must be a bounded sequence")
    if not slots:
        raise ValueError("Family A detector slots must not be empty")
    canonical_slots = tuple(slots)
    for slot in canonical_slots:
        _require_canonical_family_a_detector_slot(slot)
    if len({slot.slot_id for slot in canonical_slots}) != len(canonical_slots):
        raise ValueError("Family A detector slots must be unique")

    series = cast(VerifiedAggregateSeriesV2, aggregate_series)
    demand = _preflight(series, canonical_slots, binding, work_budget, declared_event_ceiling)
    series.verify_original()
    detector_series = bridge_verified_aggregate_series_v2(series)

    evidence_slots: list[FamilyADetectorSlotEvidence] = []
    total_signals = 0
    for slot in canonical_slots:
        definition = candidate_definition_for_verified_series(slot, detector_series)
        remaining = declared_event_ceiling - total_signals
        signals = detect_candidate_signals(
            definition,
            detector_series,
            max_emitted_signals=remaining,
        )
        total_signals += len(signals)
        evidence_slots.append(
            FamilyADetectorSlotEvidence(
                slot=slot,
                definition=definition,
                signals=signals,
                signal_ids_sha256=hash_json(
                    "phase5-family-a-slot-signal-order-v1",
                    [signal.signal_id for signal in signals],
                ),
                _factory_token=_SLOT_EVIDENCE_FACTORY,
            )
        )

    evidence_tuple = tuple(evidence_slots)
    payload = _identity_payload(
        binding=binding,
        budget_sha256=work_budget.sha256,
        work_demand=demand,
        aggregate_identity=series.aggregate_identity.value,
        aggregate_budget_sha256=series.budget_sha256,
        aggregate_row_count=series.row_count,
        slots=evidence_tuple,
        emitted_signal_count=total_signals,
    )
    evidence = FamilyADetectorExecutionEvidence(
        binding=binding,
        budget_sha256=work_budget.sha256,
        work_demand=demand,
        aggregate_identity=series.aggregate_identity.value,
        aggregate_budget_sha256=series.budget_sha256,
        aggregate_row_count=series.row_count,
        slots=evidence_tuple,
        emitted_signal_count=total_signals,
        evidence_sha256=hash_json("phase5-family-a-detector-execution-v1", payload),
        detector_series=detector_series,
        _factory_token=_EVIDENCE_FACTORY,
    )
    identifier = id(evidence)

    def cleanup(reference: weakref.ReferenceType[FamilyADetectorExecutionEvidence]) -> None:
        current = _EVIDENCE_REGISTRY.get(identifier)
        if current is not None and current.evidence is reference:
            _EVIDENCE_REGISTRY.pop(identifier, None)

    _EVIDENCE_REGISTRY[identifier] = _EvidenceRegistration(
        evidence=weakref.ref(evidence, cleanup),
        aggregate=series,
        work_budget=work_budget,
        work_demand=demand,
        snapshot=_snapshot(evidence),
    )
    return evidence


def verify_family_a_detector_execution(evidence: object) -> FamilyADetectorExecutionEvidence:
    """Revalidate factory provenance, original aggregate bytes, and all signals."""

    if type(evidence) is not FamilyADetectorExecutionEvidence:
        raise TypeError("Family A detector evidence must be exact and factory-issued")
    typed = cast(FamilyADetectorExecutionEvidence, evidence)
    registration = _EVIDENCE_REGISTRY.get(id(typed))
    if (
        registration is None
        or registration.evidence() is not typed
        or registration.snapshot != _snapshot(typed)
    ):
        raise ValueError("Family A detector evidence is unregistered or mutated")
    registration.aggregate.verify_original()
    demand = _preflight(
        registration.aggregate,
        tuple(item.slot for item in typed.slots),
        typed.binding,
        registration.work_budget,
        registration.work_demand.events,
    )
    if demand != registration.work_demand:
        raise ValueError("Family A detector work demand differs")
    if typed.work_demand != registration.work_demand:
        raise ValueError("Family A detector evidence work demand differs")
    typed.detector_series.verify()
    if typed.budget_sha256 != registration.work_budget.sha256:
        raise ValueError("Family A detector evidence budget identity differs")
    expected_payload = _identity_payload(
        binding=typed.binding,
        budget_sha256=typed.budget_sha256,
        work_demand=typed.work_demand,
        aggregate_identity=typed.aggregate_identity,
        aggregate_budget_sha256=typed.aggregate_budget_sha256,
        aggregate_row_count=typed.aggregate_row_count,
        slots=typed.slots,
        emitted_signal_count=typed.emitted_signal_count,
    )
    if typed.evidence_sha256 != hash_json(
        "phase5-family-a-detector-execution-v1", expected_payload
    ):
        raise ValueError("Family A detector evidence identity differs")
    if typed.emitted_signal_count != sum(item.emitted_signal_count for item in typed.slots):
        raise ValueError("Family A detector evidence signal count differs")
    for item in typed.slots:
        expected = candidate_definition_for_verified_series(item.slot, typed.detector_series)
        if item.definition != expected:
            raise ValueError("Family A detector definition differs from its exact slot and series")
        if item.signal_ids_sha256 != hash_json(
            "phase5-family-a-slot-signal-order-v1",
            [signal.signal_id for signal in item.signals],
        ):
            raise ValueError("Family A detector signal order identity differs")
        for signal in item.signals:
            verify_candidate_signal(signal)
    return typed


def _require_canonical_family_a_detector_slot(slot: object) -> ValidationSlot:
    if type(slot) is not ValidationSlot or not any(
        slot is canonical for canonical in VALIDATION_SLOT_ROSTER
    ):
        raise TypeError("Family A execution requires an exact canonical validation slot")
    typed = cast(ValidationSlot, slot)
    if typed.family != "A" or typed.kind not in (
        ValidationSlotKind.CORE,
        ValidationSlotKind.PERTURBATION,
    ):
        raise ValueError("Family A execution accepts only A core/adjacent-lookback slots")
    return typed


def _require_exact_work_budget(work_budget: object) -> ValidationWorkBudget:
    if type(work_budget) is not ValidationWorkBudget:
        raise TypeError("Family A execution requires the exact frozen validation work budget")
    return cast(ValidationWorkBudget, work_budget)


def _preflight(
    series: VerifiedAggregateSeriesV2,
    slots: tuple[ValidationSlot, ...],
    binding: FamilyASeriesBinding,
    work_budget: ValidationWorkBudget,
    declared_event_ceiling: int,
) -> ValidationWorkDemand:
    expected = (
        series.key.symbol,
        series.key.target_timeframe,
        series.key.interval_index,
        series.key.segment_id,
        series.aggregate_publication_sha256,
        series.series_identity,
    )
    actual = (
        binding.symbol,
        binding.timeframe,
        binding.interval_index,
        binding.segment_id,
        binding.publication_sha256,
        binding.series_sha256,
    )
    if actual != expected:
        raise ValueError("Family A aggregate series does not match its exact requested binding")
    if any(slot.timeframe != binding.timeframe for slot in slots):
        raise ValueError("Family A slot timeframe does not match the aggregate series")
    demand = ValidationWorkDemand(
        aggregate_bars=series.row_count,
        symbols=1,
        ranges=1,
        candidates=len(slots),
        trials=1_104,
        events=declared_event_ceiling,
        path_cells=series.row_count * len(slots),
        evaluations=1_104,
        bootstrap_draws=4_800,
    )
    work_budget.preflight(demand, deferred_work=slots)
    return demand


def _identity_payload(
    *,
    binding: FamilyASeriesBinding,
    budget_sha256: str,
    work_demand: ValidationWorkDemand,
    aggregate_identity: str,
    aggregate_budget_sha256: str,
    aggregate_row_count: int,
    slots: tuple[FamilyADetectorSlotEvidence, ...],
    emitted_signal_count: int,
) -> dict[str, object]:
    return {
        "binding": {
            "symbol": binding.symbol,
            "timeframe": binding.timeframe,
            "interval_index": binding.interval_index,
            "segment_id": binding.segment_id,
            "publication_sha256": binding.publication_sha256,
            "series_sha256": binding.series_sha256,
        },
        "budget_sha256": budget_sha256,
        "work_demand": {
            name: getattr(work_demand, name) for name in work_demand.__dataclass_fields__
        },
        "aggregate_identity": aggregate_identity,
        "aggregate_budget_sha256": aggregate_budget_sha256,
        "aggregate_row_count": aggregate_row_count,
        "slots": [
            {
                "slot_id": item.slot_id,
                "candidate_id": item.definition.candidate_id,
                "signal_ids_sha256": item.signal_ids_sha256,
                "emitted_signal_count": item.emitted_signal_count,
            }
            for item in slots
        ],
        "emitted_signal_count": emitted_signal_count,
        "outcome_rows": 0,
        "final_access_records": 0,
    }


def _snapshot(evidence: FamilyADetectorExecutionEvidence) -> tuple[object, ...]:
    return (
        evidence.binding,
        evidence.budget_sha256,
        evidence.work_demand,
        evidence.aggregate_identity,
        evidence.aggregate_budget_sha256,
        evidence.aggregate_row_count,
        evidence.slots,
        evidence.emitted_signal_count,
        evidence.evidence_sha256,
        evidence.detector_series,
    )


class FamilyAAggregatePathStatus(StrEnum):
    """Whether exact aggregate bars prove a legal outcome clock."""

    READY = "ready"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class FamilyAAggregatePathResolution:
    """Outcome-blind resolution of one exact aggregate-bar clock."""

    status: FamilyAAggregatePathStatus
    reason: str
    horizon_bars: int
    delay_bars: int
    entry_time: datetime | None
    exit_time: datetime | None
    aggregate_path_row_sha256: tuple[str, ...]


def resolve_family_a_aggregate_path(
    rows: Sequence[AggregateRowV2],
    *,
    legal_entry: datetime,
    symbol: str,
    timeframe: str,
    interval_index: int,
    segment_id: int,
    horizon_hours: int,
    entry_index: int,
    delay_bars: int = 0,
) -> FamilyAAggregatePathResolution:
    """Resolve a bar-count clock; never infer it from wall-clock arithmetic."""

    if not isinstance(rows, (tuple, list)) or any(type(row) is not AggregateRowV2 for row in rows):
        raise TypeError("Family A aggregate clock requires exact aggregate rows")
    if timeframe not in ("1h", "4h"):
        raise ValueError("Family A aggregate clock timeframe must be 1h or 4h")
    timeframe_hours = 1 if timeframe == "1h" else 4
    if (
        isinstance(horizon_hours, bool)
        or not isinstance(horizon_hours, int)
        or horizon_hours < 1
        or horizon_hours % timeframe_hours
    ):
        raise ValueError("Family A horizon must contain complete aggregate bars")
    if isinstance(delay_bars, bool) or not isinstance(delay_bars, int) or delay_bars < 0:
        raise ValueError("Family A delay_bars must be a non-negative integer")
    if isinstance(entry_index, bool) or not isinstance(entry_index, int) or entry_index < 0:
        raise ValueError("Family A entry_index must be a non-negative integer")
    horizon_bars = horizon_hours // timeframe_hours

    def incomplete(reason: str) -> FamilyAAggregatePathResolution:
        return FamilyAAggregatePathResolution(
            status=FamilyAAggregatePathStatus.INCOMPLETE,
            reason=reason,
            horizon_bars=horizon_bars,
            delay_bars=delay_bars,
            entry_time=None,
            exit_time=None,
            aggregate_path_row_sha256=(),
        )

    if entry_index >= len(rows) or rows[entry_index].timestamp != legal_entry:
        return incomplete("legal_entry_absent_from_verified_aggregate_series")
    signal_entry_index = entry_index
    entry_index = signal_entry_index + delay_bars
    exit_index = entry_index + horizon_bars
    if signal_entry_index < 1 or exit_index >= len(rows):
        return incomplete("incomplete_aggregate_horizon")
    expected_step = timedelta(hours=timeframe_hours)
    for previous, current in zip(
        rows[signal_entry_index - 1 : exit_index],
        rows[signal_entry_index : exit_index + 1],
        strict=True,
    ):
        if (
            current.timestamp != previous.timestamp + expected_step
            or current.symbol != symbol
            or current.target_timeframe != timeframe
            or current.interval_index != interval_index
            or current.segment_id != segment_id
        ):
            return incomplete("aggregate_path_crosses_gap_or_segment_boundary")
    interval_rows = rows[entry_index:exit_index]
    return FamilyAAggregatePathResolution(
        status=FamilyAAggregatePathStatus.READY,
        reason="exact_contiguous_aggregate_path",
        horizon_bars=horizon_bars,
        delay_bars=delay_bars,
        entry_time=rows[entry_index].timestamp,
        exit_time=rows[exit_index].timestamp,
        aggregate_path_row_sha256=tuple(row.row_sha256 for row in interval_rows),
    )


@dataclass(frozen=True, slots=True)
class FamilyAOutcomePlanItem:
    """Outcome-blind path and cost requests derived from one detector signal."""

    slot_id: str
    signal_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    direction: int
    horizon_hours: int
    horizon_bars: int
    legal_entry: datetime
    path_status: FamilyAAggregatePathStatus
    path_reason: str
    path_end: datetime | None
    aggregate_path_row_sha256: tuple[str, ...]
    expected_minute_rows: int
    cost_side: CostSide
    cost_request: CostPolicyRequestV3 | None
    delayed_path_status: FamilyAAggregatePathStatus | None
    delayed_path_reason: str | None
    delayed_cost_request: CostPolicyRequestV3 | None
    delayed_path_end: datetime | None
    delayed_aggregate_path_row_sha256: tuple[str, ...]
    required_applications: tuple[CostApplication, ...]
    missed_fill_required: bool
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _OUTCOME_PLAN_ITEM_FACTORY:
            raise TypeError("Family A outcome plan items require their planner factory")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FamilyAOutcomeMaterializationPlan:
    """Factory-issued metadata plan that opens no outcome or minute-path source."""

    detector_evidence_sha256: str
    work_budget_sha256: str
    venue: str
    work_demand: ValidationWorkDemand
    items: tuple[FamilyAOutcomePlanItem, ...]
    required_cost_events: tuple[CostPolicyRequestV3, ...]
    plan_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _OUTCOME_PLAN_FACTORY:
            raise TypeError("Family A outcome plans require their planner factory")

    def verify(self) -> Self:
        return cast(Self, verify_family_a_outcome_materialization_plan(self))


@dataclass(frozen=True, slots=True)
class _OutcomePlanRegistration:
    plan: weakref.ReferenceType[FamilyAOutcomeMaterializationPlan]
    detector_evidence: FamilyADetectorExecutionEvidence
    work_budget: ValidationWorkBudget
    snapshot: tuple[object, ...]


_OUTCOME_PLAN_REGISTRY: dict[int, _OutcomePlanRegistration] = {}


class FamilyACostCoverageStatus(StrEnum):
    """Whether cost coverage permits later outcome materialization."""

    READY = "ready"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class FamilyACostCoverageMaterial:
    """Pre-outcome cost-coverage result; never a scientific decision."""

    plan_sha256: str
    authority_identity: str
    status: FamilyACostCoverageStatus
    reason: str
    cost_policy_sha256: str | None
    event_coverage_sha256: str | None
    outcome_count: int
    path_row_count: int
    net_returns: tuple[float, ...]
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _COST_COVERAGE_FACTORY:
            raise TypeError("Family A cost coverage material requires its verifier factory")
        if self.outcome_count or self.path_row_count or self.net_returns:
            raise ValueError("pre-outcome cost coverage cannot contain outcomes, rows, or returns")


def prepare_family_a_outcome_materialization(
    detector_evidence: object,
    *,
    venue: str,
    work_budget: ValidationWorkBudget,
) -> FamilyAOutcomeMaterializationPlan:
    """Freeze path and cost requests without opening outcomes or minute rows."""

    if type(detector_evidence) is not FamilyADetectorExecutionEvidence:
        raise TypeError("Family A outcome planning requires exact detector evidence")
    _require_exact_work_budget(work_budget)
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError("Family A outcome planning venue must not be empty")
    evidence = cast(FamilyADetectorExecutionEvidence, detector_evidence)

    # Use the already declared immutable evidence counts for admission.  No signal,
    # path, or outcome iterable is touched until this demand passes.
    event_ceiling = evidence.emitted_signal_count * 2
    demand = ValidationWorkDemand(
        aggregate_bars=evidence.aggregate_row_count,
        symbols=1,
        ranges=1,
        candidates=len(evidence.slots),
        trials=1_104,
        events=event_ceiling,
        outcomes=evidence.emitted_signal_count * 2,
        path_cells=evidence.emitted_signal_count * 2 * 24 * 60,
        evaluations=1_104,
        bootstrap_draws=4_800,
    )
    work_budget.preflight(demand, deferred_work=evidence.slots)
    evidence.verify()
    registration = _EVIDENCE_REGISTRY[id(evidence)]
    aggregate_rows = registration.aggregate.rows
    aggregate_index = {row.timestamp: index for index, row in enumerate(aggregate_rows)}
    if len(aggregate_index) != len(aggregate_rows):
        raise ValueError("verified aggregate series contains duplicate timestamps")

    items: list[FamilyAOutcomePlanItem] = []
    required_events: list[CostPolicyRequestV3] = []
    for slot_evidence in evidence.slots:
        delay_applicable = (
            slot_evidence.slot.kind is ValidationSlotKind.CORE and slot_evidence.slot.primary
        )
        timeframe_hours = 1 if slot_evidence.slot.timeframe == "1h" else 4
        for signal in slot_evidence.signals:
            horizon = slot_evidence.slot.horizon_hours
            entry_index = aggregate_index.get(signal.legal_entry, len(aggregate_rows))
            path = resolve_family_a_aggregate_path(
                aggregate_rows,
                legal_entry=signal.legal_entry,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                interval_index=evidence.binding.interval_index,
                segment_id=signal.segment_id,
                horizon_hours=horizon,
                entry_index=entry_index,
            )
            request = (
                None
                if path.entry_time is None
                else CostPolicyRequestV3(
                    event_id=signal.signal_id,
                    event_timestamp=path.entry_time,
                    venue=venue,
                    symbol=signal.symbol,
                    timeframe=signal.timeframe,
                )
            )
            delayed_request = None
            delayed_status = None
            delayed_reason = None
            delayed_path_end = None
            delayed_row_sha256: tuple[str, ...] = ()
            applications: tuple[CostApplication, ...] = ()
            if request is not None:
                applications = (CostApplication.BASE, CostApplication.STRESS)
            if delay_applicable:
                delayed_path = resolve_family_a_aggregate_path(
                    aggregate_rows,
                    legal_entry=signal.legal_entry,
                    symbol=signal.symbol,
                    timeframe=signal.timeframe,
                    interval_index=evidence.binding.interval_index,
                    segment_id=signal.segment_id,
                    horizon_hours=horizon,
                    entry_index=entry_index,
                    delay_bars=1,
                )
                delayed_status = delayed_path.status
                delayed_reason = delayed_path.reason
                delayed_path_end = delayed_path.exit_time
                delayed_row_sha256 = delayed_path.aggregate_path_row_sha256
                if delayed_path.entry_time is not None:
                    delayed_request = CostPolicyRequestV3(
                        event_id=f"{signal.signal_id}:delay-one-bar",
                        event_timestamp=delayed_path.entry_time,
                        venue=venue,
                        symbol=signal.symbol,
                        timeframe=signal.timeframe,
                    )
                    applications = (*applications, CostApplication.DELAYED)
            item = FamilyAOutcomePlanItem(
                slot_id=slot_evidence.slot_id,
                signal_id=signal.signal_id,
                candidate_id=signal.candidate_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                direction=signal.direction,
                horizon_hours=horizon,
                horizon_bars=horizon // timeframe_hours,
                legal_entry=signal.legal_entry,
                path_status=path.status,
                path_reason=path.reason,
                path_end=path.exit_time,
                aggregate_path_row_sha256=path.aggregate_path_row_sha256,
                expected_minute_rows=horizon * 60,
                cost_side=CostSide.LONG if signal.direction == 1 else CostSide.SHORT,
                cost_request=request,
                delayed_path_status=delayed_status,
                delayed_path_reason=delayed_reason,
                delayed_cost_request=delayed_request,
                delayed_path_end=delayed_path_end,
                delayed_aggregate_path_row_sha256=delayed_row_sha256,
                required_applications=applications,
                missed_fill_required=True,
                _factory_token=_OUTCOME_PLAN_ITEM_FACTORY,
            )
            items.append(item)
            if request is not None:
                required_events.append(request)
            if delayed_request is not None:
                required_events.append(delayed_request)

    items_tuple = tuple(items)
    requests_tuple = tuple(required_events)
    payload = _outcome_plan_payload(
        detector_evidence_sha256=evidence.evidence_sha256,
        work_budget_sha256=work_budget.sha256,
        venue=venue,
        work_demand=demand,
        items=items_tuple,
        required_cost_events=requests_tuple,
    )
    plan = FamilyAOutcomeMaterializationPlan(
        detector_evidence_sha256=evidence.evidence_sha256,
        work_budget_sha256=work_budget.sha256,
        venue=venue,
        work_demand=demand,
        items=items_tuple,
        required_cost_events=requests_tuple,
        plan_sha256=hash_json("phase5-family-a-outcome-materialization-plan-v1", payload),
        _factory_token=_OUTCOME_PLAN_FACTORY,
    )
    identifier = id(plan)

    def cleanup(reference: weakref.ReferenceType[FamilyAOutcomeMaterializationPlan]) -> None:
        current = _OUTCOME_PLAN_REGISTRY.get(identifier)
        if current is not None and current.plan is reference:
            _OUTCOME_PLAN_REGISTRY.pop(identifier, None)

    _OUTCOME_PLAN_REGISTRY[identifier] = _OutcomePlanRegistration(
        plan=weakref.ref(plan, cleanup),
        detector_evidence=evidence,
        work_budget=work_budget,
        snapshot=_outcome_plan_snapshot(plan),
    )
    return plan


def verify_family_a_outcome_materialization_plan(
    plan: object,
) -> FamilyAOutcomeMaterializationPlan:
    """Verify an outcome-blind plan and its original detector evidence."""

    if type(plan) is not FamilyAOutcomeMaterializationPlan:
        raise TypeError("Family A outcome plan must be exact and factory-issued")
    typed = cast(FamilyAOutcomeMaterializationPlan, plan)
    registered = _OUTCOME_PLAN_REGISTRY.get(id(typed))
    if (
        registered is None
        or registered.plan() is not typed
        or registered.snapshot != _outcome_plan_snapshot(typed)
    ):
        raise ValueError("Family A outcome plan is unregistered or mutated")
    registered.detector_evidence.verify()
    registered.work_budget.preflight(typed.work_demand, deferred_work=typed.items)
    payload = _outcome_plan_payload(
        detector_evidence_sha256=typed.detector_evidence_sha256,
        work_budget_sha256=typed.work_budget_sha256,
        venue=typed.venue,
        work_demand=typed.work_demand,
        items=typed.items,
        required_cost_events=typed.required_cost_events,
    )
    if typed.plan_sha256 != hash_json("phase5-family-a-outcome-materialization-plan-v1", payload):
        raise ValueError("Family A outcome plan identity differs")
    return typed


def classify_family_a_cost_coverage(
    plan: object,
    authority: object,
) -> FamilyACostCoverageMaterial:
    """Classify exact V3 coverage without opening paths or applying costs."""

    typed_plan = verify_family_a_outcome_materialization_plan(plan)
    if type(authority) is not VerifiedCostPolicyAuthorityV3:
        raise TypeError("Family A cost coverage requires exact V3 cost authority")
    typed_authority = verify_cost_policy_authority_v3(
        cast(VerifiedCostPolicyAuthorityV3, authority)
    )
    if typed_authority.required_events != typed_plan.required_cost_events:
        raise ValueError("cost authority event population differs from the outcome plan")
    if typed_authority.terminal_state is None:
        status = FamilyACostCoverageStatus.READY
    elif (
        "coverage" in typed_authority.reason
        and typed_authority.terminal_state.decision.value == "inconclusive"
    ):
        status = FamilyACostCoverageStatus.INCOMPLETE
    else:
        raise ValueError(
            f"cost authority is invalid rather than incomplete: {typed_authority.reason}"
        )
    return FamilyACostCoverageMaterial(
        plan_sha256=typed_plan.plan_sha256,
        authority_identity=typed_authority.identity.value,
        status=status,
        reason=typed_authority.reason,
        cost_policy_sha256=typed_authority.cost_policy_sha256,
        event_coverage_sha256=typed_authority.event_coverage_sha256,
        outcome_count=0,
        path_row_count=0,
        net_returns=(),
        _factory_token=_COST_COVERAGE_FACTORY,
    )


def _outcome_plan_payload(
    *,
    detector_evidence_sha256: str,
    work_budget_sha256: str,
    venue: str,
    work_demand: ValidationWorkDemand,
    items: tuple[FamilyAOutcomePlanItem, ...],
    required_cost_events: tuple[CostPolicyRequestV3, ...],
) -> dict[str, object]:
    return {
        "detector_evidence_sha256": detector_evidence_sha256,
        "work_budget_sha256": work_budget_sha256,
        "venue": venue,
        "work_demand": {
            name: getattr(work_demand, name) for name in work_demand.__dataclass_fields__
        },
        "items": [
            {
                "slot_id": item.slot_id,
                "signal_id": item.signal_id,
                "candidate_id": item.candidate_id,
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "direction": item.direction,
                "horizon_hours": item.horizon_hours,
                "horizon_bars": item.horizon_bars,
                "legal_entry": item.legal_entry,
                "path_status": item.path_status.value,
                "path_reason": item.path_reason,
                "path_end": item.path_end,
                "aggregate_path_row_sha256": list(item.aggregate_path_row_sha256),
                "expected_minute_rows": item.expected_minute_rows,
                "cost_side": item.cost_side.value,
                "cost_request": None if item.cost_request is None else item.cost_request.to_dict(),
                "delayed_path_status": None
                if item.delayed_path_status is None
                else item.delayed_path_status.value,
                "delayed_path_reason": item.delayed_path_reason,
                "delayed_cost_request": None
                if item.delayed_cost_request is None
                else item.delayed_cost_request.to_dict(),
                "delayed_path_end": item.delayed_path_end,
                "delayed_aggregate_path_row_sha256": list(item.delayed_aggregate_path_row_sha256),
                "required_applications": [value.value for value in item.required_applications],
                "missed_fill_required": item.missed_fill_required,
            }
            for item in items
        ],
        "required_cost_events": [item.to_dict() for item in required_cost_events],
        "outcome_rows": 0,
        "path_rows": 0,
        "final_access_records": 0,
    }


def _outcome_plan_snapshot(plan: FamilyAOutcomeMaterializationPlan) -> tuple[object, ...]:
    return (
        plan.detector_evidence_sha256,
        plan.work_budget_sha256,
        plan.venue,
        plan.work_demand,
        plan.items,
        plan.required_cost_events,
        plan.plan_sha256,
    )
