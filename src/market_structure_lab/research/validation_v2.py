"""Development-only Phase 5 V2 evidence derivation and slot execution.

The V2 public boundary accepts only publisher-issued publications.  Raw
statistical summaries, selector scores, terminal decisions, and eligibility
flags are deliberately absent: they are derived after the split and every
result is bound to one frozen roster slot.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from math import isfinite
from types import MappingProxyType
import re
from typing import cast
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
    ExecutionStatus,
    ScientificDecision,
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    candidate_definition_for_verified_series,
)
from market_structure_lab.research.statistics import (
    EvaluationStatistic,
    holm_family_correction,
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

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VPV2-[a-f0-9]{64}$")
_RUNNER_VERSION = "phase5-validation-slot-runner-v2"
_PRIMARY_COUNT = 64
_FAMILY_ALPHA = 0.01
_PRECISION_REQUIRED_FAMILIES = frozenset({"B"})
_OUTCOME_FACTORY = object()
_OUTCOME_READER_FACTORY = object()
_PUBLICATION_OUTCOME_READER_ISSUANCE: dict[int, tuple[object, tuple[object, ...]]] = {}
_VERIFIED_OUTCOME_READERS: dict[
    int,
    tuple[
        weakref.ReferenceType[VerifiedDevelopmentOutcomeReaderV2],
        tuple[object, ...],
        Mapping[str, tuple[tuple[object, ...], ...]],
    ],
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
        if registration is None or registration[0]() is not self:
            raise ValueError("outcome reader is not the registered original")
        if _fixture_reader_outer_snapshot_v2(self) != registration[1]:
            raise ValueError("outcome reader differs from its registered original")
        if (
            self.programme_id != programme_id
            or self.split_sha256 != split_sha256
            or self.cost_authority_sha256 != cost_authority_sha256
        ):
            raise ValueError("outcome reader authority binding differs")
        if slot.slot_id not in self._outcomes_by_slot:
            raise ValueError("outcome reader is missing the requested slot")
        outcomes = self._outcomes_by_slot[slot.slot_id]
        if _fixture_outcome_rows_snapshot_v2(outcomes) != registration[2][slot.slot_id]:
            raise ValueError("outcome reader slot rows differ from their registered original")
        return outcomes

    def verify_original(self) -> VerifiedDevelopmentOutcomeReaderV2:
        """Revalidate this exact fixture reader against its registered bytes."""

        return _verify_fixture_outcome_reader_v2(self)


def _fixture_outcome_snapshot_v2(outcome: AttachedDevelopmentOutcomeV2) -> tuple[object, ...]:
    if type(outcome) is not AttachedDevelopmentOutcomeV2:
        raise TypeError("fixture outcome row must be the exact verifier-issued type")
    for name in ("event_id", "slot_id", "fold_id", "symbol", "timeframe", "partition_role"):
        value = getattr(outcome, name)
        if type(value) is not str or not value:
            raise TypeError("fixture outcome row string field has an invalid exact type")
    if type(outcome.timestamp) is not datetime:
        raise TypeError("fixture outcome timestamp must be an exact datetime")
    _require_utc(outcome.timestamp, "fixture outcome timestamp")
    for name in ("net_return", "detector_metric", "volume"):
        value = getattr(outcome, name)
        if type(value) is not float or not isfinite(value):
            raise TypeError("fixture outcome numeric field has an invalid exact type")
    if outcome.volume < 0:
        raise ValueError("fixture outcome volume must be non-negative")
    for name in (
        "source_partition_sha256",
        "aggregate_row_sha256",
        "split_sha256",
        "cost_authority_sha256",
        "source_publication_sha256",
        "aggregate_publication_sha256",
    ):
        value = getattr(outcome, name)
        if type(value) is not str:
            raise TypeError("fixture outcome hash field has an invalid exact type")
        _require_sha256(value, name)
    return (
        outcome.event_id,
        outcome.slot_id,
        outcome.fold_id,
        outcome.symbol,
        outcome.timeframe,
        outcome.timestamp,
        outcome.net_return,
        outcome.source_partition_sha256,
        outcome.aggregate_row_sha256,
        outcome.split_sha256,
        outcome.cost_authority_sha256,
        outcome.source_publication_sha256,
        outcome.aggregate_publication_sha256,
        outcome.detector_metric,
        outcome.volume,
        outcome.partition_role,
        outcome.outcome_sha256,
    )


def _fixture_outcome_rows_snapshot_v2(
    outcomes: tuple[AttachedDevelopmentOutcomeV2, ...],
) -> tuple[tuple[object, ...], ...]:
    if type(outcomes) is not tuple:
        raise TypeError("fixture outcome rows must be an exact tuple")
    return tuple(_fixture_outcome_snapshot_v2(outcome) for outcome in outcomes)


def _fixture_reader_outer_snapshot_v2(
    reader: VerifiedDevelopmentOutcomeReaderV2,
) -> tuple[object, ...]:
    if type(reader.programme_id) is not str:
        raise TypeError("fixture outcome reader programme_id must be an exact string")
    _require_programme(reader.programme_id)
    for value, label in (
        (reader.split_sha256, "split_sha256"),
        (reader.cost_authority_sha256, "cost_authority_sha256"),
        (reader.source_publication_sha256, "source_publication_sha256"),
        (reader.aggregate_publication_sha256, "aggregate_publication_sha256"),
        (reader.fixture_sha256, "fixture_sha256"),
    ):
        if type(value) is not str:
            raise TypeError(f"fixture outcome reader {label} must be an exact string")
        _require_sha256(value, label)
    if type(reader.slot_ids) is not tuple or any(
        type(slot_id) is not str for slot_id in reader.slot_ids
    ):
        raise TypeError("fixture outcome reader slot_ids must be an exact string tuple")
    if type(reader.canonical_bytes) is not bytes:
        raise TypeError("fixture outcome reader canonical bytes must be exact bytes")
    if type(reader._outcomes_by_slot) is not MappingProxyType:
        raise TypeError("fixture outcome reader slot mapping must be sealed")
    if tuple(reader._outcomes_by_slot) != reader.slot_ids:
        raise ValueError("fixture outcome reader slot order changed")
    return (
        reader.programme_id,
        reader.split_sha256,
        reader.cost_authority_sha256,
        reader.source_publication_sha256,
        reader.aggregate_publication_sha256,
        reader.slot_ids,
        reader.fixture_sha256,
        reader.canonical_bytes,
    )


def _fixture_reader_outcomes_snapshot_v2(
    reader: VerifiedDevelopmentOutcomeReaderV2,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    return tuple(
        (slot_id, _fixture_outcome_rows_snapshot_v2(reader._outcomes_by_slot[slot_id]))
        for slot_id in reader.slot_ids
    )


def _verify_fixture_outcome_reader_v2(
    reader: VerifiedDevelopmentOutcomeReaderV2,
) -> VerifiedDevelopmentOutcomeReaderV2:
    if type(reader) is not VerifiedDevelopmentOutcomeReaderV2:
        raise TypeError("fixture outcome reader must be the exact verifier-issued type")
    registration = _VERIFIED_OUTCOME_READERS.get(id(reader))
    if registration is None or registration[0]() is not reader:
        raise ValueError("fixture outcome reader is not the registered original")
    if registration[1] != _fixture_reader_outer_snapshot_v2(reader):
        raise ValueError("fixture outcome reader differs from its registered original")
    if tuple(registration[2].items()) != _fixture_reader_outcomes_snapshot_v2(reader):
        raise ValueError("fixture outcome reader rows changed from their registered originals")
    if hashlib.sha256(reader.canonical_bytes).hexdigest() != reader.fixture_sha256:
        raise ValueError("fixture outcome reader computation evidence changed")
    return reader


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
    _VERIFIED_OUTCOME_READERS[identifier] = (
        reference,
        _fixture_reader_outer_snapshot_v2(reader),
        MappingProxyType(dict(_fixture_reader_outcomes_snapshot_v2(reader))),
    )
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


_RESULT_ISSUANCE: dict[int, object] = {}
_VERIFIED_RESULTS: dict[
    int,
    tuple[
        weakref.ReferenceType[ValidationSlotComputationResultV2],
        tuple[object, ...],
        Callable[[], object],
        object,
        ValidationSlotComputationResultV2 | None,
        object | None,
        Callable[[], object] | None,
        Mapping[str, object],
    ],
] = {}

_MAX_RESULT_METRICS = 64
_MAX_RESULT_METRIC_TEXT = 4_096
_MAX_RESULT_METRICS_BYTES = 64 * 1024
_MAX_RESULT_METRIC_INTEGER = 2**63 - 1


@dataclass(frozen=True, slots=True, weakref_slot=True)
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
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        token = _RESULT_ISSUANCE.pop(id(_factory_token), None)
        if _factory_token is None or token is not _factory_token:
            raise TypeError("ValidationSlotComputationResultV2 requires its computation factory")
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
        object.__setattr__(
            self, "metrics", MappingProxyType(_bounded_result_metrics_v2(self.metrics))
        )

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


def _result_snapshot(result: ValidationSlotComputationResultV2) -> tuple[object, ...]:
    _validate_result_shape(result)
    return (
        result.schema_version,
        result.programme_id,
        result.slot_id,
        result.attempt_number,
        result.runner_kind,
        result.runner_version,
        result.attempt_sha256,
        result.input_sha256,
        result.evidence_sha256,
        result.result_sha256,
        result.parent_attempt_sha256,
        result.parent_result_sha256,
        result.execution_status,
        result.decision,
        result.computation_completed,
        result.reason,
        result.p_value,
        tuple(sorted(result.metrics.items())),
    )


def _bounded_result_metrics_v2(metrics: object) -> dict[str, float | int | str]:
    if type(metrics) not in {dict, MappingProxyType}:
        raise TypeError("slot computation result metrics must be an exact bounded mapping")
    typed_metrics = cast(Mapping[object, object], metrics)
    if len(typed_metrics) > _MAX_RESULT_METRICS:
        raise ValueError("slot computation result metrics exceed the frozen cardinality bound")
    bounded: dict[str, float | int | str] = {}
    for key, value in typed_metrics.items():
        if type(key) is not str or type(value) not in {float, int, str}:
            raise TypeError("slot computation result metric has an invalid exact shape")
        if not key or len(key) > 128:
            raise ValueError("slot computation result metric name exceeds the frozen bound")
        if type(value) is str and len(value) > _MAX_RESULT_METRIC_TEXT:
            raise ValueError("slot computation result metric text exceeds the frozen bound")
        if type(value) is float and not isfinite(value):
            raise ValueError("slot computation result metric must be finite")
        if type(value) is int and abs(value) > _MAX_RESULT_METRIC_INTEGER:
            raise ValueError("slot computation result metric integer exceeds the frozen bound")
        bounded[key] = cast(float | int | str, value)
    encoded = json.dumps(
        bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    if len(encoded) > _MAX_RESULT_METRICS_BYTES:
        raise ValueError("slot computation result metrics exceed the frozen byte bound")
    return bounded


def _validate_result_shape(result: ValidationSlotComputationResultV2) -> None:
    string_fields = (
        "schema_version",
        "programme_id",
        "slot_id",
        "runner_kind",
        "runner_version",
        "attempt_sha256",
        "input_sha256",
        "evidence_sha256",
        "result_sha256",
        "execution_status",
        "decision",
        "reason",
    )
    if any(type(getattr(result, name)) is not str for name in string_fields):
        raise TypeError("slot computation result string field has an invalid exact type")
    if type(result.attempt_number) is not int:
        raise TypeError("slot computation result attempt_number must be an exact integer")
    if type(result.computation_completed) is not bool:
        raise TypeError("slot computation result completion flag must be an exact bool")
    for value in (result.parent_attempt_sha256, result.parent_result_sha256):
        if value is not None and type(value) is not str:
            raise TypeError("slot computation result parent identity has an invalid exact type")
    if result.p_value is not None and type(result.p_value) is not float:
        raise TypeError("slot computation result p_value must be an exact float or null")
    if type(result.metrics) is not MappingProxyType:
        raise TypeError("slot computation result metrics must be the sealed exact mapping")
    _bounded_result_metrics_v2(result.metrics)


def _result_slot_v2(result: ValidationSlotComputationResultV2) -> ValidationSlot:
    matches = tuple(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == result.slot_id)
    if len(matches) != 1:
        raise ValueError("slot computation result is outside the frozen roster")
    return matches[0]


def _validate_individual_result_contract_v2(
    result: ValidationSlotComputationResultV2,
    *,
    computational_parent: ValidationSlotComputationResultV2 | None,
) -> ValidationSlot:
    _validate_result_shape(result)
    if result.schema_version != "validation-slot-computation-result-v2":
        raise ValueError("slot computation result schema is invalid")
    _require_programme(result.programme_id)
    slot = _result_slot_v2(result)
    if result.runner_kind != slot.kind.value:
        raise ValueError("slot computation result runner kind differs from the frozen roster")
    if not result.runner_version:
        raise ValueError("slot computation result runner version must be non-empty")
    if slot.parent_slot_id is None:
        if computational_parent is not None:
            raise ValueError("root slot cannot retain a computational parent")
        if result.parent_attempt_sha256 is not None or result.parent_result_sha256 is not None:
            raise ValueError("root slot cannot bind a parent result")
    else:
        if computational_parent is None:
            raise ValueError("child slot requires its exact computational parent")
        if type(computational_parent) is not ValidationSlotComputationResultV2:
            raise TypeError("child slot computational parent must be the exact result type")
        if computational_parent.slot_id != slot.parent_slot_id:
            raise ValueError("child slot retained the wrong computational parent")
        if (
            result.parent_attempt_sha256 != computational_parent.attempt_sha256
            or result.parent_result_sha256 != computational_parent.result_sha256
        ):
            raise ValueError("child slot parent attempt/result binding differs")
    return slot


def _result_identity_payload(
    result: ValidationSlotComputationResultV2 | Mapping[str, object],
) -> dict[str, object]:
    get = result.__getitem__ if isinstance(result, Mapping) else lambda name: getattr(result, name)
    slot_id = get("slot_id")
    parent_slot_id = next(
        (slot.parent_slot_id for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == slot_id),
        None,
    )
    return {
        "schema_version": get("schema_version"),
        "programme_id": get("programme_id"),
        "slot_id": slot_id,
        "attempt_number": get("attempt_number"),
        "runner_kind": get("runner_kind"),
        "runner_version": get("runner_version"),
        "attempt_sha256": get("attempt_sha256"),
        "input_sha256": get("input_sha256"),
        "evidence_sha256": get("evidence_sha256"),
        "parent_slot_id": parent_slot_id,
        "parent_attempt_sha256": get("parent_attempt_sha256"),
        "parent_result_sha256": get("parent_result_sha256"),
        "execution_status": get("execution_status"),
        "decision": get("decision"),
        "computation_completed": get("computation_completed"),
        "reason": get("reason"),
        "p_value": get("p_value"),
        "metrics": _bounded_result_metrics_v2(get("metrics")),
    }


def validation_slot_result_sha256_v2(
    result: ValidationSlotComputationResultV2 | Mapping[str, object],
) -> str:
    """Return the complete canonical identity for a V2 slot result payload."""

    return hash_json(
        "phase5-validation-slot-computation-result-v2",
        _result_identity_payload(result),
    )


def _issue_validation_slot_result_v2(
    *,
    evidence_verifier: Callable[[], object],
    retained_evidence: object,
    computational_parent: ValidationSlotComputationResultV2 | None = None,
    evidence_authority: object | None = None,
    evidence_authority_verifier: Callable[[], object] | None = None,
    **fields: object,
) -> ValidationSlotComputationResultV2:
    if (evidence_authority is None) != (evidence_authority_verifier is None):
        raise ValueError("result evidence authority and verifier must be supplied together")
    fields["metrics"] = _bounded_result_metrics_v2(fields.get("metrics"))
    fields.pop("result_sha256", None)
    fields["result_sha256"] = validation_slot_result_sha256_v2(fields)
    token = object()
    _RESULT_ISSUANCE[id(token)] = token
    try:
        result = ValidationSlotComputationResultV2(
            **fields,  # type: ignore[arg-type]
            _factory_token=token,
        )
    finally:
        _RESULT_ISSUANCE.pop(id(token), None)
    _validate_individual_result_contract_v2(
        result,
        computational_parent=computational_parent,
    )
    if computational_parent is not None:
        _verify_registered_result_identity_v2(computational_parent)
    identifier = id(result)

    def cleanup(reference: weakref.ReferenceType[ValidationSlotComputationResultV2]) -> None:
        current = _VERIFIED_RESULTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_RESULTS.pop(identifier, None)

    registered_payload_dict = result.to_dict()
    registered_payload_dict["metrics"] = MappingProxyType(dict(result.metrics))
    registered_payload = MappingProxyType(registered_payload_dict)
    _VERIFIED_RESULTS[identifier] = (
        weakref.ref(result, cleanup),
        _result_snapshot(result),
        evidence_verifier,
        retained_evidence,
        computational_parent,
        evidence_authority,
        evidence_authority_verifier,
        registered_payload,
    )
    return result


def _verify_registered_result_identity_v2(
    result: ValidationSlotComputationResultV2,
) -> tuple[
    weakref.ReferenceType[ValidationSlotComputationResultV2],
    tuple[object, ...],
    Callable[[], object],
    object,
    ValidationSlotComputationResultV2 | None,
    object | None,
    Callable[[], object] | None,
    Mapping[str, object],
]:
    if type(result) is not ValidationSlotComputationResultV2:
        raise TypeError("slot computation result must be the exact factory-issued type")
    registered = _VERIFIED_RESULTS.get(id(result))
    if registered is None or registered[0]() is not result:
        raise ValueError("slot computation result is not the registered original")
    _validate_individual_result_contract_v2(result, computational_parent=registered[4])
    if _result_snapshot(result) != registered[1]:
        raise ValueError("slot computation result differs from its immutable computation")
    expected_attempt = hash_json(
        "phase5-validation-slot-attempt-v2",
        {
            "slot_id": result.slot_id,
            "input_sha256": result.input_sha256,
            "attempt_number": result.attempt_number,
        },
    )
    if result.attempt_sha256 != expected_attempt:
        raise ValueError("slot computation result attempt identity differs")
    expected_result = validation_slot_result_sha256_v2(result)
    if result.result_sha256 != expected_result:
        raise ValueError("slot computation result identity differs")
    if registered[4] is not None:
        _verify_registered_result_identity_v2(registered[4])
    return registered


def _verify_validation_slot_computation_result_v2(
    result: ValidationSlotComputationResultV2,
    *,
    verified_results: set[int] | None = None,
    verified_authorities: set[int] | None = None,
) -> ValidationSlotComputationResultV2:
    registered = _verify_registered_result_identity_v2(result)
    verified = verified_results if verified_results is not None else set()
    authorities = verified_authorities if verified_authorities is not None else set()
    if id(result) in verified:
        return result
    authority = registered[5]
    authority_verifier = registered[6]
    if authority is not None and id(authority) not in authorities:
        if authority_verifier is None or authority_verifier() is not authority:
            raise ValueError("slot computation evidence authority verification differs")
        authorities.add(id(authority))
    if registered[4] is not None:
        _verify_validation_slot_computation_result_v2(
            registered[4],
            verified_results=verified,
            verified_authorities=authorities,
        )
    registered[2]()
    verified.add(id(result))
    return result


def verify_original_validation_slot_result_v2(
    result: ValidationSlotComputationResultV2,
) -> ValidationSlotComputationResultV2:
    """Public exact-original verifier for a factory-issued computation result."""

    return _verify_validation_slot_computation_result_v2(result)


def verified_validation_slot_result_payload_v2(
    result: ValidationSlotComputationResultV2,
) -> Mapping[str, object]:
    """Return the immutable issuer snapshot only after full computation replay."""

    _verify_validation_slot_computation_result_v2(result)
    registered = _VERIFIED_RESULTS[id(result)]
    return registered[7]


def verified_validation_slot_result_payloads_v2(
    results: Sequence[ValidationSlotComputationResultV2],
) -> tuple[Mapping[str, object], ...]:
    """Return issuer snapshots after one bounded shared-authority verification pass."""

    if type(results) not in {tuple, list}:
        raise TypeError("slot result payload verification requires an exact tuple or list")
    if not 1 <= len(results) <= len(VALIDATION_SLOT_ROSTER):
        raise ValueError("slot result payload verification cardinality is outside the roster")
    verified_results: set[int] = set()
    verified_authorities: set[int] = set()
    payloads: list[Mapping[str, object]] = []
    for result in results:
        _verify_validation_slot_computation_result_v2(
            result,
            verified_results=verified_results,
            verified_authorities=verified_authorities,
        )
        payloads.append(_VERIFIED_RESULTS[id(result)][7])
    return tuple(payloads)


@dataclass(frozen=True, slots=True)
class _SlotResultMaterialV2:
    input_sha256: str
    attempt_sha256: str
    evidence_sha256: str
    execution_status: str
    decision: str
    computation_completed: bool
    reason: str
    p_value: float | None
    metrics: tuple[tuple[str, float | int | str], ...]

    def result_fields(self) -> dict[str, object]:
        return {
            "input_sha256": self.input_sha256,
            "attempt_sha256": self.attempt_sha256,
            "evidence_sha256": self.evidence_sha256,
            "execution_status": self.execution_status,
            "decision": self.decision,
            "computation_completed": self.computation_completed,
            "reason": self.reason,
            "p_value": self.p_value,
            "metrics": dict(self.metrics),
        }


def _compute_slot_result_material_v2(
    *,
    inputs: SlotRunnerInputsV2,
    budget: ValidationWorkBudget,
    slot: ValidationSlot,
    attempt_number: int,
    parent: ValidationSlotComputationResultV2 | None,
) -> _SlotResultMaterialV2:
    rows = inputs.outcome_reader.read_slot(
        slot,
        programme_id=inputs.programme_id,
        split_sha256=inputs.split_sha256,
        cost_authority_sha256=inputs.cost_authority_sha256,
    )
    input_sha = hash_json(
        "phase5-validation-slot-input-v2",
        {
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
        },
    )
    attempt_sha = hash_json(
        "phase5-validation-slot-attempt-v2",
        {
            "slot_id": slot.slot_id,
            "input_sha256": input_sha,
            "attempt_number": attempt_number,
        },
    )
    if not inputs.source_available:
        missing_reason = "verified development source is unavailable"
    elif slot.family in _PRECISION_REQUIRED_FAMILIES and not inputs.precision_available:
        missing_reason = "required historical precision is unavailable"
    elif slot.parent_slot_id is not None and (
        parent is None or parent.execution_status != "completed"
    ):
        missing_reason = "required parent slot did not complete"
    else:
        missing_reason = (
            "authenticated exact quantitative primitive inputs are unavailable; "
            "fixture outcomes and caller cost-completeness booleans cannot authorize computation"
        )
    evidence_sha = hash_json(
        "phase5-validation-slot-precondition-v2",
        {"slot_id": slot.slot_id, "reason": missing_reason, "input_sha256": input_sha},
    )
    return _SlotResultMaterialV2(
        input_sha256=input_sha,
        attempt_sha256=attempt_sha,
        evidence_sha256=evidence_sha,
        execution_status="failed",
        decision="not_evaluated",
        computation_completed=False,
        reason=missing_reason,
        p_value=None,
        metrics=(),
    )


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
    inputs.outcome_reader.verify_original()
    original_outcome_reader = inputs.outcome_reader
    input_snapshot = (
        inputs.programme_id,
        inputs.split_sha256,
        inputs.cost_authority_sha256,
        inputs.promotion_grade_costs_complete,
        inputs.precision_available,
        inputs.runner_version,
        inputs.source_available,
    )
    budget_snapshot = budget.to_dict()
    demand_snapshot = {name: getattr(demand, name) for name in demand.__dataclass_fields__}
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
        material = _compute_slot_result_material_v2(
            inputs=inputs,
            budget=budget,
            slot=slot,
            attempt_number=attempt_number,
            parent=parent,
        )

        def verify_computation_evidence(
            computational_parent: ValidationSlotComputationResultV2 | None = parent,
            expected_material: _SlotResultMaterialV2 = material,
            computation_slot: ValidationSlot = slot,
            computation_attempt_number: int = attempt_number,
        ) -> object:
            if type(inputs) is not SlotRunnerInputsV2:
                raise TypeError("slot computation input parent is not exact")
            if (
                inputs.outcome_reader is not original_outcome_reader
                or (
                    inputs.programme_id,
                    inputs.split_sha256,
                    inputs.cost_authority_sha256,
                    inputs.promotion_grade_costs_complete,
                    inputs.precision_available,
                    inputs.runner_version,
                    inputs.source_available,
                )
                != input_snapshot
            ):
                raise ValueError("slot computation input parent changed")
            if budget.to_dict() != budget_snapshot:
                raise ValueError("slot computation budget admission parent changed")
            current_demand = {name: getattr(demand, name) for name in demand.__dataclass_fields__}
            if current_demand != demand_snapshot:
                raise ValueError("slot computation demand admission parent changed")
            budget.preflight(demand)
            replayed = _compute_slot_result_material_v2(
                inputs=inputs,
                budget=budget,
                slot=computation_slot,
                attempt_number=computation_attempt_number,
                parent=computational_parent,
            )
            if replayed != expected_material:
                raise ValueError("slot computation replay differs from its issued result")
            return inputs

        result = _issue_validation_slot_result_v2(
            evidence_verifier=verify_computation_evidence,
            retained_evidence=(inputs, budget, demand, parent, material),
            computational_parent=parent,
            schema_version="validation-slot-computation-result-v2",
            programme_id=inputs.programme_id,
            slot_id=slot.slot_id,
            attempt_number=attempt_number,
            runner_kind=slot.kind.value,
            runner_version=inputs.runner_version,
            parent_attempt_sha256=parent.attempt_sha256 if parent else None,
            parent_result_sha256=parent.result_sha256 if parent else None,
            **material.result_fields(),  # type: ignore[arg-type]
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

    if type(results) not in {tuple, list}:
        raise TypeError("slot results must be an exact tuple or list")
    if len(results) != len(VALIDATION_SLOT_ROSTER):
        raise ValueError("slot results must cover the exact 1,104-slot roster")
    frozen_results = tuple(results)
    if any(type(item) is not ValidationSlotComputationResultV2 for item in frozen_results):
        raise TypeError("slot results must contain exact registered result types")
    if tuple(item.slot_id for item in frozen_results) != tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    ):
        raise ValueError("slot results are missing, extra, or reordered")
    attempts: set[str] = set()
    inputs: set[str] = set()
    result_ids: set[str] = set()
    by_slot: dict[str, ValidationSlotComputationResultV2] = {}
    verified_results: set[int] = set()
    verified_authorities: set[int] = set()
    for slot, result in zip(VALIDATION_SLOT_ROSTER, frozen_results, strict=True):
        _verify_validation_slot_computation_result_v2(
            result,
            verified_results=verified_results,
            verified_authorities=verified_authorities,
        )
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
class FamilyHolmResultV2:
    family: str
    slot_id: str
    raw_p_value: float | None
    effective_p_value: float
    adjusted_p_value: float
    rejected: bool
    evaluable: bool
    alpha: float = _FAMILY_ALPHA


def apply_family_holm_v2(
    results: Sequence[ValidationSlotComputationResultV2],
) -> tuple[FamilyHolmResultV2, ...]:
    """Apply the frozen 0.01 Holm correction within each primary family."""

    if type(results) not in {tuple, list}:
        raise TypeError("family Holm results must be an exact tuple or list")
    if len(results) != len(VALIDATION_SLOT_ROSTER):
        raise ValueError("family Holm requires the complete roster and exactly 64 primaries")
    frozen_results = tuple(results)
    if any(type(item) is not ValidationSlotComputationResultV2 for item in frozen_results):
        raise TypeError("family Holm requires exact registered result types")
    expected_all = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    observed = tuple(item.slot_id for item in frozen_results)
    if observed != expected_all:
        raise ValueError("family Holm requires the complete roster and exactly 64 primaries")
    verify_slot_results_v2(frozen_results, runner_version=frozen_results[0].runner_version)
    return _apply_family_holm_verified_v2(frozen_results)


def _apply_family_holm_verified_v2(
    results: Sequence[ValidationSlotComputationResultV2],
) -> tuple[FamilyHolmResultV2, ...]:
    primary_slots = tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.kind is ValidationSlotKind.CORE and slot.primary
    )
    if len(primary_slots) != _PRIMARY_COUNT:
        raise RuntimeError("frozen roster does not contain exactly 64 primaries")
    by_slot = {item.slot_id: item for item in results}
    corrected: list[FamilyHolmResultV2] = []
    for family in ("A", "B", "G", "E", "D"):
        family_slots = tuple(slot for slot in primary_slots if slot.family == family)
        evaluations = tuple(
            EvaluationStatistic(
                slot_id=slot.slot_id,
                family=family,
                p_value=by_slot[slot.slot_id].p_value,
                status=ExecutionStatus(by_slot[slot.slot_id].execution_status),
                decision=ScientificDecision(by_slot[slot.slot_id].decision),
            )
            for slot in family_slots
        )
        adjusted = holm_family_correction(evaluations, family=family)
        evaluable = {item.slot_id: item.evaluable for item in evaluations}
        corrected.extend(
            FamilyHolmResultV2(
                family=family,
                slot_id=item.slot_id,
                raw_p_value=item.raw_p_value,
                effective_p_value=item.effective_p_value,
                adjusted_p_value=item.adjusted_p_value,
                rejected=item.rejected,
                evaluable=evaluable[item.slot_id],
            )
            for item in adjusted
        )
    return tuple(corrected)


_PROGRAMME_RUN_ISSUANCE: dict[
    int,
    tuple[
        object,
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[FamilyHolmResultV2, ...],
    ],
] = {}
_VERIFIED_PROGRAMME_RUNS: dict[
    int,
    tuple[
        weakref.ReferenceType[ValidationProgrammeRunV2],
        tuple[object, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[FamilyHolmResultV2, ...],
    ],
] = {}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationProgrammeRunV2:
    results: tuple[ValidationSlotComputationResultV2, ...]
    holm: tuple[FamilyHolmResultV2, ...]
    execution_scope: str
    planned_slot_count: int
    executed_slot_count: int
    roster_complete: bool
    scientific_terminal: bool
    execution_status: str
    decision: str
    terminal_reason: str
    final_holdout_access_count: int
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        issuance = _PROGRAMME_RUN_ISSUANCE.pop(id(_factory_token), None)
        if (
            issuance is None
            or issuance[0] is not _factory_token
            or issuance[1] is not self.results
            or issuance[2] is not self.holm
        ):
            raise TypeError("ValidationProgrammeRunV2 requires its computation factory")
        if type(self.results) is not tuple or any(
            type(item) is not ValidationSlotComputationResultV2 for item in self.results
        ):
            raise TypeError("validation programme results must be exact registered results")
        if type(self.holm) is not tuple or any(
            type(item) is not FamilyHolmResultV2 for item in self.holm
        ):
            raise TypeError("validation programme Holm evidence must use the exact result type")
        if self.execution_scope != "development-only-full-roster":
            raise ValueError("validation programme execution scope is unsupported")
        expected_slots = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
        expected_primaries = tuple(
            slot.slot_id
            for slot in VALIDATION_SLOT_ROSTER
            if slot.kind is ValidationSlotKind.CORE and slot.primary
        )
        if (
            self.planned_slot_count != len(VALIDATION_SLOT_ROSTER)
            or self.executed_slot_count != len(VALIDATION_SLOT_ROSTER)
            or tuple(item.slot_id for item in self.results) != expected_slots
            or tuple(item.slot_id for item in self.holm) != expected_primaries
            or not self.roster_complete
            or not self.scientific_terminal
        ):
            raise ValueError("development validation run must cover the exact terminal roster")
        if (
            self.execution_status != "failed"
            or self.decision != "not_evaluated"
            or not self.terminal_reason
            or type(self.final_holdout_access_count) is not int
            or self.final_holdout_access_count != 0
        ):
            raise ValueError("current development run must truthfully retain its failed gate state")

    def verify_original(self) -> ValidationProgrammeRunV2:
        """Revalidate this exact factory-issued programme and its computation evidence."""

        return verify_original_validation_programme_run_v2(self)


def _validation_programme_run_snapshot_v2(
    run: ValidationProgrammeRunV2,
) -> tuple[object, ...]:
    return (
        run.execution_scope,
        run.planned_slot_count,
        run.executed_slot_count,
        run.roster_complete,
        run.scientific_terminal,
        run.execution_status,
        run.decision,
        run.terminal_reason,
        run.final_holdout_access_count,
        tuple(id(result) for result in run.results),
        tuple(
            (
                item.family,
                item.slot_id,
                item.raw_p_value,
                item.effective_p_value,
                item.adjusted_p_value,
                item.rejected,
                item.evaluable,
                item.alpha,
            )
            for item in run.holm
        ),
    )


def verify_original_validation_programme_run_v2(
    run: ValidationProgrammeRunV2,
) -> ValidationProgrammeRunV2:
    """Reject copied, replaced, mutated, or computationally stale programme runs."""

    if type(run) is not ValidationProgrammeRunV2:
        raise TypeError("validation programme run must be the exact factory-issued type")
    registered = _VERIFIED_PROGRAMME_RUNS.get(id(run))
    if registered is None or registered[0]() is not run:
        raise ValueError("validation programme run is not the registered original")
    if run.results is not registered[2] or run.holm is not registered[3]:
        raise ValueError("validation programme run replaced its registered evidence")
    if _validation_programme_run_snapshot_v2(run) != registered[1]:
        raise ValueError("validation programme run differs from its immutable computation")
    verify_slot_results_v2(run.results, runner_version=run.results[0].runner_version)
    if run.holm != _apply_family_holm_verified_v2(run.results):
        raise ValueError("validation programme Holm evidence differs from exact recomputation")
    return run


def _issue_validation_programme_run_v2(
    *,
    results: tuple[ValidationSlotComputationResultV2, ...],
    **fields: object,
) -> ValidationProgrammeRunV2:
    if type(results) is not tuple:
        raise TypeError("validation programme results must be an exact tuple")
    verify_slot_results_v2(results, runner_version=results[0].runner_version)
    holm = _apply_family_holm_verified_v2(results)
    token = object()
    _PROGRAMME_RUN_ISSUANCE[id(token)] = (token, results, holm)
    try:
        run = ValidationProgrammeRunV2(
            results=results,
            holm=holm,
            **fields,  # type: ignore[arg-type]
            _factory_token=token,
        )
    finally:
        _PROGRAMME_RUN_ISSUANCE.pop(id(token), None)
    identifier = id(run)

    def cleanup(reference: weakref.ReferenceType[ValidationProgrammeRunV2]) -> None:
        current = _VERIFIED_PROGRAMME_RUNS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_PROGRAMME_RUNS.pop(identifier, None)

    reference = weakref.ref(run, cleanup)
    _VERIFIED_PROGRAMME_RUNS[identifier] = (
        reference,
        _validation_programme_run_snapshot_v2(run),
        results,
        holm,
    )
    return run


def _compute_real_vs0001_material_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    runner_version: str,
    reader_already_verified: bool = False,
) -> _SlotResultMaterialV2:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == "VS-0001")
    if reader_already_verified:
        outcomes = reader._outcomes_by_slot.get(slot.slot_id)
        if outcomes is None or len(outcomes) != 1:
            raise ValueError("verified VS-0001 outcome authority differs")
        outcome = outcomes[0]
    else:
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
    return _SlotResultMaterialV2(
        input_sha256=input_sha,
        attempt_sha256=attempt_sha,
        evidence_sha256=evidence_sha,
        execution_status="completed",
        decision="inconclusive",
        computation_completed=True,
        reason=(
            "promotion-grade cost evidence is incomplete: "
            + ",".join(outcome.incomplete_cost_dimensions)
        ),
        p_value=None,
        metrics=tuple(sorted(metrics.items())),
    )


def _real_vs0001_result_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    runner_version: str,
) -> ValidationSlotComputationResultV2:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.slot_id == "VS-0001")
    material = _compute_real_vs0001_material_v2(
        config=config,
        reader=reader,
        runner_version=runner_version,
    )
    config_snapshot = hash_json(
        "phase5-validation-programme-config-result-parent-v2",
        config.to_config_dict(),
    )

    def verify_computation_evidence() -> object:
        if type(config) is not ValidationProgrammeConfigV2:
            raise TypeError("VS-0001 programme config parent is not exact")
        if (
            hash_json(
                "phase5-validation-programme-config-result-parent-v2",
                config.to_config_dict(),
            )
            != config_snapshot
        ):
            raise ValueError("VS-0001 programme config parent changed")
        replayed = _compute_real_vs0001_material_v2(
            config=config,
            reader=reader,
            runner_version=runner_version,
            reader_already_verified=True,
        )
        if replayed != material:
            raise ValueError("VS-0001 computation replay differs from its issued result")
        return reader

    return _issue_validation_slot_result_v2(
        evidence_verifier=verify_computation_evidence,
        retained_evidence=(config, reader, material),
        computational_parent=None,
        evidence_authority=reader,
        evidence_authority_verifier=reader.verify_original,
        schema_version="validation-slot-computation-result-v2",
        programme_id=config.programme_id,
        slot_id=slot.slot_id,
        attempt_number=1,
        runner_kind=slot.kind.value,
        runner_version=runner_version,
        parent_attempt_sha256=None,
        parent_result_sha256=None,
        **material.result_fields(),  # type: ignore[arg-type]
    )


def _compute_unavailable_real_slot_material_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    slot: ValidationSlot,
    parent: ValidationSlotComputationResultV2 | None,
    runner_version: str,
) -> _SlotResultMaterialV2:
    reason = (
        "required parent slot did not complete"
        if slot.parent_slot_id is not None
        and (parent is None or parent.execution_status != "completed")
        else (
            "authenticated publication-bound detector outcomes and exact quantitative "
            "primitive inputs are unavailable for this frozen slot"
        )
    )
    input_sha = hash_json(
        "phase5-validation-slot-input-v2",
        {
            "programme_id": config.programme_id,
            "slot": slot.to_dict(),
            "attempt_number": 1,
            "publication_outcome_set_sha256": reader.outcome_set_sha256,
            "runner_version": runner_version,
            "parent_attempt_sha256": parent.attempt_sha256 if parent else None,
            "parent_result_sha256": parent.result_sha256 if parent else None,
            "prerequisite_status": "unavailable",
        },
    )
    return _SlotResultMaterialV2(
        input_sha256=input_sha,
        attempt_sha256=hash_json(
            "phase5-validation-slot-attempt-v2",
            {"slot_id": slot.slot_id, "input_sha256": input_sha, "attempt_number": 1},
        ),
        evidence_sha256=hash_json(
            "phase5-validation-slot-precondition-v2",
            {"slot_id": slot.slot_id, "reason": reason, "input_sha256": input_sha},
        ),
        execution_status="failed",
        decision="not_evaluated",
        computation_completed=False,
        reason=reason,
        p_value=None,
        metrics=(),
    )


def _unavailable_real_slot_result_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    slot: ValidationSlot,
    parent: ValidationSlotComputationResultV2 | None,
    runner_version: str,
) -> ValidationSlotComputationResultV2:
    material = _compute_unavailable_real_slot_material_v2(
        config=config,
        reader=reader,
        slot=slot,
        parent=parent,
        runner_version=runner_version,
    )
    config_snapshot = hash_json(
        "phase5-validation-programme-config-result-parent-v2",
        config.to_config_dict(),
    )
    reader_snapshot = _publication_outcome_reader_snapshot(reader)

    def verify_computation_evidence() -> object:
        if type(config) is not ValidationProgrammeConfigV2:
            raise TypeError("slot programme config parent is not exact")
        if (
            hash_json(
                "phase5-validation-programme-config-result-parent-v2",
                config.to_config_dict(),
            )
            != config_snapshot
        ):
            raise ValueError("slot programme config parent changed")
        registration = _VERIFIED_PUBLICATION_OUTCOME_READERS.get(id(reader))
        if (
            registration is None
            or registration.reader() is not reader
            or _publication_outcome_reader_snapshot(reader) != reader_snapshot
        ):
            raise ValueError("slot publication outcome authority parent changed")
        replayed = _compute_unavailable_real_slot_material_v2(
            config=config,
            reader=reader,
            slot=slot,
            parent=parent,
            runner_version=runner_version,
        )
        if replayed != material:
            raise ValueError("unavailable slot computation replay differs from its issued result")
        return reader

    return _issue_validation_slot_result_v2(
        evidence_verifier=verify_computation_evidence,
        retained_evidence=(config, reader, slot, parent, material),
        computational_parent=parent,
        evidence_authority=reader,
        evidence_authority_verifier=reader.verify_original,
        schema_version="validation-slot-computation-result-v2",
        programme_id=config.programme_id,
        slot_id=slot.slot_id,
        attempt_number=1,
        runner_kind=slot.kind.value,
        runner_version=runner_version,
        parent_attempt_sha256=parent.attempt_sha256 if parent else None,
        parent_result_sha256=parent.result_sha256 if parent else None,
        **material.result_fields(),
    )


def _full_development_roster_results_v2(
    *,
    config: ValidationProgrammeConfigV2,
    reader: VerifiedPublicationOutcomeReaderV2,
    runner_version: str,
) -> tuple[ValidationSlotComputationResultV2, ...]:
    first = _real_vs0001_result_v2(
        config=config,
        reader=reader,
        runner_version=runner_version,
    )
    by_slot = {first.slot_id: first}
    results = [first]
    for slot in VALIDATION_SLOT_ROSTER[1:]:
        parent = by_slot.get(slot.parent_slot_id) if slot.parent_slot_id else None
        result = _unavailable_real_slot_result_v2(
            config=config,
            reader=reader,
            slot=slot,
            parent=parent,
            runner_version=runner_version,
        )
        by_slot[slot.slot_id] = result
        results.append(result)
    frozen = tuple(results)
    return frozen


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
    results = _full_development_roster_results_v2(
        config=config,
        reader=outcome_reader,
        runner_version=runner_version,
    )
    return _issue_validation_programme_run_v2(
        results=results,
        execution_scope="development-only-full-roster",
        planned_slot_count=len(VALIDATION_SLOT_ROSTER),
        executed_slot_count=len(results),
        roster_complete=True,
        scientific_terminal=True,
        execution_status="failed",
        decision="not_evaluated",
        terminal_reason=(
            "authenticated exact quantitative prerequisites are unavailable for 1,103 "
            "frozen slots; the real VS-0001 cost evidence remains incomplete"
        ),
        final_holdout_access_count=0,
    )


__all__ = [
    "AttachedDevelopmentOutcomeV2",
    "FamilyHolmResultV2",
    "SlotRunnerInputsV2",
    "ValidationProgrammeRunV2",
    "ValidationSlotComputationResultV2",
    "ValidationV2SourceBundle",
    "VerifiedPublicationOutcomeReaderV2",
    "apply_family_holm_v2",
    "development_access_ledger_identity_v2",
    "run_slot_roster_v2",
    "run_validation_programme_v2",
    "validation_slot_result_sha256_v2",
    "verify_original_validation_slot_result_v2",
    "verify_original_validation_programme_run_v2",
    "verified_validation_slot_result_payload_v2",
    "verified_validation_slot_result_payloads_v2",
    "verify_slot_results_v2",
]
