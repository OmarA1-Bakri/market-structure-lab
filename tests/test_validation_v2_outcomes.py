from __future__ import annotations

import copy
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    open_verified_aggregate_series_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    CanonicalMinuteRowV2,
    MinutePathReadBudgetV2,
    VerifiedMinutePathV2,
    read_verified_minute_path_v2,
)
from market_structure_lab.research.candidates import (
    CandidateSignal,
    VerifiedCandidateSeriesV2,
    bridge_verified_aggregate_series_v2,
    detect_candidate_signals,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationSlotKind,
    candidate_definition_for_verified_series,
)
from market_structure_lab.research.outcomes import (
    DevelopmentOutcomeRowV2,
    attach_development_outcome_v2,
)
from market_structure_lab.research.validation_v2_costs import (
    REQUIRED_COST_DIMENSIONS_V2,
    VerifiedCostAuthorityV2,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    DevelopmentAccessAttemptLedgerV2,
    DevelopmentEventAssignmentV2,
    DevelopmentFoldSetV2,
    DevelopmentSplitPublicationV2,
    assign_development_event_v2,
    freeze_development_folds_v2,
)

pytest_plugins = ("test_aggregate_publication_v2",)

INCOMPLETE_COST_DIMENSIONS = (
    "fee",
    "spread",
    "slippage",
    "latency",
    "fill",
    "missed_fill",
    "capacity",
)


class ExplodingMinuteRows:
    def __iter__(self):
        raise AssertionError("minute rows were opened before development assignment")


def _slot() -> ValidationSlot:
    return next(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if (
            slot.kind is ValidationSlotKind.CORE
            and slot.family == "A"
            and slot.role == "donchian_breakout"
            and slot.timeframe == "1h"
            and slot.direction == "long"
            and slot.horizon_hours == 24
            and dict(slot.parameters)["lookback_hours"] == "24"
        )
    )


def _folds(split: DevelopmentSplitPublicationV2) -> DevelopmentFoldSetV2:
    return freeze_development_folds_v2(split=split, timeframe="1h")


def _extended_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, ...]:
    import test_aggregate_publication_v2 as fixtures

    real_datetime = fixtures.datetime

    def extended_datetime(*args: object, **kwargs: object) -> datetime:
        value = real_datetime(*args, **kwargs)
        if value == datetime(2025, 1, 7, tzinfo=UTC):
            return datetime(2025, 1, 25, tzinfo=UTC)
        return value

    monkeypatch.setattr(fixtures, "datetime", extended_datetime)
    return fixtures.v2_chain.__wrapped__(tmp_path, monkeypatch)


