"""Immutable profile input and output models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market_structure_lab.profiles.binning import BinDefinition


_MAXIMUM_TOUCHED_BIN_BUDGET = 65_536
_MAXIMUM_ACTIVE_PROFILE_BIN_BUDGET = 262_144
_MAXIMUM_ACTIVE_CONTRIBUTION_BUDGET = 100_000
_MAXIMUM_ACTIVE_CONTRIBUTION_CELL_BUDGET = 5_000_000


@dataclass(frozen=True, slots=True)
class ProfileWorkBudget:
    """Frozen admissibility limits for bounded profile construction."""

    maximum_touched_bins_per_candle: int = _MAXIMUM_TOUCHED_BIN_BUDGET
    maximum_active_profile_bins: int = _MAXIMUM_ACTIVE_PROFILE_BIN_BUDGET
    maximum_active_contributions: int = _MAXIMUM_ACTIVE_CONTRIBUTION_BUDGET
    maximum_active_contribution_cells: int = _MAXIMUM_ACTIVE_CONTRIBUTION_CELL_BUDGET

    def __post_init__(self) -> None:
        self._validate_limit(
            self.maximum_touched_bins_per_candle,
            name="maximum_touched_bins_per_candle",
            safe_maximum=_MAXIMUM_TOUCHED_BIN_BUDGET,
        )
        self._validate_limit(
            self.maximum_active_profile_bins,
            name="maximum_active_profile_bins",
            safe_maximum=_MAXIMUM_ACTIVE_PROFILE_BIN_BUDGET,
        )
        self._validate_limit(
            self.maximum_active_contributions,
            name="maximum_active_contributions",
            safe_maximum=_MAXIMUM_ACTIVE_CONTRIBUTION_BUDGET,
        )
        self._validate_limit(
            self.maximum_active_contribution_cells,
            name="maximum_active_contribution_cells",
            safe_maximum=_MAXIMUM_ACTIVE_CONTRIBUTION_CELL_BUDGET,
        )

    @property
    def definition_id(self) -> str:
        return (
            "profile-work-budget-v1:"
            f"maximum-touched-bins-per-candle={self.maximum_touched_bins_per_candle};"
            f"maximum-active-profile-bins={self.maximum_active_profile_bins};"
            f"maximum-active-contributions={self.maximum_active_contributions};"
            "maximum-active-contribution-cells="
            f"{self.maximum_active_contribution_cells}"
        )

    @staticmethod
    def _validate_limit(value: int, *, name: str, safe_maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"profile work budget {name} must be an integer")
        if value < 1:
            raise ValueError(f"profile work budget {name} must be positive")
        if value > safe_maximum:
            raise ValueError(
                f"profile work budget {name} exceeds the safe maximum of {safe_maximum}"
            )


DEFAULT_PROFILE_WORK_BUDGET = ProfileWorkBudget()


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
    work_budget: ProfileWorkBudget = DEFAULT_PROFILE_WORK_BUDGET

    def __post_init__(self) -> None:
        if not isinstance(self.work_budget, ProfileWorkBudget):
            raise TypeError("work_budget must be a ProfileWorkBudget")
        if len(self.bin_volumes) > self.work_budget.maximum_active_profile_bins:
            raise ValueError("active profile-bin budget exceeded in snapshot")
        if any(not isinstance(index, int) for index in self.bin_volumes):
            raise TypeError("profile bin keys must be integers")
        snapshot_binning = self.binning.snapshot_definition()
        if snapshot_binning.definition_id != self.binning.definition_id:
            raise ValueError("binning snapshot identity must match the live definition")
        if snapshot_binning.snapshot_definition() is not snapshot_binning:
            raise ValueError("profile binning snapshot must be immutable and return itself")
        object.__setattr__(self, "bin_volumes", MappingProxyType(dict(self.bin_volumes)))
        object.__setattr__(self, "binning", snapshot_binning)

    @property
    def binning_id(self) -> str:
        return self.binning.definition_id

    @property
    def work_budget_id(self) -> str:
        return self.work_budget.definition_id

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
