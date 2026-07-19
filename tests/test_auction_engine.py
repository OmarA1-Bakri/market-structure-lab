from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import market_structure_lab.auction.engine as engine_module

from market_structure_lab.auction import (
    AuctionCandle,
    AuctionEngine,
    AuctionLocation,
    FixedWindow,
    GapPolicy,
    RollingBars,
    RollingDuration,
    StructuralEventKind,
    UTCDayWindow,
    WindowPolicy,
    WindowTransition,
)
from market_structure_lab.auction.windows import WindowPolicyCheckpoint
from market_structure_lab.profiles.accumulator import (
    ProfileAccumulator,
    ProfileAccumulatorTransaction,
)
from market_structure_lab.profiles import FixedStepBins, UniformAllocation
from market_structure_lab.profiles.allocation import AllocationModel, BinContribution
from market_structure_lab.profiles.binning import BinDefinition
from market_structure_lab.profiles.models import Candle as ProfileCandle
from market_structure_lab.profiles.models import ProfileWorkBudget
from market_structure_lab.structure.nodes import NodePersistenceTracker


def auction_engine() -> AuctionEngine:
    return AuctionEngine(
        binning=FixedStepBins(step=1.0),
        allocation=UniformAllocation(),
        window_policy=RollingBars(max_bars=10),
        dataset_version="fixture-v1",
        config_version="auction-v1",
    )


def _engine(
    *,
    window_policy: WindowPolicy | None = None,
    gap_policy: GapPolicy = GapPolicy.REJECT,
    allocation: AllocationModel | None = None,
    binning: BinDefinition | None = None,
    work_budget: ProfileWorkBudget = ProfileWorkBudget(),
) -> AuctionEngine:
    return AuctionEngine(
        binning=binning or FixedStepBins(step=1.0),
        allocation=allocation or UniformAllocation(),
        window_policy=window_policy or RollingBars(max_bars=3),
        dataset_version="fixture-v1",
        config_version="auction-v1",
        gap_policy=gap_policy,
        work_budget=work_budget,
    )


def candle(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    symbol: str = "BTCUSDT",
    segment_id: int = 0,
    base: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> AuctionCandle:
    return AuctionCandle(
        timestamp=base + timedelta(minutes=minute),
        symbol=symbol,
        timeframe="1m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        segment_id=segment_id,
    )


def _simple_candle(
    minute: int,
    *,
    segment_id: int = 0,
    base: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> AuctionCandle:
    return candle(
        minute,
        open_=100.0,
        high=102.0,
        low=100.0,
        close=101.0,
        volume=30.0,
        segment_id=segment_id,
        base=base,
    )


def _object_state(value: object) -> tuple[tuple[str, Any], ...]:
    names = set(vars(value)) if hasattr(value, "__dict__") else set()
    for owner in type(value).__mro__:
        slots = getattr(owner, "__slots__", ())
        if isinstance(slots, str):
            names.add(slots)
        else:
            names.update(slots)
    return tuple(
        sorted(
            (name, _state_value(getattr(value, name)))
            for name in names
            if name not in {"__dict__", "__weakref__"} and hasattr(value, name)
        )
    )


def _state_value(value: object) -> object:
    if isinstance(value, (deque, list, tuple)):
        return tuple(_state_value(item) for item in value)
    if isinstance(value, Mapping):
        return tuple(sorted((_state_value(key), _state_value(item)) for key, item in value.items()))
    return value


def _engine_state(engine: AuctionEngine) -> tuple[object, ...]:
    return (
        _object_state(engine._window_policy),
        _object_state(engine._binning),
        tuple(engine._accumulator._active),
        engine._accumulator._active_cell_count,
        tuple(
            (index, tuple(parts)) for index, parts in sorted(engine._accumulator._bin_parts.items())
        ),
        engine._accumulator._transaction,
        _object_state(engine._accumulator.allocation),
        _object_state(engine._allocation),
        tuple(sorted(engine._contributions.items())),
        tuple(engine._node_tracker._previous),
        engine._latest_input,
        engine._latest_snapshot,
        engine._symbol,
        engine._timeframe,
    )


FaultInstaller = Callable[[pytest.MonkeyPatch], None]


class _StatefulAllocation:
    model_id = "stateful-allocation-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def transaction_checkpoint(self) -> int:
        return self.calls

    def restore_transaction(self, checkpoint: object) -> None:
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, int):
            raise TypeError("allocation checkpoint must be an integer")
        self.calls = checkpoint

    def allocate(
        self,
        candle: ProfileCandle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[ProfileCandle] | None = None,
    ) -> BinContribution:
        self.calls += 1
        if self.fail:
            raise RuntimeError("contribution fault")
        return UniformAllocation().allocate(
            candle,
            binning,
            lower_timeframe_candles=lower_timeframe_candles,
        )


class _StatefulBins:
    version = "stateful-bins-v1"

    def __init__(self) -> None:
        self.calls = 0
        self._delegate = FixedStepBins(step=1.0, version=self.version)

    @property
    def definition_id(self) -> str:
        return self._delegate.definition_id

    def transaction_checkpoint(self) -> int:
        return self.calls

    def restore_transaction(self, checkpoint: object) -> None:
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, int):
            raise TypeError("binning checkpoint must be an integer")
        self.calls = checkpoint

    def snapshot_definition(self) -> FixedStepBins:
        return self._delegate

    def bin_index(self, price: float) -> int:
        self.calls += 1
        return self._delegate.bin_index(price)

    def price_for_index(self, index: int) -> float:
        self.calls += 1
        return self._delegate.price_for_index(index)


