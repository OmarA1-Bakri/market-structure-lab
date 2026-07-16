from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_structure_lab.auction import (
    AuctionCandle,
    AuctionEngine,
    AuctionLocation,
    RollingBars,
)
from market_structure_lab.profiles import FixedStepBins, UniformAllocation


def auction_engine() -> AuctionEngine:
    return AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=RollingBars(max_bars=10),
        dataset_version="fixture-v1",
        config_version="auction-v1",
    )


def candle(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    symbol: str = "BTCUSDT",
) -> AuctionCandle:
    return AuctionCandle(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        symbol=symbol,
        timeframe="1m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        segment_id=0,
    )


def test_auction_engine_updates_profile_and_classifies_close_location() -> None:
    auction = auction_engine()
    first = auction.update(candle(0, open_=100.0, high=102.0, low=100.0, close=100.0, volume=30.0))
    second = auction.update(candle(1, open_=101.0, high=104.0, low=101.0, close=104.0, volume=60.0))

    assert first is not None and second is not None
    assert first.candle_count == 1
    assert first.profile.poc_index == 100
    assert first.location is AuctionLocation.POINT_OF_CONTROL
    assert second.candle_count == 2
    assert second.profile.poc_index == 101
    assert second.location is AuctionLocation.ABOVE_VALUE


def test_auction_engine_snapshot_is_stable_between_updates() -> None:
    auction = auction_engine()
    first = auction.update(candle(0, open_=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))
    auction.update(candle(1, open_=105.0, high=105.0, low=105.0, close=105.0, volume=90.0))

    assert first is not None
    assert first.profile.bin_volumes == {100: 10.0}
    assert first.profile.poc_index == 100


def test_auction_engine_reset_allows_a_fresh_series_without_old_state() -> None:
    auction = auction_engine()
    auction.update(candle(0, open_=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))

    auction.reset()
    snapshot = auction.update(
        candle(
            0,
            open_=105.0,
            high=105.0,
            low=105.0,
            close=105.0,
            volume=90.0,
            symbol="ETHUSDT",
        )
    )

    assert snapshot is not None
    assert snapshot.candle_count == 1
    assert snapshot.profile.bin_volumes == {105: 90.0}
    assert snapshot.location is AuctionLocation.POINT_OF_CONTROL
