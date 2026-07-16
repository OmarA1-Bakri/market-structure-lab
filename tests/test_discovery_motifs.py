from __future__ import annotations

import math

import pytest

from market_structure_lab.discovery import MotifMatch, discover_motifs


def test_motifs_find_identical_normalized_shapes_with_golden_distance() -> None:
    matches = discover_motifs(
        (0.0, 1.0, 0.0, 10.0, 11.0, 10.0),
        window_length=3,
        exclusion_zone=2,
        max_windows=4,
        top_k=1,
    )

    assert matches == (MotifMatch(0, 3, 0.0, 3),)


def test_motifs_use_canonical_start_order_for_equal_distance_ties() -> None:
    matches = discover_motifs(
        (0.0, 1.0, 0.0, 5.0, 6.0, 5.0, 10.0, 11.0, 10.0),
        window_length=3,
        exclusion_zone=2,
        max_windows=7,
        top_k=3,
    )

    assert matches[:3] == (
        MotifMatch(0, 3, 0.0, 3),
        MotifMatch(0, 6, 0.0, 3),
        MotifMatch(1, 4, 0.0, 3),
    )


def test_motifs_skip_missing_and_zero_variance_windows_without_filling() -> None:
    missing = discover_motifs(
        (0.0, 1.0, 0.0, None, 0.0, 1.0, 0.0),
        window_length=3,
        exclusion_zone=2,
        max_windows=5,
        top_k=2,
    )
    absent = discover_motifs(
        (1.0, 1.0, 1.0, None, 2.0, 2.0, 2.0),
        window_length=3,
        exclusion_zone=0,
        max_windows=5,
        top_k=2,
    )

    assert missing == (MotifMatch(0, 4, 0.0, 3),)
    assert absent == ()


def test_motif_exclusion_zone_removes_overlapping_neighbours() -> None:
    matches = discover_motifs(
        (0.0, 1.0, 0.0, 1.0, 0.0),
        window_length=3,
        exclusion_zone=2,
        max_windows=3,
        top_k=3,
    )

    assert matches == ()


def test_motif_window_cap_fails_before_unbounded_search() -> None:
    with pytest.raises(ValueError, match="max_windows"):
        discover_motifs(
            (0.0, 1.0, 0.0, 1.0, 0.0, 1.0),
            window_length=3,
            exclusion_zone=0,
            max_windows=3,
            top_k=1,
        )


@pytest.mark.parametrize(
    ("sequence", "kwargs", "error"),
    [
        ((), {}, ValueError),
        ((0.0, 1.0), {"window_length": 3}, ValueError),
        ((0.0, math.inf, 1.0), {}, ValueError),
        ((0.0, math.nan, 1.0), {}, ValueError),
        ((0.0, True, 1.0), {}, TypeError),
        ((0.0, 1.0, 0.0), {"window_length": True}, ValueError),
        ((0.0, 1.0, 0.0), {"window_length": 1}, ValueError),
        ((0.0, 1.0, 0.0), {"exclusion_zone": -1}, ValueError),
        ((0.0, 1.0, 0.0), {"top_k": 0}, ValueError),
        ((0.0, 1.0, 0.0), {"max_windows": 0}, ValueError),
    ],
)
def test_motifs_reject_invalid_inputs(
    sequence: tuple[object, ...], kwargs: dict[str, object], error: type[Exception]
) -> None:
    options: dict[str, object] = {
        "window_length": 3,
        "exclusion_zone": 0,
        "max_windows": 1,
        "top_k": 1,
    }
    options.update(kwargs)

    with pytest.raises(error):
        discover_motifs(sequence, **options)  # type: ignore[arg-type]
