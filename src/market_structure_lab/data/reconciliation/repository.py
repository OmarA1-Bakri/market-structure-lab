"""Append-only PostgreSQL publication and explicit reconciliation promotion."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import batched
from typing import Any, Protocol

from sqlalchemy import Connection, text

from market_structure_lab.data.reconciliation.manifests import (
    ReconciliationRunManifest,
    WorkUnitManifest,
)
from market_structure_lab.data.reconciliation.models import (
    MINUTE_MS,
    ReconciliationClass,
)
from market_structure_lab.data.recovery import RECOVERY_ADVISORY_LOCK_NAME, RecoveryCandle


class _Digest(Protocol):
    def update(self, value: bytes, /) -> None: ...


@dataclass(frozen=True, slots=True)
class ReconciliationReplacement:
    run_id: str
    work_unit_id: str
    symbol: str
    timeframe: str
    open_time_ms: int
    classification: ReconciliationClass
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None
    trades: int | None
    source_name: str
    source_revision: str
    payload_sha256: str
    binance_row_sha256: str
    retrieved_at: str

    def __post_init__(self) -> None:
        if self.classification not in (
            ReconciliationClass.BINANCE_CORRECTION,
            ReconciliationClass.BINANCE_FILL,
        ):
            raise ValueError("replacement classification must be a Binance correction or fill")
        if not self.symbol or self.symbol != self.symbol.upper() or self.timeframe != "1m":
            raise ValueError("replacement market identity is invalid")
        if self.open_time_ms % MINUTE_MS:
            raise ValueError("replacement timestamp must be minute-aligned")
        numeric = [self.open, self.high, self.low, self.close, self.volume]
        if self.quote_volume is not None:
            numeric.append(self.quote_volume)
        if any(not value.is_finite() for value in numeric):
            raise ValueError("replacement contains a non-finite value")
        if self.high < max(self.open, self.low, self.close) or self.low > min(
            self.open, self.high, self.close
        ):
            raise ValueError("replacement violates OHLC relationships")
        if self.volume < 0 or (self.quote_volume is not None and self.quote_volume < 0):
            raise ValueError("replacement contains negative volume")
        if self.trades is not None and self.trades < 0:
            raise ValueError("replacement contains a negative trade count")
        for value in (self.payload_sha256, self.binance_row_sha256):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("replacement hashes must be lowercase SHA-256 digests")
        if self.binance_row_sha256 != self.canonical_row_checksum():
            raise ValueError("declared Binance row checksum does not match the replacement fields")
        _parse_utc(self.retrieved_at)

    def canonical_row_checksum(self) -> str:
        """Return the frozen RecoveryCandle checksum from exact in-memory values."""
        return RecoveryCandle(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_time_ms=self.open_time_ms,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            trades=self.trades,
        ).row_checksum()


@dataclass(frozen=True, slots=True)
class VerifiedCoverageInterval:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper() or self.timeframe != "1m":
            raise ValueError("coverage market identity is invalid")
        if self.start_ms % MINUTE_MS or self.end_ms % MINUTE_MS:
            raise ValueError("coverage bounds must be minute-aligned")
        if self.end_ms <= self.start_ms:
            raise ValueError("coverage interval must be non-empty and half-open")


@dataclass(frozen=True, slots=True)
class ReconciliationPromotion:
    run_id: str
    manifest_sha256: str
    replacement_logical_sha256: str
    canonical_logical_sha256: str
    promoted_at: str


@dataclass(frozen=True, slots=True)
class ReconciliationDatabasePreflight:
    """Read-only database state and exact idempotent writes for one promotion request."""

    run_registration_rows_to_insert: int
    work_unit_rows_to_insert: int
    replacement_rows_to_insert: int
    verified_replacement_rows: int
    verified_replacement_logical_sha256: str
    coverage_rows_existing: int
    coverage_rows_to_insert: int
    promotion_rows_to_insert: int
    active_run_before: str | None
    active_run_after: str
    reconciled_view_will_switch: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReconciliationRepository:
    """Publish verified artifacts and make one run active through an explicit promotion."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def register_run(self, run: ReconciliationRunManifest) -> None:
        self._acquire_lock()
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_reconciliation_runs (
    run_id, manifest_sha256, manifest_json, dump_sha256, source_row_count,
    mapping_version, cutoff
) VALUES (
    :run_id, :manifest_sha256, CAST(:manifest_json AS jsonb), :dump_sha256,
    :source_row_count, :mapping_version, :cutoff
)
ON CONFLICT (run_id) DO NOTHING
"""
            ),
            {
                "run_id": run.run_id,
                "manifest_sha256": run.manifest_sha256,
                "manifest_json": run.to_json(),
                "dump_sha256": run.dump_sha256,
                "source_row_count": run.source_row_count,
                "mapping_version": run.mapping_version,
                "cutoff": _parse_utc(run.cutoff),
            },
        )
        stored = self.connection.execute(
            text(
                "SELECT manifest_sha256 FROM market_data.candle_reconciliation_runs "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run.run_id},
        ).scalar_one()
        if stored != run.manifest_sha256:
            raise ValueError("registered reconciliation run contains different content")

    def publish_work_unit(
        self,
        manifest: WorkUnitManifest,
        replacements: Iterable[ReconciliationReplacement],
    ) -> int:
        self._acquire_lock()
        rows = tuple(sorted(replacements, key=lambda item: item.open_time_ms))
        _verify_replacements(manifest, rows)
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_reconciliation_work_units (
    run_id, work_unit_id, manifest_sha256, manifest_json, publication_path,
    status, row_count, replacement_row_count, replacement_logical_sha256
) VALUES (
    :run_id, :work_unit_id, :manifest_sha256, CAST(:manifest_json AS jsonb),
    :publication_path, :status, :row_count, :replacement_row_count,
    :replacement_logical_sha256
)
ON CONFLICT (run_id, work_unit_id) DO NOTHING
"""
            ),
            {
                "run_id": manifest.run_id,
                "work_unit_id": manifest.work_unit_id,
                "manifest_sha256": manifest.manifest_sha256,
                "manifest_json": manifest.to_json(),
                "publication_path": manifest.publication_path,
                "status": manifest.status,
                "row_count": manifest.row_count,
                "replacement_row_count": manifest.replacement_row_count,
                "replacement_logical_sha256": manifest.replacement_logical_sha256,
            },
        )
        stored = self.connection.execute(
            text(
                "SELECT manifest_sha256 FROM market_data.candle_reconciliation_work_units "
                "WHERE run_id=:run_id AND work_unit_id=:work_unit_id"
            ),
            {"run_id": manifest.run_id, "work_unit_id": manifest.work_unit_id},
        ).scalar_one()
        if stored != manifest.manifest_sha256:
            raise ValueError("registered reconciliation work unit contains different content")
        replacement_statement = text(
            """
INSERT INTO market_data.candle_reconciliation_replacements (
    run_id, work_unit_id, symbol, "interval", open_time, classification,
    open, high, low, close, volume, quote_volume, trades, source_name,
    source_revision, payload_sha256, binance_row_sha256, retrieved_at
) VALUES (
    :run_id, :work_unit_id, :symbol, :timeframe, :open_time_ms, :classification,
    :open, :high, :low, :close, :volume, :quote_volume, :trades, :source_name,
    :source_revision, :payload_sha256, :binance_row_sha256, :retrieved_at
)
ON CONFLICT (run_id, symbol, "interval", open_time) DO NOTHING
"""
        )
        before = _database_replacement_count(self.connection, manifest)
        for batch in batched(rows, 5_000):
            self.connection.execute(
                replacement_statement,
                [
                    {
                        **asdict(row),
                        "classification": row.classification.value,
                        "retrieved_at": _parse_utc(row.retrieved_at),
                    }
                    for row in batch
                ],
            )
        _verify_database_replacements(self.connection, manifest, rows)
        return _database_replacement_count(self.connection, manifest) - before

    def promote(
        self,
        run: ReconciliationRunManifest,
        work_units: Sequence[WorkUnitManifest],
        coverage: Sequence[VerifiedCoverageInterval],
        *,
        candidate_replacement_logical_sha256: str,
    ) -> ReconciliationPromotion:
        self._acquire_lock()
        self._verify_registered_run(run, work_units)
        intervals = validate_reconciliation_coverage(run, coverage)
        replacement_hash, _ = self._verify_candidate_replacements(
            run,
            work_units,
            candidate_replacement_logical_sha256=candidate_replacement_logical_sha256,
        )
        for item in intervals:
            self.connection.execute(
                text(
                    """
INSERT INTO market_data.candle_reconciliation_coverage (
    run_id, symbol, "interval", start_time, end_time
) VALUES (:run_id, :symbol, :timeframe, :start_ms, :end_ms)
ON CONFLICT (run_id, symbol, "interval", start_time, end_time) DO NOTHING
"""
                ),
                {
                    "run_id": run.run_id,
                    "symbol": item.symbol,
                    "timeframe": item.timeframe,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                },
            )
        canonical_hash = _run_canonical_hash(self.connection, run)
        self.connection.execute(
            text(
                """
INSERT INTO market_data.candle_reconciliation_promotions (
    run_id, manifest_sha256, replacement_logical_sha256, canonical_logical_sha256
) VALUES (:run_id, :manifest_sha256, :replacement_hash, :canonical_hash)
ON CONFLICT (run_id) DO NOTHING
"""
            ),
            {
                "run_id": run.run_id,
                "manifest_sha256": run.manifest_sha256,
                "replacement_hash": replacement_hash,
                "canonical_hash": canonical_hash,
            },
        )
        row = self.connection.execute(
            text(
                """
SELECT run_id, manifest_sha256, replacement_logical_sha256,
       canonical_logical_sha256, promoted_at
FROM market_data.candle_reconciliation_promotions
WHERE run_id=:run_id
"""
            ),
            {"run_id": run.run_id},
        ).one()
        promotion = ReconciliationPromotion(
            run_id=row.run_id,
            manifest_sha256=row.manifest_sha256,
            replacement_logical_sha256=row.replacement_logical_sha256,
            canonical_logical_sha256=row.canonical_logical_sha256,
            promoted_at=row.promoted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )
        if (
            promotion.manifest_sha256 != run.manifest_sha256
            or promotion.replacement_logical_sha256 != replacement_hash
            or promotion.canonical_logical_sha256 != canonical_hash
        ):
            raise ValueError("existing reconciliation promotion contains different content")
        return promotion

    def inspect_promotion(
        self,
        run: ReconciliationRunManifest,
        work_units: Sequence[WorkUnitManifest],
        coverage: Sequence[VerifiedCoverageInterval],
        *,
        candidate_replacement_logical_sha256: str,
    ) -> ReconciliationDatabasePreflight:
        """Inspect exact durable state without inserting or activating a promotion."""
        self._acquire_lock()
        self._verify_registered_run(run, work_units)
        intervals = validate_reconciliation_coverage(run, coverage)
        replacement_hash, replacement_count = self._verify_candidate_replacements(
            run,
            work_units,
            candidate_replacement_logical_sha256=candidate_replacement_logical_sha256,
        )

        rows = self.connection.execute(
            text(
                """
SELECT symbol, "interval", start_time, end_time
FROM market_data.candle_reconciliation_coverage
WHERE run_id=:run_id
ORDER BY symbol, "interval", start_time
"""
            ).execution_options(stream_results=True),
            {"run_id": run.run_id},
        )
        existing_coverage_rows: list[VerifiedCoverageInterval] = []
        for row in rows:
            if len(existing_coverage_rows) >= len(intervals):
                raise ValueError("database coverage rows exceed the verified candidate")
            existing_coverage_rows.append(
                VerifiedCoverageInterval(row.symbol, row.interval, row.start_time, row.end_time)
            )
        existing_coverage = tuple(existing_coverage_rows)
        candidate_coverage = set(intervals)
        stored_coverage = set(existing_coverage)
        if len(stored_coverage) != len(existing_coverage):
            raise ValueError("database coverage contains duplicate intervals")
        if not stored_coverage <= candidate_coverage:
            raise ValueError("database coverage contains intervals outside the verified candidate")

        existing_promotion = self.connection.execute(
            text(
                """
SELECT promotion_id, run_id, manifest_sha256, replacement_logical_sha256,
       canonical_logical_sha256
FROM market_data.candle_reconciliation_promotions
WHERE run_id=:run_id
"""
            ),
            {"run_id": run.run_id},
        ).one_or_none()
        active = self.connection.execute(
            text(
                """
SELECT promotion_id, run_id
FROM market_data.candle_reconciliation_promotions
ORDER BY promotion_id DESC
LIMIT 1
"""
            )
        ).one_or_none()
        active_before = None if active is None else str(active.run_id)
        if existing_promotion is not None:
            if stored_coverage != candidate_coverage:
                raise ValueError("existing promotion coverage differs from the verified candidate")
            canonical_hash = _run_canonical_hash(self.connection, run)
            if (
                existing_promotion.manifest_sha256 != run.manifest_sha256
                or existing_promotion.replacement_logical_sha256 != replacement_hash
                or existing_promotion.canonical_logical_sha256 != canonical_hash
            ):
                raise ValueError("existing reconciliation promotion contains different content")
            promotion_rows_to_insert = 0
            active_after = active_before or run.run_id
        else:
            promotion_rows_to_insert = 1
            active_after = run.run_id
        return ReconciliationDatabasePreflight(
            run_registration_rows_to_insert=0,
            work_unit_rows_to_insert=0,
            replacement_rows_to_insert=0,
            verified_replacement_rows=replacement_count,
            verified_replacement_logical_sha256=replacement_hash,
            coverage_rows_existing=len(existing_coverage),
            coverage_rows_to_insert=len(candidate_coverage - stored_coverage),
            promotion_rows_to_insert=promotion_rows_to_insert,
            active_run_before=active_before,
            active_run_after=active_after,
            reconciled_view_will_switch=active_before != active_after,
        )

    def _verify_registered_run(
        self,
        run: ReconciliationRunManifest,
        work_units: Sequence[WorkUnitManifest],
    ) -> None:
        stored = self.connection.execute(
            text(
                "SELECT manifest_sha256 FROM market_data.candle_reconciliation_runs "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run.run_id},
        ).scalar_one_or_none()
        if stored != run.manifest_sha256:
            raise ValueError("reconciliation run is not registered with the frozen manifest")
        expected = {item.work_unit_id for item in run.work_units}
        supplied = {item.work_unit_id for item in work_units}
        if supplied != expected or len(work_units) != len(expected):
            raise ValueError("promotion must supply every frozen work-unit manifest exactly once")
        rows = self.connection.execute(
            text(
                "SELECT work_unit_id, manifest_sha256, status "
                "FROM market_data.candle_reconciliation_work_units WHERE run_id=:run_id"
            ).execution_options(stream_results=True),
            {"run_id": run.run_id},
        )
        actual: dict[str, tuple[str, str]] = {}
        for row in rows:
            if len(actual) >= len(expected):
                raise ValueError("database work-unit rows exceed the frozen manifest")
            if row.work_unit_id in actual:
                raise ValueError("database contains duplicate reconciliation work units")
            actual[row.work_unit_id] = (row.manifest_sha256, row.status)
        if set(actual) != expected:
            raise ValueError("database does not contain every frozen reconciliation work unit")
        for manifest in work_units:
            if actual[manifest.work_unit_id] != (manifest.manifest_sha256, manifest.status):
                raise ValueError("database work-unit evidence differs from the supplied manifest")
            if manifest.status == "failed":
                raise ValueError("failed reconciliation work units cannot be promoted")

    def _verify_candidate_replacements(
        self,
        run: ReconciliationRunManifest,
        work_units: Sequence[WorkUnitManifest],
        *,
        candidate_replacement_logical_sha256: str,
    ) -> tuple[str, int]:
        expected_count = sum(item.replacement_row_count for item in work_units)
        replacement_count = _database_run_replacement_count(self.connection, run.run_id)
        if replacement_count != expected_count:
            raise ValueError("database replacements differ from the verified promotion candidate")
        replacement_hash = _database_replacement_hash(self.connection, run.run_id)
        if replacement_hash != candidate_replacement_logical_sha256:
            raise ValueError("database replacements differ from the verified promotion candidate")
        return replacement_hash, replacement_count

    def _acquire_lock(self) -> None:
        self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
        )


