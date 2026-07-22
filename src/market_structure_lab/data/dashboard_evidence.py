"""Build the deployment-safe dashboard contract from verified local evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_structure_lab.core.artifact_io import (
    bounded_subdirectories,
    path_exists_no_follow,
    read_bounded_regular,
)
from market_structure_lab.data.export import read_snapshot_manifest, verify_snapshot
from market_structure_lab.data.freshness_sync import read_latest_freshness_artifacts
from market_structure_lab.data.reconciliation import (
    ReconciliationRunManifest,
    WorkUnitManifest,
    promotion_receipt_path,
    read_reconciliation_promotion_receipt,
    read_reconciliation_run,
    verify_work_unit_publication,
)
from market_structure_lab.features import builtin_feature_registry
from market_structure_lab.discovery.program import canonical_policy_sha256
from market_structure_lab.experiments import (
    ExperimentMode,
    TerminalStatus,
    TrialManifest,
    verify_trial_receipt,
)

LAB_EVIDENCE_SCHEMA_VERSION = 1
MAX_LAB_EVIDENCE_BYTES = 8 * 1024 * 1024
_PHASE4_FIXTURE_SCHEMA_VERSION = "phase4-discovery-fixture-v4"
_PHASE4_FIXTURE_PRODUCER = {
    "builder_id": "phase4_fixture_producer.Phase4FixtureFeatureProducer",
    "builder_version": "phase4-fixture-producer-v2",
    "input_schema": "phase4-fixture-source-v2",
}
_PHASE4_ROW_KEYS = {"timestamp", "symbol", "segment_id", "source"}
_PHASE4_SOURCE_KEYS = {
    "auction_location_ratio",
    "baseline_volume",
    "current_volume",
    "volume_scale",
}
_MAX_DASHBOARD_LEDGER_GROUPS = 1_000
_MAX_DASHBOARD_TRIAL_RECEIPTS = 100_000
_MAX_TASK14_EVIDENCE_BYTES = 64 * 1024 * 1024
_STAGING_TOKENS = ("staging", ".partial", ".tmp")
_TASK14_PROGRAM_ID = "PG-000004"
_TASK14_RUN_ID = "DR-000704"


def generate_lab_evidence(
    repository_root: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a browser-safe contract after verifying every referenced artifact."""
    freshness_root = repository_root / "data" / "exports" / "freshness"
    reconciliation_root = repository_root / "data" / "exports" / "reconciliation"
    trial_root = repository_root / "data" / "exports" / "trials"
    freshness = read_latest_freshness_artifacts(freshness_root)
    report = freshness.report
    bounded_run = read_reconciliation_run(reconciliation_root / "RR-000002.run.json")
    history_run = read_reconciliation_run(reconciliation_root / "RR-000008.run.json")
    bounded = _reconciliation_snapshot(reconciliation_root, bounded_run, require_complete=True)
    history = _reconciliation_snapshot(reconciliation_root, history_run, require_complete=False)
    history_complete = history["verified_work_units"] == history["expected_work_units"]
    history_promotion = _reconciliation_promotion_snapshot(reconciliation_root, history_run)
    history_promoted = history_promotion is not None
    if history_promoted and not history_complete:
        raise ValueError("promotion receipt exists for incomplete reconciliation evidence")
    replay = _phase4_fixture_evidence(repository_root)
    registry = builtin_feature_registry()
    trials = _read_dashboard_trial_ledger(trial_root)
    task14_checkpoint = _task14_programme_checkpoint(repository_root, trials)
    trials_by_mode = {mode.value: 0 for mode in ExperimentMode}
    trials_by_status = {status.value: 0 for status in TerminalStatus}
    trials_by_mode_and_status = {
        mode.value: {status.value: 0 for status in TerminalStatus} for mode in ExperimentMode
    }
    for trial in trials:
        trials_by_mode[trial.mode.value] += 1
        trials_by_status[trial.status.value] += 1
        trials_by_mode_and_status[trial.mode.value][trial.status.value] += 1
    timestamp = _canonical_publication_timestamp(
        generated_at
        or datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    statuses = Counter(item.status.value for item in report.symbols)

    return {
        "schema_version": LAB_EVIDENCE_SCHEMA_VERSION,
        "generated_at": timestamp,
        "connection_mode": "deployment_snapshot",
        "freshness": {
            "evidence_timestamp": report.as_of,
            "plan_filename": freshness.manifest_filename,
            "plan_sha256": report.manifest_sha256,
            "report_filename": freshness.report_filename,
            "report_sha256": report.sha256(),
            "dump_sha256": report.dump_sha256,
            "before_missing_minutes": report.before_missing_minutes,
            "recovered_minutes": report.recovered_minutes,
            "remaining_missing_minutes": report.after_missing_minutes,
            "inserted_rows": report.inserted_rows,
            "coverage_conserved": report.coverage_conserved,
            "status_counts": dict(sorted(statuses.items())),
            "symbols": [
                {
                    "symbol": item.symbol,
                    "timeframe": item.timeframe,
                    "status": item.status.value,
                    "compatibility": item.compatibility_state.value,
                    "before_missing_minutes": item.before_missing_minutes,
                    "recovered_minutes": item.recovered_minutes,
                    "remaining_missing_minutes": item.after_missing_minutes,
                    "inserted_rows": item.inserted_rows,
                    "current_through_cutoff": item.current_through_cutoff,
                    "reason": item.reason,
                }
                for item in report.symbols
            ],
        },
        "reconciliation": {
            "bounded_audit": {
                **bounded,
                "scope_label": "bounded audited keys",
                "completion_state": "bounded_audit_complete",
                "promotion_receipt_available": False,
                "promoted_verified_intervals": 0,
                "research_eligibility": "not_established_by_this_artifact",
            },
            "full_history": {
                **history,
                **(history_promotion or {}),
                "scope_label": (
                    "full-history reconciliation promoted"
                    if history_promoted
                    else "full-history audit complete; unpromoted"
                    if history_complete
                    else "partial full-history audit progress"
                ),
                "completion_state": (
                    "complete_promoted"
                    if history_promoted
                    else "complete_unpromoted"
                    if history_complete
                    else "partial"
                ),
                "promotion_receipt_available": history_promoted,
                "promoted_verified_intervals": (
                    int(history_promotion["promoted_verified_intervals"])
                    if history_promotion
                    else 0
                ),
                "research_eligibility": (
                    "reconciliation_provenance_established_snapshot_not_frozen"
                    if history_promoted
                    else "blocked_pending_deliberate_promotion_receipt"
                    if history_complete
                    else "blocked_until_complete_and_deliberately_promoted"
                ),
            },
        },
        "software_replay": replay,
        "feature_registry": {
            "feature_set_id": registry.feature_set_id,
            "registry_id": registry.registry_id,
            "registry_sha256": registry.sha256,
            "definitions": [item.to_dict() for item in registry.definitions],
        },
        "experiment_accuracy": {
            "status": "not_estimable",
            "verified_real_trial_artifacts": {
                "by_mode": trials_by_mode,
                "by_status": trials_by_status,
                "by_mode_and_status": trials_by_mode_and_status,
                "total": len(trials),
            },
            "trial_ledger_status": ("implemented_with_receipts" if trials else "implemented_empty"),
            "trial_receipt_schema": "trial-receipt-v2",
            "fixture_trials_counted_as_real": False,
            "derivation_chain_verified": False,
            "task14_programme_checkpoint": task14_checkpoint,
            "scope": "verified real-trial artifacts supplied to this dashboard contract",
            "claim": (
                "Verified terminal receipts exist, but predictive accuracy remains not estimable."
                if trials
                else "The immutable ledger is implemented but contains zero verified real trials; "
                "numeric accuracy is not estimable."
            ),
        },
        "phase_gate": _phase_gate(
            history_promoted=history_promoted,
            history_complete=history_complete,
            task14_complete=task14_checkpoint is not None,
        ),
    }


def _read_dashboard_trial_ledger(root: Path) -> tuple[TrialManifest, ...]:
    """Verify flat and one-level grouped terminal receipts within fixed scan bounds."""
    if not path_exists_no_follow(root):
        return ()
    receipts: list[TrialManifest] = []
    seen: set[str] = set()
    children = bounded_subdirectories(root, maximum=_MAX_DASHBOARD_LEDGER_GROUPS)
    for child in children:
        _reject_staging_name(child.name)
        candidates: tuple[Path, ...]
        if path_exists_no_follow(child / "receipt.json"):
            candidates = (child,)
        else:
            candidates = bounded_subdirectories(
                child,
                maximum=_MAX_DASHBOARD_TRIAL_RECEIPTS - len(receipts),
            )
            if not candidates:
                raise RuntimeError("trial ledger contains an empty grouped entry")
        for candidate in candidates:
            _reject_staging_name(candidate.name)
            receipt = verify_trial_receipt(candidate)
            if receipt.run_id in seen:
                raise RuntimeError("trial ledger contains conflicting duplicate run IDs")
            seen.add(receipt.run_id)
            receipts.append(receipt)
            if len(receipts) > _MAX_DASHBOARD_TRIAL_RECEIPTS:
                raise RuntimeError("trial ledger exceeds the bounded receipt limit")
    return tuple(sorted(receipts, key=lambda item: item.run_id))


def _reject_staging_name(name: str) -> None:
    lowered = name.lower()
    if name.startswith(".") or any(token in lowered for token in _STAGING_TOKENS):
        raise RuntimeError("trial ledger contains a hidden or staging-like entry")


def _task14_programme_checkpoint(
    repository_root: Path,
    trials: tuple[TrialManifest, ...],
) -> dict[str, Any] | None:
    program_root = repository_root / "data" / "exports" / "discovery-programs" / _TASK14_PROGRAM_ID
    if not path_exists_no_follow(program_root):
        return None
    expected_attempts = {
        "DR-000701": TerminalStatus.FAILED,
        "DR-000702": TerminalStatus.FAILED,
        "DR-000703": TerminalStatus.REJECTED,
        "DR-000704": TerminalStatus.REJECTED,
    }
    task14 = {trial.run_id: trial for trial in trials if trial.run_id in expected_attempts}
    if set(task14) != set(expected_attempts) or any(
        trial.mode is not ExperimentMode.DISCOVERY or trial.status is not expected_attempts[run_id]
        for run_id, trial in task14.items()
    ):
        raise RuntimeError("Task 14 trial ledger does not match the immutable attempt history")
    preregistration = _verified_wrapped_artifact(program_root / "preregistration.json")
    selected_universe = _verified_wrapped_artifact(program_root / "selected-universe.json")
    vector = _read_bounded_json_object(program_root / "reliability-vector.json")
    vector_sha256 = vector.get("sha256")
    vector_body = {key: value for key, value in vector.items() if key != "sha256"}
    if not _is_sha256(vector_sha256) or canonical_policy_sha256(vector_body) != vector_sha256:
        raise RuntimeError("Task 14 reliability vector logical identity is invalid")
    preregistration_sha256 = preregistration["sha256"]
    artifact = preregistration["artifact"]
    universe_artifact = selected_universe["artifact"]
    trial = task14[_TASK14_RUN_ID]
    if (
        artifact.get("schema_version") != "discovery-preregistration-v2"
        or artifact.get("preregistration_id") != _TASK14_PROGRAM_ID
        or artifact.get("selected_universe_sha256") != selected_universe["sha256"]
        or artifact.get("dataset_snapshot_id") != "DS-000704"
        or artifact.get("trial_count") != 1
        or artifact.get("trials")
        != [{"clusters": 3, "pca_components": 3, "run_id": _TASK14_RUN_ID, "seeds": [7, 11, 13]}]
        or artifact.get("phase3_execution", {}).get("maximum_rows") != 960
        or artifact.get("run_execution", {}).get("work_budget", {}).get("maximum_feature_cells")
        != 8_628
    ):
        raise RuntimeError("Task 14 preregistration boundary is invalid")
    _validate_task14_preregistration_boundary(
        artifact,
        universe_artifact,
        trial.canonical_config,
    )
    if (
        vector.get("schema_version") != "discovery-reliability-vector-v2"
        or vector.get("preregistration_sha256") != preregistration_sha256
        or vector.get("attempted_trial_count") != 1
        or vector.get("status_counts") != {"rejected": 1}
        or vector.get("conclusion") != "rejected"
        or not isinstance(vector.get("trials"), list)
        or len(vector["trials"]) != 1
    ):
        raise RuntimeError("Task 14 reliability vector contract is invalid")
    vector_trial = vector["trials"][0]
    if (
        not isinstance(vector_trial, dict)
        or vector_trial.get("run_id") != trial.run_id
        or vector_trial.get("status") != trial.status.value
        or vector_trial.get("receipt_sha256") != trial.receipt_sha256
        or vector_trial.get("identity_sha256") != trial.identity_sha256
        or vector_trial.get("artifact_sha256") != dict(trial.artifact_sha256)
        or trial.canonical_config.get("preregistration_sha256") != preregistration_sha256
        or trial.dataset_snapshot.identifier != "DS-000704"
        or trial.feature_publication.identifier != "FP-000704"
        or trial.normalizer.identifier != "NZ-000704"
        or trial.outcome_policy is not None
        or trial.cost_policy is not None
        or trial.candidate_id is not None
    ):
        raise RuntimeError("Task 14 terminal receipt is not linked to the programme checkpoint")
    trial_directory = (
        repository_root / "data" / "exports" / "trials" / "task14-PG-000004" / _TASK14_RUN_ID
    )
    metrics = _read_bounded_json_object(trial_directory / "metrics.json")
    if metrics.get("status") != "rejected_unstable":
        raise RuntimeError("Task 14 scientific status is inconsistent")
    snapshot_directory = (
        repository_root
        / "data"
        / "exports"
        / "snapshots"
        / f"dataset_version={trial.dataset_snapshot.identifier}"
    )
    snapshot = read_snapshot_manifest(snapshot_directory / "manifest.json")
    verify_snapshot(snapshot_directory, snapshot)
    if (
        snapshot.snapshot_sha256 != trial.dataset_snapshot.sha256
        or snapshot.row_count != 960
        or snapshot.min_timestamp != "2025-02-01T00:00:00Z"
        or snapshot.max_timestamp != "2025-02-01T15:59:00Z"
    ):
        raise RuntimeError("Task 14 snapshot is outside the frozen non-holdout boundary")
    event_id = trial.canonical_config.get("event_publication_id")
    event_sha256 = trial.canonical_config.get("event_publication_sha256")
    if event_id != "EP-000704" or not _is_sha256(event_sha256):
        raise RuntimeError("Task 14 event publication identity is invalid")
    return {
        "schema_version": "task14-programme-checkpoint-v1",
        "program_id": _TASK14_PROGRAM_ID,
        "run_id": _TASK14_RUN_ID,
        "programme_conclusion": "rejected",
        "scientific_status": "rejected_unstable",
        "preregistration_sha256": preregistration_sha256,
        "reliability_vector_sha256": vector_sha256,
        "trial_receipt_sha256": trial.receipt_sha256,
        "dataset_snapshot": trial.dataset_snapshot.to_dict(),
        "feature_publication": trial.feature_publication.to_dict(),
        "event_publication": {"id": event_id, "sha256": event_sha256},
        "normalizer": trial.normalizer.to_dict(),
        "holdout_rows_accessed": False,
        "outcomes_attached": False,
    }


def _validate_task14_preregistration_boundary(
    preregistration: Mapping[str, object],
    selected_universe: Mapping[str, object],
    canonical_config: Mapping[str, object],
) -> None:
    discovery = {
        "role": "discovery",
        "start": "2025-02-01T00:00:00Z",
        "end": "2025-02-01T08:00:00Z",
        "symbols": ["APTUSDT"],
    }
    development = {
        "role": "development",
        "start": "2025-02-01T08:00:00Z",
        "end": "2025-02-01T16:00:00Z",
        "symbols": ["APTUSDT"],
    }
    holdout = {
        "role": "holdout",
        "start": "2025-02-01T16:00:00Z",
        "end": "2025-02-02T00:00:00Z",
        "symbols": ["APTUSDT"],
        "asset_holdouts": ["IMXUSDT"],
    }
    expected_split = {
        "split_id": "task14-first-real-discovery-v4",
        "sha256": preregistration.get("split_sha256"),
        "discovery": discovery,
        "development": development,
        "holdout": {key: value for key, value in holdout.items() if key != "asset_holdouts"},
        "asset_holdouts": ["IMXUSDT"],
    }
    motif_policy = canonical_config.get("motif_stability_policy")
    if (
        preregistration.get("discovery_metadata") != discovery
        or preregistration.get("development_metadata") != development
        or preregistration.get("holdout_metadata") != holdout
        or selected_universe.get("schema_version") != "selected-universe-v2"
        or selected_universe.get("selection_id") != "SU-000704"
        or selected_universe.get("symbols") != ["APTUSDT"]
        or selected_universe.get("timeframe") != "1m"
        or selected_universe.get("start") != discovery["start"]
        or selected_universe.get("end") != development["end"]
        or canonical_config.get("run_id") != _TASK14_RUN_ID
        or canonical_config.get("dataset_snapshot_id") != "DS-000704"
        or canonical_config.get("feature_set_id") != "FS-000001"
        or canonical_config.get("feature_names") != preregistration.get("feature_names")
        or canonical_config.get("max_rows") != 480
        or canonical_config.get("split") != expected_split
        or not isinstance(motif_policy, Mapping)
        or motif_policy.get("max_windows") != 512
        or canonical_policy_sha256(motif_policy) != preregistration.get("motif_policy_sha256")
    ):
        raise RuntimeError("Task 14 preregistration boundary is invalid")


def _verified_wrapped_artifact(path: Path) -> dict[str, Any]:
    value = _read_bounded_json_object(path)
    if set(value) != {"artifact", "sha256"} or not isinstance(value["artifact"], dict):
        raise RuntimeError("Task 14 programme artifact contract is malformed")
    if (
        not _is_sha256(value["sha256"])
        or canonical_policy_sha256(value["artifact"]) != value["sha256"]
    ):
        raise RuntimeError("Task 14 programme artifact identity is invalid")
    return value


def _read_bounded_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_bounded_regular(path, _MAX_TASK14_EVIDENCE_BYTES))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Task 14 evidence is missing or malformed") from error
    if not isinstance(value, dict):
        raise RuntimeError("Task 14 evidence must be a JSON object")
    return value


