from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from market_structure_lab.core.identity import canonical_json, hash_canonical_json
from market_structure_lab.data.aggregate_bars import (
    CanonicalAggregateBar,
    canonical_source_row_identity,
    iter_complete_aggregate_bars,
    source_rows_sha256,
)
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)


PARENT_SHA256 = "a" * 64


def minute_frame(
    count: int,
    *,
    start: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    symbol: str = "SOLUSDT",
    timeframe: str = "1m",
    segment_id: int = 3,
) -> pl.DataFrame:
    rows = []
    for offset in range(count):
        value = 100.0 + offset
        rows.append(
            {
                "timestamp": start + timedelta(minutes=offset),
                "symbol": symbol,
                "timeframe": timeframe,
                "open": value,
                "high": value + 2.0,
                "low": value - 1.0,
                "close": value + 1.0,
                "volume": float(offset + 1),
                "segment_id": segment_id,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "timestamp": pl.Datetime("us", "UTC"),
            "symbol": pl.String,
            "timeframe": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "segment_id": pl.UInt64,
        },
    )


def rows(frame: pl.DataFrame) -> list[dict[str, object]]:
    return list(frame.iter_rows(named=True))


def demand(frame: pl.DataFrame, aggregate_bars: int) -> ValidationWorkDemand:
    source_rows = rows(frame)
    return ValidationWorkDemand(
        source_rows=frame.height,
        source_bytes=sum(
            len(
                canonical_json(
                    "canonical-source-minute",
                    {
                        "schema_version": 1,
                        **row,
                    },
                )
            )
            for row in source_rows
        ),
        aggregate_bars=aggregate_bars,
    )


def aggregate(
    batches: list[pl.DataFrame],
    *,
    target_timeframe: str,
    declared: ValidationWorkDemand | None = None,
    expected_source_sha256: str | None = None,
    budget: ValidationWorkBudget = ValidationWorkBudget(),
    parent_snapshot_sha256: str = PARENT_SHA256,
) -> list[CanonicalAggregateBar]:
    combined = pl.concat(batches)
    return list(
        iter_complete_aggregate_bars(
            batches,
            target_timeframe=target_timeframe,
            expected_source_sha256=(expected_source_sha256 or source_rows_sha256(rows(combined))),
            parent_snapshot_sha256=parent_snapshot_sha256,
            demand=declared
            or demand(
                combined,
                combined.height // {"15m": 15, "1h": 60, "4h": 240}.get(target_timeframe, 1),
            ),
            budget=budget,
        )
    )


def minutes(timeframe: str) -> int:
    return {"15m": 15, "1h": 60, "4h": 240}[timeframe]


@pytest.mark.parametrize("target_timeframe", ("15m", "1h", "4h"))
def test_complete_utc_half_open_aggregate_preserves_ohlcv_and_source_identity(
    target_timeframe: str,
) -> None:
    count = minutes(target_timeframe)
    frame = minute_frame(count)

    [bar] = aggregate([frame], target_timeframe=target_timeframe)

    assert bar.timestamp == datetime(2025, 1, 1, tzinfo=UTC)
    assert bar.bar_close == bar.timestamp + timedelta(minutes=count)
    assert (bar.open, bar.high, bar.low, bar.close) == (
        100.0,
        100.0 + count - 1 + 2.0,
        99.0,
        100.0 + count - 1 + 1.0,
    )
    assert bar.volume == sum(float(index + 1) for index in range(count))
    assert bar.symbol == "SOLUSDT"
    assert bar.source_timeframe == "1m"
    assert bar.target_timeframe == target_timeframe
    assert bar.segment_id == 3
    assert bar.continuity == "complete-contiguous-1m-v1"
    assert bar.source_row_count == count
    assert bar.source_row_ids == tuple(canonical_source_row_identity(row) for row in rows(frame))
    assert bar.source_sha256 == source_rows_sha256(rows(frame))
    assert bar.parent_snapshot_sha256 == PARENT_SHA256
    assert len(bar.row_sha256) == 64


def test_streaming_arbitrary_chunks_and_one_batch_emit_identical_rows() -> None:
    frame = minute_frame(120)
    one_batch = aggregate([frame], target_timeframe="1h")
    chunks = [frame.slice(0, 7), frame.slice(7, 58), frame.slice(65, 1), frame.slice(66)]

    streamed = aggregate(chunks, target_timeframe="1h")

    assert streamed == one_batch
    assert [item.to_json_line() for item in streamed] == [item.to_json_line() for item in one_batch]


