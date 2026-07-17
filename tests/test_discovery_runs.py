from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import market_structure_lab.discovery.runs as discovery_runs

from market_structure_lab.discovery import (
    AIInterpretation,
    BehaviourEvidencePack,
    DiscoveryRunConfig,
    PartitionRole,
    StabilityPolicy,
    TimePartition,
    freeze_split,
    make_discovery_input,
    publish_ai_interpretations,
    run_discovery,
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


def _fixture(tmp_path, *, rejected: bool = False):
    registry = _registry()
    discovery_partition = TimePartition(
        PartitionRole.DISCOVERY,
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        ("BTCUSDT", "ETHUSDT"),
    )
    development_partition = TimePartition(
        PartitionRole.DEVELOPMENT,
        datetime(2025, 1, 2, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
        ("BTCUSDT", "ETHUSDT"),
    )
    holdout_partition = TimePartition(
        PartitionRole.HOLDOUT,
        datetime(2025, 1, 4, tzinfo=UTC),
        datetime(2025, 1, 5, tzinfo=UTC),
        ("BTCUSDT", "ETHUSDT"),
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
    discovery = make_discovery_input(
        partition=discovery_partition,
        rows=discovery_rows,
        registry=registry,
        purpose="fit",
        max_rows=20,
    )
    development = make_discovery_input(
        partition=development_partition,
        rows=development_rows,
        registry=registry,
        purpose="stability",
        max_rows=20,
    )
    policy = (
        StabilityPolicy(1.0, 1.0, 0.0, 1.0, 1.0)
        if rejected
        else StabilityPolicy(-1.0, -1.0, 1.0, 0.0, -1.0)
    )
    config = DiscoveryRunConfig(
        run_id="DR-000502" if rejected else "DR-000501",
        dataset_snapshot_id="DS-000501",
        dataset_snapshot_sha256=hashlib.sha256(b"dataset").hexdigest(),
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
        code_commit="abcdef1",
        lock_sha256=hashlib.sha256(b"lock").hexdigest(),
        feature_publication_id="FP-000501",
        feature_publication_sha256=hashlib.sha256(b"feature-publication").hexdigest(),
        normalizer_id="NZ-000501",
        normalizer_sha256=hashlib.sha256(b"normalizer").hexdigest(),
        started_at=datetime(2026, 7, 17, 3, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 17, 3, 1, tzinfo=UTC),
    )
    return {
        "config": config,
        "discovery": discovery,
        "development": development,
        "registry": registry,
        "event_ids": tuple(f"EV-{index:02d}" for index in range(12)),
        "durations_seconds": (60.0,) * 12,
        "output_root": tmp_path,
    }


def _run(arguments):
    return run_discovery(**arguments)


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
    assert published_manifest["schema_version"] == "discovery-run-manifest-v2"
    assert published_manifest["transition_matrix"] == published_transitions
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
    changed["config"] = replace(
        changed["config"],
        dataset_snapshot_sha256=hashlib.sha256(b"different").hexdigest(),
    )
    with pytest.raises(RuntimeError, match="identity conflict"):
        _run(changed)


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
