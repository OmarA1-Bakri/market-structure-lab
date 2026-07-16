"""Outcome-blind, append-only candle recovery and publication."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, Engine, bindparam, text

from market_structure_lab.data.gaps import GapRange, ProvenanceState, RecoveryManifest
from market_structure_lab.data.sources.base import (
    FetchRequest,
    MarketDataSource,
    SourceError,
    SourceKline,
)

MINUTE_MS = 60_000
DEFAULT_RECOVERY_WORKERS = 8
DEFAULT_SERIES_LANES = 8
RECOVERY_ADVISORY_LOCK_NAME = "market_structure_lab.candle_recovery"


class RecoveryValidationError(ValueError):
    """A source batch cannot be admitted as real canonical observations."""


class GapResolution(StrEnum):
    RECOVERED = "recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    PROVIDER_ABSENT = "provider_absent"
    NON_TRADING = "non_trading"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_CONFLICT = "source_conflict"
    FETCH_FAILED = "fetch_failed"
    UNRESOLVED = "unresolved"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecoveryCandle:
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

    @classmethod
    def from_source(cls, row: SourceKline) -> RecoveryCandle:
        return cls(**{field: getattr(row, field) for field in cls.__dataclass_fields__})

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> RecoveryCandle:
        return cls(
            symbol=str(row["symbol"]),
            timeframe=str(row.get("timeframe", row.get("interval"))),
            open_time_ms=int(str(row.get("open_time_ms", row.get("open_time", 0)))),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
            quote_volume=(
                None if row.get("quote_volume") is None else Decimal(str(row["quote_volume"]))
            ),
            trades=None if row.get("trades") is None else int(str(row["trades"])),
        )

    def row_checksum(self) -> str:
        return hashlib.sha256(_canonical_row(self)).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidatedBatch:
    request: FetchRequest
    rows: tuple[RecoveryCandle, ...]
    logical_checksum: str


@dataclass(frozen=True, slots=True)
class CompatibilityEvidence:
    symbol: str
    existing_rows: int
    required_samples: int
    compared_samples: int
    compatible: bool
    limited_evidence: bool
    mismatches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishResult:
    inserted: int = 0
    idempotent: int = 0
    dump_preferred: int = 0
    conflicts: int = 0

    def __add__(self, other: PublishResult) -> PublishResult:
        return PublishResult(
            inserted=self.inserted + other.inserted,
            idempotent=self.idempotent + other.idempotent,
            dump_preferred=self.dump_preferred + other.dump_preferred,
            conflicts=self.conflicts + other.conflicts,
        )


@dataclass(frozen=True, slots=True)
class GapClassification:
    gap_id: str
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    resolution: GapResolution
    expected_minutes: int
    recovered_minutes: int
    reason: str


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: uuid.UUID
    manifest_sha256: str
    gaps_total: int
    gaps_completed: int
    inserted_rows: int
    logical_hash: str


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    """Durable position reconstructed from completed bounded source batches."""

    next_start_ms: int
    authoritative_empty: bool


def resume_fetch_request(gap: GapRange, checkpoint: RecoveryCheckpoint) -> FetchRequest | None:
    """Return only the unfinished suffix of a frozen gap."""
    if not gap.start_ms <= checkpoint.next_start_ms <= gap.end_ms:
        raise ValueError("recovery checkpoint lies outside its frozen gap")
    if checkpoint.next_start_ms == gap.end_ms:
        return None
    return FetchRequest(
        gap.symbol,
        gap.timeframe,
        checkpoint.next_start_ms,
        gap.end_ms,
    )


def validate_recovery_batch(
    rows: Iterable[SourceKline | RecoveryCandle], request: FetchRequest
) -> ValidatedBatch:
    admitted: list[RecoveryCandle] = []
    seen: set[int] = set()
    for source_row in rows:
        row = (
            RecoveryCandle.from_source(source_row)
            if isinstance(source_row, SourceKline)
            else source_row
        )
        if len(admitted) == 1_000:
            raise RecoveryValidationError("recovery batch exceeds the bounded 1000-row limit")
        if row.symbol != request.symbol or row.timeframe != request.timeframe:
            raise RecoveryValidationError(
                "source row crosses its requested symbol/timeframe boundary"
            )
        if row.open_time_ms % MINUTE_MS:
            raise RecoveryValidationError("source row is not aligned to the one-minute grid")
        if not request.start_ms <= row.open_time_ms < request.end_ms:
            raise RecoveryValidationError("source row is outside the requested half-open bounds")
        if row.open_time_ms in seen:
            raise RecoveryValidationError("source response contains a duplicate candle key")
        _validate_values(row)
        seen.add(row.open_time_ms)
        admitted.append(row)
    admitted.sort(key=lambda item: item.open_time_ms)
    digest = hashlib.sha256()
    for row in admitted:
        digest.update(_canonical_row(row))
    return ValidatedBatch(request, tuple(admitted), digest.hexdigest())


def distributed_sample_indices(total_rows: int, target: int = 100) -> tuple[int, ...]:
    """Zero-based beginning/middle/end coverage without random sampling."""
    if total_rows < 0 or target < 1:
        raise ValueError("sample sizes must be non-negative with a positive target")
    count = min(total_rows, target)
    if count == 0:
        return ()
    if count == 1:
        return (0,)
    return tuple((index * (total_rows - 1)) // (count - 1) for index in range(count))


def compare_source_compatibility(
    dump_rows: Sequence[RecoveryCandle],
    source_rows: Sequence[SourceKline | RecoveryCandle],
    *,
    existing_rows: int | None = None,
) -> CompatibilityEvidence:
    total = len(dump_rows) if existing_rows is None else existing_rows
    required = min(100, total)
    source_by_time = {
        row.open_time_ms: RecoveryCandle.from_source(row) if isinstance(row, SourceKline) else row
        for row in source_rows
    }
    mismatches: list[str] = []
    compared = 0
    for dump in dump_rows:
        source = source_by_time.get(dump.open_time_ms)
        if source is None:
            mismatches.append(f"{dump.open_time_ms}:missing_source_row")
            continue
        compared += 1
        differing = _differing_fields(dump, source)
        if differing:
            mismatches.append(f"{dump.open_time_ms}:{','.join(differing)}")
    if len(dump_rows) < required:
        mismatches.append(f"insufficient_dump_samples:{len(dump_rows)}/{required}")
    if compared < required:
        mismatches.append(f"insufficient_source_samples:{compared}/{required}")
    return CompatibilityEvidence(
        symbol=dump_rows[0].symbol if dump_rows else "",
        existing_rows=total,
        required_samples=required,
        compared_samples=compared,
        compatible=required > 0 and not mismatches,
        limited_evidence=0 < total <= 4,
        mismatches=tuple(mismatches),
    )


def coalesce_requests(
    requests: Iterable[FetchRequest], *, maximum_minutes: int = 43_200
) -> tuple[FetchRequest, ...]:
    """Coalesce adjacent gaps but retain bounded ranges and market boundaries."""
    if maximum_minutes < 1:
        raise ValueError("maximum_minutes must be positive")
    ordered = sorted(requests, key=lambda item: (item.symbol, item.timeframe, item.start_ms))
    result: list[FetchRequest] = []
    maximum_span = maximum_minutes * MINUTE_MS
    for item in ordered:
        if not result:
            result.append(item)
            continue
        prior = result[-1]
        same_market = prior.symbol == item.symbol and prior.timeframe == item.timeframe
        merged_end = max(prior.end_ms, item.end_ms)
        can_merge = (
            same_market
            and item.start_ms <= prior.end_ms
            and merged_end - prior.start_ms <= maximum_span
        )
        if can_merge:
            result[-1] = FetchRequest(prior.symbol, prior.timeframe, prior.start_ms, merged_end)
        else:
            result.append(item)
    return tuple(result)


def group_gaps_by_series(
    gaps: Iterable[GapRange], *, lanes_per_series: int = DEFAULT_SERIES_LANES
) -> tuple[tuple[GapRange, ...], ...]:
    """Shard each series into deterministic non-overlapping sequential lanes."""
    if lanes_per_series < 1:
        raise ValueError("lanes_per_series must be positive")
    grouped: dict[tuple[str, str], list[GapRange]] = {}
    for gap in gaps:
        grouped.setdefault((gap.symbol, gap.timeframe), []).append(gap)
    lanes: list[tuple[GapRange, ...]] = []
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda item: (item.start_ms, item.end_ms, item.gap_id))
        lane_count = min(lanes_per_series, len(ordered))
        series_lanes: list[list[GapRange]] = [[] for _ in range(lane_count)]
        for index, gap in enumerate(ordered):
            series_lanes[index % lane_count].append(gap)
        lanes.extend(tuple(lane) for lane in series_lanes)
    return tuple(lanes)


def classify_gap(
    gap: GapRange,
    *,
    recovered_minutes: int,
    authoritative_empty: bool = False,
    unavailable: bool = False,
    conflict: bool = False,
    validation_error: str | None = None,
    retrieval_error: str | None = None,
) -> GapClassification:
    if conflict:
        resolution, reason = GapResolution.SOURCE_CONFLICT, "source disagrees with dump samples"
    elif validation_error is not None:
        resolution, reason = GapResolution.UNRESOLVED, validation_error
    elif retrieval_error is not None:
        resolution, reason = GapResolution.FETCH_FAILED, retrieval_error
    elif unavailable:
        resolution, reason = GapResolution.SOURCE_UNAVAILABLE, "authoritative source unavailable"
    elif recovered_minutes == gap.expected_minutes:
        resolution, reason = GapResolution.RECOVERED, "all missing minutes recovered"
    elif recovered_minutes == 0 and authoritative_empty:
        resolution, reason = (
            GapResolution.PROVIDER_ABSENT,
            "authoritative provider returned no candle for the range",
        )
    else:
        resolution, reason = (
            GapResolution.PARTIALLY_RECOVERED,
            "authoritative source did not contain every missing minute",
        )
    return GapClassification(
        gap.gap_id,
        gap.symbol,
        gap.timeframe,
        gap.start_ms,
        gap.end_ms,
        resolution,
        gap.expected_minutes,
        recovered_minutes,
        reason,
    )


class RecoveryRepository:
    """Bounded PostgreSQL writes; the restored source table is never mutated."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create_run(
        self, run_id: uuid.UUID, manifest: RecoveryManifest, manifest_sha256: str
    ) -> None:
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_recovery_runs (
    run_id, manifest_sha256, dump_sha256, source_row_count, mapping_version,
    recovery_as_of, status
) VALUES (
    :run_id, :manifest_sha256, :dump_sha256, :source_row_count, :mapping_version,
    :recovery_as_of, 'running'
)
ON CONFLICT (manifest_sha256) DO NOTHING
"""
            ),
            {
                "run_id": run_id,
                "manifest_sha256": manifest_sha256,
                "dump_sha256": manifest.source_identity.dump_sha256,
                "source_row_count": manifest.source_identity.source_row_count,
                "mapping_version": manifest.source_identity.mapping_version,
                "recovery_as_of": manifest.as_of,
            },
        )

    def completed_summary(self, manifest_sha256: str, gaps_total: int) -> RunSummary | None:
        row = self.connection.execute(
            text(
                """
