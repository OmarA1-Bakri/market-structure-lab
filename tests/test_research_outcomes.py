from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from market_structure_lab.core.identity import canonical_json
from market_structure_lab.data.aggregate_bars import (
    canonical_source_row_identity,
    source_rows_sha256,
)
from market_structure_lab.data.aggregate_publication import (
    VerifiedAggregateSeries,
    publish_aggregate_bars,
    read_verified_aggregate_series,
)
from market_structure_lab.data.canonical import CANONICAL_SCHEMA
from market_structure_lab.data.export import (
    SnapshotIdentity,
    export_partitioned_snapshot,
)
from market_structure_lab.research.candidates import (
    CandidateSignal,
    candidate_definition_for_slot,
    detect_candidate_signals,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    OutcomeComponent,
    OutcomePolicy,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.outcomes import (
    AttachedOutcome,
    FinalHoldoutAccessRequired,
    attach_outcome,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


class ExplodingIterable:
    def __iter__(self):
        raise AssertionError("minute path was iterated before admission")


def _slot(direction: str) -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        if (
            slot.kind is ValidationSlotKind.CORE
            and slot.family == "A"
            and slot.role == "donchian_breakout"
            and slot.timeframe == "1h"
            and slot.direction == direction
            and slot.horizon_hours == 24
            and dict(slot.parameters)["lookback_hours"] == "24"
        ):
            return slot
    raise AssertionError("frozen A Donchian slot not found")


def _d_slot() -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        if (
            slot.kind is ValidationSlotKind.CORE
            and slot.family == "D"
            and slot.role == "candidate_primary"
            and slot.timeframe == "1h"
            and slot.direction == "short"
            and slot.horizon_hours == 8
            and dict(slot.parameters)["level_hours"] == "24"
        ):
            return slot
    raise AssertionError("frozen D reclaim slot not found")


def _minute_rows(
    hourly: list[tuple[float, float, float, float]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for hour, (open_, high, low, close) in enumerate(hourly):
        for minute in range(60):
            rows.append(
                {
                    "timestamp": START + timedelta(hours=hour, minutes=minute),
                    "symbol": "SOLUSDT",
                    "timeframe": "1m",
                    "open": open_ if minute == 0 else close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1.0,
                    "segment_id": 0,
                }
            )
    return rows


def _hourly(
    direction: int,
    *,
    include_exit: bool = True,
) -> list[tuple[float, float, float, float]]:
    ordinary = [(100.0, 101.0, 99.0, 100.0)] * 24
    breakout_close = 102.0 if direction == 1 else 98.0
    breakout = (
        (100.0, 103.0, 99.0, breakout_close)
        if direction == 1
        else (100.0, 101.0, 97.0, breakout_close)
    )
    path: list[tuple[float, float, float, float]] = []
    for index in range(24):
        high = 120.0 if index == 5 else 106.0
        low = 80.0 if index == 7 else 94.0
        path.append((100.0, high, low, 100.0))
    exit_open = 110.0 if direction == 1 else 90.0
    exit_bar = (exit_open, exit_open + 1.0, exit_open - 1.0, exit_open)
    return [*ordinary, breakout, *path, *([exit_bar] if include_exit else [])]


def _hourly_d() -> list[tuple[float, float, float, float]]:
    ordinary = [(100.0, 101.0, 99.0, 100.0)] * 24
    breach = (100.0, 103.0, 99.0, 100.0)
    confirmation = (100.0, 101.0, 99.0, 100.0)
    path = [(100.0, 106.0, 94.0, 100.0)] * 8
    path[2] = (100.0, 120.0, 94.0, 100.0)
    path[4] = (100.0, 106.0, 80.0, 100.0)
    exit_bar = (90.0, 91.0, 89.0, 90.0)
    return [*ordinary, breach, confirmation, *path, exit_bar]


def _source_bytes(rows: list[dict[str, object]]) -> int:
    return sum(
        len(canonical_json("canonical-source-minute", {"schema_version": 1, **row})) for row in rows
    )


def _series(
    tmp_path: Path,
    *,
    direction: int = 1,
    include_exit: bool = True,
    hourly: list[tuple[float, float, float, float]] | None = None,
) -> tuple[VerifiedAggregateSeries, list[dict[str, object]]]:
    rows = _minute_rows(
        hourly if hourly is not None else _hourly(direction, include_exit=include_exit)
    )
    frame = pl.DataFrame(
        rows,
        schema=pl.Schema({**dict(CANONICAL_SCHEMA), "segment_id": pl.UInt64}),
    )
    source = frame.drop("segment_id")
    snapshot_root = tmp_path / "snapshot"
    identity = SnapshotIdentity(
        dataset_version=f"DS-OUTCOME-{direction}",
        dump_sha256="1" * 64,
        recovery_sha256="2" * 64,
        mapping_version="canonical-outcome-test-v1",
        config_version="canonical-outcome-test-v1",
        code_commit="3" * 40,
    )
    snapshot = export_partitioned_snapshot(
        [source],
        output_root=snapshot_root,
        identity=identity,
        expected_row_count=len(rows),
    )
    snapshot_directory = snapshot_root / f"dataset_version={identity.dataset_version}"
    aggregate_count = len(rows) // 60
    demand = ValidationWorkDemand(
        source_rows=len(rows),
        source_bytes=_source_bytes(rows),
        aggregate_bars=aggregate_count,
        artifacts=1,
        artifact_bytes=16 * 1024 * 1024,
    )
    aggregate_root = tmp_path / "aggregate"
    manifest = publish_aggregate_bars(
        [frame],
        output_root=aggregate_root,
        parent_snapshot_directory=snapshot_directory,
        parent_snapshot_manifest=snapshot,
        symbol="SOLUSDT",
        segment_id=0,
        target_timeframe="1h",
        expected_source_sha256=source_rows_sha256(rows),
        config_version="outcome-test-v1",
        demand=demand,
        budget=ValidationWorkBudget(),
    )
    publication_directory = aggregate_root / "symbol=SOLUSDT/timeframe=1h/segment=0"
    return (
        read_verified_aggregate_series(
            publication_directory,
            expected_publication_sha256=manifest.publication_sha256,
            parent_snapshot_directory=snapshot_directory,
            expected_parent_snapshot_sha256=snapshot.snapshot_sha256,
            budget=ValidationWorkBudget(),
        ),
        rows,
    )


def _signal(series: VerifiedAggregateSeries, direction: int) -> CandidateSignal:
    definition = candidate_definition_for_slot(_slot("long" if direction == 1 else "short"), series)
    [signal] = detect_candidate_signals(definition, series)
    return signal


def _policy(series: VerifiedAggregateSeries) -> OutcomePolicy:
    return OutcomePolicy(
        horizon_hours=24,
        component=OutcomeComponent.DEVELOPMENT,
        aggregate_publication_sha256=series.publication_sha256,
        minute_publication_sha256=series.parent_snapshot_manifest.snapshot_sha256,
    )


def _path(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    entry = START + timedelta(hours=25)
    exit_ = entry + timedelta(hours=24)
    return [row for row in rows if entry <= row["timestamp"] < exit_]


def _path_for(
    rows: list[dict[str, object]],
    *,
    entry: datetime,
    horizon_hours: int,
) -> list[dict[str, object]]:
    exit_ = entry + timedelta(hours=horizon_hours)
    return [row for row in rows if entry <= row["timestamp"] < exit_]


@pytest.mark.parametrize(
    ("direction", "expected_exit", "expected_gross"),
    [(1, 110.0, 0.1), (-1, 90.0, 0.1)],
)
def test_attaches_exact_next_bar_outcome_and_side_aware_excursions(
    tmp_path: Path,
    direction: int,
    expected_exit: float,
    expected_gross: float,
) -> None:
    series, rows = _series(tmp_path, direction=direction)
    signal = _signal(series, direction)

    outcome = attach_outcome(
        signal,
        series,
        _path(rows),
        _policy(series),
        ValidationWorkBudget(),
    )

    assert outcome.signal_id == signal.signal_id
    assert outcome.candidate_id == signal.candidate_id
    assert outcome.feature_start == signal.feature_start
    assert outcome.information_cutoff == signal.information_cutoff
    assert outcome.entry_time == START + timedelta(hours=25)
    assert outcome.exit_time == START + timedelta(hours=49)
    assert outcome.label_start == outcome.entry_time
    assert outcome.label_end == outcome.exit_time
    assert outcome.entry_price == 100.0
    assert outcome.exit_price == expected_exit
    assert outcome.gross_signed_return == pytest.approx(expected_gross)
    assert outcome.mfe == pytest.approx(0.2)
    assert outcome.mae == pytest.approx(-0.2)
    assert outcome.path_row_count == 24 * 60
    assert outcome.minute_publication_sha256 == series.parent_snapshot_manifest.snapshot_sha256
    assert outcome.path_sha256 == source_rows_sha256(
        [canonical_source_row_identity(row) for row in _path(rows)],
        identities=True,
    )


def test_entry_minute_is_included_and_exit_minute_is_excluded(tmp_path: Path) -> None:
    series, rows = _series(tmp_path)
    path = _path(rows)
    assert path[0]["timestamp"] == START + timedelta(hours=25)
    assert path[-1]["timestamp"] == START + timedelta(hours=48, minutes=59)

    outcome = attach_outcome(
        _signal(series, 1),
        series,
        path,
        _policy(series),
        ValidationWorkBudget(),
    )

    assert outcome.path_row_count == 1_440
    assert outcome.exit_time not in {row["timestamp"] for row in path}


def test_family_d_enters_only_after_the_separate_confirmation_bar(tmp_path: Path) -> None:
    series, rows = _series(tmp_path, direction=-1, hourly=_hourly_d())
    definition = candidate_definition_for_slot(_d_slot(), series)
    [signal] = detect_candidate_signals(definition, series)
    policy = replace(_policy(series), horizon_hours=8)

    outcome = attach_outcome(
        signal,
        series,
        _path_for(rows, entry=START + timedelta(hours=26), horizon_hours=8),
        policy,
        ValidationWorkBudget(),
    )

    assert signal.information_cutoff == START + timedelta(hours=26)
    assert signal.legal_entry == START + timedelta(hours=26)
    assert outcome.entry_time == START + timedelta(hours=26)
    assert outcome.exit_time == START + timedelta(hours=34)
    assert outcome.gross_signed_return == pytest.approx(0.1)


@pytest.mark.parametrize("mutation", ["missing_entry", "missing_exit", "gap", "exit_included"])
def test_missing_or_discontinuous_entry_exit_and_path_reject(
    tmp_path: Path,
    mutation: str,
) -> None:
    series, rows = _series(tmp_path)
    signal = _signal(series, 1)
    path = _path(rows)
    if mutation == "missing_entry":
        path = path[1:]
    elif mutation == "gap":
        del path[10]
    elif mutation == "exit_included":
        path.append(next(row for row in rows if row["timestamp"] == START + timedelta(hours=49)))
    else:
        series, rows = _series(tmp_path / "missing-exit", include_exit=False)
        signal = _signal(series, 1)
        path = _path(rows)

    with pytest.raises((TypeError, ValueError), match="entry|exit|path|series|contiguous"):
        attach_outcome(
            signal,
            series,
            path,
            _policy(series),
            ValidationWorkBudget(),
        )


def test_same_bar_fantasy_fill_and_wrong_publication_reject_before_path_iteration(
    tmp_path: Path,
) -> None:
    series, _ = _series(tmp_path)
    signal = _signal(series, 1)
    policy = _policy(series)

    with pytest.raises(ValueError, match="publication"):
        attach_outcome(
            signal,
            series,
            ExplodingIterable(),
            replace(policy, aggregate_publication_sha256="f" * 64),
            ValidationWorkBudget(),
        )
    object.__setattr__(
        signal,
        "information_cutoff",
        signal.information_cutoff + timedelta(hours=1),
    )
    object.__setattr__(signal, "legal_entry", signal.legal_entry + timedelta(hours=1))
    with pytest.raises(ValueError, match="identity"):
        attach_outcome(
            signal,
            series,
            ExplodingIterable(),
            policy,
            ValidationWorkBudget(),
        )


def test_path_budget_and_final_holdout_guard_reject_before_iteration(tmp_path: Path) -> None:
    series, _ = _series(tmp_path)
    signal = _signal(series, 1)
    policy = _policy(series)

    with pytest.raises(ValueError, match="path_cells|work limit"):
        attach_outcome(
            signal,
            series,
            ExplodingIterable(),
            policy,
            replace(ValidationWorkBudget(), max_path_cells=1_439),
        )
    with pytest.raises(FinalHoldoutAccessRequired):
        attach_outcome(
            signal,
            series,
            ExplodingIterable(),
            replace(policy, component=OutcomeComponent.FINAL_TEMPORAL),
            ValidationWorkBudget(),
        )


def test_outcome_attachment_cannot_change_detector_identity(tmp_path: Path) -> None:
    series, rows = _series(tmp_path)
    signal = _signal(series, 1)
    before = (signal.signal_id, signal.candidate_id, signal.to_dict())

    first = attach_outcome(
        signal,
        series,
        _path(rows),
        _policy(series),
        ValidationWorkBudget(),
    )
    corrupted = _path(rows)
    corrupted[0] = {**corrupted[0], "high": 121.0}
    with pytest.raises(ValueError, match="path|identity|source"):
        attach_outcome(
            signal,
            series,
            corrupted,
            _policy(series),
            ValidationWorkBudget(),
        )

    assert (signal.signal_id, signal.candidate_id, signal.to_dict()) == before
    assert first.signal_id == signal.signal_id


def test_outcome_policy_and_receipt_are_identity_bound_and_not_caller_mintable(
    tmp_path: Path,
) -> None:
    series, rows = _series(tmp_path)
    signal = _signal(series, 1)
    policy = _policy(series)
    outcome = attach_outcome(
        signal,
        series,
        _path(rows),
        policy,
        ValidationWorkBudget(),
    )

    assert (
        len(
            {
                policy.sha256,
                replace(policy, horizon_hours=8).sha256,
                replace(policy, component=OutcomeComponent.FINAL_ASSET).sha256,
                replace(policy, minute_publication_sha256="f" * 64).sha256,
            }
        )
        == 4
    )
    with pytest.raises(TypeError, match="seal"):
        AttachedOutcome(**outcome.to_dict(), seal=object())


def test_outcome_api_is_available_from_the_narrow_research_boundary() -> None:
    from market_structure_lab.research import (
        AttachedOutcome as PublicAttachedOutcome,
    )
    from market_structure_lab.research import (
        OutcomeComponent as PublicOutcomeComponent,
    )
    from market_structure_lab.research import OutcomePolicy as PublicOutcomePolicy
    from market_structure_lab.research import attach_outcome as public_attach_outcome

    assert PublicAttachedOutcome is AttachedOutcome
    assert PublicOutcomeComponent is OutcomeComponent
    assert PublicOutcomePolicy is OutcomePolicy
    assert public_attach_outcome is attach_outcome
