from __future__ import annotations

from types import MappingProxyType

import pytest

from market_structure_lab.profiles.allocation import BinContribution, TypicalPriceAllocation
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle, ProfileSnapshot
from market_structure_lab.profiles.volume import calculate_profile, point_of_control_index, value_area_indices


def test_point_of_control_tie_prefers_lower_integer_bin() -> None:
    assert point_of_control_index({4: 10.0, 7: 10.0, 5: 2.0}) == 4


def test_value_area_side_tie_prefers_upper_and_remains_contiguous() -> None:
    low, high = value_area_indices(
        {0: 1.0, 2: 5.0, 4: 1.0},
        point_of_control=2,
        target_volume=6.0,
    )

    assert (low, high) == (2, 4)


def test_sparse_profile_expands_through_zero_volume_bins() -> None:
    low, high = value_area_indices(
        {0: 4.0, 3: 10.0},
        point_of_control=3,
        target_volume=14.0,
    )

    assert (low, high) == (0, 3)


def test_sparse_profile_runtime_is_bounded_by_observed_bins_not_index_span() -> None:
    low, high = value_area_indices(
        {0: 60.0, 1_000_000_000: 40.0},
        point_of_control=0,
        target_volume=90.0,
    )

    assert (low, high) == (0, 1_000_000_000)


def test_profile_snapshot_is_immutable_and_uses_only_integer_keys() -> None:
    snapshot = calculate_profile(
        [BinContribution({100: 10.0}, 10.0, 1000.0)],
        binning=FixedStepBins(step=1.0),
        allocation_id="fixture-v1",
    )

    assert isinstance(snapshot, ProfileSnapshot)
    assert all(isinstance(index, int) for index in snapshot.bin_volumes)
    assert isinstance(snapshot.bin_volumes, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.bin_volumes[101] = 5.0  # type: ignore[index]


def test_profile_vwap_uses_allocation_price_numerator() -> None:
    candle = Candle(open=100.0, high=103.0, low=100.0, close=102.0, volume=20.0)
    bins = FixedStepBins(step=1.0)
    contribution = TypicalPriceAllocation().allocate(candle, bins)

    snapshot = calculate_profile(
        [contribution],
        binning=bins,
        allocation_id=TypicalPriceAllocation().model_id,
    )

    assert snapshot.vwap == pytest.approx((100 + 103 + 102) / 3)
    assert snapshot.point_of_control == 101.0


def test_empty_and_zero_volume_profiles_have_explicit_empty_references() -> None:
    snapshot = calculate_profile([], binning=FixedStepBins(step=1.0), allocation_id="fixture-v1")

    assert snapshot.bin_volumes == {}
    assert snapshot.total_volume == 0.0
    assert snapshot.poc_index is None
    assert snapshot.value_area_low_index is None
    assert snapshot.value_area_high_index is None
    assert snapshot.vwap is None


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1])
def test_value_area_fraction_is_validated(fraction: float) -> None:
    with pytest.raises(ValueError, match="value_area_fraction"):
        calculate_profile(
            [],
            binning=FixedStepBins(step=1.0),
            allocation_id="fixture-v1",
            value_area_fraction=fraction,
        )