SELECT run_id, logical_hash
FROM market_data.candle_recovery_runs
WHERE manifest_sha256=:manifest_sha256 AND status='completed'
"""
            ),
            {"manifest_sha256": manifest_sha256},
        ).one_or_none()
        if row is None:
            return None
        run_id = row.run_id if isinstance(row.run_id, uuid.UUID) else uuid.UUID(str(row.run_id))
        return RunSummary(run_id, manifest_sha256, gaps_total, gaps_total, 0, str(row.logical_hash))

    def run_id_for_manifest(self, manifest_sha256: str) -> uuid.UUID:
        value = self.connection.execute(
            text(
                "SELECT run_id FROM market_data.candle_recovery_runs "
                "WHERE manifest_sha256 = :manifest_sha256"
            ),
            {"manifest_sha256": manifest_sha256},
        ).scalar_one()
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))

    def has_resolution(self, run_id: uuid.UUID, gap_id: str) -> bool:
        return bool(
            self.connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM market_data.candle_gap_resolutions "
                    "WHERE run_id = :run_id AND gap_id = :gap_id)"
                ),
                {"run_id": run_id, "gap_id": gap_id},
            ).scalar_one()
        )

    def count_validated_supplements(self, gap: GapRange) -> int:
        """Count admitted observations for one gap without retaining their keys in memory."""
        return int(
            self.connection.execute(
                text(
                    "SELECT count(*) FROM market_data.candle_supplements "
                    'WHERE symbol=:symbol AND "interval"=:timeframe '
                    "AND open_time>=:start_ms AND open_time<:end_ms "
                    "AND validation_status='validated'"
                ),
                {
                    "symbol": gap.symbol,
                    "timeframe": gap.timeframe,
                    "start_ms": gap.start_ms,
                    "end_ms": gap.end_ms,
                },
            ).scalar_one()
        )

    def completed_checkpoint(self, run_id: uuid.UUID, gap: GapRange) -> RecoveryCheckpoint:
        """Reconstruct the next cursor without retaining completed batch keys."""
        row = self.connection.execute(
            text(
                """
