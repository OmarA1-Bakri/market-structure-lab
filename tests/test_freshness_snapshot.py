from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.core.config import CandleSourceMapping
from market_structure_lab.data.export import SnapshotResearchBinding, verify_snapshot
from market_structure_lab.data.freshness_snapshot import (
    SnapshotPublicationPolicy,
    build_freshness_snapshot_identity,
    publish_freshness_snapshot,
    publish_scoped_freshness_snapshot,
    validate_snapshot_publication,
)
from market_structure_lab.data.freshness_sync import (
    FreshnessReport,
    FreshnessRunStatus,
    FreshnessSymbolReport,
)
from market_structure_lab.data.gaps import ProvenanceState


def _symbol_report(
    symbol: str = "BTCUSDT",
    *,
    status: FreshnessRunStatus = FreshnessRunStatus.UP_TO_DATE,
    provenance: ProvenanceState = ProvenanceState.COMPATIBLE,
) -> FreshnessSymbolReport:
    return FreshnessSymbolReport(
        symbol=symbol,
        timeframe="1m",
        compatibility_state=provenance,
        before_missing_ranges=(),
        after_missing_ranges=(),
        before_missing_minutes=0,
        after_missing_minutes=0,
        recovered_minutes=0,
        inserted_rows=0,
        status=status,
        reason=f"{status.value} test evidence",
        current_through_cutoff=True,
    )


def _report(*symbols: FreshnessSymbolReport) -> FreshnessReport:
    active = symbols or (_symbol_report(),)
    return FreshnessReport(
        report_version=1,
        manifest_sha256="b" * 64,
        compatibility_manifest_sha256="c" * 64,
        dump_sha256="a" * 64,
        as_of="2026-07-16T12:34:00Z",
        recovery_run_id=None,
        recovery_logical_hash="d" * 64,
        before_missing_minutes=0,
        after_missing_minutes=0,
        recovered_minutes=0,
        inserted_rows=0,
        coverage_conserved=True,
        symbols=tuple(sorted(active, key=lambda item: item.symbol)),
    )


def test_snapshot_publication_requires_healthy_report_by_default() -> None:
    blocked = _report(
        _symbol_report(
            status=FreshnessRunStatus.SOURCE_CONFLICT,
            provenance=ProvenanceState.SOURCE_CONFLICT,
        )
    )

    with pytest.raises(ValueError, match="provenance-blocked"):
        validate_snapshot_publication(blocked, SnapshotPublicationPolicy.REQUIRE_HEALTHY)

    validate_snapshot_publication(
        blocked,
        SnapshotPublicationPolicy.ALLOW_PROVENANCE_BLOCKED,
    )


@pytest.mark.parametrize(
    "status",
    [FreshnessRunStatus.SOURCE_CONFLICT, FreshnessRunStatus.SOURCE_UNAVAILABLE],
)
def test_provenance_policy_rejects_recovery_failures_for_compatible_sources(
    status: FreshnessRunStatus,
) -> None:
    report = _report(
        _symbol_report(
            status=status,
            provenance=ProvenanceState.COMPATIBLE,
        )
    )

    with pytest.raises(ValueError, match="compatibility evidence disagree"):
        validate_snapshot_publication(
            report,
            SnapshotPublicationPolicy.ALLOW_PROVENANCE_BLOCKED,
        )


@pytest.mark.parametrize(
    "status",
    [
        FreshnessRunStatus.PARTIALLY_RECOVERED,
        FreshnessRunStatus.PROVIDER_ABSENT,
        FreshnessRunStatus.FETCH_FAILED,
        FreshnessRunStatus.UNRESOLVED,
    ],
)
def test_provenance_policy_does_not_admit_recovery_failures(status: FreshnessRunStatus) -> None:
    report = _report(_symbol_report(status=status))

    with pytest.raises(ValueError, match=status.value):
        validate_snapshot_publication(
            report,
            SnapshotPublicationPolicy.ALLOW_PROVENANCE_BLOCKED,
        )


def test_snapshot_identity_pins_all_freshness_evidence() -> None:
    report = _report()

    identity = build_freshness_snapshot_identity(
        report,
        dataset_version="canonical-20260716",
        recovery_sha256="d" * 64,
        mapping_version="market-data-candles-v1",
        config_version="freshness-snapshot-v1",
        code_commit="0123456789abcdef",
        policy=SnapshotPublicationPolicy.REQUIRE_HEALTHY,
    )

    assert identity.dump_sha256 == report.dump_sha256
    assert identity.recovery_sha256 == report.recovery_logical_hash
    assert identity.freshness_report_sha256 == report.sha256()
    assert identity.freshness_manifest_sha256 == report.manifest_sha256
    assert identity.compatibility_manifest_sha256 == report.compatibility_manifest_sha256
    assert identity.freshness_as_of == report.as_of
    assert identity.publication_policy == "require_healthy"


