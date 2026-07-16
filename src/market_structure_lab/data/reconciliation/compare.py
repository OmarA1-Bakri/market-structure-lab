"""Exact bounded merge comparison for immutable dump and Binance candle streams."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from decimal import Decimal

from market_structure_lab.data.reconciliation.models import (
    MINUTE_MS,
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.sources.base import SourceKline

_COMPARISON_FIELDS = (
    "symbol",
    "timeframe",
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
)


def monthly_work_units(
    *,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> tuple[ReconciliationWorkUnit, ...]:
    """Split a half-open range at UTC calendar-month boundaries."""
    first = ReconciliationWorkUnit.create(symbol, timeframe, start_ms, end_ms)
    units: list[ReconciliationWorkUnit] = []
    cursor = first.start_ms
    while cursor < first.end_ms:
        current = datetime.fromtimestamp(cursor / 1_000, tz=UTC)
        if current.month == 12:
            next_month = current.replace(
                year=current.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            next_month = current.replace(
                month=current.month + 1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        unit_end = min(first.end_ms, int(next_month.timestamp() * 1_000))
        units.append(ReconciliationWorkUnit.create(symbol, timeframe, cursor, unit_end))
        cursor = unit_end
    return tuple(units)


def reconcile_ordered_rows(
    *,
    work_unit: ReconciliationWorkUnit,
    dump_rows: Iterable[RecoveryCandle],
    binance_rows: Iterable[SourceKline | RecoveryCandle],
) -> Iterator[ReconciliationRecord]:
    """Classify every minute using at most one buffered row from each ordered stream."""
    dump = _RowCursor(dump_rows, work_unit=work_unit, stream_name="dump")
    source = _RowCursor(binance_rows, work_unit=work_unit, stream_name="Binance")

    for timestamp in range(work_unit.start_ms, work_unit.end_ms, MINUTE_MS):
        dump_row = dump.take(timestamp)
        source_row = source.take(timestamp)
        yield _record(work_unit, timestamp, dump_row, source_row)

    dump.require_exhausted()
    source.require_exhausted()


class _RowCursor:
    def __init__(
        self,
        rows: Iterable[SourceKline | RecoveryCandle],
        *,
        work_unit: ReconciliationWorkUnit,
        stream_name: str,
    ) -> None:
        self._rows = iter(rows)
        self._work_unit = work_unit
        self._stream_name = stream_name
        self._previous_time: int | None = None
        self._current = self._advance()

    def take(self, timestamp: int) -> RecoveryCandle | None:
        if self._current is None or self._current.open_time_ms != timestamp:
            return None
        row = self._current
        self._current = self._advance()
        return row

    def require_exhausted(self) -> None:
        if self._current is not None:
            raise ValueError(f"{self._stream_name} row is outside the work-unit bounds")

    def _advance(self) -> RecoveryCandle | None:
        try:
            value = next(self._rows)
        except StopIteration:
            return None
        row = RecoveryCandle.from_source(value) if isinstance(value, SourceKline) else value
        _validate_row(row, self._work_unit, self._stream_name)
        if self._previous_time is not None:
            if row.open_time_ms == self._previous_time:
                raise ValueError(f"{self._stream_name} stream contains a duplicate candle key")
            if row.open_time_ms < self._previous_time:
                raise ValueError(f"{self._stream_name} stream is not ordered")
        self._previous_time = row.open_time_ms
        return row


def _record(
    work_unit: ReconciliationWorkUnit,
    timestamp: int,
    dump: RecoveryCandle | None,
    source: RecoveryCandle | None,
) -> ReconciliationRecord:
    dump_hash = None if dump is None else dump.row_checksum()
    source_hash = None if source is None else source.row_checksum()
    differing = () if dump is None or source is None else _differing_fields(dump, source)
    if source is None:
        classification = ReconciliationClass.SOURCE_UNAVAILABLE
    elif dump is None:
        classification = ReconciliationClass.BINANCE_FILL
    elif differing:
        classification = ReconciliationClass.BINANCE_CORRECTION
    else:
        classification = ReconciliationClass.EXACT_MATCH
    return ReconciliationRecord(
        symbol=work_unit.symbol,
        timeframe=work_unit.timeframe,
        open_time_ms=timestamp,
        classification=classification,
        dump_row_sha256=dump_hash,
        binance_row_sha256=source_hash,
        differing_fields=differing,
    )


def _differing_fields(
    dump: RecoveryCandle,
    source: RecoveryCandle,
) -> tuple[str, ...]:
    return tuple(
        field for field in _COMPARISON_FIELDS if getattr(dump, field) != getattr(source, field)
    )


def _validate_row(
    row: RecoveryCandle,
    work_unit: ReconciliationWorkUnit,
    stream_name: str,
) -> None:
    if row.symbol != work_unit.symbol or row.timeframe != work_unit.timeframe:
        raise ValueError(f"{stream_name} row crosses the work-unit market boundary")
    if not work_unit.start_ms <= row.open_time_ms < work_unit.end_ms:
        raise ValueError(f"{stream_name} row is outside the work-unit bounds")
    if row.open_time_ms % MINUTE_MS:
        raise ValueError(f"{stream_name} row is not minute-aligned")
    numeric: list[Decimal] = [row.open, row.high, row.low, row.close, row.volume]
    if row.quote_volume is not None:
        numeric.append(row.quote_volume)
    if any(not value.is_finite() for value in numeric):
        raise ValueError(f"{stream_name} row contains a non-finite value")
    if min(row.open, row.high, row.low, row.close) < 0:
        raise ValueError(f"{stream_name} row contains a negative price")
    if row.volume < 0 or (row.quote_volume is not None and row.quote_volume < 0):
        raise ValueError(f"{stream_name} row contains negative volume")
    if row.trades is not None and row.trades < 0:
        raise ValueError(f"{stream_name} row contains a negative trade count")
    if row.high < max(row.open, row.low, row.close) or row.low > min(
        row.open, row.high, row.close
    ):
        raise ValueError(f"{stream_name} row violates OHLC relationships")


__all__ = ["monthly_work_units", "reconcile_ordered_rows"]
