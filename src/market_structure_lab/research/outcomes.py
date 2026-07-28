"""Causal attachment of frozen candidate signals to complete gross outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite
import re
from typing import TYPE_CHECKING, cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import CanonicalAggregateBar
from market_structure_lab.data.aggregate_bars import (
    OrderedSourceIdentity,
    canonical_source_row_identity,
    target_timeframe_minutes,
)
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
from market_structure_lab.research.candidates import CandidateSignal
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    OutcomeComponent,
    OutcomePolicy,
    ValidationSlot,
    ValidationWorkBudget,
    ValidationWorkDemand,
)

if TYPE_CHECKING:
    from market_structure_lab.research.splits import VerifiedFinalAccess

_ATTACHED_OUTCOME_SEAL = object()
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VP-[a-f0-9]{64}$")


class FinalHoldoutAccessRequired(PermissionError):
    """A final component was requested without programme-scoped verified access."""


@dataclass(frozen=True, slots=True)
class AttachedOutcome:
    """One verifier-issued gross outcome and complete minute-path evidence digest."""

    outcome_id: str = field(init=False)
    signal_id: str
    candidate_id: str
    family: str
    symbol: str
    timeframe: str
    direction: int
    feature_start: datetime
    information_cutoff: datetime
    entry_time: datetime
    exit_time: datetime
    label_start: datetime
    label_end: datetime
    entry_price: float
    exit_price: float
    gross_signed_return: float
    mfe: float
    mae: float
    horizon_hours: int
    path_row_count: int
    path_sha256: str
    aggregate_publication_sha256: str
    source_series_sha256: str
    minute_publication_sha256: str
    segment_id: int
    outcome_policy_sha256: str
    programme_id: str | None = None
    final_batch_sha256: str | None = None
    final_access_receipt_sha256: str | None = None
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _ATTACHED_OUTCOME_SEAL:
            raise TypeError("AttachedOutcome requires the outcome verifier seal")
        if not self.signal_id.startswith("CS-"):
            raise ValueError("outcome signal_id must identify a candidate signal")
        if not self.candidate_id.startswith("HC-"):
            raise ValueError("outcome candidate_id must identify a human-origin candidate")
        if self.family not in EXPECTED_FAMILIES:
            raise ValueError("outcome family is unsupported")
        if self.timeframe not in ("1h", "4h"):
            raise ValueError("outcome timeframe must be 1h or 4h")
        if self.direction not in (-1, 1):
            raise ValueError("outcome direction must be -1 or +1")
        for name in (
            "feature_start",
            "information_cutoff",
            "entry_time",
            "exit_time",
            "label_start",
            "label_end",
        ):
            value = getattr(self, name)
            offset = value.utcoffset()
            if value.tzinfo is None or offset is None or offset.total_seconds():
                raise ValueError(f"{name} must be UTC-aware")
        if not (
            self.feature_start
            < self.information_cutoff
            == self.entry_time
            == self.label_start
            < self.exit_time
            == self.label_end
        ):
            raise ValueError("outcome feature and label intervals are not causal half-open ranges")
        if (
            isinstance(self.horizon_hours, bool)
            or not isinstance(self.horizon_hours, int)
            or self.horizon_hours < 1
            or self.exit_time - self.entry_time != timedelta(hours=self.horizon_hours)
        ):
            raise ValueError("outcome horizon does not match its label interval")
        if (
            isinstance(self.path_row_count, bool)
            or not isinstance(self.path_row_count, int)
            or self.path_row_count != self.horizon_hours * 60
        ):
            raise ValueError("outcome path row count must cover every minute in the horizon")
        for name in (
            "entry_price",
            "exit_price",
            "gross_signed_return",
            "mfe",
            "mae",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be finite")
        if self.entry_price <= 0 or self.exit_price <= 0:
            raise ValueError("outcome prices must be positive")
        if self.mfe < 0 or self.mae > 0:
            raise ValueError("outcome excursion signs are invalid")
        for name in (
            "path_sha256",
            "aggregate_publication_sha256",
            "source_series_sha256",
            "minute_publication_sha256",
            "outcome_policy_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        final_values = (
            self.programme_id,
            self.final_batch_sha256,
            self.final_access_receipt_sha256,
        )
        if any(value is not None for value in final_values):
            if not all(value is not None for value in final_values):
                raise ValueError("final outcome access identity is incomplete")
            if (
                not isinstance(self.programme_id, str)
                or _PROGRAMME_ID.fullmatch(self.programme_id) is None
            ):
                raise ValueError("final outcome programme identity is invalid")
            _require_sha256(self.final_batch_sha256, "final_batch_sha256")
            _require_sha256(
                self.final_access_receipt_sha256,
                "final_access_receipt_sha256",
            )
        if (
            isinstance(self.segment_id, bool)
            or not isinstance(self.segment_id, int)
            or self.segment_id < 0
        ):
            raise ValueError("outcome segment_id must be a non-negative integer")
        object.__setattr__(
            self,
            "outcome_id",
            f"OR-{hash_json('attached-outcome-v1', self.to_dict())}",
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "signal_id": self.signal_id,
            "candidate_id": self.candidate_id,
            "family": self.family,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "feature_start": self.feature_start,
            "information_cutoff": self.information_cutoff,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "label_start": self.label_start,
            "label_end": self.label_end,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "gross_signed_return": self.gross_signed_return,
            "mfe": self.mfe,
            "mae": self.mae,
            "horizon_hours": self.horizon_hours,
            "path_row_count": self.path_row_count,
            "path_sha256": self.path_sha256,
            "aggregate_publication_sha256": self.aggregate_publication_sha256,
            "source_series_sha256": self.source_series_sha256,
            "minute_publication_sha256": self.minute_publication_sha256,
            "segment_id": self.segment_id,
            "outcome_policy_sha256": self.outcome_policy_sha256,
        }
        if self.programme_id is not None:
            payload["programme_id"] = self.programme_id
            payload["final_batch_sha256"] = self.final_batch_sha256
            payload["final_access_receipt_sha256"] = self.final_access_receipt_sha256
        return payload


def attach_outcome(
    signal: CandidateSignal,
    aggregate_bars: VerifiedAggregateSeries,
    minute_path: Iterable[Mapping[str, object]],
    policy: OutcomePolicy,
    budget: ValidationWorkBudget,
    *,
    final_access: VerifiedFinalAccess | None = None,
) -> AttachedOutcome:
    """Attach one exact `[entry, exit)` outcome without changing detector identity."""

    if not isinstance(signal, CandidateSignal):
        raise TypeError("outcome attachment requires a detector-issued CandidateSignal")
    if signal.signal_id != f"CS-{hash_json('candidate-signal-v1', signal.to_dict())}":
        raise ValueError("candidate signal identity differs from its frozen causal fields")
    if not isinstance(aggregate_bars, VerifiedAggregateSeries):
        raise TypeError("outcome attachment requires a VerifiedAggregateSeries")
    if not isinstance(policy, OutcomePolicy):
        raise TypeError("outcome attachment requires an OutcomePolicy")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("outcome attachment requires a ValidationWorkBudget")
    _verify_outcome_policy(policy)
    _validate_publication_binding(signal, aggregate_bars, policy)
    slot = _signal_slot(signal)
    if policy.horizon_hours != slot.horizon_hours:
        raise ValueError("outcome horizon differs from the frozen candidate slot")
    if policy.component is OutcomeComponent.DEVELOPMENT:
        if final_access is not None:
            raise ValueError("development outcome cannot consume final access")
    else:
        if final_access is None:
            raise FinalHoldoutAccessRequired(
                "final-holdout outcome attachment requires programme-scoped verified access"
            )
        if policy.programme_id is None or policy.final_batch_sha256 is None:
            raise ValueError("final outcome policy must bind its programme and final batch")
        from market_structure_lab.research.splits import verify_final_access

        verify_final_access(
            final_access,
            programme_id=policy.programme_id,
            batch_sha256=policy.final_batch_sha256,
            candidate_id=signal.candidate_id,
            component=policy.component,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            label_start=signal.legal_entry,
            label_end=signal.legal_entry + timedelta(hours=policy.horizon_hours),
        )

    target_minutes = target_timeframe_minutes(signal.timeframe)
    horizon_minutes = policy.horizon_hours * 60
    if horizon_minutes % target_minutes:
        raise ValueError("outcome horizon must contain an integral number of aggregate bars")
    horizon_bars = horizon_minutes // target_minutes
    budget.preflight(
        ValidationWorkDemand(outcomes=1, path_cells=horizon_minutes),
        deferred_work=minute_path,
    )

    bars = aggregate_bars.bars
    cutoff_index = _bar_close_index(bars, signal.information_cutoff)
    entry_index = cutoff_index + 1
    if (
        signal.legal_entry != signal.information_cutoff
        or entry_index >= len(bars)
        or bars[entry_index].timestamp != signal.legal_entry
    ):
        raise ValueError("candidate legal entry is not the next contiguous aggregate-bar open")
    exit_index = entry_index + horizon_bars
    if exit_index >= len(bars):
        raise ValueError("outcome exit aggregate bar is missing")
    _validate_aggregate_interval(
        bars,
        start_index=cutoff_index,
        stop_index=exit_index,
        signal=signal,
    )

    entry_bar = bars[entry_index]
    exit_bar = bars[exit_index]
    expected_source_ids = tuple(
        source_id for bar in bars[entry_index:exit_index] for source_id in bar.source_row_ids
    )
    if len(expected_source_ids) != horizon_minutes:
        raise ValueError("aggregate outcome interval does not contain the complete minute path")
    path_sha256, maximum_high, minimum_low = _validate_minute_path(
        minute_path,
        expected_source_ids=expected_source_ids,
        entry_time=entry_bar.timestamp,
        exit_time=exit_bar.timestamp,
        entry_price=entry_bar.open,
        symbol=signal.symbol,
        segment_id=signal.segment_id,
    )
    entry_price = entry_bar.open
    exit_price = exit_bar.open
    gross = signal.direction * (exit_price / entry_price - 1.0)
    if signal.direction == 1:
        mfe = maximum_high / entry_price - 1.0
        mae = minimum_low / entry_price - 1.0
    else:
        mfe = 1.0 - minimum_low / entry_price
        mae = 1.0 - maximum_high / entry_price
    return AttachedOutcome(
        signal_id=signal.signal_id,
        candidate_id=signal.candidate_id,
        family=signal.family,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        direction=signal.direction,
        feature_start=signal.feature_start,
        information_cutoff=signal.information_cutoff,
        entry_time=entry_bar.timestamp,
        exit_time=exit_bar.timestamp,
        label_start=entry_bar.timestamp,
        label_end=exit_bar.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_signed_return=gross,
        mfe=mfe,
        mae=mae,
        horizon_hours=policy.horizon_hours,
        path_row_count=horizon_minutes,
        path_sha256=path_sha256,
        aggregate_publication_sha256=aggregate_bars.publication_sha256,
        source_series_sha256=aggregate_bars.series_sha256,
        minute_publication_sha256=aggregate_bars.parent_snapshot_manifest.snapshot_sha256,
        segment_id=signal.segment_id,
        outcome_policy_sha256=policy.sha256,
        programme_id=policy.programme_id,
        final_batch_sha256=policy.final_batch_sha256,
        final_access_receipt_sha256=(
            final_access.access_receipt_sha256 if final_access is not None else None
        ),
        seal=_ATTACHED_OUTCOME_SEAL,
    )


def _verify_outcome_policy(policy: OutcomePolicy) -> None:
    canonical = OutcomePolicy(
        horizon_hours=policy.horizon_hours,
        component=policy.component,
        aggregate_publication_sha256=policy.aggregate_publication_sha256,
        minute_publication_sha256=policy.minute_publication_sha256,
        programme_id=policy.programme_id,
        final_batch_sha256=policy.final_batch_sha256,
    )
    if policy != canonical:
        raise ValueError("outcome policy differs from its canonical fields")


def _validate_publication_binding(
    signal: CandidateSignal,
    series: VerifiedAggregateSeries,
    policy: OutcomePolicy,
) -> None:
    if (
        signal.source_publication_sha256 != series.publication_sha256
        or signal.source_series_sha256 != series.series_sha256
        or policy.aggregate_publication_sha256 != series.publication_sha256
    ):
        raise ValueError("outcome aggregate publication differs from the frozen signal")
    if policy.minute_publication_sha256 != series.parent_snapshot_manifest.snapshot_sha256:
        raise ValueError("outcome minute publication differs from the verified aggregate parent")
    if (
        signal.symbol != series.symbol
        or signal.timeframe != series.target_timeframe
        or signal.segment_id != series.segment_id
    ):
        raise ValueError("outcome series boundary differs from the frozen signal")


def _signal_slot(signal: CandidateSignal) -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        if slot.slot_id == signal.candidate_slot_id:
            expected_direction = "long" if signal.direction == 1 else "short"
            if (
                slot.family != signal.family
                or slot.timeframe != signal.timeframe
                or slot.direction != expected_direction
            ):
                raise ValueError("candidate signal differs from its frozen slot")
            return slot
    raise ValueError("candidate signal slot is outside the frozen validation roster")


def _bar_close_index(bars: tuple[CanonicalAggregateBar, ...], cutoff: datetime) -> int:
    for index, bar in enumerate(bars):
        if bar.bar_close == cutoff:
            return index
    raise ValueError("candidate information cutoff is absent from the aggregate series")


def _validate_aggregate_interval(
    bars: tuple[CanonicalAggregateBar, ...],
    *,
    start_index: int,
    stop_index: int,
    signal: CandidateSignal,
) -> None:
    previous = bars[start_index]
    for bar in bars[start_index + 1 : stop_index + 1]:
        if (
            bar.timestamp != previous.bar_close
            or bar.symbol != signal.symbol
            or bar.target_timeframe != signal.timeframe
            or bar.segment_id != signal.segment_id
        ):
            raise ValueError("outcome aggregate interval is not contiguous within one series")
        previous = bar


def _validate_minute_path(
    rows: Iterable[Mapping[str, object]],
    *,
    expected_source_ids: tuple[str, ...],
    entry_time: datetime,
    exit_time: datetime,
    entry_price: float,
    symbol: str,
    segment_id: int,
) -> tuple[str, float, float]:
    iterator = iter(rows)
    identity = OrderedSourceIdentity()
    maximum_high = float("-inf")
    minimum_low = float("inf")
    for index, expected_source_id in enumerate(expected_source_ids):
        try:
            row = next(iterator)
        except StopIteration:
            raise ValueError("outcome minute path ends before the frozen exit") from None
        timestamp = row.get("timestamp")
        expected_timestamp = entry_time + timedelta(minutes=index)
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() != timedelta(0)
            or timestamp.astimezone(UTC) != expected_timestamp
            or row.get("symbol") != symbol
            or row.get("timeframe") != "1m"
            or row.get("segment_id") != segment_id
        ):
            raise ValueError("outcome minute path is not contiguous within the frozen series")
        observed_source_id = canonical_source_row_identity(row)
        if observed_source_id != expected_source_id:
            raise ValueError("outcome minute path identity differs from aggregate source evidence")
        if index == 0 and cast(float, row["open"]) != entry_price:
            raise ValueError("outcome entry minute open differs from the legal aggregate entry")
        identity.update(observed_source_id)
        maximum_high = max(maximum_high, float(cast(float, row["high"])))
        minimum_low = min(minimum_low, float(cast(float, row["low"])))
    try:
        extra = next(iterator)
    except StopIteration:
        extra = None
    if extra is not None:
        timestamp = extra.get("timestamp")
        if timestamp == exit_time:
            raise ValueError("outcome minute path must exclude the exit instant")
        raise ValueError("outcome minute path extends beyond the frozen exit")
    return identity.hexdigest(), maximum_high, minimum_low


__all__ = [
    "AttachedOutcome",
    "FinalHoldoutAccessRequired",
    "attach_outcome",
]


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
