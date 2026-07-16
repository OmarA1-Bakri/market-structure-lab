"""Immutable domain inputs for deterministic auction reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite


def normalize_utc_timestamp(value: datetime) -> datetime:
    """Return an aware timestamp in UTC, rejecting ambiguous naive values."""
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AuctionCandle:
    """A validated canonical OHLCV observation at one auction timestamp."""

    timestamp: datetime
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    segment_id: int

    def __post_init__(self) -> None:
        timestamp = normalize_utc_timestamp(self.timestamp)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be non-empty")
        if isinstance(self.segment_id, bool) or not isinstance(self.segment_id, int):
            raise TypeError("segment_id must be an integer")
        if self.segment_id < 0:
            raise ValueError("segment_id must be non-negative")

        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise TypeError("OHLCV values must be numbers")
        normalized = tuple(float(value) for value in values)
        if not all(isfinite(value) for value in normalized):
            raise ValueError("OHLCV values must be finite")
        open_price, high, low, close, volume = normalized
        if min(open_price, high, low, close) < 0:
            raise ValueError("prices must be non-negative")
        if high < low:
            raise ValueError("high must be greater than or equal to low")
        if high < open_price or high < close:
            raise ValueError("high must be greater than or equal to open and close")
        if low > open_price or low > close:
            raise ValueError("low must be less than or equal to open and close")
        if volume < 0:
            raise ValueError("volume must be non-negative")

        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        object.__setattr__(self, "open", open_price)
        object.__setattr__(self, "high", high)
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "close", close)
        object.__setattr__(self, "volume", volume)