SELECT max(batch_end) AS next_start_ms,
       coalesce(bool_or(row_count = 0), false) AS authoritative_empty
FROM market_data.candle_recovery_batches
WHERE run_id=:run_id AND gap_id=:gap_id AND status='completed'
"""
            ),
            {"run_id": run_id, "gap_id": gap.gap_id},
        ).one()
        next_start_ms = gap.start_ms if row.next_start_ms is None else int(row.next_start_ms)
        return RecoveryCheckpoint(
            next_start_ms=next_start_ms,
            authoritative_empty=bool(row.authoritative_empty),
        )

    def publish_batch(
        self,
        *,
        run_id: uuid.UUID,
        rows: Sequence[RecoveryCandle],
        source_name: str,
        source_revision: str,
        payload_checksum: str,
        retrieved_at: str,
    ) -> PublishResult:
        if not rows:
            return PublishResult()
        payload = json.dumps(
            [
                {
                    "symbol": row.symbol,
                    "interval": row.timeframe,
                    "open_time": row.open_time_ms,
                    "open": str(row.open),
                    "high": str(row.high),
                    "low": str(row.low),
                    "close": str(row.close),
                    "volume": str(row.volume),
                    "quote_volume": None if row.quote_volume is None else str(row.quote_volume),
                    "trades": row.trades,
                    "row_checksum": row.row_checksum(),
                }
                for row in rows
            ],
            separators=(",", ":"),
        )
        counts = self.connection.execute(
            text(
                """
