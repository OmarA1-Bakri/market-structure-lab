from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from market_structure_lab.core.identity import canonical_json, hash_json
import market_structure_lab.research as research
from market_structure_lab.research import splits
from market_structure_lab.research.models import OutcomeComponent, ValidationWorkBudget

START = datetime(2022, 1, 1, tzinfo=UTC)
PROGRAMME_ID = f"VP-{'1' * 64}"


class OversizedEligibilitySequence:
    def __len__(self) -> int:
        return 65

    def __getitem__(self, index: int) -> object:
        raise AssertionError("eligibilities were read before the final-batch budget gate")


class OversizedEventSequence:
    def __len__(self) -> int:
        return 2_000_001

    def __getitem__(self, index: int) -> object:
        raise AssertionError("events were read before the purge work-budget gate")


class OversizedCoverageSequence:
    def __len__(self) -> int:
        return 65

    def __getitem__(self, index: int) -> object:
        raise AssertionError("coverage was read before the symbol budget gate")


def _coverage(
    symbol: str,
    *,
    start: datetime = START,
    days: int = 738,
    source_conflict: bool = False,
    mapping_compatible: bool = True,
) -> splits.SymbolCoverage:
    return splits.SymbolCoverage(
        symbol=symbol,
        complete_start=start,
        complete_end=start + timedelta(days=days),
        source_conflict=source_conflict,
        mapping_compatible=mapping_compatible,
        coverage_sha256=hash_json("test-symbol-coverage", symbol),
    )


def _coverages() -> tuple[splits.SymbolCoverage, ...]:
    return tuple(
        _coverage(symbol) for symbol in ("ADAUSDT", "APTUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT")
    )


def _split(
    *,
    timeframe: str = "1h",
    programme_id: str = PROGRAMME_ID,
) -> splits.CommonGridSplit:
    return splits.freeze_common_grid_split(
        programme_id=programme_id,
        coverages=_coverages(),
        timeframe=timeframe,
        budget=ValidationWorkBudget(),
    )


def _event(
    row_id: str,
    *,
    feature_start: datetime,
    feature_end: datetime,
    label_start: datetime,
    label_end: datetime,
) -> splits.EventInterval:
    return splits.EventInterval(
        row_id=row_id,
        symbol="SOLUSDT",
        feature_start=feature_start,
        feature_end=feature_end,
        label_start=label_start,
        label_end=label_end,
    )


def _eligibility(
    family: str,
    suffix: str,
    *,
    eligible: bool = True,
    programme_id: str = PROGRAMME_ID,
) -> splits.CandidateEligibility:
    return splits.CandidateEligibility(
        programme_id=programme_id,
        candidate_id=f"HC-{suffix * 64}",
        family=family,
        eligible=eligible,
        eligibility_receipt_sha256=suffix * 64,
    )


def _verify_temporal_access(
    access: splits.VerifiedFinalAccess,
    batch: splits.FinalHoldoutBatch,
    *,
    programme_id: str | None = None,
    batch_sha256: str | None = None,
    candidate_id: str | None = None,
) -> None:
    label_start = batch.temporal_holdout_start + timedelta(hours=1)
    splits.verify_final_access(
        access,
        programme_id=programme_id or batch.programme_id,
        batch_sha256=batch_sha256 or batch.batch_sha256,
        candidate_id=candidate_id or batch.candidate_ids[0],
        component=OutcomeComponent.FINAL_TEMPORAL,
        symbol=batch.development_symbols[0],
        timeframe=batch.timeframe,
        label_start=label_start,
        label_end=label_start + timedelta(hours=1),
    )


def test_split_and_final_access_contracts_are_available_from_the_narrow_public_api() -> None:
    assert research.CommonGridSplit is splits.CommonGridSplit
    assert research.FinalHoldoutBatch is splits.FinalHoldoutBatch
    assert research.VerifiedFinalAccess is splits.VerifiedFinalAccess
    assert research.freeze_common_grid_split is splits.freeze_common_grid_split
    assert research.freeze_final_batch is splits.freeze_final_batch


@pytest.mark.parametrize(("timeframe", "step_hours"), (("1h", 1), ("4h", 4)))
def test_freezes_six_equal_common_grid_blocks_and_four_expanding_outer_folds(
    timeframe: str,
    step_hours: int,
) -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=_coverages(),
        timeframe=timeframe,
        budget=ValidationWorkBudget(),
    )

    assert split.common_start == START
    assert split.common_end == START + timedelta(days=738)
    assert len(split.blocks) == 6
    assert len({block.timestamp_count for block in split.blocks}) == 1
    assert split.blocks[0].start == START
    assert split.blocks[-1].end == split.common_end
    assert all(current.end == following.start for current, following in pairwise(split.blocks))
    assert tuple(fold.test_block_index for fold in split.outer_folds) == (1, 2, 3, 4)
    assert tuple(len(fold.inner_folds) for fold in split.outer_folds) == (3, 3, 3, 3)
    assert all(fold.training.end == fold.test.start for fold in split.outer_folds)
    assert all(fold.test.end - fold.test.start == timedelta(days=123) for fold in split.outer_folds)
    assert split.grid_step == timedelta(hours=step_hours)
    assert split.temporal_holdout == split.blocks[5]


