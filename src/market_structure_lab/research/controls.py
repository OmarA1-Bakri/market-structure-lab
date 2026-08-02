"""Outcome-blind Phase 5 validation controls and placebo primitives."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Self, cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)

_SHA256_LENGTH = 64
_CONTROL_SELECTION_SEAL = object()
_SELECTOR_EVIDENCE_SEAL = object()


class ControlKind(StrEnum):
    """Supported outcome-blind validation control families."""

    NAIVE_ZERO = "naive_zero"
    UNCONDITIONAL = "unconditional"
    PERSISTENCE = "persistence"
    PRICE_BASELINE = "price_baseline"
    AUGMENTED_SELECTED = "augmented_selected"
    RATE_MATCHED_PLACEBO = "rate_matched_placebo"
    ATR = "atr"
    DONCHIAN = "donchian"
    FAILED_DONCHIAN = "failed_donchian"
    PRIOR_WEEK_PSEUDO_LEVEL = "prior_week_pseudo_level"
    LABEL_SHUFFLE = "label_shuffle"
    TIME_SHIFT = "time_shift"
    RANDOM_FEATURE = "random_feature"


class ControlSelectionStatus(StrEnum):
    """Operational result of building a control population."""

    COMPLETE = "complete"
    INCONCLUSIVE = "inconclusive"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LegalBoundaryInterval:
    """Frozen legal half-open interval for a non-final programme slice."""

    programme_id: str
    publication_sha256: str
    symbol: str
    timeframe: str
    segment_id: int
    fold_id: str
    component: str
    block_id: str
    interval_start: datetime
    interval_end: datetime
    boundary_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "programme_id",
            "publication_sha256",
            "symbol",
            "timeframe",
            "fold_id",
            "component",
            "block_id",
        ):
            _require_non_empty(getattr(self, name), name)
        _require_sha256(self.publication_sha256, "publication_sha256")
        if (
            isinstance(self.segment_id, bool)
            or not isinstance(self.segment_id, int)
            or self.segment_id < 0
        ):
            raise ValueError("segment_id must be a non-negative integer")
        _require_utc(self.interval_start, "interval_start")
        _require_utc(self.interval_end, "interval_end")
        if self.interval_start >= self.interval_end:
            raise ValueError("legal boundary interval must be non-empty")
        object.__setattr__(
            self,
            "boundary_sha256",
            hash_json("legal-boundary-interval-v1", self.to_dict(include_sha=False)),
        )

    @property
    def key(self) -> tuple[str, str, str, str, int, str, str, str]:
        return (
            self.programme_id,
            self.publication_sha256,
            self.symbol,
            self.timeframe,
            self.segment_id,
            self.fold_id,
            self.component,
            self.block_id,
        )

    @property
    def is_non_final(self) -> bool:
        return not self.component.startswith("final")

    def contains_path(
        self,
        *,
        feature_time: datetime,
        signal_time: datetime,
        entry_time: datetime,
        label_start: datetime,
        label_end: datetime,
    ) -> bool:
        """Return whether every shifted path timestamp lies inside this half-open interval."""

        return (
            self.is_non_final
            and self.interval_start <= feature_time
            and feature_time < signal_time <= entry_time == label_start < label_end
            and label_end <= self.interval_end
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "programme_id": self.programme_id,
            "publication_sha256": self.publication_sha256,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "segment_id": self.segment_id,
            "fold_id": self.fold_id,
            "component": self.component,
            "block_id": self.block_id,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
        }
        if include_sha:
            payload["boundary_sha256"] = self.boundary_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ControlOpportunity:
    """One legal candidate or control opportunity without accessing final rows."""

    row_identity: str
    event_id: str
    candidate_id: str
    family: str
    control_role: str
    symbol: str
    timeframe: str
    fold_id: str
    utc_week_start: datetime
    direction: int
    horizon_hours: int
    segment_id: int
    feature_time: datetime
    signal_time: datetime
    legal_entry_time: datetime
    label_start: datetime
    label_end: datetime
    programme_id: str
    publication_sha256: str
    component: str = "development"
    block_id: str = "block-unknown"
    gross_return: float = 0.0
    cost_return: float = 0.0
    prior_completed_close_return: float | None = None
    lookback_hours: int | None = None
    level_reference: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.row_identity, "row_identity")
        _require_non_empty(self.event_id, "event_id")
        if not self.candidate_id.startswith("HC-"):
            raise ValueError("candidate_id must identify a candidate")
        if self.family not in {"A", "B", "G", "E", "D"}:
            raise ValueError("family is unsupported")
        for name in (
            "control_role",
            "symbol",
            "timeframe",
            "fold_id",
            "programme_id",
            "component",
            "block_id",
        ):
            _require_non_empty(getattr(self, name), name)
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if (
            isinstance(self.horizon_hours, bool)
            or not isinstance(self.horizon_hours, int)
            or self.horizon_hours < 1
        ):
            raise ValueError("horizon_hours must be a positive integer")
        if (
            isinstance(self.segment_id, bool)
            or not isinstance(self.segment_id, int)
            or self.segment_id < 0
        ):
            raise ValueError("segment_id must be a non-negative integer")
        for name in (
            "utc_week_start",
            "feature_time",
            "signal_time",
            "legal_entry_time",
            "label_start",
            "label_end",
        ):
            _require_utc(getattr(self, name), name)
        if self.utc_week_start.weekday() != 0 or self.utc_week_start.hour != 0:
            raise ValueError("utc_week_start must be a UTC week boundary")
        if not (
            self.feature_time
            < self.signal_time
            <= self.legal_entry_time
            == self.label_start
            < self.label_end
        ):
            raise ValueError("opportunity times must be causal half-open ranges")
        if self.label_end - self.label_start != timedelta(hours=self.horizon_hours):
            raise ValueError("label interval must match horizon_hours")
        _require_sha256(self.publication_sha256, "publication_sha256")
        _require_finite(self.gross_return, "gross_return")
        _require_finite(self.cost_return, "cost_return")
        if self.prior_completed_close_return is not None:
            _require_finite(self.prior_completed_close_return, "prior_completed_close_return")
        if self.lookback_hours is not None and (
            isinstance(self.lookback_hours, bool)
            or not isinstance(self.lookback_hours, int)
            or self.lookback_hours < 1
        ):
            raise ValueError("lookback_hours must be a positive integer")

    @property
    def stratum(self) -> tuple[str, str, datetime, int, int, str, str, str, int, str, str]:
        """Exact legal matching stratum for count-matched controls."""

        return (
            self.symbol,
            self.fold_id,
            self.utc_week_start,
            self.direction,
            self.horizon_hours,
            self.timeframe,
            self.programme_id,
            self.publication_sha256,
            self.segment_id,
            self.component,
            self.block_id,
        )

    @property
    def prior_week_key(
        self,
    ) -> tuple[str, str, str, str, int | None, int, int, int, str, str | None]:
        """Exact causal prior-week pseudo-level compatibility key."""

        return (
            self.programme_id,
            self.publication_sha256,
            self.symbol,
            self.timeframe,
            self.lookback_hours,
            self.direction,
            self.segment_id,
            self.horizon_hours,
            self.fold_id,
            self.level_reference,
        )

    @property
    def is_non_final(self) -> bool:
        return not self.component.startswith("final")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "row_identity": self.row_identity,
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "family": self.family,
            "control_role": self.control_role,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "fold_id": self.fold_id,
            "utc_week_start": self.utc_week_start,
            "direction": self.direction,
            "horizon_hours": self.horizon_hours,
            "segment_id": self.segment_id,
            "feature_time": self.feature_time,
            "signal_time": self.signal_time,
            "legal_entry_time": self.legal_entry_time,
            "label_start": self.label_start,
            "label_end": self.label_end,
            "programme_id": self.programme_id,
            "publication_sha256": self.publication_sha256,
            "component": self.component,
            "block_id": self.block_id,
            "gross_return": self.gross_return,
            "cost_return": self.cost_return,
        }
        if self.prior_completed_close_return is not None:
            payload["prior_completed_close_return"] = self.prior_completed_close_return
        if self.lookback_hours is not None:
            payload["lookback_hours"] = self.lookback_hours
        if self.level_reference is not None:
            payload["level_reference"] = self.level_reference
        return payload


@dataclass(frozen=True, slots=True)
class ControlRecord:
    """One candidate-to-control binding with canonical content identity."""

    candidate_row_identity: str
    control_row_identity: str
    event_id: str
    kind: ControlKind
    control_family: str
    control_direction: int
    control_return: float
    control_cost: float
    source_row_identity: str | None = None
    selection_rank: int = 0
    control_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.candidate_row_identity, "candidate_row_identity")
        _require_non_empty(self.control_row_identity, "control_row_identity")
        _require_non_empty(self.event_id, "event_id")
        if not isinstance(self.kind, ControlKind):
            raise TypeError("kind must be a ControlKind")
        _require_non_empty(self.control_family, "control_family")
        if self.control_direction not in (-1, 1):
            raise ValueError("control_direction must be -1 or +1")
        _require_finite(self.control_return, "control_return")
        _require_finite(self.control_cost, "control_cost")
        if self.source_row_identity is not None:
            _require_non_empty(self.source_row_identity, "source_row_identity")
        if (
            isinstance(self.selection_rank, bool)
            or not isinstance(self.selection_rank, int)
            or self.selection_rank < 0
        ):
            raise ValueError("selection_rank must be a non-negative integer")
        object.__setattr__(
            self,
            "control_record_id",
            f"CR-{hash_json('control-record-v1', self.to_dict(include_id=False))}",
        )

    def to_dict(self, *, include_id: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "candidate_row_identity": self.candidate_row_identity,
            "control_row_identity": self.control_row_identity,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "control_family": self.control_family,
            "control_direction": self.control_direction,
            "control_return": self.control_return,
            "control_cost": self.control_cost,
            "selection_rank": self.selection_rank,
        }
        if self.source_row_identity is not None:
            payload["source_row_identity"] = self.source_row_identity
        if include_id:
            payload["control_record_id"] = self.control_record_id
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Self:
        expected = raw.get("control_record_id")
        payload = {key: value for key, value in raw.items() if key != "control_record_id"}
        record = cls(
            candidate_row_identity=cast(str, payload["candidate_row_identity"]),
            control_row_identity=cast(str, payload["control_row_identity"]),
            event_id=cast(str, payload["event_id"]),
            kind=ControlKind(cast(str, payload["kind"])),
            control_family=cast(str, payload["control_family"]),
            control_direction=cast(int, payload["control_direction"]),
            control_return=cast(float, payload["control_return"]),
            control_cost=cast(float, payload["control_cost"]),
            source_row_identity=cast(str | None, payload.get("source_row_identity")),
            selection_rank=cast(int, payload.get("selection_rank", 0)),
        )
        if expected is not None and expected != record.control_record_id:
            raise ValueError("control_record_id sha256 differs from canonical content")
        return record


@dataclass(frozen=True, slots=True)
class ControlSelection:
    """Canonical receipt for one bounded control/placebo selection."""

    kind: ControlKind
    status: ControlSelectionStatus
    records: tuple[ControlRecord, ...]
    reason: str
    shortage_count: int = 0
    selection_sha256: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _CONTROL_SELECTION_SEAL:
            raise TypeError("ControlSelection requires the control builder seal")
        if not isinstance(self.kind, ControlKind):
            raise TypeError("kind must be a ControlKind")
        if not isinstance(self.status, ControlSelectionStatus):
            raise TypeError("status must be a ControlSelectionStatus")
        if not isinstance(self.records, tuple):
            raise TypeError("records must be a tuple")
        _require_non_empty(self.reason, "reason")
        if (
            isinstance(self.shortage_count, bool)
            or not isinstance(self.shortage_count, int)
            or self.shortage_count < 0
        ):
            raise ValueError("shortage_count must be a non-negative integer")
        _reject_duplicate_values(
            (record.control_record_id for record in self.records), "control record"
        )
        object.__setattr__(
            self,
            "selection_sha256",
            hash_json("control-selection-v1", self.to_dict(include_sha=False)),
        )

    @property
    def sha256(self) -> str:
        return self.selection_sha256

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "status": self.status.value,
            "records": [record.to_dict() for record in self.records],
            "reason": self.reason,
            "shortage_count": self.shortage_count,
        }
        if include_sha:
            payload["sha256"] = self.selection_sha256
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Self:
        expected = raw.get("sha256")
        records = tuple(
            ControlRecord.from_dict(cast(dict[str, object], item))
            for item in cast(list[object], raw["records"])
        )
        selection = cls(
            kind=ControlKind(cast(str, raw["kind"])),
            status=ControlSelectionStatus(cast(str, raw["status"])),
            records=records,
            reason=cast(str, raw["reason"]),
            shortage_count=cast(int, raw.get("shortage_count", 0)),
            seal=_CONTROL_SELECTION_SEAL,
        )
        if expected is not None and expected != selection.selection_sha256:
            raise ValueError("selection sha256 differs from canonical content")
        return selection


@dataclass(frozen=True, slots=True)
class SelectorInput:
    """Outcome-free source row identity available to a selector before labels exist."""

    row_identity: str
    source_publication_sha256: str
    feature_time: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.row_identity, "row_identity")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_utc(self.feature_time, "feature_time")

    def to_dict(self) -> dict[str, object]:
        return {
            "row_identity": self.row_identity,
            "source_publication_sha256": self.source_publication_sha256,
            "feature_time": self.feature_time,
        }


@dataclass(frozen=True, slots=True)
class SelectorEvidence:
    """Canonical sealed evidence for an outcome-free row selector."""

    selected_row_identities: tuple[str, ...]
    source_publication_sha256: str
    selector_id: str
    selector_cutoff: datetime
    source_population_sha256: str
    selector_sha256: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _SELECTOR_EVIDENCE_SEAL:
            raise TypeError("SelectorEvidence requires the selector evidence builder seal")
        if not isinstance(self.selected_row_identities, tuple):
            raise TypeError("selected_row_identities must be a tuple")
        _reject_duplicate_values(self.selected_row_identities, "selected row")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_non_empty(self.selector_id, "selector_id")
        _require_utc(self.selector_cutoff, "selector_cutoff")
        _require_sha256(self.source_population_sha256, "source_population_sha256")
        object.__setattr__(
            self,
            "selector_sha256",
            hash_json("selector-evidence-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "selected_row_identities": list(self.selected_row_identities),
            "source_publication_sha256": self.source_publication_sha256,
            "selector_id": self.selector_id,
            "selector_cutoff": self.selector_cutoff,
            "source_population_sha256": self.source_population_sha256,
        }
        if include_sha:
            payload["selector_sha256"] = self.selector_sha256
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Self:
        evidence = cls(
            selected_row_identities=tuple(cast(list[str], raw["selected_row_identities"])),
            source_publication_sha256=cast(str, raw["source_publication_sha256"]),
            selector_id=cast(str, raw["selector_id"]),
            selector_cutoff=cast(datetime, raw["selector_cutoff"]),
            source_population_sha256=cast(str, raw["source_population_sha256"]),
            seal=_SELECTOR_EVIDENCE_SEAL,
        )
        expected = raw.get("selector_sha256")
        if expected is not None and expected != evidence.selector_sha256:
            raise ValueError("selector evidence sha256 differs from canonical content")
        return evidence


@dataclass(frozen=True, slots=True)
class CommonPopulationEvidence:
    """Family B/E common-population baseline, augmented, and placebo evidence."""

    baseline: ControlSelection
    augmented: ControlSelection
    placebo: ControlSelection
    selected_count: int
    complement_count: int
    complement_row_identities: tuple[str, ...]
    selector_evidence_sha256: str
    selector_cutoff: datetime
    evidence_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _require_sha256(self.selector_evidence_sha256, "selector_evidence_sha256")
        _require_utc(self.selector_cutoff, "selector_cutoff")
        _reject_duplicate_values(self.complement_row_identities, "complement row")
        object.__setattr__(
            self,
            "evidence_sha256",
            hash_json("common-population-evidence-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "baseline": self.baseline.to_dict(),
            "augmented": self.augmented.to_dict(),
            "placebo": self.placebo.to_dict(),
            "selected_count": self.selected_count,
            "complement_count": self.complement_count,
            "complement_row_identities": list(self.complement_row_identities),
            "selector_evidence_sha256": self.selector_evidence_sha256,
            "selector_cutoff": self.selector_cutoff,
        }
        if include_sha:
            payload["evidence_sha256"] = self.evidence_sha256
        return payload


@dataclass(frozen=True, slots=True)
class LegalDonorPoolEvidence:
    """Canonical evidence that a label-shuffle donor pool is complete per legal stratum."""

    per_stratum_counts: tuple[tuple[str, int], ...]
    total_count: int
    source_publication_sha256: str
    pool_identity: str
    donor_pool_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.per_stratum_counts, tuple):
            raise TypeError("per_stratum_counts must be a tuple")
        for stratum_digest, count in self.per_stratum_counts:
            _require_sha256(stratum_digest, "stratum digest")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("stratum donor count must be a non-negative integer")
        if (
            isinstance(self.total_count, bool)
            or not isinstance(self.total_count, int)
            or self.total_count < 0
        ):
            raise ValueError("total_count must be a non-negative integer")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_non_empty(self.pool_identity, "pool_identity")
        _require_sha256(self.donor_pool_sha256, "donor_pool_sha256")

    @property
    def evidence_sha256(self) -> str:
        return hash_json("legal-donor-pool-evidence-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "per_stratum_counts": [
                {"stratum_digest": digest, "count": count}
                for digest, count in self.per_stratum_counts
            ],
            "total_count": self.total_count,
            "source_publication_sha256": self.source_publication_sha256,
            "pool_identity": self.pool_identity,
            "donor_pool_sha256": self.donor_pool_sha256,
        }


@dataclass(frozen=True, slots=True)
class ShuffledLabel:
    """A deterministic label-shuffle binding that leaves event identity unchanged."""

    event_id: str
    source_row_identity: str
    label_source_row_identity: str
    gross_return: float
    cost_return: float = 0.0
    shuffle_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.event_id, "event_id")
        _require_non_empty(self.source_row_identity, "source_row_identity")
        _require_non_empty(self.label_source_row_identity, "label_source_row_identity")
        _require_finite(self.gross_return, "gross_return")
        _require_finite(self.cost_return, "cost_return")
        object.__setattr__(
            self,
            "shuffle_sha256",
            hash_json("label-shuffle-control-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": self.event_id,
            "source_row_identity": self.source_row_identity,
            "label_source_row_identity": self.label_source_row_identity,
            "gross_return": self.gross_return,
            "cost_return": self.cost_return,
        }
        if include_sha:
            payload["shuffle_sha256"] = self.shuffle_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ShiftedOpportunity:
    """A +1 UTC-week causal time-shift negative control."""

    source_row_identity: str
    event_id: str
    source_feature_time: datetime
    shifted_feature_time: datetime
    shifted_signal_time: datetime
    shifted_entry_time: datetime
    shifted_label_start: datetime
    shifted_label_end: datetime
    programme_id: str
    publication_sha256: str
    symbol: str
    timeframe: str
    segment_id: int
    fold_id: str
    component: str
    block_id: str
    legal_boundary_sha256: str
    boundary_interval_start: datetime
    boundary_interval_end: datetime
    shift_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_feature_time",
            "shifted_feature_time",
            "shifted_signal_time",
            "shifted_entry_time",
            "shifted_label_start",
            "shifted_label_end",
            "boundary_interval_start",
            "boundary_interval_end",
        ):
            _require_utc(getattr(self, name), name)
        for name in (
            "programme_id",
            "publication_sha256",
            "symbol",
            "timeframe",
            "fold_id",
            "component",
            "block_id",
        ):
            _require_non_empty(getattr(self, name), name)
        _require_sha256(self.publication_sha256, "publication_sha256")
        _require_sha256(self.legal_boundary_sha256, "legal_boundary_sha256")
        if self.boundary_interval_start >= self.boundary_interval_end:
            raise ValueError("boundary interval must be non-empty")
        if (
            isinstance(self.segment_id, bool)
            or not isinstance(self.segment_id, int)
            or self.segment_id < 0
        ):
            raise ValueError("segment_id must be a non-negative integer")
        object.__setattr__(
            self,
            "shift_sha256",
            hash_json("time-shift-control-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_row_identity": self.source_row_identity,
            "event_id": self.event_id,
            "source_feature_time": self.source_feature_time,
            "shifted_feature_time": self.shifted_feature_time,
            "shifted_signal_time": self.shifted_signal_time,
            "shifted_entry_time": self.shifted_entry_time,
            "shifted_label_start": self.shifted_label_start,
            "shifted_label_end": self.shifted_label_end,
            "programme_id": self.programme_id,
            "publication_sha256": self.publication_sha256,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "segment_id": self.segment_id,
            "fold_id": self.fold_id,
            "component": self.component,
            "block_id": self.block_id,
            "legal_boundary_sha256": self.legal_boundary_sha256,
            "boundary_interval_start": self.boundary_interval_start,
            "boundary_interval_end": self.boundary_interval_end,
        }
        if include_sha:
            payload["shift_sha256"] = self.shift_sha256
        return payload


@dataclass(frozen=True, slots=True)
class LabelShuffleSelection:
    """Status receipt for a legal-strata label shuffle."""

    status: ControlSelectionStatus
    records: tuple[ShuffledLabel, ...]
    reason: str
    shortage_count: int = 0
    shuffle_selection_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, "reason")
        if (
            isinstance(self.shortage_count, bool)
            or not isinstance(self.shortage_count, int)
            or self.shortage_count < 0
        ):
            raise ValueError("shortage_count must be a non-negative integer")
        object.__setattr__(
            self,
            "shuffle_selection_sha256",
            hash_json("label-shuffle-selection-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status.value,
            "records": [record.to_dict() for record in self.records],
            "reason": self.reason,
            "shortage_count": self.shortage_count,
        }
        if include_sha:
            payload["shuffle_selection_sha256"] = self.shuffle_selection_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ShiftedIntervalSelection:
    """Status receipt for outcome-free shifted interval requests."""

    status: ControlSelectionStatus
    records: tuple[ShiftedOpportunity, ...]
    reason: str
    shortage_count: int = 0
    shift_selection_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, "reason")
        if (
            isinstance(self.shortage_count, bool)
            or not isinstance(self.shortage_count, int)
            or self.shortage_count < 0
        ):
            raise ValueError("shortage_count must be a non-negative integer")
        object.__setattr__(
            self,
            "shift_selection_sha256",
            hash_json("time-shift-selection-v1", self.to_dict(include_sha=False)),
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status.value,
            "records": [record.to_dict() for record in self.records],
            "reason": self.reason,
            "shortage_count": self.shortage_count,
        }
        if include_sha:
            payload["shift_selection_sha256"] = self.shift_selection_sha256
        return payload


def build_naive_zero_controls(
    opportunities: Sequence[ControlOpportunity],
    *,
    budget: ValidationWorkBudget,
) -> ControlSelection:
    """Build exact no-trade controls on identical opportunities with zero return/cost."""

    rows = _materialize_opportunities(opportunities, budget=budget, controls=len(opportunities))
    records = tuple(
        ControlRecord(
            candidate_row_identity=row.row_identity,
            control_row_identity=row.row_identity,
            event_id=row.event_id,
            kind=ControlKind.NAIVE_ZERO,
            control_family="baseline:naive_zero",
            control_direction=row.direction,
            control_return=0.0,
            control_cost=0.0,
            selection_rank=index,
        )
        for index, row in enumerate(rows)
    )
    return _selection(ControlKind.NAIVE_ZERO, ControlSelectionStatus.COMPLETE, records, "complete")


def select_stratified_controls(
    candidates: Sequence[ControlOpportunity],
    control_pool: Sequence[ControlOpportunity],
    *,
    kind: ControlKind,
    required_control_role: str,
    budget: ValidationWorkBudget,
) -> ControlSelection:
    """Select count-matched exact-stratum controls by deterministic hash order."""

    candidate_rows = _materialize_opportunities(candidates, budget=budget, controls=len(candidates))
    pool_rows = _materialize_opportunities(control_pool, budget=budget, controls=len(control_pool))
    used_rows: set[str] = set()
    records: list[ControlRecord] = []
    shortage = 0
    for candidate in candidate_rows:
        eligible = [
            row
            for row in pool_rows
            if row.control_role == required_control_role
            and row.stratum == candidate.stratum
            and row.row_identity != candidate.row_identity
            and row.legal_entry_time != candidate.legal_entry_time
            and row.row_identity not in used_rows
        ]
        if not eligible:
            shortage += 1
            continue
        selected = min(
            eligible,
            key=lambda row: hash_json(
                "unconditional-control-order-v1",
                {"candidate_id": candidate.candidate_id, "row_identity": row.row_identity},
            ),
        )
        used_rows.add(selected.row_identity)
        records.append(
            _matched_record(
                candidate,
                selected,
                kind=kind,
                family=f"control:{required_control_role}",
                rank=len(records),
            )
        )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"control shortage: {shortage}"
    return _selection(kind, status, tuple(records), reason, shortage)


def persistence_direction(opportunity: ControlOpportunity) -> int | None:
    """Return strict sign of the immediately prior completed-bar close return."""

    value = opportunity.prior_completed_close_return
    if value is None or value == 0.0:
        return None
    return 1 if value > 0.0 else -1


def build_persistence_controls(
    candidates: Sequence[ControlOpportunity],
    control_pool: Sequence[ControlOpportunity],
    *,
    budget: ValidationWorkBudget,
) -> ControlSelection:
    """Build A persistence-direction controls using strict prior completed-bar signs."""

    candidate_rows = _materialize_opportunities(candidates, budget=budget, controls=len(candidates))
    pool_rows = _materialize_opportunities(control_pool, budget=budget, controls=len(control_pool))
    used_rows: set[str] = set()
    records: list[ControlRecord] = []
    shortage = 0
    for candidate in candidate_rows:
        direction = persistence_direction(candidate)
        if direction is None:
            shortage += 1
            continue
        eligible = [
            row
            for row in pool_rows
            if row.control_role == "persistence"
            and row.stratum == candidate.stratum
            and row.direction == direction
            and row.row_identity not in used_rows
            and row.row_identity != candidate.row_identity
            and row.legal_entry_time != candidate.legal_entry_time
            and row.is_non_final
            and candidate.is_non_final
        ]
        if not eligible:
            shortage += 1
            continue
        selected = min(
            eligible,
            key=lambda row: hash_json(
                "persistence-control-order-v1",
                {"candidate_id": candidate.candidate_id, "row_identity": row.row_identity},
            ),
        )
        used_rows.add(selected.row_identity)
        records.append(
            _matched_record(
                candidate,
                selected,
                kind=ControlKind.PERSISTENCE,
                family="control:persistence",
                rank=len(records),
            )
        )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"persistence shortage: {shortage}"
    return _selection(ControlKind.PERSISTENCE, status, tuple(records), reason, shortage)


def build_selector_evidence(
    opportunities: Sequence[SelectorInput],
    *,
    selected_row_identities: Sequence[str],
    source_publication_sha256: str,
    selector_id: str,
    selector_cutoff: datetime,
) -> SelectorEvidence:
    """Seal ordered, outcome-free selector evidence against a frozen source population."""

    rows = tuple(opportunities)
    if not all(isinstance(row, SelectorInput) for row in rows):
        raise TypeError("all selector inputs must be SelectorInput instances")
    _require_sha256(source_publication_sha256, "source_publication_sha256")
    if any(row.source_publication_sha256 != source_publication_sha256 for row in rows):
        raise ValueError("source publication does not match selector input rows")
    _require_utc(selector_cutoff, "selector_cutoff")
    selected_ids = tuple(selected_row_identities)
    _reject_duplicate_values((row.row_identity for row in rows), "row identity")
    _reject_duplicate_values(selected_ids, "selected row")
    row_by_id = {row.row_identity: row for row in rows}
    missing = [row_id for row_id in selected_ids if row_id not in row_by_id]
    if missing:
        raise ValueError(f"selected row identities are not in the source population: {missing[0]}")
    selected_rows = tuple(row_by_id[row_id] for row_id in selected_ids)
    if any(row.feature_time > selector_cutoff for row in selected_rows):
        raise ValueError("selector cutoff precedes selected feature evidence")
    return SelectorEvidence(
        selected_row_identities=selected_ids,
        source_publication_sha256=source_publication_sha256,
        selector_id=selector_id,
        selector_cutoff=selector_cutoff,
        source_population_sha256=_source_population_digest(rows),
        seal=_SELECTOR_EVIDENCE_SEAL,
    )


def build_legal_donor_pool_evidence(
    donor_pool: Sequence[ControlOpportunity],
    *,
    source_publication_sha256: str,
    pool_identity: str,
) -> LegalDonorPoolEvidence:
    """Bind the exact complete legal label-donor pool and per-stratum counts."""

    rows = tuple(donor_pool)
    if not all(isinstance(row, ControlOpportunity) for row in rows):
        raise TypeError("all donors must be ControlOpportunity instances")
    _require_sha256(source_publication_sha256, "source_publication_sha256")
    if any(row.publication_sha256 != source_publication_sha256 for row in rows):
        raise ValueError("source publication does not match donor pool rows")
    _reject_duplicate_values((row.row_identity for row in rows), "donor row identity")
    per_stratum: dict[str, int] = defaultdict(int)
    for row in rows:
        per_stratum[_stratum_digest(row.stratum)] += 1
    counts = tuple(sorted(per_stratum.items()))
    return LegalDonorPoolEvidence(
        per_stratum_counts=counts,
        total_count=len(rows),
        source_publication_sha256=source_publication_sha256,
        pool_identity=pool_identity,
        donor_pool_sha256=_donor_pool_digest(rows),
    )


def build_common_population_evidence(
    opportunities: Sequence[ControlOpportunity],
    *,
    selector_evidence: SelectorEvidence,
    selected_row_identities: Sequence[str] | None = None,
    baseline_role: str,
    augmented_role: str,
    placebo_role: str,
    seed: str,
    budget: ValidationWorkBudget,
) -> CommonPopulationEvidence:
    """Bind common-population evidence without outcome-informed selection."""

    if not isinstance(selector_evidence, SelectorEvidence):
        raise TypeError("selector_evidence must be SelectorEvidence")
    selected_ids = selector_evidence.selected_row_identities
    if selected_row_identities is not None and tuple(selected_row_identities) != selected_ids:
        raise ValueError("selector evidence selected row identities mismatch")
    _preflight_generated(
        budget,
        controls=len(opportunities) * 2,
        placebos=len(selected_ids),
        events=len(opportunities),
    )
    rows = _materialize_opportunities(opportunities, budget=budget, controls=len(opportunities))
    selector_inputs = tuple(_selector_input_from_opportunity(row) for row in rows)
    if _source_population_digest(selector_inputs) != selector_evidence.source_population_sha256:
        raise ValueError("selector evidence source population mismatch")
    if any(row.publication_sha256 != selector_evidence.source_publication_sha256 for row in rows):
        raise ValueError("selector evidence source publication mismatch")
    if any(row.feature_time > selector_evidence.selector_cutoff for row in rows):
        raise ValueError("selector evidence cutoff mismatch")
    _reject_duplicate_values(selected_ids, "selected row")
    row_by_id = {row.row_identity: row for row in rows}
    missing = [row_id for row_id in selected_ids if row_id not in row_by_id]
    if missing:
        raise ValueError(f"selected row identities are not in the common population: {missing[0]}")
    selected = set(selected_ids)
    baseline_records = tuple(
        ControlRecord(
            candidate_row_identity=row.row_identity,
            control_row_identity=row.row_identity,
            event_id=row.event_id,
            kind=ControlKind.PRICE_BASELINE,
            control_family=f"baseline:{baseline_role}",
            control_direction=row.direction,
            control_return=row.gross_return,
            control_cost=row.cost_return,
            selection_rank=index,
        )
        for index, row in enumerate(rows)
    )
    augmented_records = tuple(
        ControlRecord(
            candidate_row_identity=row.row_identity,
            control_row_identity=row.row_identity,
            event_id=row.event_id,
            kind=ControlKind.AUGMENTED_SELECTED,
            control_family=f"augmented:{augmented_role}",
            control_direction=row.direction,
            control_return=row.gross_return if row.row_identity in selected else 0.0,
            control_cost=row.cost_return if row.row_identity in selected else 0.0,
            selection_rank=index,
        )
        for index, row in enumerate(rows)
    )
    placebo_rows, placebo_shortage = _select_rate_matched_placebo_rows(
        rows, selected_row_ids=selected, seed=seed
    )
    placebo_records = tuple(
        ControlRecord(
            candidate_row_identity=row.row_identity,
            control_row_identity=row.row_identity,
            event_id=row.event_id,
            kind=ControlKind.RATE_MATCHED_PLACEBO,
            control_family=f"placebo:{placebo_role}",
            control_direction=row.direction,
            control_return=row.gross_return,
            control_cost=row.cost_return,
            selection_rank=index,
        )
        for index, row in enumerate(placebo_rows)
    )
    placebo_status = (
        ControlSelectionStatus.COMPLETE
        if placebo_shortage == 0
        else ControlSelectionStatus.INCONCLUSIVE
    )
    placebo_reason = (
        "complete"
        if placebo_shortage == 0
        else f"rate-matched placebo shortage: {placebo_shortage}"
    )
    return CommonPopulationEvidence(
        baseline=_selection(
            ControlKind.PRICE_BASELINE,
            ControlSelectionStatus.COMPLETE,
            baseline_records,
            "complete",
        ),
        augmented=_selection(
            ControlKind.AUGMENTED_SELECTED,
            ControlSelectionStatus.COMPLETE,
            augmented_records,
            "complete",
        ),
        placebo=_selection(
            ControlKind.RATE_MATCHED_PLACEBO,
            placebo_status,
            placebo_records,
            placebo_reason,
            placebo_shortage,
        ),
        selected_count=len(selected),
        complement_count=len(rows) - len(selected),
        complement_row_identities=tuple(
            row.row_identity for row in rows if row.row_identity not in selected
        ),
        selector_evidence_sha256=selector_evidence.selector_sha256,
        selector_cutoff=selector_evidence.selector_cutoff,
    )


def match_prior_week_pseudo_level_controls(
    candidates: Sequence[ControlOpportunity],
    control_pool: Sequence[ControlOpportunity],
    *,
    budget: ValidationWorkBudget,
) -> ControlSelection:
    """Match D prior-week pseudo-level controls by exact +7-day causal shift."""

    candidate_rows = _materialize_opportunities(candidates, budget=budget, controls=len(candidates))
    pool_rows = _materialize_opportunities(control_pool, budget=budget, controls=len(control_pool))
    used_rows: set[str] = set()
    records: list[ControlRecord] = []
    shortage = 0
    for candidate in candidate_rows:
        eligible = [
            row
            for row in pool_rows
            if row.control_role == "prior_week_pseudo_level"
            and row.prior_week_key == candidate.prior_week_key
            and row.feature_time + timedelta(days=7) == candidate.feature_time
            and row.utc_week_start + timedelta(days=7) == candidate.utc_week_start
            and row.is_non_final
            and candidate.is_non_final
            and row.row_identity not in used_rows
            and row.row_identity != candidate.row_identity
        ]
        if not eligible:
            shortage += 1
            continue
        selected = min(
            eligible,
            key=lambda row: hash_json(
                "prior-week-pseudo-level-control-order-v1",
                {"candidate_id": candidate.candidate_id, "row_identity": row.row_identity},
            ),
        )
        used_rows.add(selected.row_identity)
        records.append(
            _matched_record(
                candidate,
                selected,
                kind=ControlKind.PRIOR_WEEK_PSEUDO_LEVEL,
                family="control:prior_week_pseudo_level",
                rank=len(records),
            )
        )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"prior-week pseudo-level unavailable: {shortage}"
    return _selection(ControlKind.PRIOR_WEEK_PSEUDO_LEVEL, status, tuple(records), reason, shortage)


def shuffle_labels_within_legal_strata(
    candidates: Sequence[ControlOpportunity],
    donor_pool: Sequence[ControlOpportunity],
    *,
    donor_pool_evidence: LegalDonorPoolEvidence,
    seed: str,
    budget: ValidationWorkBudget,
) -> LabelShuffleSelection:
    """Deterministically bind candidate events to separate legal label donors."""

    _preflight_generated(
        budget,
        placebos=len(candidates),
        events=len(candidates) + len(donor_pool),
    )
    candidate_rows = _materialize_opportunities(candidates, budget=budget, placebos=len(candidates))
    donor_rows = _materialize_opportunities(donor_pool, budget=budget, placebos=len(donor_pool))
    donor_mismatch = _donor_pool_mismatch(donor_rows, donor_pool_evidence)
    if donor_mismatch is not None:
        return LabelShuffleSelection(
            status=ControlSelectionStatus.INCONCLUSIVE,
            records=(),
            reason=f"donor pool evidence mismatch: {donor_mismatch}",
            shortage_count=len(candidate_rows),
        )
    used_donors: set[str] = set()
    records: list[ShuffledLabel] = []
    shortage = 0
    for candidate in candidate_rows:
        eligible = [
            donor
            for donor in donor_rows
            if donor.stratum == candidate.stratum
            and donor.row_identity not in used_donors
            and donor.row_identity != candidate.row_identity
            and donor.is_non_final
            and candidate.is_non_final
        ]
        if not eligible:
            shortage += 1
            continue
        donor = min(
            eligible,
            key=lambda row: hash_json(
                "label-shuffle-donor-order-v1",
                {"seed": seed, "event_id": candidate.event_id, "row_identity": row.row_identity},
            ),
        )
        used_donors.add(donor.row_identity)
        records.append(
            ShuffledLabel(
                event_id=candidate.event_id,
                source_row_identity=candidate.row_identity,
                label_source_row_identity=donor.row_identity,
                gross_return=donor.gross_return,
                cost_return=donor.cost_return,
            )
        )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"label donor shortage: {shortage}"
    return LabelShuffleSelection(
        status=status, records=tuple(records), reason=reason, shortage_count=shortage
    )


def shift_opportunities_one_utc_week(
    opportunities: Sequence[ControlOpportunity],
    *,
    legal_boundaries: Sequence[LegalBoundaryInterval],
    budget: ValidationWorkBudget,
) -> ShiftedIntervalSelection:
    """Create +1 UTC-week outcome-free interval requests inside non-final boundaries."""

    _preflight_generated(budget, placebos=len(opportunities), events=len(opportunities))
    rows = _materialize_opportunities(opportunities, budget=budget, placebos=len(opportunities))
    boundaries = tuple(legal_boundaries)
    if not all(isinstance(boundary, LegalBoundaryInterval) for boundary in boundaries):
        raise TypeError("legal_boundaries must contain LegalBoundaryInterval instances")
    boundary_by_key = _canonical_legal_boundary_map(boundaries)
    delta = timedelta(days=7)
    shifted: list[ShiftedOpportunity] = []
    shortage = 0
    for row in rows:
        if not row.is_non_final:
            shortage += 1
            continue
        boundary = boundary_by_key.get(_legal_boundary_key(row))
        if boundary is None:
            shortage += 1
            continue
        shifted_feature = row.feature_time + delta
        shifted_signal = row.signal_time + delta
        shifted_entry = row.legal_entry_time + delta
        shifted_label_start = row.label_start + delta
        shifted_label_end = row.label_end + delta
        if not boundary.contains_path(
            feature_time=shifted_feature,
            signal_time=shifted_signal,
            entry_time=shifted_entry,
            label_start=shifted_label_start,
            label_end=shifted_label_end,
        ):
            shortage += 1
            continue
        shifted.append(
            ShiftedOpportunity(
                source_row_identity=row.row_identity,
                event_id=row.event_id,
                source_feature_time=row.feature_time,
                shifted_feature_time=shifted_feature,
                shifted_signal_time=shifted_signal,
                shifted_entry_time=shifted_entry,
                shifted_label_start=shifted_label_start,
                shifted_label_end=shifted_label_end,
                programme_id=row.programme_id,
                publication_sha256=row.publication_sha256,
                symbol=row.symbol,
                timeframe=row.timeframe,
                segment_id=row.segment_id,
                fold_id=row.fold_id,
                component=row.component,
                block_id=row.block_id,
                legal_boundary_sha256=boundary.boundary_sha256,
                boundary_interval_start=boundary.interval_start,
                boundary_interval_end=boundary.interval_end,
            )
        )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"time-shift boundary shortage: {shortage}"
    if shortage != 0 and not boundaries:
        reason = f"time-shift legal boundary unavailable: {shortage}"
    return ShiftedIntervalSelection(
        status=status, records=tuple(shifted), reason=reason, shortage_count=shortage
    )


def random_feature_selection(
    candidates: Sequence[ControlOpportunity],
    donor_pool: Sequence[ControlOpportunity],
    *,
    seed: str,
    direction_seed: str,
    budget: ValidationWorkBudget,
) -> ControlSelection:
    """Select SHA scalar random features with exact candidate strata and direction counts."""

    _preflight_generated(
        budget,
        placebos=len(candidates),
        events=len(candidates) + len(donor_pool),
    )
    candidate_rows = _materialize_opportunities(candidates, budget=budget, placebos=len(candidates))
    donor_rows = _materialize_opportunities(donor_pool, budget=budget, placebos=len(donor_pool))
    used_donors: set[str] = set()
    records: list[ControlRecord] = []
    shortage = 0
    candidates_by_stratum: dict[tuple[object, ...], list[ControlOpportunity]] = defaultdict(list)
    for candidate in candidate_rows:
        candidates_by_stratum[_directionless_stratum(candidate)].append(candidate)
    for stratum, stratum_candidates in sorted(
        candidates_by_stratum.items(), key=lambda item: repr(item[0])
    ):
        eligible = [
            donor
            for donor in donor_rows
            if _directionless_stratum(donor) == stratum
            and donor.row_identity not in used_donors
            and donor.is_non_final
            and all(
                donor.row_identity != candidate.row_identity for candidate in stratum_candidates
            )
        ]
        if not all(candidate.is_non_final for candidate in stratum_candidates):
            shortage += len(stratum_candidates)
            continue
        selected_rows = sorted(
            eligible,
            key=lambda row: _hash_scalar(
                domain="random-feature-scalar-v1",
                seed=seed,
                row_identity=row.row_identity,
                purpose="feature",
            ),
        )[: len(stratum_candidates)]
        if len(selected_rows) < len(stratum_candidates):
            shortage += len(stratum_candidates) - len(selected_rows)
            continue
        ordered_candidates = sorted(
            stratum_candidates,
            key=lambda row: hash_json(
                "random-feature-direction-candidate-order-v1",
                {"seed": direction_seed, "row_identity": row.row_identity},
            ),
        )
        ordered_directions = [
            row.direction
            for row in sorted(
                stratum_candidates,
                key=lambda row: hash_json(
                    "random-feature-direction-assignment-v1",
                    {"seed": direction_seed, "row_identity": row.row_identity},
                ),
            )
        ]
        ordered_donors = sorted(
            selected_rows,
            key=lambda row: hash_json(
                "random-feature-direction-donor-order-v1",
                {"seed": direction_seed, "row_identity": row.row_identity},
            ),
        )
        for candidate, selected, direction in zip(
            ordered_candidates, ordered_donors, ordered_directions, strict=True
        ):
            used_donors.add(selected.row_identity)
            records.append(
                ControlRecord(
                    candidate_row_identity=candidate.row_identity,
                    control_row_identity=selected.row_identity,
                    event_id=candidate.event_id,
                    kind=ControlKind.RANDOM_FEATURE,
                    control_family="negative:random_feature",
                    control_direction=direction,
                    control_return=selected.gross_return,
                    control_cost=selected.cost_return,
                    source_row_identity=selected.row_identity,
                    selection_rank=len(records),
                )
            )
    status = (
        ControlSelectionStatus.COMPLETE if shortage == 0 else ControlSelectionStatus.INCONCLUSIVE
    )
    reason = "complete" if shortage == 0 else f"random feature shortage: {shortage}"
    return _selection(ControlKind.RANDOM_FEATURE, status, tuple(records), reason, shortage)


def _preflight_generated(
    budget: ValidationWorkBudget,
    *,
    controls: int = 0,
    placebos: int = 0,
    events: int = 0,
) -> None:
    try:
        budget.preflight(ValidationWorkDemand(controls=controls, placebos=placebos, events=events))
    except ValidationWorkBudgetViolation as error:
        if error.stage in {"controls", "placebos"}:
            raise ValueError(f"control work limit exceeded: {error}") from error
        raise


def _materialize_opportunities(
    opportunities: Sequence[ControlOpportunity],
    *,
    budget: ValidationWorkBudget,
    controls: int = 0,
    placebos: int = 0,
) -> tuple[ControlOpportunity, ...]:
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be a ValidationWorkBudget")
    declared_len = len(opportunities)
    try:
        budget.preflight(
            ValidationWorkDemand(controls=controls, placebos=placebos, events=declared_len)
        )
    except ValidationWorkBudgetViolation as error:
        if error.stage in {"controls", "placebos"}:
            raise ValueError(f"control work limit exceeded: {error}") from error
        raise
    rows = tuple(opportunities)
    if len(rows) != declared_len:
        raise ValueError("opportunity collection length changed during materialisation")
    if not all(isinstance(row, ControlOpportunity) for row in rows):
        raise TypeError("all opportunities must be ControlOpportunity instances")
    _reject_duplicate_values((row.row_identity for row in rows), "row identity")
    _reject_duplicate_values((row.event_id for row in rows), "event id")
    return rows


def _matched_record(
    candidate: ControlOpportunity,
    selected: ControlOpportunity,
    *,
    kind: ControlKind,
    family: str,
    rank: int,
) -> ControlRecord:
    return ControlRecord(
        candidate_row_identity=candidate.row_identity,
        control_row_identity=selected.row_identity,
        event_id=candidate.event_id,
        kind=kind,
        control_family=family,
        control_direction=selected.direction,
        control_return=selected.gross_return,
        control_cost=selected.cost_return,
        source_row_identity=selected.row_identity,
        selection_rank=rank,
    )


def _selection(
    kind: ControlKind,
    status: ControlSelectionStatus,
    records: tuple[ControlRecord, ...],
    reason: str,
    shortage_count: int = 0,
) -> ControlSelection:
    return ControlSelection(
        kind=kind,
        status=status,
        records=records,
        reason=reason,
        shortage_count=shortage_count,
        seal=_CONTROL_SELECTION_SEAL,
    )


def _select_rate_matched_placebo_rows(
    rows: Sequence[ControlOpportunity],
    *,
    selected_row_ids: set[str],
    seed: str,
) -> tuple[tuple[ControlOpportunity, ...], int]:
    selected_by_stratum: dict[tuple[object, ...], int] = defaultdict(int)
    for row in rows:
        if row.row_identity in selected_row_ids:
            selected_by_stratum[row.stratum] += 1
    selected_placebos: list[ControlOpportunity] = []
    shortage = 0
    for stratum, required_count in sorted(
        selected_by_stratum.items(), key=lambda item: repr(item[0])
    ):
        eligible = [
            row
            for row in rows
            if row.stratum == stratum and row.row_identity not in selected_row_ids
        ]
        ordered = _select_domain_hash_rows(
            eligible,
            count=required_count,
            domain="rate-matched-placebo-order-v1",
            seed=seed,
            exclude=set(),
        )
        selected_placebos.extend(ordered)
        shortage += required_count - len(ordered)
    return tuple(selected_placebos), shortage


def _select_domain_hash_rows(
    rows: Sequence[ControlOpportunity],
    *,
    count: int,
    domain: str,
    seed: str,
    exclude: set[str],
    purpose: str | None = None,
) -> tuple[ControlOpportunity, ...]:
    eligible = [row for row in rows if row.row_identity not in exclude]
    ordered = sorted(
        eligible,
        key=lambda row: _hash_scalar(
            domain=domain, seed=seed, row_identity=row.row_identity, purpose=purpose
        ),
    )
    return tuple(ordered[:count])


def _source_population_digest(rows: Sequence[SelectorInput]) -> str:
    return hash_json(
        "selector-source-population-v1",
        [row.to_dict() for row in sorted(rows, key=lambda item: item.row_identity)],
    )


def _selector_input_from_opportunity(row: ControlOpportunity) -> SelectorInput:
    return SelectorInput(
        row_identity=row.row_identity,
        source_publication_sha256=row.publication_sha256,
        feature_time=row.feature_time,
    )


def _canonical_legal_boundary_map(
    boundaries: Sequence[LegalBoundaryInterval],
) -> dict[tuple[str, str, str, str, int, str, str, str], LegalBoundaryInterval]:
    boundary_by_key: dict[tuple[str, str, str, str, int, str, str, str], LegalBoundaryInterval] = {}
    for boundary in boundaries:
        existing = boundary_by_key.get(boundary.key)
        if existing is None:
            boundary_by_key[boundary.key] = boundary
            continue
        if existing.boundary_sha256 != boundary.boundary_sha256:
            raise ValueError("conflicting legal boundary interval")
    return boundary_by_key


def _donor_pool_digest(rows: Sequence[ControlOpportunity]) -> str:
    return hash_json(
        "legal-donor-pool-v1",
        [
            {
                "row_identity": row.row_identity,
                "stratum": _stratum_digest(row.stratum),
                "publication_sha256": row.publication_sha256,
            }
            for row in sorted(rows, key=lambda item: item.row_identity)
        ],
    )


def _stratum_digest(stratum: tuple[object, ...]) -> str:
    return hash_json("legal-stratum-v1", [str(item) for item in stratum])


def _donor_pool_mismatch(
    donor_rows: Sequence[ControlOpportunity], evidence: LegalDonorPoolEvidence
) -> str | None:
    if not isinstance(evidence, LegalDonorPoolEvidence):
        raise TypeError("donor_pool_evidence must be LegalDonorPoolEvidence")
    if len(donor_rows) != evidence.total_count:
        return "total count"
    if _donor_pool_digest(donor_rows) != evidence.donor_pool_sha256:
        return "donor digest"
    if any(row.publication_sha256 != evidence.source_publication_sha256 for row in donor_rows):
        return "source publication"
    per_stratum: dict[str, int] = defaultdict(int)
    for row in donor_rows:
        per_stratum[_stratum_digest(row.stratum)] += 1
    if tuple(sorted(per_stratum.items())) != evidence.per_stratum_counts:
        return "per-stratum counts"
    return None


def _legal_boundary_key(
    row: ControlOpportunity,
) -> tuple[str, str, str, str, int, str, str, str]:
    return (
        row.programme_id,
        row.publication_sha256,
        row.symbol,
        row.timeframe,
        row.segment_id,
        row.fold_id,
        row.component,
        row.block_id,
    )


def _directionless_stratum(row: ControlOpportunity) -> tuple[object, ...]:
    return (
        row.symbol,
        row.fold_id,
        row.utc_week_start,
        row.horizon_hours,
        row.timeframe,
        row.programme_id,
        row.publication_sha256,
        row.segment_id,
        row.component,
        row.block_id,
    )


def _deterministic_permutation(
    rows: Sequence[ControlOpportunity],
    *,
    seed: str,
    domain: str,
) -> list[ControlOpportunity]:
    return sorted(
        rows,
        key=lambda row: hash_json(domain, {"seed": seed, "row_identity": row.row_identity}),
    )


def _hash_scalar(*, domain: str, seed: str, row_identity: str, purpose: str | None) -> float:
    payload: dict[str, object] = {"seed": seed, "row_identity": row_identity}
    if purpose is not None:
        payload["purpose"] = purpose
    digest = hash_json(domain, payload)
    return int(digest[:16], 16) / 2**64


def _deterministic_direction(row: ControlOpportunity, *, seed: str) -> int:
    digest = hash_json(
        "random-feature-direction-v1",
        {"seed": seed, "row_identity": row.row_identity, "purpose": "direction"},
    )
    return 1 if int(digest[:16], 16) % 2 == 0 else -1


def _reject_duplicate_values(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label}: {value}")
        seen.add(value)


def _require_non_empty(value: object, label: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_utc(value: object, label: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime")
    offset = value.utcoffset()
    if (
        value.tzinfo is None
        or offset is None
        or value.astimezone(UTC).utcoffset() != timedelta(0)
        or offset != timedelta(0)
    ):
        raise ValueError(f"{label} must be UTC-aware")


def _require_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)
