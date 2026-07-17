from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationWorkUnit,
    TradingEnvelope,
    execute_work_unit,
    freeze_reconciliation_run,
    iter_dump_rows,
)
from market_structure_lab.data.sources.base import (
    FetchBatch,
    FetchRequest,
    SourceIntegrityError,
    SourceKline,
    SourceProvenance,
)


def _sha(character: str) -> str:
    return character * 64


def _source(timestamp: int, **overrides: object) -> SourceKline:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "open_time_ms": timestamp,
        "open": Decimal("10"),
        "high": Decimal("11"),
        "low": Decimal("9"),
        "close": Decimal("10"),
        "volume": Decimal("1"),
        "quote_volume": Decimal("10"),
        "trades": 1,
    }
    values.update(overrides)
    return SourceKline(**values)  # type: ignore[arg-type]


class FakeSource:
    name = "binance_spot"

    def __init__(
        self,
        rows: tuple[SourceKline, ...],
        *,
        excluded_row_count: int = 0,
        integrity_notes: tuple[str, ...] = (),
    ) -> None:
        self.rows = rows
        self.requests: list[FetchRequest] = []
        self.excluded_row_count = excluded_row_count
        self.integrity_notes = integrity_notes

    def fetch(self, request: FetchRequest) -> Iterator[FetchBatch]:
        self.requests.append(request)
        yield FetchBatch(
            request=request,
            rows=self.rows,
            provenance=SourceProvenance(
                source_name=self.name,
                source_revision="fixture-v1",
                location="https://data.binance.vision/fixture.zip",
                payload_checksum=_sha("c"),
                published_checksum=_sha("c"),
                retrieved_at="2026-07-16T00:00:00Z",
                excluded_row_count=self.excluded_row_count,
                integrity_notes=self.integrity_notes,
            ),
            authoritative_empty=not self.rows,
        )


class BrokenSource:
    name = "binance_spot"

    def fetch(self, request: FetchRequest) -> Iterator[FetchBatch]:
        del request
        raise SourceIntegrityError("published checksum mismatch")
        yield


@pytest.fixture
def dump_connection():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS market_data"))
        connection.execute(
            text(
                "CREATE TABLE market_data.candles ("
                'symbol text, "interval" text, open_time integer, '
                "open text, high text, low text, close text, volume text, "
                "quote_volume text, trades integer)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO market_data.candles VALUES "
                "('BTCUSDT', '1m', 0, '10', '11', '9', '10', '1', '10', 1), "
                "('BTCUSDT', '1m', 60000, '20', '21', '19', '20', '2', '40', 2), "
                "('BTCUSDT', '1m', 180000, '40', '41', '39', '40', '4', '160', 4)"
            )
        )
        yield connection
    engine.dispose()


def _run():
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 240_000)
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
            code_commit="d92531e",
            uv_lock_sha256=_sha("b"),
            envelopes=(TradingEnvelope("BTCUSDT", "1m", 0, 240_000),),
            work_units=(unit,),
        ),
        unit,
    )


def test_dump_rows_stream_in_canonical_order(dump_connection) -> None:
    _, unit = _run()

    rows = tuple(iter_dump_rows(dump_connection, unit, batch_size=1))

    assert [row.open_time_ms for row in rows] == [0, 60_000, 180_000]


def test_execute_work_unit_publishes_provenance_replacements_and_split_coverage(
    dump_connection,
    tmp_path: Path,
) -> None:
    run, unit = _run()
    source = FakeSource(
        (
            _source(0),
            _source(60_000, open=Decimal("30"), high=Decimal("31"), low=Decimal("29"),
                    close=Decimal("30"), volume=Decimal("3"), quote_volume=Decimal("90"),
                    trades=3),
            _source(120_000, open=Decimal("35"), high=Decimal("36"), low=Decimal("34"),
                    close=Decimal("35"), volume=Decimal("3.5"),
                    quote_volume=Decimal("122.5"), trades=4),
        )
    )

    result = execute_work_unit(
        dump_connection,
        source,
        run=run,
        work_unit=unit,
        output_root=tmp_path,
        batch_size=1,
        max_rows_per_part=2,
    )

    assert result.manifest.status == "source_unavailable"
    assert result.manifest.source_artifacts[0].published_sha256 == _sha("c")
    assert [item.classification for item in result.replacements] == [
        ReconciliationClass.BINANCE_CORRECTION,
        ReconciliationClass.BINANCE_FILL,
    ]
    assert [(item.start_ms, item.end_ms) for item in result.verified_coverage] == [
        (0, 180_000)
    ]
    ledger = tmp_path / result.manifest.publication_path / "part-00000.parquet"
    assert pl.read_parquet(ledger)["classification"].to_list() == [
        "exact_match",
        "binance_correction",
    ]
    assert source.requests == [FetchRequest("BTCUSDT", "1m", 0, 240_000)]

    replay = execute_work_unit(
        dump_connection,
        source,
        run=run,
        work_unit=unit,
        output_root=tmp_path,
        batch_size=2,
        max_rows_per_part=2,
    )
    assert replay.manifest == result.manifest


def test_source_integrity_failure_publishes_no_success_marker(
    dump_connection,
    tmp_path: Path,
) -> None:
    run, unit = _run()

    with pytest.raises(SourceIntegrityError, match="checksum"):
        execute_work_unit(
            dump_connection,
            BrokenSource(),
            run=run,
            work_unit=unit,
            output_root=tmp_path,
        )

    assert not tuple(tmp_path.rglob("_SUCCESS"))


def test_execute_work_unit_persists_excluded_source_row_evidence(
    dump_connection,
    tmp_path: Path,
) -> None:
    run, unit = _run()
    note = "archive_off_minute_grid:first=60001:last=60001"
    result = execute_work_unit(
        dump_connection,
        FakeSource((_source(0),), excluded_row_count=1, integrity_notes=(note,)),
        run=run,
        work_unit=unit,
        output_root=tmp_path,
    )

    artifact = result.manifest.source_artifacts[0]
    assert artifact.excluded_row_count == 1
    assert artifact.integrity_notes == (note,)
