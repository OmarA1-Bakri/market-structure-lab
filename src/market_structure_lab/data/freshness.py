"""Frozen, deterministic planning for append-only daily candle freshness."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import Connection, text

from market_structure_lab.data.gaps import (
    GapRange,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)

FRESHNESS_MANIFEST_VERSION = 1
MINUTE_MS = 60_000
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class _ObservedCanonicalSeries:
    symbol: str
    timeframe: str
    first_open_time_ms: int | None
    last_open_time_ms: int | None
    row_count: int
    distinct_timestamp_count: int
    null_timestamp_count: int
    misaligned_timestamp_count: int
    internal_gaps: tuple[GapRange, ...]


class FreshnessPlanningStatus(StrEnum):
    """Terminal result of planning one canonical symbol."""

    UP_TO_DATE = "up_to_date"
    FETCH_REQUIRED = "fetch_required"
    PROVENANCE_PENDING = "provenance_pending"
    SOURCE_CONFLICT = "source_conflict"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class CanonicalSeriesState:
    """Observed state of one dump-preferred canonical series."""

    symbol: str
    timeframe: str
    first_open_time_ms: int
    last_open_time_ms: int
    row_count: int
    missing_minutes: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("canonical symbol must be non-empty")
        if self.timeframe != "1m":
            raise ValueError("daily freshness supports only 1m canonical series")
        if (
            self.first_open_time_ms < 0
            or self.first_open_time_ms % MINUTE_MS
            or self.last_open_time_ms % MINUTE_MS
        ):
            raise ValueError("canonical bounds must be non-negative and minute-aligned")
        if self.last_open_time_ms < self.first_open_time_ms:
            raise ValueError("canonical last timestamp cannot precede its first timestamp")
        if self.row_count < 1:
            raise ValueError("canonical series row_count must be positive")
        if self.missing_minutes < 0:
            raise ValueError("canonical missing_minutes cannot be negative")


@dataclass(frozen=True, slots=True)
class FreshnessSymbolPlan:
    """Frozen missing ranges and eligibility for one symbol."""

    symbol: str
    timeframe: str
    provenance_state: ProvenanceState
    status: FreshnessPlanningStatus
    reason: str
    canonical_state: CanonicalSeriesState
    missing_ranges: tuple[GapRange, ...]

    def __post_init__(self) -> None:
        if self.symbol != self.canonical_state.symbol:
            raise ValueError("freshness plan symbol does not match canonical state")
        if self.timeframe != self.canonical_state.timeframe:
            raise ValueError("freshness plan timeframe does not match canonical state")
        if not self.reason:
            raise ValueError("freshness plan requires an explicit terminal reason")
        ordered = tuple(
            sorted(
                self.missing_ranges,
                key=lambda gap: (gap.start_ms, gap.end_ms, gap.gap_id),
            )
        )
        if ordered != self.missing_ranges:
            raise ValueError("freshness missing ranges must be deterministically ordered")
        previous_end: int | None = None
        for gap in self.missing_ranges:
            if gap.symbol != self.symbol or gap.timeframe != self.timeframe:
                raise ValueError("freshness range crosses its symbol/timeframe boundary")
            expected = GapRange.create(
                gap.symbol,
                gap.timeframe,
                gap.start_ms,
                gap.end_ms,
                gap.expected_minutes,
            )
            if gap.gap_id != expected.gap_id:
                raise ValueError("freshness range gap_id does not match its bounds")
            if previous_end is not None and gap.start_ms < previous_end:
                raise ValueError("freshness ranges cannot overlap")
            previous_end = gap.end_ms
        missing_minutes = sum(gap.expected_minutes for gap in self.missing_ranges)
        if missing_minutes != self.canonical_state.missing_minutes:
            raise ValueError("canonical missing-minute total does not match frozen ranges")
        expected_status = _planning_status(self.provenance_state, missing_minutes)
        if self.status is not expected_status:
            raise ValueError("freshness planning status is inconsistent with provenance and gaps")

    @property
    def eligible_ranges(self) -> tuple[GapRange, ...]:
        """Return fetchable ranges only after compatibility was independently approved."""
        if self.provenance_state is ProvenanceState.COMPATIBLE:
            return self.missing_ranges
        return ()


@dataclass(frozen=True, slots=True)
class FreshnessManifest:
    """Immutable plan pinned to dump identity, canonical state, and cutoff."""

    manifest_version: int
    dump_identity: SourceIdentity
    compatibility_manifest_sha256: str
    canonical_row_count: int
    as_of: str
    candidate_venue: str
    market_type: str
    symbols: tuple[FreshnessSymbolPlan, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != FRESHNESS_MANIFEST_VERSION:
            raise ValueError(f"unsupported freshness manifest version: {self.manifest_version}")
        if (
            not _SHA256.fullmatch(self.compatibility_manifest_sha256)
            or self.compatibility_manifest_sha256 != self.compatibility_manifest_sha256.lower()
        ):
            raise ValueError(
                "compatibility_manifest_sha256 must be 64 lowercase hexadecimal characters"
            )
        cutoff = _validate_cutoff(datetime.fromisoformat(self.as_of.replace("Z", "+00:00")))
        if self.as_of != _format_utc(cutoff):
            raise ValueError("freshness manifest as_of must use canonical UTC formatting")
        if not self.candidate_venue or not self.market_type:
            raise ValueError("freshness source venue and market type are required")
        if not self.symbols:
            raise ValueError("freshness manifest requires at least one canonical symbol")
        ordered_symbols = tuple(sorted(self.symbols, key=lambda plan: plan.symbol))
        if ordered_symbols != self.symbols:
            raise ValueError("freshness symbol plans must be deterministically ordered")
        names = [plan.symbol for plan in self.symbols]
        if len(set(names)) != len(names):
            raise ValueError("freshness manifest contains duplicate symbol plans")
        observed_rows = sum(plan.canonical_state.row_count for plan in self.symbols)
        if self.canonical_row_count != observed_rows:
            raise ValueError("canonical_row_count does not match per-symbol canonical state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "dump_identity": asdict(self.dump_identity),
            "compatibility_manifest_sha256": self.compatibility_manifest_sha256,
            "canonical_row_count": self.canonical_row_count,
            "as_of": self.as_of,
            "candidate_venue": self.candidate_venue,
            "market_type": self.market_type,
            "symbols": [
                {
                    "symbol": plan.symbol,
                    "timeframe": plan.timeframe,
                    "provenance_state": plan.provenance_state.value,
                    "status": plan.status.value,
                    "reason": plan.reason,
                    "canonical_state": asdict(plan.canonical_state),
                    "missing_ranges": [asdict(gap) for gap in plan.missing_ranges],
                }
                for plan in self.symbols
            ],
        }

    def canonical_bytes(self) -> bytes:
        return (_canonical_json(self.to_dict()) + "\n").encode()

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def resolve_freshness_cutoff(
    *,
    now: datetime | None = None,
    as_of: datetime | None = None,
) -> datetime:
    """Resolve the half-open cutoff at the start of the current UTC minute."""
    if now is not None and as_of is not None:
        raise ValueError("provide either now or as_of, not both")
    if as_of is not None:
        return _validate_cutoff(as_of)
    current = datetime.now(UTC) if now is None else _require_aware(now, "now")
    current_utc = current.astimezone(UTC)
    return current_utc.replace(second=0, microsecond=0)


def build_freshness_manifest(
    connection: Connection,
    *,
    dump_identity: SourceIdentity,
    compatibility_manifest: RecoveryManifest,
    compatibility_manifest_sha256: str,
    as_of: datetime,
    candidate_venue: str = "binance",
    market_type: str = "spot",
    schema: str | None = "market_data",
    table: str = "candles_canonical",
    symbol_column: str = "symbol",
    timeframe_column: str = "interval",
    timestamp_column: str = "open_time",
    timeframe: str = "1m",
) -> FreshnessManifest:
    """Plan exact internal and tail gaps using compact SQL over the canonical view."""
    if timeframe != "1m":
        raise ValueError("daily freshness supports only 1m candles")
    reviewed_sha = _validate_compatibility_artifact(
        compatibility_manifest,
        expected_sha256=compatibility_manifest_sha256,
        dump_identity=dump_identity,
        candidate_venue=candidate_venue,
        market_type=market_type,
    )
    cutoff = _validate_cutoff(as_of)
    cutoff_ms = _datetime_to_milliseconds(cutoff)
    compatibility_symbols = {envelope.symbol for envelope in compatibility_manifest.envelopes}
    if _uses_default_postgres_canonical_key_stream(
        connection,
        schema=schema,
        table=table,
        symbol_column=symbol_column,
        timeframe_column=timeframe_column,
        timestamp_column=timestamp_column,
    ):
        observed_series, canonical_symbols = _inspect_default_postgres_canonical(
            connection,
            timeframe=timeframe,
            reviewed_symbols=compatibility_symbols,
        )
    else:
        observed_series, canonical_symbols = _inspect_generic_canonical(
            connection,
            schema=schema,
            table=table,
            symbol_column=symbol_column,
            timeframe_column=timeframe_column,
            timestamp_column=timestamp_column,
            timeframe=timeframe,
        )
    if not canonical_symbols:
        raise ValueError("no canonical 1m candle series were found")
    if compatibility_symbols != canonical_symbols:
        raise ValueError("compatibility evidence must cover canonical symbols exactly")

    plans: list[FreshnessSymbolPlan] = []
    for series in observed_series:
        name = series.symbol
        row_count = series.row_count
        if not name:
            raise ValueError("canonical symbols must be non-empty")
        if series.null_timestamp_count:
            raise ValueError(f"canonical series {name} contains null timestamps")
        if series.misaligned_timestamp_count:
            raise ValueError(f"canonical series {name} contains non-minute timestamps")
        if series.distinct_timestamp_count != row_count:
            raise ValueError(f"canonical series {name} contains duplicate candle keys")
        if series.first_open_time_ms is None or series.last_open_time_ms is None:
            raise ValueError(f"canonical series {name} contains no timestamped candles")
        first_ms = series.first_open_time_ms
        last_ms = series.last_open_time_ms
        if first_ms < 0 or first_ms % MINUTE_MS or last_ms % MINUTE_MS:
            raise ValueError(f"canonical series {name} has invalid timestamp bounds")
        if last_ms >= cutoff_ms:
            raise ValueError(f"canonical series {name} must end before the cutoff's open minute")
        missing_ranges = list(series.internal_gaps)
        tail_start = last_ms + MINUTE_MS
        if tail_start < cutoff_ms:
            missing_ranges.append(
                GapRange.create(
                    name,
                    timeframe,
                    tail_start,
                    cutoff_ms,
                    (cutoff_ms - tail_start) // MINUTE_MS,
                )
            )
        frozen_ranges = tuple(
            sorted(
                missing_ranges,
                key=lambda gap: (gap.start_ms, gap.end_ms, gap.gap_id),
            )
        )
        missing_minutes = sum(gap.expected_minutes for gap in frozen_ranges)
        state = compatibility_manifest.provenance_validation[name]
        status = _planning_status(state, missing_minutes)
        canonical_state = CanonicalSeriesState(
            symbol=name,
            timeframe=timeframe,
            first_open_time_ms=first_ms,
            last_open_time_ms=last_ms,
            row_count=row_count,
            missing_minutes=missing_minutes,
        )
        plans.append(
            FreshnessSymbolPlan(
                symbol=name,
                timeframe=timeframe,
                provenance_state=state,
                status=status,
                reason=_planning_reason(status, missing_minutes),
                canonical_state=canonical_state,
                missing_ranges=frozen_ranges,
            )
        )
    ordered_plans = tuple(sorted(plans, key=lambda plan: plan.symbol))
    return FreshnessManifest(
        manifest_version=FRESHNESS_MANIFEST_VERSION,
        dump_identity=dump_identity,
        compatibility_manifest_sha256=reviewed_sha,
        canonical_row_count=sum(plan.canonical_state.row_count for plan in ordered_plans),
        as_of=_format_utc(cutoff),
        candidate_venue=candidate_venue,
        market_type=market_type,
        symbols=ordered_plans,
    )


def _uses_default_postgres_canonical_key_stream(
    connection: Connection,
    *,
    schema: str | None,
    table: str,
    symbol_column: str,
    timeframe_column: str,
    timestamp_column: str,
) -> bool:
    return (
        connection.dialect.name == "postgresql"
        and schema == "market_data"
        and table == "candles_canonical"
        and symbol_column == "symbol"
        and timeframe_column == "interval"
        and timestamp_column == "open_time"
    )


def _inspect_default_postgres_canonical(
    connection: Connection,
    *,
    timeframe: str,
    reviewed_symbols: set[str],
) -> tuple[tuple[_ObservedCanonicalSeries, ...], set[str]]:
    summary_query = text(
        """
