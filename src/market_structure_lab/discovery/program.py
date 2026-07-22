"""Frozen, outcome-blind orchestration contracts for real discovery programmes."""

from __future__ import annotations

import json
import re
import os
from dataclasses import InitVar, asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from itertools import product
from pathlib import Path
from typing import Sequence

import polars as pl

from market_structure_lab.auction import AuctionCandle, AuctionEngine, GapPolicy, RollingBars
from market_structure_lab.core.artifact_io import sha256_regular
from market_structure_lab.data.derived import (
    DerivedPublicationIdentity,
    DerivedPublicationManifest,
    LeakageAuditApproval,
    publish_feature_rows,
    publish_market_events,
)
from market_structure_lab.data.export import SnapshotResearchBinding
from market_structure_lab.data.export import SnapshotManifest, verify_snapshot
from market_structure_lab.data.reconciliation.receipts import (
    read_reconciliation_promotion_receipt,
)
from market_structure_lab.data.segments import SegmentBoundary, segment_boundaries_sha256
from market_structure_lab.discovery.splits import FrozenDiscoverySplit
from market_structure_lab.events import segment_fixed_windows
from market_structure_lab.experiments import TrialManifest, read_trial_ledger
from market_structure_lab.features import FeatureBuilder
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.normalization import (
    PartitionRole as NormalizerPartitionRole,
    RobustNormalizer,
    TrainingPartition,
    fit_robust_normalizer,
)
from market_structure_lab.features.registry import FeatureRegistry
from market_structure_lab.profiles import FixedStepBins, UniformAllocation

