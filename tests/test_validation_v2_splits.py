from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    SplitPolicyV2,
    freeze_development_split_v2,
)


class ExplodingIterable:
    def __iter__(self):
        raise AssertionError("row/outcome iterable was touched")


def _coverage(**changes: object) -> SourceCoveragePublicationV2:
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


def test_common_grid_blocks_are_deterministic_and_half_open() -> None:
    split = freeze_development_split_v2(coverage=_coverage(), policy=_policy())

    assert len(split.blocks) == 6
    assert split.blocks[0].start == datetime(2024, 1, 1, tzinfo=UTC)
    assert split.blocks[-1].end <= datetime(2025, 1, 1, tzinfo=UTC)
    assert all(left.end == right.start for left, right in zip(split.blocks, split.blocks[1:]))
    assert split.temporal_holdout == split.blocks[-1]
    assert split.development_blocks == split.blocks[:-1]


def test_common_grid_rounds_inward_for_non_midnight_subsecond_coverage() -> None:
    coverage = _coverage()
    entries = tuple(
        replace(
            entry,
            complete_start=datetime(2024, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
            complete_end=datetime(2025, 1, 1, 23, 59, 59, 999999, tzinfo=UTC),
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


def test_policy_rejects_caller_chosen_split_domain() -> None:
    with pytest.raises(TypeError):
        SplitPolicyV2(  # type: ignore[call-arg]
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