class _CommitFaultRollingBars(RollingBars):
    __slots__ = ("fail_after_commit",)

    def __init__(self, *, max_bars: int) -> None:
        super().__init__(max_bars=max_bars)
        self.fail_after_commit = False

    def commit_transition(
        self,
        candle: AuctionCandle,
        transition: WindowTransition,
        *,
        reset_before: bool = False,
    ) -> None:
        super().commit_transition(candle, transition, reset_before=reset_before)
        if self.fail_after_commit:
            raise RuntimeError("window commit fault")


class _MutableDefinitionBins:
    version = "mutable-definition-v1"

    def __init__(self) -> None:
        self.origin = 0.0

    @property
    def _definition(self) -> FixedStepBins:
        return FixedStepBins(step=1.0, origin=self.origin, version=self.version)

    @property
    def definition_id(self) -> str:
        return self._definition.definition_id

    def transaction_checkpoint(self) -> float:
        return self.origin

    def restore_transaction(self, checkpoint: object) -> None:
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, float):
            raise TypeError("binning checkpoint must be a float")
        self.origin = checkpoint

    def snapshot_definition(self) -> FixedStepBins:
        return self._definition

    def bin_index(self, price: float) -> int:
        return self._definition.bin_index(price)

    def price_for_index(self, index: int) -> float:
        return self._definition.price_for_index(index)


class _RestoreFailingAllocation:
    model_id = "restore-failing-allocation-v1"

    def transaction_checkpoint(self) -> None:
        return None

    def restore_transaction(self, checkpoint: object) -> None:
        del checkpoint
        raise RuntimeError("allocation restore fault")

    def allocate(
        self,
        candle: ProfileCandle,
        binning: BinDefinition,
        *,
        lower_timeframe_candles: Sequence[ProfileCandle] | None = None,
    ) -> BinContribution:
        del lower_timeframe_candles
        binning.bin_index(candle.low)
        raise ValueError("primary allocation fault")


class _NoDeepcopyRollingBars(RollingBars):
    __slots__ = ("reject_copy",)

    def __init__(self, *, max_bars: int) -> None:
        super().__init__(max_bars=max_bars)
        self.reject_copy = False

    def __deepcopy__(self, memo: object) -> object:
        del memo
        if self.reject_copy:
            raise AssertionError("window policy must not be deep-copied")
        copied = _NoDeepcopyRollingBars(max_bars=self._max_bars)
        copied._active.extend(self._active)
        return copied


