from __future__ import annotations

import math

import pytest

from market_structure_lab.profiles.allocation import (
    LowerTimeframeReconstruction,
    TriangularCloseAllocation,
    TypicalPriceAllocation,
    UniformAllocation,
)
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle


def _candle(
    *,
    open_: float = 100.0,
    high: float = 102.0,
    low: float = 100.0,
    close: float = 101.0,
    volume: float = 30.0,
) -> Candle:
    return Candle(open=open_, high=high, low=low, close=close, volume=volume)


def test_uniform_allocation_conserves_volume_across_touched_bins() -> None:
    contribution = UniformAllocation().allocate(_candle(), FixedStepBins(step=1.0))

    assert contribution.bin_volumes == {100: 10.0, 101: 10.0, 102: 10.0}
    assert contribution.total_volume == pytest.approx(30.0)
    assert contribution.price_volume_numerator == pytest.approx(3030.0)


def test_typical_price_allocation_uses_one_deterministic_bin_and_actual_price() -> None:
    candle = _candle(high=103.0, low=100.0, close=102.0, volume=20.0)
    contribution = TypicalPriceAllocation().allocate(candle, FixedStepBins(step=1.0))

    assert contribution.bin_volumes == {101: 20.0}
    assert contribution.price_volume_numerator == pytest.approx((103 + 100 + 102) / 3 * 20)


def test_triangular_close_allocation_is_close_weighted_and_conserves_volume() -> None:
    contribution = TriangularCloseAllocation().allocate(
        _candle(high=104.0, close=103.0, volume=55.0), FixedStepBins(step=1.0)
    )

    assert contribution.bin_volumes[103] > contribution.bin_volumes[100]
    assert sum(contribution.bin_volumes.values()) == pytest.approx(55.0)
    assert contribution.total_volume == pytest.approx(55.0)


def test_triangular_zero_range_is_deterministic() -> None:
    contribution = TriangularCloseAllocation().allocate(
        _candle(open_=100.0, high=100.0, low=100.0, close=100.0, volume=7.0),
        FixedStepBins(step=1.0),
    )

    assert contribution.bin_volumes == {100: 7.0}


def test_lower_timeframe_reconstruction_uses_only_supplied_real_candles() -> None:
    parent = _candle(high=103.0, volume=100.0)
    constituents = (
        _candle(high=101.0, close=101.0, volume=9.0),
        _candle(open_=101.0, high=103.0, low=101.0, close=102.0, volume=12.0),
    )

    contribution = LowerTimeframeReconstruction().allocate(
        parent,
        FixedStepBins(step=1.0),
        lower_timeframe_candles=constituents,
    )

    assert contribution.total_volume == pytest.approx(21.0)
    assert sum(contribution.bin_volumes.values()) == pytest.approx(21.0)
    assert contribution.total_volume != parent.volume


def test_lower_timeframe_reconstruction_requires_supplied_candles() -> None:
    with pytest.raises(ValueError, match="lower_timeframe_candles"):
        LowerTimeframeReconstruction().allocate(_candle(), FixedStepBins(step=1.0))


@pytest.mark.parametrize(
    "candle",
    [
        _candle(high=99.0),
        _candle(close=103.0),
        _candle(volume=-1.0),
        _candle(open_=-1.0, high=1.0, low=-2.0, close=0.0),
        _candle(open_=math.nan),
    ],
)
def test_allocation_models_reject_invalid_ohlcv(candle: Candle) -> None:
    with pytest.raises(ValueError):
        UniformAllocation().allocate(candle, FixedStepBins(step=1.0))


def test_allocations_have_stable_versioned_identifiers() -> None:
    assert UniformAllocation().model_id == "uniform-touched-v1"
    assert TypicalPriceAllocation().model_id == "typical-price-v1"
    assert TriangularCloseAllocation().model_id == "triangular-close-v1"
    assert LowerTimeframeReconstruction().model_id == "lower-timeframe-v1:uniform-touched-v1"
