from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.auction.windows import (
    FixedWindow,
    RollingBars,
    RollingDuration,
    UTCDayWindow,
    UTCMonthWindow,
    UTCWeekWindow,
    WindowPolicy,
)


def candle(
    minute: int,
    *,
    timestamp: datetime | None = None,
    segment_id: int = 0,
) -> AuctionCandle:
    return AuctionCandle(
        timestamp=timestamp or datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        symbol="BTCUSDT",
        timeframe="1m",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=10.0,
        segment_id=segment_id,
    )


def test_auction_candle_is_deeply_frozen_and_normalizes_aware_timestamp_to_utc() -> None:
    source_timestamp = datetime(2025, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    value = candle(0, timestamp=source_timestamp)

    assert value.timestamp == datetime(2025, 1, 1, tzinfo=UTC)
    assert value.timestamp.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        value.close = 200.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"timestamp": datetime(2025, 1, 1)}, "timezone-aware"),
        ({"symbol": ""}, "symbol"),
        ({"timeframe": " "}, "timeframe"),
        ({"segment_id": -1}, "segment_id"),
        ({"open": float("nan")}, "finite"),
        ({"high": 98.0}, "high"),
        ({"low": 101.5}, "low"),
        ({"volume": -0.1}, "volume"),
        ({"open": -1.0, "low": -2.0}, "non-negative"),
    ],
)
def test_auction_candle_rejects_invalid_data(updates: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "timestamp": datetime(2025, 1, 1, tzinfo=UTC),
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 10.0,
        "segment_id": 0,
    }
    values.update(updates)

    with pytest.raises(ValueError, match=message):
        AuctionCandle(**values)  # type: ignore[arg-type]


def test_rolling_bars_emits_exact_oldest_eviction_and_stays_bounded() -> None:
    policy = RollingBars(max_bars=2)
    first, second, third = candle(0), candle(1), candle(2)

    assert policy.transition(first).evictions == ()
    assert policy.transition(second).evictions == ()
    transition = policy.transition(third)

    assert transition.include is True
    assert transition.reset is False
    assert transition.evictions == (first,)
    assert policy.active_candles == (second, third)
    assert policy.version_id == "rolling-bars-v1:max-bars=2"


def test_rolling_duration_uses_open_left_current_closed_interval() -> None:
    policy = RollingDuration(duration=timedelta(minutes=2))
    first, second, third = candle(0), candle(1), candle(2)
    policy.transition(first)
    policy.transition(second)

    transition = policy.transition(third)

    assert transition.evictions == (first,)
    assert policy.active_candles == (second, third)
    assert policy.version_id == "rolling-duration-v1:microseconds=120000000"


def test_fixed_window_is_half_open_utc_and_exclusions_do_not_mutate_state() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    policy = FixedWindow(start=start, end=start + timedelta(minutes=2))
    before = candle(-1)
    first = candle(0)
    second = candle(1)

    excluded = policy.transition(before)
    assert excluded.include is False
    assert policy.active_candles == ()
    assert policy.transition(first).include is True
    assert policy.transition(second).include is True
    assert policy.active_candles == (first, second)
    with pytest.raises(ValueError, match="beyond fixed window"):
        policy.transition(candle(2))
    assert policy.active_candles == (first, second)


def test_fixed_window_normalizes_bounds_and_has_stable_version_id() -> None:
    bangkok = timezone(timedelta(hours=7))
    policy = FixedWindow(
        start=datetime(2025, 1, 1, 7, tzinfo=bangkok),
        end=datetime(2025, 1, 1, 8, tzinfo=bangkok),
    )

    assert policy.start == datetime(2025, 1, 1, tzinfo=UTC)
    assert policy.end == datetime(2025, 1, 1, 1, tzinfo=UTC)
    assert policy.version_id == (
        "fixed-window-v1:start=2025-01-01T00:00:00+00:00;end=2025-01-01T01:00:00+00:00"
    )


@pytest.mark.parametrize(
    ("policy_type", "before", "after", "expected_id"),
    [
        (
            UTCDayWindow,
            datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            "utc-day-window-v1",
        ),
        (
            UTCWeekWindow,
            datetime(2025, 1, 5, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 6, tzinfo=UTC),
            "utc-week-window-v1:monday-start",
        ),
        (
            UTCMonthWindow,
            datetime(2025, 1, 31, 23, 59, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            "utc-month-window-v1",
        ),
    ],
)
def test_utc_session_windows_reset_at_calendar_boundary(
    policy_type: type[UTCDayWindow | UTCWeekWindow | UTCMonthWindow],
    before: datetime,
    after: datetime,
    expected_id: str,
) -> None:
    policy = policy_type()
    previous = candle(0, timestamp=before)
    current = candle(1, timestamp=after)

    first_transition = policy.transition(previous)
    transition = policy.transition(current)

    assert first_transition.reset is False
    assert transition.include is True
    assert transition.reset is True
    assert transition.evictions == (previous,)
    assert policy.active_candles == (current,)
    assert policy.version_id == expected_id


def test_policy_reset_clears_state_for_deterministic_replay() -> None:
    policy = RollingBars(max_bars=2)
    first = candle(0)
    original = policy.transition(first)

    policy.reset()
    replay = policy.transition(first)

    assert policy.active_candles == (first,)
    assert replay == original
    assert isinstance(policy, WindowPolicy)


def test_window_plan_is_pure_and_commit_preserves_policy_identity() -> None:
    policy = RollingBars(max_bars=2)
    first = candle(0)
    second = candle(1)
    policy.transition(first)
    active_container = policy._active

    planned = policy.plan_transition(second)

    assert policy.active_candles == (first,)
    policy.commit_transition(second, planned)
    assert policy.active_candles == (first, second)
    assert policy._active is active_container


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RollingBars(max_bars=10**12),
        lambda: RollingDuration(duration=timedelta(days=10**6)),
        lambda: FixedWindow(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(9999, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_pathological_window_ranges_fail_before_state_can_grow(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="safe maximum"):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RollingBars(max_bars=0),
        lambda: RollingBars(max_bars=True),
        lambda: RollingDuration(duration=timedelta(0)),
        lambda: FixedWindow(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 1, 2, tzinfo=UTC),
        ),
        lambda: FixedWindow(
            start=datetime(2025, 1, 2, tzinfo=UTC),
            end=datetime(2025, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_window_policies_reject_invalid_configuration(factory: Callable[[], object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()