def _install_window_transition_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_during_plan(
        policy: RollingBars,
        item: AuctionCandle,
        *,
        reset_before: bool = False,
    ) -> object:
        del policy, item, reset_before
        raise RuntimeError("window transition fault")

    monkeypatch.setattr(RollingBars, "plan_transition", fail_during_plan)


def _install_allocator_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        UniformAllocation,
        "allocate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("allocator fault")),
    )


def _install_remove_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ProfileAccumulator.remove

    def fail_after_mutation(
        accumulator: ProfileAccumulator,
        contribution: object,
    ) -> None:
        original(accumulator, contribution)  # type: ignore[arg-type]
        raise RuntimeError("remove fault")

    monkeypatch.setattr(ProfileAccumulator, "remove", fail_after_mutation)


def _install_add_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ProfileAccumulator.add_contribution

    def fail_after_mutation(
        accumulator: ProfileAccumulator,
        contribution: object,
    ) -> None:
        original(accumulator, contribution)  # type: ignore[arg-type]
        raise RuntimeError("add fault")

    monkeypatch.setattr(ProfileAccumulator, "add_contribution", fail_after_mutation)


def _install_snapshot_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ProfileAccumulator,
        "snapshot",
        lambda self: (_ for _ in ()).throw(RuntimeError("snapshot fault")),
    )


def _install_node_detection_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_module,
        "detect_profile_nodes",
        lambda profile: (_ for _ in ()).throw(RuntimeError("node detection fault")),
    )


def _install_node_tracker_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_mutation(tracker: NodePersistenceTracker, nodes: object) -> object:
        tracker._previous = ("fault",)  # type: ignore[assignment]
        raise RuntimeError("node tracker fault")

    monkeypatch.setattr(NodePersistenceTracker, "update", fail_after_mutation)


def _install_structural_event_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_module,
        "_structural_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("event fault")),
    )


def test_auction_engine_updates_profile_and_classifies_close_location() -> None:
    auction = auction_engine()
    first = auction.update(candle(0, open_=100.0, high=102.0, low=100.0, close=100.0, volume=30.0))
    second = auction.update(candle(1, open_=101.0, high=104.0, low=101.0, close=104.0, volume=60.0))

    assert first is not None and second is not None
    assert first.candle_count == 1
    assert first.profile.poc_index == 100
    assert first.location is AuctionLocation.POINT_OF_CONTROL
    assert second.candle_count == 2
    assert second.profile.poc_index == 101
    assert second.location is AuctionLocation.ABOVE_VALUE


def test_auction_engine_snapshot_is_stable_between_updates() -> None:
    auction = auction_engine()
    first = auction.update(candle(0, open_=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))
    auction.update(candle(1, open_=105.0, high=105.0, low=105.0, close=105.0, volume=90.0))

    assert first is not None
    assert first.profile.bin_volumes == {100: 10.0}
    assert first.profile.poc_index == 100


def test_auction_engine_reset_allows_a_fresh_series_without_old_state() -> None:
    auction = auction_engine()
    auction.update(candle(0, open_=100.0, high=100.0, low=100.0, close=100.0, volume=10.0))

    auction.reset()
    snapshot = auction.update(
        candle(
            0,
            open_=105.0,
            high=105.0,
            low=105.0,
            close=105.0,
            volume=90.0,
            symbol="ETHUSDT",
        )
    )

    assert snapshot is not None
    assert snapshot.candle_count == 1
    assert snapshot.profile.bin_volumes == {105: 90.0}
    assert snapshot.location is AuctionLocation.POINT_OF_CONTROL


