from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.profiles.volume import VolumeProfile


class ValueMigrationDirection(StrEnum):
    LOWER = "lower"
    OVERLAPPING_LOWER = "overlapping_lower"
    OVERLAPPING = "overlapping"
    OVERLAPPING_HIGHER = "overlapping_higher"
    HIGHER = "higher"


@dataclass(frozen=True)
class ValueMigration:
    direction: ValueMigrationDirection
    point_of_control_change: float
    value_area_midpoint_change: float
    value_area_overlap: float


@dataclass(frozen=True)
class _ValueReferences:
    point_of_control: float
    value_area_low: float
    value_area_high: float


def compare_value_migration(
    previous: ProfileSnapshot | VolumeProfile,
    current: ProfileSnapshot | VolumeProfile,
) -> ValueMigration:
    """Compare value-area migration between two deterministic profiles."""
    previous_refs = _value_references(previous, name="previous")
    current_refs = _value_references(current, name="current")

    previous_midpoint = _midpoint(previous_refs.value_area_low, previous_refs.value_area_high)
    current_midpoint = _midpoint(current_refs.value_area_low, current_refs.value_area_high)
    midpoint_change = current_midpoint - previous_midpoint
    poc_change = current_refs.point_of_control - previous_refs.point_of_control
    overlap = _overlap_width(
        previous_refs.value_area_low,
        previous_refs.value_area_high,
        current_refs.value_area_low,
        current_refs.value_area_high,
    )

    return ValueMigration(
        direction=_direction(
            previous_refs, current_refs, midpoint_change=midpoint_change, overlap=overlap
        ),
        point_of_control_change=poc_change,
        value_area_midpoint_change=midpoint_change,
        value_area_overlap=overlap,
    )


def _value_references(profile: ProfileSnapshot | VolumeProfile, *, name: str) -> _ValueReferences:
    if isinstance(profile, ProfileSnapshot):
        if (
            profile.poc_index is None
            or profile.value_area_low_index is None
            or profile.value_area_high_index is None
        ):
            raise ValueError(f"{name} profile must contain POC and value-area references")
        return _ValueReferences(
            point_of_control=profile.binning.price_for_index(profile.poc_index),
            value_area_low=profile.binning.price_for_index(profile.value_area_low_index),
            value_area_high=profile.binning.price_for_index(profile.value_area_high_index),
        )

    if (
        profile.point_of_control is None
        or profile.value_area_low is None
        or profile.value_area_high is None
    ):
        raise ValueError(f"{name} profile must contain POC and value-area references")
    return _ValueReferences(
        point_of_control=profile.point_of_control,
        value_area_low=profile.value_area_low,
        value_area_high=profile.value_area_high,
    )


def _midpoint(low: float, high: float) -> float:
    return (low + high) / 2


def _overlap_width(
    previous_low: float,
    previous_high: float,
    current_low: float,
    current_high: float,
) -> float:
    return max(0.0, min(previous_high, current_high) - max(previous_low, current_low))


def _direction(
    previous: _ValueReferences,
    current: _ValueReferences,
    *,
    midpoint_change: float,
    overlap: float,
) -> ValueMigrationDirection:
    if current.value_area_low > previous.value_area_high:
        return ValueMigrationDirection.HIGHER
    if current.value_area_high < previous.value_area_low:
        return ValueMigrationDirection.LOWER
    if midpoint_change > 0:
        return ValueMigrationDirection.OVERLAPPING_HIGHER
    if midpoint_change < 0:
        return ValueMigrationDirection.OVERLAPPING_LOWER
    if overlap > 0:
        return ValueMigrationDirection.OVERLAPPING
    return ValueMigrationDirection.OVERLAPPING