@pytest.mark.parametrize(
    ("frame", "message"),
    (
        (minute_frame(15, start=datetime(2025, 1, 1, 0, 1, tzinfo=UTC)), "first source"),
        (minute_frame(14), "partial final"),
    ),
)
def test_partial_first_or_last_target_period_is_rejected(frame: pl.DataFrame, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        aggregate([frame], target_timeframe="15m")


@pytest.mark.parametrize("mutation", ("gap", "duplicate", "reordered"))
def test_gap_duplicate_or_reordering_is_rejected_without_filling(mutation: str) -> None:
    frame = minute_frame(15)
    if mutation == "gap":
        frame = pl.concat((frame.slice(0, 7), frame.slice(8))).with_columns(
            pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
        )
    elif mutation == "duplicate":
        frame = pl.concat((frame.slice(0, 7), frame.slice(6, 1), frame.slice(7)))
    else:
        frame = pl.concat((frame.slice(0, 7), frame.slice(8, 1), frame.slice(7, 1), frame.slice(9)))

    with pytest.raises(ValueError, match="gap|duplicate|reorder|out-of-order"):
        aggregate([frame], target_timeframe="15m")


@pytest.mark.parametrize("column", ("symbol", "timeframe", "segment_id"))
def test_mixed_source_series_or_segment_is_rejected(column: str) -> None:
    frame = minute_frame(15)
    value: object = {"symbol": "BTCUSDT", "timeframe": "2m", "segment_id": 4}[column]
    frame = frame.with_columns(
        pl.when(pl.int_range(pl.len()) == 8)
        .then(pl.lit(value))
        .otherwise(pl.col(column))
        .alias(column)
    )

    with pytest.raises(ValueError, match="symbol|timeframe|segment"):
        aggregate([frame], target_timeframe="15m")


@pytest.mark.parametrize("target", ("5m", "90m", "1d", "nonsense"))
def test_only_supported_integral_target_timeframes_are_accepted(target: str) -> None:
    frame = minute_frame(15)
    with pytest.raises(ValueError, match="target timeframe|supported"):
        aggregate([frame], target_timeframe=target)


def test_source_digest_tampering_is_rejected() -> None:
    frame = minute_frame(15)
    with pytest.raises(ValueError, match="source digest"):
        aggregate(
            [frame],
            target_timeframe="15m",
            expected_source_sha256="f" * 64,
        )


class ExplodingBatches:
    def __init__(self) -> None:
        self.iterations = 0

    def __iter__(self) -> Iterator[pl.DataFrame]:
        self.iterations += 1
        raise AssertionError("source batches must not be iterated")
        yield


@pytest.mark.parametrize(
    ("demand_override", "budget_override", "stage"),
    (
        ({"source_rows": 2}, {"max_source_rows": 1}, "source_rows"),
        ({"source_bytes": 2}, {"max_source_bytes": 1}, "source_bytes"),
        ({"aggregate_bars": 2}, {"max_aggregate_bars": 1}, "aggregate_bars"),
    ),
)
def test_over_budget_declaration_rejects_before_source_iteration(
    demand_override: dict[str, int], budget_override: dict[str, int], stage: str
) -> None:
    batches = ExplodingBatches()
    declared = ValidationWorkDemand(**demand_override)
    budget = replace(ValidationWorkBudget(), **budget_override)

    iterator = iter_complete_aggregate_bars(
        batches,
        target_timeframe="15m",
        expected_source_sha256="a" * 64,
        parent_snapshot_sha256=PARENT_SHA256,
        demand=declared,
        budget=budget,
    )
    with pytest.raises(ValidationWorkBudgetViolation, match=stage):
        next(iterator)
    assert batches.iterations == 0


def test_actual_source_rows_cannot_exceed_declared_demand() -> None:
    frame = minute_frame(15)
    declared = demand(frame, 1)
    declared = replace(declared, source_rows=14)
    with pytest.raises(ValidationWorkBudgetViolation, match="source_rows"):
        aggregate([frame], target_timeframe="15m", declared=declared)


def test_source_schema_is_exact_and_rejects_extra_or_changed_columns() -> None:
    frame = minute_frame(15)
    with pytest.raises(ValueError, match="schema"):
        aggregate(
            [frame.with_columns(pl.lit("extra").alias("future_return"))], target_timeframe="15m"
        )
    with pytest.raises(ValueError, match="schema"):
        aggregate([frame.with_columns(pl.col("open").cast(pl.Float32))], target_timeframe="15m")


def test_row_identity_changes_for_timestamp_numeric_source_order_and_parent_mutations() -> None:
    frame = minute_frame(15)
    [base] = aggregate([frame], target_timeframe="15m")
    numeric = replace(base, high=base.high + 0.25, row_sha256="")
    timestamp = replace(
        base,
        timestamp=base.timestamp + timedelta(minutes=15),
        bar_close=base.bar_close + timedelta(minutes=15),
        row_sha256="",
    )
    ordered_ids = tuple(reversed(base.source_row_ids))
    source_order = replace(
        base,
        source_row_ids=ordered_ids,
        source_sha256=source_rows_sha256(ordered_ids, identities=True),
        row_sha256="",
    )
    parent = replace(base, parent_snapshot_sha256="b" * 64, row_sha256="")

    assert (
        len(
            {
                base.row_sha256,
                numeric.row_sha256,
                timestamp.row_sha256,
                source_order.row_sha256,
                parent.row_sha256,
            }
        )
        == 5
    )


def test_aggregate_row_schema_and_serialized_byte_tampering_reject() -> None:
    [bar] = aggregate([minute_frame(15)], target_timeframe="15m")
    with pytest.raises(ValueError, match="schema"):
        replace(bar, schema_version=2, row_sha256="")

    encoded = canonical_json("canonical-aggregate-bar", bar.logical_dict())
    mutated = bytearray(encoded)
    mutated[len(mutated) // 2] ^= 1
    try:
        mutated_identity = hash_canonical_json(bytes(mutated))
    except ValueError:
        return
    assert mutated_identity != hash_canonical_json(encoded)
