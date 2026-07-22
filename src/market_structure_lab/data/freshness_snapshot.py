"""Explicit immutable snapshot handoff from checksum-verified freshness evidence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import polars as pl
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from market_structure_lab.core.config import CandleSourceMapping, MarketDataSettings
from market_structure_lab.data.export import (
    SnapshotIdentity,
    SnapshotManifest,
    SnapshotResearchBinding,
    export_partitioned_snapshot,
)
from market_structure_lab.data.freshness_sync import (
    FreshnessReport,
    FreshnessRunStatus,
)
from market_structure_lab.data.gaps import ProvenanceState
from market_structure_lab.data.loader import canonical_view_mapping, iter_candle_batches
from market_structure_lab.data.recovery import (
    RECOVERY_ADVISORY_LOCK_NAME,
    RecoveryRepository,
)
from market_structure_lab.data.segments import SegmentBoundary, segment_boundaries_sha256


class SnapshotPublicationPolicy(StrEnum):
    """Explicit report-health policy for a frozen research snapshot."""

    REQUIRE_HEALTHY = "require_healthy"
    ALLOW_PROVENANCE_BLOCKED = "allow_provenance_blocked"


_HEALTHY = {FreshnessRunStatus.UP_TO_DATE, FreshnessRunStatus.RECOVERED}
_PROVENANCE_BLOCKED = {
    FreshnessRunStatus.PROVENANCE_PENDING,
    FreshnessRunStatus.SOURCE_CONFLICT,
    FreshnessRunStatus.SOURCE_UNAVAILABLE,
}


def validate_snapshot_publication(
    report: FreshnessReport,
    policy: SnapshotPublicationPolicy = SnapshotPublicationPolicy.REQUIRE_HEALTHY,
) -> None:
    """Fail closed unless each symbol satisfies the selected publication policy."""
    if not report.coverage_conserved:
        raise ValueError("freshness report does not conserve planned coverage")
    expected_blocked_state = {
        FreshnessRunStatus.PROVENANCE_PENDING: ProvenanceState.PENDING,
        FreshnessRunStatus.SOURCE_CONFLICT: ProvenanceState.SOURCE_CONFLICT,
        FreshnessRunStatus.SOURCE_UNAVAILABLE: ProvenanceState.SOURCE_UNAVAILABLE,
    }
    mismatched = tuple(
        item
        for item in report.symbols
        if (item.status in _HEALTHY and item.compatibility_state is not ProvenanceState.COMPATIBLE)
        or (
            item.status in _PROVENANCE_BLOCKED
            and item.compatibility_state is not expected_blocked_state[item.status]
        )
    )
    if mismatched:
        statuses = ", ".join(
            f"{item.symbol}:{item.compatibility_state.value}/{item.status.value}"
            for item in mismatched
        )
        raise ValueError(f"freshness status and compatibility evidence disagree: {statuses}")
    admitted = _HEALTHY
    if policy is SnapshotPublicationPolicy.ALLOW_PROVENANCE_BLOCKED:
        admitted = _HEALTHY | _PROVENANCE_BLOCKED
    blocked = tuple(item for item in report.symbols if item.status in _PROVENANCE_BLOCKED)
    if blocked and policy is SnapshotPublicationPolicy.REQUIRE_HEALTHY:
        symbols = ", ".join(item.symbol for item in blocked)
        raise ValueError(
            "freshness report contains provenance-blocked symbols; "
            f"select the explicit allow_provenance_blocked policy to publish: {symbols}"
        )
    rejected = tuple(item for item in report.symbols if item.status not in admitted)
    if rejected:
        statuses = ", ".join(f"{item.symbol}:{item.status.value}" for item in rejected)
        raise ValueError(f"freshness report cannot publish snapshot: {statuses}")


def build_freshness_snapshot_identity(
    report: FreshnessReport,
    *,
    dataset_version: str,
    recovery_sha256: str,
    mapping_version: str,
    config_version: str,
    code_commit: str,
    policy: SnapshotPublicationPolicy,
    research_binding: SnapshotResearchBinding | None = None,
) -> SnapshotIdentity:
    """Bind the generic snapshot identity to the reviewed freshness evidence."""
    return SnapshotIdentity(
        dataset_version=dataset_version,
        dump_sha256=report.dump_sha256,
        recovery_sha256=recovery_sha256,
        mapping_version=mapping_version,
        config_version=config_version,
        code_commit=code_commit,
        freshness_report_sha256=report.sha256(),
        freshness_manifest_sha256=report.manifest_sha256,
        compatibility_manifest_sha256=report.compatibility_manifest_sha256,
        freshness_as_of=report.as_of,
        publication_policy=policy.value,
        research_binding=research_binding,
    )


def publish_freshness_snapshot(
    engine: Engine,
    report: FreshnessReport,
    *,
    output_root: str | Path,
    dataset_version: str,
    config_version: str,
    code_commit: str,
    policy: SnapshotPublicationPolicy = SnapshotPublicationPolicy.REQUIRE_HEALTHY,
    batch_size: int = 10_000,
    mapping: CandleSourceMapping | None = None,
    settings: MarketDataSettings | None = None,
) -> SnapshotManifest:
    """Publish one deliberate, bounded snapshot from a consistent canonical view.

    The same PostgreSQL advisory lock used by recovery excludes concurrent supplement
    publication. The report's logical supplement hash must still match the database,
    so an older report cannot silently label a newer canonical state.
    """
    validate_snapshot_publication(report, policy)
    if report.recovery_logical_hash is None:
        raise ValueError("freshness report does not pin the canonical supplement state")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    active_settings = settings or MarketDataSettings.from_env()
    source_mapping = mapping or active_settings.candles
    canonical_mapping = canonical_view_mapping(source_mapping)
    cutoff = datetime.fromisoformat(report.as_of.replace("Z", "+00:00"))

    with engine.connect() as raw_connection:
        connection = _snapshot_connection(raw_connection)
        with connection.begin():
            _acquire_snapshot_lock(connection)
            recovery_sha256 = RecoveryRepository(connection).logical_supplement_hash()
            if recovery_sha256 != report.recovery_logical_hash:
                raise ValueError("database supplement state differs from the freshness report")
            identity = build_freshness_snapshot_identity(
                report,
                dataset_version=dataset_version,
                recovery_sha256=recovery_sha256,
                mapping_version=source_mapping.version,
                config_version=config_version,
                code_commit=code_commit,
                policy=policy,
            )
            return export_partitioned_snapshot(
                _iter_report_batches(
                    connection,
                    report,
                    mapping=canonical_mapping,
                    cutoff=cutoff,
                    batch_size=batch_size,
                ),
                output_root=output_root,
                identity=identity,
            )


def publish_scoped_freshness_snapshot(
    engine: Engine,
    report: FreshnessReport,
    *,
    output_root: str | Path,
    dataset_version: str,
    config_version: str,
    code_commit: str,
    symbols: tuple[str, ...],
    timeframe: str,
    start: datetime,
    end: datetime,
    boundaries: tuple[SegmentBoundary, ...],
    research_binding: SnapshotResearchBinding,
    batch_size: int = 10_000,
    mapping: CandleSourceMapping | None = None,
    settings: MarketDataSettings | None = None,
) -> SnapshotManifest:
    """Publish only a preregistered healthy discovery/development scope."""
    if not report.coverage_conserved:
        raise ValueError("freshness report does not conserve planned coverage")
    if not symbols or symbols != tuple(sorted(set(symbols))):
        raise ValueError("scoped snapshot symbols must use canonical deterministic ordering")
    if timeframe != "1m":
        raise ValueError("scoped snapshot currently supports the canonical 1m timeframe")
    start_offset = start.utcoffset()
    if start.tzinfo is None or start_offset is None or start_offset.total_seconds() != 0:
        raise ValueError("scoped snapshot start must use UTC")
    end_offset = end.utcoffset()
    if end.tzinfo is None or end_offset is None or end_offset.total_seconds() != 0:
        raise ValueError("scoped snapshot end must use UTC")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if start >= end:
        raise ValueError("scoped snapshot start must precede end")
    cutoff = datetime.fromisoformat(report.as_of.replace("Z", "+00:00"))
    if end > cutoff:
        raise ValueError("scoped snapshot extends beyond the verified freshness cutoff")
    by_key = {(item.symbol, item.timeframe): item for item in report.symbols}
    for symbol in symbols:
        item = by_key.get((symbol, timeframe))
        if item is None:
            raise ValueError("scoped snapshot symbol is absent from freshness evidence")
        if (
            item.status not in _HEALTHY
            or item.compatibility_state is not ProvenanceState.COMPATIBLE
            or not item.current_through_cutoff
        ):
            raise ValueError("scoped snapshot requires healthy compatible current symbols")
    if not isinstance(research_binding, SnapshotResearchBinding):
        raise TypeError("scoped snapshot requires a research provenance binding")
    if segment_boundaries_sha256(boundaries) != research_binding.gap_boundaries_sha256:
        raise ValueError("scoped snapshot gap boundary identity mismatch")
    gap_minutes = 0
    for boundary in boundaries:
        if (
            boundary.symbol not in symbols
            or boundary.timeframe != timeframe
            or boundary.start < start
            or boundary.end > end
        ):
            raise ValueError("scoped snapshot gap boundary is outside the selected scope")
        seconds = int((boundary.end - boundary.start).total_seconds())
        if seconds % 60:
            raise ValueError("scoped snapshot gap boundary must align to canonical minutes")
        gap_minutes += seconds // 60
    total_seconds = int((end - start).total_seconds())
    if total_seconds % 60:
        raise ValueError("scoped snapshot range must align to canonical minutes")
    expected_rows = (total_seconds // 60) * len(symbols) - gap_minutes
    if expected_rows < 1:
        raise ValueError("scoped snapshot must contain at least one expected candle")
    if report.recovery_logical_hash is None:
        raise ValueError("freshness report does not pin the canonical supplement state")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    active_settings = settings or MarketDataSettings.from_env()
    source_mapping = mapping or active_settings.candles
    canonical_mapping = canonical_view_mapping(source_mapping)
    with engine.connect() as raw_connection:
        connection = _snapshot_connection(raw_connection)
        with connection.begin():
            _acquire_snapshot_lock(connection)
            recovery_sha256 = RecoveryRepository(connection).logical_supplement_hash()
            if recovery_sha256 != report.recovery_logical_hash:
                raise ValueError("database supplement state differs from the freshness report")
            identity = build_freshness_snapshot_identity(
                report,
                dataset_version=dataset_version,
                recovery_sha256=recovery_sha256,
                mapping_version=source_mapping.version,
                config_version=config_version,
                code_commit=code_commit,
                policy=SnapshotPublicationPolicy.REQUIRE_HEALTHY,
                research_binding=research_binding,
            )
            return export_partitioned_snapshot(
                _iter_scoped_batches(
                    connection,
                    symbols=symbols,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    mapping=canonical_mapping,
                    batch_size=batch_size,
                ),
                output_root=output_root,
                identity=identity,
                boundaries=boundaries,
                expected_row_count=expected_rows,
            )


def _iter_report_batches(
    connection: Connection,
    report: FreshnessReport,
    *,
    mapping: CandleSourceMapping,
    cutoff: datetime,
    batch_size: int,
) -> Iterator[pl.DataFrame]:
    for item in report.symbols:
        yield from iter_candle_batches(
            symbol=item.symbol,
            timeframe=item.timeframe,
            end=cutoff,
            batch_size=batch_size,
            engine=connection,
            mapping=mapping,
        )


def _iter_scoped_batches(
    connection: Connection,
    *,
    symbols: tuple[str, ...],
    timeframe: str,
    start: datetime,
    end: datetime,
    mapping: CandleSourceMapping,
    batch_size: int,
) -> Iterator[pl.DataFrame]:
    for symbol in symbols:
        yield from iter_candle_batches(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            batch_size=batch_size,
            engine=connection,
            mapping=mapping,
        )


def _snapshot_connection(connection: Connection) -> Connection:
    if connection.dialect.name == "postgresql":
        return connection.execution_options(isolation_level="REPEATABLE READ")
    return connection


def _acquire_snapshot_lock(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": RECOVERY_ADVISORY_LOCK_NAME},
        ).scalar_one()
    )
    if not acquired:
        raise RuntimeError("another candle recovery or snapshot publication is active")


__all__ = [
    "SnapshotPublicationPolicy",
    "build_freshness_snapshot_identity",
    "publish_freshness_snapshot",
    "publish_scoped_freshness_snapshot",
    "validate_snapshot_publication",
]
