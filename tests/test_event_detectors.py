from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.auction.engine import (
    AuctionLocation,
    AuctionSnapshot,
    StructuralEvent,
    StructuralEventKind,
)
from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.events.change_points import (
    CausalChangePointDetector,
    ChangePointConfig,
    ExpansionConfig,
    ExpansionDetector,
)
from market_structure_lab.events.models import EventKind
from market_structure_lab.events.structural_events import StructuralEventDetector
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.structure.nodes import NodeKind, ProfileNode

BASE = datetime(2025, 1, 1, tzinfo=UTC)
BINS = FixedStepBins(step=1.0)
REGISTRY = FeatureRegistry(
    "FS-000001",
    tuple(
        FeatureDefinition(
            name=name,
            definition=f"Fixture {name}",
            family=FeatureFamily.SEQUENCE,
            value_kind=FeatureValueKind.FLOAT,
            units="ratio",
            required_prior_observations=0,
            missing_policy=MissingPolicy.NULL,
            version="v1",
            leakage_class=LeakageClass.AT_CUTOFF,
        )
        for name in ("realized_volatility_20", "signal")
    ),
)


def observation(
    minute: int,
    *,
    signal: float | None = 1.0,
    volatility: float | None = 1.0,
    volume: float = 10.0,
    close: float = 100.0,
    nodes: tuple[ProfileNode, ...] = (),
    event_kinds: tuple[StructuralEventKind, ...] = (),
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
) -> tuple[AuctionSnapshot, FeatureRow]:
    timestamp = BASE + timedelta(minutes=minute)
    candle = AuctionCandle(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        segment_id=segment_id,
    )
    profile = ProfileSnapshot(
        bin_volumes={int(close): volume},
        total_volume=volume,
        poc_index=int(close),
        value_area_low_index=int(close),
        value_area_high_index=int(close),
        vwap=close,
        binning=BINS,
        allocation_id="fixture-allocation-v1",
    )
    events = tuple(
        StructuralEvent(
            event_id=f"phase2-{minute}-{kind.value}",
            timestamp=timestamp,
            kind=kind,
            payload=(("fixture", "true"),),
        )
        for kind in event_kinds
    )
    snapshot = AuctionSnapshot(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        candle_count=1,
        latest_candle=candle,
        active_timestamps=(timestamp,),
        profile=profile,
        location=AuctionLocation.POINT_OF_CONTROL,
        nodes=nodes,
        migration=None,
        events=events,
        window_id="rolling-bars:60",
        window_version="rolling-bars-v1:60",
        dataset_version="DS-000001",
        config_version="auction-config-v1",
        profile_definition_id="profile-v1",
    )
    row = FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp + timedelta(minutes=1),
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        dataset_version="DS-000001",
        config_version="auction-config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-bars-v1:60",
        feature_set_id="FS-000001",
        registry_id=REGISTRY.registry_id,
        values={"signal": signal, "realized_volatility_20": volatility},
    )
    return snapshot, row


def test_change_point_baseline_excludes_current_and_uses_exact_cutoff() -> None:
    detector = CausalChangePointDetector(
        ChangePointConfig("signal", history_window=3, min_history=3, robust_threshold=4.0),
        registry=REGISTRY,
    )
    for minute, value in enumerate((1.0, 1.0, 1.0)):
        assert detector.update(*observation(minute, signal=value)) is None

    event = detector.update(*observation(3, signal=100.0))

    assert event is not None
    assert event.kind is EventKind.CHANGE_POINT
    assert event.start == BASE + timedelta(minutes=3)
    assert event.end == BASE + timedelta(minutes=4)
    assert event.information_cutoff == event.end
    assert event.metadata["prior_median"] == 1.0
    assert event.metadata["prior_mad"] == 0.0
    assert event.metadata["prior_observation_count"] == 3
    assert event.feature_values["signal"] == 100.0


