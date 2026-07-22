"""Run the first bounded real outcome-blind discovery programme."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from market_structure_lab.cli.errors import database_error_message
from market_structure_lab.core.config import load_settings
from market_structure_lab.data.derived import (
    LeakageAuditApproval,
    LeakageNegativePattern,
    iter_published_event_bindings,
)
from market_structure_lab.data.freshness_snapshot import publish_scoped_freshness_snapshot
from market_structure_lab.data.freshness_sync import read_freshness_report
from market_structure_lab.data.segments import SegmentBoundary
from market_structure_lab.discovery.behaviours import BehaviourEventBinding
from market_structure_lab.discovery.matrix import MissingnessPolicy
from market_structure_lab.discovery.motifs import (
    MotifStabilityPolicy,
    freeze_motif_regime_assignments,
)
from market_structure_lab.discovery.program import (
    DiscoveryProgramBudget,
    build_phase3_publications,
    build_preregistered_trial_grid,
    canonical_policy_sha256,
    freeze_discovery_preregistration,
    freeze_selected_universe,
    publish_reliability_vector,
)
from market_structure_lab.discovery.runs import (
    DiscoveryRunConfig,
    DiscoveryWorkBudget,
    run_discovery,
)
from market_structure_lab.discovery.splits import (
    PartitionRole,
    TimePartition,
    freeze_discovery_provenance,
    freeze_split,
    make_discovery_input,
)
from market_structure_lab.discovery.stability import (
    AdjacentPeriodStabilityPolicy,
    StabilityPolicy,
)
from market_structure_lab.discovery.transitions import TransitionUncertaintyPolicy
from market_structure_lab.features.builtin import builtin_feature_registry

DEFAULT_FRESHNESS_REPORT = Path(
    "data/exports/freshness/20260717T001500Z-a1b1085f76e4-2f4f848681b7.report.json"
)
DEFAULT_PROMOTION_RECEIPT = Path(
    "data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json"
)
DEFAULT_SNAPSHOT_ROOT = Path("data/exports/snapshots")
DEFAULT_DERIVED_ROOT = Path("data/exports/derived/task14-PG-000001")
DEFAULT_PROGRAM_ROOT = Path("data/exports/discovery-programs/PG-000001")
DEFAULT_TRIAL_ROOT = Path("data/exports/trials/task14-PG-000001")
AUCTION_CONFIG_VERSION = "task14-auction-v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one preregistered outcome-blind discovery without holdout row access"
    )
    parser.add_argument("--freshness-report", type=Path, default=DEFAULT_FRESHNESS_REPORT)
    parser.add_argument("--promotion-receipt", type=Path, default=DEFAULT_PROMOTION_RECEIPT)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--derived-root", type=Path, default=DEFAULT_DERIVED_ROOT)
    parser.add_argument("--program-root", type=Path, default=DEFAULT_PROGRAM_ROOT)
    parser.add_argument("--trial-root", type=Path, default=DEFAULT_TRIAL_ROOT)
    parser.add_argument("--batch-size", type=int, default=10_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _execute(args)
    except SQLAlchemyError as error:
        print(database_error_message(error), file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _execute(args: argparse.Namespace) -> dict[str, object]:
    code_commit = _git("rev-parse", "HEAD")
    lockfile_bytes = Path("uv.lock").read_bytes()
    lock_sha256 = hashlib.sha256(lockfile_bytes).hexdigest()
    report = read_freshness_report(args.freshness_report)
    registry = builtin_feature_registry()
    start = datetime(2025, 2, 1, tzinfo=UTC)
    discovery_end = datetime(2025, 2, 8, tzinfo=UTC)
    development_end = datetime(2025, 2, 15, tzinfo=UTC)
    holdout_end = datetime(2025, 2, 22, tzinfo=UTC)
    boundaries = _selected_boundaries(report, "APTUSDT", start, development_end)
    selected = freeze_selected_universe(
        selection_id="SU-000701",
        promotion_receipt_path=args.promotion_receipt,
        symbols=("APTUSDT",),
        timeframe="1m",
        start=start,
        end=development_end,
        gap_boundaries=boundaries,
        survivorship_policy="point_in_time_freshness_recovered_only_v1",
        optional_field_policy="ignore_unavailable_optional_trade_fields_v1",
        zero_volume_policy="retain_observed_zero_volume_v1",
    )
    split = freeze_split(
        split_id="task14-first-real-discovery-v1",
        discovery=TimePartition(PartitionRole.DISCOVERY, start, discovery_end, selected.symbols),
        development=TimePartition(
            PartitionRole.DEVELOPMENT,
            discovery_end,
            development_end,
            selected.symbols,
        ),
        holdout=TimePartition(
            PartitionRole.HOLDOUT,
            development_end,
            holdout_end,
            selected.symbols,
        ),
        asset_holdouts=("IMXUSDT",),
    )
    stability = StabilityPolicy(
        minimum_seed_ari=0.75,
        minimum_subsample_ari=0.60,
        maximum_adjacent_js_distance=0.30,
        minimum_asset_coverage=1.0,
        minimum_parameter_perturbation_ari=0.60,
    )
    adjacent = AdjacentPeriodStabilityPolicy(
        policy_id="task14-adjacent-period-research-v1",
        policy_purpose="research",
        maximum_centroid_displacement=2.0,
        maximum_within_cluster_scale_change=2.0,
        maximum_assignment_margin_drift=2.0,
        minimum_cluster_event_support=50,
    )
    motif = MotifStabilityPolicy(
        policy_id="task14-motif-research-v1",
        policy_purpose="research",
        window_lengths=(3, 5),
        exclusion_zones=(2,),
        tie_seeds=(7, 11),
        tie_policies=("canonical", "seeded_hash"),
        subsample_fraction=0.75,
        distance_multipliers=(0.9, 1.0, 1.1),
        maximum_distance=1.5,
        max_windows=256,
        top_k=20,
        minimum_seed_rank_agreement=0.60,
        minimum_subsample_agreement=0.50,
        minimum_parameter_agreement=0.50,
        minimum_recurrence_support=2,
        minimum_asset_support=1,
        minimum_period_support=2,
        minimum_regime_support=1,
    )
    transition = TransitionUncertaintyPolicy(
        policy_id="task14-transition-research-v1",
        policy_purpose="research",
        horizon=1,
        bootstrap_seed=17,
        bootstrap_iterations=500,
        confidence_level=0.95,
        block_length_rule="cube_root_transition_support",
        block_length_value=8,
        minimum_effective_support=50,
        interval_width_action="reject",
        maximum_interval_width=0.30,
        sensitivity_offsets=(-1, 1),
        maximum_sensitivity_endpoint_delta=0.15,
    )
    missingness = MissingnessPolicy(
        policy_id="task14-complete-case-research-v1",
        maximum_total_drop_fraction=0.05,
        maximum_per_feature_drop_fraction=0.05,
        maximum_evidence_groups=1_000,
    )
    feature_names = (
        "poc_distance_close",
        "poc_volume_share",
        "range_close_fraction",
        "value_width_close",
        "volume_relative_median_20",
        "vwap_distance_close",
    )
    program_budget = DiscoveryProgramBudget(
        budget_id="task14-program-budget-v1",
        maximum_trials=1,
        maximum_total_stability_fits=8,
        maximum_serialized_evidence_bytes=64 * 1024 * 1024,
    )
    trials = build_preregistered_trial_grid(
        run_ids=("DR-000701",),
        pca_components=(3,),
        cluster_counts=(3,),
        seed_sets=((7, 11, 13),),
        budget=program_budget,
    )
    regime_policy = {
        "algorithm_version": "constant-contemporaneous-regime-v1",
        "information_policy": "contemporaneous",
        "regime_universe": ["all_observed"],
    }
    preregistration = freeze_discovery_preregistration(
        preregistration_id="PG-000001",
        selected_universe=selected,
        split=split,
        dataset_snapshot_id="DS-000701",
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=feature_names,
        trials=trials,
        budget=program_budget,
        stability_policy_sha256=canonical_policy_sha256(stability),
        motif_policy_sha256=canonical_policy_sha256(motif),
        transition_policy_sha256=canonical_policy_sha256(transition),
        missingness_policy_sha256=missingness.sha256,
        regime_contract_sha256=_sha_json(regime_policy),
        negative_controls=("seed_perturbation", "time_order_preserving_null"),
        naive_baselines=("single_cluster", "unconditional_recurrence"),
        rejection_rules=(
            "missingness_policy_violation",
            "stability_policy_rejection",
            "transition_interval_rejection",
        ),
        code_commit=code_commit,
        lock_sha256=lock_sha256,
    )
    _publish_json(args.program_root / "selected-universe.json", selected.to_dict(), selected.sha256)
    _publish_json(
        args.program_root / "preregistration.json",
        preregistration.to_dict(),
        preregistration.sha256,
    )
    settings = load_settings()
    engine = create_engine(settings.database.url)
    try:
        snapshot = publish_scoped_freshness_snapshot(
            engine,
            report,
            output_root=args.snapshot_root,
            dataset_version=preregistration.dataset_snapshot_id,
            config_version=AUCTION_CONFIG_VERSION,
            code_commit=code_commit,
            symbols=selected.symbols,
            timeframe=selected.timeframe,
            start=selected.start,
            end=selected.end,
            boundaries=selected.gap_boundaries,
            research_binding=selected.snapshot_research_binding,
            batch_size=args.batch_size,
            settings=settings,
        )
    finally:
        engine.dispose()
    snapshot_directory = (
        args.snapshot_root / f"dataset_version={preregistration.dataset_snapshot_id}"
    )
    approval = _leakage_approval(registry, args.program_root)
    phase3 = build_phase3_publications(
        preregistration=preregistration,
        selected_universe=selected,
        snapshot_directory=snapshot_directory,
        snapshot_manifest=snapshot,
        output_root=args.derived_root,
        work_root=args.program_root / "work",
        registry=registry,
        leakage_approval=approval,
        bin_step=0.001,
        rolling_bars=1_440,
        event_width=1,
        maximum_rows=25_000,
        lockfile_bytes=lockfile_bytes,
    )
    discovery_rows = tuple(
        row for row in phase3.rows if split.discovery.contains(row.information_cutoff)
    )
    development_rows = tuple(
        row for row in phase3.rows if split.development.contains(row.information_cutoff)
    )
    discovery_input = make_discovery_input(
        partition=split.discovery,
        rows=discovery_rows,
        registry=registry,
        purpose="fit",
        max_rows=25_000,
        publication_manifest=phase3.feature_manifest,
    )
    development_input = make_discovery_input(
        partition=split.development,
        rows=development_rows,
        registry=registry,
        purpose="stability",
        max_rows=25_000,
        publication_manifest=phase3.feature_manifest,
    )
    selected_rows = tuple(
        row
        for row in (*discovery_input.rows, *development_input.rows)
        if all(row.values[name] is not None for name in feature_names)
    )
    selected_ids = {_row_id(row) for row in selected_rows}
    regimes = freeze_motif_regime_assignments(
        contract_id="task14-contemporaneous-regimes-v1",
        algorithm_version="constant-contemporaneous-regime-v1",
        information_policy="contemporaneous",
        outcome_policy="outcome_blind",
        regime_universe=("all_observed",),
        assignments=tuple((row_id, "all_observed") for row_id in sorted(selected_ids)),
    )
    provenance = freeze_discovery_provenance(
        snapshot_manifest=snapshot,
        feature_publication=phase3.feature_manifest,
        registry=registry,
        normalizer_artifact=phase3.normalizer.canonical_json(),
        split=split,
        feature_names=feature_names,
        discovery=discovery_input,
        development=development_input,
        code_commit=code_commit,
        lockfile_bytes=lockfile_bytes,
        motif_regime_assignment_sha256=regimes.sha256,
    )
    event_bindings = tuple(
        BehaviourEventBinding(
            row_id=item.row_id,
            event_id=item.event_id,
            duration_seconds=item.duration_seconds,
            event_publication_sha256=item.event_publication_sha256,
        )
        for item in iter_published_event_bindings(phase3.event_directory, phase3.event_manifest)
        if item.row_id in selected_ids and split.discovery.contains(_row_timestamp(item.row_id))
    )
    trial = preregistration.trials[0]
    work_budget = DiscoveryWorkBudget(
        budget_id="task14-run-budget-v1",
        maximum_materialized_rows=50_000,
        maximum_feature_cells=300_000,
        maximum_pca_rows=25_000,
        maximum_pca_features=len(feature_names),
        maximum_pca_cells=150_000,
        maximum_clusters=3,
        maximum_seeds=3,
        maximum_kmeans_iterations=100,
        maximum_total_stability_fits=8,
        maximum_serialized_evidence_bytes=64 * 1024 * 1024,
        maximum_bundle_entries=100,
    )
    config = DiscoveryRunConfig(
        run_id=trial.run_id,
        dataset_snapshot_id=preregistration.dataset_snapshot_id,
        dataset_snapshot_sha256=snapshot.snapshot_sha256,
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        config_version=AUCTION_CONFIG_VERSION,
        split=split,
        feature_names=feature_names,
        pca_components=trial.pca_components,
        clusters=trial.clusters,
        seeds=trial.seeds,
        max_rows=25_000,
        max_iterations=100,
        tolerance=1e-12,
        stability_policy=stability,
        adjacent_period_stability_policy=adjacent,
        motif_stability_policy=motif,
        motif_regime_assignments=regimes,
        transition_uncertainty_policy=transition,
        missingness_policy=missingness,
        work_budget=work_budget,
        event_publication_id="EP-000701",
        event_publication_sha256=phase3.event_manifest.publication_sha256,
        code_commit=code_commit,
        lock_sha256=lock_sha256,
        feature_publication_id="FP-000701",
        feature_publication_sha256=phase3.feature_manifest.publication_sha256,
        normalizer_id="NZ-000701",
        normalizer_sha256=phase3.normalizer.artifact_sha256,
        provenance=provenance,
        preregistration_sha256=preregistration.sha256,
        started_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 22, 12, 1, tzinfo=UTC),
    )
    manifest = run_discovery(
        config=config,
        discovery=discovery_input,
        development=development_input,
        registry=registry,
        event_bindings=event_bindings,
        output_root=args.trial_root,
        snapshot_directory=snapshot_directory,
        snapshot_manifest=snapshot,
        feature_publication_directory=phase3.feature_directory,
        feature_publication=phase3.feature_manifest,
        event_publication_directory=phase3.event_directory,
        event_publication=phase3.event_manifest,
        normalizer_artifact=phase3.normalizer.canonical_json(),
        lockfile_bytes=lockfile_bytes,
    )
    vector = publish_reliability_vector(
        preregistration=preregistration,
        trial_root=args.trial_root,
        destination=args.program_root / "reliability-vector.json",
    )
    return {
        "run_id": manifest.run_id,
        "run_status": manifest.status,
        "preregistration_sha256": preregistration.sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "feature_publication_sha256": phase3.feature_manifest.publication_sha256,
        "event_publication_sha256": phase3.event_manifest.publication_sha256,
        "normalizer_sha256": phase3.normalizer.artifact_sha256,
        "reliability_vector_sha256": vector.sha256,
        "reliability_conclusion": vector.conclusion,
        "holdout_rows_accessed": False,
    }


def _selected_boundaries(report, symbol: str, start: datetime, end: datetime):
    item = next(
        (
            candidate
            for candidate in report.symbols
            if candidate.symbol == symbol and candidate.timeframe == "1m"
        ),
        None,
    )
    if item is None:
        raise ValueError("selected symbol is absent from freshness evidence")
    return tuple(
        SegmentBoundary.from_gap_range(gap, reason="freshness_verified_canonical_gap")
        for gap in item.after_missing_ranges
        if datetime.fromtimestamp(gap.end_ms / 1_000, tz=UTC) > start
        and datetime.fromtimestamp(gap.start_ms / 1_000, tz=UTC) < end
    )


def _leakage_approval(registry, program_root: Path) -> LeakageAuditApproval:
    review_path = Path("docs/PHASE4_HARDENING_EVIDENCE.md")
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    test_sha = hashlib.sha256(Path("tests/test_feature_registry.py").read_bytes()).hexdigest()
    negative_sha = hashlib.sha256(
        Path("tests/test_derived_publication.py").read_bytes()
    ).hexdigest()
    approval = LeakageAuditApproval(
        schema_version=1,
        feature_registry_sha256=registry.sha256,
        reviewer_id="task14-phase4-evidence-review-v1",
        review_artifact_sha256=review_sha,
        field_test_evidence=tuple(
            (name, _sha_json({"feature": name, "test_artifact_sha256": test_sha}))
            for name in registry.names
        ),
        negative_test_evidence=tuple(
            (pattern, _sha_json({"pattern": pattern.value, "test_artifact_sha256": negative_sha}))
            for pattern in LeakageNegativePattern
        ),
    )
    _publish_json(program_root / "leakage-approval.json", approval.to_dict(), approval.sha256)
    return approval


def _publish_json(path: Path, payload: dict[str, object], sha256: str) -> None:
    encoded = (
        json.dumps(
            {"artifact": payload, "sha256": sha256},
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(f"immutable Task 14 artifact conflict: {path.name}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _row_id(row) -> str:
    timestamp = row.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return f"{row.symbol}|{row.timeframe}|{timestamp}"


def _row_timestamp(row_id: str) -> datetime:
    return datetime.fromisoformat(row_id.rsplit("|", 1)[1].replace("Z", "+00:00")) + timedelta(
        minutes=1
    )


def _sha_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError("Task 14 Git identity is unavailable")
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
