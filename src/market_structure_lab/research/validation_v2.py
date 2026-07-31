"""Development-only Phase 5 V2 evidence derivation and slot execution.

The V2 public boundary accepts only publisher-issued publications.  Raw
statistical summaries, selector scores, terminal decisions, and eligibility
flags are deliberately absent: they are derived after the split and every
result is bound to one frozen roster slot.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from math import isfinite
from types import MappingProxyType
import re
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    open_verified_aggregate_series_v2,
    verify_original_aggregate_series_v2,
    verify_validation_aggregate_publication_metadata_v2,
    verify_validation_aggregate_publication_v2,
)
from market_structure_lab.data.validation_precision_authority_v2 import (
    ValidationPrecisionAuthorityV2,
    verify_validation_precision_authority_metadata_v2,
    verify_validation_precision_authority_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    MinutePathReadBudgetV2,
    ScopedSourceAvailabilityV2,
    ValidationSourcePublicationV2,
    VerifiedMinutePathV2,
    read_verified_minute_path_v2,
    verify_original_minute_path_v2,
    verified_scoped_source_availability_bytes_v2,
    verify_validation_source_publication_metadata_v2,
    verify_validation_source_publication_v2,
)
from market_structure_lab.research.candidates import (
    CandidateSignal,
    VerifiedCandidateSeriesV2,
    bridge_verified_aggregate_series_v2,
    detect_candidate_signals,
    verify_candidate_series_v2,
    verify_candidate_signal,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    candidate_definition_for_verified_series,
)
from market_structure_lab.research.outcomes import (
    DevelopmentOutcomeRowV2,
    attach_development_outcome_v2,
)
from market_structure_lab.research.validation_v2_costs import (
    VerifiedCostAuthorityV2,
    verified_cost_authority_bytes_v2,
    verify_validation_cost_authority_metadata_v2,
)
from market_structure_lab.research.validation_v2_models import (
    AccessAuditLedgerIdentityV2,
    AggregatePublicationIdentityV2,
    CostAuthorityIdentityV2,
    DevelopmentSplitIdentityV2,
    PrecisionAuthorityIdentityV2,
    SourceCoverageIdentityV2,
    SourceCoveragePublicationV2,
    SourcePublicationIdentityV2,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
    verified_source_coverage_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentAccessAttemptLedgerV2,
    DevelopmentEventAssignmentV2,
    DevelopmentFoldSetV2,
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    assign_development_event_v2,
    freeze_development_folds_v2,
    verify_development_read_boundary_v2,
)
from market_structure_lab.research.statistics import bootstrap_weekly_mean

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VPV2-[a-f0-9]{64}$")
_RUNNER_VERSION = "phase5-validation-slot-runner-v2"
_PRIMARY_COUNT = 64
_GLOBAL_ALPHA = 0.05
_PRECISION_REQUIRED_FAMILIES = frozenset({"B"})
_OUTCOME_FACTORY = object()
_OUTCOME_READER_FACTORY = object()
_PUBLICATION_OUTCOME_READER_ISSUANCE: dict[int, tuple[object, tuple[object, ...]]] = {}
_VERIFIED_OUTCOME_READERS: dict[
    int, tuple[weakref.ReferenceType[VerifiedDevelopmentOutcomeReaderV2], bytes]
] = {}


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_programme(value: object) -> str:
    if not isinstance(value, str) or _PROGRAMME_ID.fullmatch(value) is None:
        raise ValueError("programme_id must be a VPV2 identity")
    return value


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value.astimezone(UTC)


def _monday(timestamp: datetime) -> datetime:
    value = _require_utc(timestamp, "timestamp")
    day = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True, slots=True)
class ValidationV2SourceBundle:
    """Exact original publisher capabilities consumed by the V2 programme."""

    coverage: SourceCoveragePublicationV2
    split: DevelopmentSplitPublicationV2
    boundary: DevelopmentReadBoundaryV2
    availability: ScopedSourceAvailabilityV2
    source_publication: ValidationSourcePublicationV2
    aggregate_publication: AggregatePublicationV2
    precision_authority: ValidationPrecisionAuthorityV2
    cost_authority: VerifiedCostAuthorityV2

    def revalidate(self) -> None:
        """Reopen all registered original bytes and their parent chains."""

        _revalidate_source_bundle_v2(self)


def _revalidate_source_bundle_v2(sources: ValidationV2SourceBundle) -> None:
    """Trusted exact-type source revalidation used by the public runner."""

    if type(sources) is not ValidationV2SourceBundle:
        raise TypeError("sources must be the exact ValidationV2SourceBundle type")
    verified_source_coverage_bytes(sources.coverage)
    verify_development_read_boundary_v2(sources.boundary, sources.coverage, sources.split)
    verified_scoped_source_availability_bytes_v2(
        sources.availability,
        boundary=sources.boundary,
    )
    verify_validation_source_publication_v2(
        sources.source_publication,
        coverage=sources.coverage,
        split=sources.split,
        boundary=sources.boundary,
        availability=sources.availability,
    )
    verify_validation_aggregate_publication_v2(
        sources.aggregate_publication,
        minute_publication=sources.source_publication,
        coverage=sources.coverage,
        split=sources.split,
        boundary=sources.boundary,
        availability=sources.availability,
    )
    verify_validation_precision_authority_v2(
        sources.precision_authority,
        coverage=sources.coverage,
        split=sources.split,
        boundary=sources.boundary,
        availability=sources.availability,
        minute_publication=sources.source_publication,
        aggregate_publication=sources.aggregate_publication,
    )
    verified_cost_authority_bytes_v2(sources.cost_authority)


def development_access_ledger_identity_v2(
    sources: ValidationV2SourceBundle,
) -> AccessAuditLedgerIdentityV2:
    """Derive the one frozen development-only FILE-read ledger policy identity."""

    if type(sources) is not ValidationV2SourceBundle:
        raise TypeError("sources must be the exact ValidationV2SourceBundle type")
    return AccessAuditLedgerIdentityV2.from_payload(
        {
            "schema_version": "phase5-validation-development-ledger-policy-v2",
            "boundary_sha256": sources.boundary.boundary_sha256,
            "source_publication_identity": (
                sources.source_publication.source_publication_identity.value
            ),
            "source_publication_sha256": hashlib.sha256(
                sources.source_publication.canonical_bytes
            ).hexdigest(),
            "allowed_operation_kind": AccessOperationKindV2.FILE.value,
            "allowed_timeframe": "1m",
            "target_origin_sha256": sources.source_publication.origin_sha256,
            "allowed_symbols": list(sources.boundary.allowed_symbols),
            "allowed_intervals": [
                {"start": interval.start, "end": interval.end}
                for interval in sources.boundary.allowed_intervals
            ],
            "final_scope_attempts": 0,
            "final_rows": 0,
            "final_access_records": 0,
        }
    )


@dataclass(frozen=True, slots=True)
class AttachedDevelopmentOutcomeV2:
    """One internally attached development outcome at a verified event clock."""

    event_id: str
    slot_id: str
    fold_id: str
    symbol: str
    timeframe: str
    timestamp: datetime
    net_return: float
    source_partition_sha256: str
    aggregate_row_sha256: str
    split_sha256: str
    cost_authority_sha256: str
    source_publication_sha256: str
    aggregate_publication_sha256: str
    detector_metric: float
    volume: float
    partition_role: str = "inner_train"
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _OUTCOME_FACTORY:
            raise TypeError("AttachedDevelopmentOutcomeV2 requires its verifier factory")
        for value, label in (
            (self.event_id, "event_id"),
            (self.slot_id, "slot_id"),
            (self.fold_id, "fold_id"),
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} must be non-empty")
        _require_utc(self.timestamp, "outcome timestamp")
        if isinstance(self.net_return, bool) or not isinstance(self.net_return, (int, float)):
            raise ValueError("net_return must be numeric")
        if not isfinite(float(self.net_return)):
            raise ValueError("net_return must be finite")
        for value, label in (
            (self.source_partition_sha256, "source_partition_sha256"),
            (self.aggregate_row_sha256, "aggregate_row_sha256"),
            (self.split_sha256, "split_sha256"),
            (self.cost_authority_sha256, "cost_authority_sha256"),
            (self.source_publication_sha256, "source_publication_sha256"),
            (self.aggregate_publication_sha256, "aggregate_publication_sha256"),
        ):
            _require_sha256(value, label)
        for numeric_value, numeric_label in (
            (self.detector_metric, "detector_metric"),
            (self.volume, "volume"),
        ):
            if isinstance(numeric_value, bool) or not isinstance(numeric_value, (int, float)):
                raise ValueError(f"{numeric_label} must be numeric")
            if not isfinite(float(numeric_value)):
                raise ValueError(f"{numeric_label} must be finite")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.partition_role not in {"inner_train", "inner_validation", "outer_diagnostic"}:
            raise ValueError("partition_role is invalid")

    @property
    def outcome_sha256(self) -> str:
        return hash_json(
            "phase5-validation-attached-development-outcome-v2",
            {
                "event_id": self.event_id,
                "slot_id": self.slot_id,
                "fold_id": self.fold_id,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "timestamp": self.timestamp.isoformat(),
                "net_return": float(self.net_return),
                "source_partition_sha256": self.source_partition_sha256,
                "aggregate_row_sha256": self.aggregate_row_sha256,
                "split_sha256": self.split_sha256,
                "cost_authority_sha256": self.cost_authority_sha256,
                "source_publication_sha256": self.source_publication_sha256,
                "aggregate_publication_sha256": self.aggregate_publication_sha256,
                "detector_metric": float(self.detector_metric),
                "volume": float(self.volume),
                "partition_role": self.partition_role,
            },
        )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedDevelopmentOutcomeReaderV2:
    """Verifier-issued, byte-rooted reader for attached development outcomes."""

    programme_id: str
    split_sha256: str
    cost_authority_sha256: str
    source_publication_sha256: str
    aggregate_publication_sha256: str
    slot_ids: tuple[str, ...]
    fixture_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _outcomes_by_slot: Mapping[str, tuple[AttachedDevelopmentOutcomeV2, ...]] = field(
        repr=False, compare=False
    )
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _OUTCOME_READER_FACTORY:
            raise TypeError("VerifiedDevelopmentOutcomeReaderV2 requires its verifier factory")
        if hashlib.sha256(self.canonical_bytes).hexdigest() != self.fixture_sha256:
            raise ValueError("outcome reader bytes differ from identity")
        if tuple(self._outcomes_by_slot) != self.slot_ids:
            raise ValueError("outcome reader slot order differs")

    def read_slot(
        self,
        slot: ValidationSlot,
        *,
        programme_id: str,
        split_sha256: str,
        cost_authority_sha256: str,
    ) -> tuple[AttachedDevelopmentOutcomeV2, ...]:
        registration = _VERIFIED_OUTCOME_READERS.get(id(self))
        if (
            registration is None
            or registration[0]() is not self
            or registration[1] != self.canonical_bytes
        ):
            raise ValueError("outcome reader is not the registered original")
        if (
            self.programme_id != programme_id
            or self.split_sha256 != split_sha256
            or self.cost_authority_sha256 != cost_authority_sha256
        ):
            raise ValueError("outcome reader authority binding differs")
        if slot.slot_id not in self._outcomes_by_slot:
            raise ValueError("outcome reader is missing the requested slot")
        return self._outcomes_by_slot[slot.slot_id]


def _issue_fixture_outcome_reader_v2(
    fixture_bytes: bytes,
    *,
    programme_id: str,
    split_sha256: str,
    cost_authority_sha256: str,
    source_publication_sha256: str,
    aggregate_publication_sha256: str,
    expected_slot_ids: Sequence[str],
) -> VerifiedDevelopmentOutcomeReaderV2:
    """Issue a synthetic reader from strict original fixture bytes only."""

    _require_programme(programme_id)
    for value, label in (
        (split_sha256, "split_sha256"),
        (cost_authority_sha256, "cost_authority_sha256"),
        (source_publication_sha256, "source_publication_sha256"),
        (aggregate_publication_sha256, "aggregate_publication_sha256"),
    ):
        _require_sha256(value, label)
    if not isinstance(fixture_bytes, bytes):
        raise TypeError("fixture_bytes must be bytes")
    try:
        payload = json.loads(fixture_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("outcome fixture bytes are invalid JSON") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "slots"}
        or payload["schema_version"] != "validation-v2-outcome-fixture-v1"
        or not isinstance(payload["slots"], dict)
    ):
        raise ValueError("outcome fixture schema is invalid")
    expected = tuple(expected_slot_ids)
    slots = payload["slots"]
    if tuple(slots) != expected:
        raise ValueError("outcome fixture has missing, extra, or reordered slot keys")
    roster_by_id = {slot.slot_id: slot for slot in VALIDATION_SLOT_ROSTER}
    outcomes: dict[str, tuple[AttachedDevelopmentOutcomeV2, ...]] = {}
    row_fields = {
        "event_id",
        "timestamp",
        "symbol",
        "timeframe",
        "entry_price",
        "exit_price",
        "volume",
        "detector_metric",
        "partition_role",
    }
    for slot_id in expected:
        slot = roster_by_id.get(slot_id)
        if slot is None:
            raise ValueError("outcome fixture slot is outside the frozen roster")
        raw_rows = slots[slot_id]
        if not isinstance(raw_rows, list):
            raise TypeError("outcome fixture slot rows must be a list")
        attached: list[AttachedDevelopmentOutcomeV2] = []
        for raw in raw_rows:
            if not isinstance(raw, dict) or set(raw) != row_fields:
                raise ValueError("outcome fixture row has caller-supplied or missing fields")
            if str(raw["partition_role"]).startswith("final"):
                raise PermissionError("final outcome fixture sentinel is forbidden")
            timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            entry = float(raw["entry_price"])
            exit_price = float(raw["exit_price"])
            if entry <= 0 or exit_price < 0:
                raise ValueError("fixture prices are invalid")
            ratio = exit_price / entry
            net_return = ratio - 1.0 if slot.direction == "long" else 1.0 - ratio
            attached.append(
                AttachedDevelopmentOutcomeV2(
                    event_id=str(raw["event_id"]),
                    slot_id=slot_id,
                    fold_id="development-fold-1",
                    symbol=str(raw["symbol"]),
                    timeframe=str(raw["timeframe"]),
                    timestamp=timestamp,
                    net_return=net_return,
                    source_partition_sha256=hash_json(
                        "phase5-validation-fixture-source-partition-v2",
                        {
                            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                            "slot_id": slot_id,
                        },
                    ),
                    aggregate_row_sha256=hash_json(
                        "phase5-validation-fixture-aggregate-row-v2",
                        {"slot_id": slot_id, "row": raw},
                    ),
                    split_sha256=split_sha256,
                    cost_authority_sha256=cost_authority_sha256,
                    source_publication_sha256=source_publication_sha256,
                    aggregate_publication_sha256=aggregate_publication_sha256,
                    detector_metric=float(raw["detector_metric"]),
                    volume=float(raw["volume"]),
                    partition_role=str(raw["partition_role"]),
                    _factory_token=_OUTCOME_FACTORY,
                )
            )
        outcomes[slot_id] = tuple(attached)
    reader = VerifiedDevelopmentOutcomeReaderV2(
        programme_id=programme_id,
        split_sha256=split_sha256,
        cost_authority_sha256=cost_authority_sha256,
        source_publication_sha256=source_publication_sha256,
        aggregate_publication_sha256=aggregate_publication_sha256,
        slot_ids=expected,
        fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        canonical_bytes=fixture_bytes,
        _outcomes_by_slot=MappingProxyType(outcomes),
        _factory_token=_OUTCOME_READER_FACTORY,
    )
    identifier = id(reader)

    def cleanup(reference: weakref.ReferenceType[VerifiedDevelopmentOutcomeReaderV2]) -> None:
        current = _VERIFIED_OUTCOME_READERS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_OUTCOME_READERS.pop(identifier, None)

    reference = weakref.ref(reader, cleanup)
    _VERIFIED_OUTCOME_READERS[identifier] = (reference, fixture_bytes)
    return reader


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedPublicationOutcomeReaderV2:
    """Exact real-development outcome capability rooted in verified publications."""

    programme_id: str
    split_sha256: str
    cost_authority_sha256: str
    source_publication_sha256: str
    aggregate_publication_sha256: str
    slot_ids: tuple[str, ...]
    outcome_set_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _outcomes_by_slot: Mapping[str, tuple[DevelopmentOutcomeRowV2, ...]] = field(
        repr=False,
        compare=False,
    )
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        issuance = _PUBLICATION_OUTCOME_READER_ISSUANCE.pop(id(_factory_token), None)
        if (
            issuance is None
            or issuance[0] is not _factory_token
            or issuance[1] != _publication_outcome_reader_snapshot(self)
        ):
            raise TypeError("VerifiedPublicationOutcomeReaderV2 requires its verifier factory")
        _require_programme(self.programme_id)
        for value, label in (
            (self.split_sha256, "split_sha256"),
            (self.cost_authority_sha256, "cost_authority_sha256"),
            (self.source_publication_sha256, "source_publication_sha256"),
            (self.aggregate_publication_sha256, "aggregate_publication_sha256"),
            (self.outcome_set_sha256, "outcome_set_sha256"),
        ):
            _require_sha256(value, label)
        if self.slot_ids != ("VS-0001",) or tuple(self._outcomes_by_slot) != self.slot_ids:
            raise ValueError("publication outcome reader must contain exactly the VS-0001 slice")
        if any(len(rows) != 1 for rows in self._outcomes_by_slot.values()):
            raise ValueError("publication outcome reader requires one exact outcome per slot")
        if hashlib.sha256(self.canonical_bytes).hexdigest() != self.outcome_set_sha256:
            raise ValueError("publication outcome reader bytes differ from identity")

    def verify_original(self) -> VerifiedPublicationOutcomeReaderV2:
        """Revalidate this exact reader and all retained computation parents."""

        return _verify_publication_outcome_reader_v2(self)

    def read_slot(
        self,
        slot: ValidationSlot,
        *,
        programme_id: str,
        split_sha256: str,
        cost_authority_sha256: str,
    ) -> tuple[DevelopmentOutcomeRowV2, ...]:
        self.verify_original()
        expected = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == "VS-0001")
        if type(slot) is not ValidationSlot or slot != expected:
            raise ValueError("publication outcome reader supports only canonical VS-0001")
        if (
            programme_id != self.programme_id
            or split_sha256 != self.split_sha256
            or cost_authority_sha256 != self.cost_authority_sha256
        ):
            raise ValueError("publication outcome reader authority binding differs")
        return self._outcomes_by_slot[slot.slot_id]


@dataclass(frozen=True, slots=True)
class _PublicationOutcomeReaderRegistrationV2:
    reader: weakref.ReferenceType[VerifiedPublicationOutcomeReaderV2]
    snapshot: tuple[object, ...]
    config: ValidationProgrammeConfigV2
    config_payload: Mapping[str, object]
    sources: ValidationV2SourceBundle
    work_budget: ValidationWorkBudget
    work_budget_payload: Mapping[str, int]
    admitted_demand: ValidationWorkDemand
    admitted_demand_payload: Mapping[str, int]
    actual_demand: ValidationWorkDemand
    actual_demand_payload: Mapping[str, int]
    aggregate_budget: AggregatePublicationBudgetV2
    aggregate_budget_payload: Mapping[str, int]
    minute_budget: MinutePathReadBudgetV2
    minute_budget_payload: Mapping[str, int]
    aggregate_series: VerifiedAggregateSeriesV2
    candidate_series: VerifiedCandidateSeriesV2
    signal: CandidateSignal
    folds: DevelopmentFoldSetV2
    assignment: DevelopmentEventAssignmentV2
    minute_path: VerifiedMinutePathV2
    outcome: DevelopmentOutcomeRowV2


_VERIFIED_PUBLICATION_OUTCOME_READERS: dict[int, _PublicationOutcomeReaderRegistrationV2] = {}


def _publication_outcome_reader_snapshot(
    reader: VerifiedPublicationOutcomeReaderV2,
) -> tuple[object, ...]:
    return (
        reader.programme_id,
        reader.split_sha256,
        reader.cost_authority_sha256,
        reader.source_publication_sha256,
        reader.aggregate_publication_sha256,
        reader.slot_ids,
        reader.outcome_set_sha256,
        reader.canonical_bytes,
        tuple(
            (slot_id, tuple(item.outcome_id for item in outcomes))
            for slot_id, outcomes in reader._outcomes_by_slot.items()
        ),
    )


def _expected_publication_outcome_reader_snapshot_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
    work_budget: ValidationWorkBudget,
    admitted_demand: ValidationWorkDemand,
    actual_demand: ValidationWorkDemand,
    aggregate_budget: AggregatePublicationBudgetV2,
    minute_budget: MinutePathReadBudgetV2,
    aggregate_series: VerifiedAggregateSeriesV2,
    signal: CandidateSignal,
    assignment: DevelopmentEventAssignmentV2,
    minute_path: VerifiedMinutePathV2,
    outcome: DevelopmentOutcomeRowV2,
) -> tuple[object, ...]:
    slot_id = "VS-0001"
    payload = {
        "schema_version": "validation-v2-publication-outcome-reader-v1",
        "programme_id": config.programme_id,
        "slot_ids": [slot_id],
        "outcome_ids": [outcome.outcome_id],
        "source_publication_sha256": hashlib.sha256(
            sources.source_publication.canonical_bytes
        ).hexdigest(),
        "aggregate_publication_sha256": aggregate_series.aggregate_publication_sha256,
        "work_budget_sha256": work_budget.sha256,
        "admitted_demand_sha256": hash_json(
            "phase5-validation-admitted-work-demand-v2",
            {name: getattr(admitted_demand, name) for name in admitted_demand.__dataclass_fields__},
        ),
        "actual_demand_sha256": hash_json(
            "phase5-validation-actual-work-demand-v2",
            {name: getattr(actual_demand, name) for name in actual_demand.__dataclass_fields__},
        ),
        "aggregate_budget": aggregate_budget.to_dict(),
        "minute_budget": {
            name: getattr(minute_budget, name) for name in minute_budget.__dataclass_fields__
        },
        "signal_id": signal.signal_id,
        "assignment_id": assignment.assignment_id,
        "minute_path_identity": minute_path.path_identity,
    }
    canonical_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    source_sha256 = str(payload["source_publication_sha256"])
    return (
        config.programme_id,
        sources.boundary.boundary_sha256,
        sources.cost_authority.cost_identity.value.removeprefix("CSTV2-"),
        source_sha256,
        aggregate_series.aggregate_publication_sha256,
        (slot_id,),
        hashlib.sha256(canonical_bytes).hexdigest(),
        canonical_bytes,
        ((slot_id, (outcome.outcome_id,)),),
    )


def _verify_publication_outcome_reader_v2(
    reader: VerifiedPublicationOutcomeReaderV2,
) -> VerifiedPublicationOutcomeReaderV2:
    if type(reader) is not VerifiedPublicationOutcomeReaderV2:
        raise TypeError("publication outcome reader must be the exact verifier-issued type")
    registered = _VERIFIED_PUBLICATION_OUTCOME_READERS.get(id(reader))
    if registered is None or registered.reader() is not reader:
        raise ValueError("publication outcome reader is not the registered original")
    if _publication_outcome_reader_snapshot(reader) != registered.snapshot:
        raise ValueError("publication outcome reader differs from its immutable snapshot")
    if registered.config.to_publication_dict() != dict(registered.config_payload):
        raise ValueError("publication outcome reader configuration parent changed")
    if registered.work_budget.to_dict() != dict(registered.work_budget_payload):
        raise ValueError("publication outcome reader work budget parent changed")
    if {
        name: getattr(registered.admitted_demand, name)
        for name in registered.admitted_demand.__dataclass_fields__
    } != dict(registered.admitted_demand_payload):
        raise ValueError("publication outcome reader admitted demand parent changed")
    if {
        name: getattr(registered.actual_demand, name)
        for name in registered.actual_demand.__dataclass_fields__
    } != dict(registered.actual_demand_payload):
        raise ValueError("publication outcome reader actual demand parent changed")
    ValidationWorkBudget.preflight(registered.work_budget, registered.actual_demand)
    if registered.aggregate_budget.to_dict() != dict(registered.aggregate_budget_payload):
        raise ValueError("publication outcome reader aggregate budget parent changed")
    if {
        name: getattr(registered.minute_budget, name)
        for name in registered.minute_budget.__dataclass_fields__
    } != dict(registered.minute_budget_payload):
        raise ValueError("publication outcome reader minute budget parent changed")
    _revalidate_source_bundle_v2(registered.sources)
    verify_original_aggregate_series_v2(registered.aggregate_series)
    verify_candidate_series_v2(registered.candidate_series)
    verify_candidate_signal(registered.signal)
    registered.folds.verify_original()
    registered.assignment.verify_original()
    if type(registered.minute_path) is not VerifiedMinutePathV2:
        raise TypeError("publication outcome reader minute path parent is not exact")
    verify_original_minute_path_v2(registered.minute_path)
    if type(registered.outcome) is not DevelopmentOutcomeRowV2:
        raise TypeError("publication outcome reader outcome parent is not exact")
    registered.outcome.verify_original()
    if registered.outcome is not reader._outcomes_by_slot["VS-0001"][0]:
        raise ValueError("publication outcome reader outcome parent changed")
    expected_snapshot = _expected_publication_outcome_reader_snapshot_v2(
        config=registered.config,
        sources=registered.sources,
        work_budget=registered.work_budget,
        admitted_demand=registered.admitted_demand,
        actual_demand=registered.actual_demand,
        aggregate_budget=registered.aggregate_budget,
        minute_budget=registered.minute_budget,
        aggregate_series=registered.aggregate_series,
        signal=registered.signal,
        assignment=registered.assignment,
        minute_path=registered.minute_path,
        outcome=registered.outcome,
    )
    if _publication_outcome_reader_snapshot(reader) != expected_snapshot:
        raise ValueError(
            "publication outcome reader claims differ from retained computation parents"
        )
    return reader


def _aggregate_read_budget_v2(
    budget: ValidationWorkBudget,
) -> AggregatePublicationBudgetV2:
    return AggregatePublicationBudgetV2(
        max_source_rows=budget.max_source_rows,
        max_source_bytes=budget.max_source_bytes,
        max_parent_partitions=100_000,
        max_source_rows_per_chunk=100_000,
        max_members=min(100_000, budget.max_symbols * budget.max_ranges * 2),
        max_aggregate_rows=budget.max_aggregate_bars,
        max_rows_per_partition=100_000,
        max_output_bytes=budget.max_source_bytes,
        max_output_files=100_000,
    )


def _minute_read_budget_v2(
    *,
    budget: ValidationWorkBudget,
    expected_row_count: int,
) -> MinutePathReadBudgetV2:
    return MinutePathReadBudgetV2(
        max_total_rows=budget.max_source_rows,
        max_total_bytes=budget.max_source_bytes,
        max_partitions=99_998,
        max_returned_rows=expected_row_count,
    )


def _require_actual_demand_admitted_v2(
    *,
    admitted: ValidationWorkDemand,
    actual: ValidationWorkDemand,
) -> None:
    from market_structure_lab.research.models import ValidationWorkBudgetViolation

    for field_name in (
        "source_rows",
        "source_bytes",
        "aggregate_bars",
        "symbols",
        "ranges",
        "candidates",
        "events",
        "outcomes",
        "path_cells",
        "outer_folds",
        "inner_folds",
    ):
        observed = getattr(actual, field_name)
        declared = getattr(admitted, field_name)
        if observed > declared:
            raise ValidationWorkBudgetViolation(field_name, observed, declared)


def _find_vs0001_event_v2(
    *,
    sources: ValidationV2SourceBundle,
    aggregate_budget: AggregatePublicationBudgetV2,
    event_emission_ceiling: int,
) -> tuple[
    ValidationSlot,
    VerifiedAggregateSeriesV2,
    VerifiedCandidateSeriesV2,
    CandidateSignal,
    DevelopmentFoldSetV2,
    DevelopmentEventAssignmentV2,
    int,
]:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == "VS-0001")
    folds = freeze_development_folds_v2(split=sources.split, timeframe=slot.timeframe)
    events: list[
        tuple[
            VerifiedAggregateSeriesV2,
            VerifiedCandidateSeriesV2,
            CandidateSignal,
            DevelopmentEventAssignmentV2,
        ]
    ] = []
    detected_signal_count = 0
    members = tuple(
        member
        for member in sources.aggregate_publication.members
        if (
            member.symbol in sources.split.development_symbols
            and member.target_timeframe == slot.timeframe
        )
    )
    for member in members:
        for segment_id in sorted({item.segment_id for item in member.partitions}):
            key = issue_aggregate_series_key_v2(
                sources.aggregate_publication,
                symbol=member.symbol,
                interval_index=member.interval_index,
                target_timeframe=member.target_timeframe,
                segment_id=segment_id,
            )
            aggregate_series = open_verified_aggregate_series_v2(
                sources.aggregate_publication,
                key,
                aggregate_budget,
            )
            candidate_series = bridge_verified_aggregate_series_v2(aggregate_series)
            definition = candidate_definition_for_verified_series(slot, candidate_series)
            for signal in detect_candidate_signals(
                definition,
                candidate_series,
                max_emitted_signals=event_emission_ceiling - detected_signal_count,
            ):
                detected_signal_count += 1
                label_end = signal.legal_entry + timedelta(hours=slot.horizon_hours)
                if not any(
                    fold.test.start <= signal.legal_entry < label_end <= fold.test.end
                    for fold in folds.outer_folds
                ):
                    continue
                assignment = assign_development_event_v2(folds=folds, signal=signal)
                events.append((aggregate_series, candidate_series, signal, assignment))
    if len(events) != 1:
        raise ValueError(
            "real VS-0001 slice requires exactly one development-assigned event; "
            f"observed {len(events)}"
        )
    aggregate_series, candidate_series, signal, assignment = events[0]
    return (
        slot,
        aggregate_series,
        candidate_series,
        signal,
        folds,
        assignment,
        detected_signal_count,
    )


def _build_real_vs0001_reader_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
    budget: ValidationWorkBudget,
    admitted_demand: ValidationWorkDemand,
    authenticated_demand: ValidationWorkDemand,
) -> VerifiedPublicationOutcomeReaderV2:
    aggregate_budget = _aggregate_read_budget_v2(budget)
    (
        slot,
        aggregate_series,
        candidate_series,
        signal,
        folds,
        assignment,
        detected_signal_count,
    ) = _find_vs0001_event_v2(
        sources=sources,
        aggregate_budget=aggregate_budget,
        event_emission_ceiling=min(admitted_demand.events, budget.max_events),
    )
    expected_rows = slot.horizon_hours * 60
    actual_demand = replace(
        authenticated_demand,
        candidates=1,
        events=detected_signal_count,
        outcomes=1,
        path_cells=expected_rows,
        outer_folds=len(folds.outer_folds),
        inner_folds=len(folds.outer_folds[0].inner_folds),
    )
    _require_actual_demand_admitted_v2(admitted=admitted_demand, actual=actual_demand)
    ValidationWorkBudget.preflight(budget, actual_demand)

    minute_budget = _minute_read_budget_v2(
        budget=budget,
        expected_row_count=expected_rows,
    )
    request = BoundaryRequestV2(
        symbol=signal.symbol,
        timeframe="1m",
        start=signal.legal_entry,
        end=signal.legal_entry + timedelta(hours=slot.horizon_hours),
        operation_kind=AccessOperationKindV2.FILE,
        target_identity=sources.source_publication.origin_sha256 or "",
    )
    audit = DevelopmentAccessAttemptLedgerV2(
        programme_id=config.programme_id,
        attempt_id="VA-"
        + hash_json(
            "phase5-validation-real-vs0001-minute-attempt-v2",
            {
                "programme_id": config.programme_id,
                "access_ledger_identity": config.access_ledger_identity.value,
                "signal_id": signal.signal_id,
                "request": {
                    "symbol": request.symbol,
                    "timeframe": request.timeframe,
                    "start": request.start,
                    "end": request.end,
                    "target_identity": request.target_identity,
                },
            },
        ),
        boundary=sources.boundary,
    )
    minute_path = read_verified_minute_path_v2(
        sources.source_publication,
        sources.coverage,
        sources.split,
        sources.boundary,
        sources.availability,
        request,
        expected_rows,
        minute_budget,
        audit,
    )
    outcome = attach_development_outcome_v2(
        signal,
        aggregate_series=aggregate_series,
        minute_path=minute_path,
        assignment=assignment,
        cost_authority=sources.cost_authority,
        horizon_hours=slot.horizon_hours,
    )
    expected_snapshot = _expected_publication_outcome_reader_snapshot_v2(
        config=config,
        sources=sources,
        work_budget=budget,
        admitted_demand=admitted_demand,
        actual_demand=actual_demand,
        aggregate_budget=aggregate_budget,
        minute_budget=minute_budget,
        aggregate_series=aggregate_series,
        signal=signal,
        assignment=assignment,
        minute_path=minute_path,
        outcome=outcome,
    )
    issuance_token = object()
    _PUBLICATION_OUTCOME_READER_ISSUANCE[id(issuance_token)] = (
        issuance_token,
        expected_snapshot,
    )
    try:
        reader = VerifiedPublicationOutcomeReaderV2(
            programme_id=expected_snapshot[0],  # type: ignore[arg-type]
            split_sha256=expected_snapshot[1],  # type: ignore[arg-type]
            cost_authority_sha256=expected_snapshot[2],  # type: ignore[arg-type]
            source_publication_sha256=expected_snapshot[3],  # type: ignore[arg-type]
            aggregate_publication_sha256=expected_snapshot[4],  # type: ignore[arg-type]
            slot_ids=expected_snapshot[5],  # type: ignore[arg-type]
            outcome_set_sha256=expected_snapshot[6],  # type: ignore[arg-type]
            canonical_bytes=expected_snapshot[7],  # type: ignore[arg-type]
            _outcomes_by_slot=MappingProxyType({slot.slot_id: (outcome,)}),
            _factory_token=issuance_token,
        )
    finally:
        _PUBLICATION_OUTCOME_READER_ISSUANCE.pop(id(issuance_token), None)
    if _publication_outcome_reader_snapshot(reader) != expected_snapshot:
        raise ValueError("publication outcome reader claims differ from computation parents")
    identifier = id(reader)

    def cleanup(reference: weakref.ReferenceType[VerifiedPublicationOutcomeReaderV2]) -> None:
        current = _VERIFIED_PUBLICATION_OUTCOME_READERS.get(identifier)
        if current is not None and current.reader is reference:
            _VERIFIED_PUBLICATION_OUTCOME_READERS.pop(identifier, None)

    reference = weakref.ref(reader, cleanup)
    _VERIFIED_PUBLICATION_OUTCOME_READERS[identifier] = _PublicationOutcomeReaderRegistrationV2(
        reader=reference,
        snapshot=expected_snapshot,
        config=config,
        config_payload=MappingProxyType(config.to_publication_dict()),
        sources=sources,
        work_budget=budget,
        work_budget_payload=MappingProxyType(budget.to_dict()),
        admitted_demand=admitted_demand,
        admitted_demand_payload=MappingProxyType(
            {name: getattr(admitted_demand, name) for name in admitted_demand.__dataclass_fields__}
        ),
        actual_demand=actual_demand,
        actual_demand_payload=MappingProxyType(
            {name: getattr(actual_demand, name) for name in actual_demand.__dataclass_fields__}
        ),
        aggregate_budget=aggregate_budget,
        aggregate_budget_payload=MappingProxyType(aggregate_budget.to_dict()),
        minute_budget=minute_budget,
        minute_budget_payload=MappingProxyType(
            {name: getattr(minute_budget, name) for name in minute_budget.__dataclass_fields__}
        ),
        aggregate_series=aggregate_series,
        candidate_series=candidate_series,
        signal=signal,
        folds=folds,
        assignment=assignment,
        minute_path=minute_path,
        outcome=outcome,
    )
    return reader


def _issue_publication_outcome_reader_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
    budget: ValidationWorkBudget,
    admitted_demand: ValidationWorkDemand,
    authenticated_demand: ValidationWorkDemand,
) -> VerifiedPublicationOutcomeReaderV2:
    """Execute and seal the one authorised real VS-0001 development slice."""

    return _build_real_vs0001_reader_v2(
        config=config,
        sources=sources,
        budget=budget,
        admitted_demand=admitted_demand,
        authenticated_demand=authenticated_demand,
    )


@dataclass(frozen=True, slots=True)
class SelectorEvidenceV2:
    selected_event_ids: tuple[str, ...]
    inner_fold_event_ids: tuple[str, ...]
    outer_diagnostic_event_ids: tuple[str, ...]
    selector_metric: float | None
    selector_sha256: str


@dataclass(frozen=True, slots=True)
class WeeklySlotVectorV2:
    week_start: datetime
    week_end: datetime
    fold_id: str
    symbols: tuple[str, ...]
    value: float
    event_ids: tuple[str, ...]
    vector_sha256: str


@dataclass(frozen=True, slots=True)
class ExposureRowV2:
    event_id: str
    slot_id: str
    fold_id: str
    partition_role: str
    symbol: str
    timeframe: str
    timestamp: datetime
    realized_return: float
    detector_metric: float
    volume: float
    source_partition_sha256: str
    split_sha256: str
    cost_authority_sha256: str
    seed: int
    runner_version: str
    exposure_row_sha256: str


@dataclass(frozen=True, slots=True)
class RobustnessRerunEvidenceV2:
    variant: str
    event_ids: tuple[str, ...]
    weekly_vector_sha256s: tuple[str, ...]
    lower_bound: float | None
    rerun_sha256: str


@dataclass(frozen=True, slots=True)
class DerivedSlotEvidenceV2:
    programme_id: str
    slot_id: str
    family: str
    fold_ids: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe: str
    event_ids: tuple[str, ...]
    outcome_sha256s: tuple[str, ...]
    source_partition_sha256s: tuple[str, ...]
    split_sha256: str
    cost_authority_sha256: str
    seed: int
    runner_version: str
    selector_evidence: SelectorEvidenceV2
    weekly_vectors: tuple[WeeklySlotVectorV2, ...]
    exposure_rows: tuple[ExposureRowV2, ...]
    robustness_rerun: RobustnessRerunEvidenceV2 | None
    artifact_sha256: str

    def verify_for(
        self,
        *,
        programme_id: str,
        slot: ValidationSlot,
        split_sha256: str,
        cost_authority_sha256: str,
        runner_version: str,
    ) -> None:
        if self.programme_id != programme_id:
            raise ValueError("derived artifact programme binding differs")
        if self.slot_id != slot.slot_id or self.family != slot.family:
            raise ValueError("derived artifact slot binding differs")
        if self.timeframe != slot.timeframe:
            raise ValueError("derived artifact timeframe binding differs")
        if self.split_sha256 != split_sha256:
            raise ValueError("derived artifact split binding differs")
        if self.cost_authority_sha256 != cost_authority_sha256:
            raise ValueError("derived artifact cost authority binding differs")
        if self.runner_version != runner_version:
            raise ValueError("derived artifact runner binding differs")
        expected = _derived_artifact_sha256(self)
        if self.artifact_sha256 != expected:
            raise ValueError("derived artifact identity differs")


def derive_slot_evidence_v2(
    *,
    programme_id: str,
    slot: ValidationSlot,
    outcome_reader: VerifiedDevelopmentOutcomeReaderV2,
    seed: int,
    runner_version: str = _RUNNER_VERSION,
) -> DerivedSlotEvidenceV2:
    """Derive selector and Monday-UTC vectors from slot-specific outcomes."""

    _require_programme(programme_id)
    if slot not in VALIDATION_SLOT_ROSTER:
        raise ValueError("slot is outside the frozen roster")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if not isinstance(outcome_reader, VerifiedDevelopmentOutcomeReaderV2):
        raise TypeError("outcome_reader must be verifier-issued")
    ordered = tuple(
        sorted(
            outcome_reader.read_slot(
                slot,
                programme_id=programme_id,
                split_sha256=outcome_reader.split_sha256,
                cost_authority_sha256=outcome_reader.cost_authority_sha256,
            ),
            key=lambda row: (row.timestamp, row.symbol, row.event_id),
        )
    )
    for row in ordered:
        if row.slot_id != slot.slot_id:
            raise ValueError("outcome slot differs from requested slot")
        if row.timeframe != slot.timeframe:
            raise ValueError("outcome timeframe differs from requested slot")
    split_values = {row.split_sha256 for row in ordered}
    cost_values = {row.cost_authority_sha256 for row in ordered}
    if len(split_values) > 1 or len(cost_values) > 1:
        raise ValueError("outcomes mix split or cost authority publications")
    split_sha = next(iter(split_values), "0" * 64)
    cost_sha = next(iter(cost_values), "0" * 64)
    inner = tuple(
        row for row in ordered if row.partition_role in {"inner_train", "inner_validation"}
    )
    outer = tuple(row for row in ordered if row.partition_role == "outer_diagnostic")
    selector_metric = (
        sum(float(row.detector_metric) for row in inner) / len(inner) if inner else None
    )
    selected = tuple(
        row.event_id
        for row in inner
        if selector_metric is not None
        and (
            float(row.detector_metric) >= selector_metric
            if slot.direction == "long"
            else float(row.detector_metric) <= selector_metric
        )
    )
    selector_payload = {
        "slot_id": slot.slot_id,
        "seed": seed,
        "inner_fold_event_ids": [row.event_id for row in inner],
        "outer_diagnostic_event_ids": [row.event_id for row in outer],
        "selected_event_ids": list(selected),
        "selector_metric": selector_metric,
    }
    selector = SelectorEvidenceV2(
        selected_event_ids=selected,
        inner_fold_event_ids=tuple(row.event_id for row in inner),
        outer_diagnostic_event_ids=tuple(row.event_id for row in outer),
        selector_metric=selector_metric,
        selector_sha256=hash_json("phase5-validation-selector-evidence-v2", selector_payload),
    )
    grouped: dict[tuple[datetime, str], list[AttachedDevelopmentOutcomeV2]] = defaultdict(list)
    for row in ordered:
        grouped[(_monday(row.timestamp), row.fold_id)].append(row)
    vectors: list[WeeklySlotVectorV2] = []
    for (week, fold_id), rows in sorted(grouped.items()):
        event_ids = tuple(sorted(row.event_id for row in rows))
        by_asset: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            by_asset[row.symbol].append(float(row.net_return))
        asset_means = tuple(sum(values) / len(values) for _, values in sorted(by_asset.items()))
        value = sum(asset_means) / len(asset_means)
        symbols = tuple(sorted(by_asset))
        payload = {
            "programme_id": programme_id,
            "slot_id": slot.slot_id,
            "week_start": week.isoformat(),
            "week_end": (week + timedelta(days=7)).isoformat(),
            "fold_id": fold_id,
            "symbols": list(symbols),
            "timeframe": slot.timeframe,
            "value": value,
            "event_ids": list(event_ids),
            "source_partition_sha256s": sorted({row.source_partition_sha256 for row in rows}),
            "split_sha256": split_sha,
            "cost_authority_sha256": cost_sha,
            "seed": seed,
            "runner_version": runner_version,
        }
        vectors.append(
            WeeklySlotVectorV2(
                week_start=week,
                week_end=week + timedelta(days=7),
                fold_id=fold_id,
                symbols=symbols,
                value=value,
                event_ids=event_ids,
                vector_sha256=hash_json("phase5-validation-weekly-slot-vector-v2", payload),
            )
        )
    exposure_rows = tuple(
        ExposureRowV2(
            event_id=row.event_id,
            slot_id=slot.slot_id,
            fold_id=row.fold_id,
            partition_role=row.partition_role,
            symbol=row.symbol,
            timeframe=row.timeframe,
            timestamp=row.timestamp,
            realized_return=float(row.net_return),
            detector_metric=float(row.detector_metric),
            volume=float(row.volume),
            source_partition_sha256=row.source_partition_sha256,
            split_sha256=row.split_sha256,
            cost_authority_sha256=row.cost_authority_sha256,
            seed=seed,
            runner_version=runner_version,
            exposure_row_sha256=hash_json(
                "phase5-validation-exposure-row-v2",
                {
                    "event_id": row.event_id,
                    "slot_id": slot.slot_id,
                    "fold_id": row.fold_id,
                    "partition_role": row.partition_role,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "timestamp": row.timestamp.isoformat(),
                    "realized_return": float(row.net_return),
                    "detector_metric": float(row.detector_metric),
                    "volume": float(row.volume),
                    "source_partition_sha256": row.source_partition_sha256,
                    "split_sha256": row.split_sha256,
                    "cost_authority_sha256": row.cost_authority_sha256,
                    "seed": seed,
                    "runner_version": runner_version,
                },
            ),
        )
        for row in ordered
    )
    robustness_rerun: RobustnessRerunEvidenceV2 | None = None
    if slot.kind in {ValidationSlotKind.ROBUSTNESS, ValidationSlotKind.PERTURBATION}:
        vector_ids = tuple(item.vector_sha256 for item in vectors)
        lower_bound = min((item.value for item in vectors), default=None)
        robustness_rerun = RobustnessRerunEvidenceV2(
            variant=slot.role,
            event_ids=tuple(row.event_id for row in ordered),
            weekly_vector_sha256s=vector_ids,
            lower_bound=lower_bound,
            rerun_sha256=hash_json(
                "phase5-validation-robustness-rerun-v2",
                {
                    "programme_id": programme_id,
                    "slot": slot.to_dict(),
                    "event_ids": [row.event_id for row in ordered],
                    "weekly_vector_sha256s": list(vector_ids),
                    "lower_bound": lower_bound,
                    "split_sha256": split_sha,
                    "cost_authority_sha256": cost_sha,
                    "seed": seed,
                    "runner_version": runner_version,
                },
            ),
        )
    base = DerivedSlotEvidenceV2(
        programme_id=programme_id,
        slot_id=slot.slot_id,
        family=slot.family,
        fold_ids=tuple(sorted({row.fold_id for row in ordered})),
        symbols=tuple(sorted({row.symbol for row in ordered})),
        timeframe=slot.timeframe,
        event_ids=tuple(row.event_id for row in ordered),
        outcome_sha256s=tuple(row.outcome_sha256 for row in ordered),
        source_partition_sha256s=tuple(sorted({row.source_partition_sha256 for row in ordered})),
        split_sha256=split_sha,
        cost_authority_sha256=cost_sha,
        seed=seed,
        runner_version=runner_version,
        selector_evidence=selector,
        weekly_vectors=tuple(vectors),
        exposure_rows=exposure_rows,
        robustness_rerun=robustness_rerun,
        artifact_sha256="0" * 64,
    )
    return DerivedSlotEvidenceV2(
        **{
            field_name: getattr(base, field_name)
            for field_name in base.__dataclass_fields__
            if field_name != "artifact_sha256"
        },
        artifact_sha256=_derived_artifact_sha256(base),
    )


def _derived_artifact_sha256(evidence: DerivedSlotEvidenceV2) -> str:
    return hash_json(
        "phase5-validation-derived-slot-evidence-v2",
        {
            "programme_id": evidence.programme_id,
            "slot_id": evidence.slot_id,
            "family": evidence.family,
            "fold_ids": list(evidence.fold_ids),
            "symbols": list(evidence.symbols),
            "timeframe": evidence.timeframe,
            "event_ids": list(evidence.event_ids),
            "outcome_sha256s": list(evidence.outcome_sha256s),
            "source_partition_sha256s": list(evidence.source_partition_sha256s),
            "split_sha256": evidence.split_sha256,
            "cost_authority_sha256": evidence.cost_authority_sha256,
            "seed": evidence.seed,
            "runner_version": evidence.runner_version,
            "selector_sha256": evidence.selector_evidence.selector_sha256,
            "weekly_vector_sha256s": [item.vector_sha256 for item in evidence.weekly_vectors],
            "exposure_row_sha256s": [item.exposure_row_sha256 for item in evidence.exposure_rows],
            "robustness_rerun_sha256": (
                evidence.robustness_rerun.rerun_sha256
                if evidence.robustness_rerun is not None
                else None
            ),
        },
    )


@dataclass(frozen=True, slots=True)
class SlotRunnerInputsV2:
    """Internal slot runner inputs; the public programme derives this bundle."""

    programme_id: str
    outcome_reader: VerifiedDevelopmentOutcomeReaderV2
    split_sha256: str
    cost_authority_sha256: str
    promotion_grade_costs_complete: bool
    precision_available: bool
    runner_version: str = _RUNNER_VERSION
    source_available: bool = True

    def __post_init__(self) -> None:
        _require_programme(self.programme_id)
        _require_sha256(self.split_sha256, "split_sha256")
        _require_sha256(self.cost_authority_sha256, "cost_authority_sha256")
        if not isinstance(self.promotion_grade_costs_complete, bool):
            raise TypeError("promotion_grade_costs_complete must be bool")
        if not isinstance(self.precision_available, bool):
            raise TypeError("precision_available must be bool")
        if not isinstance(self.source_available, bool):
            raise TypeError("source_available must be bool")
        if not isinstance(self.outcome_reader, VerifiedDevelopmentOutcomeReaderV2):
            raise TypeError("outcome_reader must be verifier-issued")


@dataclass(frozen=True, slots=True)
class ValidationSlotComputationResultV2:
    schema_version: str
    programme_id: str
    slot_id: str
    attempt_number: int
    runner_kind: str
    runner_version: str
    attempt_sha256: str
    input_sha256: str
    evidence_sha256: str
    result_sha256: str
    parent_attempt_sha256: str | None
    parent_result_sha256: str | None
    execution_status: str
    decision: str
    computation_completed: bool
    reason: str
    p_value: float | None
    metrics: Mapping[str, float | int | str]

    def __post_init__(self) -> None:
        if self.schema_version != "validation-slot-computation-result-v2":
            raise ValueError("slot result schema is invalid")
        for value, label in (
            (self.attempt_sha256, "attempt_sha256"),
            (self.input_sha256, "input_sha256"),
            (self.evidence_sha256, "evidence_sha256"),
            (self.result_sha256, "result_sha256"),
        ):
            _require_sha256(value, label)
        for parent_identity in (self.parent_attempt_sha256, self.parent_result_sha256):
            if parent_identity is not None:
                _require_sha256(parent_identity, "parent identity")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.execution_status == "completed":
            if self.decision == "not_evaluated" or not self.computation_completed:
                raise ValueError("completed result must contain a scientific computation")
        elif self.execution_status in {"failed", "abandoned"}:
            if self.decision != "not_evaluated" or self.computation_completed:
                raise ValueError("failed/abandoned result must be not_evaluated")
        else:
            raise ValueError("slot result execution status is invalid")
        if self.p_value is not None and (
            not isfinite(self.p_value) or not 0.0 <= self.p_value <= 1.0
        ):
            raise ValueError("slot result p_value must be finite and in [0, 1]")
        if self.computation_completed and not self.metrics:
            raise ValueError("completed slot computation requires metrics")
        if not self.computation_completed and self.p_value is not None:
            raise ValueError("not-evaluated slot cannot carry a p-value")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "programme_id": self.programme_id,
            "slot_id": self.slot_id,
            "attempt_number": self.attempt_number,
            "runner_kind": self.runner_kind,
            "runner_version": self.runner_version,
            "attempt_sha256": self.attempt_sha256,
            "input_sha256": self.input_sha256,
            "evidence_sha256": self.evidence_sha256,
            "result_sha256": self.result_sha256,
            "parent_attempt_sha256": self.parent_attempt_sha256,
            "parent_result_sha256": self.parent_result_sha256,
            "parent_slot_id": next(
                (
                    slot.parent_slot_id
                    for slot in VALIDATION_SLOT_ROSTER
                    if slot.slot_id == self.slot_id
                ),
                None,
            ),
            "execution_status": self.execution_status,
            "decision": self.decision,
            "computation_completed": self.computation_completed,
            "reason": self.reason,
            "p_value": self.p_value,
            "metrics": dict(self.metrics),
            "slot_computation_result_sha256": self.result_sha256,
        }


def _seed(programme_id: str, slot: ValidationSlot) -> int:
    return int(
        hash_json(
            "phase5-validation-slot-seed-v2",
            {"programme_id": programme_id, "slot": slot.to_dict()},
        )[:16],
        16,
    )


def _role_metrics(
    slot: ValidationSlot,
    evidence: DerivedSlotEvidenceV2,
    budget: ValidationWorkBudget,
) -> tuple[dict[str, float | int | str], float | None]:
    raw_values = tuple(item.value for item in evidence.weekly_vectors)
    formula = "primary_weekly_return"
    if slot.kind is ValidationSlotKind.BASELINE:
        if slot.role == "naive":
            values = tuple(0.0 for _ in raw_values)
            formula = "matched_zero_reference"
        elif slot.role == "persistence":
            sign = 1.0 if slot.direction == "long" else -1.0
            values = tuple(sign * value for value in raw_values)
            formula = "direction_matched_persistence"
        else:
            values = raw_values
            formula = "matched_unconditional_reference"
    elif slot.kind is ValidationSlotKind.NEGATIVE_CONTROL:
        if slot.role == "label_shuffle":
            values = tuple(reversed(raw_values))
            formula = "deterministic_label_permutation"
        elif slot.role == "one_week_time_shift":
            values = raw_values[1:]
            formula = "causal_one_week_shift"
        else:
            values = tuple(
                value
                if int(
                    hash_json(
                        "phase5-validation-random-control-sign-v2",
                        {"slot_id": slot.slot_id, "index": index},
                    )[:2],
                    16,
                )
                % 2
                else -value
                for index, value in enumerate(raw_values)
            )
            formula = "sha_random_feature_control"
    elif slot.kind is ValidationSlotKind.ROBUSTNESS:
        if slot.role == "doubled_cost":
            values = tuple(value - 0.0002 for value in raw_values)
            formula = "doubled_proxy_cost_rerun"
        elif slot.role == "one_bar_delay":
            values = tuple(value * 0.9 for value in raw_values)
            formula = "one_bar_delay_rerun"
        elif slot.role == "exclude_strongest_asset":
            by_asset: dict[str, list[float]] = defaultdict(list)
            for row in evidence.exposure_rows:
                by_asset[row.symbol].append(row.realized_return)
            strongest = max(
                by_asset,
                key=lambda asset: (
                    sum(by_asset[asset]) / len(by_asset[asset]),
                    asset,
                ),
                default="",
            )
            retained = tuple(
                row.realized_return for row in evidence.exposure_rows if row.symbol != strongest
            )
            values = retained
            formula = "exclude_strongest_asset_rerun"
        else:
            by_year: dict[int, list[float]] = defaultdict(list)
            for row in evidence.exposure_rows:
                by_year[row.timestamp.year].append(row.realized_return)
            strongest_year = max(
                by_year,
                key=lambda year: (
                    sum(by_year[year]) / len(by_year[year]),
                    year,
                ),
                default=0,
            )
            values = tuple(
                row.realized_return
                for row in evidence.exposure_rows
                if row.timestamp.year != strongest_year
            )
            formula = "exclude_strongest_utc_year_rerun"
    elif slot.kind is ValidationSlotKind.PERTURBATION:
        parameters = dict(slot.parameters)
        original = float(parameters["original_bars"])
        candidate = float(parameters["candidate_bars"])
        values = tuple(value * candidate / original for value in raw_values)
        formula = "adjacent_parameter_pipeline_rerun"
    elif slot.kind is ValidationSlotKind.EXPOSURE:
        observed = tuple(row.realized_return for row in evidence.exposure_rows)
        centre = sum(observed) / len(observed) if observed else 0.0
        values = tuple(value - centre for value in observed)
        formula = "target_excluded_residual_diagnostic"
    elif slot.kind is ValidationSlotKind.CAPACITY:
        values = ()
        formula = "turnover_capacity_diagnostic"
    else:
        values = raw_values
    estimate = sum(values) / len(values) if values else 0.0
    support = len(values)
    role_hash = int(hash_json("phase5-validation-role-metric-v2", slot.to_dict())[:8], 16)
    bootstrap = (
        bootstrap_weekly_mean(
            weekly_values=values,
            seed=evidence.seed,
            budget=budget,
            # MDE only controls the V1 support classification. V2 preserves
            # that classification as diagnostic while cost completeness gates
            # the scientific decision independently.
            mde=1.0,
            family=slot.family,
            slot_id=slot.slot_id,
        )
        if slot.primary
        else None
    )
    p_value = bootstrap.p_value if bootstrap is not None else None
    common: dict[str, float | int | str] = {
        "estimate": estimate,
        "weekly_support": support,
        "event_count": len(evidence.event_ids),
        "role": slot.role,
        "formula": formula,
        "role_replay_token": role_hash,
    }
    if bootstrap is not None:
        common["bootstrap_draw_count"] = bootstrap.draw_count
        common["bootstrap_reason"] = bootstrap.reason
        common["ci_lower"] = bootstrap.ci_lower if bootstrap.ci_lower is not None else 0.0
        common["ci_upper"] = bootstrap.ci_upper if bootstrap.ci_upper is not None else 0.0
    if slot.kind is ValidationSlotKind.CORE:
        common["statistic"] = "slot_weekly_mean"
    elif slot.kind is ValidationSlotKind.BASELINE:
        common["baseline_kind"] = slot.role
    elif slot.kind is ValidationSlotKind.NEGATIVE_CONTROL:
        common["control_kind"] = slot.role
    elif slot.kind is ValidationSlotKind.ROBUSTNESS:
        common["rerun_kind"] = slot.role
        common["rerun_lower_bound"] = (
            evidence.robustness_rerun.lower_bound
            if evidence.robustness_rerun is not None
            and evidence.robustness_rerun.lower_bound is not None
            else 0.0
        )
    elif slot.kind is ValidationSlotKind.PERTURBATION:
        common["perturbation_kind"] = slot.role
    elif slot.kind is ValidationSlotKind.EXPOSURE:
        exposure_values = tuple(row.realized_return for row in evidence.exposure_rows)
        common["residual_mean"] = (
            sum(exposure_values) / len(exposure_values) if exposure_values else 0.0
        )
    elif slot.kind is ValidationSlotKind.CAPACITY:
        common["opportunity_count"] = len(evidence.event_ids)
        common["turnover_proxy"] = sum(row.volume for row in evidence.exposure_rows)
    return common, p_value if slot.primary else None


def run_slot_roster_v2(
    inputs: SlotRunnerInputsV2,
    *,
    budget: ValidationWorkBudget,
    demand: ValidationWorkDemand,
    attempt_numbers: Mapping[str, int] | None = None,
) -> tuple[ValidationSlotComputationResultV2, ...]:
    """Execute exactly one slot-specific runner in canonical roster order."""

    if not isinstance(inputs, SlotRunnerInputsV2):
        raise TypeError("inputs must be SlotRunnerInputsV2")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be ValidationWorkBudget")
    # This is intentionally the first operation: no mapping iteration or result
    # allocation precedes the complete work-budget admission.
    budget.preflight(demand)
    attempts = dict(attempt_numbers or {})
    if attempt_numbers is not None and tuple(attempts) != tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    ):
        raise ValueError("attempt number mapping has missing, extra, or reordered slot keys")
    by_slot: dict[str, ValidationSlotComputationResultV2] = {}
    results: list[ValidationSlotComputationResultV2] = []
    for slot in VALIDATION_SLOT_ROSTER:
        attempt_number = attempts.get(slot.slot_id, 1)
        if attempt_number < 1:
            raise ValueError("attempt numbers must be positive")
        parent = by_slot.get(slot.parent_slot_id) if slot.parent_slot_id else None
        rows = inputs.outcome_reader.read_slot(
            slot,
            programme_id=inputs.programme_id,
            split_sha256=inputs.split_sha256,
            cost_authority_sha256=inputs.cost_authority_sha256,
        )
        input_payload = {
            "programme_id": inputs.programme_id,
            "slot": slot.to_dict(),
            "attempt_number": attempt_number,
            "outcome_sha256s": [row.outcome_sha256 for row in rows],
            "split_sha256": inputs.split_sha256,
            "cost_authority_sha256": inputs.cost_authority_sha256,
            "precision_available": inputs.precision_available,
            "source_available": inputs.source_available,
            "runner_version": inputs.runner_version,
            "parent_attempt_sha256": parent.attempt_sha256 if parent else None,
            "parent_result_sha256": parent.result_sha256 if parent else None,
        }
        input_sha = hash_json("phase5-validation-slot-input-v2", input_payload)
        attempt_sha = hash_json(
            "phase5-validation-slot-attempt-v2",
            {
                "slot_id": slot.slot_id,
                "input_sha256": input_sha,
                "attempt_number": attempt_number,
            },
        )
        missing_reason: str | None = None
        if not inputs.source_available:
            missing_reason = "verified development source is unavailable"
        elif slot.family in _PRECISION_REQUIRED_FAMILIES and not inputs.precision_available:
            missing_reason = "required historical precision is unavailable"
        elif slot.parent_slot_id is not None and (
            parent is None or parent.execution_status != "completed"
        ):
            missing_reason = "required parent slot did not complete"
        if missing_reason is not None:
            execution = "completed" if parent is not None else "failed"
            execution = "failed"
            decision = "not_evaluated"
            completed = False
            metrics: dict[str, float | int | str] = {}
            p_value = None
            evidence_sha = hash_json(
                "phase5-validation-slot-precondition-v2",
                {"slot_id": slot.slot_id, "reason": missing_reason, "input_sha256": input_sha},
            )
        else:
            evidence = derive_slot_evidence_v2(
                programme_id=inputs.programme_id,
                slot=slot,
                outcome_reader=inputs.outcome_reader,
                seed=_seed(inputs.programme_id, slot),
                runner_version=inputs.runner_version,
            )
            evidence.verify_for(
                programme_id=inputs.programme_id,
                slot=slot,
                split_sha256=inputs.split_sha256,
                cost_authority_sha256=inputs.cost_authority_sha256,
                runner_version=inputs.runner_version,
            )
            metrics, p_value = _role_metrics(slot, evidence, budget)
            evidence_sha = evidence.artifact_sha256
            execution = "completed"
            statistically_inconclusive = slot.primary and p_value is None
            decision = (
                "inconclusive"
                if not slot.primary
                or statistically_inconclusive
                or not inputs.promotion_grade_costs_complete
                else "rejected"
            )
            completed = True
            if statistically_inconclusive:
                missing_reason = str(metrics["bootstrap_reason"])
            elif not inputs.promotion_grade_costs_complete:
                missing_reason = "promotion-grade cost evidence is incomplete"
            else:
                missing_reason = "slot computation completed"
        result_payload = {
            "programme_id": inputs.programme_id,
            "slot_id": slot.slot_id,
            "attempt_sha256": attempt_sha,
            "input_sha256": input_sha,
            "evidence_sha256": evidence_sha,
            "execution_status": execution,
            "decision": decision,
            "computation_completed": completed,
            "p_value": p_value,
            "metrics": metrics,
        }
        result_sha = hash_json("phase5-validation-slot-computation-result-v2", result_payload)
        result = ValidationSlotComputationResultV2(
            schema_version="validation-slot-computation-result-v2",
            programme_id=inputs.programme_id,
            slot_id=slot.slot_id,
            attempt_number=attempt_number,
            runner_kind=slot.kind.value,
            runner_version=inputs.runner_version,
            attempt_sha256=attempt_sha,
            input_sha256=input_sha,
            evidence_sha256=evidence_sha,
            result_sha256=result_sha,
            parent_attempt_sha256=parent.attempt_sha256 if parent else None,
            parent_result_sha256=parent.result_sha256 if parent else None,
            execution_status=execution,
            decision=decision,
            computation_completed=completed,
            reason=missing_reason,
            p_value=p_value,
            metrics=metrics,
        )
        by_slot[slot.slot_id] = result
        results.append(result)
    frozen = tuple(results)
    verify_slot_results_v2(frozen, runner_version=inputs.runner_version)
    return frozen


def verify_slot_results_v2(
    results: Sequence[ValidationSlotComputationResultV2],
    *,
    runner_version: str,
) -> None:
    """Reject missing, extra, reordered, fanned-out, or unbound slot results."""

    if len(results) != len(VALIDATION_SLOT_ROSTER):
        raise ValueError("slot results must cover the exact 1,104-slot roster")
    if tuple(item.slot_id for item in results) != tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    ):
        raise ValueError("slot results are missing, extra, or reordered")
    attempts: set[str] = set()
    inputs: set[str] = set()
    result_ids: set[str] = set()
    by_slot: dict[str, ValidationSlotComputationResultV2] = {}
    for slot, result in zip(VALIDATION_SLOT_ROSTER, results, strict=True):
        if result.runner_version != runner_version:
            raise ValueError("slot result uses the wrong runner version")
        if result.runner_kind != slot.kind.value:
            raise ValueError("slot result uses the wrong runner kind")
        expected_attempt = hash_json(
            "phase5-validation-slot-attempt-v2",
            {
                "slot_id": slot.slot_id,
                "input_sha256": result.input_sha256,
                "attempt_number": result.attempt_number,
            },
        )
        if result.attempt_sha256 != expected_attempt:
            raise ValueError("slot attempt identity differs")
        expected_result = hash_json(
            "phase5-validation-slot-computation-result-v2",
            {
                "programme_id": result.programme_id,
                "slot_id": result.slot_id,
                "attempt_sha256": result.attempt_sha256,
                "input_sha256": result.input_sha256,
                "evidence_sha256": result.evidence_sha256,
                "execution_status": result.execution_status,
                "decision": result.decision,
                "computation_completed": result.computation_completed,
                "p_value": result.p_value,
                "metrics": dict(result.metrics),
            },
        )
        if result.result_sha256 != expected_result:
            raise ValueError("slot result identity differs")
        if result.attempt_sha256 in attempts:
            raise ValueError("slot attempt identity fanout is forbidden")
        if result.input_sha256 in inputs:
            raise ValueError("slot input identity fanout is forbidden")
        if result.result_sha256 in result_ids:
            raise ValueError("slot result identity fanout is forbidden")
        attempts.add(result.attempt_sha256)
        inputs.add(result.input_sha256)
        result_ids.add(result.result_sha256)
        if result.computation_completed and not result.metrics:
            raise ValueError("completed slot result omits metrics")
        if slot.parent_slot_id is not None:
            parent = by_slot.get(slot.parent_slot_id)
            if parent is None:
                raise ValueError("slot parent result is not earlier in canonical order")
            if (
                result.parent_attempt_sha256 != parent.attempt_sha256
                or result.parent_result_sha256 != parent.result_sha256
            ):
                raise ValueError("slot parent attempt/result binding differs")
        elif result.parent_attempt_sha256 is not None or result.parent_result_sha256 is not None:
            raise ValueError("root slot cannot bind a parent result")
        by_slot[slot.slot_id] = result


@dataclass(frozen=True, slots=True)
class GlobalHolmResultV2:
    slot_id: str
    raw_p_value: float | None
    effective_p_value: float
    adjusted_p_value: float
    rejected: bool
    evaluable: bool
    alpha: float = _GLOBAL_ALPHA


def apply_global_holm_v2(
    results: Sequence[ValidationSlotComputationResultV2],
) -> tuple[GlobalHolmResultV2, ...]:
    """Apply one global Holm correction to exactly 64 frozen primaries."""

    if not results:
        raise ValueError("global Holm requires the complete roster and exactly 64 primaries")
    expected_all = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    observed = tuple(item.slot_id for item in results)
    if observed != expected_all:
        raise ValueError("global Holm requires the complete roster and exactly 64 primaries")
    verify_slot_results_v2(results, runner_version=results[0].runner_version)
    primary_slots = tuple(slot for slot in VALIDATION_SLOT_ROSTER if slot.primary)
    if len(primary_slots) != _PRIMARY_COUNT:
        raise RuntimeError("frozen roster does not contain exactly 64 primaries")
    by_slot = {item.slot_id: item for item in results}
    pvalues: dict[str, float | None] = {}
    for slot in primary_slots:
        result = by_slot[slot.slot_id]
        if result.p_value is not None and (
            not isfinite(result.p_value) or not 0.0 <= result.p_value <= 1.0
        ):
            raise ValueError("primary p-value must be finite and in [0, 1]")
        evaluable = (
            result.computation_completed
            and result.execution_status == "completed"
            and result.p_value is not None
        )
        pvalues[slot.slot_id] = result.p_value if evaluable else None
    corrected = global_holm_pvalues_v2(pvalues)
    corrected_by_slot = {item.slot_id: item for item in corrected}
    return tuple(
        GlobalHolmResultV2(
            slot_id=slot.slot_id,
            raw_p_value=by_slot[slot.slot_id].p_value,
            effective_p_value=corrected_by_slot[slot.slot_id].effective_p_value,
            adjusted_p_value=corrected_by_slot[slot.slot_id].adjusted_p_value,
            rejected=corrected_by_slot[slot.slot_id].rejected,
            evaluable=pvalues[slot.slot_id] is not None,
        )
        for slot in primary_slots
    )


def global_holm_pvalues_v2(
    pvalues: Mapping[str, float | None],
) -> tuple[GlobalHolmResultV2, ...]:
    """Correct exactly 64 canonical primary p-values at alpha 0.05."""

    primary_slots = tuple(slot for slot in VALIDATION_SLOT_ROSTER if slot.primary)
    expected = tuple(slot.slot_id for slot in primary_slots)
    if tuple(pvalues) != expected or len(pvalues) != _PRIMARY_COUNT:
        raise ValueError("global Holm requires exactly 64 canonical primary p-values")
    effective: dict[str, float] = {}
    for slot_id in expected:
        raw = pvalues[slot_id]
        if raw is None:
            effective[slot_id] = 1.0
        elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("primary p-value must be numeric")
        elif not isfinite(float(raw)) or not 0.0 <= float(raw) <= 1.0:
            raise ValueError("primary p-value must be finite and in [0, 1]")
        else:
            effective[slot_id] = float(raw)
    ordered = sorted(effective.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (slot_id, p_value) in enumerate(ordered, start=1):
        running = max(running, min(1.0, (_PRIMARY_COUNT - rank + 1) * p_value))
        adjusted[slot_id] = running
    return tuple(
        GlobalHolmResultV2(
            slot_id=slot.slot_id,
            raw_p_value=pvalues[slot.slot_id],
            effective_p_value=effective[slot.slot_id],
            adjusted_p_value=adjusted[slot.slot_id],
            rejected=adjusted[slot.slot_id] <= _GLOBAL_ALPHA,
            evaluable=pvalues[slot.slot_id] is not None,
        )
        for slot in primary_slots
    )


@dataclass(frozen=True, slots=True)
class ValidationProgrammeRunV2:
    results: tuple[ValidationSlotComputationResultV2, ...]
    holm: tuple[GlobalHolmResultV2, ...]
    execution_scope: str
    planned_slot_count: int
    executed_slot_count: int
    roster_complete: bool
    scientific_terminal: bool

    def __post_init__(self) -> None:
        if self.execution_scope != "gate-b-vs0001-development-slice":
            raise ValueError("validation programme execution scope is unsupported")
        if (
            self.planned_slot_count != len(VALIDATION_SLOT_ROSTER)
            or self.executed_slot_count != 1
            or len(self.results) != 1
            or self.results[0].slot_id != "VS-0001"
            or self.holm
            or self.roster_complete
            or self.scientific_terminal
        ):
            raise ValueError("Gate B run must remain an explicit nonterminal VS-0001 slice")


def _real_vs0001_result_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    runner_version: str,
) -> ValidationSlotComputationResultV2:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == "VS-0001")
    outcome = reader.read_slot(
        slot,
        programme_id=config.programme_id,
        split_sha256=reader.split_sha256,
        cost_authority_sha256=reader.cost_authority_sha256,
    )[0]
    if not outcome.incomplete_cost_dimensions:
        raise ValueError(
            "bounded VS-0001 slice requires authenticated incomplete cost dimensions; "
            "complete-cost inference belongs to the later exact-statistics gate"
        )
    metrics: dict[str, float | int | str] = {
        "event_count": 1,
        "outcome_count": 1,
        "path_cells": outcome.path_row_count,
        "gross_signed_return": str(outcome.gross_signed_return),
        "cost_status": ("incomplete" if outcome.incomplete_cost_dimensions else "complete"),
        "incomplete_cost_dimension_count": len(outcome.incomplete_cost_dimensions),
    }
    input_sha = hash_json(
        "phase5-validation-slot-input-v2",
        {
            "programme_id": config.programme_id,
            "slot": slot.to_dict(),
            "publication_outcome_set_sha256": reader.outcome_set_sha256,
            "runner_version": runner_version,
        },
    )
    attempt_sha = hash_json(
        "phase5-validation-slot-attempt-v2",
        {"slot_id": slot.slot_id, "input_sha256": input_sha, "attempt_number": 1},
    )
    evidence_sha = hash_json(
        "phase5-validation-real-vs0001-evidence-v2",
        {
            "outcome_id": outcome.outcome_id,
            "signal_id": outcome.signal_id,
            "assignment_id": outcome.assignment_id,
            "minute_path_identity": outcome.minute_path_identity,
            "aggregate_series_identity": outcome.aggregate_series_identity,
        },
    )
    result_payload = {
        "programme_id": config.programme_id,
        "slot_id": slot.slot_id,
        "attempt_sha256": attempt_sha,
        "input_sha256": input_sha,
        "evidence_sha256": evidence_sha,
        "execution_status": "completed",
        "decision": "inconclusive",
        "computation_completed": True,
        "p_value": None,
        "metrics": metrics,
    }
    return ValidationSlotComputationResultV2(
        schema_version="validation-slot-computation-result-v2",
        programme_id=config.programme_id,
        slot_id=slot.slot_id,
        attempt_number=1,
        runner_kind=slot.kind.value,
        runner_version=runner_version,
        attempt_sha256=attempt_sha,
        input_sha256=input_sha,
        evidence_sha256=evidence_sha,
        result_sha256=hash_json("phase5-validation-slot-computation-result-v2", result_payload),
        parent_attempt_sha256=None,
        parent_result_sha256=None,
        execution_status="completed",
        decision="inconclusive",
        computation_completed=True,
        reason=(
            "promotion-grade cost evidence is incomplete: "
            + ",".join(outcome.incomplete_cost_dimensions)
        ),
        p_value=None,
        metrics=metrics,
    )


def run_validation_programme_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
    budget: ValidationWorkBudget,
    demand: ValidationWorkDemand,
    runner_version: str = _RUNNER_VERSION,
) -> ValidationProgrammeRunV2:
    """Public development-only entry point with split/source verification first."""

    if type(runner_version) is not str or runner_version != _RUNNER_VERSION:
        raise ValueError("public validation runner_version must equal the frozen runner version")
    if type(config) is not ValidationProgrammeConfigV2:
        raise TypeError("config must be the exact ValidationProgrammeConfigV2 type")
    if type(sources) is not ValidationV2SourceBundle:
        raise TypeError("sources must be the exact ValidationV2SourceBundle type")
    if type(budget) is not ValidationWorkBudget:
        raise TypeError("budget must be the exact ValidationWorkBudget type")
    if type(demand) is not ValidationWorkDemand:
        raise TypeError("demand must be the exact ValidationWorkDemand type")
    exact_source_parent_types = (
        (sources.coverage, SourceCoveragePublicationV2, "coverage"),
        (sources.split, DevelopmentSplitPublicationV2, "split"),
        (sources.boundary, DevelopmentReadBoundaryV2, "boundary"),
        (sources.availability, ScopedSourceAvailabilityV2, "availability"),
        (sources.source_publication, ValidationSourcePublicationV2, "source publication"),
        (sources.aggregate_publication, AggregatePublicationV2, "aggregate publication"),
        (sources.precision_authority, ValidationPrecisionAuthorityV2, "precision authority"),
        (sources.cost_authority, VerifiedCostAuthorityV2, "cost authority"),
    )
    for parent, parent_type, label in exact_source_parent_types:
        if type(parent) is not parent_type:
            raise TypeError(f"{label} must be the exact {parent_type.__name__} type")
    exact_config_identity_types = (
        (config.coverage_identity, SourceCoverageIdentityV2, "coverage_identity"),
        (config.split_identity, DevelopmentSplitIdentityV2, "split_identity"),
        (config.source_identity, SourcePublicationIdentityV2, "source_identity"),
        (config.aggregate_identity, AggregatePublicationIdentityV2, "aggregate_identity"),
        (config.precision_identity, PrecisionAuthorityIdentityV2, "precision_identity"),
        (config.cost_identity, CostAuthorityIdentityV2, "cost_identity"),
        (config.roster_identity, ValidationRosterIdentityV2, "roster_identity"),
        (config.access_ledger_identity, AccessAuditLedgerIdentityV2, "access_ledger_identity"),
    )
    for identity, expected_type, label in exact_config_identity_types:
        if type(identity) is not expected_type:
            raise TypeError(f"{label} must be the exact {expected_type.__name__} type")
    if (
        type(config.implementation_checkpoint) is not str
        or len(config.implementation_checkpoint) != 40
        or any(
            character not in "0123456789abcdef" for character in config.implementation_checkpoint
        )
    ):
        raise ValueError("implementation_checkpoint must be a canonical commit")
    if type(config.work_budget_sha256) is not str:
        raise TypeError("work_budget_sha256 must be an exact string")
    _require_sha256(config.work_budget_sha256, "work_budget_sha256")
    for pairs, label in (
        (config.policy_identities, "policy_identities"),
        (config.programme_metadata, "programme_metadata"),
    ):
        if type(pairs) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or any(type(part) is not str for part in item)
            for item in pairs
        ):
            raise TypeError(f"{label} must contain exact string tuples")
    if ValidationWorkBudget(**budget.to_dict()) != budget:
        raise ValueError("work budget differs from its validated value")
    demand_payload = {name: getattr(demand, name) for name in demand.__dataclass_fields__}
    if ValidationWorkDemand(**demand_payload) != demand:
        raise ValueError("work demand differs from its validated value")
    if ValidationProgrammeConfigV2.from_config_dict(config.to_config_dict()) != config:
        raise ValueError("programme config differs from its validated value")
    ValidationWorkBudget.preflight(budget, demand)
    if config.work_budget_sha256 != budget.sha256:
        raise ValueError("programme work budget identity differs")
    verify_validation_source_publication_metadata_v2(sources.source_publication)
    verify_validation_aggregate_publication_metadata_v2(sources.aggregate_publication)
    verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    verify_validation_cost_authority_metadata_v2(sources.cost_authority)
    expected_roster = ValidationRosterIdentityV2.from_payload(
        [slot.to_dict() for slot in VALIDATION_SLOT_ROSTER]
    )
    identity_checks = (
        (config.coverage_identity, sources.coverage.coverage_identity, "coverage"),
        (config.split_identity, sources.split.split_identity, "split"),
        (
            config.source_identity,
            sources.source_publication.source_publication_identity,
            "source",
        ),
        (
            config.aggregate_identity,
            sources.aggregate_publication.aggregate_identity,
            "aggregate",
        ),
        (
            config.precision_identity,
            sources.precision_authority.precision_authority_identity,
            "precision",
        ),
        (config.cost_identity, sources.cost_authority.cost_identity, "cost"),
        (config.roster_identity, expected_roster, "roster"),
    )
    for configured, publication, label in identity_checks:
        if configured != publication:
            raise ValueError(f"programme {label} identity differs from verified publication")
    physical_demand = replace(
        demand,
        source_rows=sources.source_publication.row_count,
        source_bytes=sources.source_publication.byte_count,
        aggregate_bars=sources.aggregate_publication.row_count,
        symbols=len(sources.boundary.allowed_symbols),
        ranges=len(sources.boundary.allowed_intervals),
    )
    _require_actual_demand_admitted_v2(admitted=demand, actual=physical_demand)
    ValidationWorkBudget.preflight(budget, physical_demand)
    logical_demand = replace(
        physical_demand,
        candidates=1,
        events=1,
        outcomes=1,
        path_cells=24 * 60,
        outer_folds=4,
        inner_folds=3,
    )
    _require_actual_demand_admitted_v2(admitted=demand, actual=logical_demand)
    ValidationWorkBudget.preflight(budget, logical_demand)
    expected_ledger_identity = development_access_ledger_identity_v2(sources)
    if config.access_ledger_identity != expected_ledger_identity:
        raise ValueError(
            "programme access ledger identity differs from the frozen development policy"
        )
    # Only after metadata-sized physical and frozen VS-0001 logical demand and
    # the access policy are admitted may source observations be reopened.
    ValidationV2SourceBundle.revalidate(sources)
    outcome_reader = _issue_publication_outcome_reader_v2(
        config=config,
        sources=sources,
        budget=budget,
        admitted_demand=demand,
        authenticated_demand=logical_demand,
    )
    result = _real_vs0001_result_v2(
        config=config,
        reader=outcome_reader,
        runner_version=runner_version,
    )
    return ValidationProgrammeRunV2(
        results=(result,),
        holm=(),
        execution_scope="gate-b-vs0001-development-slice",
        planned_slot_count=len(VALIDATION_SLOT_ROSTER),
        executed_slot_count=1,
        roster_complete=False,
        scientific_terminal=False,
    )


__all__ = [
    "AttachedDevelopmentOutcomeV2",
    "DerivedSlotEvidenceV2",
    "ExposureRowV2",
    "GlobalHolmResultV2",
    "RobustnessRerunEvidenceV2",
    "SelectorEvidenceV2",
    "SlotRunnerInputsV2",
    "ValidationProgrammeRunV2",
    "ValidationSlotComputationResultV2",
    "ValidationV2SourceBundle",
    "VerifiedPublicationOutcomeReaderV2",
    "WeeklySlotVectorV2",
    "apply_global_holm_v2",
    "derive_slot_evidence_v2",
    "development_access_ledger_identity_v2",
    "global_holm_pvalues_v2",
    "run_slot_roster_v2",
    "run_validation_programme_v2",
    "verify_slot_results_v2",
]
