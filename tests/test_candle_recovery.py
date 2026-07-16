from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from datetime import UTC, datetime

import pytest

from market_structure_lab.data.gaps import (
    GapRange,
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)
from market_structure_lab.data.recovery import (
    GapResolution,
    RecoveryCandle,
    RecoveryCheckpoint,
    RecoveryValidationError,
    classify_gap,
    coalesce_requests,
    compare_source_compatibility,
    distributed_sample_indices,
    group_gaps_by_series,
    resume_fetch_request,
    summarize_recovery_evidence,
    validate_recovery_batch,
    verify_resolution_coverage,
)
from market_structure_lab.data.sources.base import FetchRequest, SourceKline


def _source(timestamp: int, **overrides) -> SourceKline:
    values = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "open_time_ms": timestamp,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("2"),
        "quote_volume": Decimal("200"),
        "trades": 10,
    }
    values.update(overrides)
    return SourceKline(**values)


def test_validation_sorts_and_hashes_a_bounded_batch_deterministically() -> None:
    request = FetchRequest("BTCUSDT", "1m", 0, 180_000)
    first = validate_recovery_batch([_source(60_000), _source(0)], request)
    second = validate_recovery_batch([_source(0), _source(60_000)], request)
    assert [row.open_time_ms for row in first.rows] == [0, 60_000]
    assert first.logical_checksum == second.logical_checksum


@pytest.mark.parametrize(
    "rows, message",
    [
        ([_source(0), _source(0)], "duplicate"),
        ([_source(180_000)], "outside"),
        ([_source(0, symbol="ETHUSDT")], "boundary"),
    ],
)
def test_validation_rejects_duplicates_bounds_and_market_crossing(rows, message) -> None:
    with pytest.raises(RecoveryValidationError, match=message):
        validate_recovery_batch(rows, FetchRequest("BTCUSDT", "1m", 0, 180_000))


def test_validation_rejects_invalid_recovery_values_even_if_constructed_directly() -> None:
    invalid = RecoveryCandle(
        "BTCUSDT",
        "1m",
        0,
        Decimal("100"),
        Decimal("98"),
        Decimal("99"),
        Decimal("101"),
        Decimal("2"),
    )
    with pytest.raises(RecoveryValidationError, match="OHLC"):
        validate_recovery_batch([invalid], FetchRequest("BTCUSDT", "1m", 0, 60_000))


def test_distributed_samples_include_beginning_middle_end_and_fragments() -> None:
    assert distributed_sample_indices(0) == ()
    assert distributed_sample_indices(4) == (0, 1, 2, 3)
    indices = distributed_sample_indices(1_000)
    assert len(indices) == 100 and indices[0] == 0 and indices[-1] == 999


def test_compatibility_requires_all_fragment_rows_and_100_for_large_sources() -> None:
    dump = [RecoveryCandle.from_source(_source(index * 60_000)) for index in range(4)]
    compatible = compare_source_compatibility(dump, [_source(index * 60_000) for index in range(4)])
    assert compatible.compatible and compatible.limited_evidence
    missing = compare_source_compatibility(dump, [_source(index * 60_000) for index in range(3)])
    assert not missing.compatible
    assert "insufficient_source_samples:3/4" in missing.mismatches

    insufficient = compare_source_compatibility(
        dump, list(map(_source, range(0, 240_000, 60_000))), existing_rows=1_000
    )
    assert not insufficient.compatible
    assert "insufficient_dump_samples:4/100" in insufficient.mismatches


def test_compatibility_compares_optional_fields_only_when_present_in_dump() -> None:
    source = _source(0, quote_volume=Decimal("999"), trades=999)
    absent_optional = RecoveryCandle.from_source(_source(0))
    absent_optional = replace(absent_optional, quote_volume=None, trades=None)
    assert compare_source_compatibility([absent_optional], [source]).compatible
    mismatch = compare_source_compatibility([RecoveryCandle.from_source(_source(0))], [source])
    assert not mismatch.compatible
    assert "quote_volume" in mismatch.mismatches[0]


def test_coalescing_never_crosses_symbol_or_timeframe_and_stays_bounded() -> None:
    requests = [
        FetchRequest("BTCUSDT", "1m", 0, 60_000),
        FetchRequest("BTCUSDT", "1m", 60_000, 120_000),
        FetchRequest("ETHUSDT", "1m", 0, 60_000),
    ]
    combined = coalesce_requests(requests, maximum_minutes=2)
    assert combined == (
        FetchRequest("BTCUSDT", "1m", 0, 120_000),
        FetchRequest("ETHUSDT", "1m", 0, 60_000),
    )
    assert len(coalesce_requests(requests[:2], maximum_minutes=1)) == 2


def test_recovery_parallelism_shards_series_without_crossing_gap_boundaries() -> None:
    later = GapRange.create("BTCUSDT", "1m", 180_000, 240_000, 1)
    earlier = GapRange.create("BTCUSDT", "1m", 60_000, 120_000, 1)
    eth = GapRange.create("ETHUSDT", "1m", 60_000, 120_000, 1)

    groups = group_gaps_by_series((later, eth, earlier), lanes_per_series=2)

    assert groups == ((earlier,), (later,), (eth,))
    assert sorted(gap.gap_id for group in groups for gap in group) == sorted(
        (earlier.gap_id, later.gap_id, eth.gap_id)
    )


