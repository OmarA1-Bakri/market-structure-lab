"""Compact SQL gap discovery and frozen recovery manifests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import Connection, text

MINUTE_MS = 60_000
MANIFEST_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProvenanceState(StrEnum):
    PENDING = "pending"
    COMPATIBLE = "compatible"
    SOURCE_CONFLICT = "source_conflict"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    dump_sha256: str
    source_row_count: int
    mapping_version: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.dump_sha256):
            raise ValueError("dump_sha256 must contain exactly 64 hexadecimal characters")
        if self.source_row_count < 0:
            raise ValueError("source_row_count cannot be negative")
        if not self.mapping_version:
            raise ValueError("mapping_version is required")


@dataclass(frozen=True, slots=True)
class ObservedEnvelope:
    symbol: str
    timeframe: str
    first_open_time_ms: int
    last_open_time_ms: int
    row_count: int


@dataclass(frozen=True, slots=True)
class GapRange:
    gap_id: str
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    expected_minutes: int

    @classmethod
    def create(
        cls, symbol: str, timeframe: str, start_ms: int, end_ms: int, expected_minutes: int
    ) -> GapRange:
        material = f"{symbol}\0{timeframe}\0{start_ms}\0{end_ms}".encode()
        return cls(
            gap_id=hashlib.sha256(material).hexdigest()[:24],
            symbol=symbol,
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            expected_minutes=expected_minutes,
        )

    def __post_init__(self) -> None:
        if self.timeframe != "1m":
            raise ValueError("only 1m gaps are recoverable")
        if self.start_ms % MINUTE_MS or self.end_ms % MINUTE_MS:
            raise ValueError("gap bounds must be minute aligned")
        calculated = (self.end_ms - self.start_ms) // MINUTE_MS
        if calculated <= 0 or calculated != self.expected_minutes:
            raise ValueError("gap expected_minutes must match its half-open bounds")


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    manifest_version: int
    source_identity: SourceIdentity
    as_of: str
    candidate_venue: str
    market_type: str
    envelopes: tuple[ObservedEnvelope, ...]
    gaps: tuple[GapRange, ...]
    provenance_validation: Mapping[str, ProvenanceState]

    def __post_init__(self) -> None:
        parsed = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("manifest as_of must be timezone-aware")
        if self.manifest_version != MANIFEST_VERSION:
            raise ValueError(f"unsupported recovery manifest version: {self.manifest_version}")

    def canonical_bytes(self) -> bytes:
        return (_canonical_json(self.to_dict()) + "\n").encode()

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "source_identity": asdict(self.source_identity),
            "as_of": self.as_of,
            "candidate_venue": self.candidate_venue,
            "market_type": self.market_type,
            "envelopes": [asdict(item) for item in self.envelopes],
            "gaps": [asdict(item) for item in self.gaps],
            "provenance_validation": {
                symbol: str(state)
                for symbol, state in sorted(self.provenance_validation.items())
            },
        }


def build_gap_query(
    *,
    schema: str | None = "market_data",
    table: str = "candles",
    symbol_column: str = "symbol",
    timeframe_column: str = "interval",
    timestamp_column: str = "open_time",
) -> str:
    """Return SQL that emits one row per internal gap, never one row per missing minute."""
    relation = _qualified_relation(schema, table)
    symbol = _quote_identifier(symbol_column)
    timeframe = _quote_identifier(timeframe_column)
    timestamp = _quote_identifier(timestamp_column)
    return f"""
WITH ordered AS (
    SELECT
        {symbol} AS symbol,
        {timeframe} AS timeframe,
        {timestamp} AS open_time,
        lead({timestamp}) OVER (
            PARTITION BY {symbol}, {timeframe}
            ORDER BY {timestamp}
        ) AS next_open_time
    FROM {relation}
)
SELECT
    symbol,
    timeframe,
    open_time + {MINUTE_MS} AS start_ms,
    next_open_time AS end_ms,
    ((next_open_time - open_time) / {MINUTE_MS}) - 1 AS expected_minutes
FROM ordered
WHERE timeframe = '1m'
  AND next_open_time > open_time + {MINUTE_MS}
ORDER BY symbol, timeframe, start_ms
""".strip()


def load_gap_ranges(
    connection: Connection,
    *,
    schema: str | None = "market_data",
    table: str = "candles",
    symbol_column: str = "symbol",
    timeframe_column: str = "interval",
    timestamp_column: str = "open_time",
) -> tuple[GapRange, ...]:
    rows = connection.execute(
        text(
            build_gap_query(
                schema=schema,
                table=table,
                symbol_column=symbol_column,
                timeframe_column=timeframe_column,
                timestamp_column=timestamp_column,
            )
        )
    ).mappings()
    return tuple(
        GapRange.create(
            str(row["symbol"]),
            str(row["timeframe"]),
            int(row["start_ms"]),
            int(row["end_ms"]),
            int(row["expected_minutes"]),
        )
        for row in rows
    )


def load_envelopes(
    connection: Connection,
    *,
    schema: str | None = "market_data",
    table: str = "candles",
    symbol_column: str = "symbol",
    timeframe_column: str = "interval",
    timestamp_column: str = "open_time",
) -> tuple[ObservedEnvelope, ...]:
    relation = _qualified_relation(schema, table)
    symbol = _quote_identifier(symbol_column)
    timeframe = _quote_identifier(timeframe_column)
    timestamp = _quote_identifier(timestamp_column)
    query = text(
        f"""