@pytest.mark.parametrize(
    ("installer", "max_bars"),
    [
        (_install_window_transition_fault, 3),
        (_install_allocator_fault, 3),
        (_install_remove_fault, 1),
        (_install_add_fault, 3),
        (_install_snapshot_fault, 3),
        (_install_node_detection_fault, 3),
        (_install_node_tracker_fault, 3),
        (_install_structural_event_fault, 3),
    ],
    ids=(
        "window-transition",
        "allocator",
        "profile-remove",
        "profile-add",
        "profile-snapshot",
        "node-detection",
        "node-tracker",
        "structural-events",
    ),
)
def test_failed_update_restores_every_mutable_collaborator_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    installer: FaultInstaller,
    max_bars: int,
) -> None:
    first = _simple_candle(0)
    second = _simple_candle(1)
    auction = _engine(window_policy=RollingBars(max_bars=max_bars))
    auction.update(first)
    before = _engine_state(auction)

    with monkeypatch.context() as fault:
        installer(fault)
        with pytest.raises(RuntimeError, match="fault"):
            auction.update(second)

    assert _engine_state(auction) == before

    clean = _engine(window_policy=RollingBars(max_bars=max_bars))
    clean.update(first)
    assert auction.update(second) == clean.update(second)


def test_missing_cached_eviction_failure_leaves_exact_pre_call_state() -> None:
    auction = _engine(window_policy=RollingBars(max_bars=1))
    auction.update(_simple_candle(0))
    auction._contributions.clear()
    before = _engine_state(auction)

    with pytest.raises(RuntimeError, match="without an active contribution"):
        auction.update(_simple_candle(1))

    assert _engine_state(auction) == before


@pytest.mark.parametrize(
    ("auction", "next_candle"),
    [
        (
            _engine(window_policy=RollingBars(max_bars=3), gap_policy=GapPolicy.RESET),
            _simple_candle(2),
        ),
        (
            _engine(window_policy=RollingBars(max_bars=3)),
            _simple_candle(1, segment_id=1),
        ),
        (
            _engine(window_policy=UTCDayWindow()),
            _simple_candle(1, base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC)),
        ),
    ],
    ids=("gap-reset", "segment-reset", "window-reset"),
)
def test_failed_reset_update_restores_the_pre_boundary_state(
    monkeypatch: pytest.MonkeyPatch,
    auction: AuctionEngine,
    next_candle: AuctionCandle,
) -> None:
    if isinstance(auction._window_policy, UTCDayWindow):
        first = _simple_candle(0, base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC))
    else:
        first = _simple_candle(0)
    auction.update(first)
    before = _engine_state(auction)

    with monkeypatch.context() as fault:
        _install_snapshot_fault(fault)
        with pytest.raises(RuntimeError, match="snapshot fault"):
            auction.update(next_candle)

    assert _engine_state(auction) == before


@pytest.mark.parametrize(
    ("previous", "current", "gap_policy", "source_kind"),
    [
        (
            _simple_candle(0, base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC)),
            _simple_candle(
                1,
                segment_id=1,
                base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
            ),
            GapPolicy.REJECT,
            StructuralEventKind.SEGMENT_RESET,
        ),
        (
            _simple_candle(0, base=datetime(2025, 1, 1, 23, 58, tzinfo=UTC)),
            _simple_candle(2, base=datetime(2025, 1, 1, 23, 58, tzinfo=UTC)),
            GapPolicy.RESET,
            StructuralEventKind.GAP_RESET,
        ),
    ],
    ids=("segment-and-window", "gap-and-window"),
)
def test_source_and_window_reset_causes_are_both_preserved(
    previous: AuctionCandle,
    current: AuctionCandle,
    gap_policy: GapPolicy,
    source_kind: StructuralEventKind,
) -> None:
    auction = _engine(window_policy=UTCDayWindow(), gap_policy=gap_policy)
    auction.update(previous)

    snapshot = auction.update(current)

    assert snapshot is not None
    assert tuple(event.kind for event in snapshot.events) == (
        source_kind,
        StructuralEventKind.WINDOW_RESET,
    )


