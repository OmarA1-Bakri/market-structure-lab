from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Final, Mapping, cast

import polars as pl

CANONICAL_COLUMNS: Final[tuple[str, ...]] = (
    "timestamp",
    "symbol",
    "timeframe",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
CANONICAL_SCHEMA: Final = pl.Schema(
    cast(
        Any,
        {
            "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "symbol": pl.String,
            "timeframe": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
)

_TIMEFRAME_PATTERN = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mhd])$")
_UNIT_MICROSECONDS: Final[dict[str, int]] = {
    "m": 60_000_000,
    "h": 3_600_000_000,
    "d": 86_400_000_000,
}


def empty_candle_frame(*, include_segment_id: bool = False) -> pl.DataFrame:
    """Return an empty frame with the canonical, stable research schema."""
    schema = pl.Schema(CANONICAL_SCHEMA)
    if include_segment_id:
        schema["segment_id"] = pl.UInt64
    return pl.DataFrame(schema=schema)


def normalize_utc(value: str | datetime) -> datetime:
    """Parse an explicitly timezone-aware timestamp and normalize it to UTC."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from error
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError("timestamp must be an ISO-8601 string or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def timeframe_microseconds(timeframe: str) -> int:
    """Return the exact duration represented by a supported fixed timeframe."""
    match = _TIMEFRAME_PATTERN.fullmatch(timeframe)
    if match is None:
        raise ValueError(f"unsupported fixed timeframe: {timeframe!r}")
    return int(match.group("count")) * _UNIT_MICROSECONDS[match.group("unit")]


def validate_candle_values(row: Mapping[str, object]) -> None:
    """Fail loudly when a canonical candle is corrupt or off its timestamp grid."""
    timestamp = row["timestamp"]
    symbol = row["symbol"]
    timeframe = row["timeframe"]
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("canonical candle timestamp must be timezone-aware")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("canonical candle symbol must be non-empty")
    if not isinstance(timeframe, str):
        raise ValueError("canonical candle timeframe must be a string")

    epoch_microseconds = int(timestamp.timestamp() * 1_000_000)
    if epoch_microseconds % timeframe_microseconds(timeframe) != 0:
        raise ValueError("canonical candle timestamp is not aligned to its timeframe")

    values: dict[str, float] = {}
    for column in ("open", "high", "low", "close", "volume"):
        try:
            value = float(cast(Any, row[column]))
        except (TypeError, ValueError) as error:
            raise ValueError(f"canonical candle {column} must be numeric") from error
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"canonical candle {column} must be finite and non-negative")
        values[column] = value

    if values["low"] > min(values["open"], values["close"]):
        raise ValueError("canonical candle low exceeds its body")
    if values["high"] < max(values["open"], values["close"]):
        raise ValueError("canonical candle high is below its body")
    if values["low"] > values["high"]:
        raise ValueError("canonical candle low exceeds high")


def validate_candle_frame(frame: pl.DataFrame) -> None:
    """Validate schema values, ordering, and key uniqueness for a bounded frame."""
    missing = set(CANONICAL_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"canonical candle frame is missing columns: {sorted(missing)}")

    previous: tuple[str, str, datetime] | None = None
    for row in frame.select(CANONICAL_COLUMNS).iter_rows(named=True):
        validate_candle_values(row)
        key = (str(row["symbol"]), str(row["timeframe"]), row["timestamp"])
        if previous is not None and key <= previous:
            problem = "duplicate" if key == previous else "out-of-order"
            raise ValueError(f"{problem} canonical candle key: {key!r}")
        previous = key
