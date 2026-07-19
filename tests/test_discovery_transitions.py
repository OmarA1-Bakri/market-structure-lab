from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from inspect import signature

import pytest

import market_structure_lab.discovery as discovery
import market_structure_lab.discovery.transitions as transition_module
from market_structure_lab.discovery import (
    ClusterObservation,
    ClusterTransitionEstimate,
    ClusterTransitionRow,
    TransitionBoundaryEvidence,
    compress_dwell_runs,
    estimate_cluster_transitions,
)

BASE = datetime(2025, 1, 1, tzinfo=UTC)


def _uncertainty_policy(**changes):
    values = {
        "policy_id": "software-transition-uncertainty-v1",
        "policy_purpose": "software_fixture",
        "horizon": 1,
        "bootstrap_seed": 7,
        "bootstrap_iterations": 200,
        "confidence_level": 0.9,
        "block_length_rule": "cube_root_transition_support",
        "block_length_value": 8,
        "minimum_effective_support": 4,
        "interval_width_action": "reject",
        "maximum_interval_width": 0.8,
        "sensitivity_offsets": (-1, 1),
        "maximum_sensitivity_endpoint_delta": 0.25,
    }
    values.update(changes)
    return transition_module.TransitionUncertaintyPolicy(**values)


def test_transition_uncertainty_policy_freezes_every_decision_field() -> None:
    policy = _uncertainty_policy()

    assert asdict(policy) == {
        "policy_id": "software-transition-uncertainty-v1",
        "policy_purpose": "software_fixture",
        "horizon": 1,
        "bootstrap_seed": 7,
        "bootstrap_iterations": 200,
        "confidence_level": 0.9,
        "block_length_rule": "cube_root_transition_support",
        "block_length_value": 8,
        "minimum_effective_support": 4,
        "interval_width_action": "reject",
        "maximum_interval_width": 0.8,
        "sensitivity_offsets": (-1, 1),
        "maximum_sensitivity_endpoint_delta": 0.25,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"policy_id": ""},
        {"policy_purpose": ""},
        {"horizon": 0},
        {"bootstrap_seed": True},
        {"bootstrap_iterations": 0},
        {"bootstrap_iterations": 10_001},
        {"confidence_level": 0.0},
        {"confidence_level": 1.0},
        {"block_length_rule": "optimized_from_results"},
        {"block_length_value": 0},
        {"block_length_value": 4_097},
        {"minimum_effective_support": 0},
        {"interval_width_action": "promote"},
        {"maximum_interval_width": -0.1},
        {"maximum_interval_width": 1.1},
        {"sensitivity_offsets": ()},
        {"sensitivity_offsets": (-1, 0, 1)},
        {"sensitivity_offsets": (1, -1)},
        {"sensitivity_offsets": (-9, 1)},
        {"maximum_sensitivity_endpoint_delta": -0.1},
        {"maximum_sensitivity_endpoint_delta": 1.1},
    ],
)
def test_transition_uncertainty_policy_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        _uncertainty_policy(**changes)


def test_transition_policy_drives_deterministic_conditional_recurrence_evidence() -> None:
    rows = tuple(
        _observation(index, label) for index, label in enumerate((0, 1, 0, 1, 0, 1, 0, 1, 0, 1))
    )
    policy = _uncertainty_policy(
        minimum_effective_support=1,
        maximum_interval_width=1.0,
        maximum_sensitivity_endpoint_delta=1.0,
    )

    first = estimate_cluster_transitions(rows, max_rows=100, policy=policy)
    replay = estimate_cluster_transitions(rows, max_rows=100, policy=policy)

    assert first == replay
    assert first.algorithm_version == "boundary-aware-dwell-transitions-v3"
    assert first.estimate_semantics == "conditional_recurrence_estimate"
    assert first.uncertainty_policy == policy
    assert first.horizon == policy.horizon
    assert first.bootstrap_iterations == policy.bootstrap_iterations
    assert first.confidence_level == policy.confidence_level
    assert first.block_length == 3
    assert first.dependence_diagnostics.selected_block_length == 3
    assert first.dependence_diagnostics.sensitivity_block_lengths == (2, 4)
    assert first.dependence_diagnostics.transition_sequence_count == 1
    assert first.dependence_diagnostics.transition_sequence_lengths == (9,)
    assert first.dependence_diagnostics.maximum_transition_sequence_length == 9
    assert first.dependence_diagnostics.adjacent_transition_overlap_fraction == pytest.approx(8 / 9)
    assert all(
        estimate.estimate_semantics == "conditional_recurrence_estimate"
        and estimate.interval_width
        == pytest.approx(estimate.confidence_high - estimate.confidence_low)
        and tuple(item.block_length for item in estimate.sensitivity) == (2, 4)
        for row in first.rows
        for estimate in row.destinations
    )


