"""Atomic, checksum-pinned orchestration for outcome-blind discovery runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Mapping, Sequence

from market_structure_lab.discovery.behaviours import FrozenBehaviour, freeze_behaviours
from market_structure_lab.discovery.evidence import (
    AIInterpretation,
    BehaviourEvidencePack,
)
from market_structure_lab.discovery.kmeans import fit_projected_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix, build_feature_matrix
from market_structure_lab.discovery.motifs import MotifMatch, discover_motifs
from market_structure_lab.discovery.pca import fit_pca
from market_structure_lab.discovery.splits import (
    DiscoveryInput,
    FrozenDiscoverySplit,
    PartitionRole,
)
from market_structure_lab.discovery.stability import (
    StabilityPolicy,
    evaluate_cluster_stability,
)
from market_structure_lab.discovery.transitions import (
    ClusterObservation,
    ClusterTransitionMatrix,
    ClusterTransitionRow,
    estimate_cluster_transitions,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry

_RUN_ID = re.compile(r"^DR-[0-9]{6}$")
_DATASET_ID = re.compile(r"^DS-[0-9]{6}$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CODE_COMMIT = re.compile(r"^[A-Fa-f0-9]{7,64}$")
_MAX_INTERPRETATIONS = 1_000
_SUBSAMPLE_FRACTION = 0.75
_MOTIF_MAX_WINDOW_LENGTH = 4
_MOTIF_EXCLUSION_ZONE = 2
_MOTIF_TOP_K = 3
_TRANSITION_BOOTSTRAP_ITERATIONS = 100
_TRANSITION_BLOCK_LENGTH = 2
_TRANSITION_CONFIDENCE_LEVEL = 0.95


@dataclass(frozen=True, slots=True)
class DiscoveryRunConfig:
    run_id: str
    dataset_snapshot_id: str
    dataset_snapshot_sha256: str
    feature_set_id: str
    registry_sha256: str
    config_version: str
    split: FrozenDiscoverySplit
    feature_names: tuple[str, ...]
    pca_components: int
    clusters: int
    seeds: tuple[int, ...]
    max_rows: int
    max_iterations: int
    tolerance: float
    stability_policy: StabilityPolicy
    code_commit: str
    lock_sha256: str
    parent_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_pattern(self.run_id, _RUN_ID, "run_id")
        _require_pattern(self.dataset_snapshot_id, _DATASET_ID, "dataset_snapshot_id")
        _require_sha256(self.dataset_snapshot_sha256, "dataset_snapshot_sha256")
        _require_pattern(self.feature_set_id, _FEATURE_SET_ID, "feature_set_id")
        _require_sha256(self.registry_sha256, "registry_sha256")
        _require_text(self.config_version, "config_version")
        if not isinstance(self.split, FrozenDiscoverySplit):
            raise TypeError("split must be a FrozenDiscoverySplit")
        if (
            not isinstance(self.feature_names, tuple)
            or not self.feature_names
            or len(set(self.feature_names)) != len(self.feature_names)
            or any(not isinstance(name, str) or not name for name in self.feature_names)
        ):
            raise ValueError("feature_names must be a unique non-empty tuple")
        for value, label in (
            (self.pca_components, "pca_components"),
            (self.clusters, "clusters"),
            (self.max_rows, "max_rows"),
            (self.max_iterations, "max_iterations"),
        ):
            _positive_integer(value, label)
        if (
            not isinstance(self.seeds, tuple)
            or not self.seeds
            or len(set(self.seeds)) != len(self.seeds)
            or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.seeds)
        ):
            raise ValueError("seeds must be a unique non-empty tuple of integers")
        if (
            isinstance(self.tolerance, bool)
            or not isinstance(self.tolerance, (float, int))
            or not math.isfinite(float(self.tolerance))
            or self.tolerance < 0.0
        ):
            raise ValueError("tolerance must be finite and non-negative")
        if not isinstance(self.stability_policy, StabilityPolicy):
            raise TypeError("stability_policy must be a StabilityPolicy")
        _require_pattern(self.code_commit, _CODE_COMMIT, "code_commit")
        _require_sha256(self.lock_sha256, "lock_sha256")
        if not isinstance(self.parent_run_ids, tuple) or len(set(self.parent_run_ids)) != len(
            self.parent_run_ids
        ):
            raise ValueError("parent_run_ids must be a unique tuple")
        for parent in self.parent_run_ids:
            _require_pattern(parent, _RUN_ID, "parent_run_id")
        object.__setattr__(self, "tolerance", float(self.tolerance))
        object.__setattr__(self, "parent_run_ids", tuple(sorted(self.parent_run_ids)))


@dataclass(frozen=True, slots=True)
class DiscoveryRunManifest:
    run_id: str
    status: Literal["completed", "rejected_unstable"]
    identity_sha256: str
    config_sha256: str
    artifact_sha256: tuple[tuple[str, str], ...]
    behaviours: tuple[FrozenBehaviour, ...]
    transition_rows: tuple[ClusterTransitionRow, ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "discovery-run-manifest-v1",
            "run_id": self.run_id,
            "status": self.status,
            "identity_sha256": self.identity_sha256,
            "config_sha256": self.config_sha256,
            "artifact_sha256": dict(self.artifact_sha256),
            "behaviour_ids": [item.behaviour_id for item in self.behaviours],
            "transition_rows": [_jsonable(row) for row in self.transition_rows],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class InterpretationManifest:
    run_id: str
    behaviour_ids: tuple[str, ...]
    identity_sha256: str
    artifact_sha256: tuple[tuple[str, str], ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "interpretation-manifest-v1",
            "run_id": self.run_id,
            "behaviour_ids": list(self.behaviour_ids),
            "identity_sha256": self.identity_sha256,
            "artifact_sha256": dict(self.artifact_sha256),
            "manifest_sha256": self.manifest_sha256,
        }


def run_discovery(
    *,
    config: DiscoveryRunConfig,
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    registry: FeatureRegistry,
    event_ids: Sequence[str],
    durations_seconds: Sequence[float],
    output_root: Path,
) -> DiscoveryRunManifest:
    """Execute and atomically publish one bounded outcome-blind discovery run."""

    _validate_run_inputs(config, discovery, development, registry, output_root)
    final_dir = output_root / config.run_id
    stage_dir = output_root / f".{config.run_id}.staging"
    if stage_dir.exists():
        raise RuntimeError(f"stale discovery staging directory exists: {stage_dir}")

    discovery_matrix = build_feature_matrix(
        discovery, registry, config.feature_names, config.max_rows
    )
    development_matrix = build_feature_matrix(
        development, registry, config.feature_names, config.max_rows
    )
    selected_discovery = _selected_rows(discovery, discovery_matrix)
    selected_development = _selected_rows(development, development_matrix)
    events = _validated_strings(event_ids, len(discovery_matrix.values), "event_ids")
    durations = _validated_durations(durations_seconds, len(discovery_matrix.values))
    projection = fit_pca(discovery_matrix, config.pca_components)
    clustering = fit_projected_kmeans(
        discovery_matrix,
        projection,
        clusters=config.clusters,
        seed=config.seeds[0],
        max_iterations=config.max_iterations,
        tolerance=config.tolerance,
    )
    periods, period_order = _adjacent_periods(selected_development)
    stability = evaluate_cluster_stability(
        discovery=discovery_matrix,
        development=development_matrix,
        projection=projection,
        base_result=clustering,
        symbols=tuple(row.symbol for row in selected_development),
        periods=periods,
        period_order=period_order,
        seeds=config.seeds,
        subsample_fraction=_SUBSAMPLE_FRACTION,
        policy=config.stability_policy,
    )
    behaviours = freeze_behaviours(
        run_id=config.run_id,
        matrix=discovery_matrix,
        projection=projection,
        clustering=clustering,
        stability=stability,
        event_ids=events,
        durations_seconds=durations,
        symbols=tuple(row.symbol for row in selected_discovery),
        description="Neutral recurring outcome-blind feature configurations.",
    )
    motif_payload = _motif_payload(selected_discovery, discovery_matrix)
    transition_matrix = _transition_evidence(selected_discovery, clustering.assignments, config)
    status: Literal["completed", "rejected_unstable"] = (
        "completed" if stability.accepted else "rejected_unstable"
    )
    config_payload = _config_payload(config)
    identity_payload = {
        "config": config_payload,
        "registry_sha256": registry.sha256,
        "discovery_input_sha256": _input_sha256(discovery),
        "development_input_sha256": _input_sha256(development),
        "event_ids": events,
        "durations_seconds": durations,
    }
    identity_sha256 = _sha256(_canonical_json(identity_payload))
    payloads: dict[str, bytes] = {
        "config.json": _json_file(config_payload),
        "projection.json": _json_file(projection),
        "clustering.json": _json_file(clustering),
        "stability.json": _json_file(stability),
        "behaviours.json": _json_file(behaviours),
        "motifs.json": _json_file(motif_payload),
        "transitions.json": _json_file(transition_matrix),
        "metrics.json": _json_file(
            {
                "status": status,
                "discovery_rows": len(discovery_matrix.values),
                "development_rows": len(development_matrix.values),
                "dropped_null_rows": {
                    "discovery": discovery_matrix.dropped_null_rows,
                    "development": development_matrix.dropped_null_rows,
                },
                "behaviours": len(behaviours),
                "motifs": _motif_count(motif_payload),
                "transitions": transition_matrix.total_transitions,
            }
        ),
        "summary.md": (
            f"# {config.run_id}\n\nStatus: {status}\n\n"
            "Outcome-blind discovery; final holdout was not accessed.\n"
        ).encode("utf-8"),
    }
    artifact_hashes = tuple(sorted((name, _sha256(content)) for name, content in payloads.items()))
    config_sha256 = _sha256(_canonical_json(config_payload))
    manifest_without_hash = {
        "schema_version": "discovery-run-manifest-v1",
        "run_id": config.run_id,
        "status": status,
        "identity_sha256": identity_sha256,
        "config_sha256": config_sha256,
        "artifact_sha256": dict(artifact_hashes),
        "behaviour_ids": [item.behaviour_id for item in behaviours],
        "transition_rows": [_jsonable(row) for row in transition_matrix.rows],
    }
    manifest = DiscoveryRunManifest(
        run_id=config.run_id,
        status=status,
        identity_sha256=identity_sha256,
        config_sha256=config_sha256,
        artifact_sha256=artifact_hashes,
        behaviours=behaviours,
        transition_rows=transition_matrix.rows,
        manifest_sha256=_sha256(_canonical_json(manifest_without_hash)),
    )
    manifest_bytes = _json_file(manifest.to_dict())
    if final_dir.exists():
        existing = _verify_bundle(final_dir)
        if existing.get("identity_sha256") != identity_sha256:
            raise RuntimeError("discovery run identity conflict")
        if (final_dir / "manifest.json").read_bytes() != manifest_bytes:
            raise RuntimeError("discovery run content conflict")
        return manifest

    payloads["manifest.json"] = manifest_bytes
    _publish_bundle(stage_dir, final_dir, payloads)
    return manifest


def publish_ai_interpretations(
    *,
    run_manifest: DiscoveryRunManifest,
    evidence: Sequence[BehaviourEvidencePack],
    interpretations: Sequence[AIInterpretation],
    output_root: Path,
) -> InterpretationManifest:
    """Validate detector identity and atomically publish AI interpretation evidence."""

    if not isinstance(run_manifest, DiscoveryRunManifest):
        raise TypeError("run_manifest must be a DiscoveryRunManifest")
    run_dir = output_root / run_manifest.run_id
    packs = tuple(sorted(evidence, key=lambda item: item.behaviour.behaviour_id))
    records = tuple(sorted(interpretations, key=lambda item: item.behaviour_id))
    if not packs or len(packs) > _MAX_INTERPRETATIONS or len(records) != len(packs):
        raise ValueError("evidence and interpretations must have equal bounded non-zero size")
    expected_ids = tuple(item.behaviour_id for item in run_manifest.behaviours)
    if tuple(item.behaviour.behaviour_id for item in packs) != expected_ids:
        raise ValueError("evidence must exactly cover the frozen run behaviours")
    if tuple(item.behaviour_id for item in records) != expected_ids:
        raise ValueError("interpretations must exactly cover the frozen run behaviours")
    for pack, record in zip(packs, records, strict=True):
        if pack.run_id != run_manifest.run_id:
            raise ValueError("evidence run identity changed")
        unknown_neighbours = (
            set(pack.nearest_behaviour_ids) | set(pack.contrasting_behaviour_ids)
        ) - set(expected_ids)
        if unknown_neighbours:
            raise ValueError("evidence neighbours must reference frozen run behaviours")
        detector_fields = tuple(item.feature_name for item in pack.behaviour.feature_distributions)
        if record.detector_fields != detector_fields:
            raise ValueError("AI interpretation detector fields changed")

    _verify_bundle(run_dir)
    stage_dir = run_dir / ".interpretations.staging"
    final_dir = run_dir / "interpretations"
    if stage_dir.exists():
        raise RuntimeError(f"stale interpretation staging directory exists: {stage_dir}")
    evidence_payload = [item.to_dict() for item in packs]
    interpretation_payload = [item.to_dict() for item in records]
    identity_sha256 = _sha256(
        _canonical_json(
            {
                "run_manifest_sha256": run_manifest.manifest_sha256,
                "evidence": evidence_payload,
                "interpretations": interpretation_payload,
            }
        )
    )
    payloads = {
        "evidence.json": _json_file(evidence_payload),
        "interpretations.json": _json_file(interpretation_payload),
    }
    artifact_hashes = tuple(sorted((name, _sha256(content)) for name, content in payloads.items()))
    manifest_without_hash = {
        "schema_version": "interpretation-manifest-v1",
        "run_id": run_manifest.run_id,
        "behaviour_ids": list(expected_ids),
        "identity_sha256": identity_sha256,
        "artifact_sha256": dict(artifact_hashes),
    }
    manifest = InterpretationManifest(
        run_id=run_manifest.run_id,
        behaviour_ids=expected_ids,
        identity_sha256=identity_sha256,
        artifact_sha256=artifact_hashes,
        manifest_sha256=_sha256(_canonical_json(manifest_without_hash)),
    )
    manifest_bytes = _json_file(manifest.to_dict())
    if final_dir.exists():
        existing = _verify_bundle(final_dir)
        if existing.get("identity_sha256") != identity_sha256:
            raise RuntimeError("interpretation identity conflict")
        if (final_dir / "manifest.json").read_bytes() != manifest_bytes:
            raise RuntimeError("interpretation content conflict")
        return manifest
    payloads["manifest.json"] = manifest_bytes
    _publish_bundle(stage_dir, final_dir, payloads)
    return manifest


def _validate_run_inputs(
    config: DiscoveryRunConfig,
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    registry: FeatureRegistry,
    output_root: Path,
) -> None:
    if not isinstance(config, DiscoveryRunConfig):
        raise TypeError("config must be a DiscoveryRunConfig")
    if not isinstance(discovery, DiscoveryInput) or not isinstance(development, DiscoveryInput):
        raise TypeError("discovery and development must be DiscoveryInput values")
    if discovery.partition != config.split.discovery:
        raise ValueError("discovery input does not match the frozen split")
    if development.partition != config.split.development:
        raise ValueError("development input does not match the frozen split")
    if discovery.partition.role is not PartitionRole.DISCOVERY:
        raise ValueError("discovery input must use discovery partition")
    if development.partition.role is not PartitionRole.DEVELOPMENT:
        raise ValueError("development input must use development partition")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    if (
        registry.feature_set_id != config.feature_set_id
        or registry.sha256 != config.registry_sha256
    ):
        raise ValueError("registry identity does not match discovery config")
    if (
        discovery.dataset_version != config.dataset_snapshot_id
        or development.dataset_version != config.dataset_snapshot_id
    ):
        raise ValueError("dataset snapshot identity does not match discovery inputs")
    if discovery.feature_set_id != config.feature_set_id:
        raise ValueError("discovery feature-set identity does not match config")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a Path")


def _selected_rows(input_value: DiscoveryInput, matrix: FeatureMatrix) -> tuple[FeatureRow, ...]:
    by_id = {_row_id(row): row for row in input_value.rows}
    return tuple(by_id[row_id] for row_id in matrix.row_ids)


def _adjacent_periods(
    rows: Sequence[FeatureRow],
) -> tuple[tuple[str, ...], tuple[str, str]]:
    ordered_cutoffs = tuple(sorted({row.information_cutoff for row in rows}))
    if len(ordered_cutoffs) < 2:
        raise ValueError("development rows require at least two chronological periods")
    threshold = ordered_cutoffs[len(ordered_cutoffs) // 2]
    periods = tuple(
        "development-early" if row.information_cutoff < threshold else "development-late"
        for row in rows
    )
    if len(set(periods)) != 2:
        raise ValueError("development rows require two populated adjacent periods")
    return periods, ("development-early", "development-late")


def _motif_payload(
    rows: Sequence[FeatureRow], matrix: FeatureMatrix
) -> tuple[dict[str, object], ...]:
    groups: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for row, values in zip(rows, matrix.values, strict=True):
        groups[(row.symbol, row.timeframe, row.segment_id)].append(values[0])
    payload: list[dict[str, object]] = []
    for key in sorted(groups):
        values = tuple(groups[key])
        if len(values) < 4:
            matches: tuple[MotifMatch, ...] = ()
            window_length = None
            exclusion_zone = None
            max_windows = 0
        else:
            window_length = min(_MOTIF_MAX_WINDOW_LENGTH, len(values))
            exclusion_zone = min(_MOTIF_EXCLUSION_ZONE, window_length - 1)
            max_windows = len(values) - window_length + 1
            matches = discover_motifs(
                values,
                window_length=window_length,
                exclusion_zone=exclusion_zone,
                max_windows=max_windows,
                top_k=_MOTIF_TOP_K,
            )
        payload.append(
            {
                "symbol": key[0],
                "timeframe": key[1],
                "segment_id": key[2],
                "window_length": window_length,
                "exclusion_zone": exclusion_zone,
                "max_windows": max_windows,
                "top_k": _MOTIF_TOP_K,
                "matches": [_jsonable(item) for item in matches],
            }
        )
    return tuple(payload)


def _transition_evidence(
    rows: Sequence[FeatureRow],
    assignments: Sequence[int],
    config: DiscoveryRunConfig,
) -> ClusterTransitionMatrix:
    observations = tuple(
        ClusterObservation(
            label=label,
            timestamp=row.timestamp,
            information_cutoff=row.information_cutoff,
            symbol=row.symbol,
            timeframe=row.timeframe,
            segment_id=row.segment_id,
            session_id=row.timestamp.date().isoformat(),
        )
        for row, label in zip(rows, assignments, strict=True)
    )
    return estimate_cluster_transitions(
        observations,
        horizon=1,
        max_rows=config.max_rows,
        seed=config.seeds[0],
        bootstrap_iterations=_TRANSITION_BOOTSTRAP_ITERATIONS,
        block_length=_TRANSITION_BLOCK_LENGTH,
        confidence_level=_TRANSITION_CONFIDENCE_LEVEL,
    )


def _motif_count(payload: Sequence[Mapping[str, object]]) -> int:
    total = 0
    for group in payload:
        matches = group.get("matches")
        if not isinstance(matches, list):
            raise RuntimeError("motif payload is internally inconsistent")
        total += len(matches)
    return total


def _config_payload(config: DiscoveryRunConfig) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "dataset_snapshot_id": config.dataset_snapshot_id,
        "dataset_snapshot_sha256": config.dataset_snapshot_sha256,
        "feature_set_id": config.feature_set_id,
        "registry_sha256": config.registry_sha256,
        "config_version": config.config_version,
        "split": {
            "split_id": config.split.split_id,
            "sha256": config.split.sha256,
            "discovery": config.split.discovery.to_dict(),
            "development": config.split.development.to_dict(),
            "holdout": config.split.holdout.to_dict(),
            "asset_holdouts": list(config.split.asset_holdouts),
        },
        "feature_names": list(config.feature_names),
        "pca_components": config.pca_components,
        "clusters": config.clusters,
        "seeds": list(config.seeds),
        "max_rows": config.max_rows,
        "max_iterations": config.max_iterations,
        "tolerance": config.tolerance,
        "stability_policy": _jsonable(config.stability_policy),
        "code_commit": config.code_commit,
        "lock_sha256": config.lock_sha256,
        "parent_run_ids": list(config.parent_run_ids),
        "orchestration_parameters": {
            "subsample_fraction": _SUBSAMPLE_FRACTION,
            "motif_max_window_length": _MOTIF_MAX_WINDOW_LENGTH,
            "motif_exclusion_zone": _MOTIF_EXCLUSION_ZONE,
            "motif_top_k": _MOTIF_TOP_K,
            "transition_horizon": 1,
            "transition_bootstrap_iterations": _TRANSITION_BOOTSTRAP_ITERATIONS,
            "transition_block_length": _TRANSITION_BLOCK_LENGTH,
            "transition_confidence_level": _TRANSITION_CONFIDENCE_LEVEL,
        },
    }


def _input_sha256(input_value: DiscoveryInput) -> str:
    return _sha256(
        _canonical_json(
            {
                "partition": input_value.partition.to_dict(),
                "dataset_version": input_value.dataset_version,
                "feature_set_id": input_value.feature_set_id,
                "registry_id": input_value.registry_id,
                "rows": [json.loads(row.canonical_json()) for row in input_value.rows],
            }
        )
    )


def _verify_bundle(directory: Path) -> Mapping[str, object]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("published bundle is missing its manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("published bundle manifest is tampered") from error
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise RuntimeError("published bundle manifest is tampered")
    for name, expected in hashes.items():
        path = directory / name
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not path.is_file()
            or _sha256(path.read_bytes()) != expected
        ):
            raise RuntimeError("published bundle artifact tamper detected")
    supplied_manifest_hash = manifest.pop("manifest_sha256", None)
    if (
        not isinstance(supplied_manifest_hash, str)
        or _sha256(_canonical_json(manifest)) != supplied_manifest_hash
    ):
        raise RuntimeError("published bundle manifest tamper detected")
    manifest["manifest_sha256"] = supplied_manifest_hash
    return manifest


def _publish_bundle(stage_dir: Path, final_dir: Path, payloads: Mapping[str, bytes]) -> None:
    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir()
    try:
        for name, content in payloads.items():
            (stage_dir / name).write_bytes(content)
        stage_dir.replace(final_dir)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        raise


def _validated_strings(values: Sequence[str], expected: int, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) != expected:
        raise ValueError(f"{label} length must match discovery matrix rows")
    result = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must be unique")
    return result


def _validated_durations(values: Sequence[float], expected: int) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or len(values) != expected:
        raise ValueError("durations_seconds length must match discovery matrix rows")
    result: list[float] = []
    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(float(value))
            or value < 0.0
        ):
            raise ValueError("durations_seconds must be finite and non-negative")
        result.append(float(value))
    return tuple(result)


def _row_id(row: FeatureRow) -> str:
    timestamp = row.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return f"{row.symbol}|{row.timeframe}|{timestamp}"


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _json_file(value: object) -> bytes:
    return _canonical_json(_jsonable(value)) + b"\n"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_pattern(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} has an invalid canonical format")


def _require_sha256(value: str, label: str) -> None:
    _require_pattern(value, _SHA256, label)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value
