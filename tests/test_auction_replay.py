from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.auction.engine import (
    AuctionEngine,
    AuctionLocation,
    GapPolicy,
    StructuralEventKind,
)
from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.auction.windows import FixedWindow, RollingBars, RollingDuration
from market_structure_lab.profiles.allocation import (
    AllocationModel,
    TypicalPriceAllocation,
    UniformAllocation,
)
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle as ProfileCandle
from market_structure_lab.profiles.volume import calculate_profile


def candle(
    minute: int,
    *,
    price: float = 100.0,
    volume: float = 10.0,
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
) -> AuctionCandle:
    return AuctionCandle(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        symbol=symbol,
        timeframe=timeframe,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
        segment_id=segment_id,
    )


def engine(*, gap_policy: GapPolicy = GapPolicy.REJECT, max_bars: int = 3) -> AuctionEngine:
    return AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=RollingBars(max_bars=max_bars),
        value_area_fraction=0.70,
        expected_interval=timedelta(minutes=1),
        gap_policy=gap_policy,
        dataset_version="fixture-dataset-v1",
        config_version="fixture-auction-v1",
    )


def test_rolling_engine_evicts_exact_cached_contribution() -> None:
    auction = engine()
    for item in (
        candle(0, price=100.0, volume=100.0),
        candle(1, price=101.0, volume=20.0),
        candle(2, price=102.0, volume=30.0),
    ):
        auction.update(item)
    snapshot = auction.update(candle(3, price=103.0, volume=40.0))

    assert snapshot.profile.bin_volumes == {101: 20.0, 102: 30.0, 103: 40.0}
    assert snapshot.profile.total_volume == 90.0
    assert snapshot.profile.poc_index == 103
    assert snapshot.profile.value_area_low_index == 102
    assert snapshot.profile.value_area_high_index == 103
    assert snapshot.profile.vwap == pytest.approx(102.22222222222223)
    assert snapshot.location is AuctionLocation.POINT_OF_CONTROL
    assert snapshot.active_timestamps == tuple(candle(i).timestamp for i in (1, 2, 3))
    assert snapshot.candle_count == 3


def test_duplicate_out_of_order_and_series_changes_are_atomic() -> None:
    auction = engine()
    first = auction.update(candle(0))

    for invalid, message in (
        (candle(0), "duplicate"),
        (candle(-1), "out-of-order"),
        (candle(1, symbol="ETHUSDT"), "symbol"),
        (candle(1, timeframe="5m"), "timeframe"),
    ):
        with pytest.raises(ValueError, match=message):
            auction.update(invalid)
        assert auction.latest_snapshot == first
        assert auction.active_count == 1


def test_gap_rejects_by_default_without_mutating_state() -> None:
    auction = engine()
    first = auction.update(candle(0))

    with pytest.raises(ValueError, match="gap"):
        auction.update(candle(2))

    assert auction.latest_snapshot == first
    assert auction.active_count == 1


def test_explicit_gap_reset_never_carries_profile_or_migration_across_gap() -> None:
    auction = engine(gap_policy=GapPolicy.RESET)
    auction.update(candle(0, price=100.0))
    snapshot = auction.update(candle(2, price=110.0))

    assert snapshot.candle_count == 1
    assert snapshot.profile.bin_volumes == {110: 10.0}
    assert snapshot.migration is None
    assert [event.kind for event in snapshot.events] == [StructuralEventKind.GAP_RESET]


def test_segment_change_is_an_explicit_hard_reset() -> None:
    auction = engine()
    auction.update(candle(0, price=100.0, segment_id=0))
    snapshot = auction.update(candle(1, price=110.0, segment_id=1))

    assert snapshot.profile.bin_volumes == {110: 10.0}
    assert snapshot.migration is None
    assert [event.kind for event in snapshot.events] == [StructuralEventKind.SEGMENT_RESET]


def test_replay_is_deterministic_and_prefix_invariant() -> None:
    prefix = tuple(candle(i, price=100.0 + i, volume=10.0 + i) for i in range(5))
    first = engine(max_bars=3).replay(prefix)
    second = engine(max_bars=3).replay(prefix)
    with_future = engine(max_bars=3).replay((*prefix, candle(5, price=1_000.0, volume=1_000.0)))

    assert first == second
    assert with_future[: len(prefix)] == first


def test_snapshots_remain_unchanged_after_future_updates() -> None:
    auction = engine()
    first = auction.update(candle(0, price=100.0))
    auction.update(candle(1, price=200.0))

    assert first.profile.bin_volumes == {100: 10.0}
    assert first.active_timestamps == (candle(0).timestamp,)