def test_transition_estimator_requires_only_the_full_frozen_policy_contract() -> None:
    parameters = signature(estimate_cluster_transitions).parameters

    assert tuple(parameters) == ("rows", "max_rows", "policy")
    assert parameters["policy"].default is parameters["policy"].empty


def test_transition_estimate_requires_all_uncertainty_evidence_explicitly() -> None:
    parameters = signature(ClusterTransitionEstimate).parameters

    for field_name in (
        "effective_support",
        "estimate_semantics",
        "sensitivity",
        "maximum_sensitivity_endpoint_delta",
        "evidence_status",
        "rejection_reasons",
    ):
        assert parameters[field_name].default is parameters[field_name].empty


def test_transition_matrix_cannot_synthesize_missing_policy_or_diagnostics() -> None:
    matrix = estimate_cluster_transitions(
        (_observation(0, 0), _observation(1, 1)),
        max_rows=10,
        policy=_uncertainty_policy(
            bootstrap_iterations=10,
            minimum_effective_support=1,
            maximum_interval_width=1.0,
            maximum_sensitivity_endpoint_delta=1.0,
        ),
    )

    with pytest.raises(TypeError, match="uncertainty_policy"):
        replace(matrix, uncertainty_policy=None)
    with pytest.raises(TypeError, match="dependence_diagnostics"):
        replace(matrix, dependence_diagnostics=None)


def test_transition_v2_contract_is_explicitly_incompatible() -> None:
    matrix = estimate_cluster_transitions(
        (_observation(0, 0), _observation(1, 1)),
        max_rows=10,
        policy=_uncertainty_policy(
            bootstrap_iterations=10,
            minimum_effective_support=1,
            maximum_interval_width=1.0,
            maximum_sensitivity_endpoint_delta=1.0,
        ),
    )

    with pytest.raises(ValueError, match="algorithm_version"):
        replace(
            matrix,
            algorithm_version="boundary-aware-dwell-transitions-v2",  # type: ignore[arg-type]
        )


def test_zero_destination_counts_are_retained_as_descriptive_only() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 1, 0, 1)))
    )
    zero = next(
        estimate
        for row in matrix.rows
        if row.source_label == 0
        for estimate in row.destinations
        if estimate.destination_label == 0
    )

    assert zero.count == 0
    assert zero.probability == 0.0
    assert zero.effective_support == 2
    assert zero.confidence_low == 0.0
    assert zero.confidence_high == 0.0
    assert zero.evidence_status == "descriptive_only"
    assert "zero_destination_count" in zero.rejection_reasons


def test_weak_wide_and_sensitivity_unstable_estimates_are_visibly_limited() -> None:
    rows = tuple(_observation(index, label) for index, label in enumerate((0, 1, 0, 2) * 10))
    rejected = estimate_cluster_transitions(
        rows,
        max_rows=100,
        policy=_uncertainty_policy(
            minimum_effective_support=100,
            maximum_interval_width=0.0,
            maximum_sensitivity_endpoint_delta=0.0,
        ),
    )
    estimate = next(
        item
        for row in rejected.rows
        if row.source_label == 0
        for item in row.destinations
        if item.destination_label == 1
    )

    assert estimate.evidence_status == "rejected"
    assert set(estimate.rejection_reasons) >= {
        "effective_support_below_policy",
        "interval_width_above_policy",
        "block_length_sensitivity_above_policy",
    }

    report_only = estimate_cluster_transitions(
        rows,
        max_rows=100,
        policy=_uncertainty_policy(
            minimum_effective_support=1,
            interval_width_action="report_only",
            maximum_interval_width=0.0,
            maximum_sensitivity_endpoint_delta=1.0,
        ),
    )
    reported = next(
        item
        for row in report_only.rows
        if row.source_label == 0
        for item in row.destinations
        if item.destination_label == 1
    )
    assert reported.evidence_status == "descriptive_only"
    assert reported.rejection_reasons == ("interval_width_report_only",)


