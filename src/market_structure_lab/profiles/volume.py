"""Deterministic integer-bin profile calculation and compatibility facade."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isfinite
from types import MappingProxyType

from market_structure_lab.profiles.allocation import BinContribution, UniformAllocation
from market_structure_lab.profiles.binning import BinDefinition, FixedStepBins
from market_structure_lab.profiles.models import Candle, ProfileSnapshot


def point_of_control_index(bin_volumes: Mapping[int, float]) -> int:
    """Return the highest-volume bin, breaking plateaus toward the lower index."""
    if not bin_volumes:
        raise ValueError("point of control requires at least one bin")
    return max(bin_volumes.items(), key=lambda item: (item[1], -item[0]))[0]


def value_area_indices(
    bin_volumes: Mapping[int, float], *, point_of_control: int, target_volume: float
) -> tuple[int, int]:
    """Expand through contiguous integer bins, preferring the upper side on ties."""
    if point_of_control not in bin_volumes:
        raise ValueError("point_of_control must be present in bin_volumes")
    if target_volume < 0 or not isfinite(target_volume):
        raise ValueError("target_volume must be finite and non-negative")
    minimum = min(bin_volumes)
    maximum = max(bin_volumes)
    observed = sorted(bin_volumes)
    low = high = point_of_control
    included = bin_volumes[point_of_control]

    while included < target_volume and (low > minimum or high < maximum):
        lower_volume = bin_volumes.get(low - 1, 0.0) if low > minimum else -1.0
        upper_volume = bin_volumes.get(high + 1, 0.0) if high < maximum else -1.0
        if high < maximum and upper_volume >= lower_volume:
            next_position = bisect_right(observed, high)
            high = high + 1 if high + 1 in bin_volumes else observed[next_position]
            included += bin_volumes.get(high, 0.0)
        elif low > minimum:
            previous_position = bisect_left(observed, low) - 1
            low = low - 1 if low - 1 in bin_volumes else observed[previous_position]
            included += bin_volumes.get(low, 0.0)
    return low, high


def calculate_profile(
    contributions: Iterable[BinContribution],
    *,
    binning: BinDefinition,
    allocation_id: str,
    value_area_fraction: float = 0.70,
) -> ProfileSnapshot:
    """Calculate one immutable profile from additive cached contributions."""
    if not 0 < value_area_fraction <= 1 or not isfinite(value_area_fraction):
        raise ValueError("value_area_fraction must be greater than 0 and less than or equal to 1")
    if not allocation_id:
        raise ValueError("allocation_id must not be empty")

    materialized = tuple(contributions)
    grouped: dict[int, list[float]] = {}
    for contribution in materialized:
        for index, volume in contribution.bin_volumes.items():
            if volume > 0:
                grouped.setdefault(index, []).append(volume)
    bin_volumes = {index: fsum(parts) for index, parts in sorted(grouped.items())}
    total_volume = fsum(contribution.total_volume for contribution in materialized)
    numerator = fsum(contribution.price_volume_numerator for contribution in materialized)

    if total_volume == 0 or not bin_volumes:
        return ProfileSnapshot(
            bin_volumes={},
            total_volume=0.0,
            poc_index=None,
            value_area_low_index=None,
            value_area_high_index=None,
            vwap=None,
            binning=binning,
            allocation_id=allocation_id,
            value_area_fraction=value_area_fraction,
        )

    poc = point_of_control_index(bin_volumes)
    value_low, value_high = value_area_indices(
        bin_volumes,
        point_of_control=poc,
        target_volume=total_volume * value_area_fraction,
    )
    return ProfileSnapshot(
        bin_volumes=bin_volumes,
        total_volume=total_volume,
        poc_index=poc,
        value_area_low_index=value_low,
        value_area_high_index=value_high,
        vwap=numerator / total_volume,
        binning=binning,
        allocation_id=allocation_id,
        value_area_fraction=value_area_fraction,
    )


@dataclass(frozen=True)
class VolumeProfile:
    """Price-keyed compatibility view retained for existing public consumers."""

    price_volumes: Mapping[float, float]
    total_volume: float
    point_of_control: float | None
    value_area_low: float | None
    value_area_high: float | None
    tick_size: float = 1.0
    vwap: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price_volumes", MappingProxyType(dict(self.price_volumes)))


def build_volume_profile(
    candles: Sequence[Candle],
    *,
    tick_size: float,
    value_area_fraction: float = 0.70,
) -> VolumeProfile:
    """Build the original uniform OHLCV profile through the integer-bin engine."""
    if not isfinite(tick_size) or tick_size <= 0:
        raise ValueError("tick_size must be a positive finite number")
    binning = FixedStepBins(step=tick_size)
    allocation = UniformAllocation()
    contributions = [allocation.allocate(candle, binning) for candle in candles]
    snapshot = calculate_profile(
        contributions,
        binning=binning,
        allocation_id=allocation.model_id,
        value_area_fraction=value_area_fraction,
    )
    return VolumeProfile(
        price_volumes=snapshot.price_volumes,
        total_volume=snapshot.total_volume,
        point_of_control=snapshot.point_of_control,
        value_area_low=snapshot.value_area_low,
        value_area_high=snapshot.value_area_high,
        tick_size=tick_size,
        vwap=snapshot.vwap,
    )


def price_bins(low: float, high: float, tick_size: float) -> list[float]:
    """Return public price boundaries touched by a range (compatibility API)."""
    binning = FixedStepBins(step=tick_size)
    return [
        binning.price_for_index(index)
        for index in range(binning.bin_index(low), binning.bin_index(high) + 1)
    ]


def point_of_control(price_volumes: Mapping[float, float]) -> float:
    """Return the highest-volume price, breaking ties toward the lower price."""
    if not price_volumes:
        raise ValueError("point of control requires at least one price")
    return max(price_volumes.items(), key=lambda item: (item[1], -item[0]))[0]


def value_area(
    price_volumes: Mapping[float, float],
    *,
    point_of_control: float,
    target_volume: float,
) -> tuple[float, float]:
    """Compatibility value-area expansion across ordered public prices."""
    prices = sorted(price_volumes)
    poc_index = prices.index(point_of_control)
    low_index = high_index = poc_index
    included = price_volumes[point_of_control]
    while included < target_volume and (low_index > 0 or high_index < len(prices) - 1):
        lower_volume = price_volumes[prices[low_index - 1]] if low_index > 0 else -1.0
        upper_volume = (
            price_volumes[prices[high_index + 1]] if high_index < len(prices) - 1 else -1.0
        )
        if high_index < len(prices) - 1 and upper_volume >= lower_volume:
            high_index += 1
            included += price_volumes[prices[high_index]]
        elif low_index > 0:
            low_index -= 1
            included += price_volumes[prices[low_index]]
    return prices[low_index], prices[high_index]