def test_inner_subblocks_exclude_and_bind_only_the_earliest_prefix_remainder() -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=_coverages(),
        timeframe="4h",
        budget=ValidationWorkBudget(),
    )
    first_outer = split.outer_folds[0]

    assert first_outer.prefix_remainder_count == 2
    assert first_outer.prefix_remainder_start == first_outer.training.start
    assert first_outer.prefix_remainder_end == first_outer.training.start + timedelta(hours=8)
    assert first_outer.inner_folds[0].training.start == first_outer.prefix_remainder_end
    assert tuple(inner.test.timestamp_count for inner in first_outer.inner_folds) == (184, 184, 184)
    assert first_outer.prefix_remainder_sha256 != "0" * 64


def test_symbol_order_and_compatible_symbol_insertion_cannot_move_time_boundaries() -> None:
    original = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=_coverages(),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    reordered = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=tuple(reversed(_coverages())),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    inserted = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(*_coverages(), _coverage("LTCUSDT")),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert reordered.blocks == original.blocks
    assert inserted.blocks == original.blocks
    assert reordered.split_sha256 == original.split_sha256
    assert inserted.grid_sha256 == original.grid_sha256


def test_compatible_symbol_deletion_cannot_move_time_boundaries() -> None:
    coverages = (*_coverages(), _coverage("LTCUSDT"))
    original = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=coverages,
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    deleted = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=tuple(item for item in coverages if item.symbol != "LTCUSDT"),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert deleted.blocks == original.blocks
    assert deleted.grid_sha256 == original.grid_sha256


def test_excludes_source_conflicts_and_incompatible_mappings_before_split_freeze() -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(
            *_coverages(),
            _coverage("BTCUSDT", source_conflict=True),
            _coverage("ETHUSDT", mapping_compatible=False),
        ),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert "BTCUSDT" not in split.eligible_symbols
    assert "ETHUSDT" not in split.eligible_symbols
    assert split.excluded_symbols == (
        ("BTCUSDT", "source_conflict"),
        ("ETHUSDT", "incompatible_mapping"),
    )


def test_primary_validation_excludes_btc_and_eth_even_when_a_caller_marks_them_compatible() -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(
            *_coverages(),
            _coverage("BTCUSDT"),
            _coverage("ETHUSDT"),
        ),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert "BTCUSDT" not in split.eligible_symbols
    assert "ETHUSDT" not in split.eligible_symbols
    assert split.excluded_symbols == (
        ("BTCUSDT", "primary_source_conflict_exclusion"),
        ("ETHUSDT", "primary_source_conflict_exclusion"),
    )


def test_source_conflict_exclusions_cannot_pad_the_five_symbol_gate() -> None:
    with pytest.raises(ValueError, match="at least five"):
        splits.freeze_common_grid_split(
            programme_id=PROGRAMME_ID,
            coverages=(
                *_coverages()[:4],
                _coverage("BTCUSDT"),
                _coverage("ETHUSDT"),
            ),
            timeframe="1h",
            budget=ValidationWorkBudget(),
        )


def test_split_identity_binds_excluded_coverage_receipts_without_moving_the_grid() -> None:
    first = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(*_coverages(), _coverage("BTCUSDT", source_conflict=True)),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    changed_conflict_receipt = replace(
        _coverage("BTCUSDT", source_conflict=True),
        coverage_sha256="f" * 64,
    )
    second = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(*_coverages(), changed_conflict_receipt),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert second.grid_sha256 == first.grid_sha256
    assert second.split_sha256 != first.split_sha256
    assert tuple(item.symbol for item in second.coverage_receipts) == (
        "ADAUSDT",
        "APTUSDT",
        "BTCUSDT",
        "DOGEUSDT",
        "SOLUSDT",
        "XRPUSDT",
    )
    assert second.coverage_receipts[2].coverage_sha256 == "f" * 64