def test_long_dwells_and_multiple_boundaries_are_explicit_dependence_diagnostics() -> None:
    first = tuple(_observation(index, label) for index, label in enumerate((0,) * 20 + (1, 1)))
    second = tuple(
        _observation(index, label, symbol="ETHUSDT") for index, label in enumerate((0,) * 10 + (2,))
    )
    matrix = estimate_cluster_transitions(
        first + second,
        max_rows=100,
        policy=_uncertainty_policy(
            minimum_effective_support=1,
            maximum_interval_width=1.0,
            maximum_sensitivity_endpoint_delta=1.0,
        ),
    )

    assert matrix.boundary_evidence.contiguous_sequence_count == 2
    assert matrix.boundary_evidence.symbol_break_count == 1
    assert matrix.dependence_diagnostics.longest_raw_dwell_run == 20
    assert matrix.dependence_diagnostics.median_raw_dwell_run == 6.0
    assert matrix.dependence_diagnostics.dwell_run_to_observation_ratio == pytest.approx(4 / 33)
    assert matrix.dependence_diagnostics.transition_sequence_count == 2
    assert matrix.dependence_diagnostics.transition_sequence_lengths == (1, 1)
    assert matrix.dependence_diagnostics.maximum_transition_sequence_length == 1
    assert matrix.block_length == 1
    assert matrix.dependence_diagnostics.sensitivity_block_lengths == ()


def test_interval_resolution_converges_and_retains_nearby_block_sensitivity() -> None:
    rows = tuple(_observation(index, label) for index, label in enumerate((0, 1, 0, 2) * 20))

    def interval(iterations: int) -> tuple[float, float, tuple[int, ...]]:
        matrix = estimate_cluster_transitions(
            rows,
            max_rows=100,
            policy=_uncertainty_policy(
                bootstrap_iterations=iterations,
                minimum_effective_support=1,
                maximum_interval_width=1.0,
                maximum_sensitivity_endpoint_delta=1.0,
            ),
        )
        estimate = next(
            item
            for row in matrix.rows
            if row.source_label == 0
            for item in row.destinations
            if item.destination_label == 1
        )
        return (
            estimate.confidence_low,
            estimate.confidence_high,
            tuple(item.block_length for item in estimate.sensitivity),
        )

    low = interval(100)
    high = interval(1_000)
    reference = interval(5_000)
    low_error = abs(low[0] - reference[0]) + abs(low[1] - reference[1])
    high_error = abs(high[0] - reference[0]) + abs(high[1] - reference[1])

    assert high_error < low_error
    assert low[2] == high[2] == reference[2] == (4, 6)


def test_transition_output_bound_rejects_before_any_bootstrap_work(monkeypatch) -> None:
    rows = tuple(_observation(index, index) for index in range(200))
    bootstrap_called = False
    pairs_materialized = False

    def unexpected_bootstrap(*args, **kwargs):
        nonlocal bootstrap_called
        bootstrap_called = True
        raise AssertionError("bootstrap must not run before transition output bounds pass")

    def unexpected_pairs(*args, **kwargs):
        nonlocal pairs_materialized
        pairs_materialized = True
        raise AssertionError("estimate pairs must not materialize before output bounds pass")

    monkeypatch.setattr(transition_module, "_bootstrap_intervals", unexpected_bootstrap)
    monkeypatch.setattr(
        transition_module,
        "_materialized_estimate_pairs",
        unexpected_pairs,
    )

    with pytest.raises(ValueError, match="transition sensitivity output"):
        estimate_cluster_transitions(
            rows,
            max_rows=200,
            policy=_uncertainty_policy(bootstrap_iterations=1),
        )
    assert bootstrap_called is False
    assert pairs_materialized is False


