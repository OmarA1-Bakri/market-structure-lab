from __future__ import annotations

from src.auction import AuctionEngine, AuctionLocation
from src.profiles import Candle


def test_auction_engine_updates_profile_and_classifies_close_location() -> None:
    auction = AuctionEngine(tick_size=1.0, value_area_fraction=0.70)

    first = auction.update(Candle(open=100.0, high=102.0, low=100.0, close=100.0, volume=30.0))
    second = auction.update(Candle(open=101.0, high=103.0, low=101.0, close=104.0, volume=60.0))

    assert first.candle_count == 1
    assert first.profile.point_of_control == 100.0
    assert first.location is AuctionLocation.POINT_OF_CONTROL

    assert second.candle_count == 2
    assert second.profile.point_of_control == 101.0
    assert second.profile.value_area_low == 101.0
    assert second.profile.value_area_high == 103.0
    assert second.location is AuctionLocation.ABOVE_VALUE


def test_auction_engine_snapshot_is_stable_between_updates() -> None:
    auction = AuctionEngine(tick_size=1.0)

    first = auction.update(Candle(open=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))
    auction.update(Candle(open=105.0, high=105.0, low=105.0, close=105.0, volume=90.0))

    assert first.candle_count == 1
    assert first.profile.price_volumes == {100.0: 10.0}
    assert first.profile.point_of_control == 100.0


def test_auction_engine_can_reset_without_reusing_old_state() -> None:
    auction = AuctionEngine(tick_size=1.0)
    auction.update(Candle(open=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))

    auction.reset()
    snapshot = auction.update(Candle(open=105.0, high=105.0, low=105.0, close=105.0, volume=90.0))

    assert snapshot.candle_count == 1
    assert snapshot.profile.price_volumes == {105.0: 90.0}
    assert snapshot.location is AuctionLocation.POINT_OF_CONTROL