def test_split_identity_binds_eligible_receipts_and_limiting_coverage_bounds() -> None:
    original = _split()
    changed_receipt = tuple(
        replace(item, coverage_sha256="f" * 64) if item.symbol == "SOLUSDT" else item
        for item in _coverages()
    )
    receipt_split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=changed_receipt,
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    shifted_start = tuple(
        _coverage(item.symbol, start=START + timedelta(days=6), days=732)
        if item.symbol == "SOLUSDT"
        else item
        for item in _coverages()
    )
    shifted_split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=shifted_start,
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert receipt_split.grid_sha256 == original.grid_sha256
    assert receipt_split.split_sha256 != original.split_sha256
    assert shifted_split.common_start == START + timedelta(days=6)
    assert shifted_split.grid_sha256 != original.grid_sha256


def test_asset_holdout_is_deterministic_twenty_percent_and_disjoint() -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=(*_coverages(), _coverage("LTCUSDT")),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    replay = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=tuple(reversed((*_coverages(), _coverage("LTCUSDT")))),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )

    assert len(split.asset_holdout_symbols) == 2
    assert set(split.asset_holdout_symbols).isdisjoint(split.development_symbols)
    assert set(split.asset_holdout_symbols) | set(split.development_symbols) == set(
        split.eligible_symbols
    )
    assert replay.asset_holdout_symbols == split.asset_holdout_symbols


def test_rejects_fewer_than_five_symbols_or_less_than_730_complete_days() -> None:
    with pytest.raises(ValueError, match="at least five"):
        splits.freeze_common_grid_split(
            programme_id=PROGRAMME_ID,
            coverages=_coverages()[:4],
            timeframe="1h",
            budget=ValidationWorkBudget(),
        )
    with pytest.raises(ValueError, match="730"):
        splits.freeze_common_grid_split(
            programme_id=PROGRAMME_ID,
            coverages=tuple(_coverage(item.symbol, days=729) for item in _coverages()),
            timeframe="1h",
            budget=ValidationWorkBudget(),
        )


def test_rejects_non_midnight_non_utc_and_duplicate_coverage() -> None:
    with pytest.raises(ValueError, match="midnight"):
        _coverage("SOLUSDT", start=START + timedelta(hours=1))
    with pytest.raises(ValueError, match="duplicate"):
        splits.freeze_common_grid_split(
            programme_id=PROGRAMME_ID,
            coverages=(*_coverages(), _coverage("SOLUSDT")),
            timeframe="1h",
            budget=ValidationWorkBudget(),
        )


@pytest.mark.parametrize("days", (730, 731))
@pytest.mark.parametrize("timeframe", ("1h", "4h"))
def test_common_grid_accepts_every_complete_span_at_or_above_730_days(
    days: int,
    timeframe: str,
) -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=tuple(_coverage(item.symbol, days=days) for item in _coverages()),
        timeframe=timeframe,
        budget=ValidationWorkBudget(),
    )

    assert split.common_start == START
    assert split.common_end == START + timedelta(days=days)
    assert len({block.timestamp_count for block in split.blocks}) == 1
    assert split.blocks[0].start == START
    assert split.blocks[-1].end == START + timedelta(days=days)


@pytest.mark.parametrize("timeframe", ("1h", "4h"))
def test_every_inner_grid_exactly_partitions_each_outer_training_prefix(
    timeframe: str,
) -> None:
    split = _split(timeframe=timeframe)

    for outer in split.outer_folds:
        subblock_count = outer.inner_folds[0].test.timestamp_count
        assert all(inner.test.timestamp_count == subblock_count for inner in outer.inner_folds)
        assert outer.prefix_remainder_count + 4 * subblock_count == outer.training.timestamp_count
        assert outer.inner_folds[0].training.start == outer.prefix_remainder_end
        assert outer.inner_folds[-1].test.end == outer.training.end
        assert all(inner.training.end == inner.test.start for inner in outer.inner_folds)
        assert tuple(inner.training.timestamp_count for inner in outer.inner_folds) == tuple(
            index * subblock_count for index in range(1, 4)
        )


def test_split_symbol_budget_rejects_before_reading_coverage_metadata() -> None:
    with pytest.raises(ValueError, match="symbols"):
        splits.freeze_common_grid_split(
            programme_id=PROGRAMME_ID,
            coverages=OversizedCoverageSequence(),  # type: ignore[arg-type]
            timeframe="1h",
            budget=ValidationWorkBudget(),
        )


