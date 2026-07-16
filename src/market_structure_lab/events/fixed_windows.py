"""Causal event segmentation over ordered feature-row streams."""

from __future__ import annotations

from collections import deque
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Iterator

from market_structure_lab.events.models import EventKind, MarketEvent, make_event
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry


class UTCSessionUnit(StrEnum):
    """Calendar-aligned UTC session boundaries."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


def _positive_integer(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be positive")
    return value


def _stream_identity(row: FeatureRow) -> tuple[str, ...]:
    return (
        row.symbol,
        row.timeframe,
        row.dataset_version,
        row.config_version,
        row.profile_version,
        row.window_policy_id,
        row.feature_set_id,
        row.registry_id,
    )


def _validated_rows(
    rows: Iterable[FeatureRow], registry: FeatureRegistry
) -> Iterator[tuple[FeatureRow, bool]]:
    """Yield valid rows and whether each row starts a new canonical segment."""

    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    identity: tuple[str, ...] | None = None
    previous: FeatureRow | None = None
    for row in rows:
        if not isinstance(row, FeatureRow):
            raise TypeError("rows must contain only FeatureRow values")
        registry.validate_row(row)
        current_identity = _stream_identity(row)
        if identity is None:
            identity = current_identity
        elif current_identity != identity:
            raise ValueError("feature-row stream identity changed")

        segment_start = previous is None or row.segment_id != previous.segment_id
        if previous is not None:
            if row.timestamp <= previous.timestamp:
                raise ValueError("feature rows must be in strictly increasing timestamp order")
            if segment_start:
                if row.segment_id <= previous.segment_id:
                    raise ValueError("segment identifiers must increase at a boundary")
            elif row.timestamp != previous.information_cutoff:
                raise ValueError("unexplained gap inside a canonical segment")
        yield row, segment_start
        previous = row


def segment_fixed_windows(
    rows: Iterable[FeatureRow],
    *,
    width: int,
    trigger_version: str,
    registry: FeatureRegistry,
) -> Iterator[MarketEvent]:
    """Emit full, non-overlapping fixed-width windows without crossing segments."""

    window_width = _positive_integer(width, "width")
    first: FeatureRow | None = None
    last: FeatureRow | None = None
    count = 0
    for row, segment_start in _validated_rows(rows, registry):
        if segment_start:
            first = None
            last = None
            count = 0
        if first is None:
            first = row
        last = row
        count += 1
        if count == window_width:
            yield make_event(
                EventKind.FIXED_WINDOW,
                first.timestamp,
                last.information_cutoff,
                last,
                trigger_version,
                registry=registry,
                metadata={"window_bars": window_width},
            )
            first = None
            last = None
            count = 0


def segment_rolling_windows(
    rows: Iterable[FeatureRow],
    *,
    width: int,
    step: int,
    trigger_version: str,
    registry: FeatureRegistry,
) -> Iterator[MarketEvent]:
    """Emit explicitly exploratory full rolling windows at a fixed bar step."""

    window_width = _positive_integer(width, "width")
    window_step = _positive_integer(step, "step")
    active: deque[FeatureRow] = deque(maxlen=window_width)
    observations = 0
    for row, segment_start in _validated_rows(rows, registry):
        if segment_start:
            active.clear()
            observations = 0
        active.append(row)
        observations += 1
        if len(active) != window_width or (observations - window_width) % window_step != 0:
            continue
        yield make_event(
            EventKind.ROLLING_WINDOW,
            active[0].timestamp,
            row.information_cutoff,
            row,
            trigger_version,
            registry=registry,
            exploratory=True,
            metadata={"step_bars": window_step, "window_bars": window_width},
        )


def _session_key(timestamp: datetime, unit: UTCSessionUnit) -> date | tuple[int, int]:
    utc_timestamp = timestamp.astimezone(UTC)
    if unit is UTCSessionUnit.DAY:
        return utc_timestamp.date()
    if unit is UTCSessionUnit.WEEK:
        value = utc_timestamp.date()
        return value - timedelta(days=value.weekday())
    return utc_timestamp.year, utc_timestamp.month


def _session_label(key: date | tuple[int, int]) -> str:
    if isinstance(key, date):
        return key.isoformat()
    return f"{key[0]:04d}-{key[1]:02d}"


def segment_utc_sessions(
    rows: Iterable[FeatureRow],
    *,
    unit: UTCSessionUnit,
    trigger_version: str,
    registry: FeatureRegistry,
) -> Iterator[MarketEvent]:
    """Emit observed portions of calendar-aligned UTC sessions."""

    if not isinstance(unit, UTCSessionUnit):
        raise TypeError("unit must be a UTCSessionUnit")
    first: FeatureRow | None = None
    last: FeatureRow | None = None
    key: date | tuple[int, int] | None = None
    count = 0

    def emit() -> MarketEvent:
        if first is None or last is None or key is None:
            raise RuntimeError("cannot emit an empty UTC session")
        return make_event(
            EventKind.UTC_SESSION,
            first.timestamp,
            last.information_cutoff,
            last,
            trigger_version,
            registry=registry,
            metadata={
                "observed_bars": count,
                "session": _session_label(key),
                "session_unit": unit.value,
            },
        )

    for row, segment_start in _validated_rows(rows, registry):
        row_key = _session_key(row.timestamp, unit)
        cutoff_key = _session_key(row.information_cutoff - timedelta(microseconds=1), unit)
        if cutoff_key != row_key:
            raise ValueError("a feature row crosses its declared UTC session boundary")
        if first is not None and (segment_start or row_key != key):
            yield emit()
            first = None
            last = None
            count = 0
        if first is None:
            first = row
            key = row_key
        last = row
        count += 1

    if first is not None:
        yield emit()
