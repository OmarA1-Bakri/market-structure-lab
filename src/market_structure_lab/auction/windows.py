"""Explicit bounded policies that select candles for auction profiles.

Policies decide membership only. They do not calculate profiles or inspect future
candles. The engine validates stream ordering and applies each returned transition
to its profile accumulator.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable

from market_structure_lab.auction.models import AuctionCandle, normalize_utc_timestamp


@dataclass(frozen=True, slots=True)
class WindowTransition:
    """One deterministic membership change for the current input candle."""

    include: bool
    reset: bool
    evictions: tuple[AuctionCandle, ...]
    window_id: str


@runtime_checkable
class WindowPolicy(Protocol):
    """Stateful, resettable contract used by the candle-by-candle engine."""

    @property
    def version_id(self) -> str: ...

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]: ...

    def transition(self, candle: AuctionCandle) -> WindowTransition: ...

    def reset(self) -> None: ...


class RollingBars:
    """Retain exactly the latest configured number of included bars."""

    __slots__ = ("_active", "_max_bars", "_version_id")

    def __init__(self, *, max_bars: int) -> None:
        if isinstance(max_bars, bool) or not isinstance(max_bars, int):
            raise TypeError("max_bars must be an integer")
        if max_bars < 1:
            raise ValueError("max_bars must be positive")
        self._max_bars = max_bars
        self._active: deque[AuctionCandle] = deque()
        self._version_id = f"rolling-bars-v1:max-bars={max_bars}"

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        self._active.append(candle)
        evictions: tuple[AuctionCandle, ...] = ()
        if len(self._active) > self._max_bars:
            evictions = (self._active.popleft(),)
        return WindowTransition(
            include=True,
            reset=False,
            evictions=evictions,
            window_id=self.version_id,
        )

    def reset(self) -> None:
        self._active.clear()


class RollingDuration:
    """Retain candle timestamps in ``(current - duration, current]``."""

    __slots__ = ("_active", "_duration", "_version_id")

    def __init__(self, *, duration: timedelta) -> None:
        if not isinstance(duration, timedelta):
            raise TypeError("duration must be a timedelta")
        if duration <= timedelta(0):
            raise ValueError("duration must be positive")
        duration_microseconds = (
            duration.days * 86_400_000_000
            + duration.seconds * 1_000_000
            + duration.microseconds
        )
        self._duration = duration
        self._active: deque[AuctionCandle] = deque()
        self._version_id = (
            f"rolling-duration-v1:microseconds={duration_microseconds}"
        )

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        cutoff = candle.timestamp - self._duration
        evicted: list[AuctionCandle] = []
        while self._active and self._active[0].timestamp <= cutoff:
            evicted.append(self._active.popleft())
        self._active.append(candle)
        return WindowTransition(
            include=True,
            reset=False,
            evictions=tuple(evicted),
            window_id=self.version_id,
        )

    def reset(self) -> None:
        self._active.clear()


class FixedWindow:
    """Include only candles in one immutable half-open UTC range ``[start, end)``."""

    __slots__ = ("_active", "_end", "_start", "_version_id")

    def __init__(self, *, start: datetime, end: datetime) -> None:
        normalized_start = normalize_utc_timestamp(start)
        normalized_end = normalize_utc_timestamp(end)
        if normalized_start >= normalized_end:
            raise ValueError("fixed window start must be before end")
        self._start = normalized_start
        self._end = normalized_end
        self._active: list[AuctionCandle] = []
        self._version_id = (
            f"fixed-window-v1:start={normalized_start.isoformat()};"
            f"end={normalized_end.isoformat()}"
        )

    @property
    def start(self) -> datetime:
        return self._start

    @property
    def end(self) -> datetime:
        return self._end

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        if candle.timestamp < self._start:
            return WindowTransition(
                include=False,
                reset=False,
                evictions=(),
                window_id=self.version_id,
            )
        if candle.timestamp >= self._end:
            raise ValueError("candle timestamp is beyond fixed window end")
        self._active.append(candle)
        return WindowTransition(
            include=True,
            reset=False,
            evictions=(),
            window_id=self.version_id,
        )

    def reset(self) -> None:
        self._active.clear()


class _UTCSessionWindow:
    __slots__ = ("_active", "_session_key")

    _version_id: str

    def __init__(self) -> None:
        self._active: list[AuctionCandle] = []
        self._session_key: date | tuple[int, int] | None = None

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        session_key = self._key(candle.timestamp)
        reset = self._session_key is not None and session_key != self._session_key
        evictions = tuple(self._active) if reset else ()
        if reset:
            self._active.clear()
        self._session_key = session_key
        self._active.append(candle)
        return WindowTransition(
            include=True,
            reset=reset,
            evictions=evictions,
            window_id=self._window_id(session_key),
        )

    def reset(self) -> None:
        self._active.clear()
        self._session_key = None

    def _key(self, timestamp: datetime) -> date | tuple[int, int]:
        raise NotImplementedError

    def _window_id(self, key: date | tuple[int, int]) -> str:
        if isinstance(key, date):
            label = key.isoformat()
        else:
            label = f"{key[0]:04d}-{key[1]:02d}"
        return f"{self.version_id}:session={label}"


class UTCDayWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC each calendar day."""

    __slots__ = ()
    _version_id = "utc-day-window-v1"

    def _key(self, timestamp: datetime) -> date:
        return timestamp.astimezone(UTC).date()


class UTCWeekWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC each Monday."""

    __slots__ = ()
    _version_id = "utc-week-window-v1:monday-start"

    def _key(self, timestamp: datetime) -> date:
        utc_date = timestamp.astimezone(UTC).date()
        return utc_date - timedelta(days=utc_date.weekday())


class UTCMonthWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC on the first day of each calendar month."""

    __slots__ = ()
    _version_id = "utc-month-window-v1"

    def _key(self, timestamp: datetime) -> tuple[int, int]:
        utc_timestamp = timestamp.astimezone(UTC)
        return utc_timestamp.year, utc_timestamp.month