def test_fixed_window_rejection_leaves_exact_engine_state() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    auction = _engine(window_policy=FixedWindow(start=start, end=start + timedelta(minutes=1)))
    auction.update(_simple_candle(0))
    before = _engine_state(auction)

    with pytest.raises(ValueError, match="beyond fixed window end"):
        auction.update(_simple_candle(1))

    assert _engine_state(auction) == before


def test_successful_updates_preserve_caller_owned_collaborator_identity() -> None:
    policy = RollingBars(max_bars=3)
    allocation = UniformAllocation()
    binning = FixedStepBins(step=1.0)
    auction = _engine(window_policy=policy, allocation=allocation, binning=binning)

    snapshot = auction.update(_simple_candle(0))

    assert snapshot is not None
    assert auction._window_policy is policy
    assert auction._allocation is allocation
    assert auction._accumulator.allocation is allocation
    assert auction._binning is binning
    assert auction._accumulator.binning is binning
    assert snapshot.profile.binning is binning


def test_mutating_contribution_failure_rolls_back_and_retries_on_same_allocation() -> None:
    allocation = _StatefulAllocation()
    auction = _engine(allocation=allocation)
    allocation.fail = True
    before_failure = _engine_state(auction)

    with pytest.raises(RuntimeError, match="contribution fault"):
        auction.update(_simple_candle(0))

    assert _engine_state(auction) == before_failure
    assert allocation.calls == 0
    allocation.fail = False
    snapshot = auction.update(_simple_candle(0))
    assert snapshot is not None
    assert allocation.calls == 1
    assert auction._allocation is allocation


def test_stateful_binning_rolls_back_after_late_failure_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binning = _StatefulBins()
    auction = _engine(binning=binning)
    before = _engine_state(auction)

    with monkeypatch.context() as fault:
        _install_structural_event_fault(fault)
        with pytest.raises(RuntimeError, match="event fault"):
            auction.update(_simple_candle(0))

    assert _engine_state(auction) == before
    assert binning.calls == 0

    snapshot = auction.update(_simple_candle(0))
    assert snapshot is not None
    assert snapshot.profile.bin_volumes == {100: 10.0, 101: 10.0, 102: 10.0}
    assert auction._binning is binning
    assert snapshot.profile.binning is binning.snapshot_definition()


def test_steady_state_transaction_uses_bounded_in_place_deltas() -> None:
    policy = _NoDeepcopyRollingBars(max_bars=64)
    allocation = _StatefulAllocation()
    auction = _engine(window_policy=policy, allocation=allocation)
    for minute in range(64):
        auction.update(_simple_candle(minute))

    policy.reject_copy = True
    active_container = policy._active
    accumulator_active = auction._accumulator._active
    accumulator_bins = auction._accumulator._bin_parts
    cache = auction._contributions
    allocation.fail = True
    before = _engine_state(auction)

    with pytest.raises(RuntimeError, match="contribution fault"):
        auction.update(_simple_candle(64))

    assert _engine_state(auction) == before
    assert auction._window_policy is policy
    assert policy._active is active_container
    assert auction._accumulator._active is accumulator_active
    assert auction._accumulator._bin_parts is accumulator_bins
    assert auction._contributions is cache


