from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pytest

from market_structure_lab.data.dashboard_evidence import generate_lab_evidence
from market_structure_lab.data.freshness import (
    CanonicalSeriesState,
    FreshnessManifest,
    FreshnessPlanningStatus,
    FreshnessSymbolPlan,
)
from market_structure_lab.data.freshness_sync import build_freshness_report, write_freshness_artifacts
from market_structure_lab.data.gaps import GapRange, ProvenanceState, SourceIdentity
from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    freeze_reconciliation_run,
    publish_work_unit,
    read_reconciliation_run,
    write_reconciliation_run,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phase4"


def _sha(character: str) -> str:
    return character * 64


def _freshness(root: Path) -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 120_000, 1)
    source = SourceIdentity(_sha("a"), 1, "market-data-candles-v1")
    before_symbol = FreshnessSymbolPlan(
        symbol="BTCUSDT", timeframe="1m", provenance_state=ProvenanceState.COMPATIBLE,
        status=FreshnessPlanningStatus.FETCH_REQUIRED, reason="one missing minute",
        canonical_state=CanonicalSeriesState("BTCUSDT", "1m", 0, 0, 1, 1),
        missing_ranges=(gap,),
    )
    after_symbol = FreshnessSymbolPlan(
        symbol="BTCUSDT", timeframe="1m", provenance_state=ProvenanceState.COMPATIBLE,
        status=FreshnessPlanningStatus.UP_TO_DATE, reason="recovered",
        canonical_state=CanonicalSeriesState("BTCUSDT", "1m", 0, 60_000, 2, 0),
        missing_ranges=(),
    )
    values = dict(
        manifest_version=1, dump_identity=source, compatibility_manifest_sha256=_sha("b"),
        as_of="2026-07-17T00:15:00Z", candidate_venue="binance", market_type="spot",
    )
    before = FreshnessManifest(canonical_row_count=1, symbols=(before_symbol,), **values)
    after = FreshnessManifest(canonical_row_count=2, symbols=(after_symbol,), **values)
    report = build_freshness_report(before, after, inserted_by_symbol={"BTCUSDT": 1})
    write_freshness_artifacts(before, report, root / "data" / "exports" / "freshness")


def _record(unit: ReconciliationWorkUnit) -> tuple[ReconciliationRecord, ...]:
    return tuple(
        ReconciliationRecord(
            unit.symbol, "1m", timestamp, ReconciliationClass.EXACT_MATCH,
            _sha("1"), _sha("1"), (),
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
        location="fixture.zip", payload_sha256=_sha("c"), published_sha256=_sha("c"),
        source_revision="fixture-v1", retrieved_at="2026-07-17T00:00:00Z",
    )
    for run_id, units, published in (
        ("RR-000002", (btc,), (btc,)),
        ("RR-000008", (btc, eth), (btc,)),
    ):
        run = freeze_reconciliation_run(
            run_id=run_id, cutoff=datetime(2026, 7, 17, tzinfo=UTC), dump_sha256=_sha("a"),
            source_row_count=3, mapping_version="mapping-v1", candidate_venue="binance",
            market_type="spot", source_revision="fixture-v1", algorithm_version="reconcile-v1",
            code_commit="abcdef0", uv_lock_sha256=_sha("d"),
            envelopes=tuple(TradingEnvelope(u.symbol, "1m", u.start_ms, u.end_ms) for u in units),
            work_units=units,
        )
        write_reconciliation_run(run, output / f"{run_id}.run.json")
        for unit in published:
            publish_work_unit(
                _record(unit), output_root=output, run=run, work_unit=unit,
                source_artifacts=(artifact,), max_rows_per_part=1,
            )


def _repository(tmp_path: Path) -> Path:
    _freshness(tmp_path)
    _reconciliation(tmp_path)
    destination = tmp_path / "tests" / "fixtures" / "phase4"
    destination.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES, destination)
    return tmp_path


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
    assert accuracy["verified_real_trial_artifacts"]["total"] == 0
    assert set(accuracy["verified_real_trial_artifacts"].values()) == {0}
    assert accuracy["trial_ledger_status"] == "not_implemented"
    assert accuracy["fixture_trials_counted_as_real"] is False
    replay = evidence["software_replay"]
    assert replay["runs"]["stable"]["run_id"] == "DR-000601"
    assert replay["runs"]["stable"]["transition_algorithm_version"] == (
        "boundary-aware-dwell-transitions-v2"
    )
    assert replay["runs"]["stable"]["behaviour_ids"] == [
        "B-CB3E7F47FE808F3C", "B-D6E06239FB9EC1EF"
    ]
    assert {item["neutral_name"] for item in replay["behaviours"]} == {
        "High Volume Auction State", "Low Volume Auction State"
    }
    registry = evidence["feature_registry"]
    assert registry["feature_set_id"] == "FS-000001"
    assert len(registry["definitions"]) == 26
    assert {"missing_policy", "version", "value_kind", "leakage_class"} <= set(
        registry["definitions"][0]
    )


def test_generator_rejects_missing_publication_and_fixture_tampering(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    success = next((root / "data" / "exports" / "reconciliation" / "run_id=RR-000002").rglob("_SUCCESS"))
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
    output = root / "data" / "exports" / "reconciliation"
    run = read_reconciliation_run(output / "RR-000008.run.json")
    unit = next(item for item in run.work_units if item.symbol == "ETHUSDT")
    artifact = SourceArtifactIdentity(
        location="fixture.zip", payload_sha256=_sha("c"), published_sha256=_sha("c"),
        source_revision="fixture-v1", retrieved_at="2026-07-17T00:00:00Z",
    )
    publish_work_unit(
        _record(unit), output_root=output, run=run, work_unit=unit,
        source_artifacts=(artifact,), max_rows_per_part=1,
    )

    evidence = generate_lab_evidence(root, generated_at="2026-07-17T03:00:00Z")
    history = evidence["reconciliation"]["full_history"]

    assert history["completion_state"] == "complete_unpromoted"
    assert history["scope_label"] == "full-history audit complete; unpromoted"
    assert history["promotion_receipt_available"] is False
    assert history["research_eligibility"] == "blocked_pending_deliberate_promotion_receipt"
    assert evidence["phase_gate"]["next_required_evidence"] == (
        "obtain explicit promotion approval and publish an immutable promotion receipt"
    )


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
    ],
    ids=["missing", "wrong"],
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