def test_interval_purge_and_embargo_bind_exact_excluded_identities() -> None:
    test = _event(
        "test",
        feature_start=START + timedelta(days=10),
        feature_end=START + timedelta(days=11),
        label_start=START + timedelta(days=11),
        label_end=START + timedelta(days=12),
    )
    training = (
        _event(
            "feature-overlap",
            feature_start=START + timedelta(days=9),
            feature_end=START + timedelta(days=10, hours=1),
            label_start=START + timedelta(days=10, hours=1),
            label_end=START + timedelta(days=10, hours=2),
        ),
        _event(
            "label-overlap",
            feature_start=START + timedelta(days=8),
            feature_end=START + timedelta(days=9),
            label_start=START + timedelta(days=11, hours=12),
            label_end=START + timedelta(days=12, hours=12),
        ),
        _event(
            "embargo-overlap",
            feature_start=START + timedelta(days=12, hours=12),
            feature_end=START + timedelta(days=13, hours=1),
            label_start=START + timedelta(days=13, hours=1),
            label_end=START + timedelta(days=13, hours=2),
        ),
        _event(
            "boundary-touch",
            feature_start=START + timedelta(days=7),
            feature_end=START + timedelta(days=8),
            label_start=START + timedelta(days=9),
            label_end=START + timedelta(days=10),
        ),
        _event(
            "admitted",
            feature_start=START + timedelta(days=6),
            feature_end=START + timedelta(days=7),
            label_start=START + timedelta(days=7),
            label_end=START + timedelta(days=8),
        ),
    )

    result = splits.purge_and_embargo(
        training,
        (test,),
        test_start=START + timedelta(days=10),
        test_end=START + timedelta(days=12),
        horizon=timedelta(days=1),
        budget=ValidationWorkBudget(),
    )

    assert result.admitted_row_ids == ("boundary-touch", "admitted")
    assert result.purged_row_ids == ("feature-overlap", "label-overlap")
    assert result.embargoed_row_ids == ("embargo-overlap",)
    assert result.embargo_start == START + timedelta(days=12)
    assert result.embargo_end == START + timedelta(days=13)
    assert result.result_sha256 != "0" * 64


def test_purge_rejects_duplicate_ids_and_touching_half_open_intervals_do_not_overlap() -> None:
    event = _event(
        "same",
        feature_start=START,
        feature_end=START + timedelta(hours=1),
        label_start=START + timedelta(hours=1),
        label_end=START + timedelta(hours=2),
    )
    with pytest.raises(ValueError, match="duplicate"):
        splits.purge_and_embargo(
            (event, event),
            (),
            test_start=START + timedelta(days=1),
            test_end=START + timedelta(days=2),
            horizon=timedelta(hours=1),
            budget=ValidationWorkBudget(),
        )
    assert not splits.intervals_overlap(
        START,
        START + timedelta(hours=1),
        START + timedelta(hours=1),
        START + timedelta(hours=2),
    )


def test_purge_rejects_a_test_event_whose_entry_or_label_path_leaves_the_test_fold() -> None:
    before = _event(
        "before",
        feature_start=START + timedelta(hours=23),
        feature_end=START + timedelta(days=1, hours=1),
        label_start=START + timedelta(days=1, hours=1),
        label_end=START + timedelta(days=1, hours=2),
    )
    after = _event(
        "after",
        feature_start=START + timedelta(days=1),
        feature_end=START + timedelta(days=1, hours=23),
        label_start=START + timedelta(days=1, hours=23),
        label_end=START + timedelta(days=2, hours=1),
    )

    for event in (before, after):
        with pytest.raises(ValueError, match="test event.*fold"):
            splits.purge_and_embargo(
                (),
                (event,),
                test_start=START + timedelta(days=1),
                test_end=START + timedelta(days=2),
                horizon=timedelta(hours=1),
                budget=ValidationWorkBudget(),
            )


def test_purge_excludes_training_intervals_inside_the_protected_test_fold() -> None:
    test = _event(
        "test",
        feature_start=START + timedelta(days=1),
        feature_end=START + timedelta(days=1, hours=1),
        label_start=START + timedelta(days=1, hours=1),
        label_end=START + timedelta(days=1, hours=2),
    )
    unobserved_test_period = _event(
        "unobserved-test-period",
        feature_start=START + timedelta(days=1, hours=12),
        feature_end=START + timedelta(days=1, hours=13),
        label_start=START + timedelta(days=1, hours=13),
        label_end=START + timedelta(days=1, hours=14),
    )

    result = splits.purge_and_embargo(
        (unobserved_test_period,),
        (test,),
        test_start=START + timedelta(days=1),
        test_end=START + timedelta(days=2),
        horizon=timedelta(hours=1),
        budget=ValidationWorkBudget(),
    )

    assert result.admitted_row_ids == ()
    assert result.purged_row_ids == ("unobserved-test-period",)