WITH incoming AS (
    SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS value(
        symbol text, "interval" text, open_time bigint,
        open numeric, high numeric, low numeric, close numeric, volume numeric,
        quote_volume numeric, trades integer, row_checksum character(64)
    )
), classified AS (
    SELECT incoming.*,
           EXISTS (
               SELECT 1 FROM market_data.candles AS dump
               WHERE dump.symbol=incoming.symbol
                 AND dump."interval"=incoming."interval"
                 AND dump.open_time=incoming.open_time
           ) AS dump_exists,
           (
               SELECT existing.row_checksum
               FROM market_data.candle_supplements AS existing
               WHERE existing.symbol=incoming.symbol
                 AND existing."interval"=incoming."interval"
                 AND existing.open_time=incoming.open_time
                 AND existing.validation_status='validated'
               ORDER BY existing.supplement_id LIMIT 1
           ) AS existing_checksum
    FROM incoming
), inserted AS (
    INSERT INTO market_data.candle_supplements (
        run_id, source_name, source_revision, symbol, "interval", open_time,
        open, high, low, close, volume, quote_volume, trades, retrieved_at,
        payload_checksum, row_checksum, validation_status
    )
    SELECT :run_id, :source_name, :source_revision, symbol, "interval", open_time,
           open, high, low, close, volume, quote_volume, trades, :retrieved_at,
           :payload_checksum, row_checksum, 'validated'
    FROM classified
    WHERE NOT dump_exists AND existing_checksum IS NULL
    ON CONFLICT DO NOTHING
    RETURNING supplement_id
)
SELECT
    (SELECT count(*) FROM inserted) AS inserted,
    count(*) FILTER (
        WHERE NOT dump_exists AND existing_checksum = row_checksum
    ) AS idempotent,
    count(*) FILTER (WHERE dump_exists) AS dump_preferred,
    count(*) FILTER (
        WHERE NOT dump_exists AND existing_checksum IS NOT NULL
          AND existing_checksum <> row_checksum
    ) AS conflicts
FROM classified
"""
            ),
            {
                "rows": payload,
                "run_id": run_id,
                "source_name": source_name,
                "source_revision": source_revision,
                "retrieved_at": retrieved_at,
                "payload_checksum": payload_checksum,
            },
        ).one()
        return PublishResult(
            inserted=int(counts.inserted),
            idempotent=int(counts.idempotent),
            dump_preferred=int(counts.dump_preferred),
            conflicts=int(counts.conflicts),
        )

    def record_resolution(
        self, run_id: uuid.UUID, classification: GapClassification, source_name: str
    ) -> None:
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_gap_resolutions (
    run_id, gap_id, symbol, "interval", gap_start, gap_end,
    resolution, expected_minutes, recovered_minutes, reason, source_name
) VALUES (
    :run_id, :gap_id, :symbol, :interval, :gap_start, :gap_end,
    :resolution, :expected_minutes, :recovered_minutes, :reason, :source_name
)
ON CONFLICT (run_id, gap_id) DO NOTHING
"""
            ),
            {
                "run_id": run_id,
                "gap_id": classification.gap_id,
                "symbol": classification.symbol,
                "interval": classification.timeframe,
                "gap_start": classification.start_ms,
                "gap_end": classification.end_ms,
                "resolution": str(classification.resolution),
                "expected_minutes": classification.expected_minutes,
                "recovered_minutes": classification.recovered_minutes,
                "reason": classification.reason,
                "source_name": source_name,
            },
        )

    def record_batch(
        self,
        *,
        run_id: uuid.UUID,
        gap_id: str,
        request: FetchRequest,
        payload_checksum: str | None,
        row_count: int,
        status: str,
        failure_detail: str | None = None,
    ) -> None:
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_recovery_batches (
    run_id, gap_id, batch_start, batch_end, payload_checksum,
    row_count, status, failure_detail
) VALUES (
    :run_id, :gap_id, :batch_start, :batch_end, :payload_checksum,
    :row_count, :status, :failure_detail
)
ON CONFLICT (run_id, gap_id, batch_start, batch_end, payload_checksum) DO NOTHING
"""
            ),
            {
                "run_id": run_id,
                "gap_id": gap_id,
                "batch_start": request.start_ms,
                "batch_end": request.end_ms,
                "payload_checksum": payload_checksum,
                "row_count": row_count,
                "status": status,
                "failure_detail": failure_detail,
            },
        )

    def update_cursor(self, run_id: uuid.UUID, gap_id: str) -> None:
        self.connection.execute(
            text(
                "UPDATE market_data.candle_recovery_runs SET cursor_gap_id=:gap_id "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run_id, "gap_id": gap_id},
        )

    def complete_run(self, run_id: uuid.UUID, logical_hash: str) -> None:
        self.connection.execute(
            text(
                "UPDATE market_data.candle_recovery_runs SET status='completed', "
                "logical_hash=:logical_hash, completed_at=now() WHERE run_id=:run_id"
            ),
            {"run_id": run_id, "logical_hash": logical_hash},
        )

    def logical_supplement_hash(self) -> str:
        digest = hashlib.sha256()
        result = self.connection.execute(
            text(
                """