WITH canonical_key AS (
    SELECT candle.open_time
    FROM market_data.candles AS candle
    WHERE candle.symbol = :symbol
      AND candle."interval" = :timeframe
    UNION ALL
    SELECT supplement.open_time
    FROM market_data.candle_supplements AS supplement
    WHERE supplement.symbol = :symbol
      AND supplement."interval" = :timeframe
      AND supplement.validation_status = 'validated'
      AND NOT EXISTS (
          SELECT 1
          FROM market_data.candles AS candle
          WHERE candle.symbol = supplement.symbol
            AND candle."interval" = supplement."interval"
            AND candle.open_time = supplement.open_time
      )
)
SELECT
    count(*) AS row_count,
    min(open_time) AS first_open_time,
    max(open_time) AS last_open_time,
    sum(CASE WHEN open_time % 60000 = 0 THEN 0 ELSE 1 END) AS misaligned_rows
FROM canonical_key
"""
    )
    gap_query = text(
        """
WITH canonical_key AS (
    (
        SELECT candle.open_time
        FROM market_data.candles AS candle
        WHERE candle.symbol = :symbol
          AND candle."interval" = :timeframe
        ORDER BY candle.open_time
    )
    UNION ALL
    (
        SELECT supplement.open_time
        FROM market_data.candle_supplements AS supplement
        WHERE supplement.symbol = :symbol
          AND supplement."interval" = :timeframe
          AND supplement.validation_status = 'validated'
          AND NOT EXISTS (
              SELECT 1
              FROM market_data.candles AS candle
              WHERE candle.symbol = supplement.symbol
                AND candle."interval" = supplement."interval"
                AND candle.open_time = supplement.open_time
          )
        ORDER BY supplement.open_time
    )
),
ordered AS (
    SELECT
        open_time,
        lag(open_time) OVER (ORDER BY open_time) AS previous_open_time
    FROM canonical_key
)
SELECT open_time, previous_open_time
FROM ordered
WHERE open_time > previous_open_time + 60000
ORDER BY open_time
"""
    )
    observed: list[_ObservedCanonicalSeries] = []
    canonical_symbols: set[str] = set()
    for symbol in sorted(reviewed_symbols):
        parameters = {"symbol": symbol, "timeframe": timeframe}
        summary = connection.execute(summary_query, parameters).mappings().one()
        row_count = int(summary["row_count"])
        if not row_count:
            observed.append(
                _ObservedCanonicalSeries(
                    symbol=symbol,
                    timeframe=timeframe,
                    first_open_time_ms=None,
                    last_open_time_ms=None,
                    row_count=0,
                    distinct_timestamp_count=0,
                    null_timestamp_count=0,
                    misaligned_timestamp_count=0,
                    internal_gaps=(),
                )
            )
            continue
        gap_rows = connection.execute(gap_query, parameters).mappings()
        internal_gaps = tuple(
            GapRange.create(
                symbol,
                timeframe,
                int(row["previous_open_time"]) + MINUTE_MS,
                int(row["open_time"]),
                ((int(row["open_time"]) - int(row["previous_open_time"])) // MINUTE_MS) - 1,
            )
            for row in gap_rows
        )
        observed.append(
            _ObservedCanonicalSeries(
                symbol=symbol,
                timeframe=timeframe,
                first_open_time_ms=int(summary["first_open_time"]),
                last_open_time_ms=int(summary["last_open_time"]),
                row_count=row_count,
                distinct_timestamp_count=row_count,
                null_timestamp_count=0,
                misaligned_timestamp_count=int(summary["misaligned_rows"]),
                internal_gaps=internal_gaps,
            )
        )
        canonical_symbols.add(symbol)
    unreviewed_symbol = _find_unreviewed_postgres_symbol(
        connection,
        timeframe=timeframe,
        reviewed_symbols=reviewed_symbols,
    )
    if unreviewed_symbol is not None:
        canonical_symbols.add(unreviewed_symbol)
    return tuple(observed), canonical_symbols


def _inspect_generic_canonical(
    connection: Connection,
    *,
    schema: str | None,
    table: str,
    symbol_column: str,
    timeframe_column: str,
    timestamp_column: str,
    timeframe: str,
) -> tuple[tuple[_ObservedCanonicalSeries, ...], set[str]]:
    relation = _qualified_relation(schema, table)
    symbol = _quote_identifier(symbol_column)
    interval = _quote_identifier(timeframe_column)
    open_time = _quote_identifier(timestamp_column)
    series_rows = tuple(
        connection.execute(
            text(
                f"""
