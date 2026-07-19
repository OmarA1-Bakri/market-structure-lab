from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pytest

import market_structure_lab.data.dashboard_evidence as dashboard_evidence_module
from market_structure_lab.data.dashboard_evidence import generate_lab_evidence, write_lab_evidence
from market_structure_lab.data.freshness import (
    CanonicalSeriesState,
    FreshnessManifest,
    FreshnessPlanningStatus,
    FreshnessSymbolPlan,
)
from market_structure_lab.data.freshness_sync import (
    build_freshness_report,
    write_freshness_artifacts,
)
from market_structure_lab.data.gaps import GapRange, ProvenanceState, SourceIdentity
from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationPromotion,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    VerifiedCoverageInterval,
    freeze_reconciliation_run,
    publish_work_unit,
    read_reconciliation_run,
    write_reconciliation_promotion_receipt,
    write_reconciliation_run,
)
from market_structure_lab.experiments import (
    ArtifactIdentity,
    ExperimentConfig,
    ExperimentMode,
    TerminalStatus,
    TrialRange,
    save_experiment_result,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phase4"


def _sha(character: str) -> str:
    return character * 64


def _freshness(root: Path) -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 120_000, 1)
    source = SourceIdentity(_sha("a"), 1, "market-data-candles-v1")
    before_symbol = FreshnessSymbolPlan(
        symbol="BTCUSDT",
        timeframe="1m",
        provenance_state=ProvenanceState.COMPATIBLE,
        status=FreshnessPlanningStatus.FETCH_REQUIRED,
        reason="one missing minute",
        canonical_state=CanonicalSeriesState("BTCUSDT", "1m", 0, 0, 1, 1),
        missing_ranges=(gap,),
    )
    after_symbol = FreshnessSymbolPlan(
        symbol="BTCUSDT",
        timeframe="1m",
        provenance_state=ProvenanceState.COMPATIBLE,
        status=FreshnessPlanningStatus.UP_TO_DATE,
        reason="recovered",
        canonical_state=CanonicalSeriesState("BTCUSDT", "1m", 0, 60_000, 2, 0),
        missing_ranges=(),
    )
    values = dict(
        manifest_version=1,
        dump_identity=source,
        compatibility_manifest_sha256=_sha("b"),
        as_of="2026-07-17T00:15:00Z",
        candidate_venue="binance",
        market_type="spot",
    )
    before = FreshnessManifest(canonical_row_count=1, symbols=(before_symbol,), **values)
    after = FreshnessManifest(canonical_row_count=2, symbols=(after_symbol,), **values)
    report = build_freshness_report(before, after, inserted_by_symbol={"BTCUSDT": 1})
    write_freshness_artifacts(before, report, root / "data" / "exports" / "freshness")


def _record(unit: ReconciliationWorkUnit) -> tuple[ReconciliationRecord, ...]:
    return tuple(
        ReconciliationRecord(
            unit.symbol,
            "1m",
            timestamp,
            ReconciliationClass.EXACT_MATCH,
            _sha("1"),
            _sha("1"),
            (),
        )
        for timestamp in range(unit.start_ms, unit.end_ms, 60_000)
    )


def _reconciliation(root: Path) -> None:
    output = root / "data" / "exports" / "reconciliation"
    output.mkdir(parents=True)
    start = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1_000)
    btc = ReconciliationWorkUnit.create("BTCUSDT", "1m", start, start + 120_000)
    eth = ReconciliationWorkUnit.create("ETHUSDT", "1m", start, start + 60_000)
    artifact = SourceArtifactIdentity(
        location="fixture.zip",
        payload_sha256=_sha("c"),
        published_sha256=_sha("c"),
        source_revision="fixture-v1",
        retrieved_at="2026-07-17T00:00:00Z",
    )
    for run_id, units, published in (
        ("RR-000002", (btc,), (btc,)),
        ("RR-000008", (btc, eth), (btc,)),
    ):
        run = freeze_reconciliation_run(
            run_id=run_id,
            cutoff=datetime(2026, 7, 17, tzinfo=UTC),
            dump_sha256=_sha("a"),
            source_row_count=3,
            mapping_version="mapping-v1",
            candidate_venue="binance",
            market_type="spot",
            source_revision="fixture-v1",
            algorithm_version="reconcile-v1",
            code_commit="abcdef0",
            uv_lock_sha256=_sha("d"),
            envelopes=tuple(TradingEnvelope(u.symbol, "1m", u.start_ms, u.end_ms) for u in units),
            work_units=units,
        )
        write_reconciliation_run(run, output / f"{run_id}.run.json")
        for unit in published:
            publish_work_unit(
                _record(unit),
                output_root=output,
                run=run,
                work_unit=unit,
                source_artifacts=(artifact,),
                max_rows_per_part=1,
            )


