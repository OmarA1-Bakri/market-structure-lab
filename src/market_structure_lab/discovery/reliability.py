"""Typed, hash-bound evidence for outcome-blind discovery controls and baselines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Sequence, TypeGuard, cast

from market_structure_lab.discovery.matrix import FeatureMatrix
from market_structure_lab.discovery.kmeans import KMeansResult
from market_structure_lab.discovery.pca import PCAProjection, pca_projection_sha256
from market_structure_lab.discovery.stability import StabilityReport

ReliabilityEvidenceKind = Literal["negative_control", "naive_baseline"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = "discovery-reliability-evidence-v1"


@dataclass(frozen=True, slots=True)
class ReliabilityContract:
    name: str
    kind: ReliabilityEvidenceKind
    algorithm_version: str


_CONTRACTS = {
    "seed_perturbation": ReliabilityContract(
        name="seed_perturbation",
        kind="negative_control",
        algorithm_version="seed-perturbation-stability-v1",
    ),
    "time_order_preserving_null": ReliabilityContract(
        name="time_order_preserving_null",
        kind="negative_control",
        algorithm_version="causal-sequence-frozen-detector-null-v3",
    ),
    "single_cluster": ReliabilityContract(
        name="single_cluster",
        kind="naive_baseline",
        algorithm_version="single-cluster-dispersion-v1",
    ),
    "unconditional_recurrence": ReliabilityContract(
        name="unconditional_recurrence",
        kind="naive_baseline",
        algorithm_version="unconditional-cluster-frequency-v1",
    ),
}


def expected_reliability_contract(name: str) -> ReliabilityContract:
    try:
        return _CONTRACTS[name]
    except KeyError as error:
        raise ValueError("unsupported reliability evidence name") from error


def reliability_algorithm_versions() -> dict[str, str]:
    return {name: contract.algorithm_version for name, contract in sorted(_CONTRACTS.items())}


@dataclass(frozen=True, slots=True)
class ReliabilityEvidence:
    """Immutable canonical evidence whose result and full envelope are independently hashed."""

    name: str
    kind: ReliabilityEvidenceKind
    algorithm_version: str
    input_sha256: str
    source_matrix_sha256: str
    result_canonical_json: str
    result_sha256: str
    evidence_sha256: str
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("unsupported reliability evidence schema")
        contract = expected_reliability_contract(self.name)
        if self.kind != contract.kind:
            raise ValueError("reliability evidence kind does not match its contract")
        if self.algorithm_version != contract.algorithm_version:
            raise ValueError("reliability evidence algorithm version does not match its contract")
        _require_sha256(self.input_sha256, "input_sha256")
        _require_sha256(self.source_matrix_sha256, "source_matrix_sha256")
        _require_sha256(self.result_sha256, "result_sha256")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        result = _decode_result(self.result_canonical_json)
        _validate_result(self.name, result)
        canonical_result = _canonical_json(result)
        if canonical_result.decode("ascii") != self.result_canonical_json:
            raise ValueError("reliability result must use canonical JSON")
        if _sha256(canonical_result) != self.result_sha256:
            raise ValueError("reliability result SHA does not match canonical result bytes")
        if _sha256(_canonical_json(self._identity_payload(result))) != self.evidence_sha256:
            raise ValueError("reliability evidence SHA does not match canonical content")

    @classmethod
    def create(
        cls,
        *,
        name: str,
        kind: ReliabilityEvidenceKind,
        algorithm_version: str,
        input_sha256: str,
        source_matrix_sha256: str,
        result: Mapping[str, object],
    ) -> ReliabilityEvidence:
        canonical_result = _canonical_json(dict(result))
        result_sha256 = _sha256(canonical_result)
        identity = {
            "schema_version": _SCHEMA_VERSION,
            "name": name,
            "kind": kind,
            "algorithm_version": algorithm_version,
            "input_sha256": input_sha256,
            "source_matrix_sha256": source_matrix_sha256,
            "result": json.loads(canonical_result),
            "result_canonical_json": canonical_result.decode("ascii"),
            "result_sha256": result_sha256,
        }
        return cls(
            name=name,
            kind=kind,
            algorithm_version=algorithm_version,
            input_sha256=input_sha256,
            source_matrix_sha256=source_matrix_sha256,
            result_canonical_json=canonical_result.decode("ascii"),
            result_sha256=result_sha256,
            evidence_sha256=_sha256(_canonical_json(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReliabilityEvidence:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "name",
            "kind",
            "algorithm_version",
            "input_sha256",
            "source_matrix_sha256",
            "result",
            "result_canonical_json",
            "result_sha256",
            "evidence_sha256",
        }:
            raise ValueError("reliability evidence schema fields are invalid")
        result = value["result"]
        if not isinstance(result, Mapping) or not result:
            raise ValueError("reliability evidence result must be a non-empty object")
        textual_fields = {
            name: value[name]
            for name in (
                "schema_version",
                "name",
                "kind",
                "algorithm_version",
                "input_sha256",
                "source_matrix_sha256",
                "result_canonical_json",
                "result_sha256",
                "evidence_sha256",
            )
        }
        if any(not isinstance(item, str) for item in textual_fields.values()):
            raise ValueError("reliability evidence schema fields are invalid")
        result_canonical_json = _canonical_json(dict(result)).decode("ascii")
        if textual_fields["result_canonical_json"] != result_canonical_json:
            raise ValueError("reliability result bytes do not match the structured result")
        return cls(
            schema_version=cast(str, textual_fields["schema_version"]),
            name=cast(str, textual_fields["name"]),
            kind=cast(ReliabilityEvidenceKind, textual_fields["kind"]),
            algorithm_version=cast(str, textual_fields["algorithm_version"]),
            input_sha256=cast(str, textual_fields["input_sha256"]),
            source_matrix_sha256=cast(str, textual_fields["source_matrix_sha256"]),
            result_canonical_json=result_canonical_json,
            result_sha256=cast(str, textual_fields["result_sha256"]),
            evidence_sha256=cast(str, textual_fields["evidence_sha256"]),
        )

    @property
    def result(self) -> dict[str, object]:
        return _decode_result(self.result_canonical_json)

    def to_dict(self) -> dict[str, object]:
        return {**self._identity_payload(self.result), "evidence_sha256": self.evidence_sha256}

    def _identity_payload(self, result: Mapping[str, object]) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "kind": self.kind,
            "algorithm_version": self.algorithm_version,
            "input_sha256": self.input_sha256,
            "source_matrix_sha256": self.source_matrix_sha256,
            "result": dict(result),
            "result_canonical_json": self.result_canonical_json,
            "result_sha256": self.result_sha256,
        }


def execute_reliability_evidence(
    *,
    discovery: FeatureMatrix,
    stability: StabilityReport,
    assignments: Sequence[int],
    sequence_lengths: Sequence[int],
    projection: PCAProjection,
    clustering: KMeansResult,
    work_budget_sha256: str,
    partition_projection_cells: int,
    maximum_partition_projection_cells: int,
    maximum_reliability_control_projection_cells: int,
    maximum_aggregate_projection_cells: int,
) -> tuple[dict[str, ReliabilityEvidence], dict[str, ReliabilityEvidence]]:
    """Execute the four frozen bounded outcome-blind checks for one discovery matrix."""

    return (
        {
            "seed_perturbation": execute_seed_perturbation_evidence(discovery, stability),
            "time_order_preserving_null": execute_time_order_preserving_null(
                discovery,
                sequence_lengths,
                projection=projection,
                clustering=clustering,
                work_budget_sha256=work_budget_sha256,
                partition_projection_cells=partition_projection_cells,
                maximum_partition_projection_cells=maximum_partition_projection_cells,
                maximum_reliability_control_projection_cells=(
                    maximum_reliability_control_projection_cells
                ),
                maximum_aggregate_projection_cells=maximum_aggregate_projection_cells,
            ),
        },
        {
            "single_cluster": execute_single_cluster_baseline(discovery),
            "unconditional_recurrence": execute_unconditional_recurrence_baseline(
                discovery, assignments
            ),
        },
    )


def execute_seed_perturbation_evidence(
    discovery: FeatureMatrix,
    stability: StabilityReport,
) -> ReliabilityEvidence:
    if not isinstance(discovery, FeatureMatrix):
        raise TypeError("discovery must be a FeatureMatrix")
    if not isinstance(stability, StabilityReport):
        raise TypeError("stability must be a StabilityReport")
    contract = expected_reliability_contract("seed_perturbation")
    return ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256=discovery.sha256,
        source_matrix_sha256=discovery.sha256,
        result={
            "seed_count": len(stability.seed_ari),
            "seed_ari": list(stability.seed_ari),
            "minimum_seed_ari": min(stability.seed_ari),
        },
    )


def execute_time_order_preserving_null(
    discovery: FeatureMatrix,
    sequence_lengths: Sequence[int],
    *,
    projection: PCAProjection,
    clustering: KMeansResult,
    work_budget_sha256: str,
    partition_projection_cells: int,
    maximum_partition_projection_cells: int,
    maximum_reliability_control_projection_cells: int,
    maximum_aggregate_projection_cells: int,
) -> ReliabilityEvidence:
    rows, width = _matrix_dimensions(discovery)
    _require_sha256(work_budget_sha256, "work_budget_sha256")
    projection_cells = rows * len(projection.components)
    aggregate_projection_cells = partition_projection_cells + projection_cells
    projection_budget_values = (
        (partition_projection_cells, maximum_partition_projection_cells, "partition"),
        (
            projection_cells,
            maximum_reliability_control_projection_cells,
            "reliability-control",
        ),
        (aggregate_projection_cells, maximum_aggregate_projection_cells, "aggregate"),
    )
    for observed, limit, label in projection_budget_values:
        if not _is_integer(observed) or observed < 1:
            raise ValueError(f"{label} projection cells must be a positive integer")
        if not _is_integer(limit) or limit < 1:
            raise ValueError(f"maximum {label} projection cells must be a positive integer")
        if observed > limit:
            raise ValueError(f"{label} projection cells exceed the frozen work budget")
    projection_sha256 = pca_projection_sha256(discovery, projection)
    if not isinstance(clustering, KMeansResult):
        raise TypeError("clustering must be a KMeansResult")
    if clustering.projection_sha256 != projection_sha256:
        raise ValueError("null detector clustering does not bind the frozen PCA projection")
    if not clustering.centroids or any(
        len(centroid) != len(projection.components) for centroid in clustering.centroids
    ):
        raise ValueError("null detector centroids do not match the frozen PCA projection")
    if len(clustering.assignments) != rows:
        raise ValueError("null detector assignments do not cover the discovery matrix")
    if not math.isfinite(clustering.inertia) or clustering.inertia < 0.0:
        raise ValueError("null detector primary inertia must be finite and non-negative")
    lengths = tuple(sequence_lengths)
    if (
        not lengths
        or any(not _is_integer(length) or length < 1 for length in lengths)
        or sum(lengths) != rows
    ):
        raise ValueError("null sequence lengths must exactly cover the discovery matrix")
    transformed = [list(map(float, row)) for row in discovery.values]
    sequence_evidence: list[dict[str, object]] = []
    row_start = 0
    for length in lengths:
        offsets = tuple(
            0 if length == 1 else ((column + 1) % length or 1) for column in range(width)
        )
        for local_row in range(length):
            for column in range(width):
                source_row = row_start + ((local_row + offsets[column]) % length)
                transformed[row_start + local_row][column] = float(
                    discovery.values[source_row][column]
                )
        sequence_evidence.append(
            {
                "row_start": row_start,
                "row_count": length,
                "column_offsets": list(offsets),
            }
        )
        row_start += length
    transformed_values = tuple(tuple(row) for row in transformed)
    mean_absolute_change = math.fsum(
        abs(float(discovery.values[row][column]) - transformed_values[row][column])
        for row in range(rows)
        for column in range(width)
    ) / (rows * width)
    null_scores = tuple(
        tuple(
            math.fsum(
                (value - projection.means[column]) * component[column]
                for column, value in enumerate(row)
            )
            for component in projection.components
        )
        for row in transformed_values
    )
    null_assignments = tuple(
        min(
            range(len(clustering.centroids)),
            key=lambda label: _squared_distance(score, clustering.centroids[label]),
        )
        for score in null_scores
    )
    null_assignment_counts = {
        str(label): null_assignments.count(label) for label in range(len(clustering.centroids))
    }
    null_inertia = math.fsum(
        _squared_distance(score, clustering.centroids[label])
        for score, label in zip(null_scores, null_assignments, strict=True)
    )
    detector_input_sha256 = _sha256(
        _canonical_json(
            {
                "discovery_matrix_sha256": discovery.sha256,
                "projection_sha256": projection_sha256,
                "centroids": clustering.centroids,
                "primary_inertia": clustering.inertia,
                "sequence_lengths": lengths,
                "work_budget_sha256": work_budget_sha256,
                "partition_projection_cells": partition_projection_cells,
                "maximum_partition_projection_cells": maximum_partition_projection_cells,
                "reliability_control_projection_cells": projection_cells,
                "maximum_reliability_control_projection_cells": (
                    maximum_reliability_control_projection_cells
                ),
                "aggregate_projection_cells": aggregate_projection_cells,
                "maximum_aggregate_projection_cells": maximum_aggregate_projection_cells,
            }
        )
    )
    contract = expected_reliability_contract("time_order_preserving_null")
    return ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256=detector_input_sha256,
        source_matrix_sha256=discovery.sha256,
        result={
            "row_count": rows,
            "feature_count": width,
            "sequence_count": len(lengths),
            "sequences": sequence_evidence,
            "transformed_matrix_sha256": _sha256(_canonical_json(transformed_values)),
            "mean_absolute_change": mean_absolute_change,
            "projection_sha256": projection_sha256,
            "frozen_centroids_sha256": _sha256(_canonical_json(clustering.centroids)),
            "projected_null_sha256": _sha256(_canonical_json(null_scores)),
            "null_assignment_counts": null_assignment_counts,
            "primary_inertia": clustering.inertia,
            "null_inertia": null_inertia,
            "inertia_delta": null_inertia - clustering.inertia,
            "work_budget_sha256": work_budget_sha256,
            "partition_projection_cells": partition_projection_cells,
            "maximum_partition_projection_cells": maximum_partition_projection_cells,
            "reliability_control_projection_cells": projection_cells,
            "maximum_reliability_control_projection_cells": (
                maximum_reliability_control_projection_cells
            ),
            "aggregate_projection_cells": aggregate_projection_cells,
            "maximum_aggregate_projection_cells": maximum_aggregate_projection_cells,
        },
    )


def execute_single_cluster_baseline(discovery: FeatureMatrix) -> ReliabilityEvidence:
    rows, width = _matrix_dimensions(discovery)
    centroid = tuple(
        math.fsum(float(row[column]) for row in discovery.values) / rows for column in range(width)
    )
    within_cluster_sum_squares = math.fsum(
        (float(row[column]) - centroid[column]) ** 2
        for row in discovery.values
        for column in range(width)
    )
    contract = expected_reliability_contract("single_cluster")
    return ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256=discovery.sha256,
        source_matrix_sha256=discovery.sha256,
        result={
            "row_count": rows,
            "feature_count": width,
            "centroid": list(centroid),
            "within_cluster_sum_squares": within_cluster_sum_squares,
        },
    )


def execute_unconditional_recurrence_baseline(
    discovery: FeatureMatrix,
    assignments: Sequence[int],
) -> ReliabilityEvidence:
    rows, _ = _matrix_dimensions(discovery)
    labels = tuple(assignments)
    if len(labels) != rows or any(
        isinstance(label, bool) or not isinstance(label, int) or label < 0 for label in labels
    ):
        raise ValueError("unconditional recurrence assignments must cover every matrix row")
    counts: dict[int, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    state_counts = {str(label): count for label, count in sorted(counts.items())}
    state_probabilities = {label: count / rows for label, count in state_counts.items()}
    contract = expected_reliability_contract("unconditional_recurrence")
    return ReliabilityEvidence.create(
        name=contract.name,
        kind=contract.kind,
        algorithm_version=contract.algorithm_version,
        input_sha256=discovery.sha256,
        source_matrix_sha256=discovery.sha256,
        result={
            "row_count": rows,
            "state_count": len(state_counts),
            "state_counts": state_counts,
            "state_probabilities": state_probabilities,
            "maximum_probability": max(state_probabilities.values()),
        },
    )


def _decode_result(value: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("reliability result JSON is malformed") from error
    if not isinstance(decoded, dict) or not decoded:
        raise ValueError("reliability result must be a non-empty object")
    return cast(dict[str, object], decoded)


def _matrix_dimensions(matrix: FeatureMatrix) -> tuple[int, int]:
    if not isinstance(matrix, FeatureMatrix):
        raise TypeError("discovery must be a FeatureMatrix")
    rows = len(matrix.values)
    width = len(matrix.feature_names)
    if rows < 1 or width < 1 or any(len(row) != width for row in matrix.values):
        raise ValueError("discovery matrix must be non-empty and rectangular")
    return rows, width


def _validate_result(name: str, result: Mapping[str, object]) -> None:
    validators = {
        "seed_perturbation": _validate_seed_result,
        "time_order_preserving_null": _validate_null_result,
        "single_cluster": _validate_single_cluster_result,
        "unconditional_recurrence": _validate_recurrence_result,
    }
    validators[name](result)


def _validate_seed_result(result: Mapping[str, object]) -> None:
    _require_result_fields(result, {"seed_count", "seed_ari", "minimum_seed_ari"})
    values = _finite_number_list(result["seed_ari"], "seed_ari", minimum=-1.0, maximum=1.0)
    if not _is_integer(result["seed_count"]) or result["seed_count"] != len(values):
        raise ValueError("seed perturbation count does not match its evidence")
    minimum = _finite_number(result["minimum_seed_ari"], "minimum_seed_ari")
    if minimum != min(values):
        raise ValueError("seed perturbation minimum does not match its evidence")


def _validate_null_result(result: Mapping[str, object]) -> None:
    _require_result_fields(
        result,
        {
            "row_count",
            "feature_count",
            "sequence_count",
            "sequences",
            "transformed_matrix_sha256",
            "mean_absolute_change",
            "projection_sha256",
            "frozen_centroids_sha256",
            "projected_null_sha256",
            "null_assignment_counts",
            "primary_inertia",
            "null_inertia",
            "inertia_delta",
            "work_budget_sha256",
            "partition_projection_cells",
            "maximum_partition_projection_cells",
            "reliability_control_projection_cells",
            "maximum_reliability_control_projection_cells",
            "aggregate_projection_cells",
            "maximum_aggregate_projection_cells",
        },
    )
    rows = result["row_count"]
    width = result["feature_count"]
    sequence_count = result["sequence_count"]
    sequences = result["sequences"]
    if not _is_integer(rows) or rows < 2 or not _is_integer(width) or width < 1:
        raise ValueError("null evidence dimensions are invalid")
    if (
        not _is_integer(sequence_count)
        or sequence_count < 1
        or not isinstance(sequences, list)
        or len(sequences) != sequence_count
    ):
        raise ValueError("null evidence sequences are invalid")
    expected_start = 0
    for sequence in sequences:
        if not isinstance(sequence, Mapping) or set(sequence) != {
            "row_start",
            "row_count",
            "column_offsets",
        }:
            raise ValueError("null evidence sequence schema is invalid")
        start = sequence["row_start"]
        length = sequence["row_count"]
        offsets = sequence["column_offsets"]
        if (
            not _is_integer(start)
            or start != expected_start
            or not _is_integer(length)
            or length < 1
            or not isinstance(offsets, list)
            or len(offsets) != width
            or any(
                not _is_integer(offset)
                or (offset != 0 if length == 1 else not 1 <= offset < length)
                for offset in offsets
            )
        ):
            raise ValueError("null evidence sequence values are invalid")
        expected_start += length
    if expected_start != rows:
        raise ValueError("null evidence sequences do not cover every row")
    _require_sha256(cast(str, result["transformed_matrix_sha256"]), "transformed_matrix_sha256")
    _require_sha256(cast(str, result["projection_sha256"]), "projection_sha256")
    _require_sha256(cast(str, result["frozen_centroids_sha256"]), "frozen_centroids_sha256")
    _require_sha256(cast(str, result["projected_null_sha256"]), "projected_null_sha256")
    _require_sha256(cast(str, result["work_budget_sha256"]), "work_budget_sha256")
    projection_values = tuple(
        result[name]
        for name in (
            "partition_projection_cells",
            "maximum_partition_projection_cells",
            "reliability_control_projection_cells",
            "maximum_reliability_control_projection_cells",
            "aggregate_projection_cells",
            "maximum_aggregate_projection_cells",
        )
    )
    if any(not _is_integer(value) or value < 1 for value in projection_values):
        raise ValueError("null projection-budget evidence is invalid")
    (
        partition_cells,
        partition_limit,
        control_cells,
        control_limit,
        aggregate_cells,
        aggregate_limit,
    ) = tuple(cast(int, value) for value in projection_values)
    if (
        partition_cells > partition_limit
        or control_cells > control_limit
        or aggregate_cells > aggregate_limit
        or aggregate_cells != partition_cells + control_cells
    ):
        raise ValueError("null projection-budget evidence is inconsistent")
    if _finite_number(result["mean_absolute_change"], "mean_absolute_change") < 0.0:
        raise ValueError("null mean absolute change must be non-negative")
    counts = result["null_assignment_counts"]
    if (
        not isinstance(counts, Mapping)
        or not counts
        or any(
            not str(label).isdigit() or not _is_integer(count) or count < 0
            for label, count in counts.items()
        )
        or sum(cast(int, count) for count in counts.values()) != rows
    ):
        raise ValueError("null assignment counts must cover every row")
    primary_inertia = _finite_number(result["primary_inertia"], "primary_inertia")
    null_inertia = _finite_number(result["null_inertia"], "null_inertia")
    inertia_delta = _finite_number(result["inertia_delta"], "inertia_delta")
    if primary_inertia < 0.0 or null_inertia < 0.0:
        raise ValueError("null detector inertias must be non-negative")
    if not math.isclose(
        inertia_delta,
        null_inertia - primary_inertia,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("null detector inertia delta is inconsistent")


def _validate_single_cluster_result(result: Mapping[str, object]) -> None:
    _require_result_fields(
        result,
        {"row_count", "feature_count", "centroid", "within_cluster_sum_squares"},
    )
    rows = result["row_count"]
    width = result["feature_count"]
    if not _is_integer(rows) or rows < 1 or not _is_integer(width) or width < 1:
        raise ValueError("single-cluster evidence dimensions are invalid")
    centroid = _finite_number_list(result["centroid"], "centroid")
    if len(centroid) != width:
        raise ValueError("single-cluster centroid width is invalid")
    if _finite_number(result["within_cluster_sum_squares"], "within_cluster_sum_squares") < 0:
        raise ValueError("single-cluster dispersion must be non-negative")


def _validate_recurrence_result(result: Mapping[str, object]) -> None:
    _require_result_fields(
        result,
        {
            "row_count",
            "state_count",
            "state_counts",
            "state_probabilities",
            "maximum_probability",
        },
    )
    rows = result["row_count"]
    state_count = result["state_count"]
    counts = result["state_counts"]
    probabilities = result["state_probabilities"]
    if not _is_integer(rows) or rows < 1 or not _is_integer(state_count) or state_count < 1:
        raise ValueError("recurrence evidence dimensions are invalid")
    if (
        not isinstance(counts, Mapping)
        or not isinstance(probabilities, Mapping)
        or len(counts) != state_count
        or set(counts) != set(probabilities)
        or any(
            not str(label).isdigit() or not _is_integer(count) or count < 1
            for label, count in counts.items()
        )
        or sum(cast(int, count) for count in counts.values()) != rows
    ):
        raise ValueError("recurrence state counts are invalid")
    probability_values = tuple(
        _finite_number(value, "state_probability") for value in probabilities.values()
    )
    probabilities_match_counts = all(
        math.isclose(
            _finite_number(probabilities[label], "state_probability"),
            cast(int, count) / rows,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for label, count in counts.items()
    )
    if (
        any(not 0.0 <= value <= 1.0 for value in probability_values)
        or not math.isclose(math.fsum(probability_values), 1.0, rel_tol=0.0, abs_tol=1e-12)
        or not probabilities_match_counts
    ):
        raise ValueError("recurrence probabilities are invalid")
    maximum = _finite_number(result["maximum_probability"], "maximum_probability")
    if maximum != max(probability_values):
        raise ValueError("recurrence maximum probability does not match its evidence")


def _require_result_fields(result: Mapping[str, object], expected: set[str]) -> None:
    if set(result) != expected:
        raise ValueError("reliability result schema fields are invalid")


def _finite_number_list(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    values = tuple(_finite_number(item, label) for item in value)
    if minimum is not None and any(item < minimum for item in values):
        raise ValueError(f"{label} is below its bound")
    if maximum is not None and any(item > maximum for item in values):
        raise ValueError(f"{label} is above its bound")
    return values


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise ValueError("reliability evidence must be finite canonical JSON") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _squared_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("null detector vector dimensions do not match")
    return math.fsum(
        (left_value - right_value) ** 2 for left_value, right_value in zip(left, right, strict=True)
    )


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


__all__ = [
    "ReliabilityContract",
    "ReliabilityEvidence",
    "execute_reliability_evidence",
    "execute_seed_perturbation_evidence",
    "execute_single_cluster_baseline",
    "execute_time_order_preserving_null",
    "execute_unconditional_recurrence_baseline",
    "expected_reliability_contract",
    "reliability_algorithm_versions",
]
