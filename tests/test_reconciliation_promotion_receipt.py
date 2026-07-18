from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationPromotion,
    ReconciliationWorkUnit,
    TradingEnvelope,
    VerifiedCoverageInterval,
    freeze_reconciliation_run,
    read_reconciliation_promotion_receipt,
    write_reconciliation_promotion_receipt,
)


def _sha(character: str) -> str:
    return character * 64


def _run():
    start = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1_000)
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", start, start + 120_000)
    return freeze_reconciliation_run(
        run_id="RR-000008",
        cutoff=datetime(2026, 7, 17, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=2,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="fixture-v1",
        algorithm_version="reconcile-v1",
        code_commit="abcdef0",
        uv_lock_sha256=_sha("b"),
        envelopes=(TradingEnvelope("BTCUSDT", "1m", unit.start_ms, unit.end_ms),),
        work_units=(unit,),
    )


def _promotion() -> ReconciliationPromotion:
    return ReconciliationPromotion(
        run_id="RR-000008",
        manifest_sha256=_run().manifest_sha256,
        replacement_logical_sha256=_sha("c"),
        canonical_logical_sha256=_sha("d"),
        promoted_at="2026-07-18T13:45:00Z",
    )


def test_promotion_receipt_is_immutable_idempotent_and_coverage_bound(tmp_path: Path) -> None:
    run = _run()
    coverage = (
        VerifiedCoverageInterval(
            "BTCUSDT", "1m", run.work_units[0].start_ms, run.work_units[0].end_ms
        ),
    )

    first = write_reconciliation_promotion_receipt(tmp_path, run, _promotion(), coverage)
    replay = write_reconciliation_promotion_receipt(tmp_path, run, _promotion(), coverage)
    restored = read_reconciliation_promotion_receipt(first.path)

    assert first == replay == restored
    assert restored.run_id == run.run_id
    assert restored.manifest_sha256 == run.manifest_sha256
    assert restored.coverage_interval_count == 1
    assert len(restored.coverage_logical_sha256) == 64
    assert restored.path == (tmp_path / "promotions" / "run_id=RR-000008" / "receipt.json")


def test_promotion_receipt_rejects_conflicting_or_tampered_content(tmp_path: Path) -> None:
    run = _run()
    coverage = (
        VerifiedCoverageInterval(
            "BTCUSDT", "1m", run.work_units[0].start_ms, run.work_units[0].end_ms
        ),
    )
    receipt = write_reconciliation_promotion_receipt(tmp_path, run, _promotion(), coverage)

    conflicting = ReconciliationPromotion(
        run_id="RR-000008",
        manifest_sha256=run.manifest_sha256,
        replacement_logical_sha256=_sha("e"),
        canonical_logical_sha256=_sha("d"),
        promoted_at="2026-07-18T13:45:00Z",
    )
    with pytest.raises(FileExistsError, match="different content"):
        write_reconciliation_promotion_receipt(tmp_path, run, conflicting, coverage)

    payload = json.loads(receipt.path.read_text(encoding="utf-8"))
    payload["canonical_logical_sha256"] = _sha("f")
    receipt.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum|content"):
        read_reconciliation_promotion_receipt(receipt.path)
