from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.validation_source_v2 import (
    publish_scoped_source_candidate_inputs_v2,
)
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentReadBoundaryV2,
    SplitPolicyV2,
    freeze_development_split_v2,
    freeze_development_folds_v2,
    issue_development_read_boundary_v2,
    verify_development_read_boundary_v2,
)


class ExplodingIterable:
    def __iter__(self):
        raise AssertionError("row/outcome iterable was touched")


class ExplodingPath:
    def __fspath__(self) -> str:
        raise AssertionError("legacy source evidence path was opened")


def _coverage(**changes: object) -> SourceCoveragePublicationV2:
    entries = tuple(
        SourceCoverageEntryV2(
            symbol=symbol,
            complete_start=datetime(2024, 1, 1, tzinfo=UTC),
            complete_end=datetime(2025, 1, 1, tzinfo=UTC),
            timeframes=("1m",),
            source_conflict=False,
            mapping_compatible=True,
            metadata_sha256=str(index) * 64,
        )
        for index, symbol in enumerate(
            ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT"), start=1
        )
    )
    values: dict[str, object] = {
        "raw_dump": RawDumpIdentityV2(
            dump_sha256="a" * 64,
            byte_count=123,
            pg_restore_list_sha256="b" * 64,
            candle_table_toc_identity="public.candles:table-data:42",
            source_mapping_version="callscore-candles-v1",
        ),
        "reconciliation": ReconciliationAuthorityV2(
            promotion_receipt_sha256="c" * 64,
            work_unit_manifest_sha256=("d" * 64,),
            comparison_part_sha256=("e" * 64,),
            replacement_source_policy="rr-000008-promoted-only",
        ),
        "compatibility_metadata_sha256": "f" * 64,
        "entries": entries,
    }
    values.update(changes)
    return SourceCoveragePublicationV2.freeze(**values)  # type: ignore[arg-type]


def _policy(**changes: object) -> SplitPolicyV2:
    values: dict[str, object] = {
        "block_count": 6,
        "minimum_complete_days": 180,
        "asset_holdout_fraction_numerator": 1,
        "asset_holdout_fraction_denominator": 5,
        "asset_holdout_salt": "market-structure-lab-phase5-v2-public-salt",
        "purge_hours": 24,
        "embargo_hours": 24,
        "timeframes": ("1m", "1h", "4h"),
    }
    values.update(changes)
    return SplitPolicyV2(**values)  # type: ignore[arg-type]


def test_coverage_or_policy_changes_split_without_programme_id() -> None:
    coverage = _coverage()
    first = freeze_development_split_v2(coverage=coverage, policy=_policy())
    changed_policy = freeze_development_split_v2(
        coverage=coverage,
        policy=_policy(embargo_hours=48),
    )
    changed_coverage = freeze_development_split_v2(
        coverage=_coverage(compatibility_metadata_sha256="0" * 64),
        policy=_policy(),
    )

    assert first.split_identity != changed_policy.split_identity
    assert first.split_identity != changed_coverage.split_identity
    assert "programme_id" not in first.to_dict()


def test_programme_metadata_cannot_change_frozen_split() -> None:
    coverage = _coverage()
    policy = _policy()

    assert freeze_development_split_v2(coverage=coverage, policy=policy) == (
        freeze_development_split_v2(coverage=coverage, policy=policy)
    )


def test_holdout_order_uses_fixed_domain_salt_and_coverage_only() -> None:
    split = freeze_development_split_v2(coverage=_coverage(), policy=_policy())
    split_again = freeze_development_split_v2(coverage=_coverage(), policy=_policy())

    assert split.asset_holdout_symbols == split_again.asset_holdout_symbols
    assert len(split.asset_holdout_symbols) == 1
    assert set(split.asset_holdout_symbols).isdisjoint(split.development_symbols)
    assert "programme_id" not in split.to_dict()
    assert "outcome" not in repr(split.to_dict()).lower()


def test_split_construction_never_touches_rows_or_outcomes() -> None:
    split = freeze_development_split_v2(
        coverage=_coverage(),
        policy=_policy(),
        candle_rows=ExplodingIterable(),
        outcomes=ExplodingIterable(),
    )

    assert split.development_symbols