def _hourly_shape(hour: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if hour < 24:
        return Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")
    if hour == 24:
        return Decimal("100"), Decimal("103"), Decimal("99"), Decimal("102")
    if hour == 30:
        return Decimal("100"), Decimal("120"), Decimal("94"), Decimal("100")
    if hour == 32:
        return Decimal("100"), Decimal("106"), Decimal("80"), Decimal("100")
    if hour < 49:
        return Decimal("100"), Decimal("106"), Decimal("94"), Decimal("100")
    return Decimal("110"), Decimal("111"), Decimal("109"), Decimal("110")


def _minute_requests(boundary: Any) -> tuple[tuple[CanonicalMinuteRowV2, ...], ...]:
    requests: list[tuple[CanonicalMinuteRowV2, ...]] = []
    main_interval = boundary.allowed_intervals[1]
    for symbol in boundary.allowed_symbols:
        for interval in boundary.allowed_intervals:
            hours = 52 if interval == main_interval else 4
            rows: list[CanonicalMinuteRowV2] = []
            for hour in range(hours):
                open_, high, low, close = _hourly_shape(hour)
                for minute in range(60):
                    rows.append(
                        CanonicalMinuteRowV2(
                            timestamp=interval.start + timedelta(hours=hour, minutes=minute),
                            symbol=symbol,
                            timeframe="1m",
                            open=open_ if minute == 0 else close,
                            high=high,
                            low=low,
                            close=close,
                            volume=Decimal("1"),
                        )
                    )
            requests.append(tuple(rows))
    return tuple(requests)


def _aggregate_budget() -> AggregatePublicationBudgetV2:
    return AggregatePublicationBudgetV2(
        max_source_rows=100_000,
        max_source_bytes=100_000_000,
        max_parent_partitions=10_000,
        max_source_rows_per_chunk=240,
        max_members=1_000,
        max_aggregate_rows=20_000,
        max_rows_per_partition=31,
        max_output_bytes=100_000_000,
        max_output_files=10_000,
    )


def _minute_budget() -> MinutePathReadBudgetV2:
    return MinutePathReadBudgetV2(
        max_total_rows=100_000,
        max_total_bytes=100_000_000,
        max_partitions=10_000,
        max_returned_rows=2_000,
    )


def _pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    import test_aggregate_publication_v2 as fixtures

    coverage, split, boundary, availability = _extended_chain(tmp_path, monkeypatch)
    minute_publication = fixtures._publish_minute(  # noqa: SLF001
        (coverage, split, boundary, availability),
        tmp_path,
        rows=_minute_requests(boundary),
    )
    aggregate_publication = fixtures._publish_aggregate(  # noqa: SLF001
        (coverage, split, boundary, availability),
        minute_publication,
        tmp_path / "aggregate",
        budget=_aggregate_budget(),
    )
    member = next(
        item
        for item in aggregate_publication.members
        if item.interval_index == 1 and item.target_timeframe == "1h"
    )
    key = issue_aggregate_series_key_v2(
        aggregate_publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=0,
    )
    aggregate_series = open_verified_aggregate_series_v2(
        aggregate_publication,
        key,
        _aggregate_budget(),
    )
    bridged = bridge_verified_aggregate_series_v2(aggregate_series)
    definition = candidate_definition_for_verified_series(_slot(), bridged)
    signals = detect_candidate_signals(definition, bridged)
    assert len(signals) == 1
    signal = signals[0]
    folds = _folds(split)
    assignment = assign_development_event_v2(folds=folds, signal=signal)
    request = BoundaryRequestV2(
        symbol=signal.symbol,
        timeframe="1m",
        start=signal.legal_entry,
        end=signal.legal_entry + timedelta(hours=24),
        operation_kind=AccessOperationKindV2.FILE,
        target_identity=minute_publication.origin_sha256 or "",
    )
    audit = DevelopmentAccessAttemptLedgerV2(
        programme_id="VPV2-" + "1" * 64,
        attempt_id="VA-" + "2" * 64,
        boundary=boundary,
    )
    minute_path = read_verified_minute_path_v2(
        minute_publication,
        coverage,
        split,
        boundary,
        availability,
        request,
        24 * 60,
        _minute_budget(),
        audit,
    )
    return SimpleNamespace(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute_publication=minute_publication,
        aggregate_publication=aggregate_publication,
        aggregate_series=aggregate_series,
        bridged=bridged,
        signal=signal,
        folds=folds,
        assignment=assignment,
        minute_path=minute_path,
    )


def _cost_authority(
    pipeline: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> VerifiedCostAuthorityV2:
    from market_structure_lab.research import validation_v2_costs as costs
    import test_validation_v2_costs as cost_fixtures

    cost_fixtures._patch_parent_verifiers(monkeypatch)  # noqa: SLF001
    parent_root = tmp_path / "cost-parents"
    parent_root.mkdir()
    parents = cost_fixtures._parents(parent_root)  # noqa: SLF001
    parents.coverage = pipeline.coverage
    parents.split = pipeline.split
    parents.boundary = pipeline.boundary
    parents.availability = pipeline.availability
    parents.minute = pipeline.minute_publication
    parents.aggregate = pipeline.aggregate_publication
    parents.manifest.boundary_sha256 = pipeline.boundary.boundary_sha256
    parents.manifest.source_availability_sha256 = pipeline.availability.availability_sha256
    return costs.publish_validation_cost_authority_v2(
        source_publication=parents.minute,
        aggregate_publication=parents.aggregate,
        coverage=parents.coverage,
        split=parents.split,
        boundary=parents.boundary,
        availability=parents.availability,
        archive_manifest=parents.manifest,
        archive_manifest_path=parents.manifest_path,
        archive_acquisition=parents.acquisition,
        output_root=tmp_path / "cost-authority",
    )


def _attach(
    pipeline: SimpleNamespace,
    authority: VerifiedCostAuthorityV2,
) -> DevelopmentOutcomeRowV2:
    return attach_development_outcome_v2(
        pipeline.signal,
        aggregate_series=pipeline.aggregate_series,
        minute_path=pipeline.minute_path,
        assignment=pipeline.assignment,
        cost_authority=authority,
        horizon_hours=24,
    )


def test_fold_set_is_deterministic_metadata_only_and_excludes_final_scope(v2_chain) -> None:
    _coverage, split, _boundary, _availability = v2_chain

    first = _folds(split)
    replay = _folds(split)

    assert isinstance(first, DevelopmentFoldSetV2)
    assert first == replay
    assert first.timeframe == "1h"
    assert first.purge_hours == split.policy.purge_hours
    assert first.embargo_hours == split.policy.embargo_hours
    assert tuple(fold.test_block_index for fold in first.outer_folds) == (1, 2, 3, 4)
    assert tuple(len(fold.inner_folds) for fold in first.outer_folds) == (3, 3, 3, 3)
    assert all(fold.test.end <= split.temporal_holdout.start for fold in first.outer_folds)
    assert not set(first.development_symbols) & set(split.asset_holdout_symbols)
    serialized = repr(first.to_dict()).lower()
    assert "outcome" not in serialized
    assert "final_access" not in serialized
    assert "programme_id" not in serialized
    assert first.verify_original() is first
    with pytest.raises((TypeError, ValueError), match="registered|factory"):
        copy.copy(first).verify_original()


def test_fold_identity_changes_only_with_split_timeframe_or_frozen_exclusion_policy(
    v2_chain,
) -> None:
    from market_structure_lab.research.validation_v2_splits import (
        freeze_development_split_v2,
    )

    coverage, split, _boundary, _availability = v2_chain
    replay = _folds(split)
    four_hour = freeze_development_folds_v2(split=split, timeframe="4h")
    changed_split = freeze_development_split_v2(
        coverage=coverage,
        policy=replace(split.policy, embargo_hours=split.policy.embargo_hours + 1),
    )
    changed_embargo = _folds(changed_split)

    assert replay.fold_set_sha256 != four_hour.fold_set_sha256
    assert replay.fold_set_sha256 != changed_embargo.fold_set_sha256
    assert replay.split_identity == split.split_identity


def test_assignment_is_development_only_and_precedes_any_minute_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)

    assignment = assign_development_event_v2(
        folds=pipeline.folds,
        signal=pipeline.signal,
        minute_rows=ExplodingMinuteRows(),
    )

    assert assignment.signal_id == pipeline.signal.signal_id
    assert assignment.symbol in pipeline.split.development_symbols
    assert assignment.component == "development"
    assert assignment.partition_role == "outer_diagnostic"
    assert assignment.fold_id == "outer-1"
    assert assignment.fold_sha256 == pipeline.folds.outer_folds[0].fold_sha256
    assert assignment.fold_set_sha256 == pipeline.folds.fold_set_sha256
    assert assignment.split_identity == pipeline.split.split_identity
    assert assignment.source_publication_sha256 == pipeline.signal.source_publication_sha256
    assert assignment.source_series_sha256 == pipeline.signal.source_series_sha256
    assert assignment.label_end <= pipeline.split.temporal_holdout.start
    assert DevelopmentEventAssignmentV2.verify_original(assignment) is assignment

    constructor = {
        definition.name: getattr(assignment, definition.name)
        for definition in fields(assignment)
        if definition.init
    }
    with pytest.raises(TypeError, match="factory"):
        DevelopmentEventAssignmentV2(**constructor)
    with pytest.raises((TypeError, ValueError), match="registered|factory"):
        DevelopmentEventAssignmentV2.verify_original(copy.copy(assignment))
    lookalike = object.__new__(DevelopmentEventAssignmentV2)
    for definition in fields(assignment):
        object.__setattr__(lookalike, definition.name, getattr(assignment, definition.name))
    with pytest.raises((TypeError, ValueError), match="registered|factory"):
        DevelopmentEventAssignmentV2.verify_original(lookalike)
    object.__setattr__(assignment, "fold_id", "outer-2")
    with pytest.raises(ValueError, match="snapshot|original"):
        DevelopmentEventAssignmentV2.verify_original(assignment)


def test_assignment_rejects_asset_or_temporal_holdout_before_minute_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    forbidden = copy.copy(pipeline.signal)
    object.__setattr__(forbidden, "symbol", pipeline.split.asset_holdout_symbols[0])
    object.__setattr__(
        forbidden,
        "information_cutoff",
        pipeline.split.temporal_holdout.start + timedelta(hours=25),
    )
    object.__setattr__(forbidden, "legal_entry", forbidden.information_cutoff)

    with pytest.raises(
        (TypeError, ValueError, PermissionError),
        match="identity|holdout|final|registered|original",
    ):
        assign_development_event_v2(
            folds=pipeline.folds,
            signal=forbidden,
            minute_rows=ExplodingMinuteRows(),
        )


def test_assignment_and_outcome_reject_copied_and_coherently_rehashed_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)
    copied = copy.copy(pipeline.signal)
    coherent = copy.copy(pipeline.signal)
    object.__setattr__(
        coherent,
        "feature_start",
        coherent.feature_start + timedelta(hours=1),
    )
    object.__setattr__(
        coherent,
        "signal_id",
        "CS-" + hash_json("candidate-signal-v1", coherent.to_dict()),
    )

    for forged in (copied, coherent):
        with pytest.raises(
            (TypeError, ValueError),
            match="detector-issued|registered|original",
        ):
            assign_development_event_v2(folds=pipeline.folds, signal=forged)
        with pytest.raises(
            (TypeError, ValueError),
            match="detector-issued|registered|original",
        ):
            attach_development_outcome_v2(
                forged,
                aggregate_series=pipeline.aggregate_series,
                minute_path=pipeline.minute_path,
                assignment=pipeline.assignment,
                cost_authority=authority,
                horizon_hours=24,
            )


