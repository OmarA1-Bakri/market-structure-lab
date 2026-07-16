from __future__ import annotations

import pytest

from market_structure_lab.profiles.accumulator import ProfileAccumulator
from market_structure_lab.profiles.allocation import UniformAllocation
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle
from market_structure_lab.profiles.volume import calculate_profile


def _candle(low: float, high: float, volume: float) -> Candle:
    return Candle(open=low, high=high, low=low, close=high, volume=volume)


def test_cached_add_remove_matches_full_recomputation() -> None:
    bins = FixedStepBins(step=1.0)
    allocation = UniformAllocation()
    candles = (_candle(100.0, 102.0, 30.0), _candle(101.0, 103.0, 60.0))
    accumulator = ProfileAccumulator(binning=bins, allocation=allocation)

    first = accumulator.add(candles[0])
    second = accumulator.add(candles[1])
    assert accumulator.snapshot() == calculate_profile(
        [first, second],
        binning=bins,
        allocation_id=allocation.model_id,
    )

    accumulator.remove(first)
    assert accumulator.snapshot() == calculate_profile(
        [second],
        binning=bins,
        allocation_id=allocation.model_id,
    )


def test_add_returns_cached_contribution_that_can_be_removed_without_reallocation() -> None:
    class CountingAllocation(UniformAllocation):
        calls = 0

        def allocate(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().allocate(*args, **kwargs)  # type: ignore[arg-type]

    allocation = CountingAllocation()
    accumulator = ProfileAccumulator(binning=FixedStepBins(step=1.0), allocation=allocation)
    contribution = accumulator.add(_candle(100.0, 101.0, 10.0))

    accumulator.remove(contribution)

    assert allocation.calls == 1
    assert accumulator.snapshot().total_volume == 0.0


def test_removing_unowned_or_twice_removed_contribution_fails_loudly() -> None:
    accumulator = ProfileAccumulator(
        binning=FixedStepBins(step=1.0), allocation=UniformAllocation()
    )
    contribution = UniformAllocation().allocate(
        _candle(100.0, 101.0, 10.0), FixedStepBins(step=1.0)
    )

    with pytest.raises(ValueError, match="not active"):
        accumulator.remove(contribution)

    active = accumulator.add(_candle(100.0, 101.0, 10.0))
    accumulator.remove(active)
    with pytest.raises(ValueError, match="not active"):
        accumulator.remove(active)


def test_earlier_snapshot_does_not_change_after_later_updates() -> None:
    accumulator = ProfileAccumulator(
        binning=FixedStepBins(step=1.0), allocation=UniformAllocation()
    )
    accumulator.add(_candle(100.0, 100.0, 10.0))
    earlier = accumulator.snapshot()

    accumulator.add(_candle(101.0, 101.0, 20.0))

    assert earlier.bin_volumes == {100: 10.0}
    assert earlier.total_volume == 10.0


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1, float("nan")])
def test_accumulator_rejects_invalid_value_area_fraction_at_construction(
    fraction: float,
) -> None:
    with pytest.raises(ValueError, match="value_area_fraction"):
        ProfileAccumulator(
            binning=FixedStepBins(step=1.0),
            allocation=UniformAllocation(),
            value_area_fraction=fraction,
        )