def test_purge_and_embargo_respect_exact_half_open_edges() -> None:
    microsecond = timedelta(microseconds=1)
    test_start = START + timedelta(days=10)
    test_end = START + timedelta(days=11)
    horizon = timedelta(hours=1)
    test = _event(
        "test",
        feature_start=test_start,
        feature_end=test_start + timedelta(hours=1),
        label_start=test_start + timedelta(hours=1),
        label_end=test_start + timedelta(hours=2),
    )
    training = (
        _event(
            "touch-test-start",
            feature_start=test_start - timedelta(hours=2),
            feature_end=test_start - timedelta(hours=1),
            label_start=test_start - timedelta(hours=1),
            label_end=test_start,
        ),
        _event(
            "cross-test-start",
            feature_start=test_start - timedelta(hours=2),
            feature_end=test_start - timedelta(hours=1),
            label_start=test_start - timedelta(hours=1),
            label_end=test_start + microsecond,
        ),
        _event(
            "starts-at-embargo",
            feature_start=test_end,
            feature_end=test_end + timedelta(minutes=10),
            label_start=test_end + timedelta(minutes=10),
            label_end=test_end + timedelta(minutes=20),
        ),
        _event(
            "cross-embargo-end",
            feature_start=test_end + horizon - microsecond,
            feature_end=test_end + horizon + timedelta(minutes=10),
            label_start=test_end + horizon + timedelta(minutes=10),
            label_end=test_end + horizon + timedelta(minutes=20),
        ),
        _event(
            "starts-at-embargo-end",
            feature_start=test_end + horizon,
            feature_end=test_end + horizon + timedelta(minutes=10),
            label_start=test_end + horizon + timedelta(minutes=10),
            label_end=test_end + horizon + timedelta(minutes=20),
        ),
    )

    result = splits.purge_and_embargo(
        training,
        (test,),
        test_start=test_start,
        test_end=test_end,
        horizon=horizon,
        budget=ValidationWorkBudget(),
    )

    assert result.admitted_row_ids == ("touch-test-start", "starts-at-embargo-end")
    assert result.purged_row_ids == ("cross-test-start",)
    assert result.embargoed_row_ids == ("starts-at-embargo", "cross-embargo-end")


def test_prior_embargo_remains_excluded_from_subsequent_expanding_training() -> None:
    previous_test_end = START + timedelta(days=5)
    horizon = timedelta(hours=2)
    later_test_start = START + timedelta(days=10)
    prior_embargo_event = _event(
        "prior-embargo",
        feature_start=previous_test_end + timedelta(minutes=30),
        feature_end=previous_test_end + timedelta(minutes=45),
        label_start=previous_test_end + timedelta(minutes=45),
        label_end=previous_test_end + timedelta(hours=1),
    )

    result = splits.purge_and_embargo(
        (prior_embargo_event,),
        (),
        test_start=later_test_start,
        test_end=later_test_start + timedelta(days=1),
        horizon=horizon,
        prior_embargoes=((previous_test_end, previous_test_end + horizon),),
        budget=ValidationWorkBudget(),
    )

    assert result.admitted_row_ids == ()
    assert result.embargoed_row_ids == ("prior-embargo",)
    assert result.protected_embargoes == (
        (previous_test_end, previous_test_end + horizon),
        (later_test_start + timedelta(days=1), later_test_start + timedelta(days=1, hours=2)),
    )


def test_purge_work_budget_rejects_before_reading_an_oversized_event_sequence() -> None:
    with pytest.raises(ValueError, match="events"):
        splits.purge_and_embargo(
            OversizedEventSequence(),  # type: ignore[arg-type]
            (),
            test_start=START,
            test_end=START + timedelta(days=1),
            horizon=timedelta(hours=1),
            budget=ValidationWorkBudget(),
        )


def test_split_and_selection_outputs_cannot_be_replaced_or_directly_forged() -> None:
    split = splits.freeze_common_grid_split(
        programme_id=PROGRAMME_ID,
        coverages=_coverages(),
        timeframe="1h",
        budget=ValidationWorkBudget(),
    )
    event = _event(
        "test",
        feature_start=START,
        feature_end=START + timedelta(hours=1),
        label_start=START + timedelta(hours=1),
        label_end=START + timedelta(hours=2),
    )
    selection = splits.purge_and_embargo(
        (),
        (event,),
        test_start=START,
        test_end=START + timedelta(hours=2),
        horizon=timedelta(hours=1),
        budget=ValidationWorkBudget(),
    )

    with pytest.raises(TypeError, match="factory"):
        replace(split, grid_sha256="f" * 64)
    with pytest.raises(TypeError, match="factory"):
        replace(selection, admitted_row_ids=("forged",))


