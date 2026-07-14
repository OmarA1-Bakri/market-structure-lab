from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.datasets import load_dataset, load_symbol


@pytest.fixture
def ohlcv_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table ohlcv (
                    symbol text not null,
                    timeframe text not null,
                    timestamp timestamp not null,
                    open real not null,
                    high real not null,
                    low real not null,
                    close real not null,
                    volume real not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into ohlcv
                    (symbol, timeframe, timestamp, open, high, low, close, volume)
                values
                    ('BTCUSDT', '1m', '2025-01-01 00:01:00', 101, 103, 100, 102, 12),
                    ('BTCUSDT', '1m', '2025-01-01 00:00:00', 100, 102,  99, 101, 10),
                    ('BTCUSDT', '1m', '2025-01-01 00:02:00', 102, 104, 101, 103, 14),
                    ('BTCUSDT', '5m', '2025-01-01 00:00:00', 100, 105,  98, 104, 50),
                    ('ETHUSDT', '1m', '2025-01-01 00:00:00', 200, 202, 199, 201, 20)
                """
            )
        )
    return engine


def test_load_dataset_returns_ordered_polars_frame_for_symbol_timeframe_and_window(
    ohlcv_engine: Engine,
) -> None:
    candles = load_dataset(
        symbol="BTCUSDT",
        timeframe="1m",
        start="2025-01-01 00:00:30",
        end="2025-01-01 00:02:00",
        engine=ohlcv_engine,
    )

    assert isinstance(candles, pl.DataFrame)
    assert candles.columns == [
        "timestamp",
        "symbol",
        "timeframe",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert candles["timestamp"].to_list() == [datetime(2025, 1, 1, 0, 1, tzinfo=UTC)]
    assert candles["symbol"].to_list() == ["BTCUSDT"]
    assert candles["timeframe"].to_list() == ["1m"]
    assert candles["close"].to_list() == [102.0]


def test_load_symbol_defaults_to_one_minute_dataset_for_symbol(ohlcv_engine: Engine) -> None:
    candles = load_symbol("ETHUSDT", engine=ohlcv_engine)

    assert candles.height == 1
    assert candles.row(0, named=True) == {
        "timestamp": datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        "symbol": "ETHUSDT",
        "timeframe": "1m",
        "open": 200.0,
        "high": 202.0,
        "low": 199.0,
        "close": 201.0,
        "volume": 20.0,
    }


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2025, 1, 1, 0, 0, 30), datetime(2025, 1, 1, 0, 2, 0)),
        (
            datetime(2025, 1, 1, 0, 0, 30, tzinfo=UTC),
            datetime(2025, 1, 1, 0, 2, 0, tzinfo=UTC),
        ),
    ],
)
def test_load_dataset_accepts_datetime_inputs_for_start_and_end(
    ohlcv_engine: Engine,
    start: datetime,
    end: datetime,
) -> None:
    candles = load_dataset(symbol="BTCUSDT", timeframe="1m", start=start, end=end, engine=ohlcv_engine)

    assert candles["timestamp"].to_list() == [datetime(2025, 1, 1, 0, 1, tzinfo=UTC)]
    assert candles["close"].to_list() == [102.0]
    assert candles["volume"].to_list() == [12.0]


def test_load_dataset_rejects_unsafe_table_names(ohlcv_engine: Engine) -> None:
    with pytest.raises(ValueError, match="table_name"):
        load_dataset(symbol="BTCUSDT", table_name="ohlcv; drop table ohlcv", engine=ohlcv_engine)