def _repository(tmp_path: Path) -> Path:
    _freshness(tmp_path)
    _reconciliation(tmp_path)
    destination = tmp_path / "tests" / "fixtures" / "phase4"
    destination.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES, destination)
    return tmp_path


def _complete_full_history(root: Path):
    output = root / "data" / "exports" / "reconciliation"
    run = read_reconciliation_run(output / "RR-000008.run.json")
    unit = next(item for item in run.work_units if item.symbol == "ETHUSDT")
    artifact = SourceArtifactIdentity(
        location="fixture.zip",
        payload_sha256=_sha("c"),
        published_sha256=_sha("c"),
        source_revision="fixture-v1",
        retrieved_at="2026-07-17T00:00:00Z",
    )
    publish_work_unit(
        _record(unit),
        output_root=output,
        run=run,
        work_unit=unit,
        source_artifacts=(artifact,),
        max_rows_per_part=1,
    )
    return output, run


def _trial_config(run_id: str, mode: ExperimentMode) -> ExperimentConfig:
    candidate = None if mode is ExperimentMode.DISCOVERY else f"HC-{run_id[-6:]}"
    policy_modes = {ExperimentMode.VALIDATION, ExperimentMode.STRATEGY}
    return ExperimentConfig(
        run_id=run_id,
        mode=mode,
        dataset_snapshot=ArtifactIdentity("DS-000001", _sha("1")),
        feature_publication=ArtifactIdentity("FP-000001", _sha("2")),
        feature_registry=ArtifactIdentity("FR-000001", _sha("3")),
        normalizer=ArtifactIdentity("NZ-000001", _sha("4")),
        frozen_split={"split_id": "split-v1", "sha256": _sha("5")},
        detector_version="detector-v1",
        candidate_id=candidate,
        candidate_version=None if candidate is None else "candidate-v1",
        code_commit="abcdef1",
        lock_sha256=_sha("6"),
        canonical_config={"parameter": 1},
        seed=7,
        symbols=("BTCUSDT",),
        timeframes=("1m",),
        ranges=(TrialRange("BTCUSDT", "1m", "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z"),),
        parent_ids=(),
        metrics_schema={"observations": "integer"},
        hypothesis=None if candidate is None else "Frozen falsifiable hypothesis.",
        outcome_policy=(ArtifactIdentity("OP-000001", _sha("7")) if mode in policy_modes else None),
        cost_policy=(ArtifactIdentity("CP-000001", _sha("8")) if mode in policy_modes else None),
    )