def test_final_batch_contains_every_and_only_eligible_candidate_in_family_order() -> None:
    inputs = (
        _eligibility("D", "d"),
        _eligibility("A", "a"),
        _eligibility("B", "b", eligible=False),
        _eligibility("G", "c"),
    )

    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=inputs,
        budget=ValidationWorkBudget(),
    )

    assert batch.candidate_ids == (f"HC-{'a' * 64}", f"HC-{'c' * 64}", f"HC-{'d' * 64}")
    assert batch.family_order == ("A", "G", "D")
    assert batch.batch_sha256 != "0" * 64


def test_final_batch_identity_binds_ineligible_decisions_and_receipt_bytes() -> None:
    eligible = _eligibility("A", "a")
    rejected = _eligibility("B", "b", eligible=False)
    first = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(eligible, rejected),
        budget=ValidationWorkBudget(),
    )
    changed_rejection = replace(rejected, eligibility_receipt_sha256="c" * 64)
    second = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(eligible, changed_rejection),
        budget=ValidationWorkBudget(),
    )

    assert second.candidate_ids == first.candidate_ids
    assert second.batch_sha256 != first.batch_sha256


def test_final_batch_identity_binds_exact_holdout_schema_and_bounds() -> None:
    eligibility = (_eligibility("A", "a"),)
    hourly = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(timeframe="1h"),
        eligibilities=eligibility,
        budget=ValidationWorkBudget(),
    )
    four_hour = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(timeframe="4h"),
        eligibilities=eligibility,
        budget=ValidationWorkBudget(),
    )

    assert hourly.candidate_ids == four_hour.candidate_ids
    assert hourly.split_sha256 != four_hour.split_sha256
    assert hourly.batch_sha256 != four_hour.batch_sha256


def test_final_batch_rejects_tampered_split_metadata() -> None:
    split = _split()
    object.__setattr__(split, "temporal_holdout", split.blocks[4])

    with pytest.raises(ValueError, match="split|holdout"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=split,
            eligibilities=(_eligibility("A", "a"),),
            budget=ValidationWorkBudget(),
        )


def test_final_batch_rejects_wrong_programme_and_changed_grid_bounds() -> None:
    with pytest.raises(ValueError, match="different programme"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=_split(programme_id=f"VP-{'2' * 64}"),
            eligibilities=(_eligibility("A", "a"),),
            budget=ValidationWorkBudget(),
        )

    mutations = (
        ("common_start", START + timedelta(days=1)),
        ("common_end", START + timedelta(days=737)),
    )
    for field_name, replacement in mutations:
        split = _split()
        object.__setattr__(split, field_name, replacement)
        with pytest.raises(ValueError, match="split|holdout|canonical"):
            splits.freeze_final_batch(
                programme_id=PROGRAMME_ID,
                split=split,
                eligibilities=(_eligibility("A", "a"),),
                budget=ValidationWorkBudget(),
            )

    split = _split()
    object.__setattr__(
        split.blocks[5],
        "start",
        split.blocks[5].start + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="split|holdout|canonical"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=split,
            eligibilities=(_eligibility("A", "a"),),
            budget=ValidationWorkBudget(),
        )


def test_final_batch_budget_rejects_before_reading_oversized_sequence() -> None:
    with pytest.raises(ValueError, match="final_batch_candidates"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=_split(),
            eligibilities=OversizedEligibilitySequence(),  # type: ignore[arg-type]
            budget=ValidationWorkBudget(),
        )


def test_final_batch_rejects_duplicate_wrong_programme_or_invalid_candidate_identity() -> None:
    item = _eligibility("A", "a")
    with pytest.raises(ValueError, match="duplicate"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=_split(),
            eligibilities=(item, item),
            budget=ValidationWorkBudget(),
        )
    with pytest.raises(ValueError, match="programme"):
        splits.freeze_final_batch(
            programme_id=PROGRAMME_ID,
            split=_split(),
            eligibilities=(_eligibility("A", "a", programme_id=f"VP-{'2' * 64}"),),
            budget=ValidationWorkBudget(),
        )
    with pytest.raises(ValueError, match="candidate"):
        replace(item, candidate_id="not-a-candidate")


