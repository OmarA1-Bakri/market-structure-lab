from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import Connection, text

from market_structure_lab.data.canonical import (
    empty_candle_frame,
    normalize_utc,
    validate_candle_frame,
)

if TYPE_CHECKING:
    from market_structure_lab.data.gaps import GapRange

MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class SegmentBoundary:
    """An unresolved half-open gap that downstream sequences must never cross."""

    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    reason: str

    def __post_init__(self) -> None:
        start = normalize_utc(self.start)
        end = normalize_utc(self.end)
        if not self.symbol or not self.timeframe or not self.reason:
            raise ValueError("segment boundary fields must be non-empty")
        if start >= end:
            raise ValueError("segment boundary start must be before end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @classmethod
    def from_gap_range(cls, gap: GapRange, *, reason: str) -> SegmentBoundary:
        """Convert an exact post-recovery database gap without expanding minute keys."""
        return cls(
            symbol=gap.symbol,
            timeframe=gap.timeframe,
            start=datetime.fromtimestamp(gap.start_ms / 1_000, tz=UTC),
            end=datetime.fromtimestamp(gap.end_ms / 1_000, tz=UTC),
            reason=reason,
        )


class CandleSegmenter:
    """Assign stable series-local IDs from an immutable unresolved-gap set."""

    def __init__(self, boundaries: tuple[SegmentBoundary, ...] | list[SegmentBoundary]) -> None:
        grouped: dict[tuple[str, str], list[SegmentBoundary]] = {}
        for boundary in boundaries:
            grouped.setdefault((boundary.symbol, boundary.timeframe), []).append(boundary)
        self._boundaries: dict[tuple[str, str], tuple[SegmentBoundary, ...]] = {}
        self._ends: dict[tuple[str, str], tuple[datetime, ...]] = {}
        for key, values in grouped.items():
            ordered = tuple(sorted(values, key=lambda item: (item.start, item.end, item.reason)))
            for left, right in zip(ordered, ordered[1:], strict=False):
                if left.end > right.start:
                    raise ValueError(f"overlapping segment boundaries for {key!r}")
            self._boundaries[key] = ordered
            self._ends[key] = tuple(item.end for item in ordered)

    def apply(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Add ``segment_id``; reject candles that fall inside unresolved gaps."""
        if frame.is_empty():
            return empty_candle_frame(include_segment_id=True)
        validate_candle_frame(frame)
        segment_ids: list[int] = []
        for row in frame.select("timestamp", "symbol", "timeframe").iter_rows(named=True):
            key = (str(row["symbol"]), str(row["timeframe"]))
            timestamp = row["timestamp"]
            boundaries = self._boundaries.get(key, ())
            ends = self._ends.get(key, ())
            segment_id = bisect_right(ends, timestamp)
            if segment_id < len(boundaries):
                boundary = boundaries[segment_id]
                if boundary.start <= timestamp < boundary.end:
                    raise ValueError(
                        f"candle {key!r} at {timestamp.isoformat()} lies inside unresolved gap"
                    )
            segment_ids.append(segment_id)
        return frame.with_columns(pl.Series("segment_id", segment_ids, dtype=pl.UInt64))


def assign_segment_ids(
    frame: pl.DataFrame, boundaries: tuple[SegmentBoundary, ...] | list[SegmentBoundary]
) -> pl.DataFrame:
    return CandleSegmenter(boundaries).apply(frame)


def load_canonical_gap_boundaries(
    connection: Connection,
    *,
    symbol: str,
    timeframe: str = "1m",
    schema: str | None = "market_data",
    table: str = "candles_canonical",
    minimum_gap_minutes: int = 1,
) -> tuple[SegmentBoundary, ...]:
    """Derive exact hard boundaries from the post-recovery canonical series."""
    if not symbol or not timeframe:
        raise ValueError("symbol and timeframe must be non-empty")
    if minimum_gap_minutes < 1:
        raise ValueError("minimum_gap_minutes must be positive")
    preparer = connection.dialect.identifier_preparer
    quoted_table = preparer.quote(table)
    relation = f"{preparer.quote_schema(schema)}.{quoted_table}" if schema else quoted_table
    rows = connection.execute(
        text(
            f"""
WITH ordered AS (
    SELECT open_time,
           lead(open_time) OVER (ORDER BY open_time) AS next_open_time
    FROM {relation}
    WHERE symbol=:symbol AND "interval"=:timeframe
), remaining AS (
    SELECT open_time + :minute_ms AS gap_start,
           next_open_time AS gap_end,
           ((next_open_time - open_time) / :minute_ms) - 1 AS missing_minutes
    FROM ordered
    WHERE next_open_time - open_time > :minimum_span_ms
)
SELECT gap_start, gap_end, missing_minutes
FROM remaining
ORDER BY gap_start
"""
        ),
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "minute_ms": MINUTE_MS,
            "minimum_span_ms": minimum_gap_minutes * MINUTE_MS,
        },
    )
    return tuple(
        SegmentBoundary(
            symbol=symbol,
            timeframe=timeframe,
            start=datetime.fromtimestamp(int(row.gap_start) / 1_000, tz=UTC),
            end=datetime.fromtimestamp(int(row.gap_end) / 1_000, tz=UTC),
            reason=f"canonical_missing_candles:{int(row.missing_minutes)}",
        )
        for row in rows
    )
