from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from market_structure_lab.cli.reconcile_candles import main
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationReplacement,
    ReconciliationRepository,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    VerifiedCoverageInterval,
    build_reconciliation_promotion_preflight,
    freeze_reconciliation_run,
    publish_work_unit,
    verify_reconciliation_run_publication,
    write_reconciliation_run,
)


def _sha(character: str) -> str:
    return character * 64


def _replacement_checksum() -> str:
    return RecoveryCandle(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time_ms=60_000,
        open=Decimal("30"),
        high=Decimal("31"),
        low=Decimal("29"),
        close=Decimal("30"),
        volume=Decimal("3"),
        quote_volume=Decimal("90"),
        trades=3,
    ).row_checksum()


def _fixture(output_root: Path):
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 180_000)
    run = freeze_reconciliation_run(
        run_id="RR-000001",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=3,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v3",
        code_commit="aedd375",
        uv_lock_sha256=_sha("b"),
        envelopes=(TradingEnvelope("BTCUSDT", "1m", 0, 180_000),),
        work_units=(unit,),
    )
    records = (
        ReconciliationRecord(
            "BTCUSDT", "1m", 0, ReconciliationClass.EXACT_MATCH, _sha("1"), _sha("1"), ()
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            60_000,
            ReconciliationClass.BINANCE_CORRECTION,
            _sha("2"),
            _replacement_checksum(),
            ("close",),
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
    artifact = SourceArtifactIdentity(
        location="https://data.binance.vision/example.zip",
        payload_sha256=_sha("c"),
        published_sha256=_sha("c"),
        source_revision="binance-public-data-v1",
        retrieved_at="2026-07-16T00:00:00Z",
    )
    publish_work_unit(
        records,
        output_root=output_root,
        run=run,
        work_unit=unit,
        source_artifacts=(artifact,),
    )
    manifests = verify_reconciliation_run_publication(
        output_root,
        run,
        expected_manifest_sha256=run.manifest_sha256,
    )
    return run, manifests


def test_preflight_reports_reconstructible_evidence_and_exact_intended_effects(
    tmp_path: Path,
) -> None:
    run, manifests = _fixture(tmp_path)

    preflight = build_reconciliation_promotion_preflight(tmp_path, run, manifests)

    expected_replacement_hash = hashlib.sha256(
        json.dumps(
            {
                "binance_row_sha256": _replacement_checksum(),
                "open_time_ms": 60_000,
                "symbol": "BTCUSDT",
                "timeframe": "1m",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert preflight.payload["terminal_status_counts"] == {"source_unavailable": 1}
    assert preflight.payload["work_unit_inventory"] == [
        {
            "work_unit_id": run.work_units[0].work_unit_id,
            "manifest_sha256": manifests[0].manifest_sha256,
            "status": "source_unavailable",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "start": "1970-01-01T00:00:00Z",
            "end": "1970-01-01T00:03:00Z",
            "row_count": 3,
            "replacement_rows": 1,
        }
    ]
    assert preflight.payload["classification_counts"] == {
        "binance_correction": 1,
        "exact_match": 1,
        "source_unavailable": 1,
    }
    assert preflight.payload["candidate_replacement_logical_sha256"] == expected_replacement_hash
    assert preflight.payload["coverage"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "start": "1970-01-01T00:00:00Z",
            "end": "1970-01-01T00:02:00Z",
        }
    ]
    assert preflight.payload["residual_unavailable"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "start": "1970-01-01T00:02:00Z",
            "end": "1970-01-01T00:03:00Z",
        }
    ]
    assert preflight.payload["by_symbol_era"][0]["year"] == 1970
    assert preflight.payload["static_promotion_operations"] == {
        "candidate_coverage_rows": 1,
        "candidate_promotion_rows": 1,
        "candidate_replacement_rows": 1,
        "candidate_run_id": "RR-000001",
        "promotion_insert_semantics": "append_if_absent_without_reordering",
        "active_view_rule": "highest_existing_promotion_id",
        "tables_written": [
            "market_data.candle_reconciliation_coverage",
            "market_data.candle_reconciliation_promotions",
        ],
        "view_affected": "market_data.candles_reconciled",
        "immutable_dump_modified": False,
        "requires_database_preflight_for_exact_effects": True,
    }

    with pytest.raises(ValueError, match="interval output"):
        build_reconciliation_promotion_preflight(
            tmp_path,
            run,
            manifests,
            max_intervals=1,
        )


def test_preflight_rejects_part_changed_during_bounded_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, manifests = _fixture(tmp_path)
    original_read_parquet = pl.read_parquet
    part = tmp_path / manifests[0].publication_path / manifests[0].parts[0].path

    def read_then_mutate(path, *args, **kwargs):
        frame = original_read_parquet(path, *args, **kwargs)
        target = Path(path)
        target.write_bytes(target.read_bytes() + b"changed-during-preflight")
        return frame

    monkeypatch.setattr(
        "market_structure_lab.data.reconciliation.preflight.pl.read_parquet",
        read_then_mutate,
    )

    with pytest.raises(ValueError, match="changed during promotion preflight"):
        build_reconciliation_promotion_preflight(tmp_path, run, manifests)

    assert part.read_bytes().endswith(b"changed-during-preflight")


def test_report_cli_emits_the_full_read_only_preflight(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run, _ = _fixture(tmp_path)
    run_path = tmp_path / "RR-000001.run.json"
    write_reconciliation_run(run, run_path)

    assert (
        main(
            [
                "report",
                "--run",
                str(run_path),
                "--output-root",
                str(tmp_path),
                "--expected-manifest-sha256",
                run.manifest_sha256,
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["manifest_sha256"] == run.manifest_sha256
    assert payload["work_units"] == 1
    assert payload["terminal_status_counts"] == {"source_unavailable": 1}
    assert payload["residual_unavailable_intervals"] == 1
    assert payload["static_promotion_operations"]["candidate_promotion_rows"] == 1


def test_eligible_cli_emits_only_verified_coverage_with_the_pinned_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run, _ = _fixture(tmp_path)
    run_path = tmp_path / "RR-000001.run.json"
    write_reconciliation_run(run, run_path)

    assert (
        main(
            [
                "eligible",
                "--run",
                str(run_path),
                "--output-root",
                str(tmp_path),
                "--expected-manifest-sha256",
                run.manifest_sha256,
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_sha256"] == run.manifest_sha256
    assert payload["intervals"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "start": "1970-01-01T00:00:00Z",
            "end": "1970-01-01T00:02:00Z",
        }
    ]


def test_preflight_rejects_overlapping_coverage_from_overlapping_frozen_units(
    tmp_path: Path,
) -> None:
    boundary = int(datetime(2026, 2, 1, tzinfo=UTC).timestamp() * 1_000)
    units = (
        ReconciliationWorkUnit.create("BTCUSDT", "1m", boundary - 120_000, boundary + 120_000),
        ReconciliationWorkUnit.create("BTCUSDT", "1m", boundary, boundary + 240_000),
    )
    run = freeze_reconciliation_run(
        run_id="RR-000002",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=8,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v3",
        code_commit="aedd375",
        uv_lock_sha256=_sha("b"),
        envelopes=(TradingEnvelope("BTCUSDT", "1m", boundary - 120_000, boundary + 240_000),),
        work_units=units,
    )
    artifact = SourceArtifactIdentity(
        location="https://data.binance.vision/example.zip",
        payload_sha256=_sha("c"),
        published_sha256=_sha("c"),
        source_revision="binance-public-data-v1",
        retrieved_at="2026-07-16T00:00:00Z",
    )
    for unit in units:
        records = tuple(
            ReconciliationRecord(
                "BTCUSDT",
                "1m",
                timestamp,
                ReconciliationClass.EXACT_MATCH,
                _sha("1"),
                _sha("1"),
                (),
            )
            for timestamp in range(unit.start_ms, unit.end_ms, 60_000)
        )
        publish_work_unit(
            records,
            output_root=tmp_path,
            run=run,
            work_unit=unit,
            source_artifacts=(artifact,),
        )
    manifests = verify_reconciliation_run_publication(
        tmp_path,
        run,
        expected_manifest_sha256=run.manifest_sha256,
    )

    with pytest.raises(ValueError, match="overlap"):
        build_reconciliation_promotion_preflight(tmp_path, run, manifests)


class _Result:
    def __init__(self, *, scalar=None, rows=(), row=None) -> None:
        self.scalar = scalar
        self.rows = rows
        self.row = row

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar

    def all(self):
        raise AssertionError("global preflight queries must stream instead of calling all()")

    def one_or_none(self):
        return self.row

    def execution_options(self, **_options):
        return self

    def __iter__(self):
        return iter(self.rows)


class _PreflightConnection:
    def __init__(
        self,
        run,
        manifest,
        *,
        extra_work_unit: bool = False,
        extra_coverage: bool = False,
    ) -> None:
        self.run = run
        self.manifest = manifest
        self.extra_work_unit = extra_work_unit
        self.extra_coverage = extra_coverage
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM market_data.candle_reconciliation_runs" in sql:
            return _Result(scalar=self.run.manifest_sha256)
        if "SELECT work_unit_id, manifest_sha256, status" in sql:
            rows = [
                SimpleNamespace(
                    work_unit_id=self.manifest.work_unit_id,
                    manifest_sha256=self.manifest.manifest_sha256,
                    status=self.manifest.status,
                )
            ]
            if self.extra_work_unit:
                rows.append(
                    SimpleNamespace(
                        work_unit_id="unexpected-work-unit",
                        manifest_sha256=_sha("f"),
                        status="complete",
                    )
                )
            return _Result(rows=tuple(rows))
        if (
            "SELECT symbol" in sql
            and "candle_reconciliation_replacements" in sql
            and "work_unit_id" not in sql
        ):
            return _Result(
                rows=(
                    SimpleNamespace(
                        symbol="BTCUSDT",
                        interval="1m",
                        open_time=60_000,
                        open=Decimal("30"),
                        high=Decimal("31"),
                        low=Decimal("29"),
                        close=Decimal("30"),
                        volume=Decimal("3"),
                        quote_volume=Decimal("90"),
                        trades=3,
                        binance_row_sha256=_replacement_checksum(),
                    ),
                )
            )
        if "SELECT count(*)" in sql and "candle_reconciliation_replacements" in sql:
            return _Result(scalar=1)
        if "FROM market_data.candle_reconciliation_coverage" in sql:
            rows = ()
            if self.extra_coverage:
                rows = (
                    SimpleNamespace(symbol="BTCUSDT", interval="1m", start_time=0, end_time=60_000),
                    SimpleNamespace(
                        symbol="BTCUSDT", interval="1m", start_time=60_000, end_time=120_000
                    ),
                )
            return _Result(rows=rows)
        if "WHERE run_id=:run_id" in sql and "candle_reconciliation_promotions" in sql:
            return _Result(row=None)
        if "ORDER BY promotion_id DESC" in sql:
            return _Result(row=None)
        raise AssertionError(f"unexpected SQL: {sql}")


class _Engine:
    def __init__(self, connection: _PreflightConnection) -> None:
        self.connection = connection
        self.disposed = False

    def begin(self):
        return nullcontext(self.connection)

    def dispose(self) -> None:
        self.disposed = True


class _TamperedPublicationConnection:
    def __init__(self, manifest) -> None:
        self.manifest = manifest
        self.replacements_inserted = False

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if (
            "pg_advisory_xact_lock" in sql
            or "INSERT INTO" in sql
            and ("candle_reconciliation_work_units" in sql)
        ):
            return _Result()
        if "SELECT manifest_sha256" in sql and "work_units" in sql:
            return _Result(scalar=self.manifest.manifest_sha256)
        if "SELECT count(*)" in sql and "work_unit_id" in sql:
            return _Result(scalar=1 if self.replacements_inserted else 0)
        if "INSERT INTO market_data.candle_reconciliation_replacements" in sql:
            self.replacements_inserted = True
            return _Result()
        if "SELECT symbol" in sql and "work_unit_id" in sql:
            return _Result(
                rows=(
                    SimpleNamespace(
                        symbol="BTCUSDT",
                        interval="1m",
                        open_time=60_000,
                        classification="binance_correction",
                        open=Decimal("30.000000000000000000"),
                        high=Decimal("31.000000000000000000"),
                        low=Decimal("29.000000000000000000"),
                        close=Decimal("999.000000000000000000"),
                        volume=Decimal("3.000000000000000000"),
                        quote_volume=Decimal("90.000000000000000000"),
                        trades=3,
                        source_name="binance_spot",
                        source_revision="fixture-v1",
                        payload_sha256=_sha("c"),
                        binance_row_sha256=_replacement_checksum(),
                        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
                    ),
                )
            )
        raise AssertionError(f"unexpected SQL: {sql}")


def test_promotion_dry_run_inspects_database_state_without_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, manifests = _fixture(tmp_path)
    run_path = tmp_path / "RR-000001.run.json"
    write_reconciliation_run(run, run_path)
    connection = _PreflightConnection(run, manifests[0])
    engine = _Engine(connection)
    monkeypatch.setattr(
        "market_structure_lab.cli.reconcile_candles.load_settings",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://redacted")),
    )
    monkeypatch.setattr(
        "market_structure_lab.cli.reconcile_candles.create_engine",
        lambda _url: engine,
    )

    assert (
        main(
            [
                "promote",
                "--run",
                str(run_path),
                "--output-root",
                str(tmp_path),
                "--expected-manifest-sha256",
                run.manifest_sha256,
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    database = payload["database_preflight"]
    assert database["verified_replacement_rows"] == 1
    assert database["coverage_rows_existing"] == 0
    assert database["coverage_rows_to_insert"] == 1
    assert database["promotion_rows_to_insert"] == 1
    assert database["active_run_before"] is None
    assert database["active_run_after"] == run.run_id
    assert database["reconciled_view_will_switch"] is True
    assert not any("INSERT INTO" in statement for statement in connection.statements)
    assert engine.disposed is True


def test_promotion_apply_rejects_database_replacements_that_differ_from_candidate_before_writes(
    tmp_path: Path,
) -> None:
    run, manifests = _fixture(tmp_path)
    connection = _PreflightConnection(run, manifests[0])
    repository = ReconciliationRepository(connection)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="database replacements differ"):
        repository.promote(
            run,
            manifests,
            (VerifiedCoverageInterval("BTCUSDT", "1m", 0, 120_000),),
            candidate_replacement_logical_sha256=_sha("f"),
        )

    assert not any(
        "INSERT INTO market_data.candle_reconciliation_coverage" in statement
        or "INSERT INTO market_data.candle_reconciliation_promotions" in statement
        for statement in connection.statements
    )


def test_replacement_rejects_declared_row_checksum_that_does_not_match_exact_fields() -> None:
    with pytest.raises(ValueError, match="declared Binance row checksum"):
        ReconciliationReplacement(
            run_id="RR-000001",
            work_unit_id="0" * 24,
            symbol="BTCUSDT",
            timeframe="1m",
            open_time_ms=60_000,
            classification=ReconciliationClass.BINANCE_CORRECTION,
            open=Decimal("30"),
            high=Decimal("31"),
            low=Decimal("29"),
            close=Decimal("30"),
            volume=Decimal("3"),
            quote_volume=Decimal("90"),
            trades=3,
            source_name="binance_spot",
            source_revision="fixture-v1",
            payload_sha256=_sha("c"),
            binance_row_sha256=_sha("3"),
            retrieved_at="2026-07-16T00:00:00Z",
        )


def test_work_unit_publication_rejects_stored_ohlcv_that_differs_from_verified_input(
    tmp_path: Path,
) -> None:
    run, manifests = _fixture(tmp_path)
    replacement = ReconciliationReplacement(
        run_id=run.run_id,
        work_unit_id=manifests[0].work_unit_id,
        symbol="BTCUSDT",
        timeframe="1m",
        open_time_ms=60_000,
        classification=ReconciliationClass.BINANCE_CORRECTION,
        open=Decimal("30"),
        high=Decimal("31"),
        low=Decimal("29"),
        close=Decimal("30"),
        volume=Decimal("3"),
        quote_volume=Decimal("90"),
        trades=3,
        source_name="binance_spot",
        source_revision="fixture-v1",
        payload_sha256=_sha("c"),
        binance_row_sha256=_replacement_checksum(),
        retrieved_at="2026-07-16T00:00:00Z",
    )
    connection = _TamperedPublicationConnection(manifests[0])

    with pytest.raises(ValueError, match="database replacements differ"):
        ReconciliationRepository(connection).publish_work_unit(  # type: ignore[arg-type]
            manifests[0],
            (replacement,),
        )


def test_database_preflight_rejects_work_unit_rows_beyond_frozen_manifest_bound(
    tmp_path: Path,
) -> None:
    run, manifests = _fixture(tmp_path)
    preflight = build_reconciliation_promotion_preflight(tmp_path, run, manifests)
    connection = _PreflightConnection(run, manifests[0], extra_work_unit=True)

    with pytest.raises(ValueError, match="work-unit rows exceed the frozen manifest"):
        ReconciliationRepository(connection).inspect_promotion(  # type: ignore[arg-type]
            run,
            manifests,
            preflight.coverage,
            candidate_replacement_logical_sha256=str(
                preflight.payload["candidate_replacement_logical_sha256"]
            ),
        )


def test_database_preflight_rejects_coverage_rows_beyond_candidate_bound(
    tmp_path: Path,
) -> None:
    run, manifests = _fixture(tmp_path)
    preflight = build_reconciliation_promotion_preflight(tmp_path, run, manifests)
    connection = _PreflightConnection(run, manifests[0], extra_coverage=True)

    with pytest.raises(ValueError, match="coverage rows exceed the verified candidate"):
        ReconciliationRepository(connection).inspect_promotion(  # type: ignore[arg-type]
            run,
            manifests,
            preflight.coverage,
            candidate_replacement_logical_sha256=str(
                preflight.payload["candidate_replacement_logical_sha256"]
            ),
        )