def test_attaches_exact_next_bar_half_open_path_and_gross_excursions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)

    outcome = _attach(pipeline, authority)

    assert isinstance(pipeline.bridged, VerifiedCandidateSeriesV2)
    assert isinstance(pipeline.signal, CandidateSignal)
    assert isinstance(pipeline.aggregate_series, VerifiedAggregateSeriesV2)
    assert isinstance(pipeline.minute_path, VerifiedMinutePathV2)
    assert outcome.signal_id == pipeline.signal.signal_id
    assert outcome.information_cutoff == pipeline.signal.information_cutoff
    assert outcome.entry_time == pipeline.signal.information_cutoff
    assert outcome.entry_time == pipeline.aggregate_series.rows[25].timestamp
    assert outcome.exit_time == outcome.entry_time + timedelta(hours=24)
    assert pipeline.minute_path.rows[0].timestamp == outcome.entry_time
    assert pipeline.minute_path.rows[-1].timestamp == outcome.exit_time - timedelta(minutes=1)
    assert all(row.timestamp != outcome.exit_time for row in pipeline.minute_path.rows)
    assert outcome.entry_price == Decimal("100")
    assert outcome.exit_price == Decimal("110")
    assert outcome.gross_signed_return == Decimal("0.1")
    assert outcome.mfe == Decimal("0.2")
    assert outcome.mae == Decimal("-0.2")
    assert outcome.outcome_id.startswith("DOV2-")
    assert outcome.assignment_id == pipeline.assignment.assignment_id
    assert outcome.fold_id == pipeline.assignment.fold_id
    assert outcome.fold_sha256 == pipeline.assignment.fold_sha256
    assert outcome.fold_set_sha256 == pipeline.folds.fold_set_sha256
    assert outcome.split_identity == pipeline.split.split_identity.value
    assert outcome.aggregate_publication_sha256 == pipeline.signal.source_publication_sha256
    assert outcome.aggregate_series_identity == pipeline.signal.source_series_sha256
    assert outcome.aggregate_identity == pipeline.aggregate_series.aggregate_identity.value
    assert outcome.aggregate_interval_index == pipeline.aggregate_series.key.interval_index
    assert outcome.minute_path_identity == pipeline.minute_path.path_identity
    assert outcome.minute_path_audit_identity == pipeline.minute_path.audit_binding.audit_identity
    assert outcome.minute_path_target_identity == pipeline.minute_path.request.target_identity
    assert DevelopmentOutcomeRowV2.verify_original(outcome) is outcome