def test_empty_final_batch_writes_no_access_attempt(tmp_path: Path) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(),
        budget=ValidationWorkBudget(),
    )

    with pytest.raises(ValueError, match="empty"):
        splits.attempt_final_access(batch, access_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_final_access_is_factory_sealed_atomic_and_single_use(tmp_path: Path) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )

    access = splits.attempt_final_access(batch, access_root=tmp_path)

    assert access.programme_id == PROGRAMME_ID
    assert access.batch_sha256 == batch.batch_sha256
    assert access.candidate_ids == batch.candidate_ids
    assert access.access_receipt_sha256 != "0" * 64
    assert len(list(tmp_path.iterdir())) == 1
    receipt_path = next(tmp_path.iterdir())
    receipt_payload = {
        "state": "access_attempted",
        "programme_id": PROGRAMME_ID,
        "batch_sha256": batch.batch_sha256,
        "split_sha256": batch.split_sha256,
        "timeframe": batch.timeframe,
        "common_start": batch.common_start,
        "temporal_holdout_start": batch.temporal_holdout_start,
        "temporal_holdout_end": batch.temporal_holdout_end,
        "development_symbols": list(batch.development_symbols),
        "asset_holdout_symbols": list(batch.asset_holdout_symbols),
        "candidate_ids": list(batch.candidate_ids),
    }
    assert receipt_path.read_bytes() == canonical_json(
        "final-holdout-access-attempt-v1",
        receipt_payload,
    )
    assert access.access_receipt_sha256 == hash_json(
        "final-holdout-access-attempt-v1",
        receipt_payload,
    )
    with pytest.raises(FileExistsError):
        splits.attempt_final_access(batch, access_root=tmp_path)
    with pytest.raises(TypeError, match="factory"):
        splits.VerifiedFinalAccess(
            programme_id=PROGRAMME_ID,
            batch_sha256=batch.batch_sha256,
            split_sha256=batch.split_sha256,
            timeframe=batch.timeframe,
            common_start=batch.common_start,
            temporal_holdout_start=batch.temporal_holdout_start,
            temporal_holdout_end=batch.temporal_holdout_end,
            development_symbols=batch.development_symbols,
            asset_holdout_symbols=batch.asset_holdout_symbols,
            candidate_ids=batch.candidate_ids,
            access_receipt_sha256="f" * 64,
            factory_token=object(),
        )


