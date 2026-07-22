from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import polars as pl

from market_structure_lab.data.canonical import CANONICAL_SCHEMA
from market_structure_lab.data.derived import LeakageAuditApproval, LeakageNegativePattern
from market_structure_lab.data.export import SnapshotIdentity, export_partitioned_snapshot
from market_structure_lab.data.segments import SegmentBoundary
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
from market_structure_lab.discovery.runs import DiscoveryWorkBudget
from market_structure_lab.discovery.splits import (
    PartitionRole,
    TimePartition,
    freeze_split,
)
from market_structure_lab.features.builtin import builtin_feature_registry
from market_structure_lab.experiments import (
    ArtifactIdentity,
    ExperimentConfig,
    ExperimentMode,
    TerminalStatus,
    TrialRange,
    save_experiment_result,
)


def _sha(character: str) -> str:
    return character * 64


def _write_promotion_receipt(path: Path) -> Path:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "RR-000008",
        "manifest_sha256": _sha("a"),
        "replacement_logical_sha256": _sha("b"),
        "canonical_logical_sha256": _sha("c"),
        "promoted_at": "2026-07-18T13:45:00Z",
        "coverage_interval_count": 1,
        "coverage_logical_sha256": "",
        "coverage": [
            {
                "symbol": "APTUSDT",
                "timeframe": "1m",
                "start_ms": 1738368000000,
                "end_ms": 1738368120000,
            }
        ],
    }
    payload["coverage_logical_sha256"] = hashlib.sha256(
        json.dumps(payload["coverage"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _freeze(path: Path, **changes: object):
    values: dict[str, object] = {
        "selection_id": "SU-000001",
        "promotion_receipt_path": path,
        "symbols": ("APTUSDT",),
        "timeframe": "1m",
        "start": datetime(2025, 2, 1, tzinfo=UTC),
        "end": datetime(2025, 6, 1, tzinfo=UTC),
        "gap_boundaries": (),
        "survivorship_policy": "point_in_time_freshness_recovered_only_v1",
        "optional_field_policy": "ignore_unavailable_optional_trade_fields_v1",
        "zero_volume_policy": "retain_observed_zero_volume_v1",
        "eligibility_audit_sha256": _sha("9"),
    }
    values.update(changes)
    return freeze_selected_universe(**values)


def test_selected_universe_binds_verified_promotion_and_exact_scope(tmp_path: Path) -> None:
    receipt = _write_promotion_receipt(tmp_path / "receipt.json")

    selected = _freeze(receipt)

    assert selected.promotion_run_id == "RR-000008"
    assert selected.promotion_manifest_sha256 == _sha("a")
    assert selected.promotion_replacement_logical_sha256 == _sha("b")
    assert selected.promotion_canonical_logical_sha256 == _sha("c")
    assert len(selected.promotion_receipt_artifact_sha256) == 64
    assert selected.symbols == ("APTUSDT",)
    assert selected.to_dict()["start"] == "2025-02-01T00:00:00Z"
    assert selected.to_dict()["end"] == "2025-06-01T00:00:00Z"
    assert len(selected.sha256) == 64


def test_selected_universe_identity_changes_with_scope_policy_or_boundaries(tmp_path: Path) -> None:
    receipt = _write_promotion_receipt(tmp_path / "receipt.json")
    baseline = _freeze(receipt)
    boundary = SegmentBoundary(
        symbol="APTUSDT",
        timeframe="1m",
        start=datetime(2025, 3, 1, tzinfo=UTC),
        end=datetime(2025, 3, 1, 0, 2, tzinfo=UTC),
        reason="canonical_missing_candles:2",
    )

    variants = (
        _freeze(receipt, end=datetime(2025, 5, 1, tzinfo=UTC)),
        _freeze(receipt, zero_volume_policy="exclude_observed_zero_volume_v1"),
        _freeze(receipt, gap_boundaries=(boundary,)),
    )

    assert all(item.sha256 != baseline.sha256 for item in variants)


def test_selected_universe_rejects_tampered_receipt_and_foreign_boundary(tmp_path: Path) -> None:
    receipt = _write_promotion_receipt(tmp_path / "receipt.json")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["canonical_logical_sha256"] = _sha("d")
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        _freeze(receipt)

    valid = _write_promotion_receipt(tmp_path / "valid.json")
    foreign = SegmentBoundary(
        symbol="IMXUSDT",
        timeframe="1m",
        start=datetime(2025, 3, 1, tzinfo=UTC),
        end=datetime(2025, 3, 1, 0, 1, tzinfo=UTC),
        reason="canonical_missing_candles:1",
    )
    with pytest.raises(ValueError, match="selected universe"):
        _freeze(valid, gap_boundaries=(foreign,))


def test_selected_universe_rejects_unsafe_or_noncanonical_scope(tmp_path: Path) -> None:
    receipt = _write_promotion_receipt(tmp_path / "receipt.json")

    with pytest.raises(ValueError, match="canonical deterministic ordering"):
        _freeze(receipt, symbols=("IMXUSDT", "APTUSDT"))
    with pytest.raises(ValueError, match="UTC"):
        _freeze(receipt, start=datetime(2025, 2, 1))
    with pytest.raises(ValueError, match="precede"):
        _freeze(
            receipt,
            start=datetime(2025, 6, 1, tzinfo=UTC),
            end=datetime(2025, 2, 1, tzinfo=UTC),
        )


def test_selected_universe_requires_the_verifying_factory(tmp_path: Path) -> None:
    selected = _freeze(_write_promotion_receipt(tmp_path / "receipt.json"))

    with pytest.raises(TypeError, match="freeze_selected_universe"):
        replace(selected, selection_id="SU-000002")


def _split():
    return freeze_split(
        split_id="task14-split-v1",
        discovery=TimePartition(
            role=PartitionRole.DISCOVERY,
            start=datetime(2025, 2, 1, tzinfo=UTC),
            end=datetime(2025, 4, 1, tzinfo=UTC),
            symbols=("APTUSDT",),
        ),
        development=TimePartition(
            role=PartitionRole.DEVELOPMENT,
            start=datetime(2025, 4, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
            symbols=("APTUSDT",),
        ),
        holdout=TimePartition(
            role=PartitionRole.HOLDOUT,
            start=datetime(2025, 6, 1, tzinfo=UTC),
            end=datetime(2025, 8, 1, tzinfo=UTC),
            symbols=("APTUSDT",),
        ),
        asset_holdouts=("IMXUSDT",),
    )


def _program_budget(maximum_trials: int = 4) -> DiscoveryProgramBudget:
    return DiscoveryProgramBudget(
        budget_id="task14-program-budget-v1",
        maximum_trials=maximum_trials,
        maximum_total_stability_fits=64,
        maximum_serialized_evidence_bytes=4 * 1024 * 1024,
    )


def _phase3_contract() -> Phase3ExecutionContract:
    return Phase3ExecutionContract(
        config_version="task14-auction-v1",
        bin_step=0.1,
        rolling_bars=20,
        event_width=1,
        maximum_rows=100,
    )


def _run_budget() -> DiscoveryWorkBudget:
    return DiscoveryWorkBudget(
        budget_id="task14-test-run-budget-v1",
        maximum_materialized_rows=200,
        maximum_feature_cells=1_000,
        maximum_pca_rows=100,
        maximum_pca_features=10,
        maximum_pca_cells=1_000,
        maximum_clusters=5,
        maximum_seeds=5,
        maximum_kmeans_iterations=100,
        maximum_total_stability_fits=20,
        maximum_serialized_evidence_bytes=4 * 1024 * 1024,
        maximum_bundle_entries=100,
    )


def test_trial_grid_is_exact_deterministic_and_bounded() -> None:
    trials = build_preregistered_trial_grid(
        run_ids=("DR-000701", "DR-000702", "DR-000703", "DR-000704"),
        pca_components=(2,),
        cluster_counts=(2, 3),
        seed_sets=((7, 11), (13, 17)),
        budget=_program_budget(),
    )

    assert tuple(item.run_id for item in trials) == (
        "DR-000701",
        "DR-000702",
        "DR-000703",
        "DR-000704",
    )
    assert {(item.clusters, item.seeds) for item in trials} == {
        (2, (7, 11)),
        (2, (13, 17)),
        (3, (7, 11)),
        (3, (13, 17)),
    }
    with pytest.raises(ValueError, match="trial budget"):
        build_preregistered_trial_grid(
            run_ids=("DR-000701", "DR-000702", "DR-000703", "DR-000704"),
            pca_components=(2,),
            cluster_counts=(2, 3),
            seed_sets=((7, 11), (13, 17)),
            budget=_program_budget(maximum_trials=3),
        )


def test_trial_grid_rejects_oversized_cardinality_before_iteration() -> None:
    class ExplodingSequence:
        def __len__(self) -> int:
            return 10_000

        def __getitem__(self, _index: int) -> int:
            raise AssertionError("oversized trial dimension was iterated")

    with pytest.raises(ValueError, match="trial budget"):
        build_preregistered_trial_grid(
            run_ids=("DR-000701",),
            pca_components=ExplodingSequence(),
            cluster_counts=(2,),
            seed_sets=((7, 11),),
            budget=_program_budget(maximum_trials=4),
        )


def test_preregistration_binds_outcome_blind_policy_and_exact_trials(tmp_path: Path) -> None:
    selected = _freeze(_write_promotion_receipt(tmp_path / "receipt.json"))
    budget = _program_budget(maximum_trials=1)
    trials = build_preregistered_trial_grid(
        run_ids=("DR-000701",),
        pca_components=(2,),
        cluster_counts=(3,),
        seed_sets=((7, 11, 13),),
        budget=budget,
    )

    preregistration = freeze_discovery_preregistration(
        preregistration_id="PG-000001",
        selected_universe=selected,
        split=_split(),
        dataset_snapshot_id="DS-000701",
        feature_set_id="FS-000701",
        registry_sha256=_sha("1"),
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=("auction_location", "poc_migration_bins", "value_width_bins"),
        trials=trials,
        budget=budget,
        phase3_execution=_phase3_contract(),
        run_work_budget=_run_budget(),
        run_max_rows=100,
        run_max_iterations=100,
        run_tolerance=1e-12,
        stability_policy_sha256=_sha("2"),
        adjacent_period_policy_sha256=_sha("8"),
        motif_policy_sha256=_sha("3"),
        transition_policy_sha256=_sha("4"),
        missingness_policy_sha256=_sha("5"),
        regime_contract_sha256=_sha("6"),
        orchestration_parameters_sha256=_sha("a"),
        negative_controls=("seed_perturbation", "time_order_preserving_null"),
        naive_baselines=("single_cluster", "unconditional_recurrence"),
        rejection_rules=("missingness_policy_violation", "stability_policy_rejection"),
        code_commit="2e2501be5bb531deabd8bb5790150464167c3e34",
        lock_sha256=_sha("7"),
    )

    payload = preregistration.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["trial_count"] == 1
    assert payload["holdout_metadata"]["start"] == "2025-06-01T00:00:00Z"
    assert payload["holdout_metadata"]["asset_holdouts"] == ["IMXUSDT"]
    assert "outcome" not in encoded
    assert "accuracy" not in encoded
    assert "profit" not in encoded
    assert len(preregistration.sha256) == 64
    changed = freeze_discovery_preregistration(
        preregistration_id="PG-000001",
        selected_universe=selected,
        split=_split(),
        dataset_snapshot_id="DS-000701",
        feature_set_id="FS-000701",
        registry_sha256=_sha("1"),
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=("auction_location", "poc_migration_bins", "value_width_bins"),
        trials=trials,
        budget=budget,
        phase3_execution=_phase3_contract(),
        run_work_budget=_run_budget(),
        run_max_rows=100,
        run_max_iterations=100,
        run_tolerance=1e-12,
        stability_policy_sha256=_sha("2"),
        adjacent_period_policy_sha256=_sha("8"),
        motif_policy_sha256=_sha("3"),
        transition_policy_sha256=_sha("4"),
        missingness_policy_sha256=_sha("5"),
        regime_contract_sha256=_sha("6"),
        orchestration_parameters_sha256=_sha("a"),
        negative_controls=("seed_perturbation",),
        naive_baselines=("single_cluster", "unconditional_recurrence"),
        rejection_rules=("missingness_policy_violation", "stability_policy_rejection"),
        code_commit="2e2501be5bb531deabd8bb5790150464167c3e34",
        lock_sha256=_sha("7"),
    )
    assert changed.sha256 != preregistration.sha256


def test_phase3_publications_bind_snapshot_features_normalizer_and_events(
    tmp_path: Path,
) -> None:
    receipt = _write_promotion_receipt(tmp_path / "receipt.json")
    start = datetime(2025, 2, 1, tzinfo=UTC)
    discovery_end = datetime(2025, 2, 1, 0, 30, tzinfo=UTC)
    end = datetime(2025, 2, 1, 1, 0, tzinfo=UTC)
    selected = _freeze(receipt, start=start, end=end)
    split = freeze_split(
        split_id="task14-small-split-v1",
        discovery=TimePartition(PartitionRole.DISCOVERY, start, discovery_end, ("APTUSDT",)),
        development=TimePartition(PartitionRole.DEVELOPMENT, discovery_end, end, ("APTUSDT",)),
        holdout=TimePartition(
            PartitionRole.HOLDOUT,
            datetime(2025, 2, 1, 1, 0, tzinfo=UTC),
            datetime(2025, 2, 1, 2, 0, tzinfo=UTC),
            ("APTUSDT",),
        ),
        asset_holdouts=("IMXUSDT",),
    )
    registry = builtin_feature_registry()
    budget = _program_budget(maximum_trials=1)
    trials = build_preregistered_trial_grid(
        run_ids=("DR-000701",),
        pca_components=(2,),
        cluster_counts=(3,),
        seed_sets=((7, 11, 13),),
        budget=budget,
    )
    preregistration = freeze_discovery_preregistration(
        preregistration_id="PG-000001",
        selected_universe=selected,
        split=split,
        dataset_snapshot_id="DS-000701",
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=("log_return_1", "range_close_fraction"),
        trials=trials,
        budget=budget,
        phase3_execution=_phase3_contract(),
        run_work_budget=_run_budget(),
        run_max_rows=100,
        run_max_iterations=100,
        run_tolerance=1e-12,
        stability_policy_sha256=_sha("2"),
        adjacent_period_policy_sha256=_sha("8"),
        motif_policy_sha256=_sha("3"),
        transition_policy_sha256=_sha("4"),
        missingness_policy_sha256=_sha("5"),
        regime_contract_sha256=_sha("6"),
        orchestration_parameters_sha256=_sha("a"),
        negative_controls=("seed_perturbation",),
        naive_baselines=("single_cluster",),
        rejection_rules=("stability_policy_rejection",),
        code_commit="2e2501be5bb531deabd8bb5790150464167c3e34",
        lock_sha256=hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
    )
    rows = [
        (
            start.replace(minute=minute),
            "APTUSDT",
            "1m",
            10.0 + minute / 100,
            10.2 + minute / 100,
            9.9 + minute / 100,
            10.1 + minute / 100,
            100.0 + minute,
        )
        for minute in range(60)
    ]
    frame = pl.DataFrame(rows, schema=CANONICAL_SCHEMA, orient="row")
    snapshot = export_partitioned_snapshot(
        [frame],
        output_root=tmp_path / "snapshots",
        identity=SnapshotIdentity(
            dataset_version="DS-000701",
            dump_sha256="a" * 64,
            recovery_sha256="b" * 64,
            mapping_version="candles-v1",
            config_version="task14-auction-v1",
            code_commit=preregistration.code_commit,
            research_binding=selected.snapshot_research_binding,
        ),
        boundaries=(),
        expected_row_count=60,
    )
    approval = LeakageAuditApproval(
        schema_version=1,
        feature_registry_sha256=registry.sha256,
        reviewer_id="task14-independent-contract-review-v1",
        review_artifact_sha256=hashlib.sha256(b"Task 14 independent contract review").hexdigest(),
        field_test_evidence=tuple(
            (name, hashlib.sha256(f"task14-field:{name}".encode()).hexdigest())
            for name in registry.names
        ),
        negative_test_evidence=tuple(
            (pattern, hashlib.sha256(f"task14-negative:{pattern.value}".encode()).hexdigest())
            for pattern in LeakageNegativePattern
        ),
    )

    bundle = build_phase3_publications(
        preregistration=preregistration,
        selected_universe=selected,
        snapshot_directory=tmp_path / "snapshots" / "dataset_version=DS-000701",
        snapshot_manifest=snapshot,
        output_root=tmp_path / "derived",
        work_root=tmp_path / "work",
        registry=registry,
        leakage_approval=approval,
        bin_step=0.1,
        rolling_bars=20,
        event_width=1,
        maximum_rows=100,
        lockfile_bytes=Path("uv.lock").read_bytes(),
    )

    assert bundle.feature_manifest.row_count == 60
    assert bundle.event_manifest.row_count == 28
    assert bundle.normalizer.selected_features == preregistration.feature_names
    assert bundle.feature_manifest.identity.dataset_snapshot_sha256 == snapshot.snapshot_sha256
    assert (
        bundle.event_manifest.source_feature_publication_sha256
        == bundle.feature_manifest.publication_sha256
    )

    holdout_frame = pl.DataFrame(
        [
            (
                end,
                "APTUSDT",
                "1m",
                11.0,
                11.2,
                10.9,
                11.1,
                100.0,
            )
        ],
        schema=CANONICAL_SCHEMA,
        orient="row",
    )
    foreign_snapshot = export_partitioned_snapshot(
        [holdout_frame],
        output_root=tmp_path / "foreign-snapshots",
        identity=snapshot.identity,
        boundaries=(),
    )
    with pytest.raises(ValueError, match="outside the frozen selected universe"):
        build_phase3_publications(
            preregistration=preregistration,
            selected_universe=selected,
            snapshot_directory=(tmp_path / "foreign-snapshots" / "dataset_version=DS-000701"),
            snapshot_manifest=foreign_snapshot,
            output_root=tmp_path / "foreign-derived",
            work_root=tmp_path / "foreign-work",
            registry=registry,
            leakage_approval=approval,
            bin_step=0.1,
            rolling_bars=20,
            event_width=1,
            maximum_rows=100,
            lockfile_bytes=Path("uv.lock").read_bytes(),
        )


def test_reliability_vector_reconciles_exact_terminal_trial_receipts(tmp_path: Path) -> None:
    selected = _freeze(_write_promotion_receipt(tmp_path / "receipt.json"))
    budget = _program_budget(maximum_trials=1)
    trials = build_preregistered_trial_grid(
        run_ids=("DR-000701",),
        pca_components=(2,),
        cluster_counts=(3,),
        seed_sets=((7, 11),),
        budget=budget,
    )
    stability = {"policy_id": "stability-test-v1"}
    adjacent = {"policy_id": "adjacent-test-v1"}
    motif = {"policy_id": "motif-test-v1"}
    transition = {"policy_id": "transition-test-v1"}
    missingness = {"policy_id": "missingness-test-v1"}
    regime = {
        "algorithm_version": "regime-test-v1",
        "information_policy": "contemporaneous",
        "regime_universe": ["all_observed"],
    }
    orchestration = {
        "subsample_fraction": 0.75,
        "stability_algorithm_version": "stability-test-v1",
        "motif_algorithm_version": "motif-test-v1",
    }
    preregistration = freeze_discovery_preregistration(
        preregistration_id="PG-000001",
        selected_universe=selected,
        split=_split(),
        dataset_snapshot_id="DS-000701",
        feature_set_id="FS-000701",
        registry_sha256=_sha("1"),
        normalizer_policy_id="robust-discovery-fit-only-v1",
        feature_names=("auction_location", "poc_migration_bins"),
        trials=trials,
        budget=budget,
        phase3_execution=_phase3_contract(),
        run_work_budget=_run_budget(),
        run_max_rows=100,
        run_max_iterations=100,
        run_tolerance=1e-12,
        stability_policy_sha256=canonical_policy_sha256(stability),
        adjacent_period_policy_sha256=canonical_policy_sha256(adjacent),
        motif_policy_sha256=canonical_policy_sha256(motif),
        transition_policy_sha256=canonical_policy_sha256(transition),
        missingness_policy_sha256=canonical_policy_sha256(missingness),
        regime_contract_sha256=canonical_policy_sha256(regime),
        orchestration_parameters_sha256=canonical_policy_sha256(orchestration),
        negative_controls=("seed_perturbation",),
        naive_baselines=("single_cluster",),
        rejection_rules=("stability_policy_rejection",),
        code_commit="2e2501be5bb531deabd8bb5790150464167c3e34",
        lock_sha256=_sha("7"),
    )
    identity = ArtifactIdentity("artifact-v1", _sha("8"))
    dataset_identity = ArtifactIdentity(preregistration.dataset_snapshot_id, _sha("8"))
    registry_identity = ArtifactIdentity("registry-v1", preregistration.registry_sha256)
    config = ExperimentConfig(
        run_id="DR-000701",
        mode=ExperimentMode.DISCOVERY,
        dataset_snapshot=dataset_identity,
        feature_publication=identity,
        feature_registry=registry_identity,
        normalizer=identity,
        frozen_split={"sha256": preregistration.split.sha256},
        detector_version="task14-detector-v1",
        candidate_id=None,
        candidate_version=None,
        code_commit=preregistration.code_commit,
        lock_sha256=preregistration.lock_sha256,
        canonical_config={
            "preregistration_sha256": preregistration.sha256,
            "run_id": "DR-000701",
            "dataset_snapshot_id": preregistration.dataset_snapshot_id,
            "feature_set_id": preregistration.feature_set_id,
            "registry_sha256": preregistration.registry_sha256,
            "config_version": preregistration.phase3_execution.config_version,
            "split": {"sha256": preregistration.split.sha256},
            "feature_names": list(preregistration.feature_names),
            "pca_components": trials[0].pca_components,
            "clusters": trials[0].clusters,
            "seeds": list(trials[0].seeds),
            "max_rows": preregistration.run_max_rows,
            "max_iterations": preregistration.run_max_iterations,
            "tolerance": preregistration.run_tolerance,
            "stability_policy": stability,
            "adjacent_period_stability_policy": adjacent,
            "motif_stability_policy": motif,
            "transition_uncertainty_policy": transition,
            "missingness_policy": missingness,
            "motif_regime_assignments": {**regime, "assignments": []},
            "work_budget": {
                "schema_version": "discovery-work-budget-v1",
                **asdict(preregistration.run_work_budget),
                "sha256": preregistration.run_work_budget.sha256,
            },
            "orchestration_parameters": orchestration,
            "code_commit": preregistration.code_commit,
            "lock_sha256": preregistration.lock_sha256,
        },
        seed=7,
        symbols=("APTUSDT",),
        timeframes=("1m",),
        ranges=(
            TrialRange(
                "APTUSDT",
                "1m",
                "2025-02-01T00:00:00Z",
                "2025-06-01T00:00:00Z",
            ),
        ),
        parent_ids=(),
        metrics_schema={"status": "string"},
    )
    bad_config = replace(
        config,
        canonical_config={**config.canonical_config, "clusters": 999},
    )
    save_experiment_result(
        config=bad_config,
        status=TerminalStatus.REJECTED,
        metrics={"status": "rejected_unstable"},
        conclusion="Mismatched configuration must not count.",
        started_at=datetime(2026, 7, 22, tzinfo=UTC),
        completed_at=datetime(2026, 7, 22, 0, 1, tzinfo=UTC),
        root=tmp_path / "bad-trials",
    )
    with pytest.raises(ValueError, match="clusters"):
        publish_reliability_vector(
            preregistration=preregistration,
            trial_root=tmp_path / "bad-trials",
            destination=tmp_path / "bad-reliability-vector.json",
        )
    save_experiment_result(
        config=config,
        status=TerminalStatus.REJECTED,
        metrics={"status": "rejected_unstable"},
        conclusion="Frozen detector stability policy rejected this configuration.",
        started_at=datetime(2026, 7, 22, tzinfo=UTC),
        completed_at=datetime(2026, 7, 22, 0, 1, tzinfo=UTC),
        root=tmp_path / "trials",
    )

    vector = publish_reliability_vector(
        preregistration=preregistration,
        trial_root=tmp_path / "trials",
        destination=tmp_path / "reliability-vector.json",
    )

    assert vector.attempted_trial_count == 1
    assert vector.status_counts == (("rejected", 1),)
    assert vector.conclusion == "rejected"
    assert vector.trials[0].run_id == "DR-000701"
    encoded = (tmp_path / "reliability-vector.json").read_text(encoding="utf-8")
    assert "outcome" not in encoded
    assert "accuracy" not in encoded
