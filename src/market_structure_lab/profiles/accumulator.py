"""Incremental profile accumulation using cached candle contributions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import fsum, isfinite

from market_structure_lab.profiles.allocation import (
    AllocationModel,
    BinContribution,
    UniformAllocation,
    touched_bin_count,
)
from market_structure_lab.profiles.binning import BinDefinition
from market_structure_lab.profiles.models import (
    DEFAULT_PROFILE_WORK_BUDGET,
    Candle,
    ProfileSnapshot,
    ProfileWorkBudget,
)
from market_structure_lab.profiles.volume import calculate_profile


class ProfileAccumulatorTransaction:
    """Bounded inverse-operation journal for one accumulator update."""

    def __init__(self, accumulator: ProfileAccumulator) -> None:
        self._accumulator = accumulator
        self._undo: list[Callable[[], None]] = []
        self._commit_requested = False
        self._finalize_requested = False
        self._closed = False

    def record(self, undo: Callable[[], None]) -> None:
        if self._closed or self._commit_requested:
            raise RuntimeError("profile accumulator transaction is closed")
        self._undo.append(undo)

    def commit(self) -> None:
        if self._closed or self._accumulator._transaction is not self:
            raise RuntimeError("profile accumulator transaction is not active")
        self._commit_requested = True

    def finalize(self) -> None:
        """Validate finalization while retaining rollback until every collaborator is ready."""

        if self._closed or not self._commit_requested or self._accumulator._transaction is not self:
            raise RuntimeError("profile accumulator transaction is not ready to finalize")
        self._finalize_requested = True

    def release(self) -> None:
        """Release the prepared journal idempotently after validated finalization."""

        if self._closed:
            return
        self._close()

    def ensure_released(self) -> None:
        """Force-detach a committed journal after an unexpected release failure."""

        self._close()

    def rollback(self) -> None:
        if self._closed:
            raise RuntimeError("profile accumulator transaction is closed")
        failures: list[Exception] = []
        try:
            for undo in reversed(self._undo):
                try:
                    undo()
                except Exception as error:
                    failures.append(error)
        finally:
            self._close()
        if failures:
            rollback_error = RuntimeError("profile accumulator rollback failed")
            for failure in failures:
                rollback_error.add_note(f"{type(failure).__name__}: {failure}")
            raise rollback_error from failures[0]

    def _close(self) -> None:
        self._undo.clear()
        self._closed = True
        if self._accumulator._transaction is self:
            self._accumulator._transaction = None


class ProfileAccumulator:
    """Add and remove precomputed contributions without reallocating candles."""

    def __init__(
        self,
        *,
        binning: BinDefinition,
        allocation: AllocationModel | None = None,
        value_area_fraction: float = 0.70,
        work_budget: ProfileWorkBudget = DEFAULT_PROFILE_WORK_BUDGET,
    ) -> None:
        if not isfinite(value_area_fraction) or not 0 < value_area_fraction <= 1:
            raise ValueError(
                "value_area_fraction must be greater than 0 and less than or equal to 1"
            )
        if not binning.definition_id:
            raise ValueError("binning definition_id must not be empty")
        if not isinstance(work_budget, ProfileWorkBudget):
            raise TypeError("work_budget must be a ProfileWorkBudget")
        snapshot_binning = binning.snapshot_definition()
        if not isinstance(snapshot_binning, BinDefinition):
            raise TypeError("binning snapshot must implement BinDefinition")
        if snapshot_binning.definition_id != binning.definition_id:
            raise ValueError("binning snapshot identity must match the live definition")
        if snapshot_binning.snapshot_definition() is not snapshot_binning:
            raise ValueError("binning snapshot must be immutable and return itself")
        self.binning = binning
        self._snapshot_binning = snapshot_binning
        self._binning_definition_id = snapshot_binning.definition_id
        self.allocation = allocation if allocation is not None else UniformAllocation()
        if not self.allocation.model_id:
            raise ValueError("allocation model_id must not be empty")
        self.value_area_fraction = value_area_fraction
        self.work_budget = work_budget
        self._active: list[BinContribution] = []
        self._active_cell_count = 0
        self._bin_parts: dict[int, list[float]] = {}
        self._transaction: ProfileAccumulatorTransaction | None = None

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def binning_definition_id(self) -> str:
        return self._binning_definition_id

    @property
    def work_budget_id(self) -> str:
        return self.work_budget.definition_id

    def begin_transaction(self) -> ProfileAccumulatorTransaction:
        """Begin one non-nested update journal whose cost follows touched deltas."""

        if self._transaction is not None:
            raise RuntimeError("profile accumulator transaction is already active")
        transaction = ProfileAccumulatorTransaction(self)
        self._transaction = transaction
        return transaction

    def contribution(
        self,
        candle: Candle,
        *,
        lower_timeframe_candles: Sequence[Candle] | None = None,
    ) -> BinContribution:
        self._require_stable_binning_identity()
        touched_bins = touched_bin_count(candle, self.binning)
        self._require_stable_binning_identity()
        if touched_bins > self.work_budget.maximum_touched_bins_per_candle:
            raise ValueError(
                "candle touched-bin budget exceeded: "
                f"{touched_bins} > {self.work_budget.maximum_touched_bins_per_candle}"
            )
        contribution = self.allocation.allocate(
            candle,
            self.binning,
            lower_timeframe_candles=lower_timeframe_candles,
        )
        self._require_stable_binning_identity()
        if len(contribution.bin_volumes) > self.work_budget.maximum_touched_bins_per_candle:
            raise ValueError(
                "allocation touched-bin budget exceeded: "
                f"{len(contribution.bin_volumes)} > "
                f"{self.work_budget.maximum_touched_bins_per_candle}"
            )
        return contribution

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
        self.preflight_active_bins(contribution)
        self._active.append(contribution)
        added_cells = len(contribution.bin_volumes)
        self._active_cell_count += added_cells

        def undo_active_addition() -> None:
            self._active.pop()
            self._active_cell_count -= added_cells

        self._record_undo(undo_active_addition)
        for index, volume in contribution.bin_volumes.items():
            parts = self._bin_parts.setdefault(index, [])
            parts.append(volume)

            def undo_bin_addition(
                *,
                bin_index: int = index,
                bin_parts: list[float] = parts,
            ) -> None:
                bin_parts.pop()
                if not bin_parts:
                    del self._bin_parts[bin_index]

            self._record_undo(undo_bin_addition)

    def preflight_active_bins(
        self,
        contribution: BinContribution,
        *,
        removals: Sequence[BinContribution] = (),
        reset: bool = False,
    ) -> None:
        """Reject projected active-bin overflow before mutating profile state."""
        removed_cells = sum(len(item.bin_volumes) for item in removals)
        projected_contributions = (0 if reset else len(self._active) - len(removals)) + 1
        projected_cells = (0 if reset else self._active_cell_count - removed_cells) + len(
            contribution.bin_volumes
        )
        if projected_contributions > self.work_budget.maximum_active_contributions:
            raise ValueError(
                "active contribution budget exceeded: "
                f"{projected_contributions} > "
                f"{self.work_budget.maximum_active_contributions}"
            )
        if projected_cells > self.work_budget.maximum_active_contribution_cells:
            raise ValueError(
                "active contribution-cell budget exceeded: "
                f"{projected_cells} > "
                f"{self.work_budget.maximum_active_contribution_cells}"
            )
        if reset:
            projected_count = len(contribution.bin_volumes)
        else:
            removal_counts: dict[int, int] = {}
            for removed in removals:
                for index in removed.bin_volumes:
                    removal_counts[index] = removal_counts.get(index, 0) + 1
            surviving = {
                index
                for index, parts in self._bin_parts.items()
                if len(parts) > removal_counts.get(index, 0)
            }
            projected_count = len(surviving) + sum(
                index not in surviving for index in contribution.bin_volumes
            )
        maximum = self.work_budget.maximum_active_profile_bins
        if projected_count > maximum:
            raise ValueError(f"active profile-bin budget exceeded: {projected_count} > {maximum}")

    def remove(self, contribution: BinContribution) -> None:
        active_index = next(
            (index for index, item in enumerate(self._active) if item is contribution), None
        )
        if active_index is None:
            raise ValueError("contribution is not active in this accumulator")
        removed_active_index = active_index
        self._active.pop(removed_active_index)
        removed_cells = len(contribution.bin_volumes)
        self._active_cell_count -= removed_cells

        def undo_active_removal(
            *,
            index: int = removed_active_index,
            item: BinContribution = contribution,
        ) -> None:
            self._active.insert(index, item)
            self._active_cell_count += removed_cells

        self._record_undo(undo_active_removal)
        for index, volume in contribution.bin_volumes.items():
            parts = self._bin_parts[index]
            part_index = next(
                (part_index for part_index, part in enumerate(parts) if part == volume), None
            )
            if part_index is None:
                raise RuntimeError("active contribution is missing from accumulator bins")
            removed_part_index = part_index
            parts.pop(removed_part_index)
            if not parts:
                del self._bin_parts[index]

            def undo_bin_removal(
                *,
                bin_index: int = index,
                bin_parts: list[float] = parts,
                index_in_bin: int = removed_part_index,
                removed_volume: float = volume,
            ) -> None:
                if bin_index not in self._bin_parts:
                    self._bin_parts[bin_index] = bin_parts
                bin_parts.insert(index_in_bin, removed_volume)

            self._record_undo(undo_bin_removal)

    def clear(self) -> None:
        previous_active = self._active
        previous_active_cell_count = self._active_cell_count
        previous_bin_parts = self._bin_parts
        self._active = []
        self._active_cell_count = 0
        self._bin_parts = {}
        self._record_undo(
            lambda: self._restore_containers(
                previous_active,
                previous_active_cell_count,
                previous_bin_parts,
            )
        )

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
            binning=self._snapshot_binning,
            allocation_id=self.allocation.model_id,
            value_area_fraction=self.value_area_fraction,
            work_budget=self.work_budget,
        )

    @property
    def active_bin_count(self) -> int:
        """Return the number of live non-empty bins without copying the profile map."""

        return sum(1 for parts in self._bin_parts.values() if fsum(parts) > 0)

    def _require_stable_binning_identity(self) -> None:
        if self.binning.definition_id != self._binning_definition_id:
            raise ValueError("live binning definition identity changed after construction")

    def _record_undo(self, undo: Callable[[], None]) -> None:
        if self._transaction is not None:
            self._transaction.record(undo)

    def _restore_containers(
        self,
        active: list[BinContribution],
        active_cell_count: int,
        bin_parts: dict[int, list[float]],
    ) -> None:
        self._active = active
        self._active_cell_count = active_cell_count
        self._bin_parts = bin_parts