def test_change_point_prefix_is_invariant_to_future_poison() -> None:
    prefix = tuple(observation(i, signal=value) for i, value in enumerate((1.0, 2.0, 1.0, 8.0)))
    config = ChangePointConfig("signal", history_window=3, min_history=3, robust_threshold=3.0)
    first = CausalChangePointDetector(config, registry=REGISTRY)
    first_events = tuple(first.update(*item) for item in prefix)
    poisoned = CausalChangePointDetector(config, registry=REGISTRY)
    poisoned_events = tuple(
        poisoned.update(*item)
        for item in (*prefix, observation(4, signal=1_000_000.0))
    )

    assert tuple(
        None if event is None else event.canonical_json() for event in first_events
    ) == tuple(
        None if event is None else event.canonical_json()
        for event in poisoned_events[: len(prefix)]
    )


def test_change_point_resets_before_segment_or_phase2_reset_current() -> None:
    detector = CausalChangePointDetector(
        ChangePointConfig("signal", history_window=2, min_history=2, robust_threshold=2.0),
        registry=REGISTRY,
    )
    detector.update(*observation(0, signal=1.0))
    detector.update(*observation(1, signal=1.0))
    assert detector.update(*observation(3, signal=100.0, segment_id=1)) is None
    assert detector.history_size == 1

    detector.update(*observation(4, signal=100.0, segment_id=1))
    reset = observation(
        6,
        signal=1_000.0,
        segment_id=1,
        event_kinds=(StructuralEventKind.GAP_RESET,),
    )
    assert detector.update(*reset) is None
    assert detector.history_size == 1


def test_unexplained_gap_and_identity_disagreement_fail_atomically() -> None:
    detector = CausalChangePointDetector(
        ChangePointConfig("signal", history_window=2, min_history=2),
        registry=REGISTRY,
    )
    detector.update(*observation(0))
    with pytest.raises(ValueError, match="gap"):
        detector.update(*observation(2))
    assert detector.history_size == 1


@pytest.mark.parametrize(
    "field",
    ("dataset_version", "config_version", "profile_definition_id", "window_version"),
)
def test_snapshot_identity_drift_cannot_reuse_detector_history(field: str) -> None:
    detector = CausalChangePointDetector(
        ChangePointConfig("signal", history_window=2, min_history=2),
        registry=REGISTRY,
    )
    first_snapshot, first_row = observation(0)
    detector.update(first_snapshot, first_row)
    snapshot, row = observation(1)
    drifted_snapshot = replace(snapshot, **{field: "drifted-v2"})
    if field == "dataset_version":
        row = replace(row, dataset_version="drifted-v2")
    elif field == "config_version":
        row = replace(row, config_version="drifted-v2")
    elif field == "profile_definition_id":
        row = replace(row, profile_version="drifted-v2")
    else:
        row = replace(row, window_policy_id="drifted-v2")

    with pytest.raises(ValueError, match=field):
        detector.update(drifted_snapshot, row)

    assert detector.history_size == 1


@pytest.mark.parametrize("field", ("feature_set_id", "registry_id"))
def test_feature_identity_drift_cannot_reuse_detector_history(field: str) -> None:
    detector = CausalChangePointDetector(
        ChangePointConfig("signal", history_window=2, min_history=2),
        registry=REGISTRY,
    )
    detector.update(*observation(0))
    snapshot, row = observation(1)

    with pytest.raises(ValueError, match=field):
        detector.update(snapshot, replace(row, **{field: "drifted-v2"}))

    assert detector.history_size == 1

    snapshot, row = observation(1)
    with pytest.raises(ValueError, match="dataset version"):
        detector.update(snapshot, replace(row, dataset_version="DS-999999"))
    with pytest.raises(ValueError, match="information[_ ]cutoff"):
        detector.update(snapshot, replace(row, timestamp=row.timestamp - timedelta(minutes=1)))
    assert detector.history_size == 1


def test_expansion_uses_completed_prior_medians_and_documents_zero_baseline() -> None:
    detector = ExpansionDetector(
        ExpansionConfig(history_window=2, min_history=2, volatility_multiplier=2, volume_multiplier=2),
        registry=REGISTRY,
    )
    detector.update(*observation(0, volatility=0.0, volume=0.0))
    detector.update(*observation(1, volatility=0.0, volume=0.0))

    event = detector.update(*observation(2, volatility=0.5, volume=0.0))

    assert event is not None
    assert event.kind is EventKind.EXPANSION
    assert event.metadata["prior_volatility_median"] == 0.0
    assert event.metadata["volatility_expanded"] is True
    assert event.metadata["volume_expanded"] is False
    assert event.metadata["zero_baseline_rule"] == "positive_current_is_expansion"