def test_bootstrap_draw_work_cap_rejects_before_estimates_or_bootstrap(monkeypatch) -> None:
    rows = tuple(_observation(index, index % 2) for index in range(1_001))
    bootstrap_called = False
    pairs_materialized = False

    def unexpected_bootstrap(*args, **kwargs):
        nonlocal bootstrap_called
        bootstrap_called = True
        raise AssertionError("bootstrap must not run before CPU work bounds pass")

    def unexpected_pairs(*args, **kwargs):
        nonlocal pairs_materialized
        pairs_materialized = True
        raise AssertionError("estimate pairs must not materialize before CPU work bounds pass")

    monkeypatch.setattr(transition_module, "_bootstrap_intervals", unexpected_bootstrap)
    monkeypatch.setattr(transition_module, "_materialized_estimate_pairs", unexpected_pairs)

    with pytest.raises(ValueError, match="bootstrap transition draw work"):
        estimate_cluster_transitions(
            rows,
            max_rows=2_000,
            policy=_uncertainty_policy(
                bootstrap_iterations=10_000,
                block_length_rule="fixed",
                block_length_value=1,
            ),
        )
    assert bootstrap_called is False
    assert pairs_materialized is False


def test_bootstrap_draw_work_cap_is_inclusive_at_exact_boundary(monkeypatch) -> None:
    rows = tuple(_observation(index, index % 2) for index in range(5))
    policy = _uncertainty_policy(
        bootstrap_iterations=10,
        block_length_rule="fixed",
        block_length_value=1,
        minimum_effective_support=1,
        maximum_interval_width=1.0,
        maximum_sensitivity_endpoint_delta=1.0,
    )
    expected_draws = 4 * 10 * 2

    monkeypatch.setattr(
        transition_module,
        "_MAX_BOOTSTRAP_TRANSITION_DRAWS",
        expected_draws,
    )
    assert estimate_cluster_transitions(rows, max_rows=10, policy=policy).total_transitions == 4

    monkeypatch.setattr(
        transition_module,
        "_MAX_BOOTSTRAP_TRANSITION_DRAWS",
        expected_draws - 1,
    )
    with pytest.raises(ValueError, match="bootstrap transition draw work"):
        estimate_cluster_transitions(rows, max_rows=10, policy=policy)


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
        max_rows=100,
        policy=_uncertainty_policy(
            horizon=horizon,
            bootstrap_seed=seed,
            block_length_rule="fixed",
            block_length_value=2,
            minimum_effective_support=1,
            maximum_interval_width=1.0,
            maximum_sensitivity_endpoint_delta=1.0,
        ),
    )


def _transition_estimate(
    *,
    destination_label: int = 1,
    count: int = 1,
    probability: float = 0.5,
    confidence_low: float = 0.4,
    confidence_high: float = 0.6,
    effective_support: int = 2,
) -> ClusterTransitionEstimate:
    return ClusterTransitionEstimate(
        destination_label=destination_label,
        count=count,
        probability=probability,
        confidence_low=confidence_low,
        confidence_high=confidence_high,
        effective_support=effective_support,
        estimate_semantics="conditional_recurrence_estimate",
        sensitivity=(
            transition_module.TransitionSensitivityEstimate(
                block_length=1,
                confidence_low=confidence_low,
                confidence_high=confidence_high,
            ),
        ),
        maximum_sensitivity_endpoint_delta=0.0,
        evidence_status="descriptive",
        rejection_reasons=(),
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
    assert tuple(item.destination_label for item in first.destinations) == (0, 1, 2)
    assert tuple(item.count for item in first.destinations) == (0, 1, 1)
    assert tuple(item.probability for item in first.destinations) == (0.0, 0.5, 0.5)
    assert sum(item.probability for item in first.destinations) == pytest.approx(1.0)
    assert second.support == 1
    assert tuple(item.destination_label for item in second.destinations) == (0, 1, 2)
    assert tuple(item.count for item in second.destinations) == (1, 0, 0)
    assert second.destinations[0].probability == 1.0


def test_repeated_minute_dwells_reduce_explicit_effective_support() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 0, 0, 1, 1, 0)))
    )

    assert matrix.boundary_evidence.raw_observation_count == 6
    assert matrix.boundary_evidence.dwell_run_count == 3
    assert matrix.boundary_evidence.contiguous_sequence_count == 1
    assert matrix.total_transitions == 2
    assert tuple(row.effective_support for row in matrix.rows) == (1, 1)
    assert all(
        destination.effective_support == row.support
        for row in matrix.rows
        for destination in row.destinations
    )
    assert asdict(matrix.rows[0])["effective_support"] == 1
    assert asdict(matrix.rows[0].destinations[0])["effective_support"] == 1
    assert isinstance(matrix.boundary_evidence, discovery.TransitionBoundaryEvidence)