SELECT
    {symbol} AS symbol,
    {interval} AS timeframe,
    min({open_time}) AS first_open_time_ms,
    max({open_time}) AS last_open_time_ms,
    count(*) AS row_count,
    count(DISTINCT {open_time}) AS distinct_timestamp_count,
    count(*) - count({open_time}) AS null_timestamp_count,
    sum(CASE WHEN {open_time} % {MINUTE_MS} = 0 THEN 0 ELSE 1 END)
        AS misaligned_timestamp_count
FROM {relation}
WHERE {interval} = :timeframe
GROUP BY {symbol}, {interval}
ORDER BY {symbol}, {interval}
"""
            ),
            {"timeframe": timeframe},
        ).mappings()
    )
    gaps_by_symbol: dict[str, list[GapRange]] = {str(row["symbol"]): [] for row in series_rows}
    gap_rows = connection.execute(
        text(
            f"""
WITH ordered AS (
    SELECT
        {symbol} AS symbol,
        {interval} AS timeframe,
        {open_time} AS open_time,
        lead({open_time}) OVER (
            PARTITION BY {symbol}, {interval}
            ORDER BY {open_time}
        ) AS next_open_time
    FROM {relation}
    WHERE {interval} = :timeframe
)
SELECT
    symbol,
    timeframe,
    open_time + {MINUTE_MS} AS start_ms,
    next_open_time AS end_ms,
    ((next_open_time - open_time) / {MINUTE_MS}) - 1 AS expected_minutes
