from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import log, sqrt
from pathlib import Path

import pytest

from market_structure_lab.auction.engine import (
    AuctionLocation,
    AuctionSnapshot,
    StructuralEvent,
    StructuralEventKind,
)
from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.features.builder import FeatureBuilder
from market_structure_lab.features.builtin import (
    BUILTIN_DEFINITIONS,
    builtin_feature_registry,
)
from market_structure_lab.features.registry import FeatureDefinition, FeatureRegistry
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.structure.nodes import NodeKind, ProfileNode

BASE = datetime(2025, 1, 1, tzinfo=UTC)
BINS = FixedStepBins(step=1.0)
GOLDEN = Path(__file__).parent / "fixtures" / "phase3" / "feature_golden_v1.json"


def snapshot(
    minute: int,
    *,
    close: float = 100.0,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10.0,
    bin_volumes: dict[int, float] | None = None,
    poc: int | None = 100,
    val: int | None = 99,
    vah: int | None = 101,
    vwap: float | None = 100.0,
    location: AuctionLocation = AuctionLocation.POINT_OF_CONTROL,
    nodes: tuple[ProfileNode, ...] = (),
    event_kinds: tuple[StructuralEventKind, ...] = (),
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
) -> AuctionSnapshot:
    timestamp = BASE + timedelta(minutes=minute)
    open_value = close if open_price is None else open_price
    high_value = max(open_value, close) if high is None else high
    low_value = min(open_value, close) if low is None else low
    volumes = {100: volume} if bin_volumes is None else bin_volumes
    profile = ProfileSnapshot(
        bin_volumes=volumes,
        total_volume=sum(volumes.values()),
        poc_index=poc,
        value_area_low_index=val,
        value_area_high_index=vah,
        vwap=vwap,
        binning=BINS,
        allocation_id="fixture-allocation-v1",
    )
    candle = AuctionCandle(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        volume=volume,
        segment_id=segment_id,
    )
    events = tuple(
        StructuralEvent(
            event_id=f"event-{minute}-{kind.value}",
            timestamp=timestamp,
            kind=kind,
            payload=(),
        )
        for kind in event_kinds
    )
    return AuctionSnapshot(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        candle_count=1,
        latest_candle=candle,
        active_timestamps=(timestamp,),
        profile=profile,
        location=location,
        nodes=nodes,
        migration=None,
        events=events,
        window_id="rolling-bars:60",
        window_version="rolling-bars-v1:60",
        dataset_version="dataset-v1",
        config_version="config-v1",
        profile_definition_id="profile-v1",
    )


def test_builtin_registry_locks_all_audited_features_and_history_requirements() -> None:
    registry = builtin_feature_registry()
    required = {item.name: item.required_prior_observations for item in registry.definitions}

    assert registry.feature_set_id == "FS-000001"
    assert len(registry.definitions) == 26
    assert required["auction_location"] == 0
    assert required["poc_velocity_close_1"] == 1
    assert required["inside_value_rate_20"] == 19
    assert required["volume_relative_median_20"] == 19
    assert required["realized_volatility_20"] == 20
    assert required["volatility_normalized_return_20"] == 20
    assert required["return_autocorrelation_1_20"] == 20
    assert {item.family.value for item in registry.definitions} == {"auction", "sequence"}


