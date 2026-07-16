from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationWorkUnit,
    monthly_work_units,
    reconcile_ordered_rows,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.sources.base import SourceKline


def _dump(timestamp: int, **overrides: object) -> RecoveryCandle:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "open_time_ms": timestamp,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("2"),
        "quote_volume": Decimal("200"),
        "trades": 10,
    }
    values.update(overrides)
    return RecoveryCandle(**values)  # type: ignore[arg-type]


def _source(timestamp: int, **overrides: object) -> SourceKline:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "open_time_ms": timestamp,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("2"),
        "quote_volume": Decimal("200"),
        "trades": 10,
    }
    values.update(overrides)
    return SourceKline(**values)  # type: ignore[arg-type]


def test_reconciliation_classifies_every_minute_in_the_work_unit() -> None:
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 240_000)
    records = tuple(
        reconcile_ordered_rows(
            work_unit=unit,
            dump_rows=(_dump(0), _dump(60_000), _dump(180_000)),
            binance_rows=(
                _source(0),
                _source(60_000, close=Decimal("100.5")),
                _source(120_000),
            ),
        )
    )

    assert [item.open_time_ms for item in records] == [0, 60_000, 120_000, 180_000]
    assert [item.classification for item in records] == [
        ReconciliationClass.EXACT_MATCH,
        ReconciliationClass.BINANCE_CORRECTION,
        ReconciliationClass.BINANCE_FILL,
        ReconciliationClass.SOURCE_UNAVAILABLE,
    ]
    assert records[0].dump_row_sha256 == records[0].binance_row_sha256
    assert records[1].differing_fields == ("close",)
    assert records[2].dump_row_sha256 is None
    assert records[3].binance_row_sha256 is None


def test_reconciliation_uses_fixed_field_order_and_exact_decimals() -> None:
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000)
    changed = _source(
        0,
        high=Decimal("103"),
        close=Decimal("101.000000000000000001"),
        volume=Decimal("3"),
        quote_volume=Decimal("300"),
        trades=11,
    )

    record = next(
        reconcile_ordered_rows(
            work_unit=unit,
            dump_rows=(_dump(0),),
            binance_rows=(changed,),
        )
    )

    assert record.classification is ReconciliationClass.BINANCE_CORRECTION
    assert record.differing_fields == (
        "high",
        "close",
        "volume",
        "quote_volume",
        "trades",
    )


def test_reconciliation_hashes_source_and_dump_rows_canonically() -> None:
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000)
    dump = _dump(0)
    record = next(
        reconcile_ordered_rows(
            work_unit=unit,
            dump_rows=(dump,),
            binance_rows=(_source(0),),
        )
    )

    assert record.dump_row_sha256 == dump.row_checksum()
    assert record.binance_row_sha256 == dump.row_checksum()


def test_absent_optional_dump_fields_do_not_turn_matching_ohlcv_into_a_correction() -> None:
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000)
    dump = replace(_dump(0), quote_volume=None, trades=None)

    record = next(
        reconcile_ordered_rows(
            work_unit=unit,
            dump_rows=(dump,),
            binance_rows=(_source(0, quote_volume=Decimal("999"), trades=999),),
        )
    )

    assert record.classification is ReconciliationClass.EXACT_MATCH
    assert record.differing_fields == ()
    assert record.dump_row_sha256 != record.binance_row_sha256


@pytest.mark.parametrize(
    ("dump_rows", "source_rows", "end_ms", "message"),
    [
        ((_dump(60_000), _dump(0)), (), 120_000, "ordered"),
        ((_dump(0), _dump(0)), (), 60_000, "duplicate"),
        ((), (_source(60_000), _source(0)), 120_000, "ordered"),
        ((), (_source(0), _source(0)), 60_000, "duplicate"),
        ((_dump(60_000),), (), 60_000, "outside"),
        ((), (_source(60_000),), 60_000, "outside"),
        ((replace(_dump(0), symbol="ETHUSDT"),), (), 60_000, "market boundary"),
        ((), (replace(_source(0), symbol="ETHUSDT"),), 60_000, "market boundary"),
    ],
)
def test_reconciliation_rejects_invalid_input_streams(
    dump_rows: tuple[RecoveryCandle, ...],
    source_rows: tuple[SourceKline, ...],
    end_ms: int,
    message: str,
) -> None:
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, end_ms)

    with pytest.raises(ValueError, match=message):
        tuple(
            reconcile_ordered_rows(
                work_unit=unit,
                dump_rows=dump_rows,
                binance_rows=source_rows,
            )
        )


def test_work_unit_identity_is_canonical_and_validated() -> None:
    first = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000)
    second = ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000)

    assert first == second
    assert len(first.work_unit_id) == 24
    assert first.work_unit_id.isalnum()
    with pytest.raises(ValueError, match="uppercase"):
        ReconciliationWorkUnit.create("btcusdt", "1m", 0, 60_000)
    with pytest.raises(ValueError, match="1m"):
        ReconciliationWorkUnit.create("BTCUSDT", "5m", 0, 60_000)
    with pytest.raises(ValueError, match="minute"):
        ReconciliationWorkUnit.create("BTCUSDT", "1m", 1, 60_000)
    with pytest.raises(ValueError, match="half-open"):
        ReconciliationWorkUnit.create("BTCUSDT", "1m", 60_000, 60_000)


def test_monthly_work_units_split_only_at_utc_month_boundaries() -> None:
    start = int(datetime(2025, 1, 31, 23, 58, tzinfo=UTC).timestamp() * 1_000)
    end = int(datetime(2025, 3, 1, 0, 2, tzinfo=UTC).timestamp() * 1_000)

    units = monthly_work_units(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=start,
        end_ms=end,
    )

    assert [(item.start_ms, item.end_ms) for item in units] == [
        (
            start,
            int(datetime(2025, 2, 1, tzinfo=UTC).timestamp() * 1_000),
        ),
        (
            int(datetime(2025, 2, 1, tzinfo=UTC).timestamp() * 1_000),
            int(datetime(2025, 3, 1, tzinfo=UTC).timestamp() * 1_000),
        ),
        (
            int(datetime(2025, 3, 1, tzinfo=UTC).timestamp() * 1_000),
            end,
        ),
    ]
    assert len({item.work_unit_id for item in units}) == 3