def test_timeframe_contract_distinguishes_source_reads_from_derived_targets() -> None:
    policy = _policy()
    split = freeze_development_split_v2(coverage=_coverage(), policy=policy)
    boundary = issue_development_read_boundary_v2(_coverage(), split)

    assert policy.timeframes == ("1m", "1h", "4h")
    assert policy.source_readable_timeframes == ("1m",)
    assert policy.derived_target_timeframes == ("1h", "4h")
    assert policy.to_dict()["timeframe_contract_version"] == "source-vs-derived-v2"
    assert policy.to_dict()["source_readable_timeframes"] == ["1m"]
    assert policy.to_dict()["derived_target_timeframes"] == ["1h", "4h"]
    assert boundary.source_readable_timeframes == ("1m",)
    assert boundary.derived_target_timeframes == ("1h", "4h")
    assert boundary.allowed_timeframes == ("1m",)
    assert freeze_development_folds_v2(split=split, timeframe="1h").timeframe == "1h"
    assert freeze_development_folds_v2(split=split, timeframe="4h").timeframe == "4h"
    with pytest.raises(ValueError, match="outside the frozen development split"):
        freeze_development_folds_v2(split=split, timeframe="1m")


def test_rr_month_aligned_policy_preserves_source_vs_derived_contract() -> None:
    policy = _policy(grid_contract_version="rr-month-aligned-v1")
    reopened = SplitPolicyV2.from_dict(policy.to_dict())

    assert reopened == policy
    assert policy.source_readable_timeframes == ("1m",)
    assert policy.derived_target_timeframes == ("1h", "4h")
    assert policy.to_dict()["grid_contract_version"] == "rr-month-aligned-v1"


def test_legacy_split_policy_reopens_exactly_without_becoming_new_source_trust() -> None:
    coverage = _coverage(
        entries=tuple(
            replace(entry, timeframes=("1m", "1h", "4h")) for entry in _coverage().entries
        )
    )
    legacy_payload = {
        "block_count": 6,
        "minimum_complete_days": 180,
        "asset_holdout_fraction_numerator": 1,
        "asset_holdout_fraction_denominator": 5,
        "asset_holdout_salt": "market-structure-lab-phase5-v2-public-salt",
        "purge_hours": 24,
        "embargo_hours": 24,
        "timeframes": ["1m", "1h", "4h"],
    }

    legacy_policy = SplitPolicyV2.from_dict(legacy_payload)
    legacy_split = freeze_development_split_v2(coverage=coverage, policy=legacy_policy)
    new_split = freeze_development_split_v2(coverage=coverage, policy=_policy())
    reopened = type(legacy_split).from_dict(legacy_split.to_dict(), coverage)

    assert legacy_policy.legacy_timeframes_schema is True
    assert legacy_policy.to_dict() == legacy_payload
    assert reopened.canonical_bytes == legacy_split.canonical_bytes
    assert reopened.split_identity == legacy_split.split_identity
    assert new_split.split_identity != legacy_split.split_identity
    with pytest.raises(ValueError, match="verification-only"):
        issue_development_read_boundary_v2(coverage, reopened)