SELECT {symbol} AS symbol, {timeframe} AS timeframe,
       min({timestamp}) AS first_open_time_ms,
       max({timestamp}) AS last_open_time_ms, count(*) AS row_count
FROM {relation}
WHERE {timeframe} = '1m'
GROUP BY {symbol}, {timeframe}
ORDER BY {symbol}, {timeframe}
"""
    )
    return tuple(
        ObservedEnvelope(
            symbol=str(row.symbol),
            timeframe=str(row.timeframe),
            first_open_time_ms=int(row.first_open_time_ms),
            last_open_time_ms=int(row.last_open_time_ms),
            row_count=int(row.row_count),
        )
        for row in connection.execute(query)
    )


def build_manifest(
    connection: Connection,
    *,
    dump_sha256: str,
    mapping_version: str,
    as_of: datetime,
    candidate_venue: str = "binance",
    market_type: str = "spot",
    schema: str | None = "market_data",
    table: str = "candles",
    symbol_column: str = "symbol",
    timeframe_column: str = "interval",
    timestamp_column: str = "open_time",
) -> RecoveryManifest:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    relation = _qualified_relation(schema, table)
    row_count = int(connection.execute(text(f"SELECT count(*) FROM {relation}")).scalar_one())
    envelopes = load_envelopes(
        connection,
        schema=schema,
        table=table,
        symbol_column=symbol_column,
        timeframe_column=timeframe_column,
        timestamp_column=timestamp_column,
    )
    gaps = load_gap_ranges(
        connection,
        schema=schema,
        table=table,
        symbol_column=symbol_column,
        timeframe_column=timeframe_column,
        timestamp_column=timestamp_column,
    )
    states = {envelope.symbol: ProvenanceState.PENDING for envelope in envelopes}
    return RecoveryManifest(
        manifest_version=MANIFEST_VERSION,
        source_identity=SourceIdentity(dump_sha256.lower(), row_count, mapping_version),
        as_of=as_of.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        candidate_venue=candidate_venue,
        market_type=market_type,
        envelopes=envelopes,
        gaps=gaps,
        provenance_validation=states,
    )


def write_manifest(manifest: RecoveryManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(manifest.canonical_bytes())
    temporary.replace(path)


def read_manifest(path: Path) -> RecoveryManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RecoveryManifest(
        manifest_version=int(raw["manifest_version"]),
        source_identity=SourceIdentity(**raw["source_identity"]),
        as_of=str(raw["as_of"]),
        candidate_venue=str(raw["candidate_venue"]),
        market_type=str(raw["market_type"]),
        envelopes=tuple(ObservedEnvelope(**item) for item in raw["envelopes"]),
        gaps=tuple(GapRange(**item) for item in raw["gaps"]),
        provenance_validation={
            symbol: ProvenanceState(state)
            for symbol, state in raw["provenance_validation"].items()
        },
    )


def verify_manifest_identity(
    connection: Connection,
    manifest: RecoveryManifest,
    *,
    dump_sha256: str,
    mapping_version: str,
    schema: str | None = "market_data",
    table: str = "candles",
) -> None:
    relation = _qualified_relation(schema, table)
    actual_rows = int(connection.execute(text(f"SELECT count(*) FROM {relation}")).scalar_one())
    expected = manifest.source_identity
    if (
        expected.dump_sha256.lower() != dump_sha256.lower()
        or expected.source_row_count != actual_rows
        or expected.mapping_version != mapping_version
    ):
        raise ValueError("recovery manifest does not match the inspected database identity")


def with_provenance_states(
    manifest: RecoveryManifest, states: Mapping[str, ProvenanceState]
) -> RecoveryManifest:
    expected = {item.symbol for item in manifest.envelopes}
    if set(states) != expected:
        raise ValueError("provenance states must cover every manifest symbol exactly once")
    return RecoveryManifest(
        manifest_version=manifest.manifest_version,
        source_identity=manifest.source_identity,
        as_of=manifest.as_of,
        candidate_venue=manifest.candidate_venue,
        market_type=manifest.market_type,
        envelopes=manifest.envelopes,
        gaps=manifest.gaps,
        provenance_validation=dict(states),
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def _qualified_relation(schema: str | None, table: str) -> str:
    quoted_table = _quote_identifier(table)
    return f"{_quote_identifier(schema)}.{quoted_table}" if schema else quoted_table


def gap_boundaries(gaps: Sequence[GapRange]) -> tuple[int, ...]:
    return tuple(sorted({value for gap in gaps for value in (gap.start_ms, gap.end_ms)}))
