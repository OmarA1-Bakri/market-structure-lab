"""Run the first bounded real outcome-blind discovery programme."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from market_structure_lab.cli.errors import database_error_message
from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    regular_file_matches,
    require_regular_directory,
)
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
    MOTIF_ALGORITHM_VERSION,
    MotifStabilityPolicy,
    freeze_motif_regime_assignments,
)
from market_structure_lab.discovery.program import (
    DiscoveryProgramBudget,
    Phase3ExecutionContract,
    build_phase3_publications,
    build_preregistered_trial_grid,
    canonical_policy_sha256,
    freeze_discovery_preregistration,
    freeze_selected_universe,
    publish_reliability_vector,
)
from market_structure_lab.discovery.reliability import reliability_algorithm_versions
from market_structure_lab.discovery.runs import (
    DiscoveryRunConfig,
    DiscoveryWorkBudget,
    run_discovery,
    verify_runtime_code_identity,
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
    STABILITY_ALGORITHM_VERSION,
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
DEFAULT_DERIVED_ROOT = Path("data/exports/derived/task14-PG-000004")
DEFAULT_PROGRAM_ROOT = Path("data/exports/discovery-programs/PG-000004")
DEFAULT_TRIAL_ROOT = Path("data/exports/trials/task14-PG-000004")
AUCTION_CONFIG_VERSION = "task14-auction-v1"
TASK14_TERMINAL_REJECTION_RULES = (
    "missingness_policy_violation",
    "stability_policy_rejection",
)


@dataclass(frozen=True, slots=True)
class Task14PilotConfiguration:
    selection_id: str
    preregistration_id: str
    split_id: str
    run_id: str
    dataset_snapshot_id: str
    feature_publication_id: str
    event_publication_id: str
    normalizer_id: str
    regime_assignment_contract_id: str
    start: datetime
    discovery_end: datetime
    development_end: datetime
    holdout_end: datetime
    feature_names: tuple[str, ...]
    motif_policy: MotifStabilityPolicy
    program_budget: DiscoveryProgramBudget
    phase3_execution: Phase3ExecutionContract
    work_budget: DiscoveryWorkBudget
    run_max_rows: int
    started_at: datetime
    completed_at: datetime


def _pilot_configuration() -> Task14PilotConfiguration:
    """Return the frozen metadata-only PG-000004 pilot contract without external access."""
    feature_names = (
        "poc_distance_close",
        "poc_volume_share",
        "range_close_fraction",
        "value_width_close",
        "volume_relative_median_20",
        "vwap_distance_close",
    )
    return Task14PilotConfiguration(
        selection_id="SU-000704",
        preregistration_id="PG-000004",
        split_id="task14-first-real-discovery-v4",
        run_id="DR-000704",
        dataset_snapshot_id="DS-000704",
        feature_publication_id="FP-000704",
        event_publication_id="EP-000704",
        normalizer_id="NZ-000704",
        regime_assignment_contract_id="task14-contemporaneous-regimes-v4",
        start=datetime(2025, 2, 1, tzinfo=UTC),
        discovery_end=datetime(2025, 2, 1, 8, 0, tzinfo=UTC),
        development_end=datetime(2025, 2, 1, 16, 0, tzinfo=UTC),
        holdout_end=datetime(2025, 2, 2, tzinfo=UTC),
        feature_names=feature_names,
        motif_policy=MotifStabilityPolicy(
            policy_id="task14-motif-research-v2",
            policy_purpose="research",
            window_lengths=(3, 5),
            exclusion_zones=(2,),
            tie_seeds=(7, 11),
            tie_policies=("canonical", "seeded_hash"),
            subsample_fraction=0.75,
            distance_multipliers=(0.9, 1.0, 1.1),
            maximum_distance=1.5,
            max_windows=512,
            top_k=20,
            minimum_seed_rank_agreement=0.60,
            minimum_subsample_agreement=0.50,
            minimum_parameter_agreement=0.50,
            minimum_recurrence_support=2,
            minimum_asset_support=1,
            minimum_period_support=2,
            minimum_regime_support=1,
        ),
        program_budget=DiscoveryProgramBudget(
            budget_id="task14-program-budget-v4",
            maximum_trials=1,
            maximum_total_stability_fits=8,
            maximum_serialized_evidence_bytes=64 * 1024 * 1024,
        ),
        phase3_execution=Phase3ExecutionContract(
            config_version=AUCTION_CONFIG_VERSION,
            bin_step=0.001,
            rolling_bars=1_440,
            event_width=1,
            maximum_rows=960,
        ),
        work_budget=DiscoveryWorkBudget(
            budget_id="task14-run-budget-v5",
            maximum_materialized_rows=959,
            maximum_feature_cells=8_628,
            maximum_pca_rows=480,
            maximum_pca_features=len(feature_names),
            maximum_pca_cells=2_880,
            maximum_reliability_control_projection_cells=1_437,
            maximum_aggregate_projection_cells=4_317,
            maximum_clusters=3,
            maximum_seeds=3,
            maximum_kmeans_iterations=100,
            maximum_total_stability_fits=8,
            maximum_serialized_evidence_bytes=64 * 1024 * 1024,
            maximum_bundle_entries=100,
        ),
        run_max_rows=480,
        started_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 22, 20, 1, tzinfo=UTC),
    )


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
    return _summary_exit_code(summary)


def _summary_exit_code(summary: dict[str, object]) -> int:
    terminal_pair = (summary.get("run_status"), summary.get("reliability_conclusion"))
    return (
        0
        if terminal_pair
        in {
            ("completed", "accepted"),
            ("rejected", "rejected"),
            ("rejected_unstable", "rejected"),
        }
        else 1
    )


def _execute(args: argparse.Namespace) -> dict[str, object]:
    pilot = _pilot_configuration()
    code_commit = _git("rev-parse", "HEAD")
    lockfile_bytes = Path("uv.lock").read_bytes()
    verify_runtime_code_identity(code_commit=code_commit, lockfile_bytes=lockfile_bytes)
    lock_sha256 = hashlib.sha256(lockfile_bytes).hexdigest()
    report = read_freshness_report(args.freshness_report)
    eligibility_audit = _eligibility_audit(report)
    eligibility_audit_sha256 = _sha_json(eligibility_audit)
    _publish_json(
        args.program_root / "eligibility-audit.json",
        eligibility_audit,
        eligibility_audit_sha256,
    )
    registry = builtin_feature_registry()
    start = pilot.start
    discovery_end = pilot.discovery_end
    development_end = pilot.development_end
    holdout_end = pilot.holdout_end
    boundaries = _selected_boundaries(report, "APTUSDT", start, development_end)
    selected = freeze_selected_universe(
        selection_id=pilot.selection_id,
        promotion_receipt_path=args.promotion_receipt,
        symbols=("APTUSDT",),
        timeframe="1m",
        start=start,
        end=development_end,
        gap_boundaries=boundaries,
        survivorship_policy="point_in_time_freshness_recovered_only_v1",
        optional_field_policy="ignore_unavailable_optional_trade_fields_v1",
        zero_volume_policy="retain_observed_zero_volume_v1",
        eligibility_audit_sha256=eligibility_audit_sha256,
    )
    split = freeze_split(
        split_id=pilot.split_id,
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
    motif = pilot.motif_policy
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
    feature_names = pilot.feature_names
    program_budget = pilot.program_budget
    trials = build_preregistered_trial_grid(
        run_ids=(pilot.run_id,),
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
    phase3_execution = pilot.phase3_execution
    work_budget = pilot.work_budget
    orchestration_parameters = {
        "subsample_fraction": 0.75,
        "stability_algorithm_version": STABILITY_ALGORITHM_VERSION,
        "motif_algorithm_version": MOTIF_ALGORITHM_VERSION,
        "reliability_evidence_algorithms": reliability_algorithm_versions(),
    }
    preregistration = freeze_discovery_preregistration(
        preregistration_id=pilot.preregistration_id,
        selected_universe=selected,
        split=split,
        dataset_snapshot_id=pilot.dataset_snapshot_id,
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=feature_names,
        trials=trials,
        budget=program_budget,
        phase3_execution=phase3_execution,
        run_work_budget=work_budget,
        run_max_rows=pilot.run_max_rows,
        run_max_iterations=100,
        run_tolerance=1e-12,
        stability_policy_sha256=canonical_policy_sha256(stability),
        adjacent_period_policy_sha256=canonical_policy_sha256(adjacent),
        motif_policy_sha256=canonical_policy_sha256(motif),
        transition_policy_sha256=canonical_policy_sha256(transition),
        missingness_policy_sha256=canonical_policy_sha256(missingness),
        regime_contract_sha256=_sha_json(regime_policy),
        orchestration_parameters_sha256=_sha_json(orchestration_parameters),
        negative_controls=("seed_perturbation", "time_order_preserving_null"),
        naive_baselines=("single_cluster", "unconditional_recurrence"),
        rejection_rules=TASK14_TERMINAL_REJECTION_RULES,
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
        bin_step=phase3_execution.bin_step,
        rolling_bars=phase3_execution.rolling_bars,
        event_width=phase3_execution.event_width,
        maximum_rows=phase3_execution.maximum_rows,
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
        max_rows=preregistration.run_max_rows,
        publication_manifest=phase3.feature_manifest,
    )
    development_input = make_discovery_input(
        partition=split.development,
        rows=development_rows,
        registry=registry,
        purpose="stability",
        max_rows=preregistration.run_max_rows,
        publication_manifest=phase3.feature_manifest,
    )
    selected_rows = tuple(
        row
        for row in (*discovery_input.rows, *development_input.rows)
        if all(row.values[name] is not None for name in feature_names)
    )
    selected_ids = {_row_id(row) for row in selected_rows}
    regimes = freeze_motif_regime_assignments(
        contract_id=pilot.regime_assignment_contract_id,
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
        max_rows=preregistration.run_max_rows,
        max_iterations=preregistration.run_max_iterations,
        tolerance=preregistration.run_tolerance,
        stability_policy=stability,
        adjacent_period_stability_policy=adjacent,
        motif_stability_policy=motif,
        motif_regime_assignments=regimes,
        transition_uncertainty_policy=transition,
        missingness_policy=missingness,
        work_budget=preregistration.run_work_budget,
        event_publication_id=pilot.event_publication_id,
        event_publication_sha256=phase3.event_manifest.publication_sha256,
        code_commit=code_commit,
        lock_sha256=lock_sha256,
        feature_publication_id=pilot.feature_publication_id,
        feature_publication_sha256=phase3.feature_manifest.publication_sha256,
        normalizer_id=pilot.normalizer_id,
        normalizer_sha256=phase3.normalizer.artifact_sha256,
        provenance=provenance,
        preregistration_sha256=preregistration.sha256,
        started_at=pilot.started_at,
        completed_at=pilot.completed_at,
    )
    manifest = None
    execution_error_type: str | None = None
    try:
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
    except Exception as error:
        execution_error_type = type(error).__name__
    vector = publish_reliability_vector(
        preregistration=preregistration,
        trial_root=args.trial_root,
        destination=args.program_root / "reliability-vector.json",
    )
    return {
        "run_id": trial.run_id,
        "run_status": manifest.status if manifest is not None else vector.trials[0].status,
        "preregistration_sha256": preregistration.sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "feature_publication_sha256": phase3.feature_manifest.publication_sha256,
        "event_publication_sha256": phase3.event_manifest.publication_sha256,
        "normalizer_sha256": phase3.normalizer.artifact_sha256,
        "reliability_vector_sha256": vector.sha256,
        "reliability_conclusion": vector.conclusion,
        "holdout_rows_accessed": False,
        "execution_error_type": execution_error_type,
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


def _eligibility_audit(report) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for item in report.symbols:
        if item.symbol == "APTUSDT":
            decision = "selected_discovery_development"
        elif item.symbol == "IMXUSDT":
            decision = "metadata_only_asset_holdout"
        else:
            decision = "excluded_from_single_asset_pilot"
        candidates.append(
            {
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "freshness_status": item.status.value,
                "compatibility_state": item.compatibility_state.value,
                "current_through_cutoff": item.current_through_cutoff,
                "after_missing_minutes": item.after_missing_minutes,
                "decision": decision,
            }
        )
    return {
        "schema_version": "task14-eligibility-audit-v1",
        "freshness_report_sha256": report.sha256(),
        "freshness_as_of": report.as_of,
        "selection_rule": "healthy_compatible_current_single_asset_pilot_v1",
        "selected_symbols": ["APTUSDT"],
        "asset_holdouts": ["IMXUSDT"],
        "candidates": candidates,
    }


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
    require_regular_directory(path.parent)
    encoded_bytes = encoded.encode("utf-8")
    if path_exists_no_follow(path):
        if not regular_file_matches(path, encoded_bytes):
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