@pytest.mark.parametrize(
    ("previous", "current", "gap_policy", "expected_kinds"),
    [
        (
            _simple_candle(0, base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC)),
            _simple_candle(
                1,
                segment_id=1,
                base=datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
            ),
            GapPolicy.REJECT,
            (StructuralEventKind.SEGMENT_RESET, StructuralEventKind.WINDOW_RESET),
        ),
        (
            _simple_candle(0, base=datetime(2025, 1, 1, 23, 58, tzinfo=UTC)),
            _simple_candle(2, base=datetime(2025, 1, 1, 23, 58, tzinfo=UTC)),
            GapPolicy.RESET,
            (StructuralEventKind.GAP_RESET, StructuralEventKind.WINDOW_RESET),
        ),
    ],
    ids=("segment-and-window", "gap-and-window"),
)
def test_simultaneous_reset_late_failure_rolls_back_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    previous: AuctionCandle,
    current: AuctionCandle,
    gap_policy: GapPolicy,
    expected_kinds: tuple[StructuralEventKind, ...],
) -> None:
    policy = UTCDayWindow()
    auction = _engine(window_policy=policy, gap_policy=gap_policy)
    auction.update(previous)
    before = _engine_state(auction)

    with monkeypatch.context() as fault:
        _install_structural_event_fault(fault)
        with pytest.raises(RuntimeError, match="event fault"):
            auction.update(current)

    assert _engine_state(auction) == before
    assert auction._window_policy is policy
    snapshot = auction.update(current)
    assert snapshot is not None
    assert tuple(event.kind for event in snapshot.events) == expected_kinds


def test_commit_after_mutation_failure_restores_window_and_retries_cleanly() -> None:
    policy = _CommitFaultRollingBars(max_bars=3)
    auction = _engine(window_policy=policy)
    first = _simple_candle(0)
    second = _simple_candle(1)
    auction.update(first)
    before = _engine_state(auction)
    policy.fail_after_commit = True

    with pytest.raises(RuntimeError, match="window commit fault"):
        auction.update(second)

    policy.fail_after_commit = False
    assert _engine_state(auction) == before
    clean = _engine(window_policy=RollingBars(max_bars=3))
    clean.update(first)
    assert auction.update(second) == clean.update(second)


@pytest.mark.parametrize("commit_owner", ("accumulator", "window"))
def test_post_commit_fault_restores_exact_state_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    commit_owner: str,
) -> None:
    auction = _engine(window_policy=RollingBars(max_bars=3))
    first = _simple_candle(0)
    second = _simple_candle(1)
    auction.update(first)
    before = _engine_state(auction)
    owner = (
        ProfileAccumulatorTransaction if commit_owner == "accumulator" else WindowPolicyCheckpoint
    )
    original = owner.commit
    failed = False

    def commit_then_fail(self) -> None:
        nonlocal failed
        original(self)
        if not failed:
            failed = True
            raise RuntimeError(f"{commit_owner} post-commit fault")

    with monkeypatch.context() as fault:
        fault.setattr(owner, "commit", commit_then_fail)
        with pytest.raises(RuntimeError, match="post-commit fault"):
            auction.update(second)

    assert _engine_state(auction) == before
    clean = _engine(window_policy=RollingBars(max_bars=3))
    clean.update(first)
    assert auction.update(second) == clean.update(second)


@pytest.mark.parametrize("finalize_owner", ("accumulator", "window"))
def test_post_finalize_fault_restores_exact_state_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    finalize_owner: str,
) -> None:
    auction = _engine(window_policy=RollingBars(max_bars=3))
    first = _simple_candle(0)
    second = _simple_candle(1)
    auction.update(first)
    before = _engine_state(auction)
    owner = (
        ProfileAccumulatorTransaction if finalize_owner == "accumulator" else WindowPolicyCheckpoint
    )
    original = owner.finalize
    failed = False

    def finalize_then_fail(self) -> None:
        nonlocal failed
        original(self)
        if not failed:
            failed = True
            raise RuntimeError(f"{finalize_owner} post-finalize fault")

    with monkeypatch.context() as fault:
        fault.setattr(owner, "finalize", finalize_then_fail)
        with pytest.raises(RuntimeError, match="post-finalize fault"):
            auction.update(second)

    assert _engine_state(auction) == before
    clean = _engine(window_policy=RollingBars(max_bars=3))
    clean.update(first)
    assert auction.update(second) == clean.update(second)


