from __future__ import annotations

from market_structure_lab.profiles import VolumeProfile
from market_structure_lab.structure import (
    NodeKind,
    ValueMigrationDirection,
    compare_value_migration,
    detect_profile_nodes,
)


def test_detect_profile_nodes_finds_local_hvns_and_lvns() -> None:
    profile = VolumeProfile(
        price_volumes={
            100.0: 10.0,
            101.0: 40.0,
            102.0: 15.0,
            103.0: 5.0,
            104.0: 35.0,
            105.0: 10.0,
        },
        total_volume=115.0,
        point_of_control=101.0,
        value_area_low=101.0,
        value_area_high=104.0,
    )

    nodes = detect_profile_nodes(profile, min_prominence=10.0)

    assert [(node.kind, node.price, node.volume, node.prominence) for node in nodes] == [
        (NodeKind.HVN, 101.0, 40.0, 25.0),
        (NodeKind.LVN, 103.0, 5.0, 10.0),
        (NodeKind.HVN, 104.0, 35.0, 25.0),
    ]


def test_detect_profile_nodes_ignores_endpoints_and_flat_ties() -> None:
    profile = VolumeProfile(
        price_volumes={
            100.0: 50.0,
            101.0: 20.0,
            102.0: 20.0,
            103.0: 45.0,
        },
        total_volume=135.0,
        point_of_control=100.0,
        value_area_low=100.0,
        value_area_high=103.0,
    )

    assert detect_profile_nodes(profile) == []


def test_detect_profile_nodes_skips_noncontiguous_bins() -> None:
    profile = VolumeProfile(
        price_volumes={
            101.0: 40.0,
            103.0: 15.0,
            105.0: 5.0,
            106.0: 35.0,
        },
        total_volume=95.0,
        point_of_control=101.0,
        value_area_low=101.0,
        value_area_high=106.0,
        tick_size=1.0,
    )

    assert detect_profile_nodes(profile, min_prominence=5.0) == []


def test_compare_value_migration_classifies_higher_non_overlapping_value() -> None:
    previous = VolumeProfile(
        price_volumes={99.0: 10.0, 100.0: 50.0, 101.0: 20.0},
        total_volume=80.0,
        point_of_control=100.0,
        value_area_low=99.0,
        value_area_high=101.0,
    )
    current = VolumeProfile(
        price_volumes={102.0: 20.0, 103.0: 60.0, 104.0: 30.0},
        total_volume=110.0,
        point_of_control=103.0,
        value_area_low=102.0,
        value_area_high=104.0,
    )

    migration = compare_value_migration(previous, current)

    assert migration.direction is ValueMigrationDirection.HIGHER
    assert migration.point_of_control_change == 3.0
    assert migration.value_area_midpoint_change == 3.0
    assert migration.value_area_overlap == 0.0


def test_compare_value_migration_classifies_overlapping_lower_value() -> None:
    previous = VolumeProfile(
        price_volumes={100.0: 10.0, 101.0: 50.0, 102.0: 30.0},
        total_volume=90.0,
        point_of_control=101.0,
        value_area_low=100.0,
        value_area_high=102.0,
    )
    current = VolumeProfile(
        price_volumes={99.0: 25.0, 100.0: 45.0, 101.0: 20.0},
        total_volume=90.0,
        point_of_control=100.0,
        value_area_low=99.0,
        value_area_high=101.0,
    )

    migration = compare_value_migration(previous, current)

    assert migration.direction is ValueMigrationDirection.OVERLAPPING_LOWER
    assert migration.point_of_control_change == -1.0
    assert migration.value_area_midpoint_change == -1.0
    assert migration.value_area_overlap == 1.0
