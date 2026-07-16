from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from market_structure_lab.events.models import EventKind, MarketEvent, make_event
from market_structure_lab.events.overlap import build_overlap_report, kind_pair_key
from market_structure_lab.features.models import FeatureRow


ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)


def event(
    kind: EventKind,
    start_minute: int,
    end_minute: int,
    *,
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
) -> MarketEvent:
    cutoff = ORIGIN + timedelta(minutes=end_minute)
    row = FeatureRow(
        timestamp=cutoff - timedelta(minutes=1),
        information_cutoff=cutoff,
        symbol=symbol,
        timeframe="1m",
        segment_id=segment_id,
        dataset_version="DS-000001",
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-bars-v1:max-bars=60",
        feature_set_id="FS-000001",
        registry_id="FR-0123456789AB",
        values={"poc_distance": float(end_minute)},
    )
    return make_event(
        kind,
        ORIGIN + timedelta(minutes=start_minute),
        cutoff,
        row,
        f"{kind.value}-v1",
    )


def test_overlap_report_is_empty_and_immutable_for_no_events() -> None:
    report = build_overlap_report([])

    assert report.total_events == 0
    assert report.by_kind == {}
    assert report.interval_pair_count == 0
    assert report.affected_event_count == 0
    assert report.affected_event_ratio == 0.0
    assert report.max_concurrency == 0
    assert report.by_kind_pair == {}
    assert isinstance(report.by_kind, MappingProxyType)
    assert isinstance(report.by_kind_pair, MappingProxyType)


def test_overlap_report_uses_half_open_intervals_and_stream_boundaries() -> None:
    fixed = event(EventKind.FIXED_WINDOW, 0, 3)
    rolling = event(EventKind.ROLLING_WINDOW, 1, 4)
    boundary_touch = event(EventKind.CHANGE_POINT, 3, 5)
    other_segment = event(EventKind.EXPANSION, 1, 4, segment_id=1)
    other_symbol = event(EventKind.VALUE_EXIT, 1, 4, symbol="ETHUSDT")

    report = build_overlap_report(
        [other_symbol, boundary_touch, fixed, other_segment, rolling]
    )

    assert report.total_events == 5
    assert report.by_kind == {
        "change_point": 1,
        "expansion": 1,
        "fixed_window": 1,
        "rolling_window": 1,
        "value_exit": 1,
    }
    assert report.interval_pair_count == 2
    assert report.affected_event_count == 3
    assert report.affected_event_ratio == pytest.approx(0.6)
    assert report.max_concurrency == 2
    assert report.by_kind_pair == {
        kind_pair_key(EventKind.FIXED_WINDOW, EventKind.ROLLING_WINDOW): 1,
        kind_pair_key(EventKind.CHANGE_POINT, EventKind.ROLLING_WINDOW): 1,
    }


def test_overlap_report_counts_all_pairs_and_maximum_concurrency() -> None:
    first = event(EventKind.FIXED_WINDOW, 0, 4)
    second = event(EventKind.ROLLING_WINDOW, 1, 3)
    third = event(EventKind.EXPANSION, 2, 5)

    report = build_overlap_report([third, first, second])

    assert report.interval_pair_count == 3
    assert report.affected_event_count == 3
    assert report.affected_event_ratio == 1.0
    assert report.max_concurrency == 3
    assert report.by_kind_pair == {
        "expansion|fixed_window": 1,
        "expansion|rolling_window": 1,
        "fixed_window|rolling_window": 1,
    }
    assert json.loads(report.canonical_json())["interval_pair_count"] == 3


def test_overlap_report_counts_same_kind_pairs() -> None:
    first = event(EventKind.NODE_TEST, 0, 3)
    second = event(EventKind.NODE_TEST, 1, 4)
    report = build_overlap_report([first, second])

    assert report.by_kind == {"node_test": 2}
    assert report.by_kind_pair == {"node_test|node_test": 1}


def test_overlap_report_rejects_duplicate_event_identity() -> None:
    candidate = event(EventKind.FIXED_WINDOW, 0, 3)
    with pytest.raises(ValueError, match="duplicate event_id"):
        build_overlap_report([candidate, candidate])
