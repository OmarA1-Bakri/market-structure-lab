from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.profiles import Candle, VolumeProfile, build_volume_profile


class AuctionLocation(str, Enum):
    BELOW_VALUE = "below_value"
    LOWER_VALUE = "lower_value"
    POINT_OF_CONTROL = "point_of_control"
    UPPER_VALUE = "upper_value"
    ABOVE_VALUE = "above_value"


@dataclass(frozen=True)
class AuctionSnapshot:
    candle_count: int
    latest_candle: Candle
    profile: VolumeProfile
    location: AuctionLocation


class AuctionEngine:
    """Candle-by-candle deterministic auction-state engine."""

    def __init__(self, *, tick_size: float, value_area_fraction: float = 0.70) -> None:
        self._tick_size = tick_size
        self._value_area_fraction = value_area_fraction
        self._candles: list[Candle] = []

    def update(self, candle: Candle) -> AuctionSnapshot:
        self._candles.append(candle)
        profile = build_volume_profile(
            list(self._candles),
            tick_size=self._tick_size,
            value_area_fraction=self._value_area_fraction,
        )
        return AuctionSnapshot(
            candle_count=len(self._candles),
            latest_candle=candle,
            profile=profile,
            location=_classify_location(candle.close, profile),
        )

    def reset(self) -> None:
        self._candles.clear()


def _classify_location(price: float, profile: VolumeProfile) -> AuctionLocation:
    if (
        profile.point_of_control is None
        or profile.value_area_low is None
        or profile.value_area_high is None
    ):
        raise ValueError("profile must contain auction references")

    if price < profile.value_area_low:
        return AuctionLocation.BELOW_VALUE
    if price > profile.value_area_high:
        return AuctionLocation.ABOVE_VALUE
    if price == profile.point_of_control:
        return AuctionLocation.POINT_OF_CONTROL
    if price < profile.point_of_control:
        return AuctionLocation.LOWER_VALUE
    return AuctionLocation.UPPER_VALUE