def _verify_replacements(
    manifest: WorkUnitManifest,
    rows: Sequence[ReconciliationReplacement],
) -> None:
    if len(rows) != manifest.replacement_row_count:
        raise ValueError("replacement rows do not match the work-unit manifest count")
    if any(
        row.run_id != manifest.run_id or row.work_unit_id != manifest.work_unit_id for row in rows
    ):
        raise ValueError("replacement identity does not match the work-unit manifest")
    keys = tuple((row.symbol, row.timeframe, row.open_time_ms) for row in rows)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("replacement rows must have unique canonical order")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                {
                    "binance_row_sha256": row.binance_row_sha256,
                    "open_time_ms": row.open_time_ms,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    if digest.hexdigest() != manifest.replacement_logical_sha256:
        raise ValueError("replacement rows do not match the work-unit logical hash")


def _verify_database_replacements(
    connection: Connection,
    manifest: WorkUnitManifest,
    expected: Sequence[ReconciliationReplacement],
) -> None:
    rows = connection.execute(
        text(
            """
SELECT symbol, "interval", open_time, binance_row_sha256,
       classification, open, high, low, close, volume, quote_volume, trades,
       source_name, source_revision, payload_sha256, retrieved_at
FROM market_data.candle_reconciliation_replacements
WHERE run_id=:run_id AND work_unit_id=:work_unit_id
ORDER BY symbol, "interval", open_time
"""
        ).execution_options(stream_results=True),
        {"run_id": manifest.run_id, "work_unit_id": manifest.work_unit_id},
    )
    actual_rows: list[tuple[object, ...]] = []
    for row in rows:
        if len(actual_rows) >= len(expected):
            raise ValueError("database replacements differ from the verified work-unit evidence")
        actual_rows.append(
            (
                row.symbol,
                row.interval,
                row.open_time,
                row.classification,
                row.open,
                row.high,
                row.low,
                row.close,
                row.volume,
                row.quote_volume,
                row.trades,
                row.source_name,
                row.source_revision,
                row.payload_sha256,
                row.binance_row_sha256,
                row.retrieved_at.astimezone(UTC),
            )
        )
    actual = tuple(actual_rows)
    wanted = tuple(
        (
            row.symbol,
            row.timeframe,
            row.open_time_ms,
            row.classification.value,
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume,
            row.quote_volume,
            row.trades,
            row.source_name,
            row.source_revision,
            row.payload_sha256,
            row.binance_row_sha256,
            _parse_utc(row.retrieved_at),
        )
        for row in expected
    )
    if actual != wanted:
        raise ValueError("database replacements differ from the verified work-unit evidence")


def _database_replacement_count(
    connection: Connection,
    manifest: WorkUnitManifest,
) -> int:
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM market_data.candle_reconciliation_replacements "
                "WHERE run_id=:run_id AND work_unit_id=:work_unit_id"
            ),
            {"run_id": manifest.run_id, "work_unit_id": manifest.work_unit_id},
        ).scalar_one()
    )