_SELECTION_ID = re.compile(r"^SU-[0-9]{6}$")
_PREREGISTRATION_ID = re.compile(r"^PG-[0-9]{6}$")
_RUN_ID = re.compile(r"^DR-[0-9]{6}$")
_DATASET_ID = re.compile(r"^DS-[0-9]{6}$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_SAFE_POLICY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_SELECTED_UNIVERSE_FACTORY = object()
_MAX_PROGRAM_TRIALS = 10_000
_MAX_PROGRAM_STABILITY_FITS = 100_000
_MAX_PROGRAM_EVIDENCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SelectedUniverse:
    """Exact materialized market scope bound to the promoted canonical view."""

    selection_id: str
    symbols: tuple[str, ...]
    timeframe: str
    start: datetime
    end: datetime
    survivorship_policy: str
    optional_field_policy: str
    zero_volume_policy: str
    gap_boundaries: tuple[SegmentBoundary, ...]
    gap_boundaries_sha256: str
    promotion_run_id: str
    promotion_manifest_sha256: str
    promotion_replacement_logical_sha256: str
    promotion_canonical_logical_sha256: str
    promotion_coverage_logical_sha256: str
    promotion_receipt_content_sha256: str
    promotion_receipt_artifact_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SELECTED_UNIVERSE_FACTORY:
            raise TypeError("SelectedUniverse must be created by freeze_selected_universe")
        if _SELECTION_ID.fullmatch(self.selection_id) is None:
            raise ValueError("selection_id must match SU-######")
        if (
            not self.symbols
            or self.symbols != tuple(sorted(set(self.symbols)))
            or any(not symbol or symbol != symbol.upper() for symbol in self.symbols)
        ):
            raise ValueError("symbols must use canonical deterministic ordering")
        if self.timeframe != "1m":
            raise ValueError("selected universe currently supports the canonical 1m timeframe")
        start = _require_utc(self.start, "start")
        end = _require_utc(self.end, "end")
        if start >= end:
            raise ValueError("selected universe start must precede end")
        for value, field in (
            (self.survivorship_policy, "survivorship_policy"),
            (self.optional_field_policy, "optional_field_policy"),
            (self.zero_volume_policy, "zero_volume_policy"),
        ):
            if not isinstance(value, str) or _SAFE_POLICY.fullmatch(value) is None:
                raise ValueError(f"{field} must be a safe frozen policy identifier")
        boundaries = tuple(
            sorted(
                self.gap_boundaries,
                key=lambda item: (item.symbol, item.timeframe, item.start, item.end, item.reason),
            )
        )
        if boundaries != self.gap_boundaries or len(boundaries) != len(set(boundaries)):
            raise ValueError("gap_boundaries must use canonical deterministic ordering")
        for boundary in boundaries:
            if (
                boundary.symbol not in self.symbols
                or boundary.timeframe != self.timeframe
                or boundary.start < start
                or boundary.end > end
            ):
                raise ValueError("gap boundary is outside the selected universe")
        expected_boundaries = segment_boundaries_sha256(boundaries)
        if self.gap_boundaries_sha256 != expected_boundaries:
            raise ValueError("gap boundary checksum does not match the selected universe")
        for value, field in (
            (self.promotion_manifest_sha256, "promotion_manifest_sha256"),
            (
                self.promotion_replacement_logical_sha256,
                "promotion_replacement_logical_sha256",
            ),
            (self.promotion_canonical_logical_sha256, "promotion_canonical_logical_sha256"),
            (self.promotion_coverage_logical_sha256, "promotion_coverage_logical_sha256"),
            (self.promotion_receipt_content_sha256, "promotion_receipt_content_sha256"),
            (self.promotion_receipt_artifact_sha256, "promotion_receipt_artifact_sha256"),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "selected-universe-v1",
            "selection_id": self.selection_id,
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "start": _iso_utc(self.start),
            "end": _iso_utc(self.end),
            "survivorship_policy": self.survivorship_policy,
            "optional_field_policy": self.optional_field_policy,
            "zero_volume_policy": self.zero_volume_policy,
            "gap_boundaries": _boundary_payload(self.gap_boundaries),
            "gap_boundaries_sha256": self.gap_boundaries_sha256,
            "promotion": {
                "run_id": self.promotion_run_id,
                "manifest_sha256": self.promotion_manifest_sha256,
                "replacement_logical_sha256": self.promotion_replacement_logical_sha256,
                "canonical_logical_sha256": self.promotion_canonical_logical_sha256,
                "coverage_logical_sha256": self.promotion_coverage_logical_sha256,
                "receipt_content_sha256": self.promotion_receipt_content_sha256,
                "receipt_artifact_sha256": self.promotion_receipt_artifact_sha256,
            },
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))

    @property
    def snapshot_research_binding(self) -> SnapshotResearchBinding:
        return SnapshotResearchBinding(
            selected_universe_sha256=self.sha256,
            promotion_receipt_content_sha256=self.promotion_receipt_content_sha256,
            promotion_receipt_artifact_sha256=self.promotion_receipt_artifact_sha256,
            promotion_canonical_logical_sha256=self.promotion_canonical_logical_sha256,
            gap_boundaries_sha256=self.gap_boundaries_sha256,
        )