def test_outcome_binds_aggregate_first_last_and_ordered_source_hashes_to_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    outcome = _attach(pipeline, _cost_authority(pipeline, tmp_path, monkeypatch))
    path_hashes = tuple(row.line_sha256 for row in pipeline.minute_path.rows)
    aggregate_rows = pipeline.aggregate_series.rows[25:49]

    assert outcome.source_first_row_sha256 == path_hashes[0]
    assert outcome.source_last_row_sha256 == path_hashes[-1]
    assert outcome.ordered_source_rows_sha256 == pipeline.minute_path.ordered_row_sha256
    for index, aggregate_row in enumerate(aggregate_rows):
        source_hashes = path_hashes[index * 60 : (index + 1) * 60]
        assert aggregate_row.source_first_row_sha256 == source_hashes[0]
        assert aggregate_row.source_last_row_sha256 == source_hashes[-1]
        assert aggregate_row.ordered_source_rows_sha256 == hash_json(
            "phase5-validation-aggregate-bar-source-order-v2",
            list(source_hashes),
        )
    assert outcome.aggregate_row_sha256 == tuple(row.row_sha256 for row in aggregate_rows)


def test_current_cost_authority_leaves_net_return_uncomputed_with_exact_gaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)
    outcome = _attach(pipeline, authority)

    assert REQUIRED_COST_DIMENSIONS_V2 == (
        "fee",
        "spread",
        "slippage",
        "funding",
        "latency",
        "fill",
        "missed_fill",
        "turnover",
        "capacity",
    )
    assert authority.incomplete_promotion_grade_dimensions == INCOMPLETE_COST_DIMENSIONS
    assert outcome.net_return is None
    assert outcome.incomplete_cost_dimensions == INCOMPLETE_COST_DIMENSIONS
    assert outcome.cost_authority_identity == authority.cost_identity.value
    assert outcome.component == "development"
    assert outcome.final_access_records == 0