FROM ordered
WHERE next_open_time > open_time + {MINUTE_MS}
ORDER BY symbol, timeframe, start_ms
"""
        ),
        {"timeframe": timeframe},
    ).mappings()
    for row in gap_rows:
        name = str(row["symbol"])
        gaps_by_symbol[name].append(
            GapRange.create(
                name,
                str(row["timeframe"]),
                int(row["start_ms"]),
                int(row["end_ms"]),
                int(row["expected_minutes"]),
            )
        )
    observed = tuple(
        _ObservedCanonicalSeries(
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            first_open_time_ms=(
                None if row["first_open_time_ms"] is None else int(row["first_open_time_ms"])
            ),
            last_open_time_ms=(
                None if row["last_open_time_ms"] is None else int(row["last_open_time_ms"])
            ),
            row_count=int(row["row_count"]),
            distinct_timestamp_count=int(row["distinct_timestamp_count"]),
            null_timestamp_count=int(row["null_timestamp_count"]),
            misaligned_timestamp_count=int(row["misaligned_timestamp_count"]),
            internal_gaps=tuple(gaps_by_symbol[str(row["symbol"])]),
        )
        for row in series_rows
    )
    return observed, {series.symbol for series in observed}


def _find_unreviewed_postgres_symbol(
    connection: Connection,
    *,
    timeframe: str,
    reviewed_symbols: set[str],
) -> str | None:
    ordered_symbols = sorted(reviewed_symbols)
    bounds: list[tuple[str | None, str | None]] = []
    if ordered_symbols:
        bounds.append((None, ordered_symbols[0]))
        bounds.extend(zip(ordered_symbols, ordered_symbols[1:]))
        bounds.append((ordered_symbols[-1], None))
    else:
        bounds.append((None, None))

    relations = (
        ("market_data.candles", ""),
        (
            "market_data.candle_supplements",
            "AND validation_status = 'validated'",
        ),
    )
    for relation, validation_filter in relations:
        for lower, upper in bounds:
            predicates = ['"interval" = :timeframe']
            parameters: dict[str, str] = {"timeframe": timeframe}
            if lower is not None:
                predicates.append("symbol > :lower_symbol")
                parameters["lower_symbol"] = lower
            if upper is not None:
                predicates.append("symbol < :upper_symbol")
                parameters["upper_symbol"] = upper
            where_clause = "\n  AND ".join(predicates)
            extra = connection.execute(
                text(
                    f"""
