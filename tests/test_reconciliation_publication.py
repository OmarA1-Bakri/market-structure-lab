from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    freeze_reconciliation_run,
    publish_work_unit,
    read_work_unit_manifest,
)


def _sha(character: str) -> str:
    return character * 64


def _run():
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 180_000)
    return (
        freeze_reconciliation_run(
            run_id="RR-000001",
            cutoff=datetime(2026, 7, 16, tzinfo=UTC),
            dump_sha256=_sha("a"),
            source_row_count=3,
            mapping_version="mapping-v1",
            candidate_venue="binance",
            market_type="spot",
            source_revision="binance-public-data-v1",
            algorithm_version="row-reconciliation-v1",
            code_commit="aedd375",
            uv_lock_sha256=_sha("b"),
            envelopes=(TradingEnvelope("BTCUSDT", "1m", 0, 180_000),),
            work_units=(unit,),
        ),
        unit,
    )


def _records() -> tuple[ReconciliationRecord, ...]:
    return (
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            0,
            ReconciliationClass.EXACT_MATCH,
            _sha("1"),
            _sha("1"),
            (),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            60_000,
            ReconciliationClass.BINANCE_CORRECTION,
            _sha("2"),
            _sha("3"),
            ("close", "volume"),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            120_000,
            ReconciliationClass.SOURCE_UNAVAILABLE,
            _sha("4"),
            None,
            (),
        ),
    )


def _artifact() -> SourceArtifactIdentity:
    return SourceArtifactIdentity(
        location="https://data.binance.vision/example.zip",
        payload_sha256=_sha("c"),
        published_sha256=_sha("c"),
        source_revision="binance-public-data-v1",
        retrieved_at="2026-07-16T00:00:00Z",
    )


def test_publish_work_unit_is_atomic_bounded_and_idempotent(tmp_path: Path) -> None:
    run, unit = _run()

    first = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
        max_rows_per_part=2,
    )
    second = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
        max_rows_per_part=2,
    )

    assert first == second
    assert first.row_count == 3
    assert first.classification_counts == (
        ("binance_correction", 1),
        ("exact_match", 1),
        ("source_unavailable", 1),
    )
    assert first.differing_field_counts == (("close", 1), ("volume", 1))
    assert first.replacement_row_count == 1
    assert first.max_buffered_rows == 2
    assert first.status == "source_unavailable"
    publication = tmp_path / first.publication_path
    assert (publication / "_SUCCESS").read_text(encoding="utf-8").strip() == first.manifest_sha256
    assert read_work_unit_manifest(publication / "manifest.json") == first
    parts = sorted(publication.glob("part-*.parquet"))
    assert len(parts) == 2
    assert sum(pl.read_parquet(path).height for path in parts) == 3
    assert not tuple(publication.parent.glob(".*.stage"))


def test_publication_rejects_order_identity_and_existing_content_conflicts(
    tmp_path: Path,
) -> None:
    run, unit = _run()
    records = _records()
    publish_work_unit(
        records,
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )

    with pytest.raises(ValueError, match="ordered"):
        publish_work_unit(
            (records[1], records[0], records[2]),
            output_root=tmp_path / "other",
            run=run,
            work_unit=unit,
            source_artifacts=(_artifact(),),
        )
    changed = (
        records[0],
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            60_000,
            ReconciliationClass.BINANCE_CORRECTION,
            _sha("2"),
            _sha("9"),
            ("close",),
        ),
        records[2],
    )
    with pytest.raises(ValueError, match="different content"):
        publish_work_unit(
            changed,
            output_root=tmp_path,
            run=run,
            work_unit=unit,
            source_artifacts=(_artifact(),),
        )


def test_read_manifest_rejects_tampering_and_unmanifested_parts(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    manifest_path = publication / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["row_count"] = 99
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        read_work_unit_manifest(manifest_path)

    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    pl.DataFrame({"x": [1]}).write_parquet(publication / "part-99999.parquet")
    with pytest.raises(ValueError, match="unmanifested"):
        publish_work_unit(
            _records(),
            output_root=tmp_path,
            run=run,
            work_unit=unit,
            source_artifacts=(_artifact(),),
        )