def test_dashboard_evidence_is_hermetic_exact_and_trial_scoped(tmp_path: Path) -> None:
    evidence = generate_lab_evidence(_repository(tmp_path), generated_at="2026-07-17T03:00:00Z")

    assert evidence["generated_at"] == "2026-07-17T03:00:00Z"
    assert evidence["freshness"]["recovered_minutes"] == 1
    bounded = evidence["reconciliation"]["bounded_audit"]
    assert bounded["audited_keys"] == 2
    assert bounded["verified_work_units"] == bounded["expected_work_units"] == 1
    history = evidence["reconciliation"]["full_history"]
    assert history["verified_work_units"] == 1
    assert history["expected_work_units"] == 2
    assert history["completion_state"] == "partial"
    accuracy = evidence["experiment_accuracy"]
    assert accuracy["status"] == "not_estimable"
    counts = accuracy["verified_real_trial_artifacts"]
    assert counts["total"] == 0
    assert set(counts["by_mode"].values()) == {0}
    assert set(counts["by_status"].values()) == {0}
    assert all(set(row.values()) == {0} for row in counts["by_mode_and_status"].values())
    assert accuracy["trial_ledger_status"] == "implemented_empty"
    assert accuracy["trial_receipt_schema"] == "trial-receipt-v2"
    assert accuracy["derivation_chain_verified"] is False
    assert accuracy["fixture_trials_counted_as_real"] is False
    replay = evidence["software_replay"]
    assert replay["fixture_schema_version"] == "phase4-discovery-fixture-v4"
    assert replay["fixture_producer"] == {
        "builder_id": "phase4_fixture_producer.Phase4FixtureFeatureProducer",
        "builder_version": "phase4-fixture-producer-v2",
        "input_schema": "phase4-fixture-source-v2",
    }
    assert replay["runs"]["stable"]["run_id"] == "DR-000601"
    assert replay["runs"]["stable"]["manifest_sha256"] == (
        "404da0b473a6b3d065c9ce009b37cb89d91e1394c28382b64854901e1295d27e"
    )
    assert replay["runs"]["stable"]["identity_sha256"] == (
        "b7d0be263d64c254257f5931d84cc48d26a4580834cdfed13fa09808c875a9d0"
    )
    assert replay["runs"]["stable"]["config_sha256"] == (
        "fc54c3f2e785bc46412c3fd2639071fa47c0282ac54d1d1ac51b9a1f5cec5889"
    )
    assert replay["runs"]["rejected"]["manifest_sha256"] == (
        "4ca6d4b6b3ca8e89b56c853f1b4235338ee5fa92d9b536e9cb3e07057ed371e9"
    )
    assert replay["runs"]["rejected"]["identity_sha256"] == (
        "661c2e6cccd9ec9c29cb097d748c252a50aefa5b1b1569d3571d669668df7296"
    )
    assert replay["runs"]["rejected"]["config_sha256"] == (
        "345747df49dabf330a2a095dd8e59e4829467d1e1ee4365625fd257f8c63631f"
    )
    assert replay["runs"]["stable"]["transition_algorithm_version"] == (
        "boundary-aware-dwell-transitions-v3"
    )
    assert replay["runs"]["stable"]["behaviour_ids"] == [
        "B-8254215A00884732",
        "B-94E253BA77DC21DF",
    ]
    assert {item["neutral_name"] for item in replay["behaviours"]} == {
        "High Volume Auction State",
        "Low Volume Auction State",
    }
    registry = evidence["feature_registry"]
    assert registry["feature_set_id"] == "FS-000001"
    assert len(registry["definitions"]) == 26
    assert {"missing_policy", "version", "value_kind", "leakage_class"} <= set(
        registry["definitions"][0]
    )
    assert "b20163a74cc0b4c19e2afbd214bf29e31ec14eb0d494e545db4bdc7a5c41ce98" not in (
        json.dumps(evidence, sort_keys=True)
    )


