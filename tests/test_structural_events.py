from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_structure_lab.auction import (
    AuctionCandle,
    AuctionEngine,
    GapPolicy,
    RollingBars,
    StructuralEventKind,
    UTCDayWindow,
)
from market_structure_lab.profiles import FixedStepBins, UniformAllocation


def candle(
    minute: int, price: float, volume: float = 10.0, *, segment_id: int = 0
) -> AuctionCandle:
    return AuctionCandle(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        symbol="BTCUSDT",
        timeframe="1m",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
        segment_id=segment_id,
    )


def engine() -> AuctionEngine:
    return AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=RollingBars(max_bars=10),
        dataset_version="events-fixture-v1",
        config_version="events-config-v1",
        gap_policy=GapPolicy.RESET,
    )


def test_value_breakout_and_reentry_are_emitted_when_observable() -> None:
    auction = engine()
    first = auction.update(candle(0, 100.0, 1_000.0))
    breakout = auction.update(candle(1, 110.0, 1.0))
    reentry = auction.update(candle(2, 100.0, 1.0))

    assert first is not None and breakout is not None and reentry is not None
    assert StructuralEventKind.VALUE_BREAKOUT in {event.kind for event in breakout.events}
    assert StructuralEventKind.VALUE_REENTRY in {event.kind for event in reentry.events}
    assert all(event.timestamp == breakout.timestamp for event in breakout.events)
    assert all(isinstance(event.payload, tuple) for event in (*breakout.events, *reentry.events))


def test_event_ids_are_stable_across_exact_replay() -> None:
    candles = (candle(0, 100.0, 10.0), candle(1, 101.0, 20.0), candle(2, 102.0, 30.0))

    first = engine().replay(candles)
    second = engine().replay(candles)

    assert [[event.event_id for event in snapshot.events] for snapshot in first] == [
        [event.event_id for event in snapshot.events] for snapshot in second
    ]


def test_no_migration_or_market_event_crosses_a_segment_reset() -> None:
    auction = engine()
    auction.update(candle(0, 100.0, segment_id=0))
    reset = auction.update(candle(1, 110.0, segment_id=1))

    assert reset is not None
    assert reset.migration is None
    assert [event.kind for event in reset.events] == [StructuralEventKind.SEGMENT_RESET]


def test_node_zone_persistence_is_exposed_by_engine_snapshots() -> None:
    auction = engine()
    auction.update(candle(0, 100.0, 10.0))
    auction.update(candle(1, 101.0, 30.0))
    third = auction.update(candle(2, 102.0, 10.0))
    fourth = auction.update(candle(3, 101.0, 5.0))

    assert third is not None and fourth is not None
    assert [(node.representative_index, node.persistence) for node in third.nodes] == [(101, 1)]
    assert [(node.representative_index, node.persistence) for node in fourth.nodes] == [(101, 2)]


def test_utc_session_boundary_resets_profile_migration_events_and_node_persistence() -> None:
    start = datetime(2025, 1, 1, 23, 57, tzinfo=UTC)

    def at(minute: int, price: float, volume: float) -> AuctionCandle:
        return AuctionCandle(
            timestamp=start + timedelta(minutes=minute),
            symbol="BTCUSDT",
            timeframe="1m",
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            segment_id=0,
        )

    auction = AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=UTCDayWindow(),
        dataset_version="session-fixture-v1",
        config_version="session-config-v1",
    )
    auction.update(at(0, 100.0, 10.0))
    auction.update(at(1, 101.0, 30.0))
    before = auction.update(at(2, 102.0, 10.0))
    reset = auction.update(at(3, 101.0, 5.0))
    auction.update(at(4, 100.0, 10.0))
    after = auction.update(at(5, 102.0, 10.0))

    assert before is not None and reset is not None and after is not None
    assert [(node.representative_index, node.persistence) for node in before.nodes] == [(101, 1)]
    assert reset.profile.bin_volumes == {101: 5.0}
    assert reset.migration is None
    assert [event.kind for event in reset.events] == [StructuralEventKind.WINDOW_RESET]
    assert [(node.representative_index, node.persistence) for node in after.nodes] == [(101, 1)]