def test_transition_horizon_uses_run_level_destinations() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 1, 2, 3))),
        horizon=2,
    )

    assert matrix.total_transitions == 2
    assert tuple(
        (
            row.source_label,
            next(item.destination_label for item in row.destinations if item.count),
        )
        for row in matrix.rows
    ) == ((0, 2), (1, 3))


@pytest.mark.parametrize(
    ("boundary_rows", "evidence_field"),
    [
        (
            (
                _observation(0, 0, symbol="BTCUSDT"),
                _observation(1, 1, symbol="BTCUSDT"),
                _observation(0, 2, symbol="ETHUSDT"),
                _observation(1, 3, symbol="ETHUSDT"),
            ),
            "symbol_break_count",
        ),
        (
            (
                _observation(0, 0, timeframe="1m"),
                _observation(1, 1, timeframe="1m"),
                _observation(0, 2, timeframe="5m"),
                _observation(1, 3, timeframe="5m"),
            ),
            "timeframe_break_count",
        ),
        (
            (
                _observation(0, 0, segment_id=0),
                _observation(1, 1, segment_id=0),
                _observation(2, 2, segment_id=1),
                _observation(3, 3, segment_id=1),
            ),
            "segment_break_count",
        ),
        (
            (
                _observation(0, 0, session_id="2025-01-01"),
                _observation(1, 1, session_id="2025-01-01"),
                _observation(2, 2, session_id="2025-01-02"),
                _observation(3, 3, session_id="2025-01-02"),
            ),
            "session_break_count",
        ),
        (
            (
                _observation(0, 0),
                _observation(1, 1),
                _observation(5, 2),
                _observation(6, 3),
            ),
            "non_contiguous_time_break_count",
        ),
    ],
)
def test_transitions_never_cross_symbol_timeframe_segment_session_or_gap(
    boundary_rows: tuple[ClusterObservation, ...],
    evidence_field: str,
) -> None:
    matrix = _estimate(boundary_rows)

    assert matrix.total_transitions == 2
    assert matrix.boundary_evidence.boundary_break_count == 1
    assert getattr(matrix.boundary_evidence, evidence_field) == 1
    assert tuple(
        (
            row.source_label,
            next(item.destination_label for item in row.destinations if item.count),
        )
        for row in matrix.rows
    ) == ((0, 1), (2, 3))


def test_transition_bootstrap_is_seeded_bounded_and_keeps_run_level_caveat() -> None:
    rows = tuple(
        _observation(index, label) for index, label in enumerate((0, 1, 0, 2, 0, 1, 0, 2, 0, 1))
    )

    first = _estimate(rows, seed=11)
    replay = _estimate(rows, seed=11)

    assert first == replay
    assert first.algorithm_version == "boundary-aware-dwell-transitions-v3"
    with pytest.raises(ValueError, match="algorithm_version"):
        replace(first, algorithm_version="legacy-adjacent-v1")  # type: ignore[arg-type]
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
            max_rows=200,
            policy=_uncertainty_policy(
                bootstrap_seed=0,
                bootstrap_iterations=10_000,
                block_length_rule="fixed",
                block_length_value=1,
            ),
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
            max_rows=2,
            policy=_uncertainty_policy(bootstrap_iterations=10),
        )
    assert consumed == 3