def test_legacy_boundary_reopens_exact_bytes_but_is_verification_only() -> None:
    coverage = _coverage(
        entries=tuple(
            replace(entry, timeframes=("1m", "1h", "4h")) for entry in _coverage().entries
        )
    )
    legacy_policy = SplitPolicyV2.from_dict(
        {
            "block_count": 6,
            "minimum_complete_days": 180,
            "asset_holdout_fraction_numerator": 1,
            "asset_holdout_fraction_denominator": 5,
            "asset_holdout_salt": "market-structure-lab-phase5-v2-public-salt",
            "purge_hours": 24,
            "embargo_hours": 24,
            "timeframes": ["1m", "1h", "4h"],
        }
    )
    legacy_split = freeze_development_split_v2(coverage=coverage, policy=legacy_policy)

    def utc_text(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    identity_payload = {
        "coverage_identity": coverage.coverage_identity.value,
        "split_identity": legacy_split.split_identity.value,
        "allowed_symbols": list(legacy_split.development_symbols),
        "allowed_timeframes": ["1m", "1h", "4h"],
        "allowed_intervals": [
            {"start": utc_text(block.start), "end": utc_text(block.end)}
            for block in legacy_split.development_blocks
        ],
        "forbidden_asset_symbols": list(legacy_split.asset_holdout_symbols),
        "forbidden_temporal_intervals": [
            {
                "start": utc_text(legacy_split.temporal_holdout.start),
                "end": utc_text(legacy_split.temporal_holdout.end),
            }
        ],
    }
    legacy_payload = {
        "schema_version": "phase5-validation-development-read-boundary-v2",
        **identity_payload,
        "boundary_sha256": hash_json(
            "phase5-validation-development-read-boundary-v2", identity_payload
        ),
    }
    legacy_bytes = publication_json_bytes(legacy_payload)

    boundary = DevelopmentReadBoundaryV2.from_publication_dict(
        legacy_payload, coverage, legacy_split
    )

    assert boundary.legacy_source_scope_schema is True
    assert boundary.canonical_bytes == legacy_bytes
    assert boundary.to_dict() == legacy_payload
    with pytest.raises(ValueError, match="ineligible for successor source trust"):
        verify_development_read_boundary_v2(boundary, coverage, legacy_split)
    exploding_path = cast(Any, ExplodingPath())
    with pytest.raises(ValueError, match="ineligible for successor source trust"):
        publish_scoped_source_candidate_inputs_v2(
            coverage=coverage,
            split=legacy_split,
            boundary=boundary,
            raw_dump_path=exploding_path,
            rr_promotion_receipt_path=exploding_path,
            recovery_manifest_path=exploding_path,
            reconciled_view_definition_path=exploding_path,
            output_root=exploding_path,
        )
    interval = boundary.allowed_intervals[0]
    request = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=interval.start,
        end=interval.start + timedelta(minutes=1),
        operation_kind=AccessOperationKindV2.QUERY,
        target_identity="legacy-source-view",
    )
    with pytest.raises(PermissionError, match="verification-only"):
        boundary.authorize(request)


@pytest.mark.parametrize("timeframe", ("1h", "4h"))
def test_boundary_rejects_derived_target_timeframes(timeframe: str) -> None:
    coverage = _coverage()
    split = freeze_development_split_v2(coverage=coverage, policy=_policy())
    boundary = issue_development_read_boundary_v2(coverage, split)
    interval = boundary.allowed_intervals[0]
    request = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe=timeframe,
        start=interval.start,
        end=interval.start + timedelta(minutes=1),
        operation_kind=AccessOperationKindV2.QUERY,
        target_identity="source-view",
    )

    with pytest.raises(PermissionError, match="development read boundary"):
        boundary.authorize(request)


def test_boundary_allows_one_minute_and_still_rejects_final_symbol_and_interval() -> None:
    coverage = _coverage()
    split = freeze_development_split_v2(coverage=coverage, policy=_policy())
    boundary = issue_development_read_boundary_v2(coverage, split)
    interval = boundary.allowed_intervals[0]
    allowed = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=interval.start,
        end=interval.start + timedelta(minutes=1),
        operation_kind=AccessOperationKindV2.QUERY,
        target_identity="source-view",
    )

    boundary.authorize(allowed)
    with pytest.raises(PermissionError, match="development read boundary"):
        boundary.authorize(replace(allowed, symbol=boundary.forbidden_asset_symbols[0]))
    final_interval = boundary.forbidden_temporal_intervals[0]
    with pytest.raises(PermissionError, match="development read boundary"):
        boundary.authorize(replace(allowed, start=final_interval.start, end=final_interval.end))


def test_common_grid_blocks_are_deterministic_and_half_open() -> None:
    split = freeze_development_split_v2(coverage=_coverage(), policy=_policy())

    assert len(split.blocks) == 6
    assert split.blocks[0].start == datetime(2024, 1, 1, tzinfo=UTC)
    assert split.blocks[-1].end <= datetime(2025, 1, 1, tzinfo=UTC)
    assert all(left.end == right.start for left, right in zip(split.blocks, split.blocks[1:]))
    assert split.temporal_holdout == split.blocks[-1]
    assert split.development_blocks == split.blocks[:-1]


