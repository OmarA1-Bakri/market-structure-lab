from __future__ import annotations

import json

from sqlalchemy import create_engine, text

import market_structure_lab.cli.inspect_database as inspect_cli
from market_structure_lab.core.config import DatabaseSettings, MarketDataSettings
from market_structure_lab.data.inspection import (
    DatabaseInspectionError,
    InspectionMode,
    detect_candle_mapping,
    inspect_database,
    render_human,
    render_json,
)


def test_candidate_detection_accepts_reviewed_source_names_without_table_assumption() -> None:
    mapping = detect_candle_mapping(
        ["id", "symbol", "interval", "open_time", "open", "high", "low", "close", "volume"]
    )

    assert mapping is not None
    assert mapping.timestamp == "open_time"
    assert mapping.timeframe == "interval"


def test_candidate_detection_rejects_incomplete_tables() -> None:
    assert detect_candle_mapping(["symbol", "timestamp", "open", "close"]) is None


def test_full_inspection_queries_real_tables_and_quality_metrics() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table candles (
                    id integer primary key,
                    symbol text,
                    interval text,
                    open_time integer,
                    open real,
                    high real,
                    low real,
                    close real,
                    volume real
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into candles values
                    (1, 'BTCUSDT', '1m', 1735689600000, 100, 102, 99, 101, 10),
                    (2, 'BTCUSDT', '1m', 1735689720000, 102, 101, 103, 102, 12),
                    (3, 'BTCUSDT', '1m', 1735689720000, 102, 104, 101, 103, 14),
                    (4, 'ETHUSDT', '1m', 1735689600000, 200, 202, 199, 201, null),
                    (5, 'XRPUSDT', '1m', 1735689600000, -2, -1, -3, -2, 1)
                """
            )
        )

    report = inspect_database(
        engine,
        settings=MarketDataSettings(),
        mode=InspectionMode.FULL,
        sample_limit=2,
        large_gap_minutes=1,
    )

    assert report.schemas == ("main",)
    assert report.table_count == 1
    assert report.candidate_count == 1
    table = report.tables[0]
    assert table.exact_row_count == 5
    assert table.quality is not None
    assert table.quality.row_count == 5
    assert table.quality.symbols == ("BTCUSDT", "ETHUSDT", "XRPUSDT")
    assert table.quality.timeframes == ("1m",)
    assert table.quality.minimum_timestamp == "2025-01-01T00:00:00Z"
    assert table.quality.maximum_timestamp == "2025-01-01T00:02:00Z"
    assert table.quality.duplicate_key_count == 1
    assert table.quality.invalid_ohlc_count == 2
    assert table.quality.null_counts["volume"] == 1
    assert table.quality.gap_run_count == 1
    assert table.quality.missing_minute_count == 1
    assert table.quality.largest_gap_minutes == 1
    assert len(table.quality.sample_rows) == 2


def test_quick_inspection_skips_expensive_exact_metrics() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table notes (id integer primary key, body text)"))

    report = inspect_database(
        engine,
        settings=MarketDataSettings(),
        mode=InspectionMode.QUICK,
    )

    assert report.tables[0].exact_row_count is None
    assert report.tables[0].quality is None
    assert report.candidate_count == 0


def test_full_inspection_handles_an_empty_candidate_table() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table candles (
                    symbol text, interval text, open_time integer,
                    open real, high real, low real, close real, volume real
                )
                """
            )
        )

    report = inspect_database(
        engine,
        settings=MarketDataSettings(),
        mode=InspectionMode.FULL,
    )

    quality = report.tables[0].quality
    assert quality is not None
    assert quality.row_count == 0
    assert quality.minimum_timestamp is None
    assert quality.maximum_timestamp is None
    assert all(count == 0 for count in quality.null_counts.values())


def test_renderers_are_deterministic_and_never_include_password() -> None:
    password = "do-not-print-this"
    settings = MarketDataSettings(
        database=DatabaseSettings(password=password),
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table notes (id integer primary key, body text)"))
    report = inspect_database(engine, settings=settings)

    first = render_json(report)
    second = render_json(report)
    human = render_human(report)

    assert first == second
    assert json.loads(first)["connection_url"].endswith("@localhost:5432/research")
    assert password not in first
    assert password not in human
    assert "***" in human


def test_cli_failure_output_never_includes_password(monkeypatch, capsys) -> None:
    password = "do-not-print-this"
    settings = MarketDataSettings(database=DatabaseSettings(password=password))
    monkeypatch.setattr(inspect_cli, "load_settings", lambda: settings)

    def fail_inspection(*args, **kwargs):
        raise DatabaseInspectionError(
            f"database inspection failed for {settings.database.redacted_url}"
        )

    monkeypatch.setattr(inspect_cli, "inspect_configured_database", fail_inspection)

    assert inspect_cli.main(["--full"]) == 1
    output = capsys.readouterr().err
    assert password not in output
    assert "***" in output
