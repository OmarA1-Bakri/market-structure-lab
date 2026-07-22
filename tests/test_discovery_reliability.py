from __future__ import annotations

from dataclasses import replace

import pytest

from market_structure_lab.discovery.reliability import (
    ReliabilityEvidence,
    execute_time_order_preserving_null,
    expected_reliability_contract,
)
from market_structure_lab.discovery.kmeans import fit_projected_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.pca import fit_pca
from market_structure_lab.discovery.splits import PartitionRole


def _matrix(values: tuple[tuple[float, ...], ...]) -> FeatureMatrix:
    return FeatureMatrix(
        row_ids=tuple(f"row-{index}" for index in range(len(values))),
        feature_names=("x", "y"),
        values=values,
        dropped_null_rows=0,
        partition_role=PartitionRole.DISCOVERY,
    )


def test_time_order_preserving_null_is_scored_by_the_frozen_detector() -> None:
    matrix = _matrix(((-4.0, 0.0), (-3.0, 0.5), (-2.0, 1.0), (2.0, 1.5), (3.0, 2.0), (4.0, 2.5)))
    projection = fit_pca(matrix, 1)
    clustering = fit_projected_kmeans(
        matrix,
        projection,
        clusters=2,
        seed=7,
        max_iterations=100,
        tolerance=1e-12,
    )

    evidence = execute_time_order_preserving_null(
        matrix,
        (3, 3),
        projection=projection,
        clustering=clustering,
        work_budget_sha256="f" * 64,
        partition_projection_cells=12,
        maximum_partition_projection_cells=12,
        maximum_reliability_control_projection_cells=6,
        maximum_aggregate_projection_cells=18,
    )

    assert evidence.algorithm_version == "causal-sequence-frozen-detector-null-v3"
    assert evidence.input_sha256 != matrix.sha256
    assert sum(evidence.result["null_assignment_counts"].values()) == len(matrix.values)
    assert evidence.result["primary_inertia"] == clustering.inertia
    assert evidence.result["null_inertia"] >= 0.0
    assert evidence.result["inertia_delta"] == pytest.approx(
        evidence.result["null_inertia"] - clustering.inertia
    )
    assert evidence.result["work_budget_sha256"] == "f" * 64
    assert evidence.result["reliability_control_projection_cells"] == 6
    assert evidence.result["aggregate_projection_cells"] == 18


def test_reliability_evidence_round_trips_canonical_hash_bound_result() -> None:
    contract = expected_reliability_contract("time_order_preserving_null")
    evidence = ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256="a" * 64,
        source_matrix_sha256="a" * 64,
        result={
            "row_count": 3,
            "feature_count": 2,
            "sequence_count": 1,
            "sequences": [{"row_start": 0, "row_count": 3, "column_offsets": [1, 2]}],
            "transformed_matrix_sha256": "b" * 64,
            "mean_absolute_change": 0.5,
            "projection_sha256": "c" * 64,
            "frozen_centroids_sha256": "d" * 64,
            "projected_null_sha256": "e" * 64,
            "null_assignment_counts": {"0": 2, "1": 1},
            "primary_inertia": 1.0,
            "null_inertia": 1.5,
            "inertia_delta": 0.5,
            "work_budget_sha256": "f" * 64,
            "partition_projection_cells": 6,
            "maximum_partition_projection_cells": 6,
            "reliability_control_projection_cells": 3,
            "maximum_reliability_control_projection_cells": 3,
            "aggregate_projection_cells": 9,
            "maximum_aggregate_projection_cells": 9,
        },
    )

    assert ReliabilityEvidence.from_dict(evidence.to_dict()) == evidence
    assert evidence.source_matrix_sha256 == "a" * 64
    assert evidence.to_dict()["result"]["sequences"][0]["column_offsets"] == [1, 2]
    assert evidence.to_dict()["result_canonical_json"] == evidence.result_canonical_json


def test_reliability_evidence_rejects_status_only_and_tampered_results() -> None:
    with pytest.raises(ValueError, match="schema"):
        ReliabilityEvidence.from_dict({"status": "executed"})

    contract = expected_reliability_contract("single_cluster")
    evidence = ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256="a" * 64,
        source_matrix_sha256="a" * 64,
        result={
            "row_count": 3,
            "feature_count": 2,
            "centroid": [0.0, 1.0],
            "within_cluster_sum_squares": 2.0,
        },
    )
    tampered = {**evidence.to_dict(), "result": {**evidence.to_dict()["result"], "row_count": 4}}

    with pytest.raises(ValueError, match="result (bytes|SHA)"):
        ReliabilityEvidence.from_dict(tampered)
    with pytest.raises(ValueError, match="algorithm version"):
        replace(evidence, algorithm_version="forged-v1")


@pytest.mark.parametrize(
    ("name", "result"),
    [
        ("seed_perturbation", {"seed_ari": [1.0], "minimum_seed_ari": 1.0}),
        (
            "time_order_preserving_null",
            {
                "row_count": 3,
                "feature_count": 2,
                "sequence_count": 1,
                "sequences": [{"row_start": 0, "row_count": 3, "column_offsets": [0, 2]}],
                "transformed_matrix_sha256": "b" * 64,
                "mean_absolute_change": 0.5,
                "projection_sha256": "c" * 64,
                "frozen_centroids_sha256": "d" * 64,
                "projected_null_sha256": "e" * 64,
                "null_assignment_counts": {"0": 2, "1": 1},
                "primary_inertia": 1.0,
                "null_inertia": 1.5,
                "inertia_delta": 0.5,
                "work_budget_sha256": "f" * 64,
                "partition_projection_cells": 6,
                "maximum_partition_projection_cells": 6,
                "reliability_control_projection_cells": 3,
                "maximum_reliability_control_projection_cells": 3,
                "aggregate_projection_cells": 9,
                "maximum_aggregate_projection_cells": 9,
            },
        ),
        (
            "single_cluster",
            {
                "row_count": 3,
                "feature_count": 2,
                "centroid": [0.0],
                "within_cluster_sum_squares": 2.0,
            },
        ),
        (
            "unconditional_recurrence",
            {
                "row_count": 3,
                "state_count": 2,
                "state_counts": {"0": 2, "1": 1},
                "state_probabilities": {"0": 0.5, "1": 0.5},
                "maximum_probability": 0.5,
            },
        ),
    ],
)
def test_reliability_contracts_reject_invalid_result_invariants(
    name: str,
    result: dict[str, object],
) -> None:
    contract = expected_reliability_contract(name)

    with pytest.raises(ValueError):
        ReliabilityEvidence.create(
            name=contract.name,
            kind=contract.kind,
            algorithm_version=contract.algorithm_version,
            input_sha256="a" * 64,
            source_matrix_sha256="a" * 64,
            result=result,
        )
