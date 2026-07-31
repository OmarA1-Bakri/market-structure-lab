from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path

import pytest

from market_structure_lab.data.validation_source_v2 import (
    MinutePathReadBudgetV2,
    VerifiedMinutePathV2,
    VerifiedMinuteRowV2,
    read_verified_minute_path_v2,
    verify_verified_minute_path_v2,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentAccessAttemptLedgerV2,
)

pytest_plugins = ("test_aggregate_publication_v2",)


def _budget(**overrides: int) -> MinutePathReadBudgetV2:
    values = {
        "max_total_rows": 100_000,
        "max_total_bytes": 100_000_000,
        "max_partitions": 10_000,
        "max_returned_rows": 10_000,
    }
    values.update(overrides)
    return MinutePathReadBudgetV2(**values)


def _request(publication, boundary, *, minutes: int = 60, final: bool = False):
    symbol = boundary.forbidden_asset_symbols[0] if final else boundary.allowed_symbols[0]
    interval = boundary.forbidden_temporal_intervals[0] if final else boundary.allowed_intervals[0]
    return BoundaryRequestV2(
        symbol=symbol,
        timeframe="1m",
        start=interval.start,
        end=interval.start + timedelta(minutes=minutes),
        operation_kind=AccessOperationKindV2.FILE,
        target_identity=publication.origin_sha256 or "",
    )


def _audit(boundary) -> DevelopmentAccessAttemptLedgerV2:
    return DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )


def test_minute_path_budget_has_fixed_hard_ceilings() -> None:
    with pytest.raises(ValueError, match="fixed hard ceiling"):
        _budget(max_total_rows=50_000_001)


def test_minute_path_reads_exact_original_lines_and_completes_audit(
    v2_chain, tmp_path: Path
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
    audit = _audit(boundary)

    path = read_verified_minute_path_v2(
        publication,
        coverage,
        split,
        boundary,
        availability,
        request,
        60,
        _budget(),
        audit,
    )

    assert isinstance(path, VerifiedMinutePathV2)
    assert len(path.rows) == 60
    assert all(isinstance(row, VerifiedMinuteRowV2) for row in path.rows)
    assert [row.timestamp for row in path.rows] == [
        request.start + timedelta(minutes=offset) for offset in range(60)
    ]
    assert all(row.symbol == request.symbol and row.timeframe == "1m" for row in path.rows)
    assert audit.records[-1].phase == "completion"
    assert audit.counters.rows_admitted == 60
    assert (
        verify_verified_minute_path_v2(
            path,
            publication=publication,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
            request=request,
            expected_row_count=60,
            budget=_budget(),
        )
        is path
    )


def test_minute_path_rejects_final_scope_before_any_publication_open(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary, final=True)
    audit = _audit(boundary)
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("final request opened a path"),
    )

    with pytest.raises(PermissionError):
        read_verified_minute_path_v2(
            publication,
            coverage,
            split,
            boundary,
            availability,
            request,
            60,
            _budget(),
            audit,
        )
    assert audit.counters.final_scope_attempts == 1
    assert audit.counters.denied_boundary_attempts == 1


def test_minute_path_preflights_all_bounds_before_revalidation_or_partition_open(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
    audit = _audit(boundary)
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.verify_validation_source_publication_v2",
        lambda *_args, **_kwargs: pytest.fail("budget failure reopened publication"),
    )

    with pytest.raises(ValueError, match="total row budget"):
        read_verified_minute_path_v2(
            publication,
            coverage,
            split,
            boundary,
            availability,
            request,
            60,
            _budget(max_total_rows=1),
            audit,
        )
    assert tuple(record.phase for record in audit.records) == ("start", "adjudication")


def test_minute_path_requires_exact_count_origin_and_nominal_registered_objects(
    v2_chain, tmp_path: Path
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
    with pytest.raises(ValueError, match="expected row count"):
        read_verified_minute_path_v2(
            publication,
            coverage,
            split,
            boundary,
            availability,
            request,
            59,
            _budget(),
            _audit(boundary),
        )

    path = read_verified_minute_path_v2(
        publication,
        coverage,
        split,
        boundary,
        availability,
        request,
        60,
        _budget(),
        _audit(boundary),
    )
    with pytest.raises(TypeError, match="factory"):
        VerifiedMinuteRowV2(
            timestamp=path.rows[0].timestamp,
            symbol=path.rows[0].symbol,
            timeframe="1m",
            open=path.rows[0].open,
            high=path.rows[0].high,
            low=path.rows[0].low,
            close=path.rows[0].close,
            volume=path.rows[0].volume,
            source_identity=path.rows[0].source_identity,
            origin_proof_sha256=path.rows[0].origin_proof_sha256,
            line_sha256=path.rows[0].line_sha256,
        )
    with pytest.raises((TypeError, ValueError), match="registered|original|factory"):
        verify_verified_minute_path_v2(
            copy.copy(path),
            publication=publication,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
            request=request,
            expected_row_count=60,
            budget=_budget(),
        )
