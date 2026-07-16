"""Compatibility names for the canonical, bounded market-data boundary."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import polars as pl
from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from market_structure_lab.core.config import CandleSourceMapping, MarketDataSettings
from market_structure_lab.data.canonical import CANONICAL_COLUMNS, CANONICAL_SCHEMA
from market_structure_lab.data.loader import (
    DEFAULT_BATCH_SIZE,
    iter_candle_batches,
    load_candles,
)

OHLCV_COLUMNS = list(CANONICAL_COLUMNS)
OHLCV_SCHEMA = CANONICAL_SCHEMA


def iter_dataset_batches(
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
    """Yield bounded canonical batches for large reads and exports."""
    return iter_candle_batches(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        batch_size=batch_size,
        engine=engine,
        mapping=mapping,
        settings=settings,
    )


def load_symbol(
    symbol: str,
    *,
    timeframe: str = "1m",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    engine: Engine | Connection | None = None,
    mapping: CandleSourceMapping | None = None,
    settings: MarketDataSettings | None = None,
) -> pl.DataFrame:
    """Materialize one intentionally bounded research window."""
    return load_dataset(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        batch_size=batch_size,
        engine=engine,
        mapping=mapping,
        settings=settings,
    )


def load_dataset(
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
    """Materialize canonical candles with ordered, half-open range semantics.

    Use :func:`iter_dataset_batches` for ranges that are not deliberately small
    enough to fit in memory.
    """
    return load_candles(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        batch_size=batch_size,
        engine=engine,
        mapping=mapping,
        settings=settings,
    )
