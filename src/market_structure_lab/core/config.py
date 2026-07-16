"""Typed environment configuration with secret-safe representations."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv
from sqlalchemy import URL

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TimestampUnit(str, Enum):
    """Supported integer timestamp units at source boundaries."""

    MILLISECONDS = "milliseconds"
    MICROSECONDS = "microseconds"


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Connection settings created explicitly for each operation."""

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = field(default="postgres", repr=False)
    database: str = "research"
    drivername: str = "postgresql+psycopg"

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("database host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("database port must be between 1 and 65535")
        _require_identifier("database user", self.user)
        _require_identifier("database name", self.database)
        if self.drivername != "postgresql+psycopg":
            raise ValueError("database driver must be postgresql+psycopg")

    @property
    def url(self) -> URL:
        """Return a structured URL so credentials are escaped correctly."""
        return URL.create(
            drivername=self.drivername,
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    @property
    def redacted_url(self) -> str:
        """Return a connection URL suitable for logs and reports."""
        return self.url.render_as_string(hide_password=True)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseSettings:
        """Build settings from an environment mapping without caching global state."""
        if environ is None:
            load_dotenv()
            environ = os.environ
        return cls(
            host=environ.get("POSTGRES_HOST", "localhost"),
            port=_parse_port(environ.get("POSTGRES_PORT", "5432")),
            user=environ.get("POSTGRES_USER", "postgres"),
            password=environ.get("POSTGRES_PASSWORD", "postgres"),
            database=environ.get("POSTGRES_DB", "research"),
        )


@dataclass(frozen=True, slots=True)
class CandleSourceMapping:
    """Reviewed source-to-canonical mapping for the immutable dump candles."""

    version: str = "market-data-candles-v1"
    schema: str = "market_data"
    table: str = "candles"
    source_id_column: str = "id"
    timestamp_column: str = "open_time"
    timestamp_unit: TimestampUnit = TimestampUnit.MILLISECONDS
    symbol_column: str = "symbol"
    timeframe_column: str = "interval"
    open_column: str = "open"
    high_column: str = "high"
    low_column: str = "low"
    close_column: str = "close"
    volume_column: str = "volume"
    quote_volume_column: str | None = "quote_volume"
    trades_column: str | None = "trades"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", self.version):
            raise ValueError("mapping version must be a stable identifier")
        for field_name in (
            "schema",
            "table",
            "source_id_column",
            "timestamp_column",
            "symbol_column",
            "timeframe_column",
            "open_column",
            "high_column",
            "low_column",
            "close_column",
            "volume_column",
        ):
            _require_identifier(field_name, getattr(self, field_name))
        for field_name in ("quote_volume_column", "trades_column"):
            value = getattr(self, field_name)
            if value is not None:
                _require_identifier(field_name, value)
        if isinstance(self.timestamp_unit, str):
            try:
                object.__setattr__(self, "timestamp_unit", TimestampUnit(self.timestamp_unit))
            except ValueError as exc:
                raise ValueError("timestamp_unit must be milliseconds or microseconds") from exc

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CandleSourceMapping:
        if environ is None:
            load_dotenv()
            environ = os.environ
        return cls(
            version=environ.get("CANDLE_MAPPING_VERSION", "market-data-candles-v1"),
            schema=environ.get("CANDLE_SOURCE_SCHEMA", "market_data"),
            table=environ.get("CANDLE_SOURCE_TABLE", "candles"),
            source_id_column=environ.get("CANDLE_SOURCE_ID_COLUMN", "id"),
            timestamp_column=environ.get("CANDLE_TIMESTAMP_COLUMN", "open_time"),
            timestamp_unit=TimestampUnit(
                environ.get("CANDLE_TIMESTAMP_UNIT", TimestampUnit.MILLISECONDS.value)
            ),
            symbol_column=environ.get("CANDLE_SYMBOL_COLUMN", "symbol"),
            timeframe_column=environ.get("CANDLE_TIMEFRAME_COLUMN", "interval"),
            open_column=environ.get("CANDLE_OPEN_COLUMN", "open"),
            high_column=environ.get("CANDLE_HIGH_COLUMN", "high"),
            low_column=environ.get("CANDLE_LOW_COLUMN", "low"),
            close_column=environ.get("CANDLE_CLOSE_COLUMN", "close"),
            volume_column=environ.get("CANDLE_VOLUME_COLUMN", "volume"),
            quote_volume_column=_optional_identifier(
                environ, "CANDLE_QUOTE_VOLUME_COLUMN", "quote_volume"
            ),
            trades_column=_optional_identifier(environ, "CANDLE_TRADES_COLUMN", "trades"),
        )


@dataclass(frozen=True, slots=True)
class MarketDataSettings:
    """Complete settings required by source-specific data-layer operations."""

    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    candles: CandleSourceMapping = field(default_factory=CandleSourceMapping)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MarketDataSettings:
        if environ is None:
            load_dotenv()
            environ = os.environ
        return cls(
            database=DatabaseSettings.from_env(environ),
            candles=CandleSourceMapping.from_env(environ),
        )


def load_settings(environ: Mapping[str, str] | None = None) -> MarketDataSettings:
    """Create fresh settings; callers own their configuration lifecycle."""
    return MarketDataSettings.from_env(environ)


def _parse_port(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("POSTGRES_PORT must be an integer") from exc


def _require_identifier(field_name: str, value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a valid SQL identifier")


def _optional_identifier(
    environ: Mapping[str, str],
    key: str,
    default: str,
) -> str | None:
    value = environ.get(key, default).strip()
    return value or None
