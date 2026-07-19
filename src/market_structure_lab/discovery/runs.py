"""Atomic, checksum-pinned orchestration for outcome-blind discovery runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping, Sequence

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    regular_file_matches,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.data.derived import (
    DerivedPublicationManifest,
    verify_published_feature_rows,
)
from market_structure_lab.data.export import SnapshotManifest, verify_snapshot
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
    DiscoveryProvenance,
    FrozenDiscoverySplit,
    PartitionRole,
    freeze_discovery_provenance,
    make_discovery_input,
)
from market_structure_lab.discovery.stability import (
    STABILITY_ALGORITHM_VERSION,
    AdjacentPeriodStabilityPolicy,
    StabilityPolicy,
    evaluate_cluster_stability,
)
from market_structure_lab.discovery.transitions import (
    ClusterObservation,
    ClusterTransitionMatrix,
    estimate_cluster_transitions,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.normalization import RobustNormalizer
from market_structure_lab.features.registry import FeatureRegistry
from market_structure_lab.experiments import (
    ArtifactIdentity,
    ExperimentConfig,
    ExperimentMode,
    TerminalStatus,
    TrialRange,
    save_experiment_result,
    verify_trial_receipt,
)

_RUN_ID = re.compile(r"^DR-[0-9]{6}$")
_DATASET_ID = re.compile(r"^DS-[0-9]{6}$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CODE_COMMIT = re.compile(r"^[A-Fa-f0-9]{7,64}$")
_MAX_INTERPRETATIONS = 1_000
_MAX_BUNDLE_ENTRIES = 20_000
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_SUBSAMPLE_FRACTION = 0.75
_MOTIF_MAX_WINDOW_LENGTH = 4
_MOTIF_EXCLUSION_ZONE = 2
_MOTIF_TOP_K = 3
_TRANSITION_BOOTSTRAP_ITERATIONS = 100
_TRANSITION_BLOCK_LENGTH = 2
_TRANSITION_CONFIDENCE_LEVEL = 0.95
_ROW_IDENTITY_FIELDS = (
    "dataset_version",
    "config_version",
    "profile_version",
    "window_policy_id",
    "feature_set_id",
    "registry_id",
)


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
    adjacent_period_stability_policy: AdjacentPeriodStabilityPolicy
    code_commit: str
    lock_sha256: str
    parent_run_ids: tuple[str, ...] = ()
    feature_publication_id: str | None = None
    feature_publication_sha256: str | None = None
    normalizer_id: str | None = None
    normalizer_sha256: str | None = None
    provenance: DiscoveryProvenance | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

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
        if not isinstance(self.adjacent_period_stability_policy, AdjacentPeriodStabilityPolicy):
            raise TypeError(
                "adjacent_period_stability_policy must be an AdjacentPeriodStabilityPolicy"
            )
        _require_pattern(self.code_commit, _CODE_COMMIT, "code_commit")
        _require_sha256(self.lock_sha256, "lock_sha256")
        if not isinstance(self.provenance, DiscoveryProvenance):
            raise TypeError("verified discovery provenance is required")
        _require_text(self.feature_publication_id, "feature_publication_id")
        _require_sha256(
            _require_text(self.feature_publication_sha256, "feature_publication_sha256"),
            "feature_publication_sha256",
        )
        _require_text(self.normalizer_id, "normalizer_id")
        _require_sha256(
            _require_text(self.normalizer_sha256, "normalizer_sha256"),
            "normalizer_sha256",
        )
        started_at = _require_utc_datetime(self.started_at, "started_at")
        completed_at = _require_utc_datetime(self.completed_at, "completed_at")
        if started_at > completed_at:
            raise ValueError("started_at cannot be after completed_at")
        expected_provenance = {
            "snapshot_manifest_sha256": self.dataset_snapshot_sha256,
            "derived_publication_sha256": self.feature_publication_sha256,
            "registry_sha256": self.registry_sha256,
            "normalizer_sha256": self.normalizer_sha256,
            "feature_names": self.feature_names,
            "split_sha256": self.split.sha256,
            "code_commit": self.code_commit,
            "lock_sha256": self.lock_sha256,
        }
        for field, expected in expected_provenance.items():
            if getattr(self.provenance, field) != expected:
                raise ValueError(f"discovery provenance {field} does not match config")
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
    transition_matrix: ClusterTransitionMatrix
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "discovery-run-manifest-v2",
            "run_id": self.run_id,
            "status": self.status,
            "identity_sha256": self.identity_sha256,
            "config_sha256": self.config_sha256,
            "artifact_sha256": dict(self.artifact_sha256),
            "behaviour_ids": [item.behaviour_id for item in self.behaviours],
            "transition_matrix": _jsonable(self.transition_matrix),
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
            "schema_version": "interpretation-manifest-v2",
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
    snapshot_directory: Path | None = None,
    snapshot_manifest: SnapshotManifest | None = None,
    feature_publication_directory: Path | None = None,
    feature_publication: DerivedPublicationManifest | None = None,
    normalizer_artifact: bytes | None = None,
    lockfile_bytes: bytes | None = None,
) -> DiscoveryRunManifest:
    """Execute one bounded outcome-blind attempt with an immutable terminal receipt."""

    if not isinstance(config, DiscoveryRunConfig):
        raise TypeError("config must be a DiscoveryRunConfig")
    _validate_run_inputs(config, discovery, development, registry, output_root)
    _verify_runtime_code_identity(
        code_commit=config.code_commit,
        lockfile_bytes=lockfile_bytes,
    )
    normalizer = _verify_artifact_provenance(
        config=config,
        discovery=discovery,
        development=development,
        registry=registry,
        snapshot_directory=snapshot_directory,
        snapshot_manifest=snapshot_manifest,
        feature_publication_directory=feature_publication_directory,
        feature_publication=feature_publication,
        normalizer_artifact=normalizer_artifact,
        lockfile_bytes=lockfile_bytes,
    )
    trial_config = _trial_config(config, discovery, development, registry)
    try:
        return _run_discovery_implementation(
            config=config,
            discovery=discovery,
            development=development,
            registry=registry,
            event_ids=event_ids,
            durations_seconds=durations_seconds,
            output_root=output_root,
            normalizer=normalizer,
            feature_publication=feature_publication,
        )
    except Exception as error:
        try:
            save_experiment_result(
                config=trial_config,
                status=TerminalStatus.FAILED,
                metrics={},
                conclusion=f"Trial execution raised {type(error).__name__}.",
                warnings=("discovery algorithm did not produce normal output",),
                started_at=_required_timestamp(config.started_at, "started_at"),
                completed_at=_required_timestamp(config.completed_at, "completed_at"),
                root=output_root,
            )
        except Exception as publication_error:
            error.add_note(
                "terminal discovery receipt publication also failed: "
                f"{type(publication_error).__name__}"
            )
        raise


def _run_discovery_implementation(
    *,
    config: DiscoveryRunConfig,
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    registry: FeatureRegistry,
    event_ids: Sequence[str],
    durations_seconds: Sequence[float],
    output_root: Path,
    normalizer: RobustNormalizer | None,
    feature_publication: DerivedPublicationManifest | None,
) -> DiscoveryRunManifest:
    """Compute and publish one discovery bundle after the attempt boundary is established."""

    _validate_run_inputs(config, discovery, development, registry, output_root)
    stage_dir = output_root / f".{config.run_id}.staging"
    if stage_dir.exists():
        raise RuntimeError(f"stale discovery staging directory exists: {stage_dir}")

    matrix_discovery = discovery
    matrix_development = development
    if normalizer is not None:
        if feature_publication is None:
            raise RuntimeError("verified feature publication is required for normalization")
        matrix_discovery = make_discovery_input(
            partition=discovery.partition,
            rows=(normalizer.transform_row(row) for row in discovery.rows),
            registry=registry,
            purpose="fit",
            max_rows=config.max_rows,
            publication_manifest=feature_publication,
        )
        matrix_development = make_discovery_input(
            partition=development.partition,
            rows=(normalizer.transform_row(row) for row in development.rows),
            registry=registry,
            purpose="stability",
            max_rows=config.max_rows,
            publication_manifest=feature_publication,
        )
    discovery_matrix = build_feature_matrix(
        matrix_discovery, registry, config.feature_names, config.max_rows
    )
    development_matrix = build_feature_matrix(
        matrix_development, registry, config.feature_names, config.max_rows
    )
    selected_discovery = _selected_rows(matrix_discovery, discovery_matrix)
    selected_development = _selected_rows(matrix_development, development_matrix)
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
        asset_universe=config.split.development.symbols,
        periods=periods,
        period_order=period_order,
        seeds=config.seeds,
        subsample_fraction=_SUBSAMPLE_FRACTION,
        policy=config.stability_policy,
        adjacent_period_policy=config.adjacent_period_stability_policy,
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
    metrics_payload = {
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
    payloads: dict[str, bytes] = {
        "config.json": _json_file(config_payload),
        "projection.json": _json_file(projection),
        "clustering.json": _json_file(clustering),
        "stability.json": _json_file(stability),
        "behaviours.json": _json_file(behaviours),
        "motifs.json": _json_file(motif_payload),
        "transitions.json": _json_file(transition_matrix),
        "metrics.json": _json_file(metrics_payload),
        "summary.md": (
            f"# {config.run_id}\n\nStatus: {status}\n\n"
            "Outcome-blind discovery; final holdout was not accessed.\n"
        ).encode("utf-8"),
    }
    artifact_hashes = tuple(sorted((name, _sha256(content)) for name, content in payloads.items()))
    config_sha256 = _sha256(_canonical_json(config_payload))
    manifest_without_hash = {
        "schema_version": "discovery-run-manifest-v2",
        "run_id": config.run_id,
        "status": status,
        "identity_sha256": identity_sha256,
        "config_sha256": config_sha256,
        "artifact_sha256": dict(artifact_hashes),
        "behaviour_ids": [item.behaviour_id for item in behaviours],
        "transition_matrix": _jsonable(transition_matrix),
    }
    manifest = DiscoveryRunManifest(
        run_id=config.run_id,
        status=status,
        identity_sha256=identity_sha256,
        config_sha256=config_sha256,
        artifact_sha256=artifact_hashes,
        behaviours=behaviours,
        transition_matrix=transition_matrix,
        manifest_sha256=_sha256(_canonical_json(manifest_without_hash)),
    )
    manifest_bytes = _json_file(manifest.to_dict())
    payloads["manifest.json"] = manifest_bytes
    save_experiment_result(
        config=_trial_config(config, discovery, development, registry),
        status=(TerminalStatus.COMPLETED if status == "completed" else TerminalStatus.REJECTED),
        metrics=metrics_payload,
        conclusion=(
            "Outcome-blind discovery completed; final holdout was not accessed."
            if status == "completed"
            else "Outcome-blind detector was rejected by the frozen stability policy."
        ),
        warnings=(
            () if status == "completed" else ("frozen stability policy rejected the detector",)
        ),
        started_at=_required_timestamp(config.started_at, "started_at"),
        completed_at=_required_timestamp(config.completed_at, "completed_at"),
        artifacts={name: content for name, content in payloads.items() if name != "metrics.json"},
        root=output_root,
    )
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
        if pack.transition_matrix != run_manifest.transition_matrix:
            raise ValueError("evidence transition evidence changed")
        detector_fields = tuple(item.feature_name for item in pack.behaviour.feature_distributions)
        if record.detector_fields != detector_fields:
            raise ValueError("AI interpretation detector fields changed")

    _verify_run_manifest_identity(run_dir, run_manifest)
    if (run_dir / "receipt.json").is_file():
        interpretation_root = output_root.parent / f"{output_root.name}-interpretations"
        stage_dir = interpretation_root / f".{run_manifest.run_id}.staging"
        final_dir = interpretation_root / run_manifest.run_id
    else:
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
        "schema_version": "interpretation-manifest-v2",
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
    if path_exists_no_follow(final_dir):
        existing = _verify_bundle(final_dir, exact=True)
        if existing.get("identity_sha256") != identity_sha256:
            raise RuntimeError("interpretation identity conflict")
        if not regular_file_matches(final_dir / "manifest.json", manifest_bytes):
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
    discovery_identity = _feature_row_identity(discovery)
    development_identity = _feature_row_identity(development)
    for field in _ROW_IDENTITY_FIELDS:
        if discovery_identity[field] != development_identity[field]:
            raise ValueError(f"feature row {field} changed between discovery and development")
    expected_identity = {
        "dataset_version": config.dataset_snapshot_id,
        "config_version": config.config_version,
        "feature_set_id": config.feature_set_id,
        "registry_id": registry.registry_id,
    }
    for field, expected in expected_identity.items():
        if discovery_identity[field] != expected:
            raise ValueError(f"feature row {field} does not match discovery config")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a Path")


def _verify_artifact_provenance(
    *,
    config: DiscoveryRunConfig,
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    registry: FeatureRegistry,
    snapshot_directory: Path | None,
    snapshot_manifest: SnapshotManifest | None,
    feature_publication_directory: Path | None,
    feature_publication: DerivedPublicationManifest | None,
    normalizer_artifact: bytes | None,
    lockfile_bytes: bytes | None,
) -> RobustNormalizer:
    """Verify every concrete artifact before any discovery matrix is constructed."""

    if not isinstance(snapshot_directory, Path):
        raise TypeError("snapshot directory is required for provenance verification")
    if not isinstance(snapshot_manifest, SnapshotManifest):
        raise TypeError("snapshot manifest is required for provenance verification")
    if not isinstance(feature_publication_directory, Path):
        raise TypeError("feature publication directory is required for provenance verification")
    if not isinstance(feature_publication, DerivedPublicationManifest):
        raise TypeError("feature publication manifest is required for provenance verification")
    if not isinstance(normalizer_artifact, bytes):
        raise TypeError("normalizer artifact bytes are required for provenance verification")
    if not isinstance(lockfile_bytes, bytes):
        raise TypeError("lockfile bytes are required for provenance verification")
    require_regular_directory(snapshot_directory)
    require_regular_directory(feature_publication_directory)
    _require_manifest_bytes(
        snapshot_directory / "manifest.json",
        snapshot_manifest.to_json().encode("utf-8"),
        "snapshot",
    )
    _require_manifest_bytes(
        feature_publication_directory / "manifest.json",
        feature_publication.to_json().encode("utf-8"),
        "feature publication",
    )
    verify_snapshot(snapshot_directory, snapshot_manifest)
    discovery_records = verify_published_feature_rows(
        feature_publication_directory,
        feature_publication,
        discovery.rows,
        registry,
    )
    development_records = verify_published_feature_rows(
        feature_publication_directory,
        feature_publication,
        development.rows,
        registry,
    )
    if discovery_records != discovery.partition_records or (
        development_records != development.partition_records
    ):
        raise ValueError("verified feature publication partition hashes changed")
    verified = freeze_discovery_provenance(
        snapshot_manifest=snapshot_manifest,
        feature_publication=feature_publication,
        registry=registry,
        normalizer_artifact=normalizer_artifact,
        split=config.split,
        feature_names=config.feature_names,
        discovery=discovery,
        development=development,
        code_commit=config.code_commit,
        lockfile_bytes=lockfile_bytes,
    )
    if verified != config.provenance:
        raise ValueError("supplied artifacts do not match verified discovery provenance")
    return RobustNormalizer.from_json(normalizer_artifact)


def _verify_runtime_code_identity(
    *,
    code_commit: str,
    lockfile_bytes: bytes | None,
) -> None:
    """Bind identities to the clean Git checkout containing this executing module."""

    if not isinstance(lockfile_bytes, bytes):
        raise TypeError("lockfile bytes are required for runtime code provenance")
    module_path = Path(__file__).resolve()
    repository_root = Path(
        _git_output(module_path.parent, "rev-parse", "--show-toplevel")
    ).resolve()
    try:
        module_relative = module_path.relative_to(repository_root).as_posix()
    except ValueError as error:
        raise ValueError("runtime Git top-level does not contain the executing module") from error
    try:
        read_bounded_regular(module_path, 16 * 1024 * 1024)
        tracked = _git_output(
            repository_root,
            "ls-files",
            "--error-unmatch",
            "--",
            module_relative,
        )
        _git_output(repository_root, "cat-file", "-e", f"HEAD:{module_relative}")
        _git_output(repository_root, "diff", "--quiet", "HEAD", "--", module_relative)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(
            "executing module must be a clean tracked file present in runtime Git HEAD"
        ) from error
    if tracked != module_relative:
        raise ValueError("executing module Git tracking identity is ambiguous")
    head = _git_output(repository_root, "rev-parse", "HEAD")
    if head != code_commit:
        raise ValueError("declared code commit does not match runtime Git HEAD")
    status = _git_output(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise ValueError("runtime Git worktree is dirty")
    lock_path = repository_root / "uv.lock"
    try:
        actual_lockfile = read_bounded_regular(lock_path, 64 * 1024 * 1024)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("runtime uv.lock is absent or invalid") from error
    if actual_lockfile != lockfile_bytes:
        raise ValueError("supplied lockfile bytes do not match runtime uv.lock")

def _git_output(repository_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository_root), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("runtime Git identity is unavailable") from error
    if completed.returncode != 0:
        raise ValueError("runtime Git identity is unavailable")
    return completed.stdout.strip()


def _require_manifest_bytes(path: Path, expected: bytes, label: str) -> None:
    try:
        matches = regular_file_matches(path, expected)
    except (OSError, RuntimeError):
        matches = False
    if not matches:
        raise ValueError(f"{label} manifest bytes do not match the supplied manifest")


def _feature_row_identity(input_value: DiscoveryInput) -> dict[str, str]:
    first = input_value.rows[0]
    identity = {field: getattr(first, field) for field in _ROW_IDENTITY_FIELDS}
    for row in input_value.rows[1:]:
        for field, expected in identity.items():
            if getattr(row, field) != expected:
                raise ValueError(f"feature row {field} changed within discovery input")
    return identity


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
    payload: dict[str, object] = {
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
        "adjacent_period_stability_policy": _jsonable(config.adjacent_period_stability_policy),
        "code_commit": config.code_commit,
        "lock_sha256": config.lock_sha256,
        "parent_run_ids": list(config.parent_run_ids),
        "orchestration_parameters": {
            "subsample_fraction": _SUBSAMPLE_FRACTION,
            "stability_algorithm_version": STABILITY_ALGORITHM_VERSION,
            "motif_max_window_length": _MOTIF_MAX_WINDOW_LENGTH,
            "motif_exclusion_zone": _MOTIF_EXCLUSION_ZONE,
            "motif_top_k": _MOTIF_TOP_K,
            "transition_horizon": 1,
            "transition_bootstrap_iterations": _TRANSITION_BOOTSTRAP_ITERATIONS,
            "transition_block_length": _TRANSITION_BLOCK_LENGTH,
            "transition_confidence_level": _TRANSITION_CONFIDENCE_LEVEL,
        },
    }
    payload.update(
        {
            "feature_publication_id": config.feature_publication_id,
            "feature_publication_sha256": config.feature_publication_sha256,
            "normalizer_id": config.normalizer_id,
            "normalizer_sha256": config.normalizer_sha256,
            "provenance": _jsonable(config.provenance),
            "started_at": _jsonable(config.started_at),
            "completed_at": _jsonable(config.completed_at),
        }
    )
    return payload


def _trial_config(
    config: DiscoveryRunConfig,
    discovery: DiscoveryInput,
    development: DiscoveryInput,
    registry: FeatureRegistry,
) -> ExperimentConfig:
    feature_publication_id = _require_text(config.feature_publication_id, "feature_publication_id")
    feature_publication_sha256 = _require_text(
        config.feature_publication_sha256, "feature_publication_sha256"
    )
    normalizer_id = _require_text(config.normalizer_id, "normalizer_id")
    normalizer_sha256 = _require_text(config.normalizer_sha256, "normalizer_sha256")
    rows = discovery.rows + development.rows
    symbols = config.split.development.symbols
    timeframes = tuple(sorted({row.timeframe for row in rows}))
    partitions = (config.split.discovery, config.split.development, config.split.holdout)
    ranges = tuple(
        sorted(
            (
                TrialRange(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=_utc_string(partition.start),
                    end=_utc_string(partition.end),
                )
                for partition in partitions
                for symbol in partition.symbols
                for timeframe in timeframes
            ),
            key=lambda item: (item.symbol, item.timeframe, item.start, item.end),
        )
    )
    return ExperimentConfig(
        run_id=config.run_id,
        mode=ExperimentMode.DISCOVERY,
        dataset_snapshot=ArtifactIdentity(
            config.dataset_snapshot_id, config.dataset_snapshot_sha256
        ),
        feature_publication=ArtifactIdentity(feature_publication_id, feature_publication_sha256),
        feature_registry=ArtifactIdentity(registry.registry_id, registry.sha256),
        normalizer=ArtifactIdentity(normalizer_id, normalizer_sha256),
        frozen_split={
            "split_id": config.split.split_id,
            "sha256": config.split.sha256,
            "discovery": config.split.discovery.to_dict(),
            "development": config.split.development.to_dict(),
            "holdout": config.split.holdout.to_dict(),
            "asset_holdouts": list(config.split.asset_holdouts),
        },
        detector_version="outcome-blind-discovery-v2",
        candidate_id=None,
        candidate_version=None,
        code_commit=config.code_commit,
        lock_sha256=config.lock_sha256,
        canonical_config=_config_payload(config),
        seed=config.seeds[0],
        symbols=symbols,
        timeframes=timeframes,
        ranges=ranges,
        parent_ids=config.parent_run_ids,
        metrics_schema={
            "behaviours": "integer",
            "discovery_rows": "integer",
            "development_rows": "integer",
            "dropped_null_rows": "object",
            "motifs": "integer",
            "status": "string",
            "transitions": "integer",
        },
        hypothesis=None,
    )


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


def _verify_bundle(directory: Path, *, exact: bool = False) -> Mapping[str, object]:
    actual_files = set(bounded_regular_files(directory, maximum=_MAX_BUNDLE_ENTRIES))
    has_receipt = "receipt.json" in actual_files
    if has_receipt:
        verify_trial_receipt(directory)
    manifest_path = directory / "manifest.json"
    if "manifest.json" not in actual_files:
        raise RuntimeError("published bundle is missing its manifest")
    try:
        manifest = json.loads(read_bounded_regular(manifest_path, _MAX_MANIFEST_BYTES).decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("published bundle manifest is tampered") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("published bundle manifest is tampered")
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise RuntimeError("published bundle manifest is tampered")
    artifact_names = {_safe_bundle_name(name) for name in hashes}
    if has_receipt or exact:
        expected_files = {"manifest.json", *artifact_names}
        if has_receipt:
            expected_files.add("receipt.json")
        if actual_files != expected_files:
            raise RuntimeError("published bundle contains unexpected files")
    for name, expected in hashes.items():
        safe_name = _safe_bundle_name(name)
        path = directory / PurePosixPath(safe_name)
        if (
            not isinstance(expected, str)
            or safe_name not in actual_files
            or sha256_regular(path) != expected
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


def _verify_run_manifest_identity(
    directory: Path,
    supplied: DiscoveryRunManifest,
) -> None:
    verified = _verify_bundle(directory)
    supplied_payload = supplied.to_dict()
    if verified != supplied_payload:
        raise RuntimeError("supplied discovery run manifest identity does not match publication")
    if not regular_file_matches(directory / "manifest.json", _json_file(supplied_payload)):
        raise RuntimeError("supplied discovery run manifest identity is not byte-identical")
    if not regular_file_matches(directory / "behaviours.json", _json_file(supplied.behaviours)):
        raise RuntimeError("supplied discovery run behaviour identity does not match publication")


def _safe_bundle_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeError("published bundle manifest contains an unsafe artifact path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("published bundle manifest contains an unsafe artifact path")
    return path.as_posix()


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


def _require_utc_datetime(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
        or value.microsecond != 0
    ):
        raise ValueError(f"{label} must be a whole-second UTC datetime")
    return value


def _required_timestamp(value: datetime | None, label: str) -> datetime:
    return _require_utc_datetime(value, label)


def _utc_string(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value
