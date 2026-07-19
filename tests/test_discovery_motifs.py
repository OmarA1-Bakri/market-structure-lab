from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
import market_structure_lab.discovery.motifs as motif_module

from market_structure_lab.discovery import MotifMatch, discover_motifs
from market_structure_lab.discovery.motifs import (
    MOTIF_ALGORITHM_VERSION,
    MotifObservation,
    MotifRegimeAssignmentContract,
    MotifStabilityPolicy,
    MotifUniverseSupport,
    build_contiguous_motif_sequences,
    discover_multivariate_motifs,
    evaluate_motif_stability,
    freeze_motif_regime_assignments,
)


def _observation(
    row_id: str,
    minute: int,
    values: tuple[float, ...],
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    segment_id: int = 0,
    session_id: str = "2025-01-01",
    cutoff_minute: int | None = None,
    period_id: str = "discovery",
    regime_id: str = "unclassified",
) -> MotifObservation:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute)
    cutoff = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(
        minutes=minute + 1 if cutoff_minute is None else cutoff_minute
    )
    return MotifObservation(
        row_id=row_id,
        timestamp=timestamp,
        information_cutoff=cutoff,
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        session_id=session_id,
        period_id=period_id,
        regime_id=regime_id,
        values=values,
    )


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


@pytest.mark.parametrize(
    "rows",
    [
        # A removed null row leaves a one-minute timestamp break.
        (_observation("r0", 0, (0.0, 0.0)), _observation("r2", 2, (1.0, 1.0))),
        # Explicit one-minute gap after a completed candle.
        (_observation("r0", 0, (0.0, 0.0)), _observation("r3", 3, (1.0, 1.0))),
        (
            _observation("r0", 0, (0.0, 0.0), session_id="s1"),
            _observation("r1", 1, (1.0, 1.0), session_id="s2"),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), symbol="BTCUSDT"),
            _observation("r1", 1, (1.0, 1.0), symbol="ETHUSDT"),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), timeframe="1m"),
            _observation("r1", 1, (1.0, 1.0), timeframe="5m"),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), segment_id=0),
            _observation("r1", 1, (1.0, 1.0), segment_id=1),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), cutoff_minute=2),
            _observation("r1", 1, (1.0, 1.0)),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), period_id="development-early"),
            _observation("r1", 1, (1.0, 1.0), period_id="development-late"),
        ),
        (
            _observation("r0", 0, (0.0, 0.0), regime_id="balanced"),
            _observation("r1", 1, (1.0, 1.0), regime_id="expanding"),
        ),
    ],
)
def test_contiguous_motif_sequences_split_at_every_canonical_boundary(
    rows: tuple[MotifObservation, MotifObservation],
) -> None:
    sequences = build_contiguous_motif_sequences(
        rows,
        feature_names=("auction_location", "volume_change"),
    )

    assert tuple(len(sequence.observations) for sequence in sequences) == (1, 1)
    assert tuple(
        observation.row_id for sequence in sequences for observation in sequence.observations
    ) == ("r0", rows[1].row_id)


@pytest.mark.parametrize(
    "rows",
    [
        (_observation("r0", 0, (0.0, 0.0)), _observation("r0-duplicate", 0, (1.0, 1.0))),
        (_observation("r1", 1, (1.0, 1.0)), _observation("r0", 0, (0.0, 0.0))),
    ],
)
def test_contiguous_motif_sequences_reject_duplicate_or_out_of_order_timestamps(
    rows: tuple[MotifObservation, MotifObservation],
) -> None:
    with pytest.raises(ValueError, match="canonical order"):
        build_contiguous_motif_sequences(
            rows,
            feature_names=("auction_location", "volume_change"),
        )