@pytest.mark.parametrize("release_owner", ("accumulator", "window"))
@pytest.mark.parametrize("fault_before_close", (False, True))
def test_release_fault_is_non_throwing_cleanup_after_successful_commit(
    monkeypatch: pytest.MonkeyPatch,
    release_owner: str,
    fault_before_close: bool,
) -> None:
    auction = _engine(window_policy=RollingBars(max_bars=3))
    clean = _engine(window_policy=RollingBars(max_bars=3))
    first = _simple_candle(0)
    second = _simple_candle(1)
    auction.update(first)
    clean.update(first)
    owner = (
        ProfileAccumulatorTransaction if release_owner == "accumulator" else WindowPolicyCheckpoint
    )
    original = owner.release

    def release_then_fail(self) -> None:
        if not fault_before_close:
            original(self)
        raise RuntimeError(f"{release_owner} post-release cleanup fault")

    with monkeypatch.context() as fault:
        fault.setattr(owner, "release", release_then_fail)
        snapshot = auction.update(second)

    assert snapshot == clean.update(second)
    assert _engine_state(auction) == _engine_state(clean)
    assert auction.update(_simple_candle(2)) == clean.update(_simple_candle(2))


def test_excluded_candle_does_not_invoke_stateful_profile_collaborators() -> None:
    start = datetime(2025, 1, 1, 0, 1, tzinfo=UTC)
    allocation = _StatefulAllocation()
    binning = _StatefulBins()
    auction = _engine(
        window_policy=FixedWindow(start=start, end=start + timedelta(minutes=1)),
        allocation=allocation,
        binning=binning,
    )

    assert auction.update(_simple_candle(0)) is None
    assert allocation.calls == 0
    assert binning.calls == 0


def test_profile_snapshot_freezes_mutable_binning_definition_and_prices() -> None:
    binning = _MutableDefinitionBins()
    auction = _engine(binning=binning)

    snapshot = auction.update(_simple_candle(0))

    assert snapshot is not None
    original_binning_id = snapshot.profile.binning_id
    original_prices = snapshot.profile.price_volumes
    binning.origin = 1_000.0
    assert auction._binning is binning
    assert snapshot.profile.binning is not binning
    assert snapshot.profile.binning_id == original_binning_id
    assert snapshot.profile.price_volumes == original_prices
    assert original_binning_id in snapshot.profile_definition_id
    with pytest.raises(ValueError, match="binning definition identity changed"):
        auction.update(_simple_candle(1))


def test_restore_failures_do_not_mask_primary_error_or_skip_later_collaborators() -> None:
    binning = _StatefulBins()
    auction = _engine(binning=binning, allocation=_RestoreFailingAllocation())

    with pytest.raises(ValueError, match="primary allocation fault") as captured:
        auction.update(_simple_candle(0))

    assert binning.calls == 0
    assert any("allocation restore" in note for note in getattr(captured.value, "__notes__", ()))


def test_touched_bin_budget_rejects_before_allocator_and_retries_cleanly() -> None:
    budget = ProfileWorkBudget(maximum_touched_bins_per_candle=2, maximum_active_profile_bins=4)
    allocation_attempts: list[ProfileCandle] = []

    class InvocationProbe(_StatefulAllocation):
        def allocate(
            self,
            candle: ProfileCandle,
            binning: BinDefinition,
            *,
            lower_timeframe_candles: Sequence[ProfileCandle] | None = None,
        ) -> BinContribution:
            allocation_attempts.append(candle)
            return super().allocate(
                candle,
                binning,
                lower_timeframe_candles=lower_timeframe_candles,
            )

    allocation = InvocationProbe()
    binning = _StatefulBins()
    auction = _engine(allocation=allocation, binning=binning, work_budget=budget)
    before = _engine_state(auction)

    with pytest.raises(ValueError, match="touched-bin budget"):
        auction.update(_simple_candle(0))

    assert _engine_state(auction) == before
    assert allocation_attempts == []
    assert allocation.calls == 0
    assert binning.calls == 0
    retry = replace(_simple_candle(0), high=101.0, close=101.0)
    clean = _engine(
        allocation=_StatefulAllocation(),
        binning=_StatefulBins(),
        work_budget=budget,
    )
    assert auction.update(retry) == clean.update(retry)