def test_dashboard_evidence_write_rejects_oversized_payload_before_filesystem_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path / "repository")
    output = tmp_path / "public" / "lab-evidence-v1.json"
    monkeypatch.setattr(dashboard_evidence_module, "MAX_LAB_EVIDENCE_BYTES", 1)

    with pytest.raises(ValueError, match="evidence exceeds"):
        write_lab_evidence(
            repository,
            output,
            generated_at="2026-07-17T03:00:00Z",
        )

    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".tmp").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda fixture: fixture.__setitem__("schema_version", "phase4-discovery-fixture-v2"),
        lambda fixture: fixture.__setitem__("schema_version", "phase4-discovery-fixture-v5"),
        lambda fixture: fixture.__setitem__("unexpected", True),
        lambda fixture: fixture["fixture_producer"].pop("builder_id"),
        lambda fixture: fixture["fixture_producer"].__setitem__("unexpected", True),
        lambda fixture: fixture["fixture_producer"].__setitem__("builder_version", "wrong-v1"),
        lambda fixture: fixture["discovery_rows"][0]["source"].pop("current_volume"),
        lambda fixture: fixture["discovery_rows"][0]["source"].__setitem__("unexpected", 1.0),
        lambda fixture: fixture["discovery_rows"][0]["source"].__setitem__("volume_scale", "100"),
    ],
    ids=(
        "stale-v2",
        "unknown-v5",
        "extra-top-level",
        "missing-producer-field",
        "extra-producer-field",
        "wrong-producer-version",
        "missing-raw-source",
        "extra-raw-source",
        "malformed-raw-source",
    ),
)
def test_dashboard_rejects_nonexact_phase4_v4_fixture_contract(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
) -> None:
    root = _repository(tmp_path)
    fixture_path = root / "tests" / "fixtures" / "phase4" / "discovery_run_v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    mutation(fixture)
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(ValueError, match="Phase 4 fixture"):
        generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")


def test_public_dashboard_evidence_uses_current_phase4_v4_manifest() -> None:
    repository = Path(__file__).parents[1]
    artifact = repository / "dashboard" / "public" / "data" / "lab-evidence-v1.json"
    evidence = json.loads(artifact.read_text(encoding="utf-8"))
    replay = evidence["software_replay"]

    assert replay["fixture_schema_version"] == "phase4-discovery-fixture-v4"
    assert replay["runs"]["stable"]["manifest_sha256"] == (
        "404da0b473a6b3d065c9ce009b37cb89d91e1394c28382b64854901e1295d27e"
    )
    assert replay["runs"]["rejected"]["manifest_sha256"] == (
        "4ca6d4b6b3ca8e89b56c853f1b4235338ee5fa92d9b536e9cb3e07057ed371e9"
    )
    assert (
        replay["fixture_sha256"]
        == hashlib.sha256((FIXTURES / "discovery_run_v1.json").read_bytes()).hexdigest()
    )
    assert "b20163a74cc0b4c19e2afbd214bf29e31ec14eb0d494e545db4bdc7a5c41ce98" not in (
        artifact.read_text(encoding="utf-8")
    )
    assert evidence["phase_gate"] == {
        "active_phase": "Phase B Task 13 deterministic hardening: complete",
        "next_required_evidence": (
            "run authorized Task 14 outcome-blind discovery after verified Task 13 remote checkpoint"
        ),
        "status": "complete_task14_authorized",
    }


