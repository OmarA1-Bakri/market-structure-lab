"""Explicit immutable snapshot handoff from checksum-verified freshness evidence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import polars as pl
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from market_structure_lab.core.config import CandleSourceMapping, MarketDataSettings
from market_structure_lab.data.export import (
    SnapshotIdentity,
    SnapshotManifest,
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
    "validate_snapshot_publication",
]
