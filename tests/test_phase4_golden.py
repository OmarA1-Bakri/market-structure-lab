from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from market_structure_lab.discovery import (
    AIInterpretation,
    AdjacentPeriodStabilityPolicy,
    BehaviourEvidencePack,
    ClusterObservation,
    DiscoveryRunManifest,
    MOTIF_ALGORITHM_VERSION,
    MotifObservation,
    MotifRegimeAssignmentContract,
    MotifStabilityPolicy,
    PartitionRole,
    StabilityPolicy,
    TimePartition,
    TransitionUncertaintyPolicy,
    build_contiguous_motif_sequences,
    build_feature_matrix,
    estimate_cluster_transitions,
    evaluate_cluster_stability,
    evaluate_motif_stability,
    fit_pca,
    fit_projected_kmeans,
    freeze_behaviours,
    freeze_split,
    make_discovery_input,
    publish_ai_interpretations,
)
from market_structure_lab.discovery.stability import STABILITY_ALGORITHM_VERSION
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
    adjacent_period_stability_policy: AdjacentPeriodStabilityPolicy
    motif_stability_policy: MotifStabilityPolicy
    motif_regime_assignments: MotifRegimeAssignmentContract
    transition_uncertainty_policy: TransitionUncertaintyPolicy
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
    adjacent_period_policy = AdjacentPeriodStabilityPolicy(
        **run["adjacent_period_stability_policy"]
    )
    motif_policy_payload = dict(run["motif_stability_policy"])
    for field in (
        "window_lengths",
        "exclusion_zones",
        "tie_seeds",
        "tie_policies",
        "distance_multipliers",
    ):
        motif_policy_payload[field] = tuple(motif_policy_payload[field])
    motif_stability_policy = MotifStabilityPolicy(**motif_policy_payload)
    regime_payload = dict(fixture["motif_regime_assignments"])
    regime_payload["regime_universe"] = tuple(regime_payload["regime_universe"])
    regime_payload["assignments"] = tuple(tuple(item) for item in regime_payload["assignments"])
    motif_regime_assignments = MotifRegimeAssignmentContract(**regime_payload)
    transition_policy_payload = dict(run["transition_uncertainty_policy"])
    transition_policy_payload["sensitivity_offsets"] = tuple(
        transition_policy_payload["sensitivity_offsets"]
    )
    transition_uncertainty_policy = TransitionUncertaintyPolicy(**transition_policy_payload)
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
        adjacent_period_stability_policy=adjacent_period_policy,
        motif_stability_policy=motif_stability_policy,
        motif_regime_assignments=motif_regime_assignments,
        transition_uncertainty_policy=transition_uncertainty_policy,
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
        discovery,
        registry,
        config.feature_names,
        config.max_rows,  # type: ignore[arg-type]
    )
    development_matrix = build_feature_matrix(
        development,
        registry,
        config.feature_names,
        config.max_rows,  # type: ignore[arg-type]
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
        asset_universe=config.split.development.symbols,
        periods=periods,
        period_order=period_order,
        seeds=config.seeds,
        subsample_fraction=0.75,
        policy=config.stability_policy,
        adjacent_period_policy=config.adjacent_period_stability_policy,
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
    motif_report = _motif_report(
        discovery_rows=selected_discovery,
        discovery_matrix=discovery_matrix,
        development_rows=selected_development,
        development_matrix=development_matrix,
        development_periods=periods,
        development_period_universe=period_order,
        development_asset_universe=config.split.development.symbols,
        regime_assignments=config.motif_regime_assignments,
        policy=config.motif_stability_policy,
    )
    transition_matrix = _transition_evidence(selected_discovery, clustering.assignments, config)
    transition_estimates = tuple(
        estimate for row in transition_matrix.rows for estimate in row.destinations
    )
    rejected_transition_estimates = sum(
        estimate.evidence_status == "rejected" for estimate in transition_estimates
    )
    descriptive_only_transition_estimates = sum(
        estimate.evidence_status == "descriptive_only" for estimate in transition_estimates
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
        "motif_candidates": len(motif_report.candidates),
        "motifs_published": motif_report.published_count,
        "motifs_rejected": motif_report.rejected_count,
        "transitions": transition_matrix.total_transitions,
        "conditional_recurrence_estimates": len(transition_estimates),
        "conditional_recurrence_rejected": rejected_transition_estimates,
        "conditional_recurrence_descriptive_only": descriptive_only_transition_estimates,
    }
    payloads: dict[str, bytes] = {
        "config.json": _json_file(config_payload),
        "projection.json": _json_file(projection),
        "clustering.json": _json_file(clustering),
        "stability.json": _json_file(stability),
        "behaviours.json": _json_file(behaviours),
        "motifs.json": _json_file(motif_report),
        "transitions.json": _json_file(transition_matrix),
        "metrics.json": _json_file(metrics_payload),
        "summary.md": (
            f"# {config.run_id}\n\nStatus: {status}\n\n"
            "Outcome-blind discovery; final holdout was not accessed.\n\n"
            f"Cluster behaviours: {len(behaviours)} (independent stability path).\n"
            f"Motif candidates: {len(motif_report.candidates)}; "
            f"published: {motif_report.published_count}; "
            f"rejected and retained: {motif_report.rejected_count}.\n"
            "Only motifs accepted by the frozen motif policy support recurring evidence.\n"
            f"Conditional recurrence estimates: {len(transition_estimates)}; "
            f"rejected: {rejected_transition_estimates}; descriptive-only: "
            f"{descriptive_only_transition_estimates}. These remain descriptive and "
            "carry no inferential claim.\n"
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


def _motif_report(
    *,
    discovery_rows: Sequence[FeatureRow],
    discovery_matrix,
    development_rows: Sequence[FeatureRow],
    development_matrix,
    development_periods: Sequence[str],
    development_asset_universe: Sequence[str],
    development_period_universe: Sequence[str],
    regime_assignments: MotifRegimeAssignmentContract,
    policy: MotifStabilityPolicy,
):
    expected = tuple(sorted((*discovery_matrix.row_ids, *development_matrix.row_ids)))
    assigned = tuple(row_id for row_id, _ in regime_assignments.assignments)
    if expected != assigned:
        raise ValueError("golden regime assignments must exactly cover selected rows")
    assignments = dict(regime_assignments.assignments)

    def observations(rows, matrix, periods):
        return tuple(
            MotifObservation(
                row_id=row_id,
                timestamp=row.timestamp,
                information_cutoff=row.information_cutoff,
                symbol=row.symbol,
                timeframe=row.timeframe,
                segment_id=row.segment_id,
                session_id=row.timestamp.date().isoformat(),
                period_id=period,
                regime_id=assignments[row_id],
                values=values,
            )
            for row, row_id, values, period in zip(
                rows, matrix.row_ids, matrix.values, periods, strict=True
            )
        )

    discovery_sequences = build_contiguous_motif_sequences(
        observations(
            discovery_rows,
            discovery_matrix,
            ("discovery",) * len(discovery_rows),
        ),
        feature_names=discovery_matrix.feature_names,
    )
    development_sequences = build_contiguous_motif_sequences(
        observations(development_rows, development_matrix, development_periods),
        feature_names=development_matrix.feature_names,
    )
    return evaluate_motif_stability(
        discovery_sequences=discovery_sequences,
        development_sequences=development_sequences,
        development_asset_universe=development_asset_universe,
        development_period_universe=development_period_universe,
        development_regime_universe=regime_assignments.regime_universe,
        policy=policy,
    )


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
        max_rows=config.max_rows,
        policy=config.transition_uncertainty_policy,
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
        "adjacent_period_stability_policy": _jsonable(config.adjacent_period_stability_policy),
        "motif_stability_policy": _jsonable(config.motif_stability_policy),
        "motif_regime_assignments": _jsonable(config.motif_regime_assignments),
        "transition_uncertainty_policy": _jsonable(config.transition_uncertainty_policy),
        "code_commit": config.code_commit,
        "lock_sha256": config.lock_sha256,
        "parent_run_ids": list(config.parent_run_ids),
        "orchestration_parameters": {
            "subsample_fraction": 0.75,
            "stability_algorithm_version": STABILITY_ALGORITHM_VERSION,
            "motif_algorithm_version": MOTIF_ALGORITHM_VERSION,
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
    assert STABILITY_ALGORITHM_VERSION == "cluster-stability-v2"
    assert MOTIF_ALGORITHM_VERSION == "boundary-safe-multivariate-motifs-v3"
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
    assert published_transitions["algorithm_version"] == ("boundary-aware-dwell-transitions-v3")
    assert published_manifest["transition_matrix"] == published_transitions
    assert published_manifest["transition_matrix"]["boundary_evidence"] == asdict(
        stable_first.transition_matrix.boundary_evidence
    )
    assert _load_json(stable_first_dir / "projection.json")["algorithm_version"] == (
        "deterministic-pca-v2"
    )
    published_config = _load_json(stable_first_dir / "config.json")
    assert (
        published_config["transition_uncertainty_policy"]
        == fixture["runs"]["stable"]["transition_uncertainty_policy"]
    )
    assert (
        published_transitions["uncertainty_policy"]
        == published_config["transition_uncertainty_policy"]
    )
    assert published_transitions["dependence_diagnostics"]["selected_block_length"] >= 1
    assert published_transitions["estimate_semantics"] == ("conditional_recurrence_estimate")
    assert all(
        {
            "effective_support",
            "evidence_status",
            "interval_width",
            "maximum_sensitivity_endpoint_delta",
            "rejection_reasons",
            "sensitivity",
        }.issubset(estimate)
        for row in published_transitions["rows"]
        for estimate in row["destinations"]
    )
    published_stability = _load_json(stable_first_dir / "stability.json")
    published_motifs = _load_json(stable_first_dir / "motifs.json")
    assert (
        published_config["adjacent_period_stability_policy"]
        == (fixture["runs"]["stable"]["adjacent_period_stability_policy"])
    )
    assert published_config["orchestration_parameters"]["stability_algorithm_version"] == (
        STABILITY_ALGORITHM_VERSION
    )
    assert published_stability["algorithm_version"] == STABILITY_ALGORITHM_VERSION
    assert (
        published_stability["adjacent_period_policy"]
        == (fixture["runs"]["stable"]["adjacent_period_stability_policy"])
    )
    assert published_stability["cluster_period_support"]
    assert published_stability["adjacent_period_evidence"]
    assert published_motifs["algorithm_version"] == MOTIF_ALGORITHM_VERSION
    assert published_motifs["feature_names"] == fixture["feature_names"]
    assert published_config["motif_regime_assignments"] == fixture["motif_regime_assignments"]
    assert published_motifs["development_regime_universe"] == ["balanced", "expanding"]
    assert "unclassified" not in json.dumps(published_motifs)
    assert published_motifs["candidates"]
    assert all(candidate["accepted"] for candidate in published_motifs["candidates"])
    for candidate in published_motifs["candidates"]:
        assert len(candidate["seed_tie_evidence"]) == 4
        assert len(candidate["subsample_evidence"]) == 4
        assert len(candidate["parameter_evidence"]) == 18
        assert all(
            item["passed"] is (not item["rejection_reasons"])
            for item in (
                candidate["seed_tie_evidence"]
                + candidate["subsample_evidence"]
                + candidate["parameter_evidence"]
            )
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
    rejected_motifs = _load_json(rejected_first_root / rejected_first.run_id / "motifs.json")
    assert rejected_motifs["candidates"]
    assert all(not candidate["accepted"] for candidate in rejected_motifs["candidates"])
    assert all(candidate["rejection_reasons"] for candidate in rejected_motifs["candidates"])
    assert all(candidate["universe_support"] for candidate in rejected_motifs["candidates"])


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