SELECT symbol, "interval", open_time, open, high, low, close, volume,
       quote_volume, trades, row_checksum
FROM market_data.candle_supplements
WHERE validation_status = 'validated'
ORDER BY symbol, "interval", open_time, row_checksum
"""
            ).execution_options(stream_results=True, yield_per=1_000)
        )
        for row in result:
            digest.update(
                ("\0".join("" if value is None else str(value) for value in row) + "\n").encode()
            )
        return digest.hexdigest()


def run_recovery(
    engine: Engine,
    manifest: RecoveryManifest,
    source: MarketDataSource,
    *,
    max_workers: int = DEFAULT_RECOVERY_WORKERS,
) -> RunSummary:
    """Resume a frozen manifest in bounded per-series lanes and classify every gap."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    with engine.connect() as lock_connection:
        acquired = bool(
            lock_connection.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
            ).scalar_one()
        )
        if not acquired:
            raise RecoveryValidationError(
                "another candle recovery run holds the global publication lock"
            )
        try:
            return _run_recovery_locked(
                engine,
                manifest,
                source,
                max_workers=max_workers,
            )
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_name, 0))"),
                {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
            )


def _run_recovery_locked(
    engine: Engine,
    manifest: RecoveryManifest,
    source: MarketDataSource,
    *,
    max_workers: int,
) -> RunSummary:
    manifest_sha = manifest.sha256()
    with engine.begin() as connection:
        repository = RecoveryRepository(connection)
        completed_summary = repository.completed_summary(manifest_sha, len(manifest.gaps))
        if completed_summary is not None:
            return completed_summary
        repository.create_run(uuid.uuid4(), manifest, manifest_sha)
        run_id = repository.run_id_for_manifest(manifest_sha)
    groups = group_gaps_by_series(manifest.gaps)
    workers = max(1, min(max_workers, len(groups)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="candle-recovery") as executor:
        outcomes = tuple(
            executor.map(
                lambda gaps: _recover_series(engine, run_id, gaps, manifest, source),
                groups,
            )
        )
    inserted = sum(outcome[0] for outcome in outcomes)
    completed = sum(outcome[1] for outcome in outcomes)
    with engine.begin() as connection:
        repository = RecoveryRepository(connection)
        logical_hash = repository.logical_supplement_hash()
        repository.complete_run(run_id, logical_hash)
    return RunSummary(run_id, manifest_sha, len(manifest.gaps), completed, inserted, logical_hash)


def _recover_series(
    engine: Engine,
    run_id: uuid.UUID,
    gaps: Sequence[GapRange],
    manifest: RecoveryManifest,
    source: MarketDataSource,
) -> tuple[int, int]:
    """Recover one symbol/timeframe lane sequentially for cache and rate-limit safety."""
    inserted = 0
    completed = 0
    for gap in gaps:
        with engine.begin() as connection:
            already_complete = RecoveryRepository(connection).has_resolution(run_id, gap.gap_id)
        if already_complete:
            completed += 1
            continue
        state = manifest.provenance_validation.get(gap.symbol, ProvenanceState.PENDING)
        if state != ProvenanceState.COMPATIBLE:
            classification = classify_gap(
                gap,
                recovered_minutes=0,
                conflict=state == ProvenanceState.SOURCE_CONFLICT,
                unavailable=state == ProvenanceState.SOURCE_UNAVAILABLE,
                validation_error=(
                    "source compatibility has not been validated"
                    if state == ProvenanceState.PENDING
                    else None
                ),
            )
        else:
            classification, published = _recover_gap(engine, run_id, gap, source)
            inserted += published.inserted
        with engine.begin() as connection:
            checkpoint = RecoveryRepository(connection)
            checkpoint.record_resolution(run_id, classification, source.name)
            checkpoint.update_cursor(run_id, gap.gap_id)
        completed += 1
    return inserted, completed


def _recover_gap(
    engine: Engine,
    run_id: uuid.UUID,
    gap: GapRange,
    source: MarketDataSource,
) -> tuple[GapClassification, PublishResult]:
    published = PublishResult()
    with engine.begin() as connection:
        checkpoint = RecoveryRepository(connection).completed_checkpoint(run_id, gap)
    request = resume_fetch_request(gap, checkpoint)
    authoritative_empty = checkpoint.authoritative_empty

    def recovered_count() -> int:
        with engine.begin() as connection:
            return RecoveryRepository(connection).count_validated_supplements(gap)

    if request is None:
        return (
            classify_gap(
                gap,
                recovered_minutes=recovered_count(),
                authoritative_empty=authoritative_empty,
            ),
            published,
        )

    try:
        for batch in source.fetch(request):
            validated = validate_recovery_batch(batch.rows, batch.request)
            authoritative_empty = authoritative_empty or batch.authoritative_empty
            with engine.begin() as connection:
                repository = RecoveryRepository(connection)
                result = repository.publish_batch(
                    run_id=run_id,
                    rows=validated.rows,
                    source_name=batch.provenance.source_name,
                    source_revision=batch.provenance.source_revision,
                    payload_checksum=batch.provenance.payload_checksum,
                    retrieved_at=batch.provenance.retrieved_at,
                )
                repository.record_batch(
                    run_id=run_id,
                    gap_id=gap.gap_id,
                    request=batch.request,
                    payload_checksum=batch.provenance.payload_checksum,
                    row_count=len(validated.rows),
                    status="completed",
                )
            published += result
            if result.conflicts:
                return classify_gap(
                    gap, recovered_minutes=recovered_count(), conflict=True
                ), published
    except RecoveryValidationError as error:
        with engine.begin() as connection:
            RecoveryRepository(connection).record_batch(
                run_id=run_id,
                gap_id=gap.gap_id,
                request=request,
                payload_checksum=None,
                row_count=0,
                status="failed",
                failure_detail=str(error),
            )
        return (
            classify_gap(gap, recovered_minutes=recovered_count(), validation_error=str(error)),
            published,
        )
    except SourceError as error:
        with engine.begin() as connection:
            RecoveryRepository(connection).record_batch(
                run_id=run_id,
                gap_id=gap.gap_id,
                request=request,
                payload_checksum=None,
                row_count=0,
                status="failed",
                failure_detail=str(error),
            )
        return (
            classify_gap(gap, recovered_minutes=recovered_count(), retrieval_error=str(error)),
            published,
        )
    recovered_minutes = recovered_count()
    return (
        classify_gap(
            gap,
            recovered_minutes=recovered_minutes,
            authoritative_empty=authoritative_empty,
        ),
        published,
    )


def load_distributed_dump_samples(
    connection: Connection, *, symbol: str, timeframe: str, total_rows: int
) -> tuple[RecoveryCandle, ...]:
    positions = tuple(index + 1 for index in distributed_sample_indices(total_rows))
    if not positions:
        return ()
    query = text(
        """
WITH ranked AS (
    SELECT symbol, "interval", open_time, open, high, low, close, volume,
           quote_volume, trades,
           row_number() OVER (ORDER BY open_time) AS position
    FROM market_data.candles
    WHERE symbol=:symbol AND "interval"=:timeframe
)
SELECT symbol, "interval", open_time, open, high, low, close, volume, quote_volume, trades
FROM ranked WHERE position IN :positions ORDER BY open_time
"""
    ).bindparams(bindparam("positions", expanding=True))
    rows = connection.execute(
        query, {"symbol": symbol, "timeframe": timeframe, "positions": positions}
    ).mappings()
    return tuple(RecoveryCandle.from_mapping(dict(row)) for row in rows)


def load_compatibility_dump_samples(
    connection: Connection,
    *,
    symbol: str,
    timeframe: str,
    total_rows: int,
    gaps: Sequence[GapRange],
    major_gap_count: int = 10,
) -> tuple[RecoveryCandle, ...]:
    """Load distributed samples plus existing candles bordering the largest gaps."""
    distributed = load_distributed_dump_samples(
        connection, symbol=symbol, timeframe=timeframe, total_rows=total_rows
    )
    symbol_gaps = sorted(
        (gap for gap in gaps if gap.symbol == symbol and gap.timeframe == timeframe),
        key=lambda item: (-item.expected_minutes, item.start_ms),
    )[:major_gap_count]
    boundary_times = tuple(
        sorted({time for gap in symbol_gaps for time in (gap.start_ms - MINUTE_MS, gap.end_ms)})
    )
    if not boundary_times:
        return distributed
    query = text(
        """
SELECT symbol, "interval", open_time, open, high, low, close, volume, quote_volume, trades
FROM market_data.candles
WHERE symbol=:symbol AND "interval"=:timeframe AND open_time IN :open_times
ORDER BY open_time
"""
    ).bindparams(bindparam("open_times", expanding=True))
    boundary = tuple(
        RecoveryCandle.from_mapping(dict(row))
        for row in connection.execute(
            query,
            {"symbol": symbol, "timeframe": timeframe, "open_times": boundary_times},
        ).mappings()
    )
    return tuple(
        sorted(
            {row.open_time_ms: row for row in (*distributed, *boundary)}.values(),
            key=lambda row: row.open_time_ms,
        )
    )


def fetch_source_samples(
    source: MarketDataSource, dump_rows: Sequence[RecoveryCandle]
) -> tuple[SourceKline, ...]:
    """Retrieve exact one-minute overlap observations for compatibility evidence."""
    result: list[SourceKline] = []
    for dump in dump_rows:
        request = FetchRequest(
            dump.symbol,
            dump.timeframe,
            dump.open_time_ms,
            dump.open_time_ms + MINUTE_MS,
        )
        for batch in source.fetch(request):
            validated = validate_recovery_batch(batch.rows, batch.request)
            result.extend(
                SourceKline(
                    symbol=row.symbol,
                    timeframe=row.timeframe,
                    open_time_ms=row.open_time_ms,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    quote_volume=row.quote_volume,
                    trades=row.trades,
                )
                for row in validated.rows
            )
    return tuple(result)


def build_recovery_report(connection: Connection, manifest: RecoveryManifest) -> dict[str, object]:
    """Build deterministic before/after coverage evidence for one frozen manifest."""
    source_row_count = int(
        connection.execute(text("SELECT count(*) FROM market_data.candles")).scalar_one()
    )
    if source_row_count != manifest.source_identity.source_row_count:
        raise ValueError("source row count changed after recovery")
    manifest_sha = manifest.sha256()
    run = (
        connection.execute(
            text(
                """
SELECT run_id, status, logical_hash
FROM market_data.candle_recovery_runs
WHERE manifest_sha256=:manifest_sha
"""
            ),
            {"manifest_sha": manifest_sha},
        )
        .mappings()
        .one_or_none()
    )
    if run is None:
        raise ValueError("no recovery run exists for the frozen manifest")
    resolutions = tuple(
        dict(row)
        for row in connection.execute(
            text(
                """
SELECT symbol, "interval" AS timeframe, gap_id, resolution,
       expected_minutes, recovered_minutes, reason
FROM market_data.candle_gap_resolutions
WHERE run_id=:run_id
ORDER BY symbol, "interval", gap_start, gap_id
"""
            ),
            {"run_id": run["run_id"]},
        ).mappings()
    )
    verify_resolution_coverage(manifest, resolutions)
    recovered_by_symbol: dict[str, int] = {}
    for resolution in resolutions:
        symbol = str(resolution["symbol"])
        recovered_by_symbol[symbol] = recovered_by_symbol.get(symbol, 0) + int(
            str(resolution["recovered_minutes"])
        )
    payload_checksums = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT DISTINCT payload_checksum FROM market_data.candle_supplements "
                "WHERE run_id=:run_id ORDER BY payload_checksum"
            ),
            {"run_id": run["run_id"]},
        ).scalars()
    )
    batch_error_count = int(
        connection.execute(
            text(
                "SELECT count(*) FROM market_data.candle_recovery_batches "
                "WHERE run_id=:run_id AND status='failed'"
            ),
            {"run_id": run["run_id"]},
        ).scalar_one()
    )
    report = summarize_recovery_evidence(
        manifest,
        source_row_count=source_row_count,
        recovered_by_symbol=recovered_by_symbol,
        resolutions=resolutions,
        payload_checksums=payload_checksums,
        batch_error_count=batch_error_count,
    )
    report["run"] = {
        "run_id": str(run["run_id"]),
        "status": str(run["status"]),
        "logical_hash": None if run["logical_hash"] is None else str(run["logical_hash"]),
    }
    return report