SELECT symbol
FROM {relation}
WHERE {where_clause}
  {validation_filter}
ORDER BY symbol
LIMIT 1
"""
                ),
                parameters,
            ).scalar_one_or_none()
            if extra is not None:
                return str(extra)
    return None


def write_freshness_manifest(manifest: FreshnessManifest, path: Path) -> None:
    """Atomically write a checksum-bearing deterministic manifest envelope."""
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"manifest": manifest.to_dict(), "sha256": manifest.sha256()}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_canonical_json(envelope) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_freshness_manifest(path: Path) -> FreshnessManifest:
    """Read a manifest and reject any content that does not match its frozen hash."""
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("freshness manifest envelope must be a JSON object")
    manifest_data = envelope.get("manifest")
    expected_sha = envelope.get("sha256")
    if not isinstance(manifest_data, dict) or not isinstance(expected_sha, str):
        raise ValueError("freshness manifest envelope is missing manifest or checksum")
    actual_sha = hashlib.sha256((_canonical_json(manifest_data) + "\n").encode()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("freshness manifest checksum does not match its content")
    manifest = _manifest_from_dict(manifest_data)
    if manifest.sha256() != expected_sha:
        raise ValueError("freshness manifest checksum does not match its typed content")
    return manifest


def _manifest_from_dict(raw: Mapping[str, Any]) -> FreshnessManifest:
    plans: list[FreshnessSymbolPlan] = []
    for item in raw["symbols"]:
        canonical_state = CanonicalSeriesState(**item["canonical_state"])
        plans.append(
            FreshnessSymbolPlan(
                symbol=str(item["symbol"]),
                timeframe=str(item["timeframe"]),
                provenance_state=ProvenanceState(item["provenance_state"]),
                status=FreshnessPlanningStatus(item["status"]),
                reason=str(item["reason"]),
                canonical_state=canonical_state,
                missing_ranges=tuple(GapRange(**gap) for gap in item["missing_ranges"]),
            )
        )
    return FreshnessManifest(
        manifest_version=int(raw["manifest_version"]),
        dump_identity=SourceIdentity(**raw["dump_identity"]),
        compatibility_manifest_sha256=str(raw["compatibility_manifest_sha256"]),
        canonical_row_count=int(raw["canonical_row_count"]),
        as_of=str(raw["as_of"]),
        candidate_venue=str(raw["candidate_venue"]),
        market_type=str(raw["market_type"]),
        symbols=tuple(plans),
    )


def _planning_status(state: ProvenanceState, missing_minutes: int) -> FreshnessPlanningStatus:
    if state is ProvenanceState.PENDING:
        return FreshnessPlanningStatus.PROVENANCE_PENDING
    if state is ProvenanceState.SOURCE_CONFLICT:
        return FreshnessPlanningStatus.SOURCE_CONFLICT
    if state is ProvenanceState.SOURCE_UNAVAILABLE:
        return FreshnessPlanningStatus.SOURCE_UNAVAILABLE
    if state is not ProvenanceState.COMPATIBLE:
        raise ValueError(f"unsupported provenance state: {state!r}")
    if missing_minutes:
        return FreshnessPlanningStatus.FETCH_REQUIRED
    return FreshnessPlanningStatus.UP_TO_DATE


def _validate_compatibility_artifact(
    manifest: RecoveryManifest,
    *,
    expected_sha256: str,
    dump_identity: SourceIdentity,
    candidate_venue: str,
    market_type: str,
) -> str:
    if not isinstance(manifest, RecoveryManifest):
        raise TypeError("compatibility_manifest must be a RecoveryManifest")
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError("reviewed compatibility checksum must be a SHA-256 digest")
    normalized_sha = expected_sha256.lower()
    if manifest.sha256() != normalized_sha:
        raise ValueError(
            "compatibility artifact does not match the reviewed compatibility checksum"
        )
    if not _source_identities_match(manifest.source_identity, dump_identity):
        raise ValueError("compatibility artifact dump identity does not match freshness input")
    if manifest.candidate_venue != candidate_venue:
        raise ValueError("compatibility artifact candidate venue does not match freshness input")
    if manifest.market_type != market_type:
        raise ValueError("compatibility artifact market type does not match freshness input")

    envelope_symbols: list[str] = []
    for envelope in manifest.envelopes:
        if not envelope.symbol:
            raise ValueError("compatibility artifact contains an empty symbol")
        if envelope.timeframe != "1m":
            raise ValueError("compatibility artifact supports only 1m envelopes")
        if (
            envelope.first_open_time_ms < 0
            or envelope.first_open_time_ms % MINUTE_MS
            or envelope.last_open_time_ms % MINUTE_MS
            or envelope.last_open_time_ms < envelope.first_open_time_ms
            or envelope.row_count < 1
        ):
            raise ValueError("compatibility artifact contains an invalid observed envelope")
        envelope_symbols.append(envelope.symbol)
    if len(set(envelope_symbols)) != len(envelope_symbols):
        raise ValueError("compatibility artifact contains duplicate symbol envelopes")
    if set(manifest.provenance_validation) != set(envelope_symbols):
        raise ValueError("compatibility provenance must cover its symbol envelopes exactly")
    if not all(
        isinstance(state, ProvenanceState) for state in manifest.provenance_validation.values()
    ):
        raise ValueError("compatibility provenance contains an invalid state")
    for gap in manifest.gaps:
        if gap.symbol not in set(envelope_symbols) or gap.timeframe != "1m":
            raise ValueError("compatibility artifact gap crosses its reviewed symbol set")
    return normalized_sha


def _source_identities_match(left: SourceIdentity, right: SourceIdentity) -> bool:
    return (
        left.dump_sha256.lower() == right.dump_sha256.lower()
        and left.source_row_count == right.source_row_count
        and left.mapping_version == right.mapping_version
    )


def _planning_reason(status: FreshnessPlanningStatus, missing_minutes: int) -> str:
    if status is FreshnessPlanningStatus.UP_TO_DATE:
        return "canonical series contains every minute through the frozen cutoff"
    if status is FreshnessPlanningStatus.FETCH_REQUIRED:
        return f"canonical series is missing {missing_minutes} minute(s)"
    if status is FreshnessPlanningStatus.PROVENANCE_PENDING:
        return "source compatibility review is pending; no ranges are eligible"
    if status is FreshnessPlanningStatus.SOURCE_CONFLICT:
        return "source conflicts with dump observations; no ranges are eligible"
    return "authoritative source is unavailable; no ranges are eligible"


def _validate_cutoff(value: datetime) -> datetime:
    aware = _require_aware(value, "as_of").astimezone(UTC)
    if aware.second or aware.microsecond:
        raise ValueError("freshness as_of must be minute-aligned")
    return aware


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _datetime_to_milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def _qualified_relation(schema: str | None, table: str) -> str:
    quoted_table = _quote_identifier(table)
    return f"{_quote_identifier(schema)}.{quoted_table}" if schema else quoted_table
