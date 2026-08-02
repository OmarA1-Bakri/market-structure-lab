from __future__ import annotations

import copy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from market_structure_lab.data.validation_source_v2 import (
    CanonicalMinuteRowV2,
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
    issue_development_read_boundary_v2,
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


def test_minute_path_rejects_tuple_over_hard_horizon_ceiling_before_io(
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
        lambda *_args, **_kwargs: pytest.fail("hard path ceiling reopened publication"),
    )
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("hard path ceiling opened a file"),
    )

    with pytest.raises(ValueError, match="hard path row ceiling"):
        read_verified_minute_path_v2(
            publication,
            coverage,
            split,
            boundary,
            availability,
            request,
            10_001,
            _budget(max_returned_rows=50_000_000),
            audit,
        )
    assert tuple(record.phase for record in audit.records) == ("start", "adjudication")


def test_minute_path_streams_verified_multi_partition_payload_larger_than_chunk(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_aggregate_publication_v2 as fixtures
    from market_structure_lab.data import validation_source_v2 as source_module

    coverage, split, boundary, availability = v2_chain
    request_rows = list(fixtures._minute_rows(boundary))
    interval = boundary.allowed_intervals[0]
    symbol = boundary.allowed_symbols[0]
    fractional = "1234567890" * 60
    request_rows[0] = tuple(
        CanonicalMinuteRowV2(
            timestamp=interval.start + timedelta(minutes=offset),
            symbol=symbol,
            timeframe="1m",
            open=Decimal(f"100.{fractional}"),
            high=Decimal(f"101.{fractional}"),
            low=Decimal(f"99.{fractional}"),
            close=Decimal(f"100.{fractional}"),
            volume=Decimal(f"1.{fractional}"),
        )
        for offset in range(400)
    )
    publication = fixtures._publish_minute(
        v2_chain,
        tmp_path,
        rows=tuple(request_rows),
    )
    request = _request(publication, boundary, minutes=400)
    selected = tuple(
        partition
        for partition in publication.partitions
        if partition.symbol == request.symbol
        and partition.min_timestamp < request.end.isoformat().replace("+00:00", "Z")
        and partition.max_timestamp >= request.start.isoformat().replace("+00:00", "Z")
    )
    assert len(selected) > 1
    assert sum(partition.byte_count for partition in selected) > 1024 * 1024
    original_read = source_module.read_bounded_regular

    def reject_partition_materialization(path: Path, maximum: int) -> bytes:
        if "partitions" in path.parts and path.suffix == ".jsonl":
            pytest.fail("minute partition payload used read_bounded_regular")
        return original_read(path, maximum)

    monkeypatch.setattr(source_module, "read_bounded_regular", reject_partition_materialization)

    path = read_verified_minute_path_v2(
        publication,
        coverage,
        split,
        boundary,
        availability,
        request,
        400,
        _budget(),
        _audit(boundary),
    )

    assert path.row_count == 400
    assert path.verify_original() is path


def test_minute_path_maps_tampered_partition_to_public_value_error_and_stops_audit(
    v2_chain,
    tmp_path: Path,
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
    partition = next(
        item
        for item in publication.partitions
        if item.symbol == request.symbol
        and item.min_timestamp
        <= request.start.isoformat().replace("+00:00", "Z")
        <= item.max_timestamp
    )
    partition_path = publication.publication_root / partition.path
    content = partition_path.read_bytes()
    partition_path.write_bytes(b"[" + content[1:])
    audit = _audit(boundary)

    with pytest.raises(ValueError, match="minute partition bytes/checksum changed"):
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

    assert tuple(record.phase for record in audit.records) == ("start", "adjudication")


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
    assert path.audit_binding.programme_id == "VPV2-" + "1" * 64
    assert path.audit_binding.attempt_id == "VA-" + "2" * 64
    assert path.audit_binding.boundary_sha256 == boundary.boundary_sha256
    assert path.audit_binding.terminal_record == audit.records[-1]
    assert path.audit_binding.audit_identity
    assert path.verify_original() is path
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
            audit=audit,
        )
        is path
    )


def test_minute_path_rejects_ledger_for_different_registered_boundary_before_open(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
    overlapping_boundary = issue_development_read_boundary_v2(coverage, split)
    assert overlapping_boundary == boundary
    assert overlapping_boundary is not boundary
    wrong_audit = _audit(overlapping_boundary)
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("mismatched audit opened a path"),
    )

    with pytest.raises(ValueError, match="audit.*boundary|registered original"):
        read_verified_minute_path_v2(
            publication,
            coverage,
            split,
            boundary,
            availability,
            request,
            60,
            _budget(),
            wrong_audit,
        )
    assert wrong_audit.records == ()


def test_minute_path_reverification_rejects_audit_substitution_and_mutation_before_open(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("invalid audit reopened a path"),
    )

    with pytest.raises(ValueError, match="registered original"):
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
            audit=_audit(boundary),
        )

    object.__setattr__(audit._records[-1], "byte_count", 61)  # noqa: SLF001
    with pytest.raises(ValueError, match="chain verification"):
        path.verify_original()


def test_minute_path_no_argument_verifier_rejects_copy_and_mutation_before_open(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = v2_chain
    publication = fixtures._publish_minute(v2_chain, tmp_path)
    request = _request(publication, boundary)
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
    monkeypatch.setattr(
        "market_structure_lab.data.validation_source_v2.read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("invalid path reopened original bytes"),
    )

    with pytest.raises(ValueError, match="registered original"):
        copy.copy(path).verify_original()

    object.__setattr__(path, "path_identity", "0" * 64)
    with pytest.raises(ValueError, match="serialization|identity"):
        path.verify_original()


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
            audit=_audit(boundary),
        )