def verify_resolution_coverage(
    manifest: RecoveryManifest,
    resolutions: Sequence[Mapping[str, object]],
) -> None:
    """Require one matching terminal ledger row for every frozen manifest gap."""
    expected = {gap.gap_id: gap for gap in manifest.gaps}
    actual_ids = [str(row["gap_id"]) for row in resolutions]
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("recovery ledger contains duplicate terminal gap resolutions")
    missing = sorted(set(expected).difference(actual_ids))
    unexpected = sorted(set(actual_ids).difference(expected))
    if missing:
        raise ValueError(f"missing terminal gap resolutions: {len(missing)}")
    if unexpected:
        raise ValueError(f"recovery ledger contains unexpected gap resolutions: {len(unexpected)}")
    for row in resolutions:
        gap = expected[str(row["gap_id"])]
        matches = (
            str(row["symbol"]) == gap.symbol
            and str(row["timeframe"]) == gap.timeframe
            and int(str(row["expected_minutes"])) == gap.expected_minutes
            and 0 <= int(str(row["recovered_minutes"])) <= gap.expected_minutes
        )
        if not matches:
            raise ValueError(f"gap resolution does not match the frozen manifest: {gap.gap_id}")


def summarize_recovery_evidence(
    manifest: RecoveryManifest,
    *,
    source_row_count: int,
    recovered_by_symbol: Mapping[str, int],
    resolutions: Sequence[Mapping[str, object]],
    payload_checksums: Sequence[str],
    batch_error_count: int,
) -> dict[str, object]:
    """Pure coverage accounting with a hard missing-minute conservation check."""
    if source_row_count != manifest.source_identity.source_row_count:
        raise ValueError("source row count changed after recovery")
    resolution_by_symbol: dict[str, list[Mapping[str, object]]] = {}
    for resolution in resolutions:
        resolution_by_symbol.setdefault(str(resolution["symbol"]), []).append(resolution)
    symbols: list[dict[str, object]] = []
    total_before_missing = 0
    total_after_missing = 0
    total_added = 0
    for envelope in manifest.envelopes:
        before_missing = sum(
            gap.expected_minutes for gap in manifest.gaps if gap.symbol == envelope.symbol
        )
        added = int(str(recovered_by_symbol.get(envelope.symbol, 0)))
        after_missing = before_missing - added
        if after_missing < 0 or after_missing != before_missing - added:
            raise ValueError(f"coverage conservation failed for {envelope.symbol}")
        unresolved = [
            item
            for item in resolution_by_symbol.get(envelope.symbol, [])
            if str(item["resolution"]) != GapResolution.RECOVERED
        ]
        largest_remaining = max(
            (
                int(str(item["expected_minutes"])) - int(str(item["recovered_minutes"]))
                for item in unresolved
            ),
            default=0,
        )
        expected_slots = envelope.row_count + before_missing
        symbols.append(
            {
                "symbol": envelope.symbol,
                "timeframe": envelope.timeframe,
                "before_rows": envelope.row_count,
                "before_missing_minutes": before_missing,
                "before_coverage": envelope.row_count / expected_slots,
                "added_unique_rows": added,
                "after_rows": envelope.row_count + added,
                "after_missing_minutes": after_missing,
                "after_coverage": (envelope.row_count + added) / expected_slots,
                "unresolved_gap_count": len(unresolved),
                "largest_remaining_gap_minutes": largest_remaining,
            }
        )
        total_before_missing += before_missing
        total_after_missing += after_missing
        total_added += added
    if total_after_missing != total_before_missing - total_added:
        raise ValueError("global coverage conservation failed")
    checksum_material = "\n".join(sorted(payload_checksums)).encode()
    error_resolutions = {
        GapResolution.SOURCE_UNAVAILABLE,
        GapResolution.SOURCE_CONFLICT,
        GapResolution.FETCH_FAILED,
        GapResolution.UNRESOLVED,
    }
    resolution_error_count = sum(
        str(item["resolution"]) in error_resolutions for item in resolutions
    )
    return {
        "manifest_sha256": manifest.sha256(),
        "source_row_count_before": manifest.source_identity.source_row_count,
        "source_row_count_after": source_row_count,
        "source_row_count_unchanged": True,
        "before_missing_minutes": total_before_missing,
        "added_unique_rows": total_added,
        "after_missing_minutes": total_after_missing,
        "coverage_conservation": True,
        "unresolved_gap_count": sum(int(str(item["unresolved_gap_count"])) for item in symbols),
        "largest_remaining_gap_minutes": max(
            (int(str(item["largest_remaining_gap_minutes"])) for item in symbols), default=0
        ),
        "source_checksum_count": len(set(payload_checksums)),
        "source_checksum_list_hash": hashlib.sha256(checksum_material).hexdigest(),
        "error_count": resolution_error_count + batch_error_count,
        "symbols": symbols,
    }


