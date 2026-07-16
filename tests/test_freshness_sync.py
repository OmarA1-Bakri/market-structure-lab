from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from market_structure_lab.data.freshness import (
    CanonicalSeriesState,
    FreshnessManifest,
    FreshnessPlanningStatus,
    FreshnessSymbolPlan,
)
from market_structure_lab.data.freshness_sync import (
    FreshnessRunStatus,
    build_freshness_recovery_manifest,
    build_freshness_report,
    freshness_exit_code,
    read_freshness_report,
    read_latest_freshness_report,
    run_freshness_sync,
    write_freshness_artifacts,
)
from market_structure_lab.data.gaps import (
    GapRange,
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)
from market_structure_lab.data.recovery import GapResolution, RunSummary


DUMP = SourceIdentity("a" * 64, 4, "market-data-candles-v1")
CUTOFF = "2026-07-16T12:34:00Z"


def _compatibility() -> RecoveryManifest:
    return RecoveryManifest(
        manifest_version=1,
        source_identity=DUMP,
        as_of=CUTOFF,
        candidate_venue="binance",
        market_type="spot",
        envelopes=(
            ObservedEnvelope("BTCUSDT", "1m", 0, 60_000, 2),
            ObservedEnvelope("ETHUSDT", "1m", 0, 0, 1),
            ObservedEnvelope("XRPUSDT", "1m", 0, 60_000, 2),
        ),
        gaps=(),
        provenance_validation={
            "BTCUSDT": ProvenanceState.COMPATIBLE,
            "ETHUSDT": ProvenanceState.SOURCE_CONFLICT,
            "XRPUSDT": ProvenanceState.COMPATIBLE,
        },
    )


def _plan(
    symbol: str,
    state: ProvenanceState,
    timestamps: tuple[int, int],
    row_count: int,
    gaps: tuple[GapRange, ...],
) -> FreshnessSymbolPlan:
    missing = sum(gap.expected_minutes for gap in gaps)
    if state is ProvenanceState.COMPATIBLE:
        status = (
            FreshnessPlanningStatus.FETCH_REQUIRED
            if missing
            else FreshnessPlanningStatus.UP_TO_DATE
        )
    elif state is ProvenanceState.SOURCE_CONFLICT:
        status = FreshnessPlanningStatus.SOURCE_CONFLICT
    elif state is ProvenanceState.SOURCE_UNAVAILABLE:
        status = FreshnessPlanningStatus.SOURCE_UNAVAILABLE
    else:
        status = FreshnessPlanningStatus.PROVENANCE_PENDING
    return FreshnessSymbolPlan(
        symbol=symbol,
        timeframe="1m",
        provenance_state=state,
        status=status,
        reason=f"{status.value} reason",
        canonical_state=CanonicalSeriesState(
            symbol=symbol,
            timeframe="1m",
            first_open_time_ms=timestamps[0],
            last_open_time_ms=timestamps[1],
            row_count=row_count,
            missing_minutes=missing,
        ),
        missing_ranges=gaps,
    )


def _manifest(*, recovered: bool = False) -> FreshnessManifest:
    btc_gap = () if recovered else (GapRange.create("BTCUSDT", "1m", 120_000, 180_000, 1),)
    eth_gap = (GapRange.create("ETHUSDT", "1m", 60_000, 180_000, 2),)
    plans = (
        _plan(
            "BTCUSDT",
            ProvenanceState.COMPATIBLE,
            (0, 120_000 if recovered else 60_000),
            3 if recovered else 2,
            btc_gap,
        ),
        _plan("ETHUSDT", ProvenanceState.SOURCE_CONFLICT, (0, 0), 1, eth_gap),
        _plan("XRPUSDT", ProvenanceState.COMPATIBLE, (0, 120_000), 3, ()),
    )
    compatibility = _compatibility()
    return FreshnessManifest(
        manifest_version=1,
        dump_identity=DUMP,
        compatibility_manifest_sha256=compatibility.sha256(),
        canonical_row_count=sum(item.canonical_state.row_count for item in plans),
        as_of=CUTOFF,
        candidate_venue="binance",
        market_type="spot",
        symbols=plans,
    )


def test_recovery_manifest_contains_only_eligible_ranges_and_preserves_all_identities() -> None:
    freshness = _manifest()
    compatibility = _compatibility()

    recovery = build_freshness_recovery_manifest(freshness, compatibility)

    assert [gap.symbol for gap in recovery.gaps] == ["BTCUSDT"]
    assert [item.symbol for item in recovery.envelopes] == ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    assert recovery.provenance_validation == compatibility.provenance_validation
    assert recovery.source_identity == freshness.dump_identity
    assert recovery.as_of == freshness.as_of


def test_recovery_manifest_rejects_a_different_or_tampered_compatibility_artifact() -> None:
    freshness = _manifest()
    compatibility = _compatibility()
    forged = replace(
        compatibility,
        provenance_validation={
            **compatibility.provenance_validation,
            "ETHUSDT": ProvenanceState.COMPATIBLE,
        },
    )

    with pytest.raises(ValueError, match="reviewed compatibility"):
        build_freshness_recovery_manifest(freshness, forged)


