from __future__ import annotations

import re
from datetime import UTC, datetime

import polars as pl
from polars._typing import SchemaDict
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from src.utils.db_inspection import get_connection_url

OHLCV_COLUMNS = ["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume"]
OHLCV_SCHEMA: SchemaDict = {
    "timestamp": pl.Datetime(time_zone="UTC"),
    "symbol": pl.Utf8,
    "timeframe": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}

_SAFE_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def load_symbol(
    symbol: str,
    *,
    timeframe: str = "1m",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    table_name: str = "ohlcv",
    engine: Engine | Connection | None = None,
) -> pl.DataFrame:
    """Load OHLCV candles for one symbol using the canonical dataset interface."""
    return load_dataset(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        table_name=table_name,
        engine=engine,
    )


def load_dataset(
    *,
    symbol: str,
    timeframe: str = "1m",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    table_name: str = "ohlcv",
    engine: Engine | Connection | None = None,
) -> pl.DataFrame:
    """Load OHLCV candles as an ordered Polars frame.

    Time windows are half-open: ``start <= timestamp < end``. This keeps
    adjacent research windows reproducible without duplicated boundary candles.
    """
    if not _SAFE_TABLE_NAME.fullmatch(table_name):
        raise ValueError("table_name must be a safe SQL identifier or schema-qualified identifier")

    owns_engine = engine is None
    active_engine = engine or _create_default_engine()
    query, params = _build_ohlcv_query(
        table_name=table_name,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )

    if isinstance(active_engine, Connection):
        rows = active_engine.execute(text(query), params).mappings().all()
    else:
        with active_engine.connect() as connection:
            rows = connection.execute(text(query), params).mappings().all()

    if owns_engine and isinstance(active_engine, Engine):
        active_engine.dispose()

    return _rows_to_frame([dict(row) for row in rows])


def _create_default_engine() -> Engine:
    from sqlalchemy import create_engine

    return create_engine(get_connection_url())


def _build_ohlcv_query(
    *,
    table_name: str,
    symbol: str,
    timeframe: str,
    start: str | datetime | None,
    end: str | datetime | None,
) -> tuple[str, dict[str, object]]:
    clauses = ["symbol = :symbol", "timeframe = :timeframe"]
    params: dict[str, object] = {"symbol": symbol, "timeframe": timeframe}

    if start is not None:
        clauses.append("timestamp >= :start")
        params["start"] = _database_timestamp(start)
    if end is not None:
        clauses.append("timestamp < :end")
        params["end"] = _database_timestamp(end)

    where_clause = " and ".join(clauses)
    query = f"""
        select timestamp, symbol, timeframe, open, high, low, close, volume
        from {table_name}
        where {where_clause}
        order by timestamp asc
    """
    return query, params


def _database_timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    return value


def _rows_to_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=OHLCV_SCHEMA).select(OHLCV_COLUMNS)

    normalized = [{**row, "timestamp": _utc_datetime(row["timestamp"])} for row in rows]
    return pl.DataFrame(normalized, schema=OHLCV_SCHEMA).select(OHLCV_COLUMNS)


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise TypeError(f"Unsupported timestamp value: {value!r}")
