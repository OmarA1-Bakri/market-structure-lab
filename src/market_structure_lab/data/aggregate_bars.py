"""Bounded deterministic aggregation of complete canonical one-minute bars."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import math
import re
from typing import Any, Final, cast

import polars as pl

from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.data.canonical import (
    CANONICAL_COLUMNS,
    CANONICAL_SCHEMA,
    timeframe_microseconds,
    validate_candle_values,
)
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)

SOURCE_TIMEFRAME: Final = "1m"
SUPPORTED_TARGET_TIMEFRAMES: Final = ("15m", "1h", "4h")
CONTINUITY_ID: Final = "complete-contiguous-1m-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SCHEMA = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))


class OrderedSourceIdentity:
    """Streaming domain-separated identity for ordered canonical source-row IDs."""

    __slots__ = ("_count", "_state")

    def __init__(self) -> None:
        self._count = 0
        self._state = hash_json(
            "canonical-source-minute-stream-start",
            {"source_timeframe": SOURCE_TIMEFRAME},
        )

    @property
    def count(self) -> int:
        return self._count

    def update(self, source_row_id: str) -> None:
        _require_sha256(source_row_id, "source row identity")
        self._state = hash_json(
            "canonical-source-minute-stream-step",
            {
                "index": self._count,
                "previous_sha256": self._state,
                "source_row_sha256": source_row_id,
            },
        )
        self._count += 1

    def hexdigest(self) -> str:
        return hash_json(
            "canonical-source-minute-stream-final",
            {"count": self._count, "ordered_chain_sha256": self._state},
        )


def canonical_source_row_payload(row: Mapping[str, object]) -> dict[str, object]:
    """Return the exact schema-versioned logical payload for one canonical source minute."""

    if set(row) != {*CANONICAL_COLUMNS, "segment_id"}:
        raise ValueError("canonical source row schema is invalid")
    validate_candle_values(row)
    segment_id = row["segment_id"]
    if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 0:
        raise ValueError("canonical source row segment_id must be a non-negative integer")
    timestamp = cast(datetime, row["timestamp"]).astimezone(UTC)
    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "open": cast(float, row["open"]),
        "high": cast(float, row["high"]),
        "low": cast(float, row["low"]),
        "close": cast(float, row["close"]),
        "volume": cast(float, row["volume"]),
        "segment_id": segment_id,
    }


def canonical_source_row_identity(row: Mapping[str, object]) -> str:
    """Return a domain-separated identity for one exact canonical source minute."""

    return hash_json("canonical-source-minute", canonical_source_row_payload(row))


def source_rows_sha256(
    rows: Iterable[Mapping[str, object] | str],
    *,
    identities: bool = False,
) -> str:
    """Return a bounded-memory digest of ordered source rows or their identities."""

    accumulator = OrderedSourceIdentity()
    for row in rows:
        if identities:
            if not isinstance(row, str):
                raise TypeError("source identity stream must contain strings")
            source_row_id = row
        else:
            if not isinstance(row, Mapping):
                raise TypeError("source row stream must contain mappings")
            source_row_id = canonical_source_row_identity(row)
        accumulator.update(source_row_id)
    return accumulator.hexdigest()


@dataclass(frozen=True, slots=True)
class CanonicalAggregateBar:
    """One complete target bar bound to every ordered one-minute source identity."""

    schema_version: int
    timestamp: datetime
    bar_close: datetime
    symbol: str
    source_timeframe: str
    target_timeframe: str
    segment_id: int
    continuity: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_row_count: int
    source_row_ids: tuple[str, ...]
    source_sha256: str
    parent_snapshot_sha256: str
    row_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported aggregate row schema")
        if self.source_timeframe != SOURCE_TIMEFRAME:
            raise ValueError("aggregate source timeframe must be 1m")
        target_minutes = target_timeframe_minutes(self.target_timeframe)
        timestamp = _require_utc(self.timestamp, "aggregate timestamp")
        bar_close = _require_utc(self.bar_close, "aggregate bar_close")
        if timestamp != self.timestamp or bar_close != self.bar_close:
            raise ValueError("aggregate timestamps must use UTC")
        if bar_close != timestamp + timedelta(minutes=target_minutes):
            raise ValueError("aggregate bar_close must be exactly one target period after open")
        if int(timestamp.timestamp() * 1_000_000) % timeframe_microseconds(self.target_timeframe):
            raise ValueError("aggregate timestamp is not aligned to target timeframe")
        validate_candle_values(
            {
                "timestamp": timestamp,
                "symbol": self.symbol,
                "timeframe": self.target_timeframe,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
            }
        )
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("aggregate segment_id must be a non-negative integer")
        if self.continuity != CONTINUITY_ID:
            raise ValueError("aggregate continuity identity is unsupported")
        if self.source_row_count != target_minutes:
            raise ValueError("aggregate source row count does not complete target period")
        if len(self.source_row_ids) != self.source_row_count:
            raise ValueError("aggregate source identity count mismatch")
        for source_row_id in self.source_row_ids:
            _require_sha256(source_row_id, "source row identity")
        expected_source_sha256 = source_rows_sha256(self.source_row_ids, identities=True)
        if self.source_sha256 != expected_source_sha256:
            raise ValueError("aggregate source digest mismatch")
        _require_sha256(self.parent_snapshot_sha256, "parent snapshot sha256")
        expected_row_sha256 = hash_json("canonical-aggregate-bar", self.logical_dict())
        if self.row_sha256 and self.row_sha256 != expected_row_sha256:
            raise ValueError("aggregate row identity mismatch")
        object.__setattr__(self, "row_sha256", expected_row_sha256)

    def logical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "bar_close": self.bar_close,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "target_timeframe": self.target_timeframe,
            "segment_id": self.segment_id,
            "continuity": self.continuity,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source_row_count": self.source_row_count,
            "source_row_ids": self.source_row_ids,
            "source_sha256": self.source_sha256,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.logical_dict(),
            "timestamp": _iso_utc(self.timestamp),
            "bar_close": _iso_utc(self.bar_close),
            "source_row_ids": list(self.source_row_ids),
            "row_sha256": self.row_sha256,
        }

    def to_json_line(self) -> bytes:
        return (json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> CanonicalAggregateBar:
        expected = {
            "schema_version",
            "timestamp",
            "bar_close",
            "symbol",
            "source_timeframe",
            "target_timeframe",
            "segment_id",
            "continuity",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source_row_count",
            "source_row_ids",
            "source_sha256",
            "parent_snapshot_sha256",
            "row_sha256",
        }
        if set(row) != expected:
            raise ValueError("aggregate row schema is invalid")
        source_ids = row["source_row_ids"]
        if not isinstance(source_ids, (list, tuple)):
            raise ValueError("aggregate source_row_ids must be an ordered sequence")
        return cls(
            schema_version=int(cast(Any, row["schema_version"])),
            timestamp=_coerce_datetime(row["timestamp"]),
            bar_close=_coerce_datetime(row["bar_close"]),
            symbol=str(row["symbol"]),
            source_timeframe=str(row["source_timeframe"]),
            target_timeframe=str(row["target_timeframe"]),
            segment_id=int(cast(Any, row["segment_id"])),
            continuity=str(row["continuity"]),
            open=float(cast(Any, row["open"])),
            high=float(cast(Any, row["high"])),
            low=float(cast(Any, row["low"])),
            close=float(cast(Any, row["close"])),
            volume=float(cast(Any, row["volume"])),
            source_row_count=int(cast(Any, row["source_row_count"])),
            source_row_ids=tuple(str(value) for value in source_ids),
            source_sha256=str(row["source_sha256"]),
            parent_snapshot_sha256=str(row["parent_snapshot_sha256"]),
            row_sha256=str(row["row_sha256"]),
        )


def target_timeframe_minutes(target_timeframe: str) -> int:
    if target_timeframe not in SUPPORTED_TARGET_TIMEFRAMES:
        raise ValueError(
            "target timeframe must be one of the supported integral values: 15m, 1h, 4h"
        )
    target_microseconds = timeframe_microseconds(target_timeframe)
    source_microseconds = timeframe_microseconds(SOURCE_TIMEFRAME)
    quotient, remainder = divmod(target_microseconds, source_microseconds)
    if remainder or quotient < 1:
        raise ValueError("target timeframe must be an integral multiple of 1m")
    return quotient


def iter_complete_aggregate_bars(
    batches: Iterable[pl.DataFrame],
    *,
    target_timeframe: str,
    expected_source_sha256: str,
    parent_snapshot_sha256: str,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
) -> Iterator[CanonicalAggregateBar]:
    """Stream complete target bars and reject any continuity or identity ambiguity."""

    target_minutes = target_timeframe_minutes(target_timeframe)
    _require_sha256(expected_source_sha256, "expected source sha256")
    _require_sha256(parent_snapshot_sha256, "parent snapshot sha256")
    if not isinstance(demand, ValidationWorkDemand):
        raise TypeError("demand must be a ValidationWorkDemand")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be a ValidationWorkBudget")
    budget.preflight(demand, deferred_work=batches)

    source_stream = OrderedSourceIdentity()
    current_rows: list[dict[str, object]] = []
    current_ids: list[str] = []
    previous_timestamp: datetime | None = None
    first_key: tuple[str, str, int] | None = None
    actual_source_bytes = 0
    aggregate_count = 0

    for batch in batches:
        _validate_source_frame_schema(batch)
        for raw_row in batch.iter_rows(named=True):
            row = canonical_source_row_payload(raw_row)
            timestamp = cast(datetime, row["timestamp"])
            series_key = (
                cast(str, row["symbol"]),
                cast(str, row["timeframe"]),
                cast(int, row["segment_id"]),
            )
            if series_key[1] != SOURCE_TIMEFRAME:
                raise ValueError("aggregate source timeframe must remain 1m")
            if first_key is None:
                first_key = series_key
                if int(timestamp.timestamp() * 1_000_000) % timeframe_microseconds(
                    target_timeframe
                ):
                    raise ValueError("first source minute is not aligned to target timeframe")
            elif series_key != first_key:
                raise ValueError("mixed symbol, source timeframe, or segment in aggregate input")
            if previous_timestamp is not None:
                expected_timestamp = previous_timestamp + timedelta(minutes=1)
                if timestamp == previous_timestamp:
                    raise ValueError("duplicate canonical source minute")
                if timestamp < expected_timestamp:
                    raise ValueError("reordered or out-of-order canonical source minute")
                if timestamp > expected_timestamp:
                    raise ValueError("gap in canonical source minutes; filling is prohibited")
            previous_timestamp = timestamp

            source_row_id = canonical_source_row_identity(raw_row)
            encoded_bytes = len(canonical_json("canonical-source-minute", row))
            source_stream.update(source_row_id)
            actual_source_bytes += encoded_bytes
            _require_within_declared(
                "source_rows", source_stream.count, demand.source_rows, budget.max_source_rows
            )
            _require_within_declared(
                "source_bytes",
                actual_source_bytes,
                demand.source_bytes,
                budget.max_source_bytes,
            )
            current_rows.append(row)
            current_ids.append(source_row_id)
            if len(current_rows) == target_minutes:
                aggregate_count += 1
                _require_within_declared(
                    "aggregate_bars",
                    aggregate_count,
                    demand.aggregate_bars,
                    budget.max_aggregate_bars,
                )
                yield _aggregate_rows(
                    current_rows,
                    tuple(current_ids),
                    target_timeframe=target_timeframe,
                    parent_snapshot_sha256=parent_snapshot_sha256,
                )
                current_rows = []
                current_ids = []

    if current_rows:
        raise ValueError("partial final target period is prohibited")
    if source_stream.hexdigest() != expected_source_sha256:
        raise ValueError("source digest mismatch")


def _aggregate_rows(
    rows: list[dict[str, object]],
    source_row_ids: tuple[str, ...],
    *,
    target_timeframe: str,
    parent_snapshot_sha256: str,
) -> CanonicalAggregateBar:
    first = rows[0]
    last = rows[-1]
    timestamp = cast(datetime, first["timestamp"])
    return CanonicalAggregateBar(
        schema_version=1,
        timestamp=timestamp,
        bar_close=cast(datetime, last["timestamp"]) + timedelta(minutes=1),
        symbol=cast(str, first["symbol"]),
        source_timeframe=SOURCE_TIMEFRAME,
        target_timeframe=target_timeframe,
        segment_id=cast(int, first["segment_id"]),
        continuity=CONTINUITY_ID,
        open=float(cast(Any, first["open"])),
        high=max(float(cast(Any, row["high"])) for row in rows),
        low=min(float(cast(Any, row["low"])) for row in rows),
        close=float(cast(Any, last["close"])),
        volume=math.fsum(float(cast(Any, row["volume"])) for row in rows),
        source_row_count=len(rows),
        source_row_ids=source_row_ids,
        source_sha256=source_rows_sha256(source_row_ids, identities=True),
        parent_snapshot_sha256=parent_snapshot_sha256,
    )


def _validate_source_frame_schema(frame: pl.DataFrame) -> None:
    if frame.schema != _SOURCE_SCHEMA:
        raise ValueError("canonical aggregate source frame schema is invalid")


def _require_within_declared(stage: str, observed: int, declared: int, limit: int) -> None:
    maximum = min(declared, limit)
    if observed > maximum:
        raise ValidationWorkBudgetViolation(stage, observed, maximum)


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _require_utc(parsed, "aggregate timestamp")
    raise ValueError("aggregate timestamp is invalid")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "CONTINUITY_ID",
    "CanonicalAggregateBar",
    "OrderedSourceIdentity",
    "SUPPORTED_TARGET_TIMEFRAMES",
    "canonical_source_row_identity",
    "canonical_source_row_payload",
    "iter_complete_aggregate_bars",
    "source_rows_sha256",
    "target_timeframe_minutes",
]