def test_multivariate_motifs_retain_identity_and_use_every_feature() -> None:
    sequence = build_contiguous_motif_sequences(
        tuple(
            _observation(f"r{index}", index, values)
            for index, values in enumerate(
                (
                    (0.0, 0.0),
                    (1.0, 1.0),
                    (0.0, 0.0),
                    (10.0, 0.0),
                    (11.0, -1.0),
                    (10.0, 0.0),
                )
            )
        ),
        feature_names=("auction_location", "volume_change"),
    )[0]

    matches = discover_multivariate_motifs(
        (sequence,),
        window_length=3,
        exclusion_zone=2,
        max_windows=4,
        top_k=1,
        tie_seed=7,
    )

    assert MOTIF_ALGORITHM_VERSION == "boundary-safe-multivariate-motifs-v3"
    assert matches[0].left_row_id == "r0"
    assert matches[0].right_row_id == "r3"
    assert matches[0].feature_names == ("auction_location", "volume_change")
    assert matches[0].distance > 0.0


def test_motif_stability_records_perturbations_and_development_support() -> None:
    discovery = build_contiguous_motif_sequences(
        tuple(
            _observation(f"d{index}", index, values)
            for index, values in enumerate(
                (
                    (0.0, 0.0),
                    (1.0, 2.0),
                    (0.0, 0.0),
                    (10.0, 10.0),
                    (11.0, 12.0),
                    (10.0, 10.0),
                    (20.0, 20.0),
                )
            )
        ),
        feature_names=("auction_location", "volume_change"),
    )
    development = (
        *build_contiguous_motif_sequences(
            tuple(
                _observation(
                    f"e{index}",
                    index,
                    values,
                    period_id="development-early",
                    regime_id="balanced",
                )
                for index, values in enumerate(((3.0, 3.0), (4.0, 5.0), (3.0, 3.0)))
            ),
            feature_names=("auction_location", "volume_change"),
        ),
        *build_contiguous_motif_sequences(
            tuple(
                _observation(
                    f"l{index}",
                    index,
                    values,
                    symbol="ETHUSDT",
                    period_id="development-late",
                    regime_id="expanding",
                )
                for index, values in enumerate(((7.0, 7.0), (8.0, 9.0), (7.0, 7.0)))
            ),
            feature_names=("auction_location", "volume_change"),
        ),
    )
    policy = MotifStabilityPolicy(
        policy_id="motif-policy-test-v1",
        policy_purpose="software_fixture",
        window_lengths=(3, 4),
        exclusion_zones=(2, 3),
        tie_seeds=(7, 11),
        tie_policies=("canonical", "seeded_hash"),
        subsample_fraction=1.0,
        distance_multipliers=(0.9, 1.0, 1.1),
        maximum_distance=0.01,
        max_windows=20,
        top_k=3,
        minimum_seed_rank_agreement=0.5,
        minimum_subsample_agreement=1.0,
        minimum_parameter_agreement=0.2,
        minimum_recurrence_support=2,
        minimum_asset_support=2,
        minimum_period_support=2,
        minimum_regime_support=2,
    )

    report = evaluate_motif_stability(
        discovery_sequences=discovery,
        development_sequences=development,
        development_asset_universe=("BTCUSDT", "ETHUSDT"),
        development_period_universe=("development-early", "development-late"),
        development_regime_universe=("balanced", "expanding"),
        policy=policy,
    )
    replay = evaluate_motif_stability(
        discovery_sequences=discovery,
        development_sequences=development,
        development_asset_universe=("BTCUSDT", "ETHUSDT"),
        development_period_universe=("development-early", "development-late"),
        development_regime_universe=("balanced", "expanding"),
        policy=policy,
    )

    assert report == replay
    assert report.published_count >= 1
    accepted = next(candidate for candidate in report.candidates if candidate.accepted)
    assert accepted.seed_rank_agreement >= 0.5
    assert accepted.subsample_agreement == 1.0
    assert accepted.parameter_agreement >= 0.2
    assert len(accepted.seed_tie_evidence) == 4
    assert len(accepted.subsample_evidence) == 4
    assert len(accepted.parameter_evidence) == 12
    assert all(item.rank is not None for item in accepted.seed_tie_evidence)
    assert all(item.selected_sequence_ids for item in accepted.subsample_evidence)
    assert {
        (item.window_length, item.exclusion_zone, item.distance_multiplier)
        for item in accepted.parameter_evidence
    } == {
        (window, zone, multiplier)
        for window in policy.window_lengths
        for zone in policy.exclusion_zones
        for multiplier in policy.distance_multipliers
    }
    assert all(item.passed is (not item.rejection_reasons) for item in accepted.perturbations)
    assert {item.asset for item in accepted.recurrence_support if item.recurrent} == {
        "BTCUSDT",
        "ETHUSDT",
    }
    assert {item.period_id for item in accepted.recurrence_support if item.recurrent} == {
        "development-early",
        "development-late",
    }


