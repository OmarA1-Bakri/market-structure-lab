from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from sqlalchemy import Engine, create_engine, text

from market_structure_lab.core.config import CandleSourceMapping, TimestampUnit
from market_structure_lab.data.loader import (
    build_candle_query,
    iter_candle_batches,
    load_candles,
    source_epoch_to_datetime,
)


@pytest.fixture
def candle_engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE candles_canonical (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL
                )
                """
            )
        )
        rows = [
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": 1_704_067_200_000 + minute * 60_000,
                "open": 100.0 + minute,
                "high": 102.0 + minute,
                "low": 99.0 + minute,
                "close": 101.0 + minute,
                "volume": 10.0 + minute,
            }
            for minute in range(5)
        ]
        connection.execute(
            text(
                """
                INSERT INTO candles_canonical
                    (symbol, interval, open_time, open, high, low, close, volume)
                VALUES
                    (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume)
                """
            ),
            rows,
        )
    return engine


@pytest.fixture
def sqlite_mapping() -> CandleSourceMapping:
    return CandleSourceMapping(schema="main", table="candles_canonical")


def test_loader_yields_only_bounded_batches_in_explicit_order(
    candle_engine: Engine, sqlite_mapping: CandleSourceMapping
) -> None:
    batches = list(
        iter_candle_batches(
            symbol="BTCUSDT",
            engine=candle_engine,
            mapping=sqlite_mapping,
            batch_size=2,
        )
    )

    assert [batch.height for batch in batches] == [2, 2, 1]
    timestamps = pl.concat(batches)["timestamp"].to_list()
    assert timestamps == sorted(timestamps)
    assert all(value.utcoffset() == timedelta(0) for value in timestamps)


def test_loader_has_empty_schema_and_adjacent_half_open_windows(
    candle_engine: Engine, sqlite_mapping: CandleSourceMapping
) -> None:
    boundary = "2024-01-01T00:02:00Z"
    left = load_candles(
        symbol="BTCUSDT",
        start="2024-01-01T00:00:00Z",
        end=boundary,
        engine=candle_engine,
        mapping=sqlite_mapping,
    )
    right = load_candles(
        symbol="BTCUSDT",
        start=boundary,
        end="2024-01-01T00:05:00Z",
        engine=candle_engine,
        mapping=sqlite_mapping,
    )
    empty = load_candles(
        symbol="ETHUSDT",
        start="2024-01-01T00:00:00Z",
        end="2024-01-01T00:05:00Z",
        engine=candle_engine,
        mapping=sqlite_mapping,
    )

    assert left.height == 2
    assert right.height == 3
    assert left["timestamp"].max() < right["timestamp"].min()
    assert empty.is_empty()
    assert empty.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")


def test_loader_normalizes_offset_bounds_to_utc(
    candle_engine: Engine, sqlite_mapping: CandleSourceMapping
) -> None:
    frame = load_candles(
        symbol="BTCUSDT",
        start="2024-01-01T07:00:00+07:00",
        end="2024-01-01T07:01:00+07:00",
        engine=candle_engine,
        mapping=sqlite_mapping,
    )

    assert frame["timestamp"].to_list() == [datetime(2024, 1, 1, tzinfo=UTC)]


def test_query_consumes_mapping_and_orders_source_keys(sqlite_mapping: CandleSourceMapping) -> None:
    query, params = build_candle_query(
        mapping=sqlite_mapping,
        symbol="BTCUSDT",
        timeframe="1m",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert "FROM main.candles_canonical" in query
    assert "ORDER BY symbol, interval, open_time" in query
    assert params["start_timestamp"] == 1_704_067_200_000
    assert params["end_timestamp"] == 1_704_153_600_000


def test_millisecond_query_rounds_exact_submillisecond_boundaries_up(
    sqlite_mapping: CandleSourceMapping,
) -> None:
    _, params = build_candle_query(
        mapping=sqlite_mapping,
        symbol="BTCUSDT",
        timeframe="1m",
        start=datetime(2024, 1, 1, 0, 0, 0, 500, tzinfo=UTC),
        end=datetime(2024, 1, 1, 0, 1, 0, 500, tzinfo=UTC),
    )

    assert params["start_timestamp"] == 1_704_067_200_001
    assert params["end_timestamp"] == 1_704_067_260_001


def test_mapping_normalizes_microsecond_source_epochs() -> None:
    mapping = CandleSourceMapping(timestamp_unit=TimestampUnit.MICROSECONDS)

    assert source_epoch_to_datetime(1_735_689_600_000_000, mapping=mapping) == datetime(
        2025, 1, 1, tzinfo=UTC
    )


def test_loader_rejects_naive_bounds(
    candle_engine: Engine, sqlite_mapping: CandleSourceMapping
) -> None:
    with pytest.raises(ValueError, match="timezone"):
        load_candles(
            symbol="BTCUSDT",
            start=datetime(2024, 1, 1),
            end="2024-01-01T00:01:00Z",
            engine=candle_engine,
            mapping=sqlite_mapping,
        )


def test_materializing_loader_requires_both_bounds(
    candle_engine: Engine, sqlite_mapping: CandleSourceMapping
) -> None:
    with pytest.raises(TypeError, match="start"):
        load_candles(  # type: ignore[call-arg]
            symbol="BTCUSDT",
            end="2024-01-01T00:05:00Z",
            engine=candle_engine,
            mapping=sqlite_mapping,
        )
    with pytest.raises(TypeError, match="end"):
        load_candles(  # type: ignore[call-arg]
            symbol="BTCUSDT",
            start="2024-01-01T00:00:00Z",
            engine=candle_engine,
            mapping=sqlite_mapping,
        )
    with pytest.raises(TypeError, match="start.*end|end.*start"):
        load_candles(  # type: ignore[call-arg]
            symbol="BTCUSDT",
            engine=candle_engine,
            mapping=sqlite_mapping,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (None, "2024-01-01T00:05:00Z"),
        ("2024-01-01T00:00:00Z", None),
    ],
)
def test_materializing_loader_rejects_explicit_none_bounds(
    candle_engine: Engine,
    sqlite_mapping: CandleSourceMapping,
    start: str | None,
    end: str | None,
) -> None:
    with pytest.raises(ValueError, match="explicit start and end"):
        load_candles(
            symbol="BTCUSDT",
            start=start,  # type: ignore[arg-type]
            end=end,  # type: ignore[arg-type]
            engine=candle_engine,
            mapping=sqlite_mapping,
        )


@pytest.mark.parametrize(
    ("extra_row", "message"),
    [
        (
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": 1_704_067_200_000,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10.0,
            },
            "duplicate",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": 1_704_067_500_000,
                "open": 100.0,
                "high": 90.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10.0,
            },
            "high is below",
        ),
    ],
)
def test_loader_rejects_duplicate_and_invalid_rows(
    candle_engine: Engine,
    sqlite_mapping: CandleSourceMapping,
    extra_row: dict[str, object],
    message: str,
) -> None:
    with candle_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO candles_canonical
                    (symbol, interval, open_time, open, high, low, close, volume)
                VALUES
                    (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume)
                """
            ),
            extra_row,
        )

    with pytest.raises(ValueError, match=message):
        list(
            iter_candle_batches(
                symbol="BTCUSDT",
                engine=candle_engine,
                mapping=sqlite_mapping,
                batch_size=2,
            )
        )
