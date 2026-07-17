"""Append-only PostgreSQL publication and explicit reconciliation promotion."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import batched

from sqlalchemy import Connection, text

from market_structure_lab.data.reconciliation.manifests import (
    ReconciliationRunManifest,
    WorkUnitManifest,
)
from market_structure_lab.data.reconciliation.models import (
    MINUTE_MS,
    ReconciliationClass,
)
from market_structure_lab.data.recovery import RECOVERY_ADVISORY_LOCK_NAME


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
        _parse_utc(self.retrieved_at)


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
    ) -> ReconciliationPromotion:
        self._acquire_lock()
        self._verify_registered_run(run, work_units)
        intervals = _validate_coverage(run, coverage)
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
        replacement_hash = _database_replacement_hash(self.connection, run.run_id)
        canonical_hash = _run_canonical_hash(self.connection, run.run_id)
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
            ),
            {"run_id": run.run_id},
        ).all()
        actual = {row.work_unit_id: (row.manifest_sha256, row.status) for row in rows}
        if set(actual) != expected:
            raise ValueError("database does not contain every frozen reconciliation work unit")
        for manifest in work_units:
            if actual[manifest.work_unit_id] != (manifest.manifest_sha256, manifest.status):
                raise ValueError("database work-unit evidence differs from the supplied manifest")
            if manifest.status == "failed":
                raise ValueError("failed reconciliation work units cannot be promoted")

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
SELECT symbol, "interval", open_time, binance_row_sha256
FROM market_data.candle_reconciliation_replacements
WHERE run_id=:run_id AND work_unit_id=:work_unit_id
ORDER BY symbol, "interval", open_time
"""
        ),
        {"run_id": manifest.run_id, "work_unit_id": manifest.work_unit_id},
    ).all()
    actual = tuple(
        (row.symbol, row.interval, row.open_time, row.binance_row_sha256) for row in rows
    )
    wanted = tuple(
        (row.symbol, row.timeframe, row.open_time_ms, row.binance_row_sha256) for row in expected
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


def _validate_coverage(
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


def _run_canonical_hash(connection: Connection, run_id: str) -> str:
    digest = hashlib.sha256()
    rows = connection.execute(
        text(
            """
WITH replacements AS (
    SELECT symbol, "interval", open_time, open::text AS open, high::text AS high,
           low::text AS low, close::text AS close, volume::text AS volume,
           quote_volume::text AS quote_volume, trades::text AS trades,
           classification AS origin
    FROM market_data.candle_reconciliation_replacements
    WHERE run_id=:run_id
),
verified_dump AS (
    SELECT candle.symbol, candle."interval", candle.open_time,
           candle.open::text AS open, candle.high::text AS high,
           candle.low::text AS low, candle.close::text AS close,
           candle.volume::text AS volume, candle.quote_volume::text AS quote_volume,
           candle.trades::text AS trades, 'dump_verified_match'::text AS origin
    FROM market_data.candles AS candle
    JOIN market_data.candle_reconciliation_coverage AS coverage
      ON coverage.run_id=:run_id
     AND coverage.symbol=candle.symbol
     AND coverage."interval"=candle."interval"
     AND coverage.start_time <= candle.open_time
     AND candle.open_time < coverage.end_time
    WHERE NOT EXISTS (
        SELECT 1 FROM replacements
        WHERE replacements.symbol=candle.symbol
          AND replacements."interval"=candle."interval"
          AND replacements.open_time=candle.open_time
    )
)
SELECT * FROM replacements
UNION ALL
SELECT * FROM verified_dump
ORDER BY symbol, "interval", open_time
"""
        ).execution_options(stream_results=True),
        {"run_id": run_id},
    )
    for row in rows:
        digest.update(
            json.dumps(
                dict(row._mapping),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    return digest.hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


__all__ = [
    "ReconciliationPromotion",
    "ReconciliationReplacement",
    "ReconciliationRepository",
    "VerifiedCoverageInterval",
]
