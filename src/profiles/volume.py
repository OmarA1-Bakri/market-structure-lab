from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class VolumeProfile:
    price_volumes: dict[float, float]
    total_volume: float
    point_of_control: float | None
    value_area_low: float | None
    value_area_high: float | None


def build_volume_profile(
    candles: list[Candle],
    *,
    tick_size: float,
    value_area_fraction: float = 0.70,
) -> VolumeProfile:
    """Build a deterministic volume profile from OHLCV candles.

    Without trade-level data, each candle's volume is distributed equally across
    every price bin touched by the candle's low-high range.
    """
    _validate_profile_inputs(tick_size=tick_size, value_area_fraction=value_area_fraction)

    price_volumes: dict[float, float] = {}
    for candle in candles:
        _validate_candle(candle)
        bins = _price_bins(candle.low, candle.high, tick_size)
        volume_per_bin = candle.volume / len(bins)
        for price in bins:
            price_volumes[price] = price_volumes.get(price, 0.0) + volume_per_bin

    if not price_volumes:
        return VolumeProfile(
            price_volumes={},
            total_volume=0.0,
            point_of_control=None,
            value_area_low=None,
            value_area_high=None,
        )

    ordered_volumes = dict(sorted(price_volumes.items()))
    total_volume = sum(ordered_volumes.values())
    point_of_control = _point_of_control(ordered_volumes)
    value_area_low, value_area_high = _value_area(
        ordered_volumes,
        point_of_control=point_of_control,
        target_volume=total_volume * value_area_fraction,
    )

    return VolumeProfile(
        price_volumes=ordered_volumes,
        total_volume=total_volume,
        point_of_control=point_of_control,
        value_area_low=value_area_low,
        value_area_high=value_area_high,
    )


def _validate_profile_inputs(*, tick_size: float, value_area_fraction: float) -> None:
    if tick_size <= 0 or not isfinite(tick_size):
        raise ValueError("tick_size must be a positive finite number")
    if not 0 < value_area_fraction <= 1:
        raise ValueError("value_area_fraction must be greater than 0 and less than or equal to 1")


def _validate_candle(candle: Candle) -> None:
    values = [candle.open, candle.high, candle.low, candle.close, candle.volume]
    if any(not isfinite(value) for value in values):
        raise ValueError("candle values must be finite")
    if candle.high < candle.low:
        raise ValueError("high must be greater than or equal to low")
    if candle.volume < 0:
        raise ValueError("volume must be greater than or equal to 0")


def _price_bins(low: float, high: float, tick_size: float) -> list[float]:
    low_index = floor(low / tick_size)
    high_index = floor(high / tick_size)
    decimals = _tick_decimals(tick_size)
    return [round(index * tick_size, decimals) for index in range(low_index, high_index + 1)]


def _tick_decimals(tick_size: float) -> int:
    text = f"{tick_size:.12f}".rstrip("0").rstrip(".")
    return len(text.partition(".")[2])


def _point_of_control(price_volumes: dict[float, float]) -> float:
    return max(price_volumes.items(), key=lambda item: (item[1], -item[0]))[0]


def _value_area(
    price_volumes: dict[float, float],
    *,
    point_of_control: float,
    target_volume: float,
) -> tuple[float, float]:
    prices = list(price_volumes)
    poc_index = prices.index(point_of_control)
    low_index = poc_index
    high_index = poc_index
    included_volume = price_volumes[point_of_control]

    while included_volume < target_volume and (low_index > 0 or high_index < len(prices) - 1):
        lower_volume = price_volumes[prices[low_index - 1]] if low_index > 0 else -1.0
        upper_volume = price_volumes[prices[high_index + 1]] if high_index < len(prices) - 1 else -1.0

        if upper_volume >= lower_volume and high_index < len(prices) - 1:
            high_index += 1
            included_volume += price_volumes[prices[high_index]]
        elif low_index > 0:
            low_index -= 1
            included_volume += price_volumes[prices[low_index]]
        else:
            break

    return prices[low_index], prices[high_index]
