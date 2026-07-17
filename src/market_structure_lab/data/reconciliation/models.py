"""Immutable identities and evidence for row-level candle reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

MINUTE_MS = 60_000


class ReconciliationClass(StrEnum):
    """Terminal classification for one expected one-minute candle key."""

    EXACT_MATCH = "exact_match"
    BINANCE_CORRECTION = "binance_correction"
    BINANCE_FILL = "binance_fill"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class ReconciliationWorkUnit:
    """One deterministic bounded symbol/timeframe reconciliation range."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    work_unit_id: str

    @classmethod
    def create(
        cls,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> ReconciliationWorkUnit:
        _validate_market_range(symbol, timeframe, start_ms, end_ms)
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            work_unit_id=_work_unit_id(symbol, timeframe, start_ms, end_ms),
        )

    def __post_init__(self) -> None:
        _validate_market_range(self.symbol, self.timeframe, self.start_ms, self.end_ms)
        expected = _work_unit_id(
            self.symbol,
            self.timeframe,
            self.start_ms,
            self.end_ms,
        )
        if self.work_unit_id != expected:
            raise ValueError("work_unit_id does not match the canonical work-unit identity")


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    """Frozen comparison evidence for one expected candle key."""

    symbol: str
    timeframe: str
    open_time_ms: int
    classification: ReconciliationClass
    dump_row_sha256: str | None
    binance_row_sha256: str | None
    differing_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("record symbol must be uppercase")
        if self.timeframe != "1m":
            raise ValueError("record timeframe must be 1m")
        if self.open_time_ms % MINUTE_MS:
            raise ValueError("record timestamp must be minute-aligned")
        for value in (self.dump_row_sha256, self.binance_row_sha256):
            if value is not None and (
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("record row hashes must be lowercase SHA-256 digests")


def _validate_market_range(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> None:
    if not symbol or symbol != symbol.upper():
        raise ValueError("symbol must be a non-empty uppercase exchange symbol")
    if timeframe != "1m":
        raise ValueError("reconciliation supports only the 1m timeframe")
    if start_ms % MINUTE_MS or end_ms % MINUTE_MS:
        raise ValueError("reconciliation bounds must be aligned to the minute grid")
    if end_ms <= start_ms:
        raise ValueError("reconciliation range must be non-empty and half-open")


def _work_unit_id(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> str:
    payload = {
        "end_ms": end_ms,
        "start_ms": start_ms,
        "symbol": symbol,
        "timeframe": timeframe,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:24]


__all__ = [
    "MINUTE_MS",
    "ReconciliationClass",
    "ReconciliationRecord",
    "ReconciliationWorkUnit",
]