def test_unstable_motifs_are_retained_as_rejected_not_published() -> None:
    sequence = build_contiguous_motif_sequences(
        tuple(
            _observation(f"r{index}", index, values)
            for index, values in enumerate(
                ((0.0, 0.0), (1.0, 2.0), (0.0, 0.0), (5.0, 5.0), (6.0, 7.0), (5.0, 5.0))
            )
        ),
        feature_names=("auction_location", "volume_change"),
    )
    policy = MotifStabilityPolicy(
        policy_id="motif-policy-reject-v1",
        policy_purpose="software_fixture",
        window_lengths=(3,),
        exclusion_zones=(2,),
        tie_seeds=(7, 11),
        tie_policies=("canonical", "seeded_hash"),
        subsample_fraction=1.0,
        distance_multipliers=(1.0,),
        maximum_distance=0.01,
        max_windows=10,
        top_k=1,
        minimum_seed_rank_agreement=1.0,
        minimum_subsample_agreement=1.0,
        minimum_parameter_agreement=1.0,
        minimum_recurrence_support=1,
        minimum_asset_support=1,
        minimum_period_support=1,
        minimum_regime_support=1,
    )

    report = evaluate_motif_stability(
        discovery_sequences=sequence,
        development_sequences=(),
        development_asset_universe=("BTCUSDT",),
        development_period_universe=("development-early",),
        development_regime_universe=("balanced",),
        policy=policy,
    )

    assert report.published_count == 0
    assert report.rejected_count == 1
    assert report.candidates[0].accepted is False
    assert report.candidates[0].rejection_reasons == (
        "development_recurrence_below_policy",
        "asset_support_below_policy",
        "period_support_below_policy",
        "regime_support_below_policy",
    )
    assert report.candidates[0].universe_support == (
        MotifUniverseSupport("asset", "BTCUSDT", 0, 0),
        MotifUniverseSupport("period", "development-early", 0, 0),
        MotifUniverseSupport("regime", "balanced", 0, 0),
    )