def _phase_gate(
    *,
    history_promoted: bool,
    history_complete: bool,
    task14_complete: bool,
) -> dict[str, str]:
    if task14_complete:
        return {
            "active_phase": "Phase C Task 14 outcome-blind discovery: complete",
            "status": "complete_task15_authorized",
            "next_required_evidence": "execute the separate Task 15 Phase 5 validation plan",
        }
    if history_promoted:
        return {
            "active_phase": "Phase B Task 13 deterministic hardening: complete",
            "status": "complete_task14_authorized",
            "next_required_evidence": (
                "run authorized Task 14 outcome-blind discovery after verified Task 13 remote checkpoint"
            ),
        }
    return {
        "active_phase": "Phase 0: trustworthy foundation",
        "status": "remediation_in_progress",
        "next_required_evidence": (
            "obtain explicit promotion approval and publish an immutable promotion receipt"
            if history_complete
            else "complete RR-000008 and obtain explicit promotion approval"
        ),
    }


def write_lab_evidence(
    repository_root: Path,
    output_path: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Generate and atomically write the public dashboard evidence contract."""
    evidence = generate_lab_evidence(repository_root, generated_at=generated_at)
    content = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(content) > MAX_LAB_EVIDENCE_BYTES:
        raise ValueError("dashboard evidence exceeds the serialized byte budget")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(output_path)
    return evidence


def _reconciliation_snapshot(
    output_root: Path,
    run: ReconciliationRunManifest,
    *,
    require_complete: bool,
) -> dict[str, Any]:
    planned = {item.work_unit_id: item for item in run.work_units}
    manifests: dict[str, WorkUnitManifest] = {}
    run_root = output_root / f"run_id={run.run_id}"
    for path in sorted(run_root.glob("symbol=*/year=*/month=*/manifest.json")):
        manifest = verify_work_unit_publication(path.parent)
        if manifest.run_id != run.run_id:
            raise ValueError("work-unit publication run identity does not match the frozen run")
        unit = planned.get(manifest.work_unit_id)
        if unit is None:
            raise ValueError("work-unit publication is not present in the frozen run")
        if manifest.work_unit_id in manifests:
            raise ValueError("duplicate work-unit publication found")
        start = datetime.fromtimestamp(unit.start_ms / 1_000, tz=UTC)
        expected_path = (
            f"run_id={run.run_id}/symbol={unit.symbol}/"
            f"year={start.year:04d}/month={start.month:02d}"
        )
        if manifest.publication_path != expected_path:
            raise ValueError("work-unit publication path does not match its frozen identity")
        if (output_root / manifest.publication_path).resolve() != path.parent.resolve():
            raise ValueError("work-unit publication is stored outside its manifested path")
        manifests[manifest.work_unit_id] = manifest
    if require_complete and set(manifests) != set(planned):
        raise ValueError("reconciliation evidence is missing frozen work-unit publications")

    classifications: Counter[str] = Counter()
    differing_fields: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for manifest in manifests.values():
        classifications.update(dict(manifest.classification_counts))
        differing_fields.update(dict(manifest.differing_field_counts))
        statuses[manifest.status] += 1
    manifest_set_sha256 = hashlib.sha256(
        json.dumps(
            sorted(item.manifest_sha256 for item in manifests.values()),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "run_id": run.run_id,
        "cutoff": run.cutoff,
        "algorithm_version": run.algorithm_version,
        "mapping_version": run.mapping_version,
        "run_manifest_sha256": run.manifest_sha256,
        "verified_manifest_set_sha256": manifest_set_sha256,
        "expected_work_units": len(run.work_units),
        "verified_work_units": len(manifests),
        "audited_keys": sum(item.row_count for item in manifests.values()),
        "replacement_rows": sum(item.replacement_row_count for item in manifests.values()),
        "work_unit_status_counts": dict(sorted(statuses.items())),
        "classification_counts": dict(sorted(classifications.items())),
        "differing_field_counts": dict(sorted(differing_fields.items())),
    }


def _reconciliation_promotion_snapshot(
    output_root: Path,
    run: ReconciliationRunManifest,
) -> dict[str, Any] | None:
    path = promotion_receipt_path(output_root, run.run_id)
    if not path.exists():
        return None
    receipt = read_reconciliation_promotion_receipt(path)
    if receipt.run_id != run.run_id or receipt.manifest_sha256 != run.manifest_sha256:
        raise ValueError("promotion receipt identity does not match the frozen run")
    return {
        "promoted_at": receipt.promoted_at,
        "promoted_verified_intervals": receipt.coverage_interval_count,
        "promotion_receipt_content_sha256": receipt.content_sha256,
        "promotion_coverage_logical_sha256": receipt.coverage_logical_sha256,
        "replacement_logical_sha256": receipt.replacement_logical_sha256,
        "canonical_logical_sha256": receipt.canonical_logical_sha256,
    }


def _phase4_fixture_evidence(repository_root: Path) -> dict[str, Any]:
    root = repository_root / "tests" / "fixtures" / "phase4"
    fixture_path = root / "discovery_run_v1.json"
    input_path = root / "discovery_interpretation_input_v1.json"
    response_path = root / "discovery_interpretation_response_v1.json"
    fixture = _read_json_object(fixture_path)
    interpretation_input = _read_json_object(input_path)
    response = _read_json_object(response_path)
    expected_top = {
        "schema_version",
        "dataset_snapshot",
        "source_identity",
        "registry",
        "split",
        "feature_names",
        "caps",
        "code_commit",
        "lock_sha256",
        "discovery_rows",
        "development_rows",
        "event_ids",
        "event_publication_sha256",
        "durations_seconds",
        "motif_regime_assignments",
        "runs",
        "interpretation_input_sha256",
        "interpretation_response_sha256",
        "interpretation_publication",
        "fixture_producer",
    }
    if (
        set(fixture) != expected_top
        or fixture.get("schema_version") != _PHASE4_FIXTURE_SCHEMA_VERSION
    ):
        raise ValueError("unsupported Phase 4 fixture schema")
    producer = fixture.get("fixture_producer")
    if not isinstance(producer, dict) or producer != _PHASE4_FIXTURE_PRODUCER:
        raise ValueError("Phase 4 fixture producer contract is invalid")
    registry_sha256 = _validate_phase4_registry(fixture.get("registry"))
    _validate_phase4_source_rows(fixture.get("discovery_rows"), label="discovery")
    _validate_phase4_source_rows(fixture.get("development_rows"), label="development")
    input_sha = _sha256_file(input_path)
    response_sha = _sha256_file(response_path)
    if fixture["interpretation_input_sha256"] != input_sha:
        raise ValueError("Phase 4 interpretation input hash does not match the fixture")
    if fixture["interpretation_response_sha256"] != response_sha:
        raise ValueError("Phase 4 interpretation response hash does not match the fixture")
    runs = fixture.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"stable", "rejected"}:
        raise ValueError("Phase 4 fixture requires stable and rejected runs")
    stable = _fixture_run(runs["stable"], expected_status="completed")
    rejected = _fixture_run(runs["rejected"], expected_status="rejected_unstable")
    interpretation_publication = _fixture_interpretation_publication(
        fixture.get("interpretation_publication"),
        stable=stable,
    )
    if interpretation_input.get("schema_version") != "phase4-interpretation-input-v2":
        raise ValueError("unsupported Phase 4 interpretation input schema")
    if (
        interpretation_input.get("run_id") != stable["run_id"]
        or interpretation_input.get("run_manifest_sha256") != stable["manifest_sha256"]
    ):
        raise ValueError("Phase 4 interpretation input is linked to a different run")
    evidence_rows = interpretation_input.get("evidence")
    if not isinstance(evidence_rows, list):
        raise ValueError("Phase 4 interpretation input evidence must be a list")
    evidence_sha = hashlib.sha256(
        json.dumps(evidence_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if interpretation_input.get("evidence_sha256") != evidence_sha:
        raise ValueError("Phase 4 interpretation evidence hash does not match")
    interpretations = response.get("interpretations")
    if not isinstance(interpretations, list):
        raise ValueError("Phase 4 interpretation response is invalid")
    by_id = {
        item["behaviour_id"]: item
        for item in interpretations
        if isinstance(item, dict) and isinstance(item.get("behaviour_id"), str)
    }
    behaviours: list[dict[str, Any]] = []
    for row in evidence_rows:
        if not isinstance(row, dict) or not isinstance(row.get("behaviour"), dict):
            raise ValueError("Phase 4 behaviour evidence is invalid")
        behaviour = row["behaviour"]
        behaviour_id = behaviour.get("behaviour_id")
        interpretation = by_id.get(behaviour_id)
        if not isinstance(behaviour_id, str) or interpretation is None:
            raise ValueError("Phase 4 interpretations do not cover exact behaviour IDs")
        behaviours.append(
            {
                "behaviour_id": behaviour_id,
                "neutral_name": interpretation.get("neutral_name"),
                "centroid": behaviour.get("feature_centroid"),
                "frequency": behaviour.get("frequency"),
                "asset_coverage": behaviour.get("asset_coverage"),
                "seed_ari": behaviour.get("stability", {}).get("seed_ari"),
                "parameter_perturbation_ari": behaviour.get("stability", {}).get(
                    "parameter_perturbation_ari"
                ),
                "falsifiable_hypothesis": interpretation.get("falsifiable_hypothesis"),
                "cautions": interpretation.get("spuriousness_reasons"),
            }
        )
    if sorted(by_id) != sorted(stable["behaviour_ids"]):
        raise ValueError("Phase 4 response behaviour IDs differ from the stable fixture run")
    return {
        "status": "fixture_contract_present",
        "fixture_kind": "synthetic_golden",
        "fixture_path": "tests/fixtures/phase4/discovery_run_v1.json",
        "fixture_sha256": _sha256_file(fixture_path),
        "fixture_schema_version": _PHASE4_FIXTURE_SCHEMA_VERSION,
        "fixture_producer": dict(_PHASE4_FIXTURE_PRODUCER),
        "fixture_registry_sha256": registry_sha256,
        "interpretation_input_sha256": input_sha,
        "interpretation_response_sha256": response_sha,
        "interpretation_publication_manifest_sha256": interpretation_publication["manifest_sha256"],
        "verification_receipt_available": False,
        "claim": "fixture only; no real-market recurrence or predictive evidence",
        "runs": {"stable": stable, "rejected": rejected},
        "behaviours": sorted(behaviours, key=lambda item: item["behaviour_id"]),
    }


def _fixture_run(value: object, *, expected_status: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("expected"), dict):
        raise ValueError("Phase 4 run fixture is invalid")
    expected = value["expected"]
    metrics = expected.get("metrics")
    artifact_sha256 = expected.get("artifact_sha256")
    expected_artifacts = {
        "behaviours.json",
        "clustering.json",
        "config.json",
        "metrics.json",
        "missingness.json",
        "motifs.json",
        "projection.json",
        "stability.json",
        "summary.md",
        "transitions.json",
    }
    if (
        not isinstance(value.get("run_id"), str)
        or expected.get("status") != expected_status
        or not _is_sha256(expected.get("manifest_sha256"))
        or not _is_sha256(expected.get("identity_sha256"))
        or not _is_sha256(expected.get("config_sha256"))
        or not isinstance(expected.get("behaviour_ids"), list)
        or expected.get("transition_algorithm_version") != "boundary-aware-dwell-transitions-v3"
        or not isinstance(metrics, dict)
        or not isinstance(artifact_sha256, dict)
        or set(artifact_sha256) != expected_artifacts
        or any(not _is_sha256(digest) for digest in artifact_sha256.values())
    ):
        raise ValueError("Phase 4 run fixture contract is invalid")
    return {
        "run_id": value["run_id"],
        "status": expected_status,
        "manifest_sha256": expected["manifest_sha256"],
        "identity_sha256": expected["identity_sha256"],
        "config_sha256": expected["config_sha256"],
        "artifact_sha256": dict(sorted(artifact_sha256.items())),
        "behaviour_ids": expected["behaviour_ids"],
        "transition_algorithm_version": expected["transition_algorithm_version"],
        "metrics": metrics,
    }


def _validate_phase4_registry(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {"feature_set_id", "definitions"}:
        raise ValueError("Phase 4 fixture registry contract is invalid")
    if value.get("feature_set_id") != "FS-000601":
        raise ValueError("Phase 4 fixture registry feature-set ID is invalid")
    definitions = value.get("definitions")
    if not isinstance(definitions, list) or len(definitions) != 2:
        raise ValueError("Phase 4 fixture registry definitions are invalid")
    by_name = {item.get("name"): item for item in definitions if isinstance(item, dict)}
    expected_contracts = {
        "auction_location": {
            "value_kind": "float",
            "source_fields": ["fixture.auction_location_ratio"],
            "trailing_window": "current_observation",
            "observable_cutoff_rule": "at_information_cutoff",
            "normalization_requirement": "not_required",
        },
        "volume_deviation_from_baseline": {
            "value_kind": "float",
            "source_fields": [
                "fixture.baseline_volume",
                "fixture.current_volume",
                "fixture.volume_scale",
            ],
            "trailing_window": "current_observation",
            "observable_cutoff_rule": "at_information_cutoff",
            "normalization_requirement": "not_required",
        },
    }
    if set(by_name) != set(expected_contracts):
        raise ValueError("Phase 4 fixture registry field names are invalid")
    for name, contract in expected_contracts.items():
        definition = by_name[name]
        if (
            any(definition.get(field) != expected for field, expected in contract.items())
            or definition.get("builder_id") != _PHASE4_FIXTURE_PRODUCER["builder_id"]
            or definition.get("builder_version") != _PHASE4_FIXTURE_PRODUCER["builder_version"]
            or definition.get("future_outcome_prohibited") is not True
        ):
            raise ValueError("Phase 4 fixture registry producer contract is invalid")
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_phase4_source_rows(value: object, *, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Phase 4 fixture {label} rows are invalid")
    for row in value:
        if not isinstance(row, dict) or set(row) != _PHASE4_ROW_KEYS:
            raise ValueError(f"Phase 4 fixture {label} row schema is invalid")
        source = row.get("source")
        if not isinstance(source, dict) or set(source) != _PHASE4_SOURCE_KEYS:
            raise ValueError(f"Phase 4 fixture {label} raw source schema is invalid")
        if (
            not isinstance(row.get("timestamp"), str)
            or not isinstance(row.get("symbol"), str)
            or isinstance(row.get("segment_id"), bool)
            or not isinstance(row.get("segment_id"), int)
            or any(
                isinstance(item, bool) or not isinstance(item, (int, float))
                for item in source.values()
            )
            or not math.isfinite(float(source["auction_location_ratio"]))
            or not math.isfinite(float(source["baseline_volume"]))
            or not math.isfinite(float(source["current_volume"]))
            or not math.isfinite(float(source["volume_scale"]))
            or float(source["volume_scale"]) <= 0
        ):
            raise ValueError(f"Phase 4 fixture {label} raw source values are invalid")


def _fixture_interpretation_publication(
    value: object,
    *,
    stable: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"expected"}:
        raise ValueError("Phase 4 fixture interpretation publication is invalid")
    expected = value.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("Phase 4 fixture interpretation publication is invalid")
    artifacts = expected.get("artifact_sha256")
    if (
        expected.get("schema_version") != "interpretation-manifest-v2"
        or expected.get("run_id") != stable["run_id"]
        or expected.get("behaviour_ids") != stable["behaviour_ids"]
        or not _is_sha256(expected.get("identity_sha256"))
        or not _is_sha256(expected.get("manifest_sha256"))
        or not isinstance(artifacts, dict)
        or set(artifacts) != {"evidence.json", "interpretations.json"}
        or any(not _is_sha256(digest) for digest in artifacts.values())
    ):
        raise ValueError("Phase 4 fixture interpretation publication contract is invalid")
    return expected


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"evidence artifact is not valid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"evidence artifact must be a JSON object: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_publication_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("generated_at must be a canonical UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("generated_at must be a valid UTC ISO-8601 timestamp") from error
    canonical = parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise ValueError("generated_at must use canonical second-precision UTC ISO-8601")
    return canonical


__all__ = [
    "LAB_EVIDENCE_SCHEMA_VERSION",
    "MAX_LAB_EVIDENCE_BYTES",
    "generate_lab_evidence",
    "write_lab_evidence",
]