def test_zero_volume_boundaries_have_no_migration_or_spurious_poc_event() -> None:
    zero_to_positive = engine(max_bars=2)
    empty = zero_to_positive.update(candle(0, volume=0.0))
    positive = zero_to_positive.update(candle(1, volume=1.0))

    assert empty is not None and positive is not None
    assert empty.location is AuctionLocation.NO_VALUE
    assert positive.migration is None
    assert StructuralEventKind.POC_MIGRATION not in {event.kind for event in positive.events}

    positive_to_zero = engine(max_bars=1)
    positive_to_zero.update(candle(0, volume=1.0))
    emptied = positive_to_zero.update(candle(1, volume=0.0))

    assert emptied is not None
    assert emptied.location is AuctionLocation.NO_VALUE
    assert emptied.migration is None
    assert StructuralEventKind.POC_MIGRATION not in {event.kind for event in emptied.events}


def test_fixed_window_exclusions_still_advance_and_validate_stream_cursor() -> None:
    start = candle(2).timestamp
    auction = AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=FixedWindow(start=start, end=start + timedelta(minutes=2)),
        dataset_version="fixed-cursor-v1",
        config_version="fixed-cursor-v1",
    )
    assert auction.update(candle(0)) is None

    for invalid, message in (
        (candle(0), "duplicate"),
        (candle(-1), "out-of-order"),
        (candle(1, symbol="ETHUSDT"), "symbol"),
        (candle(1, timeframe="5m"), "timeframe"),
    ):
        with pytest.raises(ValueError, match=message):
            auction.update(invalid)
        assert auction.latest_snapshot is None
        assert auction.active_count == 0

    assert auction.update(candle(1)) is None
    assert auction.update(candle(2)) is not None


def test_rolling_duration_engine_evicts_expired_contributions() -> None:
    auction = AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=RollingDuration(duration=timedelta(minutes=2)),
        dataset_version="duration-v1",
        config_version="duration-v1",
    )
    auction.update(candle(0, price=100.0, volume=10.0))
    auction.update(candle(1, price=101.0, volume=20.0))
    snapshot = auction.update(candle(2, price=102.0, volume=30.0))

    assert snapshot is not None
    assert snapshot.profile.bin_volumes == {101: 20.0, 102: 30.0}
    assert snapshot.active_timestamps == (candle(1).timestamp, candle(2).timestamp)


@pytest.mark.parametrize("max_bars", [1, 2, 60])
@pytest.mark.parametrize("allocation", [UniformAllocation(), TypicalPriceAllocation()])
def test_incremental_rolling_profile_matches_full_recomputation(
    max_bars: int, allocation: AllocationModel
) -> None:
    bins = FixedStepBins(step=1.0)
    auction = AuctionEngine(
        binning=bins,
        allocation=allocation,
        window_policy=RollingBars(max_bars=max_bars),
        dataset_version="equivalence-v1",
        config_version="equivalence-v1",
    )
    active: list[AuctionCandle] = []
    for minute in range(512):
        low = 100.0 + minute % 20
        item = AuctionCandle(
            timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
            symbol="BTCUSDT",
            timeframe="1m",
            open=low,
            high=low + 2.0,
            low=low,
            close=low + 1.0,
            volume=float(1 + minute % 17),
            segment_id=0,
        )
        active.append(item)
        active = active[-max_bars:]
        snapshot = auction.update(item)
        assert snapshot is not None
        expected = calculate_profile(
            [
                allocation.allocate(
                    ProfileCandle(
                        open=value.open,
                        high=value.high,
                        low=value.low,
                        close=value.close,
                        volume=value.volume,
                    ),
                    bins,
                )
                for value in active
            ],
            binning=bins,
            allocation_id=allocation.model_id,
        )
        assert snapshot.profile.bin_volumes == pytest.approx(dict(expected.bin_volumes), abs=1e-12)
        assert snapshot.profile.total_volume == pytest.approx(expected.total_volume, abs=1e-12)
        assert snapshot.profile.vwap == pytest.approx(expected.vwap, abs=1e-12)
        assert snapshot.profile.poc_index == expected.poc_index
        assert snapshot.profile.value_area_low_index == expected.value_area_low_index
        assert snapshot.profile.value_area_high_index == expected.value_area_high_index
        assert snapshot.profile.binning_id == expected.binning_id
        assert snapshot.profile.allocation_id == expected.allocation_id
        assert snapshot.candle_count <= max_bars