def test_zero_support_universe_members_reduce_coverage_and_fail_policy() -> None:
    discovery = build_contiguous_motif_sequences(
        tuple(
            _observation(f"d{index}", index, values)
            for index, values in enumerate(
                ((0.0, 0.0), (1.0, 2.0), (0.0, 0.0), (5.0, 5.0), (6.0, 7.0), (5.0, 5.0))
            )
        ),
        feature_names=("auction_location", "volume_change"),
    )
    development = build_contiguous_motif_sequences(
        tuple(
            _observation(
                f"e{index}",
                index,
                values,
                period_id="development-early",
                regime_id="unclassified",
            )
            for index, values in enumerate(((2.0, 2.0), (3.0, 4.0), (2.0, 2.0)))
        ),
        feature_names=("auction_location", "volume_change"),
    )
    policy = MotifStabilityPolicy(
        policy_id="motif-policy-universe-v1",
        policy_purpose="software_fixture",
        window_lengths=(3,),
        exclusion_zones=(2,),
        tie_seeds=(7,),
        tie_policies=("canonical",),
        subsample_fraction=1.0,
        distance_multipliers=(1.0,),
        maximum_distance=0.01,
        max_windows=10,
        top_k=1,
        minimum_seed_rank_agreement=1.0,
        minimum_subsample_agreement=1.0,
        minimum_parameter_agreement=1.0,
        minimum_recurrence_support=1,
        minimum_asset_support=2,
        minimum_period_support=2,
        minimum_regime_support=2,
    )

    report = evaluate_motif_stability(
        discovery_sequences=discovery,
        development_sequences=development,
        development_asset_universe=("BTCUSDT", "SOLUSDT"),
        development_period_universe=("development-early", "development-late"),
        development_regime_universe=("balanced", "unclassified"),
        policy=policy,
    )

    candidate = report.candidates[0]
    zero_members = {
        (item.dimension, item.member_id)
        for item in candidate.universe_support
        if item.sequence_count == 0
    }
    assert zero_members == {
        ("asset", "SOLUSDT"),
        ("period", "development-late"),
        ("regime", "balanced"),
    }
    assert candidate.accepted is False
    assert "regime_support_below_policy" in candidate.rejection_reasons


def test_frozen_regime_assignments_are_outcome_blind_canonical_and_hashed() -> None:
    contract = freeze_motif_regime_assignments(
        contract_id="regime-fixture-v1",
        algorithm_version="trailing-range-regime-v1",
        information_policy="trailing_only",
        outcome_policy="outcome_blind",
        regime_universe=("balanced", "expanding"),
        assignments=(("row-b", "expanding"), ("row-a", "balanced")),
    )

    assert isinstance(contract, MotifRegimeAssignmentContract)
    assert contract.assignments == (("row-a", "balanced"), ("row-b", "expanding"))
    assert len(contract.sha256) == 64

    with pytest.raises(ValueError, match="contemporaneous or trailing_only"):
        freeze_motif_regime_assignments(
            contract_id="bad-policy",
            algorithm_version="future-regime-v1",
            information_policy="future",  # type: ignore[arg-type]
            outcome_policy="outcome_blind",
            regime_universe=("balanced",),
            assignments=(("row-a", "balanced"),),
        )
    with pytest.raises(ValueError, match="outcome or future"):
        freeze_motif_regime_assignments(
            contract_id="bad-label",
            algorithm_version="trailing-regime-v1",
            information_policy="trailing_only",
            outcome_policy="outcome_blind",
            regime_universe=("future_return_high",),
            assignments=(("row-a", "future_return_high"),),
        )


def test_oversized_policy_rejects_before_any_motif_search(monkeypatch) -> None:
    called = False

    def unexpected_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("motif search must not run for an oversized policy")

    monkeypatch.setattr(motif_module, "discover_multivariate_motifs", unexpected_search)

    with pytest.raises(ValueError, match="window_lengths.*cap"):
        MotifStabilityPolicy(
            policy_id="oversized-axis-v1",
            policy_purpose="software_fixture",
            window_lengths=tuple(range(2, 67)),
            exclusion_zones=(1,),
            tie_seeds=(7,),
            tie_policies=("canonical",),
            subsample_fraction=1.0,
            distance_multipliers=(1.0,),
            maximum_distance=1.0,
            max_windows=100,
            top_k=1,
            minimum_seed_rank_agreement=0.0,
            minimum_subsample_agreement=0.0,
            minimum_parameter_agreement=0.0,
            minimum_recurrence_support=1,
            minimum_asset_support=1,
            minimum_period_support=1,
            minimum_regime_support=1,
        )
    assert called is False


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"window_lengths": (1_025,)}, "window length.*cap"),
        ({"exclusion_zones": (1_025,)}, "exclusion zone.*cap"),
        ({"max_windows": 10_001}, "max_windows.*cap"),
        ({"top_k": 1_001}, "candidate limit.*cap"),
    ],
)
def test_policy_operational_caps_reject_before_search(
    monkeypatch,
    override: dict[str, object],
    message: str,
) -> None:
    called = False

    def unexpected_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("motif search must not run for an oversized policy")

    monkeypatch.setattr(motif_module, "discover_multivariate_motifs", unexpected_search)
    options: dict[str, object] = {
        "policy_id": "oversized-operation-v1",
        "policy_purpose": "software_fixture",
        "window_lengths": (3,),
        "exclusion_zones": (2,),
        "tie_seeds": (7,),
        "tie_policies": ("canonical",),
        "subsample_fraction": 1.0,
        "distance_multipliers": (1.0,),
        "maximum_distance": 1.0,
        "max_windows": 100,
        "top_k": 1,
        "minimum_seed_rank_agreement": 0.0,
        "minimum_subsample_agreement": 0.0,
        "minimum_parameter_agreement": 0.0,
        "minimum_recurrence_support": 1,
        "minimum_asset_support": 1,
        "minimum_period_support": 1,
        "minimum_regime_support": 1,
    }
    options.update(override)

    with pytest.raises(ValueError, match=message):
        MotifStabilityPolicy(**options)  # type: ignore[arg-type]
    assert called is False