def test_report_conserves_minutes_and_includes_current_recovered_and_blocked_symbols() -> None:
    before = _manifest()
    after = _manifest(recovered=True)

    report = build_freshness_report(
        before,
        after,
        inserted_by_symbol={"BTCUSDT": 1},
        resolutions_by_symbol={"BTCUSDT": (GapResolution.RECOVERED,)},
        recovery_run_id=uuid.UUID(int=7),
        recovery_logical_hash="b" * 64,
    )

    symbols = {item.symbol: item for item in report.symbols}
    assert tuple(item.symbol for item in report.symbols) == ("BTCUSDT", "ETHUSDT", "XRPUSDT")
    assert symbols["BTCUSDT"].status is FreshnessRunStatus.RECOVERED
    assert symbols["BTCUSDT"].before_missing_minutes == 1
    assert symbols["BTCUSDT"].after_missing_minutes == 0
    assert symbols["BTCUSDT"].inserted_rows == 1
    assert symbols["BTCUSDT"].current_through_cutoff is True
    assert symbols["ETHUSDT"].status is FreshnessRunStatus.SOURCE_CONFLICT
    assert symbols["ETHUSDT"].current_through_cutoff is False
    assert symbols["XRPUSDT"].status is FreshnessRunStatus.UP_TO_DATE
    assert report.before_missing_minutes == 3
    assert report.after_missing_minutes == 2
    assert report.recovered_minutes == 1
    assert report.coverage_conserved is True
    assert freshness_exit_code(report) == 2


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        (GapResolution.PARTIALLY_RECOVERED, FreshnessRunStatus.PARTIALLY_RECOVERED),
        (GapResolution.PROVIDER_ABSENT, FreshnessRunStatus.PROVIDER_ABSENT),
        (GapResolution.FETCH_FAILED, FreshnessRunStatus.FETCH_FAILED),
        (GapResolution.UNRESOLVED, FreshnessRunStatus.UNRESOLVED),
    ],
)
def test_report_retains_explicit_unresolved_recovery_reason(
    resolution: GapResolution, expected: FreshnessRunStatus
) -> None:
    before = _manifest()

    report = build_freshness_report(
        before,
        before,
        inserted_by_symbol={},
        resolutions_by_symbol={"BTCUSDT": (resolution,)},
    )

    btc = report.symbols[0]
    assert btc.status is expected
    assert resolution.value in btc.reason
    assert btc.current_through_cutoff is False
    assert freshness_exit_code(report) == 2


def test_current_report_exits_zero() -> None:
    current = _manifest(recovered=True)
    current_symbols = tuple(
        item for item in current.symbols if item.provenance_state is ProvenanceState.COMPATIBLE
    )
    current = replace(
        current,
        canonical_row_count=sum(item.canonical_state.row_count for item in current_symbols),
        symbols=current_symbols,
    )
    report = build_freshness_report(current, current)

    assert all(item.current_through_cutoff for item in report.symbols)
    assert freshness_exit_code(report) == 0


def test_complete_but_source_blocked_symbol_keeps_scheduler_unhealthy() -> None:
    current = _manifest(recovered=True)
    eth = _plan("ETHUSDT", ProvenanceState.SOURCE_CONFLICT, (0, 120_000), 3, ())
    symbols = tuple(eth if item.symbol == "ETHUSDT" else item for item in current.symbols)
    blocked = replace(
        current,
        canonical_row_count=sum(item.canonical_state.row_count for item in symbols),
        symbols=symbols,
    )

    report = build_freshness_report(blocked, blocked)

    assert report.symbols[1].status is FreshnessRunStatus.SOURCE_CONFLICT
    assert report.symbols[1].current_through_cutoff is True
    assert freshness_exit_code(report) == 2


def test_report_and_latest_pointer_are_checksum_bearing_and_tamper_evident(
    tmp_path: Path,
) -> None:
    before = _manifest()
    report = build_freshness_report(before, _manifest(recovered=True))

    paths = write_freshness_artifacts(before, report, tmp_path)

    assert paths.manifest.exists()
    assert paths.report.exists()
    assert paths.latest == tmp_path / "latest.json"
    assert read_freshness_report(paths.report) == report
    assert read_latest_freshness_report(tmp_path) == report
    latest = json.loads(paths.latest.read_text(encoding="utf-8"))
    assert latest["manifest_sha256"] == before.sha256()
    assert latest["report_sha256"] == report.sha256()

    envelope = json.loads(paths.report.read_text(encoding="utf-8"))
    envelope["report"]["after_missing_minutes"] += 1
    paths.report.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        read_freshness_report(paths.report)


def test_runner_reuses_existing_recovery_engine_and_replans_same_cutoff(monkeypatch) -> None:
    from market_structure_lab.data import freshness_sync

    before = _manifest()
    after = _manifest(recovered=True)
    compatibility = _compatibility()
    source = object()
    calls: dict[str, object] = {}

    def fake_run(engine, recovery, actual_source):
        calls["engine"] = engine
        calls["recovery"] = recovery
        calls["source"] = actual_source
        return RunSummary(uuid.UUID(int=9), recovery.sha256(), 1, 1, 1, "c" * 64)

    monkeypatch.setattr(freshness_sync, "run_recovery", fake_run)
    monkeypatch.setattr(freshness_sync, "_replan_freshness", lambda *args, **kwargs: after)
    monkeypatch.setattr(
        freshness_sync,
        "_load_recovery_evidence",
        lambda *args, **kwargs: ({"BTCUSDT": 1}, {"BTCUSDT": (GapResolution.RECOVERED,)}),
    )
    engine = object()

    report = run_freshness_sync(engine, before, compatibility, source)

    assert calls["engine"] is engine
    assert calls["source"] is source
    assert [gap.symbol for gap in calls["recovery"].gaps] == ["BTCUSDT"]
    assert report.as_of == before.as_of
    assert report.symbols[0].status is FreshnessRunStatus.RECOVERED
