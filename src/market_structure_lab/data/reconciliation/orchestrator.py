"""Bounded source/dump execution for one frozen reconciliation work unit."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text

from market_structure_lab.data.reconciliation.compare import reconcile_ordered_candles
from market_structure_lab.data.reconciliation.manifests import (
    ReconciliationRunManifest,
    SourceArtifactIdentity,
    WorkUnitManifest,
)
from market_structure_lab.data.reconciliation.models import (
    ReconciliationClass,
    ReconciliationWorkUnit,
)
from market_structure_lab.data.reconciliation.publication import publish_work_unit
from market_structure_lab.data.reconciliation.repository import (
    ReconciliationReplacement,
    VerifiedCoverageInterval,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.sources.base import (
    FetchRequest,
    MarketDataSource,
    SourceKline,
    SourceProvenance,
)


@dataclass(frozen=True, slots=True)
class WorkUnitExecution:
    manifest: WorkUnitManifest
    replacements: tuple[ReconciliationReplacement, ...]
    verified_coverage: tuple[VerifiedCoverageInterval, ...]


def iter_dump_rows(
    connection: Connection,
    work_unit: ReconciliationWorkUnit,
    *,
    batch_size: int,
) -> Iterator[RecoveryCandle]:
    """Stream immutable dump rows in bounded server-side batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    statement = text(
        """
SELECT symbol, "interval", open_time, open, high, low, close, volume,
       quote_volume, trades
FROM market_data.candles
WHERE symbol=:symbol AND "interval"=:timeframe
  AND :start_ms <= open_time AND open_time < :end_ms
ORDER BY open_time
"""
    ).execution_options(stream_results=True, yield_per=batch_size)
    result = connection.execute(
        statement,
        {
            "symbol": work_unit.symbol,
            "timeframe": work_unit.timeframe,
            "start_ms": work_unit.start_ms,
            "end_ms": work_unit.end_ms,
        },
    ).mappings()
    for partition in result.partitions(batch_size):
        for row in partition:
            yield RecoveryCandle.from_mapping(dict(row))


def execute_work_unit(
    connection: Connection,
    source: MarketDataSource,
    *,
    run: ReconciliationRunManifest,
    work_unit: ReconciliationWorkUnit,
    output_root: Path,
    batch_size: int = 10_000,
    max_rows_per_part: int = 100_000,
) -> WorkUnitExecution:
    """Fetch, compare, publish, and retain only bounded correction/fill rows."""
    if work_unit not in run.work_units:
        raise ValueError("work unit is not frozen in the reconciliation run")
    source_stream = _SourceRowStream(
        source,
        FetchRequest(
            work_unit.symbol,
            work_unit.timeframe,
            work_unit.start_ms,
            work_unit.end_ms,
        ),
    )
    replacements: list[ReconciliationReplacement] = []
    coverage: list[VerifiedCoverageInterval] = []
    coverage_start: int | None = None

    def records():
        nonlocal coverage_start
        reconciled = reconcile_ordered_candles(
            work_unit=work_unit,
            dump_rows=iter_dump_rows(connection, work_unit, batch_size=batch_size),
            binance_rows=source_stream,
        )
        for result in reconciled:
            timestamp = result.record.open_time_ms
            artifact = source_stream.take_artifact(timestamp)
            if result.binance is None:
                if coverage_start is not None:
                    coverage.append(
                        VerifiedCoverageInterval(
                            work_unit.symbol,
                            work_unit.timeframe,
                            coverage_start,
                            timestamp,
                        )
                    )
                    coverage_start = None
            else:
                if artifact is None:
                    raise ValueError("Binance candle has no source provenance")
                if coverage_start is None:
                    coverage_start = timestamp
                if result.record.classification in (
                    ReconciliationClass.BINANCE_CORRECTION,
                    ReconciliationClass.BINANCE_FILL,
                ):
                    replacements.append(
                        _replacement(
                            source_name=source.name,
                            run=run,
                            work_unit=work_unit,
                            classification=result.record.classification,
                            candle=result.binance,
                            artifact=artifact,
                        )
                    )
            yield result.record
        if coverage_start is not None:
            coverage.append(
                VerifiedCoverageInterval(
                    work_unit.symbol,
                    work_unit.timeframe,
                    coverage_start,
                    work_unit.end_ms,
                )
            )

    manifest = publish_work_unit(
        records(),
        output_root=output_root,
        run=run,
        work_unit=work_unit,
        source_artifacts=source_stream.artifacts,
        max_rows_per_part=max_rows_per_part,
    )
    return WorkUnitExecution(
        manifest=manifest,
        replacements=tuple(replacements),
        verified_coverage=tuple(coverage),
    )


class _SourceRowStream:
    def __init__(self, source: MarketDataSource, request: FetchRequest) -> None:
        self.source = source
        self.request = request
        self.artifacts: list[SourceArtifactIdentity] = []
        self._artifact_by_timestamp: dict[int, SourceArtifactIdentity] = {}

    def __iter__(self) -> Iterator[SourceKline]:
        for batch in self.source.fetch(self.request):
            artifact = _artifact(batch.provenance)
            if artifact not in self.artifacts:
                self.artifacts.append(artifact)
            for row in batch.rows:
                if row.open_time_ms in self._artifact_by_timestamp:
                    raise ValueError("source provenance contains a duplicate candle key")
                self._artifact_by_timestamp[row.open_time_ms] = artifact
                yield row

    def take_artifact(self, timestamp: int) -> SourceArtifactIdentity | None:
        return self._artifact_by_timestamp.pop(timestamp, None)


def _artifact(provenance: SourceProvenance) -> SourceArtifactIdentity:
    return SourceArtifactIdentity(
        location=provenance.location,
        payload_sha256=provenance.payload_checksum,
        published_sha256=provenance.published_checksum,
        source_revision=provenance.source_revision,
        retrieved_at=provenance.retrieved_at,
        excluded_row_count=provenance.excluded_row_count,
        integrity_notes=provenance.integrity_notes,
    )


def _replacement(
    *,
    source_name: str,
    run: ReconciliationRunManifest,
    work_unit: ReconciliationWorkUnit,
    classification: ReconciliationClass,
    candle: RecoveryCandle,
    artifact: SourceArtifactIdentity,
) -> ReconciliationReplacement:
    return ReconciliationReplacement(
        run_id=run.run_id,
        work_unit_id=work_unit.work_unit_id,
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time_ms=candle.open_time_ms,
        classification=classification,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        quote_volume=candle.quote_volume,
        trades=candle.trades,
        source_name=source_name,
        source_revision=artifact.source_revision,
        payload_sha256=artifact.payload_sha256,
        binance_row_sha256=candle.row_checksum(),
        retrieved_at=artifact.retrieved_at,
    )


__all__ = ["WorkUnitExecution", "execute_work_unit", "iter_dump_rows"]