def test_common_grid_rounds_inward_for_non_midnight_coverage() -> None:
    coverage = _coverage()
    entries = tuple(
        replace(
            entry,
            complete_start=datetime(2024, 1, 1, 0, 1, tzinfo=UTC),
            complete_end=datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
        )
        for entry in coverage.entries
    )
    changed = SourceCoveragePublicationV2.freeze(
        raw_dump=coverage.raw_dump,
        reconciliation=coverage.reconciliation,
        compatibility_metadata_sha256=coverage.compatibility_metadata_sha256,
        entries=entries,
    )

    split = freeze_development_split_v2(coverage=changed, policy=_policy())

    assert split.blocks[0].start == datetime(2024, 1, 2, tzinfo=UTC)
    assert split.blocks[-1].end <= datetime(2025, 1, 1, tzinfo=UTC)
    assert all(
        entry.complete_start <= block.start < block.end <= entry.complete_end
        for entry in entries
        for block in split.blocks
    )


def test_rr_month_aligned_grid_uses_whole_utc_months_without_delaying_final() -> None:
    coverage = _coverage()
    entries = tuple(
        replace(
            entry,
            complete_start=datetime(2024, 4, 12, tzinfo=UTC),
            complete_end=datetime(2026, 7, 13, tzinfo=UTC),
        )
        for entry in coverage.entries
    )
    changed = SourceCoveragePublicationV2.freeze(
        raw_dump=coverage.raw_dump,
        reconciliation=coverage.reconciliation,
        compatibility_metadata_sha256=coverage.compatibility_metadata_sha256,
        entries=entries,
    )
    equal_days = freeze_development_split_v2(coverage=changed, policy=_policy())
    aligned = freeze_development_split_v2(
        coverage=changed,
        policy=_policy(grid_contract_version="rr-month-aligned-v1"),
    )

    assert aligned.blocks[0].start == datetime(2024, 6, 1, tzinfo=UTC)
    assert aligned.temporal_holdout.start == datetime(2026, 2, 1, tzinfo=UTC)
    assert aligned.blocks[-1].end == datetime(2026, 6, 1, tzinfo=UTC)
    assert aligned.temporal_holdout.start <= equal_days.temporal_holdout.start
    assert all(block.start.day == block.end.day == 1 for block in aligned.blocks)
    assert all(left.end == right.start for left, right in zip(aligned.blocks, aligned.blocks[1:]))


def test_coverage_rejects_one_microsecond_identity_collision() -> None:
    coverage = _coverage()
    with pytest.raises(ValueError, match="minute-aligned"):
        replace(
            coverage.entries[0],
            complete_start=coverage.entries[0].complete_start.replace(microsecond=1),
        )


def test_ineligible_coverage_is_excluded_before_holdout() -> None:
    coverage = _coverage()
    conflicting = replace(coverage.entries[0], source_conflict=True)
    incompatible = replace(coverage.entries[1], mapping_compatible=False)
    changed = SourceCoveragePublicationV2.freeze(
        raw_dump=coverage.raw_dump,
        reconciliation=coverage.reconciliation,
        compatibility_metadata_sha256=coverage.compatibility_metadata_sha256,
        entries=(conflicting, incompatible, *coverage.entries[2:]),
    )

    split = freeze_development_split_v2(coverage=changed, policy=_policy())

    assert {item.symbol for item in split.excluded_symbols} == {
        conflicting.symbol,
        incompatible.symbol,
    }


def test_individually_insufficient_compatible_coverage_is_explicitly_excluded() -> None:
    coverage = _coverage()
    insufficient = replace(
        coverage.entries[0],
        complete_start=datetime(2024, 7, 10, 0, 1, tzinfo=UTC),
    )
    changed = SourceCoveragePublicationV2.freeze(
        raw_dump=coverage.raw_dump,
        reconciliation=coverage.reconciliation,
        compatibility_metadata_sha256=coverage.compatibility_metadata_sha256,
        entries=(insufficient, *coverage.entries[1:]),
    )

    split = freeze_development_split_v2(coverage=changed, policy=_policy())

    assert insufficient.symbol not in split.eligible_symbols
    assert tuple((item.symbol, item.reason) for item in split.excluded_symbols) == (
        (insufficient.symbol, "insufficient_complete_days"),
    )


def test_policy_rejects_caller_chosen_split_domain() -> None:
    with pytest.raises(TypeError):
        cast(Any, SplitPolicyV2)(
            block_count=6,
            minimum_complete_days=180,
            asset_holdout_fraction_numerator=1,
            asset_holdout_fraction_denominator=5,
            asset_holdout_salt="salt",
            purge_hours=24,
            embargo_hours=24,
            timeframes=("1m",),
            split_domain="caller-domain",
        )
