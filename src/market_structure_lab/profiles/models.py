"""Immutable profile input and output models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market_structure_lab.profiles.binning import BinDefinition


@dataclass(frozen=True)
class Candle:
    """An OHLCV observation used by the profile approximation models."""

    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ProfileSnapshot:
    """Frozen profile state whose internal volume keys are integer bin indices."""

    bin_volumes: Mapping[int, float]
    total_volume: float
    poc_index: int | None
    value_area_low_index: int | None
    value_area_high_index: int | None
    vwap: float | None
    binning: BinDefinition
    allocation_id: str
    value_area_fraction: float = 0.70

    def __post_init__(self) -> None:
        if any(not isinstance(index, int) for index in self.bin_volumes):
            raise TypeError("profile bin keys must be integers")
        object.__setattr__(self, "bin_volumes", MappingProxyType(dict(self.bin_volumes)))

    @property
    def binning_id(self) -> str:
        return self.binning.definition_id

    @property
    def price_volumes(self) -> Mapping[float, float]:
        return MappingProxyType(
            {
                self.binning.price_for_index(index): volume
                for index, volume in self.bin_volumes.items()
            }
        )

    @property
    def point_of_control(self) -> float | None:
        if self.poc_index is None:
            return None
        return self.binning.price_for_index(self.poc_index)

    @property
    def value_area_low(self) -> float | None:
        if self.value_area_low_index is None:
            return None
        return self.binning.price_for_index(self.value_area_low_index)

    @property
    def value_area_high(self) -> float | None:
        if self.value_area_high_index is None:
            return None
        return self.binning.price_for_index(self.value_area_high_index)
