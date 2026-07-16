from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import polars as pl
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from market_structure_lab.core.config import CandleSourceMapping, MarketDataSettings
from market_structure_lab.data.canonical import (
    CANONICAL_COLUMNS,
    CANONICAL_SCHEMA,
    empty_candle_frame,
    normalize_utc,
    validate_candle_values,
)

DEFAULT_BATCH_SIZE = 10_000
CANONICAL_VIEW = "candles_canonical"


def canonical_view_mapping(source: CandleSourceMapping) -> CandleSourceMapping:
    """Retarget the inspected dump mapping to the dump-preferred canonical view."""
    return replace(source, table=CANONICAL_VIEW)


def iter_candle_batches(
    *,
    symbol: str,
    timeframe: str = "1m",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    engine: Engine | Connection | None = None,
    mapping: CandleSourceMapping | None = None,
    settings: MarketDataSettings | None = None,
) -> Iterator[pl.DataFrame]:
    """Stream ordered canonical candles using bounded server-side result batches.

    Ranges are half-open (``start <= timestamp < end``). Every yielded frame has
    at most ``batch_size`` rows and is validated before it crosses the data-layer
    boundary.
    """
    if not symbol:
        raise ValueError("symbol must be non-empty")
    if not timeframe:
        raise ValueError("timeframe must be non-empty")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    active_settings = settings
    if mapping is None:
        active_settings = active_settings or MarketDataSettings.from_env()
        mapping = canonical_view_mapping(active_settings.candles)

    start_utc = normalize_utc(start) if start is not None else None
    end_utc = normalize_utc(end) if end is not None else None
    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise ValueError("start must not be after end")

    owns_engine = engine is None
    if engine is None:
        active_settings = active_settings or MarketDataSettings.from_env()
        engine = create_engine(active_settings.database.url)

    query, params = build_candle_query(
        mapping=mapping,
        symbol=symbol,
        timeframe=timeframe,
        start=start_utc,
        end=end_utc,
    )

    context = nullcontext(engine) if isinstance(engine, Connection) else engine.connect()
    previous_key: tuple[str, str, datetime] | None = None
    try:
        with context as connection:
            streaming = connection.execution_options(
                stream_results=True,
                max_row_buffer=batch_size,
            )
            result = streaming.execute(text(query), params).mappings()
            while rows := result.fetchmany(batch_size):
                normalized: list[tuple[object, ...]] = []
                for raw_row in rows:
                    row = normalize_source_row(raw_row, mapping=mapping)
                    if row["symbol"] != symbol or row["timeframe"] != timeframe:
                        raise ValueError("source returned a candle outside the requested series")
                    validate_candle_values(row)
                    timestamp = cast(datetime, row["timestamp"])
                    key = (str(row["symbol"]), str(row["timeframe"]), timestamp)
                    if previous_key is not None and key <= previous_key:
                        problem = "duplicate" if key == previous_key else "out-of-order"
                        raise ValueError(f"{problem} canonical candle key: {key!r}")
                    previous_key = key
                    normalized.append(tuple(row[column] for column in CANONICAL_COLUMNS))
                yield pl.DataFrame(
                    normalized,
                    schema=CANONICAL_SCHEMA,
                    orient="row",
                )
    finally:
        if owns_engine and isinstance(engine, Engine):
            engine.dispose()


def load_candles(
    *,
    symbol: str,
    timeframe: str = "1m",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    engine: Engine | Connection | None = None,
    mapping: CandleSourceMapping | None = None,
    settings: MarketDataSettings | None = None,
) -> pl.DataFrame:
    """Materialize a deliberately bounded research window as a canonical frame."""
    batches = list(
        iter_candle_batches(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            batch_size=batch_size,
            engine=engine,
            mapping=mapping,
            settings=settings,
        )
    )
    if not batches:
        return empty_candle_frame()
    return pl.concat(batches, how="vertical", rechunk=True)


def build_candle_query(
    *,
    mapping: CandleSourceMapping,
    symbol: str,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
) -> tuple[str, dict[str, object]]:
    """Build source-specific SQL solely inside the data layer."""
    clauses = [
        f"{mapping.symbol_column} = :symbol",
        f"{mapping.timeframe_column} = :timeframe",
    ]
    params: dict[str, object] = {"symbol": symbol, "timeframe": timeframe}
    if start is not None:
        clauses.append(f"{mapping.timestamp_column} >= :start_timestamp")
        params["start_timestamp"] = datetime_to_source_epoch(start, mapping=mapping)
    if end is not None:
        clauses.append(f"{mapping.timestamp_column} < :end_timestamp")
        params["end_timestamp"] = datetime_to_source_epoch(end, mapping=mapping)

    selected = [
        f"{mapping.symbol_column} AS symbol",
        f"{mapping.timeframe_column} AS timeframe",
        f"{mapping.timestamp_column} AS source_timestamp",
        f"{mapping.open_column} AS open",
        f"{mapping.high_column} AS high",
        f"{mapping.low_column} AS low",
        f"{mapping.close_column} AS close",
        f"{mapping.volume_column} AS volume",
    ]
    where = " AND ".join(clauses)
    query = (
        f"SELECT {', '.join(selected)} FROM {mapping.qualified_table} "
        f"WHERE {where} ORDER BY {mapping.symbol_column}, {mapping.timeframe_column}, "
        f"{mapping.timestamp_column}"
    )
    return query, params


def normalize_source_row(raw_row: Any, *, mapping: CandleSourceMapping) -> dict[str, object]:
    timestamp = source_epoch_to_datetime(raw_row["source_timestamp"], mapping=mapping)
    return {
        "timestamp": timestamp,
        "symbol": raw_row["symbol"],
        "timeframe": raw_row["timeframe"],
        "open": raw_row["open"],
        "high": raw_row["high"],
        "low": raw_row["low"],
        "close": raw_row["close"],
        "volume": raw_row["volume"],
    }


def datetime_to_source_epoch(value: datetime, *, mapping: CandleSourceMapping) -> int:
    """Return the first source tick not earlier than an exact UTC boundary."""
    unit = _timestamp_unit_name(mapping)
    utc_value = value.astimezone(UTC)
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    microseconds = (
        delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    if unit == "microseconds":
        return microseconds
    milliseconds, remainder = divmod(microseconds, 1_000)
    return milliseconds + int(remainder > 0)


def source_epoch_to_datetime(value: object, *, mapping: CandleSourceMapping) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("source timestamp must be a numeric epoch")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("source timestamp must be an integer epoch")
    unit = _timestamp_unit_name(mapping)
    divisor = 1_000 if unit == "milliseconds" else 1_000_000
    try:
        return datetime.fromtimestamp(float(value) / divisor, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("source timestamp is outside the supported range") from error


def _timestamp_unit_name(mapping: CandleSourceMapping) -> str:
    raw_unit = getattr(mapping.timestamp_unit, "value", mapping.timestamp_unit)
    normalized = str(raw_unit).lower()
    aliases = {
        "ms": "milliseconds",
        "millisecond": "milliseconds",
        "milliseconds": "milliseconds",
        "us": "microseconds",
        "microsecond": "microseconds",
        "microseconds": "microseconds",
    }
    try:
        return aliases[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported source timestamp unit: {raw_unit!r}") from error