def test_direct_replace_and_lookalike_outcome_objects_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)
    outcome = _attach(pipeline, authority)
    constructor = {
        definition.name: getattr(outcome, definition.name)
        for definition in fields(outcome)
        if definition.init
    }

    with pytest.raises(TypeError, match="factory|verifier"):
        DevelopmentOutcomeRowV2(**constructor)
    with pytest.raises(TypeError, match="factory|verifier"):
        replace(outcome)
    lookalike = object.__new__(DevelopmentOutcomeRowV2)
    for definition in fields(outcome):
        object.__setattr__(lookalike, definition.name, getattr(outcome, definition.name))
    with pytest.raises((TypeError, ValueError), match="original|registered|factory|verifier"):
        DevelopmentOutcomeRowV2.verify_original(lookalike)
    with pytest.raises((TypeError, ValueError), match="original|registered|factory"):
        attach_development_outcome_v2(
            pipeline.signal,
            aggregate_series=pipeline.aggregate_series,
            minute_path=pipeline.minute_path,
            assignment=copy.copy(pipeline.assignment),
            cost_authority=authority,
            horizon_hours=24,
        )
    with pytest.raises(TypeError, match="exact factory-issued aggregate series"):
        attach_development_outcome_v2(
            pipeline.signal,
            aggregate_series=object(),  # type: ignore[arg-type]
            minute_path=pipeline.minute_path,
            assignment=pipeline.assignment,
            cost_authority=authority,
            horizon_hours=24,
        )
    with pytest.raises((TypeError, ValueError), match="registered|original"):
        attach_development_outcome_v2(
            pipeline.signal,
            aggregate_series=copy.copy(pipeline.aggregate_series),
            minute_path=pipeline.minute_path,
            assignment=pipeline.assignment,
            cost_authority=authority,
            horizon_hours=24,
        )
    object.__setattr__(outcome, "fold_id", "outer-2")
    with pytest.raises(ValueError, match="snapshot|original"):
        DevelopmentOutcomeRowV2.verify_original(outcome)


