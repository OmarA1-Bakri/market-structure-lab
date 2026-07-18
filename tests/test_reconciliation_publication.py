from __future__ import annotations

import json
import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    VerifiedCoverageInterval,
    freeze_reconciliation_run,
    publish_work_unit,
    read_work_unit_manifest,
    validate_reconciliation_coverage,
    verify_reconciliation_run_publication,
    verify_work_unit_publication,
)
from market_structure_lab.data.reconciliation import repository as reconciliation_repository
from market_structure_lab.data.reconciliation.repository import ReconciliationRepository


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


def _rewrite_manifest(publication: Path, raw: dict[str, object]) -> None:
    logical = {key: value for key, value in raw.items() if key != "manifest_sha256"}
    raw["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            logical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    (publication / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    (publication / "_SUCCESS").write_text(str(raw["manifest_sha256"]) + "\n", encoding="utf-8")


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _RegisterConnection:
    def __init__(self, manifest_sha256: str) -> None:
        self.manifest_sha256 = manifest_sha256
        self.statements: list[str] = []

    def execute(self, statement: object, parameters: object = None) -> _ScalarResult:
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT manifest_sha256" in sql:
            return _ScalarResult(self.manifest_sha256)
        return _ScalarResult(None)


class _CanonicalHashConnection:
    def __init__(self, rows_by_query: dict[tuple[str, str, str], list[object]]) -> None:
        self.rows_by_query = rows_by_query
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: object = None) -> list[object]:
        sql = str(statement)
        bound = dict(parameters or {})
        self.calls.append((sql, bound))
        if "set_config" in sql:
            return []
        branch = "dump" if "FROM market_data.candles AS candle" in sql else "replacement"
        return self.rows_by_query[(str(bound["symbol"]), str(bound["timeframe"]), branch)]


def _canonical_row(symbol: str, open_time: int, origin: str) -> object:
    mapping = {
        "symbol": symbol,
        "interval": "1m",
        "open_time": open_time,
        "open": "1",
        "high": "2",
        "low": "0.5",
        "close": "1.5",
        "volume": "10",
        "quote_volume": "15",
        "trades": "2",
        "origin": origin,
    }
    return SimpleNamespace(_mapping=mapping, open_time=open_time)


