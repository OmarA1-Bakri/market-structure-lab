from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
import market_structure_lab.data.derived as derived_data
import market_structure_lab.data.export as snapshot_export
import market_structure_lab.discovery.runs as discovery_runs

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.data.canonical import CANONICAL_SCHEMA
from market_structure_lab.data.derived import (
    DerivedPublicationIdentity,
    publish_feature_rows,
)
from market_structure_lab.data.export import (
    SnapshotIdentity,
    export_partitioned_snapshot,
)
from market_structure_lab.discovery import (
    AIInterpretation,
    AdjacentPeriodStabilityPolicy,
    BehaviourEvidencePack,
    DiscoveryRunConfig,
    MotifStabilityPolicy,
    PartitionRole,
    StabilityPolicy,
    TimePartition,
    freeze_split,
    freeze_motif_regime_assignments,
    make_discovery_input,
    publish_ai_interpretations,
    run_discovery,
)
from market_structure_lab.discovery.splits import freeze_discovery_provenance
from market_structure_lab.features.normalization import (
    PartitionRole as NormalizerPartitionRole,
    RobustNormalizer,
    TrainingPartition,
    fit_robust_normalizer,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)
from market_structure_lab.experiments import (
    ExperimentMode,
    TerminalStatus,
    read_trial_ledger,
    verify_trial_receipt,
)


def _registry() -> FeatureRegistry:
    return FeatureRegistry(
        "FS-000501",
        (
            FeatureDefinition(
                name="auction_location",
                definition="Normalized auction location.",
                family=FeatureFamily.AUCTION,
                value_kind=FeatureValueKind.FLOAT,
                units="ratio",
                required_prior_observations=0,
                missing_policy=MissingPolicy.ERROR,
                version="1.0.0",
                leakage_class=LeakageClass.AT_CUTOFF,
            ),
            FeatureDefinition(
                name="volume_change",
                definition="Trailing normalized volume change.",
                family=FeatureFamily.SEQUENCE,
                value_kind=FeatureValueKind.FLOAT,
                units="ratio",
                required_prior_observations=1,
                missing_policy=MissingPolicy.ERROR,
                version="1.0.0",
                leakage_class=LeakageClass.TRAILING_ONLY,
            ),
        ),
    )


def _row(
    timestamp: datetime,
    symbol: str,
    value: float,
    registry: FeatureRegistry,
) -> FeatureRow:
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp + timedelta(minutes=1),
        symbol=symbol,
        timeframe="1m",
        segment_id=0,
        dataset_version="DS-000501",
        config_version="cfg-1",
        profile_version="profile-1",
        window_policy_id="window-1",
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
        values={"auction_location": value, "volume_change": value / 10.0},
    )


def _feature_row_id(row: FeatureRow) -> str:
    timestamp = row.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return f"{row.symbol}|{row.timeframe}|{timestamp}"


def _controlled_repository(
    root: Path,
    lockfile_bytes: bytes,
    *,
    track_module: bool = True,
) -> tuple[Path, str]:
    repository = root / "repository"
    if not repository.exists():
        repository.mkdir(parents=True)
        module_path = repository / "src/market_structure_lab/discovery/runs.py"
        module_path.parent.mkdir(parents=True)
        module_path.write_text("# controlled executing module fixture\n", encoding="utf-8")
        if not track_module:
            (repository / ".gitignore").write_text(
                "src/market_structure_lab/discovery/runs.py\n",
                encoding="utf-8",
            )
        (repository / "uv.lock").write_bytes(lockfile_bytes)
        (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Fixture"],
            check=True,
        )
        tracked = ["uv.lock", "tracked.txt", "src"] if track_module else [
            ".gitignore",
            "uv.lock",
            "tracked.txt",
        ]
        subprocess.run(["git", "-C", str(repository), "add", *tracked], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
            check=True,
        )
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, commit


