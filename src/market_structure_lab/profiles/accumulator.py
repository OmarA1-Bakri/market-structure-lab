"""Incremental profile accumulation using cached candle contributions."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite

from market_structure_lab.profiles.allocation import (
    AllocationModel,
    BinContribution,
    UniformAllocation,
)
from market_structure_lab.profiles.binning import BinDefinition
from market_structure_lab.profiles.models import Candle, ProfileSnapshot
from market_structure_lab.profiles.volume import calculate_profile


class ProfileAccumulator:
    """Add and remove precomputed contributions without reallocating candles."""

    def __init__(
        self,
        *,
        binning: BinDefinition,
        allocation: AllocationModel | None = None,
        value_area_fraction: float = 0.70,
    ) -> None:
        if not isfinite(value_area_fraction) or not 0 < value_area_fraction <= 1:
            raise ValueError(
                "value_area_fraction must be greater than 0 and less than or equal to 1"
            )
        if not binning.definition_id:
            raise ValueError("binning definition_id must not be empty")
        self.binning = binning
        self.allocation = allocation or UniformAllocation()
        if not self.allocation.model_id:
            raise ValueError("allocation model_id must not be empty")
        self.value_area_fraction = value_area_fraction
        self._active: list[BinContribution] = []
        self._bin_parts: dict[int, list[float]] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    def contribution(
        self,
        candle: Candle,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        return self.allocation.allocate(
            candle,
            self.binning,
            lower_timeframe_candles=lower_timeframe_candles,
        )

    def add(
        self,
        candle: Candle,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        contribution = self.contribution(candle, lower_timeframe_candles=lower_timeframe_candles)
        self.add_contribution(contribution)
        return contribution

    def add_contribution(self, contribution: BinContribution) -> None:
        self._active.append(contribution)
        for index, volume in contribution.bin_volumes.items():
            self._bin_parts.setdefault(index, []).append(volume)

    def remove(self, contribution: BinContribution) -> None:
        active_index = next(
            (index for index, item in enumerate(self._active) if item is contribution), None
        )
        if active_index is None:
            raise ValueError("contribution is not active in this accumulator")
        self._active.pop(active_index)
        for index, volume in contribution.bin_volumes.items():
            parts = self._bin_parts[index]
            part_index = next(
                (part_index for part_index, part in enumerate(parts) if part == volume), None
            )
            if part_index is None:
                raise RuntimeError("active contribution is missing from accumulator bins")
            parts.pop(part_index)
            if not parts:
                del self._bin_parts[index]

    def clear(self) -> None:
        self._active.clear()
        self._bin_parts.clear()

    def snapshot(self) -> ProfileSnapshot:
        # Build from cached bin parts and contribution numerators. ``fsum`` keeps
        # rolling add/remove equivalent to recomputation independent of order.
        bin_volumes = {
            index: total
            for index, parts in sorted(self._bin_parts.items())
            if (total := fsum(parts)) > 0
        }
        aggregate = BinContribution(
            bin_volumes,
            fsum(bin_volumes.values()),
            fsum(item.price_volume_numerator for item in self._active),
        )
        return calculate_profile(
            [aggregate] if self._active else [],
            binning=self.binning,
            allocation_id=self.allocation.model_id,
            value_area_fraction=self.value_area_fraction,
        )
