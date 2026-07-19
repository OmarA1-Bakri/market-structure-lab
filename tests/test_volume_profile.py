from __future__ import annotations

import pytest

from market_structure_lab.profiles import Candle, ProfileWorkBudget, build_volume_profile


def test_build_volume_profile_allocates_candle_volume_across_touched_price_bins() -> None:
    profile = build_volume_profile(
        [
            Candle(open=100.0, high=102.0, low=100.0, close=101.0, volume=30.0),
            Candle(open=101.0, high=103.0, low=101.0, close=102.0, volume=60.0),
        ],
        tick_size=1.0,
        value_area_fraction=0.70,
    )

    assert profile.price_volumes == {
        100.0: 10.0,
        101.0: 30.0,
        102.0: 30.0,
        103.0: 20.0,
    }
    assert profile.total_volume == 90.0
    assert profile.point_of_control == 101.0
    assert profile.value_area_low == 101.0
    assert profile.value_area_high == 103.0


def test_build_volume_profile_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="tick_size"):
        build_volume_profile([], tick_size=0.0)

    with pytest.raises(ValueError, match="value_area_fraction"):
        build_volume_profile([], tick_size=1.0, value_area_fraction=1.5)

    with pytest.raises(ValueError, match="high"):
        build_volume_profile(
            [Candle(open=100.0, high=99.0, low=100.0, close=100.0, volume=10.0)],
            tick_size=1.0,
        )


def test_empty_volume_profile_has_no_auction_references() -> None:
    profile = build_volume_profile([], tick_size=1.0)

    assert profile.price_volumes == {}
    assert profile.total_volume == 0.0
    assert profile.point_of_control is None
    assert profile.value_area_low is None
    assert profile.value_area_high is None


def test_build_volume_profile_rejects_pathological_range_before_bin_materialization() -> None:
    budget = ProfileWorkBudget(
        maximum_touched_bins_per_candle=8,
        maximum_active_profile_bins=8,
    )

    with pytest.raises(ValueError, match="touched-bin budget"):
        build_volume_profile(
            [Candle(open=0.0, high=10**12, low=0.0, close=10**12, volume=1.0)],
            tick_size=0.000_001,
            work_budget=budget,
        )