def test_golden_current_and_one_observation_formulas() -> None:
    hvn = ProfileNode(NodeKind.HVN, 101.0, 30.0, 10.0, persistence=4)
    lvn = ProfileNode(NodeKind.LVN, 103.0, 5.0, 5.0, persistence=1)
    builder = FeatureBuilder()
    first = snapshot(
        0,
        close=100.0,
        open_price=99.0,
        high=102.0,
        low=98.0,
        volume=10.0,
        bin_volumes={98: 10.0, 99: 20.0, 100: 30.0, 101: 20.0, 102: 10.0},
    )
    second = snapshot(
        1,
        close=102.0,
        open_price=100.0,
        high=104.0,
        low=99.0,
        volume=20.0,
        bin_volumes={100: 10.0, 101: 30.0, 102: 60.0, 103: 20.0},
        poc=102,
        val=101,
        vah=103,
        vwap=101.5,
        nodes=(hvn, lvn),
    )

    first_row = builder.update(first)
    values = builder.update(second).values
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["expected_second_values"]

    assert first_row.values["poc_velocity_close_1"] is None
    assert first_row.values["log_return_1"] is None
    assert tuple(values) == tuple(sorted(expected))
    for name, expected_value in expected.items():
        if isinstance(expected_value, float):
            assert values[name] == pytest.approx(expected_value)
        else:
            assert values[name] == expected_value


def test_history_windows_become_eligible_at_exact_observations() -> None:
    builder = FeatureBuilder()
    rows = []
    for minute in range(21):
        events = (StructuralEventKind.VALUE_REENTRY,) if minute in {0, 5} else ()
        rows.append(
            builder.update(
                snapshot(
                    minute,
                    close=100.0 + minute + (minute % 3),
                    volume=float(minute + 1),
                    location=AuctionLocation.LOWER_VALUE,
                    event_kinds=events,
                )
            )
        )

    assert rows[18].values["inside_value_rate_20"] is None
    assert rows[18].values["value_reentry_rate_20"] is None
    assert rows[18].values["volume_relative_median_20"] is None
    assert rows[19].values["inside_value_rate_20"] == 1.0
    assert rows[19].values["value_reentry_rate_20"] == pytest.approx(0.1)
    assert rows[19].values["volume_relative_median_20"] == pytest.approx(20 / 10.5 - 1)
    assert rows[19].values["realized_volatility_20"] is None
    expected_returns = [
        log(
            (100.0 + minute + minute % 3)
            / (100.0 + minute - 1 + (minute - 1) % 3)
        )
        for minute in range(1, 21)
    ]
    expected_volatility = sqrt(sum(value * value for value in expected_returns) / 20)
    assert rows[20].values["realized_volatility_20"] == pytest.approx(expected_volatility)
    assert rows[20].values["volatility_normalized_return_20"] == pytest.approx(
        expected_returns[-1] / expected_volatility
    )
    assert rows[20].values["return_autocorrelation_1_20"] is not None


def test_zero_denominators_and_undefined_references_remain_null_without_epsilon() -> None:
    builder = FeatureBuilder()
    zero = snapshot(
        0,
        close=0.0,
        volume=0.0,
        bin_volumes={},
        poc=None,
        val=None,
        vah=None,
        vwap=None,
        location=AuctionLocation.NO_VALUE,
    )
    values = builder.update(zero).values

    for name in (
        "poc_distance_close",
        "value_width_close",
        "poc_volume_share",
        "vwap_distance_close",
        "close_value_position",
        "nearest_hvn_distance_close",
        "nearest_lvn_distance_close",
        "range_close_fraction",
        "body_range_ratio",
        "upper_wick_range_ratio",
        "lower_wick_range_ratio",
    ):
        assert values[name] is None
    assert values["max_node_persistence_bars"] == 0
    assert values["auction_location"] == "no_value"


def test_zero_trailing_volatility_and_volume_baseline_remain_null() -> None:
    builder = FeatureBuilder()
    rows = [
        builder.update(snapshot(minute, close=100.0, volume=0.0))
        for minute in range(21)
    ]

    assert rows[-1].values["realized_volatility_20"] == 0.0
    assert rows[-1].values["volatility_normalized_return_20"] is None
    assert rows[-1].values["return_autocorrelation_1_20"] is None
    assert rows[-1].values["volume_relative_median_20"] is None
    assert rows[-1].values["log_volume_ratio_1"] is None


