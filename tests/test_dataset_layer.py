from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from market_structure_lab.core.config import CandleSourceMapping
from market_structure_lab.datasets import load_dataset, load_symbol

SQLITE_MAPPING = CandleSourceMapping(schema="main", table="candles_canonical")


@pytest.fixture
def ohlcv_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table candles_canonical (
                    symbol text not null,
                    interval text not null,
                    open_time integer not null,
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
                insert into candles_canonical
                    (symbol, interval, open_time, open, high, low, close, volume)
                values
                    ('BTCUSDT', '1m', 1735689660000, 101, 103, 100, 102, 12),
                    ('BTCUSDT', '1m', 1735689600000, 100, 102,  99, 101, 10),
                    ('BTCUSDT', '1m', 1735689720000, 102, 104, 101, 103, 14),
                    ('BTCUSDT', '5m', 1735689600000, 100, 105,  98, 104, 50),
                    ('ETHUSDT', '1m', 1735689600000, 200, 202, 199, 201, 20)
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
        start="2025-01-01T00:00:30Z",
        end="2025-01-01T00:02:00Z",
        engine=ohlcv_engine,
        mapping=SQLITE_MAPPING,
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
    candles = load_symbol("ETHUSDT", engine=ohlcv_engine, mapping=SQLITE_MAPPING)

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
    candles = load_dataset(
        symbol="BTCUSDT",
        timeframe="1m",
        start=start,
        end=end,
        engine=ohlcv_engine,
        mapping=SQLITE_MAPPING,
    )

    assert candles["timestamp"].to_list() == [datetime(2025, 1, 1, 0, 1, tzinfo=UTC)]
    assert candles["close"].to_list() == [102.0]
    assert candles["volume"].to_list() == [12.0]


def test_load_dataset_rejects_naive_datetime_bounds(ohlcv_engine: Engine) -> None:
    with pytest.raises(ValueError, match="timezone"):
        load_dataset(
            symbol="BTCUSDT",
            start=datetime(2025, 1, 1),
            engine=ohlcv_engine,
            mapping=SQLITE_MAPPING,
        )
