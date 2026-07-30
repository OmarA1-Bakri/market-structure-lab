from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentAccessAttemptLedgerV2,
    DevelopmentReadBoundaryV2,
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
    open_development_consumer_v2,
    verify_development_read_boundary_v2,
)


def _coverage() -> SourceCoveragePublicationV2:
    entries = tuple(
        SourceCoverageEntryV2(
            symbol=symbol,
            complete_start=datetime(2024, 1, 1, tzinfo=UTC),
            complete_end=datetime(2025, 1, 1, tzinfo=UTC),
            timeframes=("1m", "1h", "4h"),
            source_conflict=False,
            mapping_compatible=True,
            metadata_sha256=str(index) * 64,
        )
        for index, symbol in enumerate(
            ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT"), start=1
        )
    )
    return SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2(
            dump_sha256="a" * 64,
            byte_count=1,
            pg_restore_list_sha256="b" * 64,
            candle_table_toc_identity="public.candles:table-data:42",
            source_mapping_version="callscore-candles-v1",
        ),
        reconciliation=ReconciliationAuthorityV2(
            promotion_receipt_sha256="c" * 64,
            work_unit_manifest_sha256=("d" * 64,),
            comparison_part_sha256=("e" * 64,),
            replacement_source_policy="rr-000008-promoted-only",
        ),
        compatibility_metadata_sha256="f" * 64,
        entries=entries,
    )


def _issued():
    coverage = _coverage()
    split = freeze_development_split_v2(
        coverage=coverage,
        policy=SplitPolicyV2(
            block_count=6,
            minimum_complete_days=180,
            asset_holdout_fraction_numerator=1,
            asset_holdout_fraction_denominator=5,
            asset_holdout_salt="market-structure-lab-phase5-v2-public-salt",
            purge_hours=24,
            embargo_hours=24,
            timeframes=("1m", "1h", "4h"),
        ),
    )
    return coverage, split, issue_development_read_boundary_v2(coverage, split)


def _allowed_request(boundary: DevelopmentReadBoundaryV2) -> BoundaryRequestV2:
    interval = boundary.allowed_intervals[0]
    return BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=interval.start,
        end=min(interval.end, interval.start + timedelta(days=1)),
        operation_kind=AccessOperationKindV2.FILE,
        target_identity="fixture-source",
    )


def test_boundary_is_factory_issued_immutable_and_verifiable() -> None:
    coverage, split, boundary = _issued()

    assert verify_development_read_boundary_v2(boundary, coverage, split) == boundary
    assert boundary.allowed_symbols == split.development_symbols
    assert boundary.allowed_intervals == tuple(
        (block.start, block.end) for block in split.development_blocks
    )
    assert boundary.forbidden_asset_symbols == split.asset_holdout_symbols
    assert boundary.forbidden_temporal_intervals == (
        (split.temporal_holdout.start, split.temporal_holdout.end),
    )
    with pytest.raises(FrozenInstanceError):
        boundary.boundary_sha256 = "0" * 64  # type: ignore[misc]
    with pytest.raises(TypeError, match="factory"):
        DevelopmentReadBoundaryV2(**boundary.constructor_fields())  # type: ignore[arg-type]


@pytest.mark.parametrize("copier", (copy.copy, copy.deepcopy))
def test_copied_boundary_loses_original_publication_authority(copier) -> None:
    _, _, boundary = _issued()
    copied = copier(boundary)

    with pytest.raises(ValueError, match="original publication"):
        open_development_consumer_v2(copied, _allowed_request(copied), object)


def test_object_new_or_private_token_cannot_forge_boundary_authority() -> None:
    from market_structure_lab.research import validation_v2_splits as module

    coverage, split, boundary = _issued()
    forged = object.__new__(DevelopmentReadBoundaryV2)
    for name, value in boundary.constructor_fields().items():
        object.__setattr__(forged, name, value)
    with pytest.raises(ValueError, match="original publication"):
        verify_development_read_boundary_v2(forged, coverage, split)

    reconstructed = DevelopmentReadBoundaryV2(
        **boundary.constructor_fields(),  # type: ignore[arg-type]
        _factory_token=module._BOUNDARY_FACTORY,  # noqa: SLF001
    )
    with pytest.raises(ValueError, match="original publication"):
        verify_development_read_boundary_v2(reconstructed, coverage, split)


def test_coherent_private_token_holdout_widening_cannot_authorize() -> None:
    from market_structure_lab.core.identity import hash_json
    from market_structure_lab.research import validation_v2_splits as module
    from market_structure_lab.research.validation_v2_models import publication_json_bytes

    _, _, boundary = _issued()
    widened_symbols = tuple(sorted((*boundary.allowed_symbols, *boundary.forbidden_asset_symbols)))
    payload = boundary._identity_payload()  # noqa: SLF001
    payload["allowed_symbols"] = list(widened_symbols)
    payload["forbidden_asset_symbols"] = []
    digest = hash_json(
        "phase5-validation-development-read-boundary-v2",
        payload,
    )
    public = {
        "schema_version": "phase5-validation-development-read-boundary-v2",
        **payload,
        "boundary_sha256": digest,
    }
    forged = DevelopmentReadBoundaryV2(
        coverage_identity=boundary.coverage_identity,
        split_identity=boundary.split_identity,
        allowed_symbols=widened_symbols,
        allowed_timeframes=boundary.allowed_timeframes,
        allowed_intervals=boundary.allowed_intervals,
        forbidden_asset_symbols=(),
        forbidden_temporal_intervals=boundary.forbidden_temporal_intervals,
        boundary_sha256=digest,
        canonical_bytes=publication_json_bytes(public),
        _factory_token=module._BOUNDARY_FACTORY,  # noqa: SLF001
    )
    request = replace(_allowed_request(boundary), symbol=boundary.forbidden_asset_symbols[0])

    with pytest.raises(ValueError, match="original publication"):
        open_development_consumer_v2(forged, request, object)


