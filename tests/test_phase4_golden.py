from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from market_structure_lab.discovery import (
    AIInterpretation,
    BehaviourEvidencePack,
    ClusterObservation,
    DiscoveryRunManifest,
    PartitionRole,
    StabilityPolicy,
    TimePartition,
    build_feature_matrix,
    discover_motifs,
    estimate_cluster_transitions,
    evaluate_cluster_stability,
    fit_pca,
    fit_projected_kmeans,
    freeze_behaviours,
    freeze_split,
    make_discovery_input,
    publish_ai_interpretations,
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


@dataclass(frozen=True, slots=True)
class FrozenFixtureRunConfig:
    run_id: str
    dataset_snapshot_id: str
    dataset_snapshot_sha256: str
    feature_set_id: str
    registry_sha256: str
    config_version: str
    split: Any
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
    config = FrozenFixtureRunConfig(
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


def _replay(arguments: dict[str, object]) -> DiscoveryRunManifest:
    config = arguments["config"]
    discovery = arguments["discovery"]
    development = arguments["development"]
    registry = arguments["registry"]
    output_root = arguments["output_root"]
    assert isinstance(config, FrozenFixtureRunConfig)
    assert isinstance(output_root, Path)

    discovery_matrix = build_feature_matrix(
        discovery, registry, config.feature_names, config.max_rows  # type: ignore[arg-type]
    )
    development_matrix = build_feature_matrix(
        development, registry, config.feature_names, config.max_rows  # type: ignore[arg-type]
    )
    selected_discovery = _selected_rows(discovery, discovery_matrix)  # type: ignore[arg-type]
    selected_development = _selected_rows(development, development_matrix)  # type: ignore[arg-type]
    event_ids = tuple(arguments["event_ids"])  # type: ignore[arg-type]
    durations = tuple(float(value) for value in arguments["durations_seconds"])  # type: ignore[union-attr]
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
        subsample_fraction=0.75,
        policy=config.stability_policy,
    )
    behaviours = freeze_behaviours(
        run_id=config.run_id,
        matrix=discovery_matrix,
        projection=projection,
        clustering=clustering,
        stability=stability,
        event_ids=event_ids,
        durations_seconds=durations,
        symbols=tuple(row.symbol for row in selected_discovery),
        description="Neutral recurring outcome-blind feature configurations.",
    )
    motif_payload = _motif_payload(selected_discovery, discovery_matrix)
    transition_matrix = _transition_evidence(
        selected_discovery, clustering.assignments, config
    )
    status = "completed" if stability.accepted else "rejected_unstable"
    config_payload = _config_payload(config)
    identity_payload = {
        "config": config_payload,
        "registry_sha256": registry.sha256,  # type: ignore[union-attr]
        "discovery_input_sha256": _input_sha256(discovery),  # type: ignore[arg-type]
        "development_input_sha256": _input_sha256(development),  # type: ignore[arg-type]
        "event_ids": event_ids,
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
        "motifs": sum(len(group["matches"]) for group in motif_payload),
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
    artifact_hashes = tuple(
        sorted((name, _sha256(content)) for name, content in payloads.items())
    )
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
        status=status,  # type: ignore[arg-type]
        identity_sha256=identity_sha256,
        config_sha256=config_sha256,
        artifact_sha256=artifact_hashes,
        behaviours=behaviours,
        transition_matrix=transition_matrix,
        manifest_sha256=_sha256(_canonical_json(manifest_without_hash)),
    )
    payloads["manifest.json"] = _json_file(manifest.to_dict())
    destination = output_root / config.run_id
    destination.mkdir(parents=True)
    for name, content in payloads.items():
        (destination / name).write_bytes(content)
    return manifest


def _selected_rows(input_value, matrix) -> tuple[FeatureRow, ...]:
    by_id = {_row_id(row): row for row in input_value.rows}
    return tuple(by_id[row_id] for row_id in matrix.row_ids)


def _adjacent_periods(rows: Sequence[FeatureRow]) -> tuple[tuple[str, ...], tuple[str, str]]:
    ordered_cutoffs = tuple(sorted({row.information_cutoff for row in rows}))
    threshold = ordered_cutoffs[len(ordered_cutoffs) // 2]
    return (
        tuple(
            "development-early" if row.information_cutoff < threshold else "development-late"
            for row in rows
        ),
        ("development-early", "development-late"),
    )


def _motif_payload(rows: Sequence[FeatureRow], matrix) -> tuple[dict[str, object], ...]:
    groups: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for row, values in zip(rows, matrix.values, strict=True):
        groups[(row.symbol, row.timeframe, row.segment_id)].append(values[0])
    payload: list[dict[str, object]] = []
    for key in sorted(groups):
        values = tuple(groups[key])
        window_length = min(4, len(values)) if len(values) >= 4 else None
        exclusion_zone = min(2, window_length - 1) if window_length is not None else None
        max_windows = len(values) - window_length + 1 if window_length is not None else 0
        matches = (
            discover_motifs(
                values,
                window_length=window_length,
                exclusion_zone=exclusion_zone,
                max_windows=max_windows,
                top_k=3,
            )
            if window_length is not None and exclusion_zone is not None
            else ()
        )
        payload.append(
            {
                "symbol": key[0],
                "timeframe": key[1],
                "segment_id": key[2],
                "window_length": window_length,
                "exclusion_zone": exclusion_zone,
                "max_windows": max_windows,
                "top_k": 3,
                "matches": [_jsonable(item) for item in matches],
            }
        )
    return tuple(payload)


def _transition_evidence(rows, assignments, config: FrozenFixtureRunConfig):
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
        bootstrap_iterations=100,
        block_length=2,
        confidence_level=0.95,
    )


def _config_payload(config: FrozenFixtureRunConfig) -> dict[str, object]:
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
            "subsample_fraction": 0.75,
            "motif_max_window_length": 4,
            "motif_exclusion_zone": 2,
            "motif_top_k": 3,
            "transition_horizon": 1,
            "transition_bootstrap_iterations": 100,
            "transition_block_length": 2,
            "transition_confidence_level": 0.95,
        },
    }


