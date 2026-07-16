from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Iterable

import pytest

from market_structure_lab.events.models import EventKind, MarketEvent, make_event
from market_structure_lab.events.overlap import build_overlap_report, kind_pair_key
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)


ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)
REGISTRY = FeatureRegistry(
    "FS-000001",
    (
        FeatureDefinition(
            name="poc_distance",
            definition="Signed distance from close to POC.",
            family=FeatureFamily.AUCTION,
            value_kind=FeatureValueKind.FLOAT,
            units="close_fraction",
            required_prior_observations=0,
            missing_policy=MissingPolicy.NULL,
            version="1",
            leakage_class=LeakageClass.AT_CUTOFF,
        ),
    ),
)


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
        registry_id=REGISTRY.registry_id,
        values={"poc_distance": float(end_minute)},
    )
    return make_event(
        kind,
        ORIGIN + timedelta(minutes=start_minute),
        cutoff,
        row,
        f"{kind.value}-v1",
        registry=REGISTRY,
    )


def canonical_order(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    return sorted(
        events,
        key=lambda candidate: (
            candidate.dataset_version,
            candidate.config_version,
            candidate.profile_version,
            candidate.window_policy_id,
            candidate.feature_set_id,
            candidate.registry_id,
            candidate.registry_sha256,
            candidate.symbol,
            candidate.timeframe,
            candidate.segment_id,
            candidate.start,
            candidate.end,
            candidate.event_id,
        ),
    )


def test_overlap_report_is_empty_and_immutable_for_no_events() -> None:
    report = build_overlap_report([])

    assert report.total_events == 0
    assert report.by_kind == {}
    assert report.interval_pair_count == 0
    assert report.affected_event_count == 0
    assert report.affected_event_ratio == 0.0
    assert report.max_concurrency == 0
    assert report.max_active_events == 0
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
        canonical_order([other_symbol, boundary_touch, fixed, other_segment, rolling])
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
    assert report.max_active_events == 2
    assert report.by_kind_pair == {
        kind_pair_key(EventKind.FIXED_WINDOW, EventKind.ROLLING_WINDOW): 1,
        kind_pair_key(EventKind.CHANGE_POINT, EventKind.ROLLING_WINDOW): 1,
    }


def test_overlap_report_counts_all_pairs_and_maximum_concurrency() -> None:
    first = event(EventKind.FIXED_WINDOW, 0, 4)
    second = event(EventKind.ROLLING_WINDOW, 1, 3)
    third = event(EventKind.EXPANSION, 2, 5)

    report = build_overlap_report(canonical_order([third, first, second]))

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


def test_overlap_report_rejects_unordered_input() -> None:
    first = event(EventKind.FIXED_WINDOW, 0, 1)
    second = event(EventKind.FIXED_WINDOW, 2, 3)

    with pytest.raises(ValueError, match="canonical overlap order"):
        build_overlap_report([second, first])


def test_overlap_report_retains_only_active_intervals_for_long_stream() -> None:
    def non_overlapping_events() -> Iterable[MarketEvent]:
        for minute in range(0, 20_000, 2):
            yield event(EventKind.FIXED_WINDOW, minute, minute + 1)

    report = build_overlap_report(non_overlapping_events())

    assert report.total_events == 10_000
    assert report.interval_pair_count == 0
    assert report.affected_event_count == 0
    assert report.max_concurrency == 1
    assert report.max_active_events == 1
    assert json.loads(report.canonical_json())["max_active_events"] == 1


def test_overlap_report_matches_naive_half_open_reference() -> None:
    kinds = tuple(EventKind)
    ordered = canonical_order(
        event(kinds[index % len(kinds)], index, index + 1 + (index * 7) % 11)
        for index in range(200)
    )

    expected_pairs = 0
    expected_affected: set[int] = set()
    expected_kind_pairs: Counter[str] = Counter()
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            right = ordered[right_index]
            if right.start >= left.end:
                break
            expected_pairs += 1
            expected_affected.update((left_index, right_index))
            expected_kind_pairs[kind_pair_key(left.kind, right.kind)] += 1
    expected_max_concurrency = max(
        sum(candidate.start <= point.start < candidate.end for candidate in ordered)
        for point in ordered
    )

    report = build_overlap_report(iter(ordered))

    assert report.interval_pair_count == expected_pairs
    assert report.affected_event_count == len(expected_affected)
    assert report.by_kind_pair == expected_kind_pairs
    assert report.max_concurrency == expected_max_concurrency
    assert report.max_active_events == expected_max_concurrency
