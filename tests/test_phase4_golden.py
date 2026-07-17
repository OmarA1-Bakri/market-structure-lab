from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

FIXTURE_PATH = Path("tests/fixtures/phase4/discovery_run_v1.json")
INTERPRETATION_INPUT_PATH = Path("tests/fixtures/phase4/discovery_interpretation_input_v1.json")
INTERPRETATION_RESPONSE_PATH = Path(
    "tests/fixtures/phase4/discovery_interpretation_response_v1.json"
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _partition(payload: dict[str, Any]) -> TimePartition:
    return TimePartition(
        role=PartitionRole(payload["role"]),
        start=_timestamp(payload["start"]),
        end=_timestamp(payload["end"]),
        symbols=tuple(payload["symbols"]),
    )


def _registry(payload: dict[str, Any]) -> FeatureRegistry:
    definitions = tuple(
        FeatureDefinition(
            name=item["name"],
            definition=item["definition"],
            family=FeatureFamily(item["family"]),
            value_kind=FeatureValueKind(item["value_kind"]),
            units=item["units"],
            required_prior_observations=item["required_prior_observations"],
            missing_policy=MissingPolicy(item["missing_policy"]),
            version=item["version"],
            leakage_class=LeakageClass(item["leakage_class"]),
            allowed_categories=tuple(item["allowed_categories"]),
        )
        for item in payload["definitions"]
    )
    return FeatureRegistry(payload["feature_set_id"], definitions)


def _rows(
    payloads: list[dict[str, Any]],
    *,
    fixture: dict[str, Any],
    registry: FeatureRegistry,
) -> tuple[FeatureRow, ...]:
    identity = fixture["source_identity"]
    dataset = fixture["dataset_snapshot"]
    return tuple(
        FeatureRow(
            timestamp=_timestamp(item["timestamp"]),
            information_cutoff=_timestamp(item["timestamp"]) + timedelta(minutes=1),
            symbol=item["symbol"],
            timeframe="1m",
            segment_id=item["segment_id"],
            dataset_version=dataset["dataset_version"],
            config_version=identity["config_version"],
            profile_version=identity["profile_version"],
            window_policy_id=identity["window_policy_id"],
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
            values={name: float(value) for name, value in item["values"].items()},
        )
        for item in payloads
    )


def _run_arguments(
    fixture: dict[str, Any],
    run_name: str,
    output_root: Path,
) -> dict[str, object]:
    registry = _registry(fixture["registry"])
    split_payload = fixture["split"]
    discovery_partition = _partition(split_payload["discovery"])
    development_partition = _partition(split_payload["development"])
    split = freeze_split(
        split_id=split_payload["split_id"],
        discovery=discovery_partition,
        development=development_partition,
        holdout=_partition(split_payload["holdout_metadata"]),
        asset_holdouts=tuple(split_payload["asset_holdouts"]),
    )
    discovery = make_discovery_input(
        partition=discovery_partition,
        rows=_rows(fixture["discovery_rows"], fixture=fixture, registry=registry),
        registry=registry,
        purpose="fit",
        max_rows=fixture["caps"]["max_rows"],
    )
    development = make_discovery_input(
        partition=development_partition,
        rows=_rows(fixture["development_rows"], fixture=fixture, registry=registry),
        registry=registry,
        purpose="stability",
        max_rows=fixture["caps"]["max_rows"],
    )
    run = fixture["runs"][run_name]
    policy = StabilityPolicy(**run["stability_policy"])
    config = DiscoveryRunConfig(
        run_id=run["run_id"],
        dataset_snapshot_id=fixture["dataset_snapshot"]["dataset_version"],
        dataset_snapshot_sha256=fixture["dataset_snapshot"]["sha256"],
        feature_set_id=registry.feature_set_id,
        registry_sha256=registry.sha256,
        config_version=fixture["source_identity"]["config_version"],
        split=split,
        feature_names=tuple(fixture["feature_names"]),
        pca_components=run["pca_components"],
        clusters=run["clusters"],
        seeds=tuple(run["seeds"]),
        max_rows=fixture["caps"]["max_rows"],
        max_iterations=fixture["caps"]["max_iterations"],
        tolerance=fixture["caps"]["tolerance"],
        stability_policy=policy,
        code_commit=fixture["code_commit"],
        lock_sha256=fixture["lock_sha256"],
    )
    return {
        "config": config,
        "discovery": discovery,
        "development": development,
        "registry": registry,
        "event_ids": tuple(fixture["event_ids"]),
        "durations_seconds": tuple(fixture["durations_seconds"]),
        "output_root": output_root,
    }


def _bundle_bytes(run_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


def _expected_run(manifest, run_dir: Path) -> dict[str, object]:
    metrics = _load_json(run_dir / "metrics.json")
    return {
        "status": manifest.status,
        "manifest_sha256": manifest.manifest_sha256,
        "identity_sha256": manifest.identity_sha256,
        "config_sha256": manifest.config_sha256,
        "behaviour_ids": [item.behaviour_id for item in manifest.behaviours],
        "artifact_sha256": dict(manifest.artifact_sha256),
        "metrics": metrics,
        "transition_algorithm_version": manifest.transition_matrix.algorithm_version,
    }


def _evidence(manifest) -> tuple[BehaviourEvidencePack, ...]:
    behaviour_ids = tuple(item.behaviour_id for item in manifest.behaviours)
    return tuple(
        BehaviourEvidencePack(
            run_id=manifest.run_id,
            behaviour=behaviour,
            nearest_behaviour_ids=tuple(
                item for item in behaviour_ids if item != behaviour.behaviour_id
            ),
            contrasting_behaviour_ids=(),
            transition_matrix=manifest.transition_matrix,
        )
        for behaviour in manifest.behaviours
    )


def _interpretation_input(manifest) -> dict[str, object]:
    evidence = _evidence(manifest)
    evidence_payload = [item.to_dict() for item in evidence]
    payload = {
        "schema_version": "phase4-interpretation-input-v2",
        "run_id": manifest.run_id,
        "run_manifest_sha256": manifest.manifest_sha256,
        "evidence_sha256": hashlib.sha256(
            json.dumps(
                evidence_payload,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "evidence": evidence_payload,
        "required_response_fields": [
            "behaviour_id",
            "neutral_name",
            "description",
            "candidate_mechanism_inference",
            "falsifiable_hypothesis",
            "detector_fields",
            "proposed_horizon",
            "proposed_metrics",
            "spuriousness_reasons",
        ],
        "constraints": [
            "Use a canonical observable-language neutral_name.",
            "Prefix candidate_mechanism_inference with 'Inference:'.",
            "Do not claim a validated edge, profitability, participant identity, or causation.",
            "Do not change detector_fields.",
            "Treat every hypothesis as unvalidated until Phase 5 untouched-data testing.",
        ],
    }
    return json.loads(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _interpretations(
    response: dict[str, Any],
    *,
    prompt_sha256: str,
    response_sha256: str,
) -> tuple[AIInterpretation, ...]:
    provenance = response["provenance"]
    generated_at = _timestamp(provenance["generated_at"])
    return tuple(
        AIInterpretation(
            behaviour_id=item["behaviour_id"],
            neutral_name=item["neutral_name"],
            description=item["description"],
            candidate_mechanism_inference=item["candidate_mechanism_inference"],
            falsifiable_hypothesis=item["falsifiable_hypothesis"],
            detector_fields=tuple(item["detector_fields"]),
            proposed_horizon=item["proposed_horizon"],
            proposed_metrics=tuple(item["proposed_metrics"]),
            spuriousness_reasons=tuple(item["spuriousness_reasons"]),
            provider=provenance["provider"],
            model=provenance["model"],
            prompt_sha256=prompt_sha256,
            temperature=provenance["temperature"],
            generated_at=generated_at,
            response_sha256=response_sha256,
        )
        for item in response["interpretations"]
    )


def test_phase4_golden_stable_and_rejected_runs_replay_byte_identically(tmp_path) -> None:
    fixture = _load_json(FIXTURE_PATH)
    assert fixture["schema_version"] == "phase4-discovery-fixture-v2"
    assert "holdout_rows" not in fixture
    assert all(
        forbidden not in FIXTURE_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ("forward_return", "profitability", "target_hit", "mfe", "mae")
    )

    stable_first_root = tmp_path / "stable-first"
    stable_second_root = tmp_path / "stable-second"
    stable_first = run_discovery(**_run_arguments(fixture, "stable", stable_first_root))
    stable_second = run_discovery(**_run_arguments(fixture, "stable", stable_second_root))
    stable_first_dir = stable_first_root / stable_first.run_id
    stable_second_dir = stable_second_root / stable_second.run_id

    assert stable_first == stable_second
    assert _bundle_bytes(stable_first_dir) == _bundle_bytes(stable_second_dir)
    published_manifest = _load_json(stable_first_dir / "manifest.json")
    published_transitions = _load_json(stable_first_dir / "transitions.json")
    assert published_manifest["transition_matrix"] == published_transitions
    assert (
        published_manifest["transition_matrix"]["boundary_evidence"]
        == asdict(stable_first.transition_matrix.boundary_evidence)
    )
    assert _load_json(stable_first_dir / "projection.json")["algorithm_version"] == (
        "deterministic-pca-v2"
    )
    assert _expected_run(stable_first, stable_first_dir) == fixture["runs"]["stable"]["expected"]

    rejected_first_root = tmp_path / "rejected-first"
    rejected_second_root = tmp_path / "rejected-second"
    rejected_first = run_discovery(**_run_arguments(fixture, "rejected", rejected_first_root))
    rejected_second = run_discovery(**_run_arguments(fixture, "rejected", rejected_second_root))

    assert rejected_first == rejected_second
    assert rejected_first.status == "rejected_unstable"
    assert rejected_first.behaviours == ()
    assert _bundle_bytes(rejected_first_root / rejected_first.run_id) == _bundle_bytes(
        rejected_second_root / rejected_second.run_id
    )
    assert (
        _expected_run(
            rejected_first,
            rejected_first_root / rejected_first.run_id,
        )
        == fixture["runs"]["rejected"]["expected"]
    )


def test_phase4_interpretation_input_is_frozen_from_real_evidence(tmp_path) -> None:
    fixture = _load_json(FIXTURE_PATH)
    manifest = run_discovery(**_run_arguments(fixture, "stable", tmp_path))
    expected = _interpretation_input(manifest)
    frozen_bytes = INTERPRETATION_INPUT_PATH.read_bytes()

    assert _load_json(INTERPRETATION_INPUT_PATH) == expected
    assert hashlib.sha256(frozen_bytes).hexdigest() == fixture["interpretation_input_sha256"]
    lowered = json.dumps(expected["evidence"], sort_keys=True).encode("utf-8").lower()
    assert b"holdout" not in lowered
    assert b"forward_return" not in lowered
    assert b"profitability" not in lowered


def test_phase4_parent_interpretation_publishes_with_exact_provenance(tmp_path) -> None:
    fixture = _load_json(FIXTURE_PATH)
    manifest = run_discovery(**_run_arguments(fixture, "stable", tmp_path))
    prompt_sha256 = hashlib.sha256(INTERPRETATION_INPUT_PATH.read_bytes()).hexdigest()
    response_bytes = INTERPRETATION_RESPONSE_PATH.read_bytes()
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    response = _load_json(INTERPRETATION_RESPONSE_PATH)
    interpretations = _interpretations(
        response,
        prompt_sha256=prompt_sha256,
        response_sha256=response_sha256,
    )

    published = publish_ai_interpretations(
        run_manifest=manifest,
        evidence=_evidence(manifest),
        interpretations=interpretations,
        output_root=tmp_path,
    )
    replay = publish_ai_interpretations(
        run_manifest=manifest,
        evidence=_evidence(manifest),
        interpretations=interpretations,
        output_root=tmp_path,
    )

    assert published == replay
    assert published.to_dict() == fixture["interpretation_publication"]["expected"]
    assert prompt_sha256 == fixture["interpretation_input_sha256"]
    assert response_sha256 == fixture["interpretation_response_sha256"]