def test_policy_rejects_candidate_times_cartesian_evidence_explosion() -> None:
    with pytest.raises(ValueError, match="serialized perturbation evidence"):
        MotifStabilityPolicy(
            policy_id="oversized-evidence-v1",
            policy_purpose="software_fixture",
            window_lengths=tuple(range(2, 10)),
            exclusion_zones=tuple(range(8)),
            tie_seeds=tuple(range(16)),
            tie_policies=("canonical", "seeded_hash"),
            subsample_fraction=1.0,
            distance_multipliers=tuple(float(value) for value in range(1, 9)),
            maximum_distance=1.0,
            max_windows=1_000,
            top_k=1_000,
            minimum_seed_rank_agreement=0.0,
            minimum_subsample_agreement=0.0,
            minimum_parameter_agreement=0.0,
            minimum_recurrence_support=1,
            minimum_asset_support=1,
            minimum_period_support=1,
            minimum_regime_support=1,
        )


def test_evaluator_rejects_serialized_universe_evidence_before_search(monkeypatch) -> None:
    sequence = build_contiguous_motif_sequences(
        tuple(
            _observation(f"r{index}", index, values)
            for index, values in enumerate(((0.0, 0.0), (1.0, 2.0), (0.0, 0.0), (3.0, 3.0)))
        ),
        feature_names=("auction_location", "volume_change"),
    )
    policy = MotifStabilityPolicy(
        policy_id="universe-evidence-cap-v1",
        policy_purpose="software_fixture",
        window_lengths=(3,),
        exclusion_zones=(2,),
        tie_seeds=(7,),
        tie_policies=("canonical",),
        subsample_fraction=1.0,
        distance_multipliers=(1.0,),
        maximum_distance=1.0,
        max_windows=10,
        top_k=100,
        minimum_seed_rank_agreement=0.0,
        minimum_subsample_agreement=0.0,
        minimum_parameter_agreement=0.0,
        minimum_recurrence_support=1,
        minimum_asset_support=1,
        minimum_period_support=1,
        minimum_regime_support=1,
    )
    called = False

    def unexpected_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("motif search must not run before evidence bounds pass")

    monkeypatch.setattr(motif_module, "discover_multivariate_motifs", unexpected_search)
    assets = tuple(f"A{index:05d}" for index in range(10_000)) + ("BTCUSDT",)

    with pytest.raises(ValueError, match="serialized motif evidence"):
        evaluate_motif_stability(
            discovery_sequences=sequence,
            development_sequences=(),
            development_asset_universe=assets,
            development_period_universe=("development-early",),
            development_regime_universe=("balanced",),
            policy=policy,
        )
    assert called is False