@dataclass(frozen=True, slots=True)
class DiscoveryProgramBudget:
    """Frozen aggregate admission limits across a preregistered run grid."""

    budget_id: str
    maximum_trials: int
    maximum_total_stability_fits: int
    maximum_serialized_evidence_bytes: int

    def __post_init__(self) -> None:
        _require_safe_id(self.budget_id, "budget_id")
        for field, hard_limit in (
            ("maximum_trials", _MAX_PROGRAM_TRIALS),
            ("maximum_total_stability_fits", _MAX_PROGRAM_STABILITY_FITS),
            ("maximum_serialized_evidence_bytes", _MAX_PROGRAM_EVIDENCE_BYTES),
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
            if value > hard_limit:
                raise ValueError(f"{field} exceeds its conservative safety maximum")

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_id": self.budget_id,
            "maximum_trials": self.maximum_trials,
            "maximum_total_stability_fits": self.maximum_total_stability_fits,
            "maximum_serialized_evidence_bytes": self.maximum_serialized_evidence_bytes,
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class PreregisteredTrial:
    """One exact outcome-blind detector configuration in the frozen grid."""

    run_id: str
    pca_components: int
    clusters: int
    seeds: tuple[int, ...]

    def __post_init__(self) -> None:
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("run_id must match DR-######")
        for value, field in (
            (self.pca_components, "pca_components"),
            (self.clusters, "clusters"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if (
            not self.seeds
            or len(self.seeds) != len(set(self.seeds))
            or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.seeds)
        ):
            raise ValueError("seeds must be a unique non-empty integer tuple")

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "pca_components": self.pca_components,
            "clusters": self.clusters,
            "seeds": list(self.seeds),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryPreregistration:
    """Canonical Task 14 programme frozen before any detector attempt."""

    preregistration_id: str
    selected_universe_sha256: str
    split: FrozenDiscoverySplit
    dataset_snapshot_id: str
    feature_set_id: str
    registry_sha256: str
    normalizer_policy_id: str
    feature_names: tuple[str, ...]
    trials: tuple[PreregisteredTrial, ...]
    budget: DiscoveryProgramBudget
    stability_policy_sha256: str
    motif_policy_sha256: str
    transition_policy_sha256: str
    missingness_policy_sha256: str
    regime_contract_sha256: str
    negative_controls: tuple[str, ...]
    naive_baselines: tuple[str, ...]
    rejection_rules: tuple[str, ...]
    survivorship_policy: str
    optional_field_policy: str
    zero_volume_policy: str
    code_commit: str
    lock_sha256: str

    def __post_init__(self) -> None:
        if _PREREGISTRATION_ID.fullmatch(self.preregistration_id) is None:
            raise ValueError("preregistration_id must match PG-######")
        if not isinstance(self.split, FrozenDiscoverySplit):
            raise TypeError("split must be a FrozenDiscoverySplit")
        if _DATASET_ID.fullmatch(self.dataset_snapshot_id) is None:
            raise ValueError("dataset_snapshot_id must match DS-######")
        if _FEATURE_SET_ID.fullmatch(self.feature_set_id) is None:
            raise ValueError("feature_set_id must match FS-######")
        for value, field in (
            (self.selected_universe_sha256, "selected_universe_sha256"),
            (self.registry_sha256, "registry_sha256"),
            (self.stability_policy_sha256, "stability_policy_sha256"),
            (self.motif_policy_sha256, "motif_policy_sha256"),
            (self.transition_policy_sha256, "transition_policy_sha256"),
            (self.missingness_policy_sha256, "missingness_policy_sha256"),
            (self.regime_contract_sha256, "regime_contract_sha256"),
            (self.lock_sha256, "lock_sha256"),
        ):
            _require_sha256(value, field)
        _require_safe_id(self.normalizer_policy_id, "normalizer_policy_id")
        _require_ordered_unique(self.feature_names, "feature_names", canonical=False)
        _require_ordered_unique(self.negative_controls, "negative_controls")
        _require_ordered_unique(self.naive_baselines, "naive_baselines")
        _require_ordered_unique(self.rejection_rules, "rejection_rules")
        for value, field in (
            (self.survivorship_policy, "survivorship_policy"),
            (self.optional_field_policy, "optional_field_policy"),
            (self.zero_volume_policy, "zero_volume_policy"),
        ):
            _require_safe_id(value, field)
        if _COMMIT.fullmatch(self.code_commit) is None:
            raise ValueError("code_commit must be a committed hexadecimal identity")
        if not isinstance(self.budget, DiscoveryProgramBudget):
            raise TypeError("budget must be a DiscoveryProgramBudget")
        if (
            not self.trials
            or tuple(sorted(self.trials, key=lambda item: item.run_id)) != self.trials
            or len({item.run_id for item in self.trials}) != len(self.trials)
        ):
            raise ValueError("trials must use unique canonical run ordering")
        if len(self.trials) > self.budget.maximum_trials:
            raise ValueError("trial grid exceeds the frozen trial budget")
        stability_fits = sum(len(item.seeds) * 2 + 2 for item in self.trials)
        if stability_fits > self.budget.maximum_total_stability_fits:
            raise ValueError("trial grid exceeds the frozen stability-fit budget")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "discovery-preregistration-v1",
            "preregistration_id": self.preregistration_id,
            "selected_universe_sha256": self.selected_universe_sha256,
            "split_sha256": self.split.sha256,
            "discovery_metadata": self.split.discovery.to_dict(),
            "development_metadata": self.split.development.to_dict(),
            "holdout_metadata": {
                **self.split.holdout.to_dict(),
                "asset_holdouts": list(self.split.asset_holdouts),
            },
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "feature_set_id": self.feature_set_id,
            "registry_sha256": self.registry_sha256,
            "normalizer_policy_id": self.normalizer_policy_id,
            "feature_names": list(self.feature_names),
            "trials": [item.to_dict() for item in self.trials],
            "trial_count": len(self.trials),
            "budget": self.budget.to_dict(),
            "stability_policy_sha256": self.stability_policy_sha256,
            "motif_policy_sha256": self.motif_policy_sha256,
            "transition_policy_sha256": self.transition_policy_sha256,
            "missingness_policy_sha256": self.missingness_policy_sha256,
            "regime_contract_sha256": self.regime_contract_sha256,
            "negative_controls": list(self.negative_controls),
            "naive_baselines": list(self.naive_baselines),
            "rejection_rules": list(self.rejection_rules),
            "survivorship_policy": self.survivorship_policy,
            "optional_field_policy": self.optional_field_policy,
            "zero_volume_policy": self.zero_volume_policy,
            "code_commit": self.code_commit,
            "lock_sha256": self.lock_sha256,
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class Phase3PublicationBundle:
    """Verified feature, normalizer, and event artifacts for one preregistration."""

    feature_directory: Path
    feature_manifest: DerivedPublicationManifest
    event_directory: Path
    event_manifest: DerivedPublicationManifest
    normalizer_path: Path
    normalizer: RobustNormalizer
    rows: tuple[FeatureRow, ...]


@dataclass(frozen=True, slots=True)
class TrialReliabilityRecord:
    run_id: str
    status: str
    identity_sha256: str
    receipt_sha256: str
    artifact_sha256: tuple[tuple[str, str], ...]
    conclusion: str
    warnings: tuple[str, ...]

    @classmethod
    def from_manifest(cls, manifest: TrialManifest) -> TrialReliabilityRecord:
        return cls(
            run_id=manifest.run_id,
            status=manifest.status.value,
            identity_sha256=manifest.identity_sha256,
            receipt_sha256=manifest.receipt_sha256,
            artifact_sha256=manifest.artifact_sha256,
            conclusion=manifest.conclusion,
            warnings=manifest.warnings,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "identity_sha256": self.identity_sha256,
            "receipt_sha256": self.receipt_sha256,
            "artifact_sha256": dict(self.artifact_sha256),
            "conclusion": self.conclusion,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryReliabilityVector:
    preregistration_sha256: str
    attempted_trial_count: int
    status_counts: tuple[tuple[str, int], ...]
    conclusion: str
    trials: tuple[TrialReliabilityRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "discovery-reliability-vector-v1",
            "preregistration_sha256": self.preregistration_sha256,
            "attempted_trial_count": self.attempted_trial_count,
            "status_counts": dict(self.status_counts),
            "conclusion": self.conclusion,
            "trials": [item.to_dict() for item in self.trials],
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))


def build_preregistered_trial_grid(
    *,
    run_ids: Sequence[str],
    pca_components: Sequence[int],
    cluster_counts: Sequence[int],
    seed_sets: Sequence[tuple[int, ...]],
    budget: DiscoveryProgramBudget,
) -> tuple[PreregisteredTrial, ...]:
    """Build the exact deterministic Cartesian detector grid before execution."""
    if not isinstance(budget, DiscoveryProgramBudget):
        raise TypeError("budget must be a DiscoveryProgramBudget")
    combinations = tuple(product(pca_components, cluster_counts, seed_sets))
    if len(combinations) > budget.maximum_trials:
        raise ValueError("trial grid exceeds the frozen trial budget")
    canonical_run_ids = tuple(run_ids)
    if (
        len(canonical_run_ids) != len(combinations)
        or tuple(sorted(set(canonical_run_ids))) != canonical_run_ids
    ):
        raise ValueError("run_ids must exactly cover the canonical trial grid")
    trials = tuple(
        PreregisteredTrial(
            run_id=run_id,
            pca_components=int(components),
            clusters=int(clusters),
            seeds=tuple(seeds),
        )
        for run_id, (components, clusters, seeds) in zip(
            canonical_run_ids, combinations, strict=True
        )
    )
    stability_fits = sum(len(item.seeds) * 2 + 2 for item in trials)
    if stability_fits > budget.maximum_total_stability_fits:
        raise ValueError("trial grid exceeds the frozen stability-fit budget")
    return trials


def freeze_discovery_preregistration(
    *,
    preregistration_id: str,
    selected_universe: SelectedUniverse,
    split: FrozenDiscoverySplit,
    dataset_snapshot_id: str,
    feature_set_id: str,
    registry_sha256: str,
    normalizer_policy_id: str,
    feature_names: tuple[str, ...],
    trials: tuple[PreregisteredTrial, ...],
    budget: DiscoveryProgramBudget,
    stability_policy_sha256: str,
    motif_policy_sha256: str,
    transition_policy_sha256: str,
    missingness_policy_sha256: str,
    regime_contract_sha256: str,
    negative_controls: tuple[str, ...],
    naive_baselines: tuple[str, ...],
    rejection_rules: tuple[str, ...],
    code_commit: str,
    lock_sha256: str,
) -> DiscoveryPreregistration:
    """Freeze metadata-only holdout boundaries and the complete search policy."""
    if not isinstance(selected_universe, SelectedUniverse):
        raise TypeError("selected_universe must be factory verified")
    if not isinstance(split, FrozenDiscoverySplit):
        raise TypeError("split must be a FrozenDiscoverySplit")
    if (
        split.discovery.symbols != selected_universe.symbols
        or split.development.symbols != selected_universe.symbols
        or split.discovery.start != selected_universe.start
        or split.development.end != selected_universe.end
    ):
        raise ValueError("selected universe must exactly cover discovery and development metadata")
    return DiscoveryPreregistration(
        preregistration_id=preregistration_id,
        selected_universe_sha256=selected_universe.sha256,
        split=split,
        dataset_snapshot_id=dataset_snapshot_id,
        feature_set_id=feature_set_id,
        registry_sha256=registry_sha256,
        normalizer_policy_id=normalizer_policy_id,
        feature_names=feature_names,
        trials=trials,
        budget=budget,
        stability_policy_sha256=stability_policy_sha256,
        motif_policy_sha256=motif_policy_sha256,
        transition_policy_sha256=transition_policy_sha256,
        missingness_policy_sha256=missingness_policy_sha256,
        regime_contract_sha256=regime_contract_sha256,
        negative_controls=tuple(sorted(negative_controls)),
        naive_baselines=tuple(sorted(naive_baselines)),
        rejection_rules=tuple(sorted(rejection_rules)),
        survivorship_policy=selected_universe.survivorship_policy,
        optional_field_policy=selected_universe.optional_field_policy,
        zero_volume_policy=selected_universe.zero_volume_policy,
        code_commit=code_commit,
        lock_sha256=lock_sha256,
    )


def build_phase3_publications(
    *,
    preregistration: DiscoveryPreregistration,
    selected_universe: SelectedUniverse,
    snapshot_directory: Path,
    snapshot_manifest: SnapshotManifest,
    output_root: Path,
    work_root: Path,
    registry: FeatureRegistry,
    leakage_approval: LeakageAuditApproval,
    bin_step: float,
    rolling_bars: int,
    event_width: int,
    maximum_rows: int,
    lockfile_bytes: bytes,
) -> Phase3PublicationBundle:
    """Build the bounded deterministic Phase 3 chain without holdout row access."""
    if not isinstance(preregistration, DiscoveryPreregistration):
        raise TypeError("preregistration must be a DiscoveryPreregistration")
    if not isinstance(selected_universe, SelectedUniverse):
        raise TypeError("selected_universe must be factory verified")
    if preregistration.selected_universe_sha256 != selected_universe.sha256:
        raise ValueError("preregistration selected-universe identity mismatch")
    if not isinstance(snapshot_manifest, SnapshotManifest):
        raise TypeError("snapshot_manifest must be a SnapshotManifest")
    if snapshot_manifest.identity.dataset_version != preregistration.dataset_snapshot_id:
        raise ValueError("snapshot dataset identity does not match preregistration")
    if snapshot_manifest.identity.code_commit != preregistration.code_commit:
        raise ValueError("snapshot code identity does not match preregistration")
    if snapshot_manifest.identity.research_binding != selected_universe.snapshot_research_binding:
        raise ValueError("snapshot research provenance does not match selected universe")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    if (
        registry.feature_set_id != preregistration.feature_set_id
        or registry.sha256 != preregistration.registry_sha256
    ):
        raise ValueError("feature registry does not match preregistration")
    if not isinstance(leakage_approval, LeakageAuditApproval):
        raise TypeError("leakage_approval must be a LeakageAuditApproval")
    if leakage_approval.feature_registry_sha256 != registry.sha256:
        raise ValueError("leakage approval does not match the preregistered registry")
    if (
        not isinstance(lockfile_bytes, bytes)
        or _sha256(lockfile_bytes) != preregistration.lock_sha256
    ):
        raise ValueError("lockfile bytes do not match preregistration")
    for value, field in (
        (rolling_bars, "rolling_bars"),
        (event_width, "event_width"),
        (maximum_rows, "maximum_rows"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field} must be a positive integer")
    if not isinstance(bin_step, (int, float)) or isinstance(bin_step, bool) or bin_step <= 0:
        raise ValueError("bin_step must be positive")
    verify_snapshot(snapshot_directory, snapshot_manifest)
    if snapshot_manifest.row_count > maximum_rows:
        raise ValueError("snapshot rows exceed the frozen Phase 3 row budget")
    work_root.mkdir(parents=True, exist_ok=True)
    feature_batch_path = work_root / "feature-build.jsonl"
    snapshots = _iter_auction_snapshots(
        snapshot_directory=snapshot_directory,
        manifest=snapshot_manifest,
        bin_step=float(bin_step),
        rolling_bars=rolling_bars,
    )
    batch = FeatureBuilder(registry).build_batch(
        snapshots,
        artifact_path=feature_batch_path,
        maximum_rows=maximum_rows,
    )
    try:
        rows = tuple(batch.iter_rows())
        if not rows:
            raise ValueError("Phase 3 feature construction produced no rows")
        training_rows = tuple(
            row for row in rows if preregistration.split.discovery.contains(row.information_cutoff)
        )
        partition = TrainingPartition(
            split_id=preregistration.split.split_id,
            start=preregistration.split.discovery.start,
            end=preregistration.split.discovery.end,
            role=NormalizerPartitionRole.TRAINING,
            symbols=preregistration.split.discovery.symbols,
        )
        normalizer = fit_robust_normalizer(
            training_rows,
            registry,
            partition,
            preregistration.dataset_snapshot_id,
            preregistration.feature_names,
        )
        first = rows[0]
        identity = DerivedPublicationIdentity(
            dataset_version=preregistration.dataset_snapshot_id,
            dataset_snapshot_sha256=snapshot_manifest.snapshot_sha256,
            feature_set_id=registry.feature_set_id,
            feature_registry_sha256=registry.sha256,
            config_version=first.config_version,
            profile_version=first.profile_version,
            window_policy_id=first.window_policy_id,
            event_version=f"fixed-window-{event_width}-v1",
            normalizer_artifact_sha256=normalizer.artifact_sha256,
            leakage_audit_approval_sha256=leakage_approval.sha256,
            code_commit=preregistration.code_commit,
            uv_lock_sha256=preregistration.lock_sha256,
        )
        feature_manifest = publish_feature_rows(
            batch,
            output_root=output_root,
            identity=identity,
            registry=registry,
            leakage_audit=leakage_approval,
        )
        feature_directory = (
            output_root
            / f"dataset_version={preregistration.dataset_snapshot_id}"
            / f"feature_set={registry.feature_set_id}"
            / "features"
        )
        events = tuple(
            event
            for group in _rows_by_stream(rows)
            for event in segment_fixed_windows(
                group,
                width=event_width,
                trigger_version=f"fixed-window-{event_width}-v1",
                registry=registry,
            )
        )
        event_manifest = publish_market_events(
            events,
            output_root=output_root,
            identity=identity,
            registry=registry,
            source_feature_directory=feature_directory,
            source_feature_manifest=feature_manifest,
        )
        event_directory = (
            output_root
            / f"dataset_version={preregistration.dataset_snapshot_id}"
            / f"feature_set={registry.feature_set_id}"
            / "events"
        )
        normalizer_path = (
            output_root
            / f"dataset_version={preregistration.dataset_snapshot_id}"
            / f"feature_set={registry.feature_set_id}"
            / "normalizer.json"
        )
        normalizer_path.parent.mkdir(parents=True, exist_ok=True)
        normalizer_bytes = normalizer.canonical_json()
        if normalizer_path.exists():
            if normalizer_path.read_bytes() != normalizer_bytes:
                raise FileExistsError("normalizer artifact conflicts with prior publication")
        else:
            temporary = normalizer_path.with_name(f".{normalizer_path.name}.tmp")
            temporary.write_bytes(normalizer_bytes)
            os.replace(temporary, normalizer_path)
        return Phase3PublicationBundle(
            feature_directory=feature_directory,
            feature_manifest=feature_manifest,
            event_directory=event_directory,
            event_manifest=event_manifest,
            normalizer_path=normalizer_path,
            normalizer=normalizer,
            rows=rows,
        )
    finally:
        batch.close()


def publish_reliability_vector(
    *,
    preregistration: DiscoveryPreregistration,
    trial_root: Path,
    destination: Path,
) -> DiscoveryReliabilityVector:
    """Reconcile every preregistered attempt to one verified terminal receipt."""
    if not isinstance(preregistration, DiscoveryPreregistration):
        raise TypeError("preregistration must be a DiscoveryPreregistration")
    manifests = read_trial_ledger(trial_root)
    expected = tuple(item.run_id for item in preregistration.trials)
    actual = tuple(item.run_id for item in manifests)
    if actual != expected:
        raise ValueError("terminal trial receipts do not exactly cover the preregistered grid")
    for manifest in manifests:
        if manifest.mode.value != "discovery":
            raise ValueError("reliability vector accepts discovery-mode receipts only")
        if manifest.canonical_config.get("preregistration_sha256") != preregistration.sha256:
            raise ValueError("trial receipt is not bound to the preregistration")
    records = tuple(TrialReliabilityRecord.from_manifest(item) for item in manifests)
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    if any(record.status == "completed" for record in records):
        conclusion = "accepted"
    elif records and all(record.status == "rejected" for record in records):
        conclusion = "rejected"
    else:
        conclusion = "inconclusive"
    vector = DiscoveryReliabilityVector(
        preregistration_sha256=preregistration.sha256,
        attempted_trial_count=len(records),
        status_counts=tuple(sorted(counts.items())),
        conclusion=conclusion,
        trials=records,
    )
    payload = {
        **vector.to_dict(),
        "sha256": vector.sha256,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    if len(encoded.encode("utf-8")) > preregistration.budget.maximum_serialized_evidence_bytes:
        raise ValueError("reliability vector exceeds the frozen evidence byte budget")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_text(encoding="utf-8") != encoded:
            raise FileExistsError("reliability vector conflicts with prior publication")
        return vector
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    os.replace(temporary, destination)
    return vector


def _iter_auction_snapshots(
    *,
    snapshot_directory: Path,
    manifest: SnapshotManifest,
    bin_step: float,
    rolling_bars: int,
):
    active_symbol: str | None = None
    engine: AuctionEngine | None = None
    for partition in manifest.partitions:
        path = snapshot_directory / partition.path
        for batch in pl.scan_parquet(path).collect_batches(chunk_size=10_000, maintain_order=True):
            for row in batch.iter_rows(named=True):
                symbol = str(row["symbol"])
                if symbol != active_symbol:
                    active_symbol = symbol
                    engine = AuctionEngine(
                        binning=FixedStepBins(step=bin_step),
                        allocation=UniformAllocation(),
                        window_policy=RollingBars(max_bars=rolling_bars),
                        dataset_version=manifest.identity.dataset_version,
                        config_version=manifest.identity.config_version,
                        gap_policy=GapPolicy.RESET,
                    )
                if engine is None:
                    raise RuntimeError("auction engine was not initialized")
                snapshot = engine.update(
                    AuctionCandle(
                        timestamp=row["timestamp"],
                        symbol=symbol,
                        timeframe=str(row["timeframe"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        segment_id=int(row["segment_id"]),
                    )
                )
                if snapshot is not None:
                    yield snapshot


def _rows_by_stream(rows: tuple[FeatureRow, ...]) -> tuple[tuple[FeatureRow, ...], ...]:
    groups: list[list[FeatureRow]] = []
    key: tuple[str, str] | None = None
    for row in rows:
        current = (row.symbol, row.timeframe)
        if current != key:
            groups.append([])
            key = current
        groups[-1].append(row)
    return tuple(tuple(group) for group in groups)


def freeze_selected_universe(
    *,
    selection_id: str,
    promotion_receipt_path: str | Path,
    symbols: tuple[str, ...],
    timeframe: str,
    start: datetime,
    end: datetime,
    gap_boundaries: tuple[SegmentBoundary, ...],
    survivorship_policy: str,
    optional_field_policy: str,
    zero_volume_policy: str,
) -> SelectedUniverse:
    """Verify promotion evidence and freeze the exact non-holdout materialization scope."""

    receipt_path = Path(promotion_receipt_path)
    receipt = read_reconciliation_promotion_receipt(receipt_path)
    artifact_sha256 = sha256_regular(receipt_path)
    boundaries = tuple(
        sorted(
            gap_boundaries,
            key=lambda item: (item.symbol, item.timeframe, item.start, item.end, item.reason),
        )
    )
    return SelectedUniverse(
        selection_id=selection_id,
        symbols=symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        survivorship_policy=survivorship_policy,
        optional_field_policy=optional_field_policy,
        zero_volume_policy=zero_volume_policy,
        gap_boundaries=boundaries,
        gap_boundaries_sha256=segment_boundaries_sha256(boundaries),
        promotion_run_id=receipt.run_id,
        promotion_manifest_sha256=receipt.manifest_sha256,
        promotion_replacement_logical_sha256=receipt.replacement_logical_sha256,
        promotion_canonical_logical_sha256=receipt.canonical_logical_sha256,
        promotion_coverage_logical_sha256=receipt.coverage_logical_sha256,
        promotion_receipt_content_sha256=receipt.content_sha256,
        promotion_receipt_artifact_sha256=artifact_sha256,
        _factory_token=_SELECTED_UNIVERSE_FACTORY,
    )


def _boundary_payload(boundaries: tuple[SegmentBoundary, ...]) -> list[dict[str, str]]:
    return [
        {
            "symbol": item.symbol,
            "timeframe": item.timeframe,
            "start": _iso_utc(item.start),
            "end": _iso_utc(item.end),
            "reason": item.reason,
        }
        for item in boundaries
    ]


def _require_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")
    return value.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def canonical_policy_sha256(value: object) -> str:
    """Hash one frozen dataclass policy using canonical JSON-compatible values."""
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("policy must be a frozen dataclass instance")
    return _sha256(_canonical_json(_jsonable_policy(asdict(value))))


def _jsonable_policy(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable_policy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_policy(item) for item in value]
    return value


def _require_safe_id(value: str, field: str) -> None:
    if not isinstance(value, str) or _SAFE_POLICY.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe non-empty identifier")


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_ordered_unique(values: tuple[str, ...], field: str, *, canonical: bool = True) -> None:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) != len(set(values))
        or any(
            not isinstance(value, str) or _SAFE_POLICY.fullmatch(value) is None for value in values
        )
        or (canonical and tuple(sorted(values)) != values)
    ):
        raise ValueError(f"{field} must be a unique non-empty canonical tuple")


__all__ = [
    "DiscoveryPreregistration",
    "DiscoveryProgramBudget",
    "DiscoveryReliabilityVector",
    "Phase3PublicationBundle",
    "PreregisteredTrial",
    "SelectedUniverse",
    "build_preregistered_trial_grid",
    "build_phase3_publications",
    "canonical_policy_sha256",
    "freeze_discovery_preregistration",
    "freeze_selected_universe",
    "publish_reliability_vector",
]