def test_snapshot_identity_rejects_partial_or_unknown_freshness_evidence() -> None:
    report = _report()
    identity = build_freshness_snapshot_identity(
        report,
        dataset_version="canonical-20260716",
        recovery_sha256="d" * 64,
        mapping_version="market-data-candles-v1",
        config_version="freshness-snapshot-v1",
        code_commit="0123456789abcdef",
        policy=SnapshotPublicationPolicy.REQUIRE_HEALTHY,
    )

    with pytest.raises(ValueError, match="must be complete"):
        type(identity)(
            dataset_version=identity.dataset_version,
            dump_sha256=identity.dump_sha256.upper(),
            recovery_sha256=identity.recovery_sha256.upper(),
            mapping_version=identity.mapping_version,
            config_version=identity.config_version,
            code_commit=identity.code_commit,
            freshness_report_sha256=identity.freshness_report_sha256,
        )
    with pytest.raises(ValueError, match="unsupported"):
        type(identity)(
            dataset_version=identity.dataset_version,
            dump_sha256=identity.dump_sha256,
            recovery_sha256=identity.recovery_sha256,
            mapping_version=identity.mapping_version,
            config_version=identity.config_version,
            code_commit=identity.code_commit,
            freshness_report_sha256=identity.freshness_report_sha256,
            freshness_manifest_sha256=identity.freshness_manifest_sha256,
            compatibility_manifest_sha256=identity.compatibility_manifest_sha256,
            freshness_as_of=identity.freshness_as_of,
            publication_policy="unknown",
        )


def test_publish_streams_canonical_view_to_existing_immutable_exporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE candles_canonical ("
                "id INTEGER PRIMARY KEY, symbol TEXT, interval TEXT, open_time INTEGER, "
                "open REAL, high REAL, low REAL, close REAL, volume REAL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candles_canonical VALUES "
                "(1, 'BTCUSDT', '1m', 1784205120000, 1, 2, 1, 2, 3), "
                "(2, 'BTCUSDT', '1m', 1784205180000, 2, 3, 2, 3, 4), "
                "(3, 'BTCUSDT', '1m', 1784205240000, 3, 4, 3, 4, 5)"
            )
        )
    monkeypatch.setattr(
        "market_structure_lab.data.freshness_snapshot.RecoveryRepository.logical_supplement_hash",
        lambda _repository: "d" * 64,
    )
    report = _report()
    mapping = CandleSourceMapping(schema="main", table="candles")

    manifest = publish_freshness_snapshot(
        engine,
        report,
        output_root=tmp_path,
        dataset_version="canonical-20260716",
        mapping=mapping,
        config_version="freshness-snapshot-v1",
        code_commit="0123456789abcdef",
        batch_size=1,
    )

    published = tmp_path / "dataset_version=canonical-20260716"
    assert manifest.row_count == 2
    assert manifest.max_timestamp == "2026-07-16T12:33:00Z"
    verify_snapshot(published)

    repeated = publish_freshness_snapshot(
        engine,
        report,
        output_root=tmp_path,
        dataset_version="canonical-20260716",
        mapping=mapping,
        config_version="freshness-snapshot-v1",
        code_commit="0123456789abcdef",
        batch_size=1,
    )
    assert repeated == manifest


def test_publish_rejects_database_state_newer_than_freshness_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    engine = create_engine("sqlite://")
    monkeypatch.setattr(
        "market_structure_lab.data.freshness_snapshot.RecoveryRepository.logical_supplement_hash",
        lambda _repository: "e" * 64,
    )

    with pytest.raises(ValueError, match="supplement state"):
        publish_freshness_snapshot(
            engine,
            _report(),
            output_root=tmp_path,
            dataset_version="stale-report",
            mapping=CandleSourceMapping(schema="main", table="candles"),
            config_version="freshness-snapshot-v1",
            code_commit="0123456789abcdef",
        )


def test_scoped_publish_admits_only_selected_healthy_series_and_binds_research_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE candles_canonical ("
                "id INTEGER PRIMARY KEY, symbol TEXT, interval TEXT, open_time INTEGER, "
                "open REAL, high REAL, low REAL, close REAL, volume REAL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candles_canonical VALUES "
                "(1, 'BTCUSDT', '1m', 1784205120000, 1, 2, 1, 2, 3), "
                "(2, 'BTCUSDT', '1m', 1784205180000, 2, 3, 2, 3, 4), "
                "(3, 'BTCUSDT', '1m', 1784205240000, 3, 4, 3, 4, 5)"
            )
        )
    monkeypatch.setattr(
        "market_structure_lab.data.freshness_snapshot.RecoveryRepository.logical_supplement_hash",
        lambda _repository: "d" * 64,
    )
    report = _report(
        _symbol_report("BTCUSDT"),
        _symbol_report(
            "ETHUSDT",
            status=FreshnessRunStatus.PROVIDER_ABSENT,
            provenance=ProvenanceState.COMPATIBLE,
        ),
    )
    binding = SnapshotResearchBinding(
        selected_universe_sha256="1" * 64,
        promotion_receipt_content_sha256="2" * 64,
        promotion_receipt_artifact_sha256="3" * 64,
        promotion_canonical_logical_sha256="4" * 64,
        gap_boundaries_sha256=hashlib.sha256(b"[]").hexdigest(),
        eligibility_audit_sha256="5" * 64,
    )

    manifest = publish_scoped_freshness_snapshot(
        engine,
        report,
        output_root=tmp_path,
        dataset_version="task14-selected-v1",
        config_version="task14-snapshot-v1",
        code_commit="0123456789abcdef",
        symbols=("BTCUSDT",),
        timeframe="1m",
        start=datetime(2026, 7, 16, 12, 32, tzinfo=UTC),
        end=datetime(2026, 7, 16, 12, 34, tzinfo=UTC),
        boundaries=(),
        research_binding=binding,
        mapping=CandleSourceMapping(schema="main", table="candles"),
        batch_size=1,
    )

    assert manifest.row_count == 2
    assert manifest.identity.research_binding == binding
    assert manifest.min_timestamp == "2026-07-16T12:32:00Z"
    assert manifest.max_timestamp == "2026-07-16T12:33:00Z"
    verify_snapshot(tmp_path / "dataset_version=task14-selected-v1")