@pytest.mark.parametrize("max_rows", [0, 1_000_001])
def test_transition_configuration_fails_closed(max_rows: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        estimate_cluster_transitions(
            (_observation(0, 0), _observation(1, 1)),
            max_rows=max_rows,
            policy=_uncertainty_policy(),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_label": True},
        {"destination_label": -1},
        {"count": True},
        {"count": -1},
        {"probability": float("nan")},
        {"probability": 1.1},
        {"confidence_low": -0.1},
        {"confidence_high": float("inf")},
        {"confidence_low": 0.6},
        {"confidence_high": 0.4},
        {"effective_support": 0},
        {"count": 3},
        {"estimate_semantics": "significance_test"},
        {"sensitivity": []},
        {"sensitivity": (transition_module.TransitionSensitivityEstimate(1, 0.7, 0.8),)},
        {"maximum_sensitivity_endpoint_delta": 0.1},
        {"maximum_sensitivity_endpoint_delta": 1.1},
        {"evidence_status": "promoted"},
        {"evidence_status": "rejected"},
        {"rejection_reasons": ("reason",)},
    ],
)
def test_transition_estimate_direct_construction_fails_closed(
    changes: dict[str, object],
) -> None:
    estimate = _transition_estimate()

    with pytest.raises((TypeError, ValueError)):
        replace(estimate, **changes)


def test_transition_row_direct_construction_fails_closed() -> None:
    destination = _transition_estimate(
        count=2,
        probability=1.0,
        confidence_low=0.5,
        confidence_high=1.0,
    )
    row = ClusterTransitionRow(0, 2, (destination,))
    forged_destination = replace(destination)
    object.__setattr__(forged_destination, "effective_support", 1)

    invalid = (
        {"source_label": True},
        {"source_label": -1},
        {"support": True},
        {"support": 0},
        {"destinations": []},
        {"destinations": ()},
        {"destinations": (destination, destination)},
        {"destinations": (replace(destination, count=1, probability=0.5),)},
        {"destinations": (forged_destination,)},
    )
    for changes in invalid:
        with pytest.raises((TypeError, ValueError)):
            replace(row, **changes)


def test_transition_boundary_evidence_direct_construction_fails_closed() -> None:
    evidence = TransitionBoundaryEvidence(6, 3, 2, 1, 1, 0, 0, 0, 1)
    invalid = (
        {"raw_observation_count": True},
        {"raw_observation_count": -1},
        {"dwell_run_count": 7},
        {"contiguous_sequence_count": 4},
        {"boundary_break_count": 0},
        {"symbol_break_count": 2},
        {
            "symbol_break_count": 0,
            "non_contiguous_time_break_count": 0,
        },
    )
    for changes in invalid:
        with pytest.raises((TypeError, ValueError)):
            replace(evidence, **changes)


def test_transition_matrix_direct_construction_fails_closed_and_empty_is_valid() -> None:
    matrix = _estimate(tuple(_observation(index, label) for index, label in enumerate((0, 1))))
    forged_row = replace(matrix.rows[0])
    object.__setattr__(forged_row, "effective_support", 2)
    invalid = (
        {"algorithm_version": "legacy-adjacent-v1"},
        {"estimate_semantics": "significance_test"},
        {"uncertainty_policy": object()},
        {
            "uncertainty_policy": replace(
                matrix.uncertainty_policy,
                horizon=2,
            )
        },
        {"horizon": True},
        {"horizon": 0},
        {"rows": []},
        {"rows": (forged_row,)},
        {"total_transitions": True},
        {"total_transitions": 2},
        {"sample_unit": "minute"},
        {"confidence_method": "binomial"},
        {"seed": True},
        {"bootstrap_iterations": 0},
        {"bootstrap_iterations": 10_001},
        {"block_length": 0},
        {"block_length": 4_097},
        {"confidence_level": float("nan")},
        {"confidence_level": 1.0},
        {"boundary_evidence": object()},
        {"dependence_diagnostics": object()},
        {
            "dependence_diagnostics": replace(
                matrix.dependence_diagnostics,
                dwell_run_to_observation_ratio=0.5,
            )
        },
    )
    for changes in invalid:
        with pytest.raises((TypeError, ValueError)):
            replace(matrix, **changes)

    empty = _estimate((_observation(0, 0), _observation(1, 0)))
    assert empty.rows == ()
    assert empty.total_transitions == 0
    assert empty.boundary_evidence.dwell_run_count == 1


def test_transition_matrix_capacity_accounts_for_horizon() -> None:
    matrix = _estimate(
        tuple(_observation(index, label) for index, label in enumerate((0, 1, 2, 3)))
    )

    with pytest.raises(ValueError, match="boundary-safe dwell-run capacity"):
        replace(
            matrix,
            horizon=4,
            uncertainty_policy=replace(matrix.uncertainty_policy, horizon=4),
        )
