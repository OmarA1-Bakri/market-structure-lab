from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from market_structure_lab.discovery import (
    ClusterObservation,
    compress_dwell_runs,
    estimate_cluster_transitions,
)

BASE = datetime(2025, 1, 1, tzinfo=UTC)


def _observation(
    minute: int,
    label: int,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    segment_id: int = 0,
    session_id: str = "2025-01-01",
) -> ClusterObservation:
    timestamp = BASE + timedelta(minutes=minute)
    return ClusterObservation(
        label=label,
        timestamp=timestamp,
        information_cutoff=timestamp + timedelta(minutes=1),
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        session_id=session_id,
    )


def _estimate(rows: tuple[ClusterObservation, ...], *, horizon: int = 1, seed: int = 7):
    return estimate_cluster_transitions(
        rows,
        horizon=horizon,
        max_rows=100,
        seed=seed,
        bootstrap_iterations=200,
        block_length=2,
        confidence_level=0.9,
    )


def test_cluster_observation_rejects_invalid_or_future_unsafe_identity() -> None:
    valid = _observation(0, 0)

    invalid_changes = (
        {"label": -1},
        {"label": True},
        {"timestamp": valid.timestamp.replace(tzinfo=None)},
        {"timestamp": valid.timestamp.astimezone(timezone(timedelta(hours=1)))},
        {"information_cutoff": valid.timestamp},
        {"symbol": ""},
        {"timeframe": ""},
        {"segment_id": -1},
        {"session_id": ""},
    )

    for changes in invalid_changes:
        with pytest.raises((TypeError, ValueError)):
            replace(valid, **changes)


def test_dwell_compression_retains_run_bounds_and_order() -> None:
    compressed = compress_dwell_runs(
        tuple(_observation(index, label) for index, label in enumerate((0, 0, 1, 1, 0)))
    )

    assert tuple(row.label for row in compressed) == (0, 1, 0)
    assert tuple(row.timestamp for row in compressed) == (
        BASE,
        BASE + timedelta(minutes=2),
        BASE + timedelta(minutes=4),
    )
    assert tuple(row.information_cutoff for row in compressed) == (
        BASE + timedelta(minutes=2),
        BASE + timedelta(minutes=4),
        BASE + timedelta(minutes=5),
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"symbol": "ETHUSDT"},
        {"timeframe": "5m"},
        {"segment_id": 1},
        {"session_id": "2025-01-02"},
    ],
)
def test_dwell_compression_never_merges_across_declared_boundaries(
    changed: dict[str, object],
) -> None:
    left = _observation(0, 0)
    right = replace(_observation(1, 0), **changed)

    assert compress_dwell_runs((left, right)) == (left, right)


def test_dwell_compression_never_merges_across_material_gap() -> None:
    rows = (_observation(0, 0), _observation(5, 0))

    assert compress_dwell_runs(rows) == rows


def test_transition_matrix_counts_runs_and_returns_row_stochastic_probabilities() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 0, 1, 1, 0, 2)))
    )

    assert matrix.sample_unit == "dwell_run"
    assert matrix.total_transitions == 3
    assert matrix.horizon == 1
    assert tuple(row.source_label for row in matrix.rows) == (0, 1)
    first, second = matrix.rows
    assert first.support == 2
    assert tuple(item.destination_label for item in first.destinations) == (1, 2)
    assert tuple(item.count for item in first.destinations) == (1, 1)
    assert tuple(item.probability for item in first.destinations) == (0.5, 0.5)
    assert sum(item.probability for item in first.destinations) == pytest.approx(1.0)
    assert second.support == 1
    assert second.destinations[0].destination_label == 0
    assert second.destinations[0].probability == 1.0


def test_transition_horizon_uses_run_level_destinations() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 1, 2, 3))),
        horizon=2,
    )

    assert matrix.total_transitions == 2
    assert tuple(
        (row.source_label, row.destinations[0].destination_label) for row in matrix.rows
    ) == ((0, 2), (1, 3))