def test_dashboard_counts_verified_real_trials_exactly_by_mode_and_status(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    ledger = root / "data" / "exports" / "trials"
    for run_id, mode, status in (
        ("TR-000001", ExperimentMode.DISCOVERY, TerminalStatus.COMPLETED),
        ("TR-000002", ExperimentMode.VALIDATION, TerminalStatus.REJECTED),
        ("TR-000003", ExperimentMode.VALIDATION, TerminalStatus.FAILED),
    ):
        save_experiment_result(
            config=_trial_config(run_id, mode),
            status=status,
            metrics=({} if status is TerminalStatus.FAILED else {"observations": 1}),
            conclusion="Terminal fixture receipt for generator verification.",
            started_at=datetime(2026, 7, 17, 3, 0, tzinfo=UTC),
            completed_at=datetime(2026, 7, 17, 3, 1, tzinfo=UTC),
            root=ledger,
        )

    evidence = generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")
    accuracy = evidence["experiment_accuracy"]
    counts = accuracy["verified_real_trial_artifacts"]

    assert accuracy["trial_ledger_status"] == "implemented_with_receipts"
    assert counts["total"] == 3
    assert counts["by_mode"] == {"discovery": 1, "hypothesis": 0, "validation": 2, "strategy": 0}
    assert counts["by_status"] == {
        "failed": 1,
        "inconclusive": 0,
        "abandoned": 0,
        "rejected": 1,
        "completed": 1,
    }
    assert counts["by_mode_and_status"]["validation"]["failed"] == 1
    assert counts["by_mode_and_status"]["validation"]["rejected"] == 1
    assert accuracy["status"] == "not_estimable"


@pytest.mark.parametrize("malformation", ["artifact_tamper", "extra_file", "staging"])
def test_dashboard_trial_ledger_fails_closed_on_any_malformed_entry(
    tmp_path: Path,
    malformation: str,
) -> None:
    root = _repository(tmp_path)
    ledger = root / "data" / "exports" / "trials"
    result = save_experiment_result(
        config=_trial_config("TR-000001", ExperimentMode.DISCOVERY),
        status=TerminalStatus.COMPLETED,
        metrics={"observations": 1},
        conclusion="Receipt.",
        started_at=datetime(2026, 7, 17, 3, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 17, 3, 1, tzinfo=UTC),
        root=ledger,
    )
    if malformation == "artifact_tamper":
        (result.path / "metrics.json").write_text("{}\n", encoding="utf-8")
    elif malformation == "extra_file":
        (result.path / "extra.json").write_text("{}\n", encoding="utf-8")
    else:
        (ledger / ".TR-000002.staging").mkdir()

    with pytest.raises(RuntimeError, match="tamper|unexpected|interrupted|staging"):
        generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")


def test_generator_rejects_missing_publication_and_fixture_tampering(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    success = next(
        (root / "data" / "exports" / "reconciliation" / "run_id=RR-000002").rglob("_SUCCESS")
    )
    success.unlink()
    with pytest.raises(ValueError, match="success marker"):
        generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")

    root = _repository(tmp_path / "second")
    response = root / "tests" / "fixtures" / "phase4" / "discovery_interpretation_response_v1.json"
    payload = json.loads(response.read_text(encoding="utf-8"))
    payload["interpretations"][0]["neutral_name"] = "tampered"
    response.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="response hash"):
        generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")


def test_complete_full_history_remains_unpromoted_and_research_blocked(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _complete_full_history(root)

    evidence = generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")
    history = evidence["reconciliation"]["full_history"]

    assert history["completion_state"] == "complete_unpromoted"
    assert history["scope_label"] == "full-history audit complete; unpromoted"
    assert history["promotion_receipt_available"] is False
    assert history["research_eligibility"] == "blocked_pending_deliberate_promotion_receipt"
    assert evidence["phase_gate"]["next_required_evidence"] == (
        "obtain explicit promotion approval and publish an immutable promotion receipt"
    )


def test_promoted_full_history_uses_checksum_verified_receipt_without_claiming_snapshot(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    output, run = _complete_full_history(root)
    coverage = tuple(
        VerifiedCoverageInterval(unit.symbol, unit.timeframe, unit.start_ms, unit.end_ms)
        for unit in run.work_units
    )
    write_reconciliation_promotion_receipt(
        output,
        run,
        ReconciliationPromotion(
            run_id=run.run_id,
            manifest_sha256=run.manifest_sha256,
            replacement_logical_sha256=_sha("7"),
            canonical_logical_sha256=_sha("8"),
            promoted_at="2026-07-18T13:45:00Z",
        ),
        coverage,
    )

    evidence = generate_lab_evidence(root, generated_at="2026-07-18T14:00:00Z")
    history = evidence["reconciliation"]["full_history"]

    assert history["completion_state"] == "complete_promoted"
    assert history["scope_label"] == "full-history reconciliation promoted"
    assert history["promotion_receipt_available"] is True
    assert history["promoted_verified_intervals"] == 2
    assert history["replacement_logical_sha256"] == _sha("7")
    assert history["canonical_logical_sha256"] == _sha("8")
    assert history["research_eligibility"] == (
        "reconciliation_provenance_established_snapshot_not_frozen"
    )
    assert evidence["phase_gate"] == {
        "active_phase": "Phase B Task 13 deterministic hardening: complete",
        "status": "complete_task14_authorized",
        "next_required_evidence": (
            "run authorized Task 14 outcome-blind discovery after verified Task 13 remote checkpoint"
        ),
    }


@pytest.mark.parametrize(
    "generated_at",
    [
        "2026-07-17T03:00:00",
        "2026-07-17T10:00:00+07:00",
        "2026-07-17T03:00:00.123Z",
        "not-a-timestamp",
    ],
)
def test_generator_rejects_noncanonical_publication_timestamps(
    tmp_path: Path,
    generated_at: str,
) -> None:
    with pytest.raises(ValueError, match="generated_at"):
        generate_lab_evidence(_repository(tmp_path), generated_at=generated_at)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda run: run.pop("transition_algorithm_version"),
        lambda run: run.__setitem__("transition_algorithm_version", "legacy-v1"),
        lambda run: run.__setitem__(
            "transition_algorithm_version",
            "boundary-aware-dwell-transitions-v2",
        ),
    ],
    ids=["missing", "wrong", "superseded-v2"],
)
def test_dashboard_verifier_rejects_invalid_transition_algorithm_version(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
) -> None:
    evidence = generate_lab_evidence(
        _repository(tmp_path / "repository"),
        generated_at="2026-07-17T03:00:00Z",
    )
    mutation(evidence["software_replay"]["runs"]["stable"])
    artifact = tmp_path / "lab-evidence-v1.json"
    artifact.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        [
            "node",
            "dashboard/scripts/verify-lab-evidence.mjs",
            str(artifact),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "transition algorithm version" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda accuracy: accuracy.__setitem__("trial_ledger_status", "not_implemented"),
        lambda accuracy: accuracy.__setitem__("derivation_chain_verified", True),
        lambda accuracy: accuracy["verified_real_trial_artifacts"]["by_status"].__setitem__(
            "completed", 1
        ),
    ],
    ids=["obsolete-status", "false-provenance", "inconsistent-count"],
)
def test_node_contract_rejects_malformed_trial_ledger_evidence(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
) -> None:
    evidence = generate_lab_evidence(
        _repository(tmp_path / "repository"),
        generated_at="2026-07-17T03:00:00Z",
    )
    mutation(evidence["experiment_accuracy"])
    artifact = tmp_path / "lab-evidence-v1.json"
    artifact.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        ["node", "dashboard/scripts/verify-lab-evidence.mjs", str(artifact)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "trial ledger contract" in result.stderr


def test_node_contract_rejects_stale_phase4_approval_gate(tmp_path: Path) -> None:
    evidence = generate_lab_evidence(
        _repository(tmp_path / "repository"),
        generated_at="2026-07-17T03:00:00Z",
    )
    evidence["phase_gate"] = {
        "active_phase": "Phase 0 + Phase 4 deterministic hardening: complete",
        "next_required_evidence": (
            "obtain explicit approval before Phase 4 provenance and discovery hardening"
        ),
        "status": "complete_awaiting_phase_approval",
    }
    artifact = tmp_path / "lab-evidence-v1.json"
    artifact.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        ["node", "dashboard/scripts/verify-lab-evidence.mjs", str(artifact)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "phase gate" in result.stderr


def test_browser_trial_count_validator_rejects_extra_keys() -> None:
    result = subprocess.run(
        ["node", "dashboard/scripts/verify-trial-counts-runtime.mjs"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_overview_renders_trial_state_from_contract_without_hardcoded_empty_claims() -> None:
    source = (
        Path(__file__).parents[1] / "dashboard" / "src" / "pages" / "overview-page.tsx"
    ).read_text(encoding="utf-8")

    assert "trial_ledger_status" in source
    assert "verified_real_trial_artifacts.by_mode" in source
    assert "accuracy.claim" in source
    assert 'history.completion_state === "partial"' in source
    assert "full-history reconciliation remain incomplete" in source
    assert "Full-history reconciliation is complete but unpromoted" in source
    assert "implemented and empty" not in source
    assert "zero verified real receipts" not in source
