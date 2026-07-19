"""Bounded candle-by-candle deterministic auction reconstruction."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.auction.windows import WindowPolicy, WindowTransition
from market_structure_lab.profiles.accumulator import (
    ProfileAccumulator,
    ProfileAccumulatorTransaction,
)
from market_structure_lab.profiles.allocation import AllocationModel, BinContribution
from market_structure_lab.profiles.binning import BinDefinition
from market_structure_lab.profiles.models import Candle as ProfileCandle
from market_structure_lab.profiles.models import (
    DEFAULT_PROFILE_WORK_BUDGET,
    ProfileSnapshot,
    ProfileWorkBudget,
)
from market_structure_lab.structure.nodes import (
    NodePersistenceTracker,
    ProfileNode,
    detect_profile_nodes,
)
from market_structure_lab.structure.value_migration import ValueMigration, compare_value_migration


class AuctionLocation(StrEnum):
    NO_VALUE = "no_value"
    BELOW_VALUE = "below_value"
    LOWER_VALUE = "lower_value"
    POINT_OF_CONTROL = "point_of_control"
    UPPER_VALUE = "upper_value"
    ABOVE_VALUE = "above_value"


class GapPolicy(StrEnum):
    REJECT = "reject"
    RESET = "reset"


class StructuralEventKind(StrEnum):
    GAP_RESET = "gap_reset"
    SEGMENT_RESET = "segment_reset"
    WINDOW_RESET = "window_reset"
    POC_MIGRATION = "poc_migration"
    VALUE_BREAKOUT = "value_breakout"
    VALUE_REENTRY = "value_reentry"


@dataclass(frozen=True, slots=True)
class StructuralEvent:
    event_id: str
    timestamp: datetime
    kind: StructuralEventKind
    payload: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class AuctionSnapshot:
    timestamp: datetime
    symbol: str
    timeframe: str
    segment_id: int
    candle_count: int
    latest_candle: AuctionCandle
    active_timestamps: tuple[datetime, ...]
    profile: ProfileSnapshot
    location: AuctionLocation
    nodes: tuple[ProfileNode, ...]
    migration: ValueMigration | None
    events: tuple[StructuralEvent, ...]
    window_id: str
    window_version: str
    dataset_version: str
    config_version: str
    profile_definition_id: str


class AuctionEngine:
    """One ordered symbol/timeframe stream with an explicit bounded window."""

    def __init__(
        self,
        *,
        binning: BinDefinition,
        allocation: AllocationModel,
        window_policy: WindowPolicy,
        dataset_version: str,
        config_version: str,
        value_area_fraction: float = 0.70,
        expected_interval: timedelta = timedelta(minutes=1),
        gap_policy: GapPolicy = GapPolicy.REJECT,
        work_budget: ProfileWorkBudget = DEFAULT_PROFILE_WORK_BUDGET,
    ) -> None:
        if not isinstance(binning, BinDefinition):
            raise TypeError("binning must implement the transactional BinDefinition contract")
        if not isinstance(allocation, AllocationModel):
            raise TypeError("allocation must implement the transactional AllocationModel contract")
        if not isinstance(window_policy, WindowPolicy):
            raise TypeError("window_policy must implement the transactional WindowPolicy contract")
        if not dataset_version or not config_version:
            raise ValueError("dataset_version and config_version must be non-empty")
        if not isinstance(expected_interval, timedelta) or expected_interval <= timedelta(0):
            raise ValueError("expected_interval must be a positive timedelta")
        window_policy.validate_expected_interval(expected_interval)
        self._binning = binning
        self._allocation = allocation
        self._window_policy = window_policy
        self._dataset_version = dataset_version
        self._config_version = config_version
        self._expected_interval = expected_interval
        self._gap_policy = GapPolicy(gap_policy)
        self._accumulator = ProfileAccumulator(
            binning=binning,
            allocation=allocation,
            value_area_fraction=value_area_fraction,
            work_budget=work_budget,
        )
        self._contributions: dict[datetime, BinContribution] = {}
        self._node_tracker = NodePersistenceTracker()
        self._latest_input: AuctionCandle | None = None
        self._latest_snapshot: AuctionSnapshot | None = None
        self._symbol: str | None = None
        self._timeframe: str | None = None
        self._profile_definition_id = (
            f"profile-v1:{self._accumulator.binning_definition_id}:{allocation.model_id}:"
            f"value-area={value_area_fraction:.12g}:{self._accumulator.work_budget_id}"
        )

    @property
    def latest_snapshot(self) -> AuctionSnapshot | None:
        return self._latest_snapshot

    @property
    def active_count(self) -> int:
        return self._accumulator.active_count

    def update(self, candle: AuctionCandle) -> AuctionSnapshot | None:
        """Apply one observation atomically after validating its stream boundary."""
        source_reset_kind = self._validate_stream(candle)
        window_checkpoint = None
        binning_checkpoint: object = None
        allocation_checkpoint: object = None
        node_checkpoint: tuple[ProfileNode, ...] = ()
        binning_checkpoint_ready = False
        allocation_checkpoint_ready = False
        node_checkpoint_ready = False
        accumulator_transaction: ProfileAccumulatorTransaction | None = None
        cache_removed: list[tuple[datetime, BinContribution]] = []
        cache_added = False
        try:
            window_checkpoint = self._window_policy.transaction_checkpoint()
            binning_checkpoint = self._binning.transaction_checkpoint()
            binning_checkpoint_ready = True
            allocation_checkpoint = self._allocation.transaction_checkpoint()
            allocation_checkpoint_ready = True
            node_checkpoint = self._node_tracker.transaction_checkpoint()
            node_checkpoint_ready = True
            transition = self._window_policy.plan_transition(
                candle,
                reset_before=source_reset_kind is not None,
            )
            reset_kinds = () if source_reset_kind is None else (source_reset_kind,)
            window_reset = transition.reset
            if source_reset_kind is not None:
                window_reset = window_reset or self._window_policy.plan_transition(candle).reset
            if window_reset:
                reset_kinds = (*reset_kinds, StructuralEventKind.WINDOW_RESET)

            if not transition.include:
                self._window_policy.commit_transition(
                    candle,
                    transition,
                    reset_before=source_reset_kind is not None,
                )
                window_checkpoint.commit()
                window_checkpoint.finalize()
                self._record_input(candle)
                _release_committed_transactions(
                    (window_checkpoint.release, window_checkpoint.ensure_released)
                )
                return None

            incoming_contribution = self._accumulator.contribution(_profile_candle(candle))
            reset_profile = source_reset_kind is not None or transition.reset
            evicted_contributions: list[tuple[datetime, BinContribution]] = []
            if not reset_profile:
                for evicted in transition.evictions:
                    evicted_contribution = self._contributions.get(evicted.timestamp)
                    if evicted_contribution is None:
                        raise RuntimeError("window evicted a candle without an active contribution")
                    evicted_contributions.append((evicted.timestamp, evicted_contribution))

            self._accumulator.preflight_active_bins(
                incoming_contribution,
                removals=tuple(item[1] for item in evicted_contributions),
                reset=reset_profile,
            )
            accumulator_transaction = self._accumulator.begin_transaction()
            if reset_profile:
                self._accumulator.clear()
                self._node_tracker.reset()
            else:
                for _, evicted_contribution in evicted_contributions:
                    self._accumulator.remove(evicted_contribution)

            self._accumulator.add_contribution(incoming_contribution)
            profile = self._accumulator.snapshot()
            prior_snapshot = None if reset_kinds else self._latest_snapshot
            migration = None
            if (
                prior_snapshot is not None
                and _has_value_references(prior_snapshot.profile)
                and _has_value_references(profile)
            ):
                migration = compare_value_migration(prior_snapshot.profile, profile)
            location = _classify_location(candle.close, profile)
            nodes = self._node_tracker.update(detect_profile_nodes(profile))
            events = _structural_events(
                candle,
                prior_snapshot,
                profile,
                location,
                reset_kinds=reset_kinds,
                window_id=transition.window_id,
            )
            snapshot = AuctionSnapshot(
                timestamp=candle.timestamp,
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                segment_id=candle.segment_id,
                candle_count=self._accumulator.active_count,
                latest_candle=candle,
                active_timestamps=tuple(
                    item.timestamp
                    for item in _planned_active_candles(
                        self._window_policy,
                        candle,
                        transition,
                        reset_before=source_reset_kind is not None,
                    )
                ),
                profile=profile,
                location=location,
                nodes=nodes,
                migration=migration,
                events=events,
                window_id=transition.window_id,
                window_version=self._window_policy.version_id,
                dataset_version=self._dataset_version,
                config_version=self._config_version,
                profile_definition_id=self._profile_definition_id,
            )

            if reset_profile:
                replacement_cache = {candle.timestamp: incoming_contribution}
            else:
                for timestamp, contribution in evicted_contributions:
                    removed = self._contributions.pop(timestamp)
                    if removed is not contribution:
                        raise RuntimeError("contribution cache changed during auction update")
                    cache_removed.append((timestamp, contribution))
                self._contributions[candle.timestamp] = incoming_contribution
                cache_added = True

            self._window_policy.commit_transition(
                candle,
                transition,
                reset_before=source_reset_kind is not None,
            )
            accumulator_transaction.commit()
            window_checkpoint.commit()
            accumulator_transaction.finalize()
            window_checkpoint.finalize()
            if reset_profile:
                self._contributions = replacement_cache
            self._record_input(candle)
            self._latest_snapshot = snapshot
            _release_committed_transactions(
                (accumulator_transaction.release, accumulator_transaction.ensure_released),
                (window_checkpoint.release, window_checkpoint.ensure_released),
            )
            accumulator_transaction = None
            return snapshot
        except Exception as error:
            if window_checkpoint is not None:
                _attempt_restoration(
                    error,
                    "window policy",
                    lambda: self._window_policy.restore_transaction(window_checkpoint),
                )
            _attempt_restoration(
                error,
                "contribution cache",
                lambda: _restore_contribution_cache(
                    self._contributions,
                    candle.timestamp,
                    cache_added=cache_added,
                    removed=cache_removed,
                ),
            )
            if accumulator_transaction is not None:
                _attempt_restoration(error, "profile accumulator", accumulator_transaction.rollback)
            if node_checkpoint_ready:
                _attempt_restoration(
                    error,
                    "node tracker",
                    lambda: self._node_tracker.restore_transaction(node_checkpoint),
                )
            if allocation_checkpoint_ready:
                _attempt_restoration(
                    error,
                    "allocation",
                    lambda: self._allocation.restore_transaction(allocation_checkpoint),
                )
            if binning_checkpoint_ready:
                _attempt_restoration(
                    error,
                    "binning",
                    lambda: self._binning.restore_transaction(binning_checkpoint),
                )
            raise

    def replay(self, candles: Iterable[AuctionCandle]) -> tuple[AuctionSnapshot, ...]:
        snapshots: list[AuctionSnapshot] = []
        for candle in candles:
            snapshot = self.update(candle)
            if snapshot is not None:
                snapshots.append(snapshot)
        return tuple(snapshots)

    def reset(self) -> None:
        self._clear_window()
        self._latest_input = None
        self._latest_snapshot = None
        self._symbol = None
        self._timeframe = None

    def _validate_stream(self, candle: AuctionCandle) -> StructuralEventKind | None:
        previous = self._latest_input
        if previous is None:
            return None
        if candle.symbol != self._symbol:
            raise ValueError("auction stream symbol changed")
        if candle.timeframe != self._timeframe:
            raise ValueError("auction stream timeframe changed")
        if candle.timestamp == previous.timestamp:
            raise ValueError("duplicate candle timestamp")
        if candle.timestamp < previous.timestamp:
            raise ValueError("out-of-order candle timestamp")
        if candle.segment_id != previous.segment_id:
            return StructuralEventKind.SEGMENT_RESET
        if candle.timestamp - previous.timestamp != self._expected_interval:
            if self._gap_policy is GapPolicy.REJECT:
                raise ValueError("material gap in auction stream")
            return StructuralEventKind.GAP_RESET
        return None

    def _clear_profile_only(self) -> None:
        self._accumulator.clear()
        self._contributions.clear()
        self._node_tracker.reset()

    def _clear_window(self) -> None:
        self._clear_profile_only()
        self._window_policy.reset()

    def _record_input(self, candle: AuctionCandle) -> None:
        self._latest_input = candle
        self._symbol = candle.symbol
        self._timeframe = candle.timeframe


def _attempt_restoration(
    original_error: Exception,
    label: str,
    restore: Callable[[], None],
) -> None:
    try:
        restore()
    except Exception as restoration_error:
        original_error.add_note(
            f"{label} restore failed: {type(restoration_error).__name__}: {restoration_error}"
        )


def _release_committed_transactions(
    *release_actions: tuple[Callable[[], None], Callable[[], None]],
) -> None:
    """Treat post-commit journal release as non-throwing cleanup.

    Both journals remain rollbackable through their validated finalize phase. Once engine state is
    swapped, release can no longer turn a successful committed update into a failed update.
    """

    for release, ensure_released in release_actions:
        try:
            release()
        except Exception:
            try:
                ensure_released()
            except Exception:
                pass


def _restore_contribution_cache(
    cache: dict[datetime, BinContribution],
    added_timestamp: datetime,
    *,
    cache_added: bool,
    removed: list[tuple[datetime, BinContribution]],
) -> None:
    if cache_added:
        cache.pop(added_timestamp, None)
    for timestamp, contribution in removed:
        cache[timestamp] = contribution


def _profile_candle(candle: AuctionCandle) -> ProfileCandle:
    return ProfileCandle(
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def _planned_active_candles(
    policy: WindowPolicy,
    candle: AuctionCandle,
    transition: WindowTransition,
    *,
    reset_before: bool,
) -> tuple[AuctionCandle, ...]:
    if reset_before or transition.reset:
        retained: tuple[AuctionCandle, ...] = ()
    else:
        retained = policy.active_candles[len(transition.evictions) :]
    return (*retained, candle) if transition.include else retained


def _classify_location(price: float, profile: ProfileSnapshot) -> AuctionLocation:
    if (
        profile.poc_index is None
        or profile.value_area_low_index is None
        or profile.value_area_high_index is None
    ):
        return AuctionLocation.NO_VALUE
    price_index = profile.binning.bin_index(price)
    if price_index < profile.value_area_low_index:
        return AuctionLocation.BELOW_VALUE
    if price_index > profile.value_area_high_index:
        return AuctionLocation.ABOVE_VALUE
    if price_index == profile.poc_index:
        return AuctionLocation.POINT_OF_CONTROL
    if price_index < profile.poc_index:
        return AuctionLocation.LOWER_VALUE
    return AuctionLocation.UPPER_VALUE


def _has_value_references(profile: ProfileSnapshot) -> bool:
    return (
        profile.poc_index is not None
        and profile.value_area_low_index is not None
        and profile.value_area_high_index is not None
    )


def _structural_events(
    candle: AuctionCandle,
    previous: AuctionSnapshot | None,
    profile: ProfileSnapshot,
    location: AuctionLocation,
    *,
    reset_kinds: tuple[StructuralEventKind, ...],
    window_id: str,
) -> tuple[StructuralEvent, ...]:
    if reset_kinds:
        return tuple(_event(candle, kind, window_id, ()) for kind in reset_kinds)
    if previous is None:
        return ()
    events: list[StructuralEvent] = []
    if (
        previous.profile.poc_index is not None
        and profile.poc_index is not None
        and previous.profile.poc_index != profile.poc_index
    ):
        events.append(
            _event(
                candle,
                StructuralEventKind.POC_MIGRATION,
                window_id,
                (
                    ("from_bin", str(previous.profile.poc_index)),
                    ("to_bin", str(profile.poc_index)),
                ),
            )
        )
    outside = {AuctionLocation.BELOW_VALUE, AuctionLocation.ABOVE_VALUE}
    if previous.location not in outside and location in outside:
        events.append(_event(candle, StructuralEventKind.VALUE_BREAKOUT, window_id, ()))
    elif previous.location in outside and location not in outside:
        events.append(_event(candle, StructuralEventKind.VALUE_REENTRY, window_id, ()))
    return tuple(events)


def _event(
    candle: AuctionCandle,
    kind: StructuralEventKind,
    window_id: str,
    payload: tuple[tuple[str, str], ...],
) -> StructuralEvent:
    material = "|".join(
        (
            candle.symbol,
            candle.timeframe,
            candle.timestamp.isoformat(),
            str(candle.segment_id),
            window_id,
            kind,
            repr(payload),
        )
    )
    return StructuralEvent(
        event_id=hashlib.sha256(material.encode()).hexdigest()[:24],
        timestamp=candle.timestamp,
        kind=kind,
        payload=payload,
    )