def test_volume_expansion_does_not_require_a_populated_volatility_feature() -> None:
    detector = ExpansionDetector(
        ExpansionConfig(history_window=2, min_history=2, volatility_multiplier=2, volume_multiplier=2),
        registry=REGISTRY,
    )
    detector.update(*observation(0, volatility=None, volume=10.0))
    detector.update(*observation(1, volatility=None, volume=10.0))

    event = detector.update(*observation(2, volatility=None, volume=30.0))

    assert event is not None
    assert event.kind is EventKind.EXPANSION
    assert event.metadata["volatility_expanded"] is False
    assert event.metadata["volume_expanded"] is True


@pytest.mark.parametrize(
    ("phase2_kind", "event_kind"),
    [
        (StructuralEventKind.VALUE_BREAKOUT, EventKind.VALUE_EXIT),
        (StructuralEventKind.VALUE_REENTRY, EventKind.VALUE_REENTRY),
        (StructuralEventKind.POC_MIGRATION, EventKind.POC_MIGRATION),
    ],
)
def test_phase2_structural_events_are_lifted_at_observable_cutoff(
    phase2_kind: StructuralEventKind, event_kind: EventKind
) -> None:
    detector = StructuralEventDetector(registry=REGISTRY)
    snapshot, row = observation(0, event_kinds=(phase2_kind,))

    (event,) = detector.update(snapshot, row)

    assert event.kind is event_kind
    assert event.start == row.timestamp
    assert event.end == row.information_cutoff
    assert event.metadata["source_candle_open_timestamp"] == "2025-01-01T00:00:00Z"
    assert event.metadata["observable_trigger_timestamp"] == "2025-01-01T00:01:00Z"
    assert event.metadata["phase2_event_id"] == f"phase2-0-{phase2_kind.value}"


def test_node_test_and_traversal_use_only_prior_snapshot_zones() -> None:
    prior_node = ProfileNode(
        kind=NodeKind.HVN,
        price=100.0,
        volume=20.0,
        prominence=10.0,
        start_index=99,
        end_index=101,
        representative_index=100,
        price_low=99.0,
        price_high=101.0,
        binning_id=BINS.definition_id,
    )
    poison = ProfileNode(
        kind=NodeKind.LVN,
        price=1_000.0,
        volume=1.0,
        prominence=1.0,
        price_low=999.0,
        price_high=1_001.0,
    )

    test_detector = StructuralEventDetector(registry=REGISTRY)
    test_detector.update(*observation(0, close=98.0, nodes=(prior_node,)))
    (node_test,) = test_detector.update(*observation(1, close=100.0, nodes=(poison,)))
    assert node_test.kind is EventKind.NODE_TEST
    assert node_test.metadata["node_source"] == "prior_snapshot"
    assert node_test.metadata["prior_node_low"] == 99.0
    assert node_test.metadata["prior_node_high"] == 101.0

    traversal_detector = StructuralEventDetector(registry=REGISTRY)
    traversal_detector.update(*observation(0, close=98.0, nodes=(prior_node,)))
    (traversal,) = traversal_detector.update(*observation(1, close=102.0, nodes=(poison,)))
    assert traversal.kind is EventKind.NODE_TRAVERSAL
    assert traversal.start == BASE + timedelta(minutes=1)
    assert traversal.end == BASE + timedelta(minutes=2)


def test_structural_detector_never_crosses_reset_boundary() -> None:
    node = ProfileNode(NodeKind.HVN, 100.0, 20.0, 10.0, price_low=99.0, price_high=101.0)
    detector = StructuralEventDetector(registry=REGISTRY)
    detector.update(*observation(0, close=98.0, nodes=(node,)))

    events = detector.update(
        *observation(
            2,
            close=102.0,
            nodes=(node,),
            segment_id=1,
            event_kinds=(StructuralEventKind.SEGMENT_RESET,),
        )
    )

    assert events == ()
