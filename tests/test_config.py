from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from market_structure_lab.core.config import (
    CandleSourceMapping,
    DatabaseSettings,
    TimestampUnit,
    load_settings,
)


def test_database_url_uses_structured_escaping_and_redaction() -> None:
    settings = DatabaseSettings(
        host="db.internal",
        port=5433,
        user="alice",
        password="p@ss/word",
        database="research",
    )

    assert (
        settings.url.render_as_string(hide_password=False)
        == "postgresql+psycopg://alice:p%40ss%2Fword@db.internal:5433/research"
    )
    assert settings.redacted_url == "postgresql+psycopg://alice:***@db.internal:5433/research"
    assert "p@ss/word" not in repr(settings)


def test_database_settings_require_an_explicit_nonempty_password(capsys) -> None:
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        DatabaseSettings()
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        DatabaseSettings(password="")
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        DatabaseSettings.from_env({})
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        DatabaseSettings.from_env({"POSTGRES_PASSWORD": "   "})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_database_settings_validate_port_and_identifiers() -> None:
    with pytest.raises(ValueError, match="port"):
        DatabaseSettings(port=0)
    with pytest.raises(ValueError, match="database name"):
        DatabaseSettings(database="research; drop database research")
    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        DatabaseSettings.from_env({"POSTGRES_PORT": "not-a-number"})


def test_candle_source_mapping_matches_reviewed_crypto_restore() -> None:
    mapping = CandleSourceMapping()

    assert mapping.version == "market-data-candles-v1"
    assert mapping.qualified_table == "market_data.candles"
    assert mapping.source_id_column == "id"
    assert mapping.timestamp_column == "open_time"
    assert mapping.timestamp_unit is TimestampUnit.MILLISECONDS
    assert mapping.timeframe_column == "interval"
    assert mapping.quote_volume_column == "quote_volume"
    assert mapping.trades_column == "trades"


def test_candle_mapping_is_frozen_and_validates_identifiers() -> None:
    mapping = CandleSourceMapping()
    with pytest.raises(FrozenInstanceError):
        mapping.table = "other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="table"):
        CandleSourceMapping(table="candles; delete")


def test_environment_builds_fresh_settings_without_a_global_singleton() -> None:
    environ = {
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5434",
        "POSTGRES_USER": "reader",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": "research",
        "CANDLE_SOURCE_SCHEMA": "market_data",
        "CANDLE_SOURCE_TABLE": "candles",
        "CANDLE_TIMESTAMP_UNIT": "milliseconds",
    }

    first = load_settings(environ)
    second = load_settings(environ)

    assert first == second
    assert first is not second
    assert first.database.port == 5434
    assert first.candles.qualified_table == "market_data.candles"
    assert "secret" not in repr(first)


def test_optional_source_columns_can_be_disabled_explicitly() -> None:
    mapping = CandleSourceMapping.from_env(
        {
            "CANDLE_QUOTE_VOLUME_COLUMN": "",
            "CANDLE_TRADES_COLUMN": "",
        }
    )

    assert mapping.quote_volume_column is None
    assert mapping.trades_column is None
