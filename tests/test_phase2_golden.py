from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from market_structure_lab.auction import (
    AuctionCandle,
    AuctionEngine,
    AuctionSnapshot,
    RollingBars,
    UTCDayWindow,
    canonical_snapshot_json,
    snapshot_stream_sha256,
)
from market_structure_lab.profiles import (
    Candle,
    FixedStepBins,
    UniformAllocation,
    TriangularCloseAllocation,
    TypicalPriceAllocation,
    calculate_profile,
)
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.profiles.volume import value_area_indices
from market_structure_lab.structure import detect_profile_nodes

FIXTURES = Path(__file__).parent / "fixtures" / "phase2"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_profile_golden_fixture_locks_integer_bins_and_value_area() -> None:
    fixture = _fixture("profile_golden_v1.json")
    definition = fixture["binning"]
    binning = FixedStepBins(step=float(definition["step"]), origin=float(definition["origin"]))
    allocation = UniformAllocation()
    candles = [
        Candle(**{key: float(value) for key, value in row.items()})
        for row in fixture["candles"]
    ]

    profile = calculate_profile(
        [allocation.allocate(candle, binning) for candle in candles],
        binning=binning,
        allocation_id=allocation.model_id,
        value_area_fraction=float(fixture["value_area_fraction"]),
    )
    expected = fixture["expected"]

    assert list(profile.bin_volumes.items()) == [
        (int(index), float(volume)) for index, volume in expected["bin_volumes"]
    ]
    assert profile.total_volume == float(expected["total_volume"])
    assert profile.poc_index == expected["poc_index"]
    assert profile.value_area_low_index == expected["value_area_low_index"]
    assert profile.value_area_high_index == expected["value_area_high_index"]
    assert profile.point_of_control == float(expected["point_of_control"])
    assert profile.value_area_low == float(expected["value_area_low"])
    assert profile.value_area_high == float(expected["value_area_high"])


def test_auction_golden_fixture_replays_byte_identically() -> None:
    fixture = _fixture("auction_rolling_3_v1.json")
    start = datetime.fromisoformat(fixture["start"].replace("Z", "+00:00")).astimezone(UTC)
    candles = tuple(
        AuctionCandle(
            timestamp=start + timedelta(minutes=row["minute"]),
            symbol=fixture["symbol"],
            timeframe=fixture["timeframe"],
            open=float(row["price"]),
            high=float(row["price"]),
            low=float(row["price"]),
            close=float(row["price"]),
            volume=float(row["volume"]),
            segment_id=0,
        )
        for row in fixture["candles"]
    )

    def replay() -> tuple[AuctionSnapshot, ...]:
        profile = fixture["profile"]
        engine = AuctionEngine(
            binning=FixedStepBins(
                step=float(profile["binning"]["step"]),
                origin=float(profile["binning"]["origin"]),
            ),
            allocation=UniformAllocation(),
            window_policy=RollingBars(max_bars=fixture["window"]["max_bars"]),
            dataset_version=fixture["fixture_version"],
            config_version="phase2-golden-config-v1",
            value_area_fraction=float(profile["value_area_fraction"]),
        )
        return engine.replay(candles)

    first = replay()
    second = replay()
    assert [canonical_snapshot_json(item) for item in first] == [
        canonical_snapshot_json(item) for item in second
    ]
    assert snapshot_stream_sha256(first) == snapshot_stream_sha256(second)
    assert snapshot_stream_sha256(first) == fixture["expected_snapshot_stream_sha256"]
    assert [snapshot_stream_sha256([item]) for item in first] == fixture[
        "expected_snapshot_sha256"
    ]

    final = first[-1]
    expected = fixture["expected_final"]
    assert list(final.profile.bin_volumes.items()) == [
        (int(index), float(volume)) for index, volume in expected["bin_volumes"]
    ]
    assert final.profile.total_volume == float(expected["total_volume"])
    assert final.profile.poc_index == expected["poc_index"]
    assert final.profile.value_area_low_index == expected["value_area_low_index"]
    assert final.profile.value_area_high_index == expected["value_area_high_index"]
    assert final.profile.vwap == pytest.approx(float(expected["vwap"]))
    assert final.location.value == expected["location"]
    assert [timestamp.minute for timestamp in final.active_timestamps] == expected["active_minutes"]


def test_representation_golden_fixture_locks_allocations_sparse_ties_and_plateaus() -> None:
    fixture = _fixture("representation_cases_v1.json")
    binning = FixedStepBins(step=1.0)
    models = {
        "typical-price-v1": TypicalPriceAllocation(),
        "triangular-close-v1": TriangularCloseAllocation(),
    }
    for case in fixture["allocation_cases"]:
        candle = Candle(**{key: float(value) for key, value in case["candle"].items()})
        contribution = models[case["model"]].allocate(candle, binning)
        assert dict(contribution.bin_volumes) == pytest.approx(
            {int(index): float(volume) for index, volume in case["expected_bin_volumes"]}
        )
        assert contribution.price_volume_numerator == pytest.approx(case["expected_numerator"])

    value_case = fixture["sparse_tie_value_area"]
    assert value_area_indices(
        {int(index): float(volume) for index, volume in value_case["bin_volumes"]},
        point_of_control=value_case["poc_index"],
        target_volume=float(value_case["target_volume"]),
    ) == tuple(value_case["expected"])

    node_case = fixture["plateau_nodes"]
    volumes = {int(index): float(volume) for index, volume in node_case["bin_volumes"]}
    profile = ProfileSnapshot(
        bin_volumes=volumes,
        total_volume=sum(volumes.values()),
        poc_index=1,
        value_area_low_index=0,
        value_area_high_index=3,
        vwap=None,
        binning=binning,
        allocation_id=fixture["fixture_version"],
    )
    nodes = detect_profile_nodes(profile, min_prominence=float(node_case["min_prominence"]))
    assert [
        {
            "kind": node.kind.value,
            "start_index": node.start_index,
            "end_index": node.end_index,
            "representative_index": node.representative_index,
            "prominence": node.prominence,
        }
        for node in nodes
    ] == node_case["expected"]


def test_representation_golden_fixture_locks_utc_day_window_boundary() -> None:
    case = _fixture("representation_cases_v1.json")["utc_day_boundary"]

    def at(value: str) -> AuctionCandle:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        return AuctionCandle(
            timestamp=timestamp,
            symbol="BTCUSDT",
            timeframe="1m",
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
            segment_id=0,
        )

    policy = UTCDayWindow()
    before = at(case["before"])
    first = policy.transition(before)
    second = policy.transition(at(case["after"]))

    assert first.window_id == case["expected_before_window_id"]
    assert second.window_id == case["expected_after_window_id"]
    assert second.reset is True
    assert second.evictions == (before,)
