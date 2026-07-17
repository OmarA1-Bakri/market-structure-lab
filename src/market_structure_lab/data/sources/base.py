"""Transport-neutral contract for authoritative candle sources."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, Protocol


class SourceError(RuntimeError):
    """Base class for source retrieval and integrity errors."""


class SourceUnavailable(SourceError):
    """The authoritative source has no payload for the requested range."""


class SourceRateLimited(SourceError):
    """The source requested a bounded pause before retrying."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("source rate limit exceeded")
        self.retry_after_seconds = max(0.0, retry_after_seconds)


class SourceIntegrityError(SourceError):
    """A source payload failed checksum or structural validation."""


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """A half-open range of one-minute candles from one market."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("symbol must be a non-empty uppercase exchange symbol")
        if self.timeframe != "1m":
            raise ValueError("recovery supports only the 1m timeframe")
        if self.start_ms % 60_000 or self.end_ms % 60_000:
            raise ValueError("recovery bounds must be aligned to the minute grid")
        if self.end_ms <= self.start_ms:
            raise ValueError("recovery range must be non-empty and half-open")


@dataclass(frozen=True, slots=True)
class SourceKline:
    """Exact source values normalized to epoch milliseconds."""

    symbol: str
    timeframe: str
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    trades: int | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.timeframe:
            raise ValueError("source kline market identity is required")
        if self.open_time_ms % 60_000:
            raise ValueError("source kline must be aligned to the minute grid")
        numeric = [self.open, self.high, self.low, self.close, self.volume]
        if self.quote_volume is not None:
            numeric.append(self.quote_volume)
        if any(not value.is_finite() for value in numeric):
            raise ValueError("source kline contains a non-finite numeric value")
        if min(self.open, self.high, self.low, self.close) < 0:
            raise ValueError("source kline contains a negative price")
        if self.volume < 0 or (self.quote_volume is not None and self.quote_volume < 0):
            raise ValueError("source kline contains negative volume")
        if self.trades is not None and self.trades < 0:
            raise ValueError("source kline contains a negative trade count")
        if self.high < max(self.open, self.low, self.close) or self.low > min(
            self.open, self.high, self.close
        ):
            raise ValueError("source kline violates OHLC relationships")

    def comparison_values(self) -> tuple[object, ...]:
        return (
            self.open_time_ms,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.quote_volume,
            self.trades,
        )


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    source_name: str
    source_revision: str
    location: str
    payload_checksum: str
    retrieved_at: str
    published_checksum: str | None = None
    excluded_row_count: int = 0
    integrity_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.excluded_row_count < 0:
            raise ValueError("excluded source row count cannot be negative")
        if self.integrity_notes != tuple(sorted(set(self.integrity_notes))):
            raise ValueError("source integrity notes must be unique and sorted")
        if any(not note for note in self.integrity_notes):
            raise ValueError("source integrity notes cannot be empty")


@dataclass(frozen=True, slots=True)
class FetchBatch:
    """One bounded, independently checkable source batch."""

    request: FetchRequest
    rows: tuple[SourceKline, ...]
    provenance: SourceProvenance
    authoritative_empty: bool = False

    def __post_init__(self) -> None:
        if len(self.rows) > 1_000:
            raise ValueError("source batches may contain at most 1000 candles")
        if self.authoritative_empty != (not self.rows):
            raise ValueError("authoritative_empty must describe an empty batch")


class MarketDataSource(Protocol):
    """A source that yields bounded batches for a half-open request."""

    name: str

    def fetch(self, request: FetchRequest) -> Iterator[FetchBatch]: ...
