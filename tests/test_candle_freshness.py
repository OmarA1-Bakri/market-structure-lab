from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from market_structure_lab.data.freshness import (
    FreshnessPlanningStatus,
    build_freshness_manifest,
    read_freshness_manifest,
    resolve_freshness_cutoff,
    write_freshness_manifest,
)
from market_structure_lab.data.gaps import (
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)

DUMP_IDENTITY = SourceIdentity("a" * 64, 35_748_117, "callscore-crypto-v1")


@pytest.fixture
def canonical_connection():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE candles_canonical (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open_time BIGINT NOT NULL
                )
                """
            )
        )
        yield connection
    engine.dispose()


def _insert(connection, symbol: str, timestamps: list[int], timeframe: str = "1m") -> None:
    connection.execute(
        text("INSERT INTO candles_canonical VALUES (:symbol, :timeframe, :open_time)"),
        [
            {"symbol": symbol, "timeframe": timeframe, "open_time": timestamp}
            for timestamp in timestamps
        ],
    )


def _reviewed_compatibility(
    states: dict[str, ProvenanceState],
    *,
    dump_identity: SourceIdentity = DUMP_IDENTITY,
    candidate_venue: str = "binance",
    market_type: str = "spot",
) -> RecoveryManifest:
    return RecoveryManifest(
        manifest_version=1,
        source_identity=dump_identity,
        as_of=datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        candidate_venue=candidate_venue,
        market_type=market_type,
        envelopes=tuple(ObservedEnvelope(symbol, "1m", 0, 60_000, 2) for symbol in sorted(states)),
        gaps=(),
        provenance_validation=states,
    )


def _build(connection, states: dict[str, ProvenanceState], *, as_of: datetime):
    compatibility = _reviewed_compatibility(states)
    return build_freshness_manifest(
        connection,
        dump_identity=DUMP_IDENTITY,
        compatibility_manifest=compatibility,
        compatibility_manifest_sha256=compatibility.sha256(),
        as_of=as_of,
        schema=None,
    )


def test_cutoff_uses_start_of_current_utc_minute_and_validates_replay_cutoffs() -> None:
    now = datetime(
        2026,
        7,
        16,
        19,
        34,
        59,
        999_999,
        tzinfo=timezone(timedelta(hours=7)),
    )

    assert resolve_freshness_cutoff(now=now) == datetime(2026, 7, 16, 12, 34, tzinfo=UTC)
    assert resolve_freshness_cutoff(as_of=datetime(2026, 7, 16, 12, 34, tzinfo=UTC)) == datetime(
        2026, 7, 16, 12, 34, tzinfo=UTC
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_freshness_cutoff(as_of=datetime(2026, 7, 16, 12, 34))
    with pytest.raises(ValueError, match="minute-aligned"):
        resolve_freshness_cutoff(as_of=datetime(2026, 7, 16, 12, 34, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="either now or as_of"):
        resolve_freshness_cutoff(now=now, as_of=now)


def test_planning_includes_internal_gaps_and_tail_from_canonical_view(
    canonical_connection,
) -> None:
    _insert(canonical_connection, "BTCUSDT", [0, 120_000, 180_000])

    manifest = _build(
        canonical_connection,
        {"BTCUSDT": ProvenanceState.COMPATIBLE},
        as_of=datetime.fromtimestamp(360, tz=UTC),
    )

    plan = manifest.symbols[0]
    assert plan.status is FreshnessPlanningStatus.FETCH_REQUIRED
    assert [(gap.start_ms, gap.end_ms, gap.expected_minutes) for gap in plan.missing_ranges] == [
        (60_000, 120_000, 1),
        (240_000, 360_000, 2),
    ]
    assert plan.eligible_ranges == plan.missing_ranges
    assert plan.canonical_state.row_count == 3
    assert plan.canonical_state.missing_minutes == 3
    assert manifest.canonical_row_count == 3
    assert manifest.dump_identity.source_row_count == 35_748_117
    assert (
        manifest.compatibility_manifest_sha256
        == _reviewed_compatibility({"BTCUSDT": ProvenanceState.COMPATIBLE}).sha256()
    )


def test_current_symbol_has_no_ranges(canonical_connection) -> None:
    _insert(canonical_connection, "BTCUSDT", [0, 60_000, 120_000])

    manifest = _build(
        canonical_connection,
        {"BTCUSDT": ProvenanceState.COMPATIBLE},
        as_of=datetime.fromtimestamp(180, tz=UTC),
    )

    plan = manifest.symbols[0]
    assert plan.status is FreshnessPlanningStatus.UP_TO_DATE
    assert plan.missing_ranges == ()
    assert plan.eligible_ranges == ()


def test_every_symbol_has_one_status_and_blocked_sources_have_no_eligible_ranges(
    canonical_connection,
) -> None:
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        _insert(canonical_connection, symbol, [0])
    states = {
        "BTCUSDT": ProvenanceState.COMPATIBLE,
        "ETHUSDT": ProvenanceState.PENDING,
        "SOLUSDT": ProvenanceState.SOURCE_CONFLICT,
        "XRPUSDT": ProvenanceState.SOURCE_UNAVAILABLE,
    }

    manifest = _build(
        canonical_connection,
        states,
        as_of=datetime.fromtimestamp(180, tz=UTC),
    )

    plans = {plan.symbol: plan for plan in manifest.symbols}
    assert tuple(plans) == tuple(sorted(states))
    assert plans["BTCUSDT"].status is FreshnessPlanningStatus.FETCH_REQUIRED
    assert plans["ETHUSDT"].status is FreshnessPlanningStatus.PROVENANCE_PENDING
    assert plans["SOLUSDT"].status is FreshnessPlanningStatus.SOURCE_CONFLICT
    assert plans["XRPUSDT"].status is FreshnessPlanningStatus.SOURCE_UNAVAILABLE
    assert plans["BTCUSDT"].eligible_ranges
    for symbol in ("ETHUSDT", "SOLUSDT", "XRPUSDT"):
        assert plans[symbol].missing_ranges
        assert plans[symbol].eligible_ranges == ()
        assert plans[symbol].reason


def test_provenance_must_cover_canonical_symbols_exactly(canonical_connection) -> None:
    _insert(canonical_connection, "BTCUSDT", [0])

    with pytest.raises(ValueError, match="cover canonical symbols exactly"):
        compatibility = _reviewed_compatibility({})
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=datetime.fromtimestamp(60, tz=UTC),
            schema=None,
        )
    with pytest.raises(ValueError, match="cover canonical symbols exactly"):
        compatibility = _reviewed_compatibility(
            {
                "BTCUSDT": ProvenanceState.COMPATIBLE,
                "ETHUSDT": ProvenanceState.COMPATIBLE,
            }
        )
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=datetime.fromtimestamp(60, tz=UTC),
            schema=None,
        )


def test_freshness_requires_hash_pinned_compatibility_evidence(canonical_connection) -> None:
    _insert(canonical_connection, "BTCUSDT", [0])
    reviewed = _reviewed_compatibility({"BTCUSDT": ProvenanceState.PENDING})
    forged = replace(
        reviewed,
        provenance_validation={"BTCUSDT": ProvenanceState.COMPATIBLE},
    )

    with pytest.raises(TypeError, match="provenance_states"):
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=reviewed,
            compatibility_manifest_sha256=reviewed.sha256(),
            provenance_states={"BTCUSDT": ProvenanceState.COMPATIBLE},
            as_of=datetime.fromtimestamp(120, tz=UTC),
            schema=None,
        )
    with pytest.raises(ValueError, match="reviewed compatibility checksum"):
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=forged,
            compatibility_manifest_sha256=reviewed.sha256(),
            as_of=datetime.fromtimestamp(120, tz=UTC),
            schema=None,
        )


@pytest.mark.parametrize(
    ("compatibility", "kwargs", "message"),
    [
        (
            _reviewed_compatibility(
                {"BTCUSDT": ProvenanceState.COMPATIBLE},
                dump_identity=SourceIdentity("b" * 64, 35_748_117, "callscore-crypto-v1"),
            ),
            {},
            "dump identity",
        ),
        (
            _reviewed_compatibility(
                {"BTCUSDT": ProvenanceState.COMPATIBLE}, candidate_venue="kraken"
            ),
            {},
            "candidate venue",
        ),
        (
            _reviewed_compatibility({"BTCUSDT": ProvenanceState.COMPATIBLE}, market_type="futures"),
            {},
            "market type",
        ),
    ],
)
def test_compatibility_identity_and_market_must_match_freshness_request(
    canonical_connection, compatibility, kwargs, message
) -> None:
    _insert(canonical_connection, "BTCUSDT", [0])

    with pytest.raises(ValueError, match=message):
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=datetime.fromtimestamp(120, tz=UTC),
            schema=None,
            **kwargs,
        )


def test_manifest_serialization_hash_and_tamper_detection_are_deterministic(
    canonical_connection, tmp_path
) -> None:
    _insert(canonical_connection, "BTCUSDT", [0, 120_000])
    manifest = _build(
        canonical_connection,
        {"BTCUSDT": ProvenanceState.COMPATIBLE},
        as_of=datetime.fromtimestamp(180, tz=UTC),
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_freshness_manifest(manifest, first)
    write_freshness_manifest(manifest, second)

    assert first.read_bytes() == second.read_bytes()
    assert read_freshness_manifest(first) == manifest
    assert len(manifest.sha256()) == 64

    envelope = json.loads(first.read_text(encoding="utf-8"))
    envelope["manifest"]["canonical_row_count"] += 1
    first.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        read_freshness_manifest(first)


def test_empty_duplicate_invalid_bounds_and_unsupported_timeframes_fail_loudly(
    canonical_connection,
) -> None:
    as_of = datetime.fromtimestamp(180, tz=UTC)
    with pytest.raises(ValueError, match="no canonical"):
        compatibility = _reviewed_compatibility({})
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=as_of,
            schema=None,
        )

    _insert(canonical_connection, "BTCUSDT", [0, 0])
    with pytest.raises(ValueError, match="duplicate"):
        compatibility = _reviewed_compatibility({"BTCUSDT": ProvenanceState.COMPATIBLE})
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=as_of,
            schema=None,
        )

    canonical_connection.execute(text("DELETE FROM candles_canonical"))
    _insert(canonical_connection, "BTCUSDT", [180_000])
    with pytest.raises(ValueError, match="before the cutoff"):
        compatibility = _reviewed_compatibility({"BTCUSDT": ProvenanceState.COMPATIBLE})
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=as_of,
            schema=None,
        )

    with pytest.raises(ValueError, match="only 1m"):
        compatibility = _reviewed_compatibility({"BTCUSDT": ProvenanceState.COMPATIBLE})
        build_freshness_manifest(
            canonical_connection,
            dump_identity=DUMP_IDENTITY,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=compatibility.sha256(),
            as_of=as_of,
            timeframe="5m",
            schema=None,
        )