def _validate_values(row: RecoveryCandle) -> None:
    numeric = [row.open, row.high, row.low, row.close, row.volume]
    if row.quote_volume is not None:
        numeric.append(row.quote_volume)
    if any(not value.is_finite() for value in numeric):
        raise RecoveryValidationError("source candle contains a non-finite numeric value")
    if min(row.open, row.high, row.low, row.close) < 0:
        raise RecoveryValidationError("source candle contains a negative price")
    if row.volume < 0 or (row.quote_volume is not None and row.quote_volume < 0):
        raise RecoveryValidationError("source candle contains negative volume")
    if row.trades is not None and row.trades < 0:
        raise RecoveryValidationError("source candle contains a negative trade count")
    if row.high < max(row.open, row.low, row.close) or row.low > min(row.open, row.high, row.close):
        raise RecoveryValidationError("source candle violates OHLC relationships")


def _differing_fields(left: RecoveryCandle, right: RecoveryCandle) -> tuple[str, ...]:
    fields = ("symbol", "timeframe", "open_time_ms", "open", "high", "low", "close", "volume")
    optional = ("quote_volume", "trades")
    differences = [name for name in fields if getattr(left, name) != getattr(right, name)]
    differences.extend(
        name
        for name in optional
        if getattr(left, name) is not None and getattr(left, name) != getattr(right, name)
    )
    return tuple(differences)


def _canonical_row(row: RecoveryCandle) -> bytes:
    values: list[Any] = [
        row.symbol,
        row.timeframe,
        row.open_time_ms,
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
        row.quote_volume,
        row.trades,
    ]
    normalized = [None if value is None else str(value) for value in values]
    return (json.dumps(normalized, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
