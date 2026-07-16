from __future__ import annotations

import pytest

from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.structure.nodes import (
    NodeKind,
    NodePersistenceTracker,
    ProfileNode,
    detect_profile_nodes,
)
from market_structure_lab.structure.value_migration import (
    ValueMigrationDirection,
    compare_value_migration,
)


def _snapshot(
    bin_volumes: dict[int, float],
    *,
    poc_index: int = 1,
    value_area_low_index: int = 0,
    value_area_high_index: int = 2,
) -> ProfileSnapshot:
    return ProfileSnapshot(
        bin_volumes=bin_volumes,
        total_volume=sum(bin_volumes.values()),
        poc_index=poc_index,
        value_area_low_index=value_area_low_index,
        value_area_high_index=value_area_high_index,
        vwap=None,
        binning=FixedStepBins(step=0.5, origin=100.0),
        allocation_id="test-allocation-v1",
    )


def test_detects_contiguous_plateaus_as_deterministic_bin_zones() -> None:
    profile = _snapshot({0: 10.0, 1: 40.0, 2: 40.0, 3: 10.0, 4: 5.0, 5: 5.0, 6: 20.0})

    nodes = detect_profile_nodes(profile, min_prominence=5.0)

    assert [
        (
            node.kind,
            node.start_index,
            node.end_index,
            node.width_bins,
            node.representative_index,
            node.price,
            node.price_low,
            node.price_high,
            node.prominence,
        )
        for node in nodes
    ] == [
        (NodeKind.HVN, 1, 2, 2, 1, 100.5, 100.5, 101.0, 30.0),
        (NodeKind.LVN, 4, 5, 2, 4, 102.0, 102.0, 102.5, 5.0),
    ]


def test_smoothing_never_uses_bins_across_a_missing_index() -> None:
    combined = _snapshot({0: 0.0, 1: 9.0, 2: 0.0, 4: 100.0, 5: 0.0, 6: 100.0})
    left = _snapshot({0: 0.0, 1: 9.0, 2: 0.0})
    right = _snapshot({4: 100.0, 5: 0.0, 6: 100.0})

    combined_nodes = detect_profile_nodes(combined, smoothing_radius=1)
    separate_nodes = [
        *detect_profile_nodes(left, smoothing_radius=1),
        *detect_profile_nodes(right, smoothing_radius=1),
    ]

    assert combined_nodes == separate_nodes
    assert [(node.kind, node.representative_index) for node in combined_nodes] == [
        (NodeKind.LVN, 1),
        (NodeKind.HVN, 5),
    ]


def test_prominence_threshold_is_inclusive() -> None:
    profile = _snapshot({0: 10.0, 1: 15.0, 2: 10.0})

    assert len(detect_profile_nodes(profile, min_prominence=5.0)) == 1
    assert detect_profile_nodes(profile, min_prominence=5.000_001) == []


def test_node_persistence_tracks_overlapping_same_kind_zones_and_resets() -> None:
    tracker = NodePersistenceTracker()
    first = ProfileNode(
        kind=NodeKind.HVN,
        price=100.5,
        volume=40.0,
        prominence=20.0,
        start_index=1,
        end_index=2,
        representative_index=1,
        price_low=100.5,
        price_high=101.0,
    )
    shifted = ProfileNode(
        kind=NodeKind.HVN,
        price=101.0,
        volume=45.0,
        prominence=25.0,
        start_index=2,
        end_index=3,
        representative_index=2,
        price_low=101.0,
        price_high=101.5,
    )
    opposite_kind = ProfileNode(
        kind=NodeKind.LVN,
        price=101.0,
        volume=5.0,
        prominence=5.0,
        start_index=2,
        end_index=2,
        representative_index=2,
        price_low=101.0,
        price_high=101.0,
    )

    assert tracker.update([first])[0].persistence == 1
    persisted, independent = tracker.update([shifted, opposite_kind])
    assert persisted.persistence == 2
    assert independent.persistence == 1

    tracker.reset()

    assert tracker.update([shifted])[0].persistence == 1


def test_profile_snapshot_value_migration_uses_public_price_boundaries() -> None:
    previous = _snapshot(
        {0: 20.0, 1: 50.0, 2: 30.0},
        poc_index=1,
        value_area_low_index=0,
        value_area_high_index=2,
    )
    current = _snapshot(
        {2: 20.0, 3: 50.0, 4: 30.0},
        poc_index=3,
        value_area_low_index=2,
        value_area_high_index=4,
    )

    migration = compare_value_migration(previous, current)

    assert migration.direction is ValueMigrationDirection.OVERLAPPING_HIGHER
    assert migration.point_of_control_change == 1.0
    assert migration.value_area_midpoint_change == 1.0
    assert migration.value_area_overlap == 0.0


def test_smoothing_radius_must_be_a_non_negative_integer() -> None:
    profile = _snapshot({0: 10.0, 1: 20.0, 2: 10.0})

    with pytest.raises(ValueError, match="non-negative integer"):
        detect_profile_nodes(profile, smoothing_radius=-1)
    with pytest.raises(TypeError, match="integer"):
        detect_profile_nodes(profile, smoothing_radius=1.5)  # type: ignore[arg-type]