def test_interrupted_gap_resumes_after_the_last_completed_batch() -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 300_000, 4)

    resumed = resume_fetch_request(
        gap,
        RecoveryCheckpoint(
            next_start_ms=180_000,
            authoritative_empty=True,
        ),
    )

    assert resumed == FetchRequest("BTCUSDT", "1m", 180_000, 300_000)
    assert (
        resume_fetch_request(
            gap,
            RecoveryCheckpoint(
                next_start_ms=300_000,
                authoritative_empty=False,
            ),
        )
        is None
    )


def test_resume_checkpoint_must_stay_inside_its_frozen_gap() -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 180_000, 2)

    with pytest.raises(ValueError, match="checkpoint"):
        resume_fetch_request(
            gap,
            RecoveryCheckpoint(
                next_start_ms=0,
                authoritative_empty=False,
            ),
        )


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"recovered_minutes": 2}, GapResolution.RECOVERED),
        ({"recovered_minutes": 1}, GapResolution.PARTIALLY_RECOVERED),
        ({"recovered_minutes": 0, "authoritative_empty": True}, GapResolution.PROVIDER_ABSENT),
        ({"recovered_minutes": 0, "unavailable": True}, GapResolution.SOURCE_UNAVAILABLE),
        ({"recovered_minutes": 0, "conflict": True}, GapResolution.SOURCE_CONFLICT),
        ({"recovered_minutes": 0, "validation_error": "bad"}, GapResolution.UNRESOLVED),
        ({"recovered_minutes": 0, "retrieval_error": "down"}, GapResolution.FETCH_FAILED),
    ],
)
def test_every_gap_ends_with_an_explicit_terminal_classification(kwargs, expected) -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 180_000, 2)
    assert classify_gap(gap, **kwargs).resolution == expected


def test_resolution_coverage_requires_exactly_one_terminal_row_per_manifest_gap() -> None:
    gap = GapRange.create("BTCUSDT", "1m", 60_000, 180_000, 2)
    manifest = RecoveryManifest(
        manifest_version=1,
        source_identity=SourceIdentity("a" * 64, 2, "v1"),
        as_of=datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        candidate_venue="binance",
        market_type="spot",
        envelopes=(ObservedEnvelope("BTCUSDT", "1m", 0, 180_000, 2),),
        gaps=(gap,),
        provenance_validation={"BTCUSDT": ProvenanceState.COMPATIBLE},
    )
    terminal = {
        "gap_id": gap.gap_id,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "resolution": "recovered",
        "expected_minutes": 2,
        "recovered_minutes": 2,
    }
    verify_resolution_coverage(manifest, (terminal,))
    with pytest.raises(ValueError, match="missing terminal gap resolutions"):
        verify_resolution_coverage(manifest, ())
    with pytest.raises(ValueError, match="does not match the frozen manifest"):
        verify_resolution_coverage(manifest, ({**terminal, "expected_minutes": 1},))


def test_recovery_report_enforces_coverage_conservation_and_source_immutability() -> None:
    gaps = (
        GapRange.create("BTCUSDT", "1m", 60_000, 180_000, 2),
        GapRange.create("ETHUSDT", "1m", 120_000, 180_000, 1),
    )
    manifest = RecoveryManifest(
        manifest_version=1,
        source_identity=SourceIdentity("a" * 64, 17, "v1"),
        as_of=datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        candidate_venue="binance",
        market_type="spot",
        envelopes=(
            ObservedEnvelope("BTCUSDT", "1m", 0, 180_000, 10),
            ObservedEnvelope("ETHUSDT", "1m", 0, 180_000, 7),
        ),
        gaps=gaps,
        provenance_validation={
            "BTCUSDT": ProvenanceState.COMPATIBLE,
            "ETHUSDT": ProvenanceState.COMPATIBLE,
        },
    )
    resolutions = (
        {
            "symbol": "BTCUSDT",
            "resolution": "partially_recovered",
            "expected_minutes": 2,
            "recovered_minutes": 1,
        },
        {
            "symbol": "ETHUSDT",
            "resolution": "source_unavailable",
            "expected_minutes": 1,
            "recovered_minutes": 0,
        },
    )
    report = summarize_recovery_evidence(
        manifest,
        source_row_count=17,
        recovered_by_symbol={"BTCUSDT": 1},
        resolutions=resolutions,
        payload_checksums=("b" * 64, "b" * 64),
        batch_error_count=1,
    )
    assert report["before_missing_minutes"] == 3
    assert report["added_unique_rows"] == 1
    assert report["after_missing_minutes"] == 2
    assert report["unresolved_gap_count"] == 2
    assert report["largest_remaining_gap_minutes"] == 1
    assert report["source_checksum_count"] == 1
    assert report["error_count"] == 2
    assert report["source_row_count_unchanged"] is True

    with pytest.raises(ValueError, match="source row count changed"):
        summarize_recovery_evidence(
            manifest,
            source_row_count=18,
            recovered_by_symbol={},
            resolutions=(),
            payload_checksums=(),
            batch_error_count=0,
        )