def test_canonical_hash_streams_each_frozen_market_without_a_global_sort() -> None:
    btc = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 180_000)
    eth = ReconciliationWorkUnit.create("ETHUSDT", "1m", 0, 180_000)
    run = freeze_reconciliation_run(
        run_id="RR-000001",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=6,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v1",
        code_commit="aedd375",
        uv_lock_sha256=_sha("b"),
        envelopes=(
            TradingEnvelope("BTCUSDT", "1m", 0, 180_000),
            TradingEnvelope("ETHUSDT", "1m", 0, 180_000),
        ),
        work_units=(btc, eth),
    )
    rows_by_query = {
        ("BTCUSDT", "1m", "replacement"): [_canonical_row("BTCUSDT", 60_000, "binance_correction")],
        ("BTCUSDT", "1m", "dump"): [
            _canonical_row("BTCUSDT", 0, "dump_verified_match"),
            _canonical_row("BTCUSDT", 120_000, "dump_verified_match"),
        ],
        ("ETHUSDT", "1m", "replacement"): [_canonical_row("ETHUSDT", 120_000, "binance_fill")],
        ("ETHUSDT", "1m", "dump"): [
            _canonical_row("ETHUSDT", 0, "dump_verified_match"),
            _canonical_row("ETHUSDT", 60_000, "dump_verified_match"),
        ],
    }
    connection = _CanonicalHashConnection(rows_by_query)
    expected = hashlib.sha256()
    for symbol, open_time, origin in (
        ("BTCUSDT", 0, "dump_verified_match"),
        ("BTCUSDT", 60_000, "binance_correction"),
        ("BTCUSDT", 120_000, "dump_verified_match"),
        ("ETHUSDT", 0, "dump_verified_match"),
        ("ETHUSDT", 60_000, "dump_verified_match"),
        ("ETHUSDT", 120_000, "binance_fill"),
    ):
        expected.update(
            json.dumps(
                _canonical_row(symbol, open_time, origin)._mapping,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )

    actual = reconciliation_repository._run_canonical_hash(  # noqa: SLF001
        connection,  # type: ignore[arg-type]
        run,
    )

    assert actual == expected.hexdigest()
    optimizer_sql, optimizer_parameters = connection.calls[0]
    assert optimizer_parameters == {}
    assert "set_config('enable_seqscan', 'off', true)" in optimizer_sql
    assert "set_config('enable_bitmapscan', 'off', true)" in optimizer_sql
    assert "set_config('max_parallel_workers_per_gather', '0', true)" in optimizer_sql
    canonical_calls = connection.calls[1:]
    assert len(canonical_calls) == 4
    assert [call[1] for call in canonical_calls] == [
        {"run_id": run.run_id, "symbol": "BTCUSDT", "timeframe": "1m"},
        {"run_id": run.run_id, "symbol": "BTCUSDT", "timeframe": "1m"},
        {"run_id": run.run_id, "symbol": "ETHUSDT", "timeframe": "1m"},
        {"run_id": run.run_id, "symbol": "ETHUSDT", "timeframe": "1m"},
    ]
    assert all(":symbol" in sql and ":timeframe" in sql for sql, _ in canonical_calls)
    assert all('ORDER BY symbol, "interval", open_time' not in sql for sql, _ in canonical_calls)


def test_repository_waits_for_the_global_publication_lock() -> None:
    run, _ = _run()
    connection = _RegisterConnection(run.manifest_sha256)

    ReconciliationRepository(connection).register_run(run)  # type: ignore[arg-type]

    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert "pg_try_advisory_xact_lock" not in connection.statements[0]


def test_coverage_validation_rejects_overlap_before_promotion() -> None:
    run, _ = _run()
    with pytest.raises(ValueError, match="overlap"):
        validate_reconciliation_coverage(
            run,
            (
                VerifiedCoverageInterval("BTCUSDT", "1m", 0, 120_000),
                VerifiedCoverageInterval("BTCUSDT", "1m", 60_000, 180_000),
            ),
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


def test_publish_rejects_part_size_above_phase_zero_safe_maximum(tmp_path: Path) -> None:
    run, unit = _run()

    with pytest.raises(ValueError, match="safe maximum"):
        publish_work_unit(
            _records(),
            output_root=tmp_path,
            run=run,
            work_unit=unit,
            source_artifacts=(_artifact(),),
            max_rows_per_part=100_001,
        )


def test_manifest_rejects_self_consistent_oversized_part_declaration(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    oversized = 100_001
    raw["row_count"] = oversized
    raw["classification_counts"] = {"exact_match": oversized}
    raw["differing_field_counts"] = {}
    raw["replacement_row_count"] = 0
    raw["replacement_logical_sha256"] = hashlib.sha256().hexdigest()
    raw["max_rows_per_part"] = oversized
    raw["max_buffered_rows"] = oversized
    raw_parts = raw["parts"]
    assert isinstance(raw_parts, list)
    assert isinstance(raw_parts[0], dict)
    raw_parts[0]["row_count"] = oversized
    _rewrite_manifest(publication, raw)

    with pytest.raises(ValueError, match="safe maximum"):
        read_work_unit_manifest(publication / "manifest.json")
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


def test_public_verifier_checks_the_full_work_unit_publication(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path

    assert verify_work_unit_publication(publication) == manifest

    (publication / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="success marker"):
        verify_work_unit_publication(publication)


def test_run_verifier_requires_exact_unique_publication_paths(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path

    assert verify_reconciliation_run_publication(
        tmp_path, run, expected_manifest_sha256=run.manifest_sha256
    ) == (manifest,)
    with pytest.raises(ValueError, match="expected SHA-256"):
        verify_reconciliation_run_publication(
            tmp_path,
            run,
            expected_manifest_sha256="0" * 64,
        )

    duplicate = publication.parent / "duplicate"
    shutil.copytree(publication, duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )
    shutil.rmtree(duplicate)

    moved = publication.parent / "moved"
    publication.rename(moved)
    with pytest.raises(ValueError, match="publication path"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rejects_missing_and_extra_frozen_units(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    removed = tmp_path / "removed-publication"
    publication.rename(removed)
    with pytest.raises(ValueError, match="missing="):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )
    removed.rename(publication)

    extra_unit = ReconciliationWorkUnit.create("ETHUSDT", "1m", 0, 180_000)
    extra_run = freeze_reconciliation_run(
        run_id=run.run_id,
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
        envelopes=(TradingEnvelope("ETHUSDT", "1m", 0, 180_000),),
        work_units=(extra_unit,),
    )
    extra_records = tuple(
        ReconciliationRecord(
            "ETHUSDT",
            row.timeframe,
            row.open_time_ms,
            row.classification,
            row.dump_row_sha256,
            row.binance_row_sha256,
            row.differing_fields,
        )
        for row in _records()
    )
    publish_work_unit(
        extra_records,
        output_root=tmp_path,
        run=extra_run,
        work_unit=extra_unit,
        source_artifacts=(_artifact(),),
    )
    with pytest.raises(ValueError, match="extra="):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rejects_stages_missing_markers_and_failed_units(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    stage = publication.parent / f".{unit.work_unit_id}.stage"
    stage.mkdir()
    with pytest.raises(ValueError, match="stale staging"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )
    stage.rmdir()

    success = publication / "_SUCCESS"
    success.unlink()
    with pytest.raises(ValueError, match="success marker"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )

    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw["status"] = "failed"
    _rewrite_manifest(publication, raw)
    with pytest.raises(ValueError, match="failed"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rederives_terminal_status_from_the_ledger(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw["status"] = "completed"
    _rewrite_manifest(publication, raw)

    with pytest.raises(ValueError, match="terminal status"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rechecks_part_hashes_and_physical_row_counts(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    part = publication / manifest.parts[0].path
    original = part.read_bytes()
    part.write_bytes(original + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )

    part.write_bytes(original)
    frame = pl.read_parquet(part)
    pl.concat((frame, frame.head(1))).write_parquet(part)
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw_parts = raw["parts"]
    assert isinstance(raw_parts, list)
    assert isinstance(raw_parts[0], dict)
    raw_parts[0]["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
    _rewrite_manifest(publication, raw)
    with pytest.raises(ValueError, match="row count"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rejects_self_consistent_shortened_work_unit(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    part = publication / manifest.parts[0].path
    frame = pl.read_parquet(part).head(2)
    frame.write_parquet(part)
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw["row_count"] = 2
    raw["classification_counts"] = {"binance_correction": 1, "exact_match": 1}
    raw["status"] = "completed"
    raw_parts = raw["parts"]
    assert isinstance(raw_parts, list)
    assert isinstance(raw_parts[0], dict)
    raw_parts[0]["row_count"] = 2
    raw_parts[0]["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
    _rewrite_manifest(publication, raw)

    with pytest.raises(ValueError, match="frozen minute count"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rechecks_ledger_identity_after_manifest_rehash(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    part = publication / manifest.parts[0].path
    frame = pl.read_parquet(part).with_columns(pl.lit("ETHUSDT").alias("symbol"))
    frame.write_parquet(part)
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw_parts = raw["parts"]
    assert isinstance(raw_parts, list)
    assert isinstance(raw_parts[0], dict)
    raw_parts[0]["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
    _rewrite_manifest(publication, raw)

    with pytest.raises(ValueError, match="frozen identity"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_rechecks_classification_hash_semantics(tmp_path: Path) -> None:
    run, unit = _run()
    manifest = publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    publication = tmp_path / manifest.publication_path
    part = publication / manifest.parts[0].path
    frame = pl.read_parquet(part).with_columns(
        pl.when(pl.col("open_time_ms") == 0)
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(pl.col("binance_row_sha256"))
        .alias("binance_row_sha256")
    )
    frame.write_parquet(part)
    raw = json.loads((publication / "manifest.json").read_text(encoding="utf-8"))
    raw_parts = raw["parts"]
    assert isinstance(raw_parts, list)
    assert isinstance(raw_parts[0], dict)
    raw_parts[0]["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
    _rewrite_manifest(publication, raw)

    with pytest.raises(ValueError, match="classification"):
        verify_reconciliation_run_publication(
            tmp_path, run, expected_manifest_sha256=run.manifest_sha256
        )


def test_run_verifier_accepts_exact_match_with_ignored_optional_dump_fields(
    tmp_path: Path,
) -> None:
    run, unit = _run()
    records = list(_records())
    records[0] = ReconciliationRecord(
        "BTCUSDT",
        "1m",
        0,
        ReconciliationClass.EXACT_MATCH,
        _sha("1"),
        _sha("9"),
        (),
    )
    manifest = publish_work_unit(
        records,
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )

    verified = verify_reconciliation_run_publication(
        tmp_path,
        run,
        expected_manifest_sha256=run.manifest_sha256,
    )

    assert verified == (manifest,)


def test_run_verifier_rejects_a_self_consistent_changed_run_against_the_pin(
    tmp_path: Path,
) -> None:
    run, unit = _run()
    publish_work_unit(
        _records(),
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(_artifact(),),
    )
    changed = freeze_reconciliation_run(
        run_id=run.run_id,
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=3,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v2",
        code_commit="deadbeef",
        uv_lock_sha256=_sha("b"),
        envelopes=run.envelopes,
        work_units=run.work_units,
    )

    with pytest.raises(ValueError, match="expected SHA-256"):
        verify_reconciliation_run_publication(
            tmp_path,
            changed,
            expected_manifest_sha256=run.manifest_sha256,
        )
