"""Deterministic Phase 5 common-grid split and final-holdout contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from datetime import datetime, time, timedelta
from math import ceil
import os
from pathlib import Path
import re
import stat

from market_structure_lab.core.artifact_io import (
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.core.secure_windows import WindowsHandleFilesystem
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    OutcomeComponent,
    ValidationWorkBudget,
    ValidationWorkDemand,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME_ID = re.compile(r"^VP-[a-f0-9]{64}$")
_CANDIDATE_ID = re.compile(r"^HC-[a-f0-9]{64}$")
_SPLIT_FACTORY = object()
_SELECTION_FACTORY = object()
_FINAL_BATCH_FACTORY = object()
_FINAL_ACCESS_FACTORY = object()
_MINIMUM_COMPLETE_DAYS = 730
_BLOCK_COUNT = 6
_PRIMARY_SOURCE_CONFLICT_EXCLUSIONS = frozenset({"BTCUSDT", "ETHUSDT"})


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_programme_id(value: object) -> None:
    if not isinstance(value, str) or _PROGRAMME_ID.fullmatch(value) is None:
        raise ValueError("programme_id must be a content-addressed VP identity")


def _require_utc(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")


def _require_midnight(value: datetime, label: str) -> None:
    _require_utc(value, label)
    if value.timetz().replace(tzinfo=None) != time():
        raise ValueError(f"{label} must be an exact UTC midnight")


@dataclass(frozen=True, slots=True)
class SymbolCoverage:
    """Outcome-blind complete-day coverage metadata for one source symbol."""

    symbol: str
    complete_start: datetime
    complete_end: datetime
    source_conflict: bool
    mapping_compatible: bool
    coverage_sha256: str

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("coverage symbol must be a non-empty upper-case identifier")
        _require_midnight(self.complete_start, "complete_start")
        _require_midnight(self.complete_end, "complete_end")
        if self.complete_end <= self.complete_start:
            raise ValueError("coverage interval must be positive")
        if not isinstance(self.source_conflict, bool):
            raise TypeError("source_conflict must be boolean")
        if not isinstance(self.mapping_compatible, bool):
            raise TypeError("mapping_compatible must be boolean")
        _require_sha256(self.coverage_sha256, "coverage_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "complete_start": self.complete_start,
            "complete_end": self.complete_end,
            "source_conflict": self.source_conflict,
            "mapping_compatible": self.mapping_compatible,
            "coverage_sha256": self.coverage_sha256,
        }


@dataclass(frozen=True, slots=True)
class GridBlock:
    """One exact half-open block on the common timeframe timestamp grid."""

    start: datetime
    end: datetime
    timestamp_count: int
    block_sha256: str

    def __post_init__(self) -> None:
        _require_utc(self.start, "block start")
        _require_utc(self.end, "block end")
        if self.end <= self.start:
            raise ValueError("grid block must be positive")
        if (
            isinstance(self.timestamp_count, bool)
            or not isinstance(self.timestamp_count, int)
            or self.timestamp_count < 1
        ):
            raise ValueError("grid block timestamp_count must be positive")
        _require_sha256(self.block_sha256, "block_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start,
            "end": self.end,
            "timestamp_count": self.timestamp_count,
            "block_sha256": self.block_sha256,
        }


@dataclass(frozen=True, slots=True)
class InnerFold:
    """One expanding inner-training interval and its following validation subblock."""

    fold_id: str
    training: GridBlock
    test: GridBlock
    fold_sha256: str

    def __post_init__(self) -> None:
        if not self.fold_id:
            raise ValueError("inner fold_id must not be empty")
        if self.training.end != self.test.start:
            raise ValueError("inner training and test intervals must be contiguous")
        _require_sha256(self.fold_sha256, "inner fold_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "fold_id": self.fold_id,
            "training": self.training.to_dict(),
            "test": self.test.to_dict(),
            "fold_sha256": self.fold_sha256,
        }


@dataclass(frozen=True, slots=True)
class OuterFold:
    """One expanding outer-training interval, test block, and frozen inner folds."""

    fold_id: str
    test_block_index: int
    training: GridBlock
    test: GridBlock
    prefix_remainder_count: int
    prefix_remainder_start: datetime
    prefix_remainder_end: datetime
    prefix_remainder_sha256: str
    inner_folds: tuple[InnerFold, ...]
    fold_sha256: str

    def __post_init__(self) -> None:
        if self.test_block_index not in (1, 2, 3, 4):
            raise ValueError("outer test_block_index must be 1..4")
        if self.training.end != self.test.start:
            raise ValueError("outer training and test intervals must be contiguous")
        if len(self.inner_folds) != 3:
            raise ValueError("outer fold must contain exactly three inner validation folds")
        if (
            isinstance(self.prefix_remainder_count, bool)
            or not isinstance(self.prefix_remainder_count, int)
            or self.prefix_remainder_count < 0
        ):
            raise ValueError("prefix remainder count must be non-negative")
        _require_utc(self.prefix_remainder_start, "prefix remainder start")
        _require_utc(self.prefix_remainder_end, "prefix remainder end")
        if self.prefix_remainder_end < self.prefix_remainder_start:
            raise ValueError("prefix remainder interval is invalid")
        _require_sha256(self.prefix_remainder_sha256, "prefix_remainder_sha256")
        _require_sha256(self.fold_sha256, "outer fold_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "fold_id": self.fold_id,
            "test_block_index": self.test_block_index,
            "training": self.training.to_dict(),
            "test": self.test.to_dict(),
            "prefix_remainder_count": self.prefix_remainder_count,
            "prefix_remainder_start": self.prefix_remainder_start,
            "prefix_remainder_end": self.prefix_remainder_end,
            "prefix_remainder_sha256": self.prefix_remainder_sha256,
            "inner_folds": [fold.to_dict() for fold in self.inner_folds],
            "fold_sha256": self.fold_sha256,
        }


@dataclass(frozen=True, slots=True)
class CommonGridSplit:
    """Frozen development/holdout metadata derived without outcome-row access."""

    programme_id: str
    timeframe: str
    grid_step: timedelta
    common_start: datetime
    common_end: datetime
    coverage_receipts: tuple[SymbolCoverage, ...]
    eligible_symbols: tuple[str, ...]
    excluded_symbols: tuple[tuple[str, str], ...]
    development_symbols: tuple[str, ...]
    asset_holdout_symbols: tuple[str, ...]
    blocks: tuple[GridBlock, ...]
    outer_folds: tuple[OuterFold, ...]
    temporal_holdout: GridBlock
    grid_sha256: str
    split_sha256: str
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _SPLIT_FACTORY:
            raise TypeError("CommonGridSplit requires the canonical split factory")
        _require_programme_id(self.programme_id)
        if self.timeframe not in ("1h", "4h"):
            raise ValueError("common-grid timeframe must be 1h or 4h")
        if len(self.blocks) != 6 or len(self.outer_folds) != 4:
            raise ValueError("common-grid split requires six blocks and four outer folds")
        if self.temporal_holdout != self.blocks[5]:
            raise ValueError("temporal holdout must be block 5")
        coverage_symbols = tuple(item.symbol for item in self.coverage_receipts)
        if coverage_symbols != tuple(sorted(set(coverage_symbols))):
            raise ValueError("coverage receipts must be uniquely ordered by symbol")
        excluded_symbol_set = {symbol for symbol, _reason in self.excluded_symbols}
        if set(self.eligible_symbols) & excluded_symbol_set:
            raise ValueError("eligible and excluded coverage symbols must be disjoint")
        if set(self.eligible_symbols) | excluded_symbol_set != set(coverage_symbols):
            raise ValueError("split must classify every coverage receipt")
        if set(self.development_symbols) & set(self.asset_holdout_symbols):
            raise ValueError("development and asset-holdout symbols must be disjoint")
        if set(self.development_symbols) | set(self.asset_holdout_symbols) != set(
            self.eligible_symbols
        ):
            raise ValueError("symbol components must cover every eligible symbol")
        _require_sha256(self.grid_sha256, "grid_sha256")
        _require_sha256(self.split_sha256, "split_sha256")


def freeze_common_grid_split(
    *,
    programme_id: str,
    coverages: Sequence[SymbolCoverage],
    timeframe: str,
    budget: ValidationWorkBudget,
) -> CommonGridSplit:
    """Freeze common complete-day grids, nested folds, and deterministic asset holdout."""

    _require_programme_id(programme_id)
    if timeframe not in ("1h", "4h"):
        raise ValueError("common-grid timeframe must be 1h or 4h")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("common-grid split requires a ValidationWorkBudget")
    if len(coverages) > budget.max_symbols:
        budget.preflight(
            ValidationWorkDemand(symbols=len(coverages), outer_folds=4, inner_folds=3),
            deferred_work=coverages,
        )

    observed: dict[str, SymbolCoverage] = {}
    excluded: list[tuple[str, str]] = []
    for coverage in coverages:
        if not isinstance(coverage, SymbolCoverage):
            raise TypeError("common-grid coverage entries must be SymbolCoverage values")
        if coverage.symbol in observed:
            raise ValueError(f"duplicate coverage symbol: {coverage.symbol}")
        observed[coverage.symbol] = coverage
        if coverage.source_conflict:
            excluded.append((coverage.symbol, "source_conflict"))
        elif not coverage.mapping_compatible:
            excluded.append((coverage.symbol, "incompatible_mapping"))
        elif coverage.symbol in _PRIMARY_SOURCE_CONFLICT_EXCLUSIONS:
            excluded.append((coverage.symbol, "primary_source_conflict_exclusion"))
    eligible = tuple(
        observed[symbol]
        for symbol in sorted(observed)
        if (
            not observed[symbol].source_conflict
            and observed[symbol].mapping_compatible
            and symbol not in _PRIMARY_SOURCE_CONFLICT_EXCLUSIONS
        )
    )
    if len(eligible) < 5:
        raise ValueError("validation requires at least five eligible symbols")
    budget.preflight(
        ValidationWorkDemand(symbols=len(eligible), ranges=6, outer_folds=4, inner_folds=3),
    )

    common_start = max(item.complete_start for item in eligible)
    common_end = min(item.complete_end for item in eligible)
    common_days = (common_end - common_start).days
    if common_days < _MINIMUM_COMPLETE_DAYS:
        raise ValueError("common coverage must contain at least 730 complete UTC days")

    step = timedelta(hours=1 if timeframe == "1h" else 4)
    step_seconds = int(step.total_seconds())
    total_seconds = int((common_end - common_start).total_seconds())
    if total_seconds % step_seconds:
        raise ValueError("common coverage is not divisible by the target timeframe")
    total_timestamps = total_seconds // step_seconds
    if total_timestamps % _BLOCK_COUNT:
        raise ValueError("common timestamp grid cannot form six equal blocks")
    block_timestamps = total_timestamps // _BLOCK_COUNT
    blocks = tuple(
        _grid_block(
            common_start + index * block_timestamps * step,
            common_start + (index + 1) * block_timestamps * step,
            step,
            label=f"block-{index}",
        )
        for index in range(_BLOCK_COUNT)
    )
    outer_folds = tuple(
        _outer_fold(
            programme_id=programme_id,
            timeframe=timeframe,
            blocks=blocks,
            test_block_index=index,
            step=step,
        )
        for index in range(1, 5)
    )
    eligible_symbols = tuple(item.symbol for item in eligible)
    holdout_order = sorted(
        eligible_symbols,
        key=lambda symbol: (
            hash_json(
                "asset-holdout-order-v1",
                {"programme_id": programme_id, "symbol": symbol},
            ),
            symbol,
        ),
    )
    holdout_count = max(1, ceil(len(eligible_symbols) / 5))
    asset_holdout_symbols = tuple(sorted(holdout_order[-holdout_count:]))
    asset_holdout_set = set(asset_holdout_symbols)
    development_symbols = tuple(
        symbol for symbol in eligible_symbols if symbol not in asset_holdout_set
    )
    grid_payload = {
        "timeframe": timeframe,
        "step_seconds": int(step.total_seconds()),
        "common_start": common_start,
        "common_end": common_end,
        "blocks": [block.to_dict() for block in blocks],
        "outer_folds": [fold.to_dict() for fold in outer_folds],
    }
    grid_sha256 = hash_json("common-grid-v1", grid_payload)
    split_payload = {
        "programme_id": programme_id,
        "grid_sha256": grid_sha256,
        "eligible_symbols": list(eligible_symbols),
        "excluded_symbols": [list(item) for item in sorted(excluded)],
        "coverage": [observed[symbol].to_dict() for symbol in sorted(observed)],
        "development_symbols": list(development_symbols),
        "asset_holdout_symbols": list(asset_holdout_symbols),
        "temporal_holdout_sha256": blocks[5].block_sha256,
    }
    return CommonGridSplit(
        programme_id=programme_id,
        timeframe=timeframe,
        grid_step=step,
        common_start=common_start,
        common_end=common_end,
        coverage_receipts=tuple(observed[symbol] for symbol in sorted(observed)),
        eligible_symbols=eligible_symbols,
        excluded_symbols=tuple(sorted(excluded)),
        development_symbols=development_symbols,
        asset_holdout_symbols=asset_holdout_symbols,
        blocks=blocks,
        outer_folds=outer_folds,
        temporal_holdout=blocks[5],
        grid_sha256=grid_sha256,
        split_sha256=hash_json("common-grid-split-v1", split_payload),
        factory_token=_SPLIT_FACTORY,
    )


def _grid_block(start: datetime, end: datetime, step: timedelta, *, label: str) -> GridBlock:
    seconds = (end - start).total_seconds()
    step_seconds = step.total_seconds()
    if seconds <= 0 or seconds % step_seconds:
        raise ValueError("grid interval is not divisible by the target timeframe")
    count = int(seconds // step_seconds)
    payload = {
        "label": label,
        "start": start,
        "end": end,
        "step_seconds": int(step_seconds),
        "timestamp_count": count,
    }
    return GridBlock(start, end, count, hash_json("common-grid-block-v1", payload))


def _outer_fold(
    *,
    programme_id: str,
    timeframe: str,
    blocks: tuple[GridBlock, ...],
    test_block_index: int,
    step: timedelta,
) -> OuterFold:
    training = _grid_block(
        blocks[0].start,
        blocks[test_block_index].start,
        step,
        label=f"outer-{test_block_index}-training",
    )
    test = blocks[test_block_index]
    q, remainder = divmod(training.timestamp_count, 4)
    if q < 1:
        raise ValueError("outer training grid is too short for four inner subblocks")
    remainder_end = training.start + remainder * step
    remainder_payload = {
        "programme_id": programme_id,
        "timeframe": timeframe,
        "outer_test_block_index": test_block_index,
        "start": training.start,
        "end": remainder_end,
        "timestamp_count": remainder,
    }
    subblocks = tuple(
        _grid_block(
            remainder_end + index * q * step,
            remainder_end + (index + 1) * q * step,
            step,
            label=f"outer-{test_block_index}-inner-subblock-{index}",
        )
        for index in range(4)
    )
    inner_folds: list[InnerFold] = []
    for index in range(1, 4):
        inner_training = _grid_block(
            subblocks[0].start,
            subblocks[index].start,
            step,
            label=f"outer-{test_block_index}-inner-{index}-training",
        )
        inner_test = subblocks[index]
        payload = {
            "programme_id": programme_id,
            "timeframe": timeframe,
            "outer_test_block_index": test_block_index,
            "inner_test_subblock_index": index,
            "training": inner_training.to_dict(),
            "test": inner_test.to_dict(),
        }
        inner_folds.append(
            InnerFold(
                fold_id=f"outer-{test_block_index}-inner-{index}",
                training=inner_training,
                test=inner_test,
                fold_sha256=hash_json("inner-walk-forward-fold-v1", payload),
            )
        )
    payload = {
        "programme_id": programme_id,
        "timeframe": timeframe,
        "test_block_index": test_block_index,
        "training": training.to_dict(),
        "test": test.to_dict(),
        "prefix_remainder": remainder_payload,
        "inner_folds": [fold.to_dict() for fold in inner_folds],
    }
    return OuterFold(
        fold_id=f"outer-{test_block_index}",
        test_block_index=test_block_index,
        training=training,
        test=test,
        prefix_remainder_count=remainder,
        prefix_remainder_start=training.start,
        prefix_remainder_end=remainder_end,
        prefix_remainder_sha256=hash_json("inner-prefix-remainder-v1", remainder_payload),
        inner_folds=tuple(inner_folds),
        fold_sha256=hash_json("outer-walk-forward-fold-v1", payload),
    )


@dataclass(frozen=True, slots=True)
class EventInterval:
    """Feature and label intervals for one outcome-bearing candidate event."""

    row_id: str
    symbol: str
    feature_start: datetime
    feature_end: datetime
    label_start: datetime
    label_end: datetime

    def __post_init__(self) -> None:
        if not self.row_id:
            raise ValueError("event row_id must not be empty")
        if not self.symbol:
            raise ValueError("event symbol must not be empty")
        for label in ("feature_start", "feature_end", "label_start", "label_end"):
            _require_utc(getattr(self, label), label)
        if not (self.feature_start < self.feature_end <= self.label_start < self.label_end):
            raise ValueError("event feature and label intervals must be causal and half-open")

    def to_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "symbol": self.symbol,
            "feature_start": self.feature_start,
            "feature_end": self.feature_end,
            "label_start": self.label_start,
            "label_end": self.label_end,
        }


@dataclass(frozen=True, slots=True)
class PurgeEmbargoResult:
    """Identity-bound selection after interval purge and exact horizon embargo."""

    admitted_row_ids: tuple[str, ...]
    purged_row_ids: tuple[str, ...]
    embargoed_row_ids: tuple[str, ...]
    test_row_ids: tuple[str, ...]
    test_start: datetime
    test_end: datetime
    embargo_start: datetime
    embargo_end: datetime
    protected_embargoes: tuple[tuple[datetime, datetime], ...]
    result_sha256: str
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _SELECTION_FACTORY:
            raise TypeError("PurgeEmbargoResult requires the canonical selection factory")
        sets = tuple(
            set(values)
            for values in (
                self.admitted_row_ids,
                self.purged_row_ids,
                self.embargoed_row_ids,
                self.test_row_ids,
            )
        )
        if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
            raise ValueError("purge/embargo result classes must be disjoint")
        if not self.protected_embargoes:
            raise ValueError("purge/embargo result must bind at least the current embargo")
        previous_end: datetime | None = None
        for start, end in self.protected_embargoes:
            _require_utc(start, "protected embargo start")
            _require_utc(end, "protected embargo end")
            if end <= start:
                raise ValueError("protected embargo intervals must be positive")
            if previous_end is not None and start < previous_end:
                raise ValueError("protected embargo intervals must be canonical and disjoint")
            previous_end = end
        if self.protected_embargoes[-1] != (self.embargo_start, self.embargo_end):
            raise ValueError("current embargo must be the final protected embargo")
        _require_sha256(self.result_sha256, "result_sha256")


def intervals_overlap(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> bool:
    """Return whether two non-empty half-open UTC intervals overlap."""

    for value, label in (
        (left_start, "left_start"),
        (left_end, "left_end"),
        (right_start, "right_start"),
        (right_end, "right_end"),
    ):
        _require_utc(value, label)
    if left_end <= left_start or right_end <= right_start:
        raise ValueError("intervals must be positive")
    return left_start < right_end and right_start < left_end


def purge_and_embargo(
    training: Sequence[EventInterval],
    test: Sequence[EventInterval],
    *,
    test_start: datetime,
    test_end: datetime,
    horizon: timedelta,
    prior_embargoes: Sequence[tuple[datetime, datetime]] = (),
    budget: ValidationWorkBudget,
) -> PurgeEmbargoResult:
    """Exclude explicit feature/label intersections and exact post-test embargo."""

    _require_utc(test_start, "test_start")
    _require_utc(test_end, "test_end")
    if test_end <= test_start:
        raise ValueError("test interval must be positive")
    if not isinstance(horizon, timedelta) or horizon <= timedelta(0):
        raise ValueError("embargo horizon must be positive")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("purge/embargo requires a ValidationWorkBudget")
    training_count = len(training)
    test_count = len(test)
    prior_embargo_count = len(prior_embargoes)
    budget.preflight(
        ValidationWorkDemand(
            events=training_count + test_count,
            ranges=prior_embargo_count + 1,
        ),
        deferred_work=training,
    )
    frozen_training = tuple(training)
    frozen_test = tuple(test)
    frozen_prior_embargoes = tuple(prior_embargoes)
    if (
        len(frozen_training) != training_count
        or len(frozen_test) != test_count
        or len(frozen_prior_embargoes) != prior_embargo_count
    ):
        raise ValueError("purge/embargo input sequence changed during selection")
    all_events = (*frozen_training, *frozen_test)
    if any(not isinstance(event, EventInterval) for event in all_events):
        raise TypeError("purge/embargo inputs must contain EventInterval values")
    ids = [event.row_id for event in all_events]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event row_id in purge/embargo inputs")
    if any(
        event.feature_start < test_start
        or event.label_start < test_start
        or event.label_end > test_end
        for event in frozen_test
    ):
        raise ValueError("test event entry or label path leaves the test fold")

    test_intervals = (
        (test_start, test_end),
        *(
            interval
            for event in frozen_test
            for interval in (
                (event.feature_start, event.feature_end),
                (event.label_start, event.label_end),
            )
        ),
    )
    embargo_start = test_end
    embargo_end = test_end + horizon
    for prior_start, prior_end in frozen_prior_embargoes:
        _require_utc(prior_start, "prior embargo start")
        _require_utc(prior_end, "prior embargo end")
        if prior_end <= prior_start:
            raise ValueError("prior embargo interval must be positive")
        if prior_end > test_start:
            raise ValueError("prior embargo must end before the current test interval")
    protected_embargoes = tuple(sorted((*frozen_prior_embargoes, (embargo_start, embargo_end))))
    if len(set(protected_embargoes)) != len(protected_embargoes):
        raise ValueError("duplicate protected embargo interval")
    if any(
        left_end > right_start
        for (_left_start, left_end), (right_start, _right_end) in zip(
            protected_embargoes,
            protected_embargoes[1:],
            strict=False,
        )
    ):
        raise ValueError("protected embargo intervals must not overlap")
    admitted: list[str] = []
    purged: list[str] = []
    embargoed: list[str] = []
    for event in frozen_training:
        candidate_intervals = (
            (event.feature_start, event.feature_end),
            (event.label_start, event.label_end),
        )
        if any(
            intervals_overlap(*candidate, *protected)
            for candidate in candidate_intervals
            for protected in test_intervals
        ):
            purged.append(event.row_id)
        elif any(
            intervals_overlap(*candidate, protected_start, protected_end)
            for candidate in candidate_intervals
            for protected_start, protected_end in protected_embargoes
        ):
            embargoed.append(event.row_id)
        else:
            admitted.append(event.row_id)
    payload = {
        "training": [event.to_dict() for event in frozen_training],
        "test": [event.to_dict() for event in frozen_test],
        "test_start": test_start,
        "test_end": test_end,
        "embargo_start": embargo_start,
        "embargo_end": embargo_end,
        "protected_embargoes": [list(item) for item in protected_embargoes],
        "admitted_row_ids": admitted,
        "purged_row_ids": purged,
        "embargoed_row_ids": embargoed,
    }
    return PurgeEmbargoResult(
        admitted_row_ids=tuple(admitted),
        purged_row_ids=tuple(purged),
        embargoed_row_ids=tuple(embargoed),
        test_row_ids=tuple(event.row_id for event in frozen_test),
        test_start=test_start,
        test_end=test_end,
        embargo_start=embargo_start,
        embargo_end=embargo_end,
        protected_embargoes=protected_embargoes,
        result_sha256=hash_json("purge-embargo-selection-v1", payload),
        factory_token=_SELECTION_FACTORY,
    )


@dataclass(frozen=True, slots=True)
class CandidateEligibility:
    """One metadata decision; Task 8 must verify its receipt before any real access."""

    programme_id: str
    candidate_id: str
    family: str
    eligible: bool
    eligibility_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_programme_id(self.programme_id)
        if (
            not isinstance(self.candidate_id, str)
            or _CANDIDATE_ID.fullmatch(self.candidate_id) is None
        ):
            raise ValueError("candidate_id must be a content-addressed human-origin candidate")
        if self.family not in EXPECTED_FAMILIES:
            raise ValueError("candidate eligibility family is unsupported")
        if not isinstance(self.eligible, bool):
            raise TypeError("candidate eligibility flag must be boolean")
        _require_sha256(self.eligibility_receipt_sha256, "eligibility_receipt_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "programme_id": self.programme_id,
            "candidate_id": self.candidate_id,
            "family": self.family,
            "eligible": self.eligible,
            "eligibility_receipt_sha256": self.eligibility_receipt_sha256,
        }


def _verify_common_grid_split(
    split: CommonGridSplit,
    *,
    budget: ValidationWorkBudget,
) -> CommonGridSplit:
    if not isinstance(split, CommonGridSplit):
        raise TypeError("final batch requires a canonical CommonGridSplit")
    canonical = freeze_common_grid_split(
        programme_id=split.programme_id,
        coverages=split.coverage_receipts,
        timeframe=split.timeframe,
        budget=budget,
    )
    if split != canonical:
        raise ValueError("common-grid split or temporal holdout metadata is not canonical")
    return canonical


@dataclass(frozen=True, slots=True)
class FinalHoldoutBatch:
    """Metadata-only canonical set of all development-eligible candidates."""

    programme_id: str
    split_sha256: str
    timeframe: str
    common_start: datetime
    temporal_holdout_start: datetime
    temporal_holdout_end: datetime
    temporal_holdout_sha256: str
    development_symbols: tuple[str, ...]
    asset_holdout_symbols: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    family_order: tuple[str, ...]
    eligibility_receipt_sha256s: tuple[str, ...]
    eligibility_decisions: tuple[CandidateEligibility, ...]
    batch_sha256: str
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _FINAL_BATCH_FACTORY:
            raise TypeError("FinalHoldoutBatch requires the canonical batch factory")
        _require_programme_id(self.programme_id)
        _require_sha256(self.split_sha256, "split_sha256")
        if self.timeframe not in ("1h", "4h"):
            raise ValueError("final batch timeframe must be 1h or 4h")
        _require_utc(self.common_start, "final batch common start")
        _require_utc(self.temporal_holdout_start, "temporal holdout start")
        _require_utc(self.temporal_holdout_end, "temporal holdout end")
        if not self.common_start < self.temporal_holdout_start < self.temporal_holdout_end:
            raise ValueError("final batch temporal holdout interval must be positive")
        _require_sha256(self.temporal_holdout_sha256, "temporal_holdout_sha256")
        if not self.development_symbols or set(self.development_symbols) & set(
            self.asset_holdout_symbols
        ):
            raise ValueError("final batch symbol components must be non-empty and disjoint")
        if self.development_symbols != tuple(sorted(set(self.development_symbols))):
            raise ValueError("final batch development symbols must be canonical")
        if self.asset_holdout_symbols != tuple(sorted(set(self.asset_holdout_symbols))):
            raise ValueError("final batch asset-holdout symbols must be canonical")
        if not (
            len(self.candidate_ids)
            == len(self.family_order)
            == len(self.eligibility_receipt_sha256s)
        ):
            raise ValueError("final batch metadata lengths differ")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("final batch candidate identities must be unique")
        for candidate_id in self.candidate_ids:
            if _CANDIDATE_ID.fullmatch(candidate_id) is None:
                raise ValueError("final batch candidate identity is invalid")
        if any(family not in EXPECTED_FAMILIES for family in self.family_order):
            raise ValueError("final batch family is unsupported")
        for receipt in self.eligibility_receipt_sha256s:
            _require_sha256(receipt, "eligibility receipt SHA")
        canonical_decisions = tuple(
            sorted(
                self.eligibility_decisions,
                key=lambda item: (EXPECTED_FAMILIES.index(item.family), item.candidate_id),
            )
        )
        if self.eligibility_decisions != canonical_decisions:
            raise ValueError("final batch eligibility decisions are not canonical")
        if any(item.programme_id != self.programme_id for item in canonical_decisions):
            raise ValueError("final batch eligibility decision belongs to another programme")
        if len({item.candidate_id for item in canonical_decisions}) != len(canonical_decisions):
            raise ValueError("final batch eligibility decision identities must be unique")
        eligible_decisions = tuple(item for item in canonical_decisions if item.eligible)
        if self.candidate_ids != tuple(item.candidate_id for item in eligible_decisions):
            raise ValueError("final batch candidate identities differ from eligible decisions")
        if self.family_order != tuple(item.family for item in eligible_decisions):
            raise ValueError("final batch families differ from eligible decisions")
        if self.eligibility_receipt_sha256s != tuple(
            item.eligibility_receipt_sha256 for item in eligible_decisions
        ):
            raise ValueError("final batch receipts differ from eligible decisions")
        expected = _final_batch_sha256(
            programme_id=self.programme_id,
            split_sha256=self.split_sha256,
            timeframe=self.timeframe,
            common_start=self.common_start,
            temporal_holdout_start=self.temporal_holdout_start,
            temporal_holdout_end=self.temporal_holdout_end,
            temporal_holdout_sha256=self.temporal_holdout_sha256,
            development_symbols=self.development_symbols,
            asset_holdout_symbols=self.asset_holdout_symbols,
            candidate_ids=self.candidate_ids,
            family_order=self.family_order,
            eligibility_receipt_sha256s=self.eligibility_receipt_sha256s,
            eligibility_decisions=self.eligibility_decisions,
        )
        if self.batch_sha256 != expected:
            raise ValueError("final batch SHA differs from its canonical metadata")


def freeze_final_batch(
    *,
    programme_id: str,
    split: CommonGridSplit,
    eligibilities: Sequence[CandidateEligibility],
    budget: ValidationWorkBudget,
) -> FinalHoldoutBatch:
    """Freeze every and only eligible candidate after a pre-iteration size gate."""

    _require_programme_id(programme_id)
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("final batch requires a ValidationWorkBudget")
    canonical_split = _verify_common_grid_split(split, budget=budget)
    if canonical_split.programme_id != programme_id:
        raise ValueError("final batch split belongs to a different programme")
    declared_count = len(eligibilities)
    budget.preflight(
        ValidationWorkDemand(
            final_batch_candidates=declared_count,
        ),
        deferred_work=eligibilities,
    )
    observed = tuple(eligibilities)
    if len(observed) != declared_count:
        raise ValueError("final eligibility sequence changed during freeze")
    if any(not isinstance(item, CandidateEligibility) for item in observed):
        raise TypeError("final batch entries must be CandidateEligibility values")
    candidate_ids = [item.candidate_id for item in observed]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate candidate eligibility identity")
    if any(item.programme_id != programme_id for item in observed):
        raise ValueError("candidate eligibility belongs to a different programme")
    canonical_decisions = tuple(
        sorted(
            observed,
            key=lambda item: (EXPECTED_FAMILIES.index(item.family), item.candidate_id),
        )
    )
    eligible = tuple(item for item in canonical_decisions if item.eligible)
    budget.preflight(
        ValidationWorkDemand(
            final_holdout_candidates=len(observed),
            final_batch_candidates=len(eligible),
        )
    )
    frozen_candidate_ids = tuple(item.candidate_id for item in eligible)
    frozen_family_order = tuple(item.family for item in eligible)
    frozen_receipts = tuple(item.eligibility_receipt_sha256 for item in eligible)
    return FinalHoldoutBatch(
        programme_id=programme_id,
        split_sha256=canonical_split.split_sha256,
        timeframe=canonical_split.timeframe,
        common_start=canonical_split.common_start,
        temporal_holdout_start=canonical_split.temporal_holdout.start,
        temporal_holdout_end=canonical_split.temporal_holdout.end,
        temporal_holdout_sha256=canonical_split.temporal_holdout.block_sha256,
        development_symbols=canonical_split.development_symbols,
        asset_holdout_symbols=canonical_split.asset_holdout_symbols,
        candidate_ids=frozen_candidate_ids,
        family_order=frozen_family_order,
        eligibility_receipt_sha256s=frozen_receipts,
        eligibility_decisions=canonical_decisions,
        batch_sha256=_final_batch_sha256(
            programme_id=programme_id,
            split_sha256=canonical_split.split_sha256,
            timeframe=canonical_split.timeframe,
            common_start=canonical_split.common_start,
            temporal_holdout_start=canonical_split.temporal_holdout.start,
            temporal_holdout_end=canonical_split.temporal_holdout.end,
            temporal_holdout_sha256=canonical_split.temporal_holdout.block_sha256,
            development_symbols=canonical_split.development_symbols,
            asset_holdout_symbols=canonical_split.asset_holdout_symbols,
            candidate_ids=frozen_candidate_ids,
            family_order=frozen_family_order,
            eligibility_receipt_sha256s=frozen_receipts,
            eligibility_decisions=canonical_decisions,
        ),
        factory_token=_FINAL_BATCH_FACTORY,
    )


def _final_batch_sha256(
    *,
    programme_id: str,
    split_sha256: str,
    timeframe: str,
    common_start: datetime,
    temporal_holdout_start: datetime,
    temporal_holdout_end: datetime,
    temporal_holdout_sha256: str,
    development_symbols: tuple[str, ...],
    asset_holdout_symbols: tuple[str, ...],
    candidate_ids: tuple[str, ...],
    family_order: tuple[str, ...],
    eligibility_receipt_sha256s: tuple[str, ...],
    eligibility_decisions: tuple[CandidateEligibility, ...],
) -> str:
    return hash_json(
        "final-holdout-batch-v1",
        {
            "programme_id": programme_id,
            "split_sha256": split_sha256,
            "timeframe": timeframe,
            "common_start": common_start,
            "temporal_holdout_start": temporal_holdout_start,
            "temporal_holdout_end": temporal_holdout_end,
            "temporal_holdout_sha256": temporal_holdout_sha256,
            "development_symbols": list(development_symbols),
            "asset_holdout_symbols": list(asset_holdout_symbols),
            "candidate_ids": list(candidate_ids),
            "family_order": list(family_order),
            "eligibility_receipt_sha256s": list(eligibility_receipt_sha256s),
            "eligibility_decisions": [item.to_dict() for item in eligibility_decisions],
        },
    )


@dataclass(frozen=True, slots=True)
class VerifiedFinalAccess:
    """Single-attempt programme capability issued before any final-row iteration."""

    programme_id: str
    batch_sha256: str
    split_sha256: str
    timeframe: str
    common_start: datetime
    temporal_holdout_start: datetime
    temporal_holdout_end: datetime
    development_symbols: tuple[str, ...]
    asset_holdout_symbols: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    access_receipt_sha256: str
    access_record_path: Path | None = field(default=None, repr=False, compare=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _FINAL_ACCESS_FACTORY:
            raise TypeError("VerifiedFinalAccess requires the canonical access factory")
        _require_programme_id(self.programme_id)
        _require_sha256(self.batch_sha256, "batch_sha256")
        _require_sha256(self.split_sha256, "split_sha256")
        if self.timeframe not in ("1h", "4h"):
            raise ValueError("verified final access timeframe must be 1h or 4h")
        _require_utc(self.common_start, "final access common start")
        _require_utc(self.temporal_holdout_start, "final access temporal holdout start")
        _require_utc(self.temporal_holdout_end, "final access temporal holdout end")
        if not self.common_start < self.temporal_holdout_start < self.temporal_holdout_end:
            raise ValueError("verified final access holdout bounds are invalid")
        if (
            not self.development_symbols
            or not self.asset_holdout_symbols
            or set(self.development_symbols) & set(self.asset_holdout_symbols)
        ):
            raise ValueError("verified final access symbol components are invalid")
        _require_sha256(self.access_receipt_sha256, "access_receipt_sha256")
        if not self.candidate_ids or len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("verified final access requires a non-empty unique candidate batch")
        for candidate_id in self.candidate_ids:
            if _CANDIDATE_ID.fullmatch(candidate_id) is None:
                raise ValueError("verified final access candidate identity is invalid")
        if not isinstance(self.access_record_path, Path):
            raise TypeError("verified final access requires its durable access record path")
        expected = hash_json(
            "final-holdout-access-attempt-v1",
            _access_payload(
                programme_id=self.programme_id,
                batch_sha256=self.batch_sha256,
                split_sha256=self.split_sha256,
                timeframe=self.timeframe,
                common_start=self.common_start,
                temporal_holdout_start=self.temporal_holdout_start,
                temporal_holdout_end=self.temporal_holdout_end,
                development_symbols=self.development_symbols,
                asset_holdout_symbols=self.asset_holdout_symbols,
                candidate_ids=self.candidate_ids,
            ),
        )
        if self.access_receipt_sha256 != expected:
            raise ValueError("final access receipt SHA differs from its canonical attempt")


def _access_payload(
    *,
    programme_id: str,
    batch_sha256: str,
    split_sha256: str,
    timeframe: str,
    common_start: datetime,
    temporal_holdout_start: datetime,
    temporal_holdout_end: datetime,
    development_symbols: tuple[str, ...],
    asset_holdout_symbols: tuple[str, ...],
    candidate_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "state": "access_attempted",
        "programme_id": programme_id,
        "batch_sha256": batch_sha256,
        "split_sha256": split_sha256,
        "timeframe": timeframe,
        "common_start": common_start,
        "temporal_holdout_start": temporal_holdout_start,
        "temporal_holdout_end": temporal_holdout_end,
        "development_symbols": list(development_symbols),
        "asset_holdout_symbols": list(asset_holdout_symbols),
        "candidate_ids": list(candidate_ids),
    }


def _write_all(descriptor: int, encoded: bytes) -> None:
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("final access attempt write made no progress")
        view = view[written:]


def _create_access_record(access_root: Path, filename: str, encoded: bytes) -> Path:
    require_regular_directory(access_root)
    if os.name == "nt":
        with WindowsHandleFilesystem().create_regular_exclusive(
            access_root,
            filename,
        ) as descriptor:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        return access_root / filename

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor = os.open(access_root, directory_flags)
    try:
        metadata = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("final access root must remain a regular directory")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(filename, file_flags, 0o600, dir_fd=directory_descriptor)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise RuntimeError("final access record must be a regular file")
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return access_root / filename


def attempt_final_access(
    batch: FinalHoldoutBatch,
    *,
    access_root: Path,
) -> VerifiedFinalAccess:
    """Exercise the synthetic atomic primitive; Task 8 owns the real programme root."""

    if not isinstance(batch, FinalHoldoutBatch):
        raise TypeError("final access requires a canonical FinalHoldoutBatch")
    if batch.batch_sha256 != _final_batch_sha256(
        programme_id=batch.programme_id,
        split_sha256=batch.split_sha256,
        timeframe=batch.timeframe,
        common_start=batch.common_start,
        temporal_holdout_start=batch.temporal_holdout_start,
        temporal_holdout_end=batch.temporal_holdout_end,
        temporal_holdout_sha256=batch.temporal_holdout_sha256,
        development_symbols=batch.development_symbols,
        asset_holdout_symbols=batch.asset_holdout_symbols,
        candidate_ids=batch.candidate_ids,
        family_order=batch.family_order,
        eligibility_receipt_sha256s=batch.eligibility_receipt_sha256s,
        eligibility_decisions=batch.eligibility_decisions,
    ):
        raise ValueError("final batch SHA differs from its canonical metadata")
    if not batch.candidate_ids:
        raise ValueError("empty final batch cannot create an access attempt")
    if not isinstance(access_root, Path):
        raise TypeError("access_root must be a pathlib.Path")
    require_regular_directory(access_root)
    payload = _access_payload(
        programme_id=batch.programme_id,
        batch_sha256=batch.batch_sha256,
        split_sha256=batch.split_sha256,
        timeframe=batch.timeframe,
        common_start=batch.common_start,
        temporal_holdout_start=batch.temporal_holdout_start,
        temporal_holdout_end=batch.temporal_holdout_end,
        development_symbols=batch.development_symbols,
        asset_holdout_symbols=batch.asset_holdout_symbols,
        candidate_ids=batch.candidate_ids,
    )
    receipt_sha256 = hash_json("final-holdout-access-attempt-v1", payload)
    encoded = canonical_json("final-holdout-access-attempt-v1", payload)
    path = _create_access_record(
        access_root,
        f"{batch.programme_id}.access-attempt.json",
        encoded,
    )
    return VerifiedFinalAccess(
        programme_id=batch.programme_id,
        batch_sha256=batch.batch_sha256,
        split_sha256=batch.split_sha256,
        timeframe=batch.timeframe,
        common_start=batch.common_start,
        temporal_holdout_start=batch.temporal_holdout_start,
        temporal_holdout_end=batch.temporal_holdout_end,
        development_symbols=batch.development_symbols,
        asset_holdout_symbols=batch.asset_holdout_symbols,
        candidate_ids=batch.candidate_ids,
        access_receipt_sha256=receipt_sha256,
        access_record_path=path,
        factory_token=_FINAL_ACCESS_FACTORY,
    )


def verify_final_access(
    access: VerifiedFinalAccess,
    *,
    programme_id: str,
    batch_sha256: str,
    candidate_id: str,
    component: OutcomeComponent,
    symbol: str,
    timeframe: str,
    label_start: datetime,
    label_end: datetime,
) -> None:
    """Verify exact programme, batch, and candidate scope before a final read."""

    if not isinstance(access, VerifiedFinalAccess):
        raise TypeError("final outcome requires VerifiedFinalAccess")
    payload = _access_payload(
        programme_id=access.programme_id,
        batch_sha256=access.batch_sha256,
        split_sha256=access.split_sha256,
        timeframe=access.timeframe,
        common_start=access.common_start,
        temporal_holdout_start=access.temporal_holdout_start,
        temporal_holdout_end=access.temporal_holdout_end,
        development_symbols=access.development_symbols,
        asset_holdout_symbols=access.asset_holdout_symbols,
        candidate_ids=access.candidate_ids,
    )
    expected_bytes = canonical_json("final-holdout-access-attempt-v1", payload)
    expected_receipt = hash_json("final-holdout-access-attempt-v1", payload)
    if access.access_receipt_sha256 != expected_receipt:
        raise ValueError("final access receipt differs from its canonical attempt")
    if not isinstance(access.access_record_path, Path):
        raise ValueError("final access receipt has no durable access record")
    observed_bytes = read_bounded_regular(
        access.access_record_path,
        maximum=len(expected_bytes),
    )
    if observed_bytes != expected_bytes:
        raise ValueError("final access receipt differs from its durable access record")
    _require_programme_id(programme_id)
    _require_sha256(batch_sha256, "batch_sha256")
    if access.programme_id != programme_id:
        raise ValueError("final access programme differs from the outcome policy")
    if access.batch_sha256 != batch_sha256:
        raise ValueError("final access batch differs from the outcome policy")
    if candidate_id not in access.candidate_ids:
        raise ValueError("candidate is not authorised by the final access batch")
    if not isinstance(component, OutcomeComponent):
        raise TypeError("final access component must be an OutcomeComponent")
    if component not in (OutcomeComponent.FINAL_TEMPORAL, OutcomeComponent.FINAL_ASSET):
        raise ValueError("final access may authorise only a final outcome component")
    if not symbol:
        raise ValueError("final component symbol is invalid")
    if timeframe != access.timeframe:
        raise ValueError("final component timeframe differs from the frozen split")
    _require_utc(label_start, "final component label start")
    _require_utc(label_end, "final component label end")
    if label_end <= label_start:
        raise ValueError("final component label interval must be positive")
    if component == OutcomeComponent.FINAL_TEMPORAL:
        if symbol not in access.development_symbols:
            raise ValueError("final temporal component requires a development symbol")
        if not (
            access.temporal_holdout_start <= label_start < label_end <= access.temporal_holdout_end
        ):
            raise ValueError("final temporal interval must remain inside block 5")
        return
    if symbol not in access.asset_holdout_symbols:
        raise ValueError("final asset component requires an asset-holdout symbol")
    if not (access.common_start <= label_start < label_end <= access.temporal_holdout_start):
        raise ValueError("final asset interval must remain inside development blocks 0 through 4")


__all__ = [
    "CandidateEligibility",
    "CommonGridSplit",
    "EventInterval",
    "FinalHoldoutBatch",
    "GridBlock",
    "InnerFold",
    "OuterFold",
    "PurgeEmbargoResult",
    "SymbolCoverage",
    "VerifiedFinalAccess",
    "attempt_final_access",
    "freeze_common_grid_split",
    "freeze_final_batch",
    "intervals_overlap",
    "purge_and_embargo",
    "verify_final_access",
]