def _input_sha256(input_value) -> str:
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


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_file(value: object) -> bytes:
    return _canonical_json(value) + b"\n"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    stable_first = _replay(_run_arguments(fixture, "stable", stable_first_root))
    stable_second = _replay(_run_arguments(fixture, "stable", stable_second_root))
    stable_first_dir = stable_first_root / stable_first.run_id
    stable_second_dir = stable_second_root / stable_second.run_id

    assert stable_first == stable_second
    assert _bundle_bytes(stable_first_dir) == _bundle_bytes(stable_second_dir)
    published_manifest = _load_json(stable_first_dir / "manifest.json")
    published_transitions = _load_json(stable_first_dir / "transitions.json")
    assert published_manifest["transition_matrix"] == published_transitions
    assert published_manifest["transition_matrix"]["boundary_evidence"] == asdict(
        stable_first.transition_matrix.boundary_evidence
    )
    assert _load_json(stable_first_dir / "projection.json")["algorithm_version"] == (
        "deterministic-pca-v2"
    )
    assert _expected_run(stable_first, stable_first_dir) == fixture["runs"]["stable"]["expected"]

    rejected_first_root = tmp_path / "rejected-first"
    rejected_second_root = tmp_path / "rejected-second"
    rejected_first = _replay(_run_arguments(fixture, "rejected", rejected_first_root))
    rejected_second = _replay(_run_arguments(fixture, "rejected", rejected_second_root))

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
    manifest = _replay(_run_arguments(fixture, "stable", tmp_path))
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
    manifest = _replay(_run_arguments(fixture, "stable", tmp_path))
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