@pytest.mark.parametrize(
    "boundary_rows",
    [
        (
            _observation(0, 0, symbol="BTCUSDT"),
            _observation(1, 1, symbol="BTCUSDT"),
            _observation(0, 2, symbol="ETHUSDT"),
            _observation(1, 3, symbol="ETHUSDT"),
        ),
        (
            _observation(0, 0, timeframe="1m"),
            _observation(1, 1, timeframe="1m"),
            _observation(0, 2, timeframe="5m"),
            _observation(1, 3, timeframe="5m"),
        ),
        (
            _observation(0, 0, segment_id=0),
            _observation(1, 1, segment_id=0),
            _observation(2, 2, segment_id=1),
            _observation(3, 3, segment_id=1),
        ),
        (
            _observation(0, 0, session_id="2025-01-01"),
            _observation(1, 1, session_id="2025-01-01"),
            _observation(2, 2, session_id="2025-01-02"),
            _observation(3, 3, session_id="2025-01-02"),
        ),
        (
            _observation(0, 0),
            _observation(1, 1),
            _observation(5, 2),
            _observation(6, 3),
        ),
    ],
)
def test_transitions_never_cross_symbol_timeframe_segment_session_or_gap(
    boundary_rows: tuple[ClusterObservation, ...],
) -> None:
    matrix = _estimate(boundary_rows)

    assert matrix.total_transitions == 2
    assert tuple(
        (row.source_label, row.destinations[0].destination_label) for row in matrix.rows
    ) == ((0, 1), (2, 3))


def test_transition_bootstrap_is_seeded_bounded_and_keeps_run_level_caveat() -> None:
    rows = tuple(
        _observation(index, label) for index, label in enumerate((0, 1, 0, 2, 0, 1, 0, 2, 0, 1))
    )

    first = _estimate(rows, seed=11)
    replay = _estimate(rows, seed=11)

    assert first == replay
    assert first.seed == 11
    assert first.bootstrap_iterations == 200
    assert first.block_length == 2
    assert first.confidence_level == 0.9
    for row in first.rows:
        for destination in row.destinations:
            assert 0.0 <= destination.confidence_low <= destination.probability
            assert destination.probability <= destination.confidence_high <= 1.0


def test_transition_bootstrap_caps_probability_sample_storage() -> None:
    rows = tuple(_observation(index, index) for index in range(103))

    with pytest.raises(ValueError, match="bootstrap probability storage"):
        estimate_cluster_transitions(
            rows,
            horizon=1,
            max_rows=200,
            seed=0,
            bootstrap_iterations=10_000,
            block_length=1,
            confidence_level=0.9,
        )


def test_transition_input_is_ordered_and_bounded_before_processing() -> None:
    with pytest.raises(ValueError, match="canonical order"):
        _estimate((_observation(1, 1), _observation(0, 0)))

    consumed = 0

    def rows():
        nonlocal consumed
        for index in range(3):
            consumed += 1
            yield _observation(index, index)

    with pytest.raises(ValueError, match="max_rows"):
        estimate_cluster_transitions(
            rows(),
            horizon=1,
            max_rows=2,
            seed=0,
            bootstrap_iterations=10,
            block_length=1,
            confidence_level=0.9,
        )
    assert consumed == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon": 0},
        {"max_rows": 0},
        {"seed": True},
        {"bootstrap_iterations": 0},
        {"bootstrap_iterations": 10_001},
        {"block_length": 0},
        {"block_length": 4097},
        {"confidence_level": 0.0},
        {"confidence_level": 1.0},
    ],
)
def test_transition_configuration_fails_closed(kwargs: dict[str, object]) -> None:
    options: dict[str, object] = {
        "horizon": 1,
        "max_rows": 100,
        "seed": 0,
        "bootstrap_iterations": 10,
        "block_length": 1,
        "confidence_level": 0.9,
    }
    options.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        estimate_cluster_transitions(
            (_observation(0, 0), _observation(1, 1)),
            **options,  # type: ignore[arg-type]
        )
