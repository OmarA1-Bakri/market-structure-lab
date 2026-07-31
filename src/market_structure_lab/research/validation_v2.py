"""Development-only Phase 5 V2 evidence derivation and slot execution.

The V2 public boundary accepts only publisher-issued publications.  Raw
statistical summaries, selector scores, terminal decisions, and eligibility
flags are deliberately absent: they are derived after the split and every
result is bound to one frozen roster slot.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
from math import isfinite
from types import MappingProxyType
import re
import weakref

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationV2,
    verify_validation_aggregate_publication_v2,
)
from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionRequirementStatusV2,
    ValidationPrecisionAuthorityV2,
    precision_requirement_status_v2,
    verify_validation_precision_authority_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceAvailabilityV2,
    ScopedSourceStatusV2,
    ValidationSourcePublicationV2,
    verified_scoped_source_availability_bytes_v2,
    verify_validation_source_publication_v2,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2_costs import (
    VerifiedCostAuthorityV2,
    verified_cost_authority_bytes_v2,
)
from market_structure_lab.research.validation_v2_models import (
    SourceCoveragePublicationV2,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
    verified_source_coverage_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
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

    def __post_init__(self) -> None:
        self.revalidate()

    def revalidate(self) -> None:
        """Reopen all registered original bytes and their parent chains."""

        verified_source_coverage_bytes(self.coverage)
        verify_development_read_boundary_v2(self.boundary, self.coverage, self.split)
        verified_scoped_source_availability_bytes_v2(self.availability, boundary=self.boundary)
        verify_validation_source_publication_v2(
            self.source_publication,
            coverage=self.coverage,
            split=self.split,
            boundary=self.boundary,
            availability=self.availability,
        )
        verify_validation_aggregate_publication_v2(
            self.aggregate_publication,
            minute_publication=self.source_publication,
            coverage=self.coverage,
            split=self.split,
            boundary=self.boundary,
            availability=self.availability,
        )
        verify_validation_precision_authority_v2(
            self.precision_authority,
            coverage=self.coverage,
            split=self.split,
            boundary=self.boundary,
            availability=self.availability,
            minute_publication=self.source_publication,
            aggregate_publication=self.aggregate_publication,
        )
        verified_cost_authority_bytes_v2(self.cost_authority)


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


def _issue_publication_outcome_reader_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
) -> VerifiedDevelopmentOutcomeReaderV2:
    """Issue the development reader only after reopening publication parents."""

    sources.revalidate()
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    publication_payload = json.dumps(
        {
            "schema_version": "validation-v2-outcome-fixture-v1",
            "slots": {slot_id: [] for slot_id in slot_ids},
        },
        sort_keys=True,
    ).encode()
    return _issue_fixture_outcome_reader_v2(
        publication_payload,
        programme_id=config.programme_id,
        split_sha256=sources.boundary.boundary_sha256,
        cost_authority_sha256=sources.cost_authority.cost_identity.value.removeprefix("CSTV2-"),
        source_publication_sha256=hashlib.sha256(
            sources.source_publication.canonical_bytes
        ).hexdigest(),
        aggregate_publication_sha256=hashlib.sha256(
            sources.aggregate_publication.canonical_bytes
        ).hexdigest(),
        expected_slot_ids=slot_ids,
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


def run_validation_programme_v2(
    *,
    config: ValidationProgrammeConfigV2,
    sources: ValidationV2SourceBundle,
    budget: ValidationWorkBudget,
    demand: ValidationWorkDemand,
    runner_version: str = _RUNNER_VERSION,
) -> ValidationProgrammeRunV2:
    """Public development-only entry point with split/source verification first."""

    if not isinstance(config, ValidationProgrammeConfigV2):
        raise TypeError("config must be ValidationProgrammeConfigV2")
    if not isinstance(sources, ValidationV2SourceBundle):
        raise TypeError("sources must be ValidationV2SourceBundle")
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
    if config.work_budget_sha256 != budget.sha256:
        raise ValueError("programme work budget identity differs")
    budget.preflight(demand)
    # Stage order is explicit: split/boundary and source parents are reopened
    # before an outcome reader is issued or iterated.
    sources.revalidate()
    precision_ready = (
        precision_requirement_status_v2(sources.precision_authority)
        is PrecisionRequirementStatusV2.READY
    )
    outcome_reader = _issue_publication_outcome_reader_v2(
        config=config,
        sources=sources,
    )
    inputs = SlotRunnerInputsV2(
        programme_id=config.programme_id,
        outcome_reader=outcome_reader,
        split_sha256=sources.boundary.boundary_sha256,
        cost_authority_sha256=sources.cost_authority.cost_identity.value.removeprefix("CSTV2-"),
        promotion_grade_costs_complete=not bool(
            sources.cost_authority.incomplete_promotion_grade_dimensions
        ),
        precision_available=precision_ready,
        runner_version=runner_version,
        source_available=(
            sources.availability.status is ScopedSourceStatusV2.AVAILABLE
            and sources.aggregate_publication.status is ScopedSourceStatusV2.AVAILABLE
        ),
    )
    results = run_slot_roster_v2(inputs, budget=budget, demand=demand)
    return ValidationProgrammeRunV2(results=results, holm=apply_global_holm_v2(results))


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
    "WeeklySlotVectorV2",
    "apply_global_holm_v2",
    "derive_slot_evidence_v2",
    "global_holm_pvalues_v2",
    "run_slot_roster_v2",
    "run_validation_programme_v2",
    "verify_slot_results_v2",
]
