from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from market_structure_lab.discovery import (
    AIInterpretation,
    BehaviourEvidencePack,
    ClusterTransitionEstimate,
    ClusterTransitionMatrix,
    ClusterTransitionRow,
    FeatureMatrix,
    PartitionRole,
    StabilityPolicy,
    StabilityReport,
    TransitionBoundaryEvidence,
    fit_pca,
    fit_projected_kmeans,
    freeze_behaviours,
)


def _behaviour():
    matrix = FeatureMatrix(
        row_ids=("r1", "r2", "r3", "r4"),
        feature_names=("auction_location", "volume_change"),
        values=((-2.0, 1.0), (-1.0, 3.0), (9.0, 5.0), (11.0, 7.0)),
        dropped_null_rows=0,
        partition_role=PartitionRole.DISCOVERY,
    )
    projection = fit_pca(matrix, 1)
    clustering = fit_projected_kmeans(
        matrix,
        projection,
        clusters=2,
        seed=7,
        max_iterations=50,
        tolerance=1e-12,
    )
    report = StabilityReport(
        policy=StabilityPolicy(0.0, 0.0, 1.0, 0.0, -1.0),
        seed_ari=(1.0,),
        subsample_ari=(1.0,),
        adjacent_js_distance=0.0,
        asset_coverage=1.0,
        parameter_perturbation_ari=(0.0,),
    )
    return freeze_behaviours(
        run_id="DR-000501",
        matrix=matrix,
        projection=projection,
        clustering=clustering,
        stability=report,
        event_ids=("EV-1", "EV-2", "EV-3", "EV-4"),
        durations_seconds=(60.0, 60.0, 60.0, 60.0),
        symbols=("BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"),
        description="Neutral recurring feature configurations.",
    )[0]


def _transition_row() -> ClusterTransitionRow:
    return ClusterTransitionRow(
        source_label=0,
        support=2,
        destinations=(
            ClusterTransitionEstimate(
                destination_label=1,
                count=2,
                probability=1.0,
                confidence_low=0.5,
                confidence_high=1.0,
            ),
        ),
    )


def _transition_matrix() -> ClusterTransitionMatrix:
    return ClusterTransitionMatrix(
        algorithm_version="boundary-aware-dwell-transitions-v2",
        horizon=1,
        rows=(_transition_row(),),
        total_transitions=2,
        sample_unit="dwell_run",
        confidence_method="boundary_block_bootstrap",
        seed=7,
        bootstrap_iterations=100,
        block_length=2,
        confidence_level=0.95,
        boundary_evidence=TransitionBoundaryEvidence(
            raw_observation_count=6,
            dwell_run_count=3,
            contiguous_sequence_count=1,
            boundary_break_count=0,
            symbol_break_count=0,
            timeframe_break_count=0,
            segment_break_count=0,
            session_break_count=0,
            non_contiguous_time_break_count=0,
        ),
    )


def _interpretation(behaviour_id: str) -> AIInterpretation:
    return AIInterpretation(
        behaviour_id=behaviour_id,
        neutral_name="Alternating Value Configuration",
        description="A recurring outcome-blind feature configuration.",
        candidate_mechanism_inference="Inference: alternating auction location may reflect balance.",
        falsifiable_hypothesis="The frozen detector will recur across untouched assets.",
        detector_fields=("auction_location", "volume_change"),
        proposed_horizon="next 4 observed events",
        proposed_metrics=("forward_return", "asset_coverage"),
        spuriousness_reasons=("small sample", "regime concentration"),
        provider="openai",
        model="gpt-test",
        prompt_sha256=hashlib.sha256(b"prompt").hexdigest(),
        temperature=0.0,
        generated_at=datetime(2025, 1, 1, tzinfo=UTC),
        response_sha256=hashlib.sha256(b"response").hexdigest(),
    )


def test_evidence_pack_contains_only_frozen_summaries() -> None:
    behaviour = _behaviour()
    pack = BehaviourEvidencePack(
        run_id="DR-000501",
        behaviour=behaviour,
        nearest_behaviour_ids=("B-1111111111111111",),
        contrasting_behaviour_ids=("B-2222222222222222",),
        transition_matrix=_transition_matrix(),
    )

    payload = pack.to_dict()
    text = pack.canonical_json()

    assert payload["behaviour"]["behaviour_id"] == behaviour.behaviour_id
    assert payload["schema_version"] == "behaviour-evidence-pack-v2"
    assert payload["transition_matrix"]["boundary_evidence"] == {
        "raw_observation_count": 6,
        "dwell_run_count": 3,
        "contiguous_sequence_count": 1,
        "boundary_break_count": 0,
        "symbol_break_count": 0,
        "timeframe_break_count": 0,
        "segment_break_count": 0,
        "session_break_count": 0,
        "non_contiguous_time_break_count": 0,
    }
    assert "rows" not in payload
    assert "holdout" not in text.lower()
    assert "future_return" not in text
    assert "profitability" not in text


def test_evidence_pack_rejects_self_neighbours_overlap_and_run_drift() -> None:
    behaviour = _behaviour()
    base = BehaviourEvidencePack(
        run_id="DR-000501",
        behaviour=behaviour,
        nearest_behaviour_ids=(),
        contrasting_behaviour_ids=(),
        transition_matrix=_transition_matrix(),
    )

    for changes in (
        {"run_id": "DR-000999"},
        {"nearest_behaviour_ids": (behaviour.behaviour_id,)},
        {
            "nearest_behaviour_ids": ("B-1111111111111111",),
            "contrasting_behaviour_ids": ("B-1111111111111111",),
        },
    ):
        with pytest.raises(ValueError):
            replace(base, **changes)


def test_ai_interpretation_requires_complete_neutral_inference_and_provenance() -> None:
    interpretation = _interpretation(_behaviour().behaviour_id)

    assert interpretation.candidate_mechanism_inference.startswith("Inference:")
    assert interpretation.generated_at.utcoffset() == timedelta(0)

    invalid = (
        {"neutral_name": "Validated profitable edge"},
        {"neutral_name": "Institutional Whale Support"},
        {"neutral_name": "Neutral structure guarantees riches through coordinated funds"},
        {"candidate_mechanism_inference": "This mechanism is certain."},
        {"detector_fields": ()},
        {"proposed_metrics": ()},
        {"spuriousness_reasons": ()},
        {"prompt_sha256": "bad"},
        {"temperature": -0.1},
        {"generated_at": datetime(2025, 1, 1)},
        {"generated_at": datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
    )
    for changes in invalid:
        with pytest.raises((TypeError, ValueError)):
            replace(interpretation, **changes)