@pytest.mark.parametrize("operation_kind", tuple(AccessOperationKindV2))
def test_each_consumer_kind_is_denied_before_construction(
    operation_kind: AccessOperationKindV2,
) -> None:
    _, _, boundary = _issued()
    request = replace(
        _allowed_request(boundary),
        symbol=boundary.forbidden_asset_symbols[0],
        operation_kind=operation_kind,
    )
    constructed = False

    def consumer():
        nonlocal constructed
        constructed = True
        return object()

    with pytest.raises(PermissionError, match="development read boundary"):
        open_development_consumer_v2(boundary, request, consumer)
    assert constructed is False


@pytest.mark.parametrize("mutation", ("final_asset", "final_time", "widened", "timeframe"))
def test_boundary_rejects_forbidden_requests_before_consumer_construction(
    mutation: str,
) -> None:
    _, _, boundary = _issued()
    request = _allowed_request(boundary)
    interval = boundary.allowed_intervals[0]
    if mutation == "final_asset":
        request = replace(request, symbol=boundary.forbidden_asset_symbols[0])
    elif mutation == "final_time":
        request = replace(
            request,
            start=boundary.forbidden_temporal_intervals[0].start,
            end=boundary.forbidden_temporal_intervals[0].end,
        )
    elif mutation == "widened":
        request = replace(request, start=interval.start - timedelta(minutes=1))
    else:
        request = replace(request, timeframe="1d")
    constructed = False

    def consumer():
        nonlocal constructed
        constructed = True
        return object()

    with pytest.raises(PermissionError, match="development read boundary"):
        open_development_consumer_v2(boundary, request, consumer)
    assert constructed is False


def test_stale_wrong_coverage_or_split_invalidates_boundary() -> None:
    coverage, split, boundary = _issued()
    changed_coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=coverage.raw_dump,
        reconciliation=coverage.reconciliation,
        compatibility_metadata_sha256="0" * 64,
        entries=coverage.entries,
    )
    changed_split = freeze_development_split_v2(
        coverage=changed_coverage,
        policy=split.policy,
    )

    with pytest.raises(ValueError, match="coverage"):
        verify_development_read_boundary_v2(boundary, changed_coverage, split)
    with pytest.raises(ValueError, match="split"):
        verify_development_read_boundary_v2(boundary, coverage, changed_split)


def test_stale_original_publication_bytes_invalidate_issue() -> None:
    coverage, split, _boundary = _issued()
    object.__setattr__(coverage, "canonical_bytes", b"{}\n")

    with pytest.raises(ValueError, match="original publication bytes"):
        issue_development_read_boundary_v2(coverage, split)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_missing_or_extra_interval_invalidates_reconstructed_publication(
    mutation: str,
) -> None:
    coverage, split, boundary = _issued()
    payload = boundary.to_dict()
    intervals = list(payload["allowed_intervals"])  # type: ignore[arg-type]
    if mutation == "missing":
        intervals.pop()
    else:
        intervals.append(dict(intervals[-1]))
    payload["allowed_intervals"] = intervals

    with pytest.raises(ValueError, match="boundary"):
        DevelopmentReadBoundaryV2.from_publication_dict(payload, coverage, split)


def test_audit_ledger_is_separate_and_does_not_mutate_boundary() -> None:
    _, _, boundary = _issued()
    before = boundary.boundary_sha256
    ledger = DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )
    request = _allowed_request(boundary)

    ledger.start(request)
    ledger.adjudicate(request)
    ledger.complete(request, row_count=2, byte_count=100)

    assert boundary.boundary_sha256 == before
    assert ledger.counters.source_file_attempts == 1
    assert ledger.counters.rows_admitted == 2
    assert ledger.counters.bytes_admitted == 100
    assert ledger.counters.final_scope_attempts == 0


def test_ledger_internally_denies_and_counts_final_scope() -> None:
    _, _, boundary = _issued()
    ledger = DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )
    request = replace(_allowed_request(boundary), symbol=boundary.forbidden_asset_symbols[0])

    ledger.start(request)
    with pytest.raises(PermissionError):
        ledger.adjudicate(request)

    assert ledger.counters.denied_boundary_attempts == 1
    assert ledger.counters.final_scope_attempts == 1
    with pytest.raises(ValueError, match="ordering"):
        ledger.complete(request, row_count=0, byte_count=0)


def test_ledger_enforces_start_adjudication_completion_order() -> None:
    _, _, boundary = _issued()
    ledger = DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )
    request = _allowed_request(boundary)

    with pytest.raises(ValueError, match="ordering"):
        ledger.adjudicate(request)
    ledger.start(request)
    with pytest.raises(ValueError, match="ordering"):
        ledger.start(request)
    ledger.adjudicate(request)
    with pytest.raises(ValueError, match="ordering"):
        ledger.adjudicate(request)


def test_ledger_counters_recompute_and_reject_chain_tampering() -> None:
    _, _, boundary = _issued()
    ledger = DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )
    request = _allowed_request(boundary)
    ledger.start(request)
    object.__setattr__(ledger._records[0], "byte_count", 99)  # noqa: SLF001

    with pytest.raises(ValueError, match="chain verification"):
        _ = ledger.counters