def _fixture(
    tmp_path,
    *,
    rejected: bool = False,
    declared_code_commit: str | None = None,
    normalizer_row_delta: float = 0.0,
    normalizer_symbols: tuple[str, ...] | None = None,
    track_runtime_module: bool = True,
    split_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
    minimum_asset_coverage: float = 0.0,
    regime_assignment_mode: str = "complete",
):
    registry = _registry()
    discovery_partition = TimePartition(
        PartitionRole.DISCOVERY,
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        split_symbols,
    )
    development_partition = TimePartition(
        PartitionRole.DEVELOPMENT,
        datetime(2025, 1, 2, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
        split_symbols,
    )
    holdout_partition = TimePartition(
        PartitionRole.HOLDOUT,
        datetime(2025, 1, 4, tzinfo=UTC),
        datetime(2025, 1, 5, tzinfo=UTC),
        split_symbols,
    )
    split = freeze_split(
        split_id="phase4-fixture-v1",
        discovery=discovery_partition,
        development=development_partition,
        holdout=holdout_partition,
    )
    discovery_rows = tuple(
        _row(
            discovery_partition.start + timedelta(minutes=index),
            symbol,
            value,
            registry,
        )
        for symbol in ("BTCUSDT", "ETHUSDT")
        for index, value in enumerate((-10.3, -10.2, -10.1, 10.1, 10.2, 10.3))
    )
    development_rows = tuple(
        _row(timestamp, symbol, value, registry)
        for symbol in ("BTCUSDT", "ETHUSDT")
        for timestamp, value in (
            (datetime(2025, 1, 2, 0, 0, tzinfo=UTC), -10.2),
            (datetime(2025, 1, 2, 0, 1, tzinfo=UTC), 10.2),
            (datetime(2025, 1, 3, 0, 0, tzinfo=UTC), -9.9),
            (datetime(2025, 1, 3, 0, 1, tzinfo=UTC), 9.9),
        )
    )
    lockfile_bytes = b"version = 1\n"
    lock_sha256 = hashlib.sha256(lockfile_bytes).hexdigest()
    input_root = tmp_path.parent / f"{tmp_path.name}-inputs"
    repository_root, actual_code_commit = _controlled_repository(
        input_root,
        lockfile_bytes,
        track_module=track_runtime_module,
    )
    code_commit = declared_code_commit or actual_code_commit
    snapshot_root = input_root / "snapshots"
    snapshot_candles = pl.DataFrame(
        [
            (
                datetime(2025, 1, 1, tzinfo=UTC),
                "BTCUSDT",
                "1m",
                100.0,
                101.0,
                99.0,
                100.5,
                10.0,
            )
        ],
        schema=CANONICAL_SCHEMA,
        orient="row",
    )
    snapshot_manifest = export_partitioned_snapshot(
        (snapshot_candles,),
        output_root=snapshot_root,
        identity=SnapshotIdentity(
            dataset_version="DS-000501",
            dump_sha256=hashlib.sha256(b"dump").hexdigest(),
            recovery_sha256=hashlib.sha256(b"recovery").hexdigest(),
            mapping_version="mapping-v1",
            config_version="cfg-1",
            code_commit=code_commit,
        ),
    )
    snapshot_directory = snapshot_root / "dataset_version=DS-000501"
    normalizer_rows = tuple(
        replace(
            row,
            values={name: float(value) + normalizer_row_delta for name, value in row.values.items()},
        )
        for row in discovery_rows
    )
    normalizer = fit_robust_normalizer(
        normalizer_rows,
        registry,
        TrainingPartition(
            split_id=split.split_id,
            start=discovery_partition.start,
            end=discovery_partition.end,
            role=NormalizerPartitionRole.TRAINING,
            symbols=normalizer_symbols or discovery_partition.symbols,
        ),
        "DS-000501",
        selected_features=("auction_location", "volume_change"),
    )
    normalizer_artifact = normalizer.canonical_json()
    feature_publication_root = input_root / "features"
    feature_publication = publish_feature_rows(
        sorted(
            discovery_rows + development_rows,
            key=lambda row: (row.symbol, row.timeframe, row.timestamp),
        ),
        output_root=feature_publication_root,
        identity=DerivedPublicationIdentity(
            dataset_version="DS-000501",
            dataset_snapshot_sha256=snapshot_manifest.snapshot_sha256,
            feature_set_id=registry.feature_set_id,
            feature_registry_sha256=registry.sha256,
            config_version="cfg-1",
            profile_version="profile-1",
            window_policy_id="window-1",
            event_version="events-v1",
            normalizer_artifact_sha256=normalizer.artifact_sha256,
            code_commit=code_commit,
            uv_lock_sha256=lock_sha256,
        ),
        registry=registry,
        max_rows_per_part=4,
    )
    feature_publication_directory = (
        feature_publication_root
        / "dataset_version=DS-000501"
        / f"feature_set={registry.feature_set_id}"
        / "features"
    )
    discovery = make_discovery_input(
        partition=discovery_partition,
        rows=discovery_rows,
        registry=registry,
        purpose="fit",
        max_rows=20,
        publication_manifest=feature_publication,
    )
    development = make_discovery_input(
        partition=development_partition,
        rows=development_rows,
        registry=registry,
        purpose="stability",
        max_rows=20,
        publication_manifest=feature_publication,
    )
    regime_pairs = [
        (
            _feature_row_id(row),
            "balanced" if row.symbol == "BTCUSDT" else "expanding",
        )
        for row in (*discovery.rows, *development.rows)
    ]
    if regime_assignment_mode == "missing":
        regime_pairs.pop()
    elif regime_assignment_mode == "extra":
        regime_pairs.append(("UNKNOWN|1m|2025-01-01T00:00:00Z", "balanced"))
    elif regime_assignment_mode != "complete":
        raise ValueError("unsupported regime_assignment_mode fixture")
    regime_assignments = freeze_motif_regime_assignments(
        contract_id="software-test-regimes-v1",
        algorithm_version="symbol-partition-fixture-v1",
        information_policy="contemporaneous",
        outcome_policy="outcome_blind",
        regime_universe=("balanced", "expanding"),
        assignments=regime_pairs,
    )
    policy = (
        StabilityPolicy(1.0, 1.0, 0.0, 1.0, 1.0)
        if rejected
        else StabilityPolicy(-1.0, -1.0, 1.0, minimum_asset_coverage, -1.0)
    )
    provenance = freeze_discovery_provenance(
        snapshot_manifest=snapshot_manifest,
        feature_publication=feature_publication,
        registry=registry,
        normalizer_artifact=normalizer_artifact,
        split=split,
        feature_names=("auction_location", "volume_change"),
        discovery=discovery,
        development=development,
        code_commit=code_commit,
        lockfile_bytes=lockfile_bytes,
        motif_regime_assignment_sha256=regime_assignments.sha256,
    )
    config = DiscoveryRunConfig(
        run_id="DR-000502" if rejected else "DR-000501",
        dataset_snapshot_id="DS-000501",
        dataset_snapshot_sha256=snapshot_manifest.snapshot_sha256,
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        config_version="cfg-1",
        split=split,
        feature_names=("auction_location", "volume_change"),
        pca_components=1,
        clusters=2,
        seeds=(7, 11),
        max_rows=20,
        max_iterations=100,
        tolerance=1e-12,
        stability_policy=policy,
        adjacent_period_stability_policy=AdjacentPeriodStabilityPolicy(
            policy_id="software-test-run-adjacent-period-v1",
            policy_purpose="software_fixture",
            maximum_centroid_displacement=1.0,
            maximum_within_cluster_scale_change=1.0,
            maximum_assignment_margin_drift=2.0,
            minimum_cluster_event_support=1,
        ),
        motif_stability_policy=MotifStabilityPolicy(
            policy_id="software-test-run-motifs-v1",
            policy_purpose="software_fixture",
            window_lengths=(2, 3),
            exclusion_zones=(1, 2),
            tie_seeds=(7, 11),
            tie_policies=("canonical", "seeded_hash"),
            subsample_fraction=0.75,
            distance_multipliers=(0.9, 1.0, 1.1),
            maximum_distance=0.25,
            max_windows=20,
            top_k=3,
            minimum_seed_rank_agreement=0.5,
            minimum_subsample_agreement=0.0,
            minimum_parameter_agreement=0.1,
            minimum_recurrence_support=1,
            minimum_asset_support=1,
            minimum_period_support=1,
            minimum_regime_support=1,
        ),
        motif_regime_assignments=regime_assignments,
        code_commit=code_commit,
        lock_sha256=lock_sha256,
        feature_publication_id="FP-000501",
        feature_publication_sha256=feature_publication.publication_sha256,
        normalizer_id="NZ-000501",
        normalizer_sha256=normalizer.artifact_sha256,
        provenance=provenance,
        started_at=datetime(2026, 7, 17, 3, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 17, 3, 1, tzinfo=UTC),
    )
    return {
        "config": config,
        "discovery": discovery,
        "development": development,
        "registry": registry,
        "snapshot_directory": snapshot_directory,
        "snapshot_manifest": snapshot_manifest,
        "feature_publication_directory": feature_publication_directory,
        "feature_publication": feature_publication,
        "normalizer_artifact": normalizer_artifact,
        "lockfile_bytes": lockfile_bytes,
        "repository_root": repository_root,
        "event_ids": tuple(f"EV-{index:02d}" for index in range(12)),
        "durations_seconds": (60.0,) * 12,
        "output_root": tmp_path,
    }


def _run(arguments):
    supplied = dict(arguments)
    repository_root = supplied.pop("repository_root")
    module_path = repository_root / "src/market_structure_lab/discovery/runs.py"
    with patch.object(discovery_runs, "__file__", str(module_path)):
        return run_discovery(**supplied)


def _interpretation(behaviour) -> AIInterpretation:
    return AIInterpretation(
        behaviour_id=behaviour.behaviour_id,
        neutral_name="Recurring Auction Configuration",
        description="A frozen outcome-blind feature configuration.",
        candidate_mechanism_inference="Inference: the configuration may reflect local balance.",
        falsifiable_hypothesis="The detector will recur across untouched assets.",
        detector_fields=tuple(
            distribution.feature_name for distribution in behaviour.feature_distributions
        ),
        proposed_horizon="next 4 observed events",
        proposed_metrics=("occurrence_rate", "asset_coverage"),
        spuriousness_reasons=("small sample",),
        provider="openai",
        model="gpt-test",
        prompt_sha256=hashlib.sha256(b"prompt").hexdigest(),
        temperature=0.0,
        generated_at=datetime(2025, 1, 5, tzinfo=UTC),
        response_sha256=hashlib.sha256(behaviour.behaviour_id.encode()).hexdigest(),
    )


def test_discovery_run_is_atomic_reproducible_and_idempotent(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    provenance = arguments["config"].provenance

    assert provenance is not None
    assert provenance.feature_partitions
    assert provenance.feature_names == ("auction_location", "volume_change")
    assert arguments["feature_publication"].max_buffered_rows <= 4

    first = _run(arguments)
    first_bytes = {
        path.relative_to(tmp_path / first.run_id): path.read_bytes()
        for path in (tmp_path / first.run_id).rglob("*")
        if path.is_file()
    }
    replay = _run(arguments)

    assert first == replay
    assert first.status == "completed"
    assert len(first.behaviours) == 2
    assert first.transition_matrix.rows
    assert first.transition_matrix.boundary_evidence.raw_observation_count == 12
    assert first.transition_matrix.boundary_evidence.dwell_run_count < 12
    published_manifest = json.loads(
        (tmp_path / first.run_id / "manifest.json").read_text(encoding="utf-8")
    )
    published_transitions = json.loads(
        (tmp_path / first.run_id / "transitions.json").read_text(encoding="utf-8")
    )
    published_config = json.loads(
        (tmp_path / first.run_id / "config.json").read_text(encoding="utf-8")
    )
    published_motifs = json.loads(
        (tmp_path / first.run_id / "motifs.json").read_text(encoding="utf-8")
    )
    published_metrics = json.loads(
        (tmp_path / first.run_id / "metrics.json").read_text(encoding="utf-8")
    )
    assert published_manifest["schema_version"] == "discovery-run-manifest-v2"
    assert published_manifest["transition_matrix"] == published_transitions
    assert published_config["adjacent_period_stability_policy"] == {
        "maximum_assignment_margin_drift": 2.0,
        "maximum_centroid_displacement": 1.0,
        "maximum_within_cluster_scale_change": 1.0,
        "minimum_cluster_event_support": 1,
        "policy_id": "software-test-run-adjacent-period-v1",
        "policy_purpose": "software_fixture",
    }
    assert published_motifs["algorithm_version"] == "boundary-safe-multivariate-motifs-v3"
    assert published_motifs["feature_names"] == ["auction_location", "volume_change"]
    assert published_motifs["development_regime_universe"] == ["balanced", "expanding"]
    assert "unclassified" not in json.dumps(published_motifs)
    assert published_config["motif_regime_assignments"]["sha256"] == (
        provenance.motif_regime_assignment_sha256
    )
    assert all(
        candidate["accepted"] is (not candidate["rejection_reasons"])
        for candidate in published_motifs["candidates"]
    )
    assert published_metrics["motif_candidates"] == len(published_motifs["candidates"])
    assert published_metrics["motifs_published"] + published_metrics["motifs_rejected"] == (
        published_metrics["motif_candidates"]
    )
    assert replay.transition_matrix == first.transition_matrix
    receipt = verify_trial_receipt(tmp_path / first.run_id)
    assert receipt.mode is ExperimentMode.DISCOVERY
    assert receipt.status is TerminalStatus.COMPLETED
    assert receipt.feature_publication.identifier == "FP-000501"
    assert receipt.normalizer.identifier == "NZ-000501"
    assert receipt.hypothesis is None
    assert receipt.outcome_policy is None
    assert receipt.cost_policy is None
    assert not (tmp_path / ".DR-000501.staging").exists()
    assert first_bytes == {
        path.relative_to(tmp_path / first.run_id): path.read_bytes()
        for path in (tmp_path / first.run_id).rglob("*")
        if path.is_file()
    }


def test_motif_rejection_does_not_reject_independent_cluster_behaviours(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    config = arguments["config"]
    arguments["config"] = replace(
        config,
        motif_stability_policy=replace(
            config.motif_stability_policy,
            minimum_asset_support=3,
        ),
    )

    manifest = _run(arguments)
    metrics = json.loads(
        (tmp_path / manifest.run_id / "metrics.json").read_text(encoding="utf-8")
    )
    motifs = json.loads(
        (tmp_path / manifest.run_id / "motifs.json").read_text(encoding="utf-8")
    )

    assert manifest.status == "completed"
    assert len(manifest.behaviours) == 2
    assert metrics["motifs_published"] == 0
    assert metrics["motifs_rejected"] == metrics["motif_candidates"]
    assert motifs["candidates"]
    assert all(not candidate["accepted"] for candidate in motifs["candidates"])


@pytest.mark.parametrize("regime_assignment_mode", ["missing", "extra"])
def test_run_rejects_incomplete_or_unknown_regime_row_ids_before_motif_construction(
    tmp_path,
    regime_assignment_mode: str,
) -> None:
    arguments = _fixture(tmp_path, regime_assignment_mode=regime_assignment_mode)

    with pytest.raises(ValueError, match="exactly cover selected rows"):
        _run(arguments)


def test_adjacent_period_policy_is_bound_into_run_identity(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    _run(arguments)
    config = arguments["config"]
    changed = dict(arguments)
    changed["config"] = replace(
        config,
        adjacent_period_stability_policy=replace(
            config.adjacent_period_stability_policy,
            maximum_centroid_displacement=1.1,
        ),
    )

    with pytest.raises(RuntimeError, match="identity conflict"):
        _run(changed)


def test_regime_assignment_policy_and_hash_are_bound_to_verified_provenance(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    config = arguments["config"]
    changed_contract = freeze_motif_regime_assignments(
        contract_id=config.motif_regime_assignments.contract_id,
        algorithm_version="changed-contemporaneous-fixture-v2",
        information_policy="contemporaneous",
        outcome_policy="outcome_blind",
        regime_universe=config.motif_regime_assignments.regime_universe,
        assignments=config.motif_regime_assignments.assignments,
    )

    with pytest.raises(ValueError, match="motif_regime_assignment_sha256"):
        replace(config, motif_regime_assignments=changed_contract)


def test_run_uses_complete_frozen_development_asset_universe_before_publication(
    tmp_path,
) -> None:
    arguments = _fixture(
        tmp_path,
        split_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        minimum_asset_coverage=1.0,
    )
    arguments["config"] = replace(
        arguments["config"],
        motif_stability_policy=replace(
            arguments["config"].motif_stability_policy,
            minimum_asset_support=3,
        ),
    )

    manifest = _run(arguments)
    stability = json.loads(
        (tmp_path / manifest.run_id / "stability.json").read_text(encoding="utf-8")
    )
    motifs = json.loads(
        (tmp_path / manifest.run_id / "motifs.json").read_text(encoding="utf-8")
    )

    assert manifest.status == "rejected_unstable"
    assert stability["asset_coverage"] == pytest.approx(2 / 3)
    assert all(
        support["asset_event_counts"][-1] == ["SOLUSDT", 0]
        for support in stability["cluster_period_support"]
    )
    assert motifs["development_asset_universe"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert motifs["candidates"]
    assert all(not candidate["accepted"] for candidate in motifs["candidates"])
    assert all(
        any(
            item["dimension"] == "asset"
            and item["member_id"] == "SOLUSDT"
            and item["sequence_count"] == 0
            for item in candidate["universe_support"]
        )
        for candidate in motifs["candidates"]
    )


def test_provenance_drift_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
) -> None:
    base = _fixture(tmp_path)
    normalizer = RobustNormalizer.from_json(base["normalizer_artifact"])
    changed_partition = replace(
        normalizer.partition,
        start=normalizer.partition.start + timedelta(minutes=1),
    )
    wrong_partition = replace(
        normalizer,
        partition=changed_partition,
        training_partition_sha256=changed_partition.sha256,
    ).canonical_json()
    wrong_features = fit_robust_normalizer(
        base["discovery"].rows,
        base["registry"],
        normalizer.partition,
        normalizer.dataset_snapshot_id,
        selected_features=("auction_location",),
    ).canonical_json()
    changed_registry = FeatureRegistry(
        base["registry"].feature_set_id,
        tuple(
            replace(definition, definition=f"{definition.definition} changed")
            if definition.name == "auction_location"
            else definition
            for definition in base["registry"].definitions
        ),
    )
    wrong_commit_config = copy.copy(base["config"])
    object.__setattr__(wrong_commit_config, "code_commit", "deadbee")
    cases = {
        "missing normalizer": {"normalizer_artifact": None},
        "wrong training partition": {"normalizer_artifact": wrong_partition},
        "wrong selected feature list": {"normalizer_artifact": wrong_features},
        "changed normalizer bytes": {
            "normalizer_artifact": base["normalizer_artifact"] + b"\n"
        },
        "mismatched feature publication": {
            "feature_publication": replace(
                base["feature_publication"], publication_sha256="0" * 64
            )
        },
        "mismatched snapshot manifest": {
            "snapshot_manifest": replace(
                base["snapshot_manifest"], snapshot_sha256="0" * 64
            )
        },
        "changed registry": {"registry": changed_registry},
        "uncommitted dirty identity": {
            "snapshot_manifest": replace(
                base["snapshot_manifest"],
                identity=replace(
                    base["snapshot_manifest"].identity,
                    code_commit="abcdef1-dirty",
                ),
            )
        },
        "wrong code commit": {"config": wrong_commit_config},
        "changed lockfile": {"lockfile_bytes": b"version = 2\n"},
    }

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("matrix construction was reached before provenance rejection")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    for label, changes in cases.items():
        arguments = dict(base)
        arguments.update(changes)
        with pytest.raises((TypeError, ValueError), match="provenance|normalizer|snapshot|publication|registry|commit|lock"):
            _run(arguments)


def test_public_discovery_rejects_legacy_fixture_bypass_before_matrix_construction(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path)

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("public legacy bypass reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(TypeError, match="unexpected"):
        replace(
            arguments["config"],
            legacy_fixture_schema="phase4-discovery-fixture-v2",
        )


def test_production_discovery_module_exposes_no_legacy_replay_entrypoint() -> None:
    assert not hasattr(discovery_runs, "replay_frozen_discovery_fixture")
    assert not hasattr(discovery_runs, "_executing_module_path")
    assert "legacy_fixture_schema" not in {
        field.name for field in fields(DiscoveryRunConfig)
    }


@pytest.mark.parametrize("artifact", ("snapshot", "feature publication"))
def test_on_disk_manifest_byte_tamper_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
    artifact: str,
) -> None:
    arguments = _fixture(tmp_path)
    directory = (
        arguments["snapshot_directory"]
        if artifact == "snapshot"
        else arguments["feature_publication_directory"]
    )
    manifest_path = directory / "manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("manifest byte tamper reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match=f"{artifact} manifest bytes"):
        _run(arguments)


def test_internally_consistent_false_commit_is_rejected_against_runtime_head(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path, declared_code_commit="deadbeef")

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("false commit reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match="runtime Git HEAD"):
        _run(arguments)


def test_public_discovery_cannot_select_an_unrelated_runtime_repository(tmp_path) -> None:
    arguments = _fixture(tmp_path)

    with pytest.raises(TypeError, match="repository_root|unexpected"):
        run_discovery(**arguments)


def test_dirty_runtime_worktree_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path)
    (arguments["repository_root"] / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("dirty worktree reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match="dirty"):
        _run(arguments)


def test_ignored_runtime_module_absent_from_head_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path, track_runtime_module=False)

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("ignored runtime module reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match="tracked|HEAD|executing module"):
        _run(arguments)


def test_bounded_regular_read_rejects_parent_traversal_before_open(tmp_path) -> None:
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    (outside / "nested").mkdir(parents=True)
    (base / "secret").write_bytes(b"inside")
    (outside / "secret").write_bytes(b"outside")
    link = base / "link"
    try:
        link.symlink_to(outside / "nested", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    with pytest.raises(RuntimeError, match="parent|traversal|ambiguous"):
        read_bounded_regular(link / ".." / "secret", 64)


@pytest.mark.parametrize("artifact", ("snapshot", "feature publication"))
@pytest.mark.parametrize(
    "entry",
    ("root", "ancestor", "parent_traversal", "marker", "partition", "tree"),
)
def test_input_artifact_symlinks_are_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
    artifact: str,
    entry: str,
) -> None:
    arguments = _fixture(tmp_path / f"{artifact}-{entry}")
    directory_key = (
        "snapshot_directory" if artifact == "snapshot" else "feature_publication_directory"
    )
    directory = arguments[directory_key]
    outside = tmp_path / f"outside-{artifact}-{entry}"
    outside.mkdir(parents=True, exist_ok=True)

    try:
        if entry == "root":
            link = tmp_path / f"linked-{artifact}-root"
            link.symlink_to(directory, target_is_directory=True)
            arguments[directory_key] = link
        elif entry == "ancestor":
            link = tmp_path / f"linked-{artifact}-parent"
            link.symlink_to(directory.parent, target_is_directory=True)
            arguments[directory_key] = link / directory.name
        elif entry == "parent_traversal":
            target = directory.parent / f"{artifact}-traversal-target"
            target.mkdir()
            link = directory.parent / f"{artifact}-traversal-link"
            link.symlink_to(target, target_is_directory=True)
            arguments[directory_key] = link / ".." / directory.name
        elif entry == "marker":
            marker = directory / "_SUCCESS"
            target = outside / "_SUCCESS"
            target.write_bytes(marker.read_bytes())
            marker.unlink()
            marker.symlink_to(target)
        else:
            manifest = (
                arguments["snapshot_manifest"]
                if artifact == "snapshot"
                else arguments["feature_publication"]
            )
            partition = directory / manifest.partitions[0].path
            if entry == "partition":
                target = outside / "partition.parquet"
                target.write_bytes(partition.read_bytes())
                partition.unlink()
                partition.symlink_to(target)
            else:
                tree = directory / Path(manifest.partitions[0].path).parts[0]
                target = outside / tree.name
                tree.replace(target)
                tree.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("symlinked input artifact reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    if entry in ("ancestor", "parent_traversal"):

        def ancestor_artifact_was_read(*args, **kwargs):
            raise AssertionError("artifact under symlinked ancestor was read before rejection")

        monkeypatch.setattr(discovery_runs, "regular_file_matches", ancestor_artifact_was_read)
    elif entry == "tree":
        module = snapshot_export if artifact == "snapshot" else derived_data

        def outside_partition_was_read(*args, **kwargs):
            raise AssertionError("symlinked partition tree was read before rejection")

        monkeypatch.setattr(module, "sha256_regular", outside_partition_was_read)
    with pytest.raises(
        (RuntimeError, ValueError),
        match="symlink|regular|directory|traversal",
    ):
        _run(arguments)


def test_feature_row_content_forgery_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path)
    discovery = arguments["discovery"]
    forged = replace(
        discovery.rows[0],
        values={**discovery.rows[0].values, "auction_location": 999.0},
    )
    arguments["discovery"] = make_discovery_input(
        partition=discovery.partition,
        rows=(forged,) + discovery.rows[1:],
        registry=arguments["registry"],
        purpose="fit",
        max_rows=20,
        publication_manifest=arguments["feature_publication"],
    )

    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("matrix construction was reached before row verification")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match="published feature row content"):
        _run(arguments)


@pytest.mark.parametrize(
    "normalizer_changes",
    (
        {"normalizer_row_delta": 1.0},
        {"normalizer_symbols": ("BTCUSDT", "ETHUSDT", "SOLUSDT")},
    ),
)
def test_normalizer_training_identity_drift_is_rejected_before_matrix_construction(
    tmp_path,
    monkeypatch,
    normalizer_changes: dict[str, object],
) -> None:
    def matrix_was_touched(*args, **kwargs):
        raise AssertionError("normalizer training identity drift reached matrix construction")

    monkeypatch.setattr(discovery_runs, "build_feature_matrix", matrix_was_touched)
    with pytest.raises(ValueError, match="normalizer training"):
        arguments = _fixture(tmp_path, **normalizer_changes)
        _run(arguments)


def test_unstable_discovery_run_is_retained_as_rejected_without_behaviours(tmp_path) -> None:
    manifest = _run(_fixture(tmp_path, rejected=True))

    assert manifest.status == "rejected_unstable"
    assert manifest.behaviours == ()
    assert (tmp_path / "DR-000502" / "manifest.json").exists()
    assert verify_trial_receipt(tmp_path / "DR-000502").status is TerminalStatus.REJECTED


def test_discovery_run_detects_stale_stage_tamper_and_identity_conflict(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    stale = tmp_path / ".DR-000501.staging"
    stale.mkdir()
    with pytest.raises(RuntimeError, match="stale"):
        _run(arguments)
    stale.rmdir()

    _run(arguments)
    (tmp_path / "DR-000501" / "metrics.json").write_text("{}\n")
    with pytest.raises(RuntimeError, match="tamper"):
        _run(arguments)

    fresh_root = tmp_path / "identity"
    changed = dict(_fixture(fresh_root))
    _run(changed)
    with pytest.raises(ValueError, match="provenance"):
        replace(
            changed["config"],
            dataset_snapshot_sha256=hashlib.sha256(b"different").hexdigest(),
        )


def test_discovery_replay_rejects_unmanifested_extra_file(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    manifest = _run(arguments)
    (tmp_path / manifest.run_id / "extra.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unexpected"):
        _run(arguments)


def test_discovery_replay_rejects_symlinked_artifact_without_outside_read(tmp_path) -> None:
    arguments = _fixture(tmp_path / "ledger")
    manifest = _run(arguments)
    metrics_path = arguments["output_root"] / manifest.run_id / "metrics.json"
    outside = tmp_path / "outside-metrics.json"
    outside.write_bytes(metrics_path.read_bytes())
    metrics_path.unlink()
    try:
        metrics_path.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    with pytest.raises(RuntimeError, match="symlink|regular"):
        _run(arguments)


def test_discovery_algorithm_failure_is_receipted_without_swallowing_original(
    tmp_path,
    monkeypatch,
) -> None:
    arguments = _fixture(tmp_path)
    original = ArithmeticError("sensitive detector detail")

    def fail_projection(*args, **kwargs):
        raise original

    monkeypatch.setattr(discovery_runs, "fit_pca", fail_projection)

    with pytest.raises(ArithmeticError) as raised:
        _run(arguments)

    assert raised.value is original
    receipt = verify_trial_receipt(tmp_path / "DR-000501")
    assert receipt.status is TerminalStatus.FAILED
    assert receipt.conclusion == "Trial execution raised ArithmeticError."
    assert "sensitive detector detail" not in (tmp_path / "DR-000501" / "receipt.json").read_text()


def test_discovery_run_rejects_config_version_drift_from_feature_rows(tmp_path) -> None:
    arguments = _fixture(tmp_path)
    arguments["config"] = replace(arguments["config"], config_version="cfg-drifted")

    with pytest.raises(ValueError, match="config_version"):
        _run(arguments)


@pytest.mark.parametrize(
    "field",
    ("config_version", "profile_version", "window_policy_id"),
)
def test_discovery_run_rejects_source_identity_drift_between_partitions(
    tmp_path,
    field: str,
) -> None:
    arguments = _fixture(tmp_path)
    development = arguments["development"]
    arguments["development"] = make_discovery_input(
        partition=development.partition,
        rows=tuple(replace(row, **{field: "drifted-v2"}) for row in development.rows),
        registry=arguments["registry"],
        purpose="stability",
        max_rows=20,
    )

    with pytest.raises(ValueError, match=field):
        _run(arguments)


def test_interpretation_publication_is_atomic_idempotent_and_detector_frozen(
    tmp_path,
) -> None:
    run_manifest = _run(_fixture(tmp_path))
    receipt = verify_trial_receipt(tmp_path / run_manifest.run_id)
    evidence = tuple(
        BehaviourEvidencePack(
            run_id=run_manifest.run_id,
            behaviour=behaviour,
            nearest_behaviour_ids=tuple(
                item.behaviour_id
                for item in run_manifest.behaviours
                if item.behaviour_id != behaviour.behaviour_id
            ),
            contrasting_behaviour_ids=(),
            transition_matrix=run_manifest.transition_matrix,
        )
        for behaviour in run_manifest.behaviours
    )
    interpretations = tuple(_interpretation(item.behaviour) for item in evidence)

    first = publish_ai_interpretations(
        run_manifest=run_manifest,
        evidence=evidence,
        interpretations=interpretations,
        output_root=tmp_path,
    )
    replay = publish_ai_interpretations(
        run_manifest=run_manifest,
        evidence=evidence,
        interpretations=interpretations,
        output_root=tmp_path,
    )

    assert first == replay
    assert first.to_dict()["schema_version"] == "interpretation-manifest-v2"
    assert first.behaviour_ids == tuple(
        sorted(behaviour.behaviour_id for behaviour in run_manifest.behaviours)
    )
    interpretation_root = tmp_path.parent / f"{tmp_path.name}-interpretations"
    assert not (interpretation_root / ".DR-000501.staging").exists()
    assert (interpretation_root / "DR-000501" / "manifest.json").is_file()
    assert verify_trial_receipt(tmp_path / "DR-000501") == receipt
    assert read_trial_ledger(tmp_path) == (receipt,)

    boundary_evidence = run_manifest.transition_matrix.boundary_evidence
    forged_matrix = replace(
        run_manifest.transition_matrix,
        boundary_evidence=replace(
            boundary_evidence,
            raw_observation_count=boundary_evidence.raw_observation_count + 1,
        ),
    )
    with pytest.raises(ValueError, match="transition evidence"):
        publish_ai_interpretations(
            run_manifest=run_manifest,
            evidence=(replace(evidence[0], transition_matrix=forged_matrix),) + evidence[1:],
            interpretations=interpretations,
            output_root=tmp_path,
        )

    changed = replace(
        interpretations[0],
        detector_fields=("auction_location",),
    )
    with pytest.raises(ValueError, match="detector"):
        publish_ai_interpretations(
            run_manifest=run_manifest,
            evidence=evidence,
            interpretations=(changed,) + interpretations[1:],
            output_root=tmp_path / "detector-change",
        )


def test_interpretation_publication_rejects_forged_run_manifest_identity(tmp_path) -> None:
    run_manifest = _run(_fixture(tmp_path))
    evidence = tuple(
        BehaviourEvidencePack(
            run_id=run_manifest.run_id,
            behaviour=behaviour,
            nearest_behaviour_ids=tuple(
                item.behaviour_id
                for item in run_manifest.behaviours
                if item.behaviour_id != behaviour.behaviour_id
            ),
            contrasting_behaviour_ids=(),
            transition_matrix=run_manifest.transition_matrix,
        )
        for behaviour in run_manifest.behaviours
    )
    interpretations = tuple(_interpretation(item.behaviour) for item in evidence)
    forged = replace(run_manifest, manifest_sha256="0" * 64)

    with pytest.raises(RuntimeError, match="manifest identity"):
        publish_ai_interpretations(
            run_manifest=forged,
            evidence=evidence,
            interpretations=interpretations,
            output_root=tmp_path,
        )

    interpretation_root = tmp_path.parent / f"{tmp_path.name}-interpretations"
    assert not (interpretation_root / run_manifest.run_id).exists()


def test_interpretation_publication_rejects_forged_frozen_behaviour_identity(tmp_path) -> None:
    run_manifest = _run(_fixture(tmp_path))
    evidence = tuple(
        BehaviourEvidencePack(
            run_id=run_manifest.run_id,
            behaviour=behaviour,
            nearest_behaviour_ids=tuple(
                item.behaviour_id
                for item in run_manifest.behaviours
                if item.behaviour_id != behaviour.behaviour_id
            ),
            contrasting_behaviour_ids=(),
            transition_matrix=run_manifest.transition_matrix,
        )
        for behaviour in run_manifest.behaviours
    )
    interpretations = tuple(_interpretation(item.behaviour) for item in evidence)
    changed_behaviour = copy.copy(run_manifest.behaviours[0])
    object.__setattr__(
        changed_behaviour,
        "description",
        "Another neutral frozen description.",
    )
    forged = replace(
        run_manifest,
        behaviours=(changed_behaviour,) + run_manifest.behaviours[1:],
    )

    with pytest.raises(RuntimeError, match="behaviour identity"):
        publish_ai_interpretations(
            run_manifest=forged,
            evidence=evidence,
            interpretations=interpretations,
            output_root=tmp_path,
        )
