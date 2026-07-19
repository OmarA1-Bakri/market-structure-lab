"""Explicit bounded policies that select candles for auction profiles.

Policies decide membership only. They do not calculate profiles or inspect future
candles. The engine validates stream ordering and applies each returned transition
to its profile accumulator.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import takewhile
from typing import Callable, Protocol, runtime_checkable

from market_structure_lab.auction.models import AuctionCandle, normalize_utc_timestamp


_MAXIMUM_ACTIVE_WINDOW_CANDLES = 100_000
_MAXIMUM_WINDOW_DURATION = timedelta(days=3_653)


def _require_safe_active_count(
    active_count: int,
    *,
    eviction_count: int,
    reset: bool,
    include: bool,
) -> None:
    projected = (0 if reset else active_count - eviction_count) + int(include)
    if projected > _MAXIMUM_ACTIVE_WINDOW_CANDLES:
        raise ValueError(
            "window active candle count exceeds the safe maximum of "
            f"{_MAXIMUM_ACTIVE_WINDOW_CANDLES}"
        )


def _require_duration_capacity(
    duration: timedelta,
    expected_interval: timedelta,
    *,
    label: str,
) -> None:
    if not isinstance(expected_interval, timedelta) or expected_interval <= timedelta(0):
        raise ValueError("expected_interval must be a positive timedelta")
    duration_us = duration // timedelta(microseconds=1)
    interval_us = expected_interval // timedelta(microseconds=1)
    projected = (duration_us + interval_us - 1) // interval_us
    if projected > _MAXIMUM_ACTIVE_WINDOW_CANDLES:
        raise ValueError(
            f"{label} can retain {projected} candles at the expected interval, "
            f"exceeding the safe maximum of {_MAXIMUM_ACTIVE_WINDOW_CANDLES}"
        )


@dataclass(frozen=True, slots=True)
class WindowTransition:
    """One deterministic membership change for the current input candle."""

    include: bool
    reset: bool
    evictions: tuple[AuctionCandle, ...]
    window_id: str


class WindowPolicyCheckpoint:
    """Bounded inverse-operation journal for one caller-owned window policy."""

    def __init__(self, owner: _TransactionalWindowPolicy) -> None:
        self._owner = owner
        self._undo: list[Callable[[], None]] = []
        self._commit_requested = False
        self._finalize_requested = False
        self._closed = False

    def record(self, undo: Callable[[], None]) -> None:
        if self._closed or self._commit_requested:
            raise RuntimeError("window policy checkpoint is closed")
        self._undo.append(undo)

    def commit(self) -> None:
        if self._closed or self._owner._window_transaction is not self:
            raise RuntimeError("window policy checkpoint is not active")
        self._commit_requested = True

    def finalize(self) -> None:
        """Validate finalization while retaining rollback until every collaborator is ready."""

        if (
            self._closed
            or not self._commit_requested
            or self._owner._window_transaction is not self
        ):
            raise RuntimeError("window policy checkpoint is not ready to finalize")
        self._finalize_requested = True

    def release(self) -> None:
        """Release the prepared journal idempotently after validated finalization."""

        if self._closed:
            return
        self._finish()

    def ensure_released(self) -> None:
        """Force-detach a committed journal after an unexpected release failure."""

        self._finish()

    def rollback(self) -> None:
        if self._closed:
            raise RuntimeError("window policy checkpoint is closed")
        failures: list[Exception] = []
        try:
            for undo in reversed(self._undo):
                try:
                    undo()
                except Exception as error:
                    failures.append(error)
        finally:
            self._finish()
        if failures:
            rollback_error = RuntimeError("window policy rollback failed")
            for failure in failures:
                rollback_error.add_note(f"{type(failure).__name__}: {failure}")
            raise rollback_error from failures[0]

    def _finish(self) -> None:
        self._undo.clear()
        self._closed = True
        if self._owner._window_transaction is self:
            self._owner._window_transaction = None


class _TransactionalWindowPolicy:
    """Identity-preserving transaction support shared by built-in policies."""

    __slots__ = ("_window_transaction",)

    def __init__(self) -> None:
        self._window_transaction: WindowPolicyCheckpoint | None = None

    def transaction_checkpoint(self) -> WindowPolicyCheckpoint:
        if self._window_transaction is not None:
            raise RuntimeError("window policy transaction is already active")
        checkpoint = WindowPolicyCheckpoint(self)
        self._window_transaction = checkpoint
        return checkpoint

    def restore_transaction(self, checkpoint: object) -> None:
        if checkpoint is not self._window_transaction or not isinstance(
            checkpoint, WindowPolicyCheckpoint
        ):
            raise ValueError("window policy checkpoint does not match the active transaction")
        checkpoint.rollback()

    def _record_undo(self, undo: Callable[[], None]) -> None:
        if self._window_transaction is not None:
            self._window_transaction.record(undo)

    def _clear_active(self, active: deque[AuctionCandle] | list[AuctionCandle]) -> None:
        previous = tuple(active)
        active.clear()
        self._record_undo(lambda: active.extend(previous))

    def _append_active(
        self,
        active: deque[AuctionCandle] | list[AuctionCandle],
        candle: AuctionCandle,
    ) -> None:
        active.append(candle)

        def undo_append() -> None:
            active.pop()

        self._record_undo(undo_append)

    def _popleft_active(self, active: deque[AuctionCandle]) -> AuctionCandle:
        removed = active.popleft()

        def undo_removal() -> None:
            active.appendleft(removed)

        self._record_undo(undo_removal)
        return removed

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition:
        raise NotImplementedError

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        raise NotImplementedError

    def _transactional_transition(self, candle: AuctionCandle) -> WindowTransition:
        checkpoint = self.transaction_checkpoint()
        try:
            transition = self.plan_transition(candle)
            self.commit_transition(candle, transition)
            checkpoint.commit()
            checkpoint.finalize()
            checkpoint.release()
            return transition
        except Exception as error:
            try:
                self.restore_transaction(checkpoint)
            except Exception as restoration_error:
                error.add_note(
                    "window policy restore failed: "
                    f"{type(restoration_error).__name__}: {restoration_error}"
                )
            raise


@runtime_checkable
class WindowPolicy(Protocol):
    """Caller-owned policy with pure planning and deterministic in-place commit.

    ``plan_transition`` must not mutate policy state. ``commit_transition``
    applies an unchanged plan in place. Its mutations remain rollback-capable
    until the engine commits the checkpoint after every other fallible stage.
    """

    @property
    def version_id(self) -> str: ...

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]: ...

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition: ...

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None: ...

    def transaction_checkpoint(self) -> WindowPolicyCheckpoint: ...

    def restore_transaction(self, checkpoint: object) -> None: ...

    def validate_expected_interval(self, expected_interval: timedelta) -> None: ...

    def transition(self, candle: AuctionCandle) -> WindowTransition: ...

    def reset(self) -> None: ...


class RollingBars(_TransactionalWindowPolicy):
    """Retain exactly the latest configured number of included bars."""

    __slots__ = ("_active", "_max_bars", "_version_id")

    def __init__(self, *, max_bars: int) -> None:
        super().__init__()
        if isinstance(max_bars, bool) or not isinstance(max_bars, int):
            raise TypeError("max_bars must be an integer")
        if max_bars < 1:
            raise ValueError("max_bars must be positive")
        if max_bars > _MAXIMUM_ACTIVE_WINDOW_CANDLES:
            raise ValueError(
                f"max_bars exceeds the safe maximum of {_MAXIMUM_ACTIVE_WINDOW_CANDLES}"
            )
        self._max_bars = max_bars
        self._active: deque[AuctionCandle] = deque()
        self._version_id = f"rolling-bars-v1:max-bars={max_bars}"

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition:
        evictions = () if reset_before or len(self._active) < self._max_bars else (self._active[0],)
        _require_safe_active_count(
            len(self._active),
            eviction_count=len(evictions),
            reset=reset_before,
            include=True,
        )
        return WindowTransition(
            include=True,
            reset=False,
            evictions=evictions,
            window_id=self.version_id,
        )

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        if transition != self.plan_transition(candle, reset_before=reset_before):
            raise ValueError("window transition no longer matches current policy state")
        if reset_before:
            self.reset()
        for evicted in transition.evictions:
            removed = self._popleft_active(self._active)
            if removed != evicted:
                raise RuntimeError("rolling window eviction plan became inconsistent")
        if transition.include:
            self._append_active(self._active, candle)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        return self._transactional_transition(candle)

    def reset(self) -> None:
        self._clear_active(self._active)

    def validate_expected_interval(self, expected_interval: timedelta) -> None:
        _require_duration_capacity(
            expected_interval * self._max_bars,
            expected_interval,
            label="rolling-bars window",
        )


class RollingDuration(_TransactionalWindowPolicy):
    """Retain candle timestamps in ``(current - duration, current]``."""

    __slots__ = ("_active", "_duration", "_version_id")

    def __init__(self, *, duration: timedelta) -> None:
        super().__init__()
        if not isinstance(duration, timedelta):
            raise TypeError("duration must be a timedelta")
        if duration <= timedelta(0):
            raise ValueError("duration must be positive")
        if duration > _MAXIMUM_WINDOW_DURATION:
            raise ValueError(
                f"duration exceeds the safe maximum of {_MAXIMUM_WINDOW_DURATION.days} days"
            )
        duration_microseconds = (
            duration.days * 86_400_000_000 + duration.seconds * 1_000_000 + duration.microseconds
        )
        self._duration = duration
        self._active: deque[AuctionCandle] = deque()
        self._version_id = f"rolling-duration-v1:microseconds={duration_microseconds}"

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition:
        cutoff = candle.timestamp - self._duration
        evicted = (
            ()
            if reset_before
            else tuple(takewhile(lambda item: item.timestamp <= cutoff, self._active))
        )
        _require_safe_active_count(
            len(self._active),
            eviction_count=len(evicted),
            reset=reset_before,
            include=True,
        )
        return WindowTransition(
            include=True,
            reset=False,
            evictions=evicted,
            window_id=self.version_id,
        )

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        if transition != self.plan_transition(candle, reset_before=reset_before):
            raise ValueError("window transition no longer matches current policy state")
        if reset_before:
            self.reset()
        for evicted in transition.evictions:
            removed = self._popleft_active(self._active)
            if removed != evicted:
                raise RuntimeError("rolling window eviction plan became inconsistent")
        if transition.include:
            self._append_active(self._active, candle)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        return self._transactional_transition(candle)

    def reset(self) -> None:
        self._clear_active(self._active)

    def validate_expected_interval(self, expected_interval: timedelta) -> None:
        _require_duration_capacity(
            self._duration,
            expected_interval,
            label="rolling-duration window",
        )


class FixedWindow(_TransactionalWindowPolicy):
    """Include only candles in one immutable half-open UTC range ``[start, end)``."""

    __slots__ = ("_active", "_end", "_start", "_version_id")

    def __init__(self, *, start: datetime, end: datetime) -> None:
        super().__init__()
        normalized_start = normalize_utc_timestamp(start)
        normalized_end = normalize_utc_timestamp(end)
        if normalized_start >= normalized_end:
            raise ValueError("fixed window start must be before end")
        if normalized_end - normalized_start > _MAXIMUM_WINDOW_DURATION:
            raise ValueError(
                "fixed window range exceeds the safe maximum of "
                f"{_MAXIMUM_WINDOW_DURATION.days} days"
            )
        self._start = normalized_start
        self._end = normalized_end
        self._active: list[AuctionCandle] = []
        self._version_id = (
            f"fixed-window-v1:start={normalized_start.isoformat()};end={normalized_end.isoformat()}"
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

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition:
        if candle.timestamp < self._start:
            return WindowTransition(
                include=False,
                reset=False,
                evictions=(),
                window_id=self.version_id,
            )
        if candle.timestamp >= self._end:
            raise ValueError("candle timestamp is beyond fixed window end")
        _require_safe_active_count(
            len(self._active),
            eviction_count=0,
            reset=reset_before,
            include=True,
        )
        return WindowTransition(
            include=True,
            reset=False,
            evictions=(),
            window_id=self.version_id,
        )

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        if transition != self.plan_transition(candle, reset_before=reset_before):
            raise ValueError("window transition no longer matches current policy state")
        if reset_before:
            self.reset()
        if transition.include:
            self._append_active(self._active, candle)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        return self._transactional_transition(candle)

    def reset(self) -> None:
        self._clear_active(self._active)

    def validate_expected_interval(self, expected_interval: timedelta) -> None:
        _require_duration_capacity(
            self._end - self._start,
            expected_interval,
            label="fixed window",
        )


class _UTCSessionWindow(_TransactionalWindowPolicy):
    __slots__ = ("_active", "_session_key")

    _version_id: str
    _maximum_session_duration: timedelta

    def __init__(self) -> None:
        super().__init__()
        self._active: list[AuctionCandle] = []
        self._session_key: date | tuple[int, int] | None = None

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def active_candles(self) -> tuple[AuctionCandle, ...]:
        return tuple(self._active)

    def plan_transition(
        self,
        candle: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> WindowTransition:
        session_key = self._key(candle.timestamp)
        reset = (
            not reset_before and self._session_key is not None and session_key != self._session_key
        )
        evictions = tuple(self._active) if reset else ()
        _require_safe_active_count(
            len(self._active),
            eviction_count=len(evictions),
            reset=reset_before or reset,
            include=True,
        )
        return WindowTransition(
            include=True,
            reset=reset,
            evictions=evictions,
            window_id=self._window_id(session_key),
        )

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        if transition != self.plan_transition(candle, reset_before=reset_before):
            raise ValueError("window transition no longer matches current policy state")
        if reset_before or transition.reset:
            self.reset()
        previous_key = self._session_key
        self._session_key = self._key(candle.timestamp)
        self._record_undo(lambda: setattr(self, "_session_key", previous_key))
        if transition.include:
            self._append_active(self._active, candle)

    def transition(self, candle: AuctionCandle) -> WindowTransition:
        return self._transactional_transition(candle)

    def reset(self) -> None:
        previous_key = self._session_key
        self._clear_active(self._active)
        self._session_key = None
        self._record_undo(lambda: setattr(self, "_session_key", previous_key))

    def _key(self, timestamp: datetime) -> date | tuple[int, int]:
        raise NotImplementedError

    def _window_id(self, key: date | tuple[int, int]) -> str:
        if isinstance(key, date):
            label = key.isoformat()
        else:
            label = f"{key[0]:04d}-{key[1]:02d}"
        return f"{self.version_id}:session={label}"

    def validate_expected_interval(self, expected_interval: timedelta) -> None:
        _require_duration_capacity(
            self._maximum_session_duration,
            expected_interval,
            label=self.version_id,
        )


class UTCDayWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC each calendar day."""

    __slots__ = ()
    _version_id = "utc-day-window-v1"
    _maximum_session_duration = timedelta(days=1)

    def _key(self, timestamp: datetime) -> date:
        return timestamp.astimezone(UTC).date()


class UTCWeekWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC each Monday."""

    __slots__ = ()
    _version_id = "utc-week-window-v1:monday-start"
    _maximum_session_duration = timedelta(days=7)

    def _key(self, timestamp: datetime) -> date:
        utc_date = timestamp.astimezone(UTC).date()
        return utc_date - timedelta(days=utc_date.weekday())


class UTCMonthWindow(_UTCSessionWindow):
    """Reset at 00:00 UTC on the first day of each calendar month."""

    __slots__ = ()
    _version_id = "utc-month-window-v1"
    _maximum_session_duration = timedelta(days=31)

    def _key(self, timestamp: datetime) -> tuple[int, int]:
        utc_timestamp = timestamp.astimezone(UTC)
        return utc_timestamp.year, utc_timestamp.month