def test_final_access_revalidates_the_sealed_batch_before_writing(tmp_path: Path) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    object.__setattr__(batch, "candidate_ids", (f"HC-{'b' * 64}",))

    with pytest.raises(ValueError, match="batch.*canonical|batch SHA"):
        splits.attempt_final_access(batch, access_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        (
            "candidate_ids",
            (f"HC-{'c' * 64}", f"HC-{'a' * 64}", f"HC-{'d' * 64}"),
        ),
        ("candidate_ids", (f"HC-{'a' * 64}", f"HC-{'c' * 64}")),
        (
            "candidate_ids",
            (
                f"HC-{'a' * 64}",
                f"HC-{'c' * 64}",
                f"HC-{'d' * 64}",
                f"HC-{'e' * 64}",
            ),
        ),
        ("family_order", ("G", "A", "D")),
        ("eligibility_receipt_sha256s", ("9" * 64, "8" * 64, "7" * 64)),
    ),
)
def test_final_access_rejects_reordered_incomplete_or_extra_batch_metadata(
    tmp_path: Path,
    field_name: str,
    replacement: object,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(
            _eligibility("A", "a"),
            _eligibility("G", "c"),
            _eligibility("D", "d"),
        ),
        budget=ValidationWorkBudget(),
    )
    object.__setattr__(batch, field_name, replacement)

    with pytest.raises(ValueError, match="batch.*canonical|batch SHA"):
        splits.attempt_final_access(batch, access_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("temporal_holdout_start", START + timedelta(days=616)),
        ("temporal_holdout_end", START + timedelta(days=737)),
    ),
)
def test_final_access_rejects_changed_batch_timestamp_bounds_before_write(
    tmp_path: Path,
    field_name: str,
    replacement: datetime,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    object.__setattr__(batch, field_name, replacement)

    with pytest.raises(ValueError, match="batch.*canonical|batch SHA"):
        splits.attempt_final_access(batch, access_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_final_access_refuses_a_symlinked_access_root(tmp_path: Path) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink|reparse"):
        splits.attempt_final_access(batch, access_root=linked_root)

    assert list(real_root.iterdir()) == []


def test_final_access_verification_rejects_wrong_programme_batch_or_candidate(
    tmp_path: Path,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    access = splits.attempt_final_access(batch, access_root=tmp_path)

    _verify_temporal_access(access, batch)
    with pytest.raises(ValueError, match="programme"):
        _verify_temporal_access(
            access,
            batch,
            programme_id=f"VP-{'2' * 64}",
        )
    with pytest.raises(ValueError, match="batch"):
        _verify_temporal_access(
            access,
            batch,
            batch_sha256="2" * 64,
        )
    with pytest.raises(ValueError, match="candidate"):
        _verify_temporal_access(
            access,
            batch,
            candidate_id=f"HC-{'9' * 64}",
        )


def test_final_access_verification_rejects_recomputed_in_memory_forgery(
    tmp_path: Path,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    access = splits.attempt_final_access(batch, access_root=tmp_path)
    forged_candidate_id = f"HC-{'2' * 64}"
    object.__setattr__(access, "candidate_ids", (forged_candidate_id,))
    forged_payload = {
        "state": "access_attempted",
        "programme_id": access.programme_id,
        "batch_sha256": access.batch_sha256,
        "split_sha256": access.split_sha256,
        "timeframe": access.timeframe,
        "common_start": access.common_start,
        "temporal_holdout_start": access.temporal_holdout_start,
        "temporal_holdout_end": access.temporal_holdout_end,
        "development_symbols": list(access.development_symbols),
        "asset_holdout_symbols": list(access.asset_holdout_symbols),
        "candidate_ids": [forged_candidate_id],
    }
    object.__setattr__(
        access,
        "access_receipt_sha256",
        hash_json("final-holdout-access-attempt-v1", forged_payload),
    )

    with pytest.raises(ValueError, match="durable access record"):
        _verify_temporal_access(
            access,
            batch,
            candidate_id=forged_candidate_id,
        )


def test_final_access_enforces_temporal_asset_and_unused_component_boundaries(
    tmp_path: Path,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    access = splits.attempt_final_access(batch, access_root=tmp_path)
    candidate_id = batch.candidate_ids[0]
    temporal_start = batch.temporal_holdout_start + timedelta(hours=1)
    temporal_end = temporal_start + timedelta(hours=1)
    asset_start = batch.common_start + timedelta(hours=1)
    asset_end = asset_start + timedelta(hours=1)

    splits.verify_final_access(
        access,
        programme_id=PROGRAMME_ID,
        batch_sha256=batch.batch_sha256,
        candidate_id=candidate_id,
        component=OutcomeComponent.FINAL_TEMPORAL,
        symbol=batch.development_symbols[0],
        timeframe=batch.timeframe,
        label_start=temporal_start,
        label_end=temporal_end,
    )
    splits.verify_final_access(
        access,
        programme_id=PROGRAMME_ID,
        batch_sha256=batch.batch_sha256,
        candidate_id=candidate_id,
        component=OutcomeComponent.FINAL_ASSET,
        symbol=batch.asset_holdout_symbols[0],
        timeframe=batch.timeframe,
        label_start=asset_start,
        label_end=asset_end,
    )
    with pytest.raises(ValueError, match="development symbol"):
        splits.verify_final_access(
            access,
            programme_id=PROGRAMME_ID,
            batch_sha256=batch.batch_sha256,
            candidate_id=candidate_id,
            component=OutcomeComponent.FINAL_TEMPORAL,
            symbol=batch.asset_holdout_symbols[0],
            timeframe=batch.timeframe,
            label_start=temporal_start,
            label_end=temporal_end,
        )
    with pytest.raises(ValueError, match="blocks 0 through 4"):
        splits.verify_final_access(
            access,
            programme_id=PROGRAMME_ID,
            batch_sha256=batch.batch_sha256,
            candidate_id=candidate_id,
            component=OutcomeComponent.FINAL_ASSET,
            symbol=batch.asset_holdout_symbols[0],
            timeframe=batch.timeframe,
            label_start=temporal_start,
            label_end=temporal_end,
        )


def test_final_access_rejects_string_component_before_boundary_dispatch(
    tmp_path: Path,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    access = splits.attempt_final_access(batch, access_root=tmp_path)
    asset_start = batch.common_start + timedelta(hours=1)
    asset_end = asset_start + timedelta(hours=1)

    with pytest.raises(TypeError, match="OutcomeComponent"):
        splits.verify_final_access(
            access,
            programme_id=PROGRAMME_ID,
            batch_sha256=batch.batch_sha256,
            candidate_id=batch.candidate_ids[0],
            component=cast(OutcomeComponent, "final_temporal"),
            symbol=batch.asset_holdout_symbols[0],
            timeframe=batch.timeframe,
            label_start=asset_start,
            label_end=asset_end,
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("programme_id", f"VP-{'2' * 64}"),
        ("batch_sha256", "2" * 64),
        ("candidate_ids", (f"HC-{'2' * 64}",)),
        ("access_receipt_sha256", "2" * 64),
    ),
)
def test_final_access_verification_rejects_tampered_capability(
    tmp_path: Path,
    field_name: str,
    replacement: object,
) -> None:
    batch = splits.freeze_final_batch(
        programme_id=PROGRAMME_ID,
        split=_split(),
        eligibilities=(_eligibility("A", "a"),),
        budget=ValidationWorkBudget(),
    )
    access = splits.attempt_final_access(batch, access_root=tmp_path)
    object.__setattr__(access, field_name, replacement)

    with pytest.raises(ValueError, match="receipt|programme|batch|candidate"):
        _verify_temporal_access(
            access,
            batch,
        )
