"""Versioned OHLCV volume-allocation approximations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isclose, isfinite
from types import MappingProxyType
from typing import Protocol

from market_structure_lab.profiles.binning import BinDefinition
from market_structure_lab.profiles.models import Candle


@dataclass(frozen=True)
class BinContribution:
    """Cached additive contribution from one input observation or reconstruction."""

    bin_volumes: Mapping[int, float]
    total_volume: float
    price_volume_numerator: float

    def __post_init__(self) -> None:
        if any(not isinstance(index, int) for index in self.bin_volumes):
            raise TypeError("contribution bin keys must be integers")
        if any(not isfinite(volume) or volume < 0 for volume in self.bin_volumes.values()):
            raise ValueError("bin volumes must be finite and non-negative")
        if not isfinite(self.total_volume) or self.total_volume < 0:
            raise ValueError("total_volume must be finite and non-negative")
        if not isfinite(self.price_volume_numerator):
            raise ValueError("price_volume_numerator must be finite")
        if not isclose(
            fsum(self.bin_volumes.values()), self.total_volume, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("total_volume must equal the sum of bin volumes")
        object.__setattr__(self, "bin_volumes", MappingProxyType(dict(self.bin_volumes)))


class AllocationModel(Protocol):
    @property
    def model_id(self) -> str: ...

    def allocate(
        self,
        candle: Candle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution: ...


def validate_candle(candle: Candle) -> None:
    values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
    if any(not isfinite(value) for value in values):
        raise ValueError("candle values must be finite")
    if min(candle.open, candle.high, candle.low, candle.close) < 0:
        raise ValueError("candle prices must be non-negative")
    if candle.high < candle.low:
        raise ValueError("high must be greater than or equal to low")
    if not candle.low <= candle.open <= candle.high:
        raise ValueError("open must lie between low and high")
    if not candle.low <= candle.close <= candle.high:
        raise ValueError("close must lie between low and high")
    if candle.volume < 0:
        raise ValueError("volume must be non-negative")


def _touched_indices(candle: Candle, binning: BinDefinition) -> range:
    low_index = binning.bin_index(candle.low)
    high_index = binning.bin_index(candle.high)
    return range(low_index, high_index + 1)


def _contribution(
    bin_volumes: Mapping[int, float], *, numerator: float | None, binning: BinDefinition
) -> BinContribution:
    total = fsum(bin_volumes.values())
    if numerator is None:
        numerator = fsum(
            binning.price_for_index(index) * volume for index, volume in bin_volumes.items()
        )
    return BinContribution(bin_volumes, total, numerator)


@dataclass(frozen=True)
class UniformAllocation:
    """Distribute candle volume equally across every touched bin."""

    model_id: str = "uniform-touched-v1"

    def allocate(
        self,
        candle: Candle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        del lower_timeframe_candles
        validate_candle(candle)
        indices = _touched_indices(candle, binning)
        volume_per_bin = candle.volume / len(indices)
        return _contribution(
            {index: volume_per_bin for index in indices}, numerator=None, binning=binning
        )


@dataclass(frozen=True)
class TypicalPriceAllocation:
    """Assign all candle volume to its typical-price bin."""

    model_id: str = "typical-price-v1"

    def allocate(
        self,
        candle: Candle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        del lower_timeframe_candles
        validate_candle(candle)
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        index = binning.bin_index(typical_price)
        return _contribution(
            {index: candle.volume},
            numerator=typical_price * candle.volume,
            binning=binning,
        )


@dataclass(frozen=True)
class TriangularCloseAllocation:
    """Weight touched bins linearly toward the candle close bin."""

    model_id: str = "triangular-close-v1"

    def allocate(
        self,
        candle: Candle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        del lower_timeframe_candles
        validate_candle(candle)
        indices = tuple(_touched_indices(candle, binning))
        close_index = binning.bin_index(candle.close)
        width = len(indices)
        weights = {index: float(width - abs(index - close_index)) for index in indices}
        total_weight = fsum(weights.values())
        volumes = {
            index: candle.volume * weight / total_weight for index, weight in weights.items()
        }
        return _contribution(volumes, numerator=None, binning=binning)


@dataclass(frozen=True)
class LowerTimeframeReconstruction:
    """Reconstruct a parent profile only from supplied real constituent candles."""

    base_model: AllocationModel = UniformAllocation()
    version: str = "lower-timeframe-v1"

    @property
    def model_id(self) -> str:
        return f"{self.version}:{self.base_model.model_id}"

    def allocate(
        self,
        candle: Candle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        validate_candle(candle)
        if lower_timeframe_candles is None:
            raise ValueError("lower_timeframe_candles must be supplied for reconstruction")

        contributions: list[BinContribution] = []
        for constituent in lower_timeframe_candles:
            validate_candle(constituent)
            if constituent.low < candle.low or constituent.high > candle.high:
                raise ValueError("lower-timeframe candle lies outside the parent price range")
            contributions.append(self.base_model.allocate(constituent, binning))

        grouped: dict[int, list[float]] = {}
        for contribution in contributions:
            for index, volume in contribution.bin_volumes.items():
                grouped.setdefault(index, []).append(volume)
        volumes = {index: fsum(parts) for index, parts in grouped.items()}
        numerator = fsum(item.price_volume_numerator for item in contributions)
        return _contribution(volumes, numerator=numerator, binning=binning)