def test_attach_rejects_non_original_minute_paths_before_outcome_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import outcomes as outcomes_module

    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)

    class MinutePathLookalike:
        def verify_original(self) -> MinutePathLookalike:
            return self

    class MutatedMinutePath(VerifiedMinutePathV2):
        def verify_original(self) -> MutatedMinutePath:
            return self

    lookalike = MinutePathLookalike()
    for definition in fields(VerifiedMinutePathV2):
        object.__setattr__(
            lookalike,
            definition.name,
            getattr(pipeline.minute_path, definition.name),
        )

    mutated = object.__new__(MutatedMinutePath)
    for definition in fields(VerifiedMinutePathV2):
        object.__setattr__(
            mutated,
            definition.name,
            getattr(pipeline.minute_path, definition.name),
        )
    forged_row = copy.copy(mutated.rows[1])
    object.__setattr__(forged_row, "high", Decimal("999"))
    object.__setattr__(mutated, "rows", (mutated.rows[0], forged_row, *mutated.rows[2:]))

    def unexpected_outcome_creation(*args: object, **kwargs: object) -> None:
        raise AssertionError("outcome construction preceded minute-path authority rejection")

    monkeypatch.setattr(
        outcomes_module,
        "DevelopmentOutcomeRowV2",
        unexpected_outcome_creation,
    )
    cases = (
        (lookalike, TypeError, "exact factory-issued minute path"),
        (copy.copy(pipeline.minute_path), ValueError, "registered original"),
        (mutated, TypeError, "exact factory-issued minute path"),
    )
    for minute_path, error, message in cases:
        with pytest.raises(error, match=message):
            attach_development_outcome_v2(
                pipeline.signal,
                aggregate_series=pipeline.aggregate_series,
                minute_path=minute_path,  # type: ignore[arg-type]
                assignment=pipeline.assignment,
                cost_authority=authority,
                horizon_hours=24,
            )


@pytest.mark.parametrize("target", ("aggregate", "minute"))
def test_swapped_authenticated_rows_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    authority = _cost_authority(pipeline, tmp_path, monkeypatch)
    capability = pipeline.aggregate_series if target == "aggregate" else pipeline.minute_path
    rows = capability.rows
    object.__setattr__(capability, "rows", (rows[1], rows[0], *rows[2:]))

    with pytest.raises(ValueError, match="identity|order|original|contiguous"):
        _attach(pipeline, authority)