def _database_run_replacement_count(connection: Connection, run_id: str) -> int:
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM market_data.candle_reconciliation_replacements "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run_id},
        ).scalar_one()
    )


def validate_reconciliation_coverage(
    run: ReconciliationRunManifest,
    coverage: Sequence[VerifiedCoverageInterval],
) -> tuple[VerifiedCoverageInterval, ...]:
    ordered = tuple(sorted(coverage, key=lambda item: (item.symbol, item.timeframe, item.start_ms)))
    if tuple(coverage) != ordered:
        raise ValueError("coverage intervals must use canonical sorted order")
    previous: VerifiedCoverageInterval | None = None
    for item in ordered:
        if not any(
            item.symbol == envelope.symbol
            and item.timeframe == envelope.timeframe
            and envelope.start_ms <= item.start_ms < item.end_ms <= envelope.end_ms
            for envelope in run.envelopes
        ):
            raise ValueError("coverage interval lies outside the frozen trading envelope")
        if (
            previous is not None
            and (previous.symbol, previous.timeframe) == (item.symbol, item.timeframe)
            and item.start_ms < previous.end_ms
        ):
            raise ValueError("coverage intervals cannot overlap")
        previous = item
    return ordered


def _database_replacement_hash(connection: Connection, run_id: str) -> str:
    digest = hashlib.sha256()
    rows = connection.execute(
        text(
            """
SELECT symbol, "interval", open_time, binance_row_sha256
FROM market_data.candle_reconciliation_replacements
WHERE run_id=:run_id
ORDER BY symbol, "interval", open_time
"""
        ).execution_options(stream_results=True),
        {"run_id": run_id},
    )
    for row in rows:
        digest.update(
            json.dumps(
                {
                    "binance_row_sha256": row.binance_row_sha256,
                    "open_time_ms": row.open_time,
                    "symbol": row.symbol,
                    "timeframe": row.interval,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    return digest.hexdigest()


def _run_canonical_hash(connection: Connection, run: ReconciliationRunManifest) -> str:
    digest = hashlib.sha256()
    optimizer_settings = connection.execute(
        text(
            """
SELECT set_config('enable_seqscan', 'off', true),
       set_config('enable_bitmapscan', 'off', true),
       set_config('max_parallel_workers_per_gather', '0', true)
"""
        )
    )
    close_settings = getattr(optimizer_settings, "close", None)
    if close_settings is not None:
        close_settings()
    markets = sorted({(item.symbol, item.timeframe) for item in run.envelopes})
    replacement_statement = text(
        """
SELECT symbol, "interval", open_time, open::text AS open, high::text AS high,
       low::text AS low, close::text AS close, volume::text AS volume,
       quote_volume::text AS quote_volume, trades::text AS trades,
       classification AS origin
FROM market_data.candle_reconciliation_replacements
WHERE run_id=:run_id AND symbol=:symbol AND "interval"=:timeframe
ORDER BY open_time
"""
    ).execution_options(stream_results=True)
    verified_dump_statement = text(
        """
SELECT candle.symbol, candle."interval", candle.open_time,
       candle.open::text AS open, candle.high::text AS high,
       candle.low::text AS low, candle.close::text AS close,
       candle.volume::text AS volume, candle.quote_volume::text AS quote_volume,
       candle.trades::text AS trades, 'dump_verified_match'::text AS origin
FROM market_data.candles AS candle
WHERE candle.symbol=:symbol AND candle."interval"=:timeframe
  AND EXISTS (
      SELECT 1
      FROM market_data.candle_reconciliation_coverage AS coverage
      WHERE coverage.run_id=:run_id
        AND coverage.symbol=candle.symbol
        AND coverage."interval"=candle."interval"
        AND coverage.start_time <= candle.open_time
        AND candle.open_time < coverage.end_time
  )
  AND NOT EXISTS (
      SELECT 1
      FROM market_data.candle_reconciliation_replacements AS replacement
      WHERE replacement.run_id=:run_id
        AND replacement.symbol=candle.symbol
        AND replacement."interval"=candle."interval"
        AND replacement.open_time=candle.open_time
  )
ORDER BY candle.open_time
"""
    ).execution_options(stream_results=True)
    for symbol, timeframe in markets:
        parameters = {
            "run_id": run.run_id,
            "symbol": symbol,
            "timeframe": timeframe,
        }
        replacements = connection.execute(replacement_statement, parameters)
        verified_dump = connection.execute(verified_dump_statement, parameters)
        try:
            _update_canonical_hash(digest, replacements, verified_dump)
        finally:
            for rows in (replacements, verified_dump):
                close = getattr(rows, "close", None)
                if close is not None:
                    close()
    return digest.hexdigest()


def _update_canonical_hash(
    digest: _Digest,
    replacements: Iterable[Any],
    verified_dump: Iterable[Any],
) -> None:
    replacement_rows = iter(replacements)
    dump_rows = iter(verified_dump)
    replacement = next(replacement_rows, None)
    dump = next(dump_rows, None)
    while replacement is not None or dump is not None:
        if dump is None:
            row = replacement
            replacement = next(replacement_rows, None)
        elif replacement is None:
            row = dump
            dump = next(dump_rows, None)
        elif replacement.open_time < dump.open_time:
            row = replacement
            replacement = next(replacement_rows, None)
        elif dump.open_time < replacement.open_time:
            row = dump
            dump = next(dump_rows, None)
        else:
            raise ValueError("canonical hash inputs contain a duplicate market timestamp")
        assert row is not None
        digest.update(
            json.dumps(
                dict(row._mapping),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


__all__ = [
    "ReconciliationDatabasePreflight",
    "ReconciliationPromotion",
    "ReconciliationReplacement",
    "ReconciliationRepository",
    "VerifiedCoverageInterval",
    "validate_reconciliation_coverage",
]
