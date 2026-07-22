"""Frozen, outcome-blind orchestration contracts for real discovery programmes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import InitVar, asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from itertools import product
from pathlib import Path
from typing import Mapping, Sequence, cast

import polars as pl

from market_structure_lab.auction import AuctionCandle, AuctionEngine, GapPolicy, RollingBars
from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    read_bounded_regular,
    regular_file_matches,
    require_regular_directory,
    sha256_regular,
)
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
from market_structure_lab.discovery.runs import DiscoveryWorkBudget
from market_structure_lab.discovery.reliability import (
    ReliabilityEvidence,
    expected_reliability_contract,
    reliability_algorithm_versions,
)
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
_RECONCILIATION_RUN_ID = re.compile(r"^RR-[0-9]{6}$")
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
    eligibility_audit_sha256: str
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
        if _RECONCILIATION_RUN_ID.fullmatch(self.promotion_run_id) is None:
            raise ValueError("promotion_run_id must match RR-######")
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
            (self.eligibility_audit_sha256, "eligibility_audit_sha256"),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "selected-universe-v2",
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
            "eligibility_audit_sha256": self.eligibility_audit_sha256,
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
            eligibility_audit_sha256=self.eligibility_audit_sha256,
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
class Phase3ExecutionContract:
    """Exact deterministic feature/event construction policy frozen before data access."""

    config_version: str
    bin_step: float
    rolling_bars: int
    event_width: int
    maximum_rows: int

    def __post_init__(self) -> None:
        _require_safe_id(self.config_version, "config_version")
        if (
            isinstance(self.bin_step, bool)
            or not isinstance(self.bin_step, (int, float))
            or not 0.0 < float(self.bin_step) < float("inf")
        ):
            raise ValueError("bin_step must be a finite positive number")
        for field in ("rolling_bars", "event_width", "maximum_rows"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        object.__setattr__(self, "bin_step", float(self.bin_step))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase3-execution-contract-v1",
            "config_version": self.config_version,
            "bin_step": self.bin_step,
            "rolling_bars": self.rolling_bars,
            "event_width": self.event_width,
            "maximum_rows": self.maximum_rows,
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class DiscoveryPreregistration:
    """Canonical Task 14 programme frozen before any detector attempt."""

    preregistration_id: str
    selected_universe_sha256: str
    selected_timeframe: str
    split: FrozenDiscoverySplit
    dataset_snapshot_id: str
    feature_set_id: str
    registry_sha256: str
    normalizer_policy_id: str
    feature_names: tuple[str, ...]
    trials: tuple[PreregisteredTrial, ...]
    budget: DiscoveryProgramBudget
    phase3_execution: Phase3ExecutionContract
    run_work_budget: DiscoveryWorkBudget
    run_max_rows: int
    run_max_iterations: int
    run_tolerance: float
    stability_policy_sha256: str
    adjacent_period_policy_sha256: str
    motif_policy_sha256: str
    transition_policy_sha256: str
    missingness_policy_sha256: str
    regime_contract_sha256: str
    orchestration_parameters_sha256: str
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
            (self.adjacent_period_policy_sha256, "adjacent_period_policy_sha256"),
            (self.motif_policy_sha256, "motif_policy_sha256"),
            (self.transition_policy_sha256, "transition_policy_sha256"),
            (self.missingness_policy_sha256, "missingness_policy_sha256"),
            (self.regime_contract_sha256, "regime_contract_sha256"),
            (self.orchestration_parameters_sha256, "orchestration_parameters_sha256"),
            (self.lock_sha256, "lock_sha256"),
        ):
            _require_sha256(value, field)
        if self.selected_timeframe != "1m":
            raise ValueError("selected_timeframe must bind the canonical 1m universe")
        _require_safe_id(self.normalizer_policy_id, "normalizer_policy_id")
        _require_ordered_unique(self.feature_names, "feature_names", canonical=False)
        _require_ordered_unique(self.negative_controls, "negative_controls")
        _require_ordered_unique(self.naive_baselines, "naive_baselines")
        for name in self.negative_controls:
            if expected_reliability_contract(name).kind != "negative_control":
                raise ValueError("negative_controls contain an unsupported contract")
        for name in self.naive_baselines:
            if expected_reliability_contract(name).kind != "naive_baseline":
                raise ValueError("naive_baselines contain an unsupported contract")
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
        if not isinstance(self.phase3_execution, Phase3ExecutionContract):
            raise TypeError("phase3_execution must be a Phase3ExecutionContract")
        if not isinstance(self.run_work_budget, DiscoveryWorkBudget):
            raise TypeError("run_work_budget must be a DiscoveryWorkBudget")
        for field in ("run_max_rows", "run_max_iterations"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if (
            isinstance(self.run_tolerance, bool)
            or not isinstance(self.run_tolerance, (int, float))
            or not 0.0 < float(self.run_tolerance) < float("inf")
        ):
            raise ValueError("run_tolerance must be a finite positive number")
        object.__setattr__(self, "run_tolerance", float(self.run_tolerance))
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
            "schema_version": "discovery-preregistration-v2",
            "preregistration_id": self.preregistration_id,
            "selected_universe_sha256": self.selected_universe_sha256,
            "selected_timeframe": self.selected_timeframe,
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
            "phase3_execution": self.phase3_execution.to_dict(),
            "run_execution": {
                "max_rows": self.run_max_rows,
                "max_iterations": self.run_max_iterations,
                "tolerance": self.run_tolerance,
                "work_budget": _jsonable_policy(asdict(self.run_work_budget)),
                "work_budget_sha256": self.run_work_budget.sha256,
            },
            "stability_policy_sha256": self.stability_policy_sha256,
            "adjacent_period_policy_sha256": self.adjacent_period_policy_sha256,
            "motif_policy_sha256": self.motif_policy_sha256,
            "transition_policy_sha256": self.transition_policy_sha256,
            "missingness_policy_sha256": self.missingness_policy_sha256,
            "regime_contract_sha256": self.regime_contract_sha256,
            "orchestration_parameters_sha256": self.orchestration_parameters_sha256,
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
    evidence: tuple[tuple[str, object], ...]

    @classmethod
    def from_manifest(
        cls,
        manifest: TrialManifest,
        evidence: Mapping[str, object],
    ) -> TrialReliabilityRecord:
        return cls(
            run_id=manifest.run_id,
            status=manifest.status.value,
            identity_sha256=manifest.identity_sha256,
            receipt_sha256=manifest.receipt_sha256,
            artifact_sha256=manifest.artifact_sha256,
            conclusion=manifest.conclusion,
            warnings=manifest.warnings,
            evidence=tuple(sorted(evidence.items())),
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
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryReliabilityVector:
    preregistration_sha256: str
    attempted_trial_count: int
    status_counts: tuple[tuple[str, int], ...]
    conclusion: str
    negative_control_execution: tuple[tuple[str, str], ...]
    naive_baseline_execution: tuple[tuple[str, str], ...]
    trials: tuple[TrialReliabilityRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "discovery-reliability-vector-v2",
            "preregistration_sha256": self.preregistration_sha256,
            "attempted_trial_count": self.attempted_trial_count,
            "status_counts": dict(self.status_counts),
            "conclusion": self.conclusion,
            "negative_control_execution": dict(self.negative_control_execution),
            "naive_baseline_execution": dict(self.naive_baseline_execution),
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
    dimensions = (len(pca_components), len(cluster_counts), len(seed_sets))
    if any(size < 1 for size in dimensions):
        raise ValueError("trial grid dimensions must be non-empty")
    combination_count = dimensions[0] * dimensions[1] * dimensions[2]
    if combination_count > budget.maximum_trials:
        raise ValueError("trial grid exceeds the frozen trial budget")
    if len(run_ids) != combination_count:
        raise ValueError("run_ids must exactly cover the canonical trial grid")
    combinations = tuple(product(pca_components, cluster_counts, seed_sets))
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
    phase3_execution: Phase3ExecutionContract,
    run_work_budget: DiscoveryWorkBudget,
    run_max_rows: int,
    run_max_iterations: int,
    run_tolerance: float,
    stability_policy_sha256: str,
    adjacent_period_policy_sha256: str,
    motif_policy_sha256: str,
    transition_policy_sha256: str,
    missingness_policy_sha256: str,
    regime_contract_sha256: str,
    orchestration_parameters_sha256: str,
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
        selected_timeframe=selected_universe.timeframe,
        split=split,
        dataset_snapshot_id=dataset_snapshot_id,
        feature_set_id=feature_set_id,
        registry_sha256=registry_sha256,
        normalizer_policy_id=normalizer_policy_id,
        feature_names=feature_names,
        trials=trials,
        budget=budget,
        phase3_execution=phase3_execution,
        run_work_budget=run_work_budget,
        run_max_rows=run_max_rows,
        run_max_iterations=run_max_iterations,
        run_tolerance=run_tolerance,
        stability_policy_sha256=stability_policy_sha256,
        adjacent_period_policy_sha256=adjacent_period_policy_sha256,
        motif_policy_sha256=motif_policy_sha256,
        transition_policy_sha256=transition_policy_sha256,
        missingness_policy_sha256=missingness_policy_sha256,
        regime_contract_sha256=regime_contract_sha256,
        orchestration_parameters_sha256=orchestration_parameters_sha256,
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
    if snapshot_manifest.identity.config_version != preregistration.phase3_execution.config_version:
        raise ValueError("snapshot config identity does not match preregistration")
    if snapshot_manifest.identity.code_commit != preregistration.code_commit:
        raise ValueError("snapshot code identity does not match preregistration")
    if snapshot_manifest.identity.research_binding != selected_universe.snapshot_research_binding:
        raise ValueError("snapshot research provenance does not match selected universe")
    _validate_snapshot_scope(snapshot_manifest, selected_universe)
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
    actual_phase3 = Phase3ExecutionContract(
        config_version=snapshot_manifest.identity.config_version,
        bin_step=float(bin_step),
        rolling_bars=rolling_bars,
        event_width=event_width,
        maximum_rows=maximum_rows,
    )
    if actual_phase3 != preregistration.phase3_execution:
        raise ValueError("Phase 3 execution does not match the frozen preregistration")
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
        selected_universe=selected_universe,
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
        event_rows = tuple(
            row
            for row in rows
            if preregistration.split.discovery.contains(row.information_cutoff)
            and all(row.values[name] is not None for name in preregistration.feature_names)
        )
        events = tuple(
            event
            for group in _rows_by_stream(event_rows)
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
        require_regular_directory(normalizer_path.parent)
        normalizer_bytes = normalizer.canonical_json()
        if path_exists_no_follow(normalizer_path):
            if not regular_file_matches(normalizer_path, normalizer_bytes):
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
    records: list[TrialReliabilityRecord] = []
    for manifest, trial in zip(manifests, preregistration.trials, strict=True):
        if manifest.mode.value != "discovery":
            raise ValueError("reliability vector accepts discovery-mode receipts only")
        if manifest.canonical_config.get("preregistration_sha256") != preregistration.sha256:
            raise ValueError("trial receipt is not bound to the preregistration")
        work_budget = _verify_trial_config(preregistration, trial, manifest)
        records.append(
            TrialReliabilityRecord.from_manifest(
                manifest,
                _trial_reliability_evidence(
                    trial_root / manifest.run_id,
                    manifest,
                    work_budget=work_budget,
                    negative_controls=preregistration.negative_controls,
                    naive_baselines=preregistration.naive_baselines,
                ),
            )
        )
    frozen_records = tuple(records)
    counts: dict[str, int] = {}
    for record in frozen_records:
        counts[record.status] = counts.get(record.status, 0) + 1
    negative_control_execution = _aggregate_execution_classification(
        preregistration.negative_controls,
        frozen_records,
        evidence_field="negative_control_execution",
    )
    naive_baseline_execution = _aggregate_execution_classification(
        preregistration.naive_baselines,
        frozen_records,
        evidence_field="naive_baseline_execution",
    )
    all_required_evidence_executed = all(
        status == "executed"
        for _, status in (*negative_control_execution, *naive_baseline_execution)
    )
    if any(record.status == "completed" for record in frozen_records) and (
        all_required_evidence_executed
    ):
        conclusion = "accepted"
    elif frozen_records and all(record.status == "rejected" for record in frozen_records):
        conclusion = "rejected"
    else:
        conclusion = "inconclusive"
    vector = DiscoveryReliabilityVector(
        preregistration_sha256=preregistration.sha256,
        attempted_trial_count=len(frozen_records),
        status_counts=tuple(sorted(counts.items())),
        conclusion=conclusion,
        negative_control_execution=negative_control_execution,
        naive_baseline_execution=naive_baseline_execution,
        trials=frozen_records,
    )
    payload = {
        **vector.to_dict(),
        "sha256": vector.sha256,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    if len(encoded.encode("utf-8")) > preregistration.budget.maximum_serialized_evidence_bytes:
        raise ValueError("reliability vector exceeds the frozen evidence byte budget")
    destination.parent.mkdir(parents=True, exist_ok=True)
    require_regular_directory(destination.parent)
    encoded_bytes = encoded.encode("utf-8")
    if path_exists_no_follow(destination):
        if not regular_file_matches(destination, encoded_bytes):
            raise FileExistsError("reliability vector conflicts with prior publication")
        return vector
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    os.replace(temporary, destination)
    return vector


def _verify_trial_config(
    preregistration: DiscoveryPreregistration,
    trial: PreregisteredTrial,
    manifest: TrialManifest,
) -> DiscoveryWorkBudget:
    if manifest.timeframes != (preregistration.selected_timeframe,):
        raise ValueError("trial receipt timeframe does not match the preregistration")
    if (
        manifest.run_id != trial.run_id
        or manifest.dataset_snapshot.identifier != preregistration.dataset_snapshot_id
        or manifest.feature_registry.sha256 != preregistration.registry_sha256
        or manifest.code_commit != preregistration.code_commit
        or manifest.lock_sha256 != preregistration.lock_sha256
        or manifest.seed != trial.seeds[0]
        or manifest.symbols != preregistration.split.discovery.symbols
        or manifest.frozen_split.get("sha256") != preregistration.split.sha256
    ):
        raise ValueError("trial receipt identity does not match the preregistration")
    config = manifest.canonical_config
    expected_scalars: tuple[tuple[str, object], ...] = (
        ("run_id", trial.run_id),
        ("dataset_snapshot_id", preregistration.dataset_snapshot_id),
        ("feature_set_id", preregistration.feature_set_id),
        ("registry_sha256", preregistration.registry_sha256),
        ("config_version", preregistration.phase3_execution.config_version),
        ("feature_names", list(preregistration.feature_names)),
        ("pca_components", trial.pca_components),
        ("clusters", trial.clusters),
        ("seeds", list(trial.seeds)),
        ("max_rows", preregistration.run_max_rows),
        ("max_iterations", preregistration.run_max_iterations),
        ("tolerance", preregistration.run_tolerance),
        ("code_commit", preregistration.code_commit),
        ("lock_sha256", preregistration.lock_sha256),
    )
    for field, expected in expected_scalars:
        if config.get(field) != expected:
            raise ValueError(f"trial receipt {field} does not match the preregistration")
    split = config.get("split")
    if not isinstance(split, Mapping) or split.get("sha256") != preregistration.split.sha256:
        raise ValueError("trial receipt split does not match the preregistration")
    policy_fields = (
        ("stability_policy", preregistration.stability_policy_sha256),
        ("adjacent_period_stability_policy", preregistration.adjacent_period_policy_sha256),
        ("motif_stability_policy", preregistration.motif_policy_sha256),
        ("transition_uncertainty_policy", preregistration.transition_policy_sha256),
        ("missingness_policy", preregistration.missingness_policy_sha256),
    )
    for field, expected_sha256 in policy_fields:
        value = config.get(field)
        if not isinstance(value, Mapping) or canonical_policy_sha256(value) != expected_sha256:
            raise ValueError(f"trial receipt {field} does not match the preregistration")
    work_budget = config.get("work_budget")
    if not isinstance(work_budget, Mapping):
        raise ValueError("trial receipt work budget does not match the preregistration")
    work_values = {
        key: value for key, value in work_budget.items() if key not in {"schema_version", "sha256"}
    }
    try:
        actual_work_budget = DiscoveryWorkBudget(**work_values)
    except (TypeError, ValueError) as error:
        raise ValueError("trial receipt work budget does not match the preregistration") from error
    if (
        dict(work_budget) != actual_work_budget.to_dict()
        or actual_work_budget != preregistration.run_work_budget
        or actual_work_budget.sha256 != preregistration.run_work_budget.sha256
    ):
        raise ValueError("trial receipt work budget does not match the preregistration")
    orchestration = config.get("orchestration_parameters")
    if (
        not isinstance(orchestration, Mapping)
        or canonical_policy_sha256(orchestration) != preregistration.orchestration_parameters_sha256
    ):
        raise ValueError("trial receipt orchestration parameters do not match preregistration")
    regime = config.get("motif_regime_assignments")
    if not isinstance(regime, Mapping):
        raise ValueError("trial receipt regime contract does not match preregistration")
    regime_policy = {
        "algorithm_version": regime.get("algorithm_version"),
        "information_policy": regime.get("information_policy"),
        "regime_universe": regime.get("regime_universe"),
    }
    if canonical_policy_sha256(regime_policy) != preregistration.regime_contract_sha256:
        raise ValueError("trial receipt regime contract does not match preregistration")
    return actual_work_budget


def _trial_reliability_evidence(
    directory: Path,
    manifest: TrialManifest,
    *,
    work_budget: DiscoveryWorkBudget,
    negative_controls: tuple[str, ...],
    naive_baselines: tuple[str, ...],
) -> dict[str, object]:
    artifacts = dict(manifest.artifact_sha256)

    def optional_json(name: str) -> object:
        if name not in artifacts:
            return {}
        payload = read_bounded_regular(
            directory / name,
            64 * 1024 * 1024,
        )
        if _sha256(payload) != artifacts[name]:
            raise RuntimeError("trial evidence artifact hash does not match terminal receipt")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("trial evidence JSON is malformed") from error
        if not isinstance(value, (dict, list)):
            raise RuntimeError("trial evidence JSON must be a structured value")
        return value

    metrics = optional_json("metrics.json")
    discovery_manifest = optional_json("manifest.json")
    stability = optional_json("stability.json")
    motifs = optional_json("motifs.json")
    transitions = optional_json("transitions.json")
    negative_control_execution = _execution_classification(
        metrics,
        declared=negative_controls,
        metrics_field="negative_controls",
        work_budget=work_budget,
        discovery_manifest=discovery_manifest,
    )
    naive_baseline_execution = _execution_classification(
        metrics,
        declared=naive_baselines,
        metrics_field="naive_baselines",
        work_budget=work_budget,
        discovery_manifest=discovery_manifest,
    )
    if (
        any(
            status == "executed"
            for _, status in (*negative_control_execution, *naive_baseline_execution)
        )
        and manifest.canonical_config.get("reliability_evidence_algorithms")
        != reliability_algorithm_versions()
    ):
        raise RuntimeError("trial reliability algorithms are not frozen in config identity")
    return {
        "data_evidence": {
            "dataset_snapshot": manifest.dataset_snapshot.to_dict(),
            "feature_publication": manifest.feature_publication.to_dict(),
            "feature_registry": manifest.feature_registry.to_dict(),
            "normalizer": manifest.normalizer.to_dict(),
            "split_sha256": manifest.frozen_split.get("sha256"),
            "code_commit": manifest.code_commit,
            "lock_sha256": manifest.lock_sha256,
        },
        "replay_hashes": artifacts,
        "effective_supports": metrics,
        "cluster_stability": stability,
        "motif_stability": motifs,
        "transition_intervals": transitions,
        "negative_control_execution": dict(negative_control_execution),
        "naive_baseline_execution": dict(naive_baseline_execution),
        "asset_regime_breakdowns": {
            "stability": stability,
            "motifs": motifs,
        },
        "contradictions": {
            "status": manifest.status.value,
            "warnings": list(manifest.warnings),
            "conclusion": manifest.conclusion,
        },
    }


def _execution_classification(
    metrics: object,
    *,
    declared: tuple[str, ...],
    metrics_field: str,
    work_budget: DiscoveryWorkBudget,
    discovery_manifest: object,
) -> tuple[tuple[str, str], ...]:
    raw = metrics.get(metrics_field) if isinstance(metrics, Mapping) else None
    if raw is None:
        return tuple((name, "unexecuted") for name in declared)
    if not isinstance(raw, Mapping) or any(name not in declared for name in raw):
        raise RuntimeError("trial control execution evidence is malformed")
    if not raw:
        return tuple((name, "unexecuted") for name in declared)
    input_sha256 = metrics.get("discovery_matrix_sha256") if isinstance(metrics, Mapping) else None
    if not isinstance(input_sha256, str) or _SHA256.fullmatch(input_sha256) is None:
        raise RuntimeError("trial control execution input identity is missing")
    work_evidence: Mapping[str, object] | None = None
    metrics_mapping = cast(Mapping[str, object], metrics)
    classified: list[tuple[str, str]] = []
    for name in declared:
        payload = raw.get(name)
        if payload is None:
            classified.append((name, "unexecuted"))
            continue
        if not isinstance(payload, Mapping):
            raise RuntimeError("trial control execution evidence is malformed")
        evidence = ReliabilityEvidence.from_dict(payload)
        if work_evidence is None:
            work_evidence = _verify_reliability_context(
                metrics=metrics_mapping,
                discovery_manifest=discovery_manifest,
                input_sha256=input_sha256,
                work_budget=work_budget,
            )
        if evidence.name != name or evidence.source_matrix_sha256 != input_sha256:
            raise RuntimeError("trial control execution evidence identity is inconsistent")
        if name == "time_order_preserving_null":
            assert work_evidence is not None
            _verify_null_projection_budget(evidence.result, work_evidence, work_budget)
        classified.append((name, "executed"))
    return tuple(classified)


def _verify_reliability_context(
    *,
    metrics: Mapping[str, object],
    discovery_manifest: object,
    input_sha256: str,
    work_budget: DiscoveryWorkBudget,
) -> Mapping[str, object]:
    if not isinstance(discovery_manifest, Mapping):
        raise RuntimeError("trial discovery manifest identity is missing")
    if discovery_manifest.get("schema_version") != "discovery-run-manifest-v3":
        raise RuntimeError("trial discovery manifest schema is invalid")
    manifest_sha256 = discovery_manifest.get("manifest_sha256")
    manifest_body = {
        key: value for key, value in discovery_manifest.items() if key != "manifest_sha256"
    }
    if (
        not isinstance(manifest_sha256, str)
        or _SHA256.fullmatch(manifest_sha256) is None
        or canonical_policy_sha256(manifest_body) != manifest_sha256
    ):
        raise RuntimeError("trial discovery manifest identity is invalid")
    if discovery_manifest.get("discovery_matrix_sha256") != input_sha256:
        raise RuntimeError("trial control matrix identity is inconsistent")
    work_evidence = metrics.get("work_budget")
    if not isinstance(work_evidence, Mapping):
        raise RuntimeError("trial control work budget evidence is missing")
    if (
        canonical_policy_sha256(work_evidence) != discovery_manifest.get("work_preflight_sha256")
        or work_evidence.get("budget_sha256") != work_budget.sha256
    ):
        raise RuntimeError("trial control work budget identity is inconsistent")
    return work_evidence


def _verify_null_projection_budget(
    result: Mapping[str, object],
    work_evidence: Mapping[str, object],
    work_budget: DiscoveryWorkBudget,
) -> None:
    control_limit = (
        work_budget.maximum_reliability_control_projection_cells or work_budget.maximum_pca_cells
    )
    aggregate_limit = (
        work_budget.maximum_aggregate_projection_cells or work_budget.maximum_pca_cells * 2
    )
    expected = {
        "work_budget_sha256": work_budget.sha256,
        "partition_projection_cells": work_evidence.get("maximum_partition_pca_cells"),
        "maximum_partition_projection_cells": work_budget.maximum_pca_cells,
        "reliability_control_projection_cells": work_evidence.get(
            "reliability_control_projection_cells"
        ),
        "maximum_reliability_control_projection_cells": control_limit,
        "aggregate_projection_cells": work_evidence.get("aggregate_projection_cells"),
        "maximum_aggregate_projection_cells": aggregate_limit,
    }
    expected_work_limits = {
        "maximum_partition_pca_cells_limit": work_budget.maximum_pca_cells,
        "maximum_reliability_control_projection_cells": control_limit,
        "maximum_aggregate_projection_cells": aggregate_limit,
    }
    if any(work_evidence.get(name) != value for name, value in expected_work_limits.items()):
        raise RuntimeError("trial control projection work budget is inconsistent")
    if any(result.get(name) != value for name, value in expected.items()):
        raise RuntimeError("trial null projection evidence does not match the frozen work budget")


def _aggregate_execution_classification(
    declared: tuple[str, ...],
    records: tuple[TrialReliabilityRecord, ...],
    *,
    evidence_field: str,
) -> tuple[tuple[str, str], ...]:
    completed = tuple(record for record in records if record.status == "completed")
    aggregate: list[tuple[str, str]] = []
    for name in declared:
        executed = bool(completed) and all(
            isinstance((evidence := dict(record.evidence).get(evidence_field)), Mapping)
            and evidence.get(name) == "executed"
            for record in completed
        )
        aggregate.append((name, "executed" if executed else "unexecuted"))
    return tuple(aggregate)


def _iter_auction_snapshots(
    *,
    snapshot_directory: Path,
    manifest: SnapshotManifest,
    bin_step: float,
    rolling_bars: int,
    selected_universe: SelectedUniverse,
):
    active_symbol: str | None = None
    engine: AuctionEngine | None = None
    for partition in manifest.partitions:
        path = snapshot_directory / partition.path
        for batch in pl.scan_parquet(path).collect_batches(chunk_size=10_000, maintain_order=True):
            for row in batch.iter_rows(named=True):
                symbol = str(row["symbol"])
                timeframe = str(row["timeframe"])
                timestamp = row["timestamp"]
                if (
                    symbol not in selected_universe.symbols
                    or timeframe != selected_universe.timeframe
                    or timestamp < selected_universe.start
                    or timestamp >= selected_universe.end
                    or any(
                        boundary.symbol == symbol
                        and boundary.timeframe == timeframe
                        and boundary.start <= timestamp < boundary.end
                        for boundary in selected_universe.gap_boundaries
                    )
                ):
                    raise ValueError("snapshot row is outside the frozen selected universe")
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
                        timestamp=timestamp,
                        symbol=symbol,
                        timeframe=timeframe,
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


def _validate_snapshot_scope(
    manifest: SnapshotManifest,
    selected_universe: SelectedUniverse,
) -> None:
    if manifest.row_count < 1 or manifest.min_timestamp is None or manifest.max_timestamp is None:
        raise ValueError("snapshot must contain selected-universe rows")
    minimum = datetime.fromisoformat(manifest.min_timestamp.replace("Z", "+00:00"))
    maximum = datetime.fromisoformat(manifest.max_timestamp.replace("Z", "+00:00"))
    if minimum < selected_universe.start or maximum >= selected_universe.end:
        raise ValueError("snapshot manifest extends outside the frozen selected universe")
    allowed_prefixes = tuple(
        f"symbol={symbol}/timeframe={selected_universe.timeframe}/"
        for symbol in selected_universe.symbols
    )
    if any(not partition.path.startswith(allowed_prefixes) for partition in manifest.partitions):
        raise ValueError("snapshot manifest contains a foreign symbol or timeframe")


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
    eligibility_audit_sha256: str,
    expected_promotion_run_id: str = "RR-000008",
) -> SelectedUniverse:
    """Verify promotion evidence and freeze the exact non-holdout materialization scope."""

    receipt_path = Path(promotion_receipt_path)
    receipt = read_reconciliation_promotion_receipt(receipt_path)
    if receipt.run_id != expected_promotion_run_id:
        raise ValueError("promotion receipt does not match the frozen prerequisite run")
    _require_sha256(eligibility_audit_sha256, "eligibility_audit_sha256")
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
        eligibility_audit_sha256=eligibility_audit_sha256,
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
    if is_dataclass(value) and not isinstance(value, type):
        payload: object = asdict(value)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise TypeError("policy must be a frozen dataclass or mapping")
    return _sha256(_canonical_json(_jsonable_policy(payload)))


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
    "Phase3ExecutionContract",
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