@pytest.mark.parametrize(
    "kind",
    [
        StructuralEventKind.WINDOW_RESET,
        StructuralEventKind.GAP_RESET,
        StructuralEventKind.SEGMENT_RESET,
    ],
)
def test_explicit_reset_events_clear_history_before_current_row(
    kind: StructuralEventKind,
) -> None:
    builder = FeatureBuilder()
    builder.update(snapshot(0, close=100.0))
    reset_row = builder.update(
        snapshot(2, close=110.0, event_kinds=(kind,), segment_id=int(kind is StructuralEventKind.SEGMENT_RESET))
    )

    assert reset_row.values["log_return_1"] is None
    assert reset_row.values["poc_velocity_close_1"] is None
    assert reset_row.values["location_dwell_bars"] == 1
    assert builder.history_size == 1


def test_segment_change_without_event_is_a_boundary_but_rolling_eviction_is_not() -> None:
    builder = FeatureBuilder()
    first = builder.update(snapshot(0, close=100.0))
    second = builder.update(snapshot(1, close=101.0))
    reset = builder.update(snapshot(2, close=102.0, segment_id=1))

    assert first.values["location_dwell_bars"] == 1
    assert second.values["location_dwell_bars"] == 2
    assert second.values["log_return_1"] is not None
    assert reset.values["location_dwell_bars"] == 1
    assert reset.values["log_return_1"] is None


def test_order_series_and_unexplained_gap_fail_atomically() -> None:
    builder = FeatureBuilder()
    first = builder.update(snapshot(0))
    for invalid, message in (
        (snapshot(0), "duplicate"),
        (snapshot(-1), "out-of-order"),
        (snapshot(1, symbol="ETHUSDT"), "symbol"),
        (snapshot(1, timeframe="5m"), "timeframe"),
        (snapshot(2), "gap"),
    ):
        with pytest.raises(ValueError, match=message):
            builder.update(invalid)
        assert builder.history_size == 1
    valid = builder.update(snapshot(1, close=101.0))
    assert valid.values["log_return_1"] == pytest.approx(log(1.01))
    assert first.values["log_return_1"] is None


@pytest.mark.parametrize(
    "field",
    ("dataset_version", "config_version", "profile_definition_id", "window_version"),
)
def test_stream_identity_drift_cannot_reuse_trailing_history(field: str) -> None:
    builder = FeatureBuilder()
    first = snapshot(0)
    builder.update(first)

    with pytest.raises(ValueError, match=field):
        builder.update(replace(snapshot(1), **{field: "drifted-v2"}))

    assert builder.history_size == 1
    assert builder.update(snapshot(1, close=101.0)).values["log_return_1"] is not None


def test_prefix_invariance_future_poisoning_and_bounded_history() -> None:
    prefix = tuple(snapshot(minute, close=100.0 + minute) for minute in range(30))
    first = FeatureBuilder().build(prefix)
    poisoned = FeatureBuilder().build(
        (*prefix, snapshot(30, close=1_000_000.0, volume=1_000_000.0))
    )
    bounded = FeatureBuilder()
    bounded.build(tuple(snapshot(minute, close=100.0 + minute) for minute in range(100)))

    assert tuple(row.canonical_json() for row in poisoned[: len(prefix)]) == tuple(
        row.canonical_json() for row in first
    )
    assert bounded.history_size == 21


def test_builder_rejects_metadata_drift_under_reused_feature_set_id() -> None:
    changed = list(BUILTIN_DEFINITIONS)
    original = changed[0]
    changed[0] = FeatureDefinition(
        **{
            **original.to_dict(),
            "definition": "Changed formula metadata.",
            "family": original.family,
            "value_kind": original.value_kind,
            "missing_policy": original.missing_policy,
            "leakage_class": original.leakage_class,
            "allowed_categories": original.allowed_categories,
        }
    )
    drifted = FeatureRegistry("FS-000001", changed)

    with pytest.raises(ValueError, match="exactly match"):
        FeatureBuilder(drifted)