def test_active_bin_budget_rejects_before_profile_mutation_and_retries_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = ProfileWorkBudget(maximum_touched_bins_per_candle=1, maximum_active_profile_bins=2)
    auction = _engine(window_policy=RollingBars(max_bars=3), work_budget=budget)
    first = replace(_simple_candle(0), open=100.0, high=100.0, low=100.0, close=100.0)
    second = replace(_simple_candle(1), open=101.0, high=101.0, low=101.0, close=101.0)
    overflow = replace(_simple_candle(2), open=102.0, high=102.0, low=102.0, close=102.0)
    auction.update(first)
    auction.update(second)
    before = _engine_state(auction)
    mutation_attempts: list[BinContribution] = []
    original_add = auction._accumulator.add_contribution

    def record_add(contribution: BinContribution) -> None:
        mutation_attempts.append(contribution)
        original_add(contribution)

    monkeypatch.setattr(auction._accumulator, "add_contribution", record_add)

    with pytest.raises(ValueError, match="active profile-bin budget"):
        auction.update(overflow)

    assert mutation_attempts == []
    assert _engine_state(auction) == before
    monkeypatch.undo()
    retry = replace(overflow, open=100.0, high=100.0, low=100.0, close=100.0)
    clean = _engine(window_policy=RollingBars(max_bars=3), work_budget=budget)
    clean.update(first)
    clean.update(second)
    assert auction.update(retry) == clean.update(retry)


def test_active_contribution_cell_budget_rejects_before_profile_mutation() -> None:
    budget = ProfileWorkBudget(
        maximum_touched_bins_per_candle=1,
        maximum_active_profile_bins=1,
        maximum_active_contributions=2,
        maximum_active_contribution_cells=2,
    )
    auction = _engine(window_policy=RollingBars(max_bars=3), work_budget=budget)

    def one_bin(minute: int) -> AuctionCandle:
        return replace(
            _simple_candle(minute),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
        )

    auction.update(one_bin(0))
    auction.update(one_bin(1))
    before = _engine_state(auction)

    with pytest.raises(ValueError, match="active contribution"):
        auction.update(one_bin(2))

    assert _engine_state(auction) == before


def test_profile_budget_changes_snapshot_and_profile_definition_identity() -> None:
    narrow = ProfileWorkBudget(
        maximum_touched_bins_per_candle=4,
        maximum_active_profile_bins=8,
    )
    wider = replace(narrow, maximum_active_profile_bins=9)
    narrow_snapshot = _engine(work_budget=narrow).update(_simple_candle(0))
    wider_snapshot = _engine(work_budget=wider).update(_simple_candle(0))

    assert narrow_snapshot is not None and wider_snapshot is not None
    assert narrow_snapshot.profile.work_budget_id == narrow.definition_id
    assert wider_snapshot.profile.work_budget_id == wider.definition_id
    assert narrow_snapshot.profile_definition_id != wider_snapshot.profile_definition_id


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_touched_bins_per_candle": 0},
        {"maximum_active_profile_bins": 0},
        {"maximum_touched_bins_per_candle": 10**12},
        {"maximum_active_profile_bins": 10**12},
        {"maximum_active_contributions": 0},
        {"maximum_active_contribution_cells": 0},
        {"maximum_active_contributions": 10**12},
        {"maximum_active_contribution_cells": 10**12},
    ],
)
def test_profile_work_budget_rejects_invalid_or_unsafe_limits(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="budget"):
        ProfileWorkBudget(**changes)


@pytest.mark.parametrize(
    "policy",
    (
        RollingDuration(duration=timedelta(days=3_000)),
        FixedWindow(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2033, 1, 1, tzinfo=UTC),
        ),
    ),
)
def test_engine_preflights_window_capacity_against_expected_cadence(policy: WindowPolicy) -> None:
    with pytest.raises(ValueError, match="retain.*candles|exceeding the safe maximum"):
        _engine(window_policy=policy)