def test_many_short_sequences_reject_nested_selected_id_evidence_before_search(
    monkeypatch,
) -> None:
    sequences = build_contiguous_motif_sequences(
        tuple(
            _observation(
                f"r{index}",
                index * 2,
                (float(index % 5), float(index % 7)),
            )
            for index in range(4_000)
        ),
        feature_names=("auction_location", "volume_change"),
    )
    assert len(sequences) == 4_000
    policy = MotifStabilityPolicy(
        policy_id="nested-selected-id-cap-v1",
        policy_purpose="software_fixture",
        window_lengths=(3,),
        exclusion_zones=(2,),
        tie_seeds=(7,),
        tie_policies=("canonical",),
        subsample_fraction=1.0,
        distance_multipliers=(1.0,),
        maximum_distance=1.0,
        max_windows=10_000,
        top_k=100,
        minimum_seed_rank_agreement=0.0,
        minimum_subsample_agreement=0.0,
        minimum_parameter_agreement=0.0,
        minimum_recurrence_support=1,
        minimum_asset_support=1,
        minimum_period_support=1,
        minimum_regime_support=1,
    )
    called = False

    def unexpected_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("motif search must not run before nested evidence bounds pass")

    monkeypatch.setattr(motif_module, "discover_multivariate_motifs", unexpected_search)

    with pytest.raises(ValueError, match="serialized motif evidence"):
        evaluate_motif_stability(
            discovery_sequences=sequences,
            development_sequences=(),
            development_asset_universe=("BTCUSDT",),
            development_period_universe=("development-early",),
            development_regime_universe=("unclassified",),
            policy=policy,
        )
    assert called is False


@pytest.mark.parametrize("tie_policy", ["canonical", "seeded_hash"])
def test_bounded_multivariate_selection_matches_complete_sort(tie_policy: str) -> None:
    sequence = build_contiguous_motif_sequences(
        tuple(
            _observation(f"r{index}", index, (value, value / 2.0))
            for index, value in enumerate((0.0, 1.0, 0.0, 3.0, 4.0, 3.0, 6.0, 7.0, 6.0))
        ),
        feature_names=("auction_location", "volume_change"),
    )
    complete = discover_multivariate_motifs(
        sequence,
        window_length=3,
        exclusion_zone=2,
        max_windows=7,
        top_k=100,
        tie_seed=7,
        tie_policy=tie_policy,  # type: ignore[arg-type]
    )
    bounded = discover_multivariate_motifs(
        sequence,
        window_length=3,
        exclusion_zone=2,
        max_windows=7,
        top_k=3,
        tie_seed=7,
        tie_policy=tie_policy,  # type: ignore[arg-type]
    )

    assert bounded == complete[:3]


def test_multivariate_search_retains_at_most_top_k_candidates(monkeypatch) -> None:
    sequence = build_contiguous_motif_sequences(
        tuple(
            _observation(
                f"r{index}",
                index,
                (float(index % 11), float((index * 7) % 13)),
            )
            for index in range(250)
        ),
        feature_names=("auction_location", "volume_change"),
    )
    original_push = motif_module.heapq.heappush
    original_replace = motif_module.heapq.heapreplace
    maximum_retained = 0

    def tracked_push(heap, item):
        nonlocal maximum_retained
        result = original_push(heap, item)
        maximum_retained = max(maximum_retained, len(heap))
        return result

    def tracked_replace(heap, item):
        nonlocal maximum_retained
        result = original_replace(heap, item)
        maximum_retained = max(maximum_retained, len(heap))
        return result

    monkeypatch.setattr(motif_module.heapq, "heappush", tracked_push)
    monkeypatch.setattr(motif_module.heapq, "heapreplace", tracked_replace)

    matches = discover_multivariate_motifs(
        sequence,
        window_length=3,
        exclusion_zone=2,
        max_windows=248,
        top_k=3,
        tie_seed=7,
        tie_policy="seeded_hash",
    )

    assert len(matches) == 3
    assert maximum_retained == 3


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
