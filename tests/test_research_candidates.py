from __future__ import annotations

from collections import Counter
import copy
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Protocol

import polars as pl
import pytest
import market_structure_lab.research.candidates as research_candidates
import market_structure_lab.research.models as research_models

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import (
    CONTINUITY_ID,
    CanonicalAggregateBar,
    canonical_source_row_identity,
    source_rows_sha256,
)
from market_structure_lab.data.aggregate_publication import (
    AGGREGATE_MANIFEST_NAME,
    AGGREGATE_SUCCESS_NAME,
    AggregatePublicationManifest,
    AggregateSourceSelectionReceipt,
    VerifiedAggregateSeries,
    read_verified_aggregate_series,
)
from market_structure_lab.data.export import PartitionRecord, SnapshotIdentity, SnapshotManifest
from market_structure_lab.data.price_precision import (
    SourcePricePrecisionUnavailable,
    read_source_price_precision_manifest,
)
from market_structure_lab.research.candidates import (
    A_SELECTOR_GRID,
    CandidateSignal,
    ProfileValueReferences,
    build_verified_profile_stream,
    candidate_definition_for_slot,
    detect_candidate_signals,
    profile_config_artifact_bytes,
    target_bars,
    value_migration_acceptance,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    CandidateDefinition,
    EXPECTED_FAMILIES,
    ValidationProgrammeConfig,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
)

PARENT = "b" * 64
START = datetime(2025, 1, 1, tzinfo=UTC)
_SERIES_TEMPORARIES: list[TemporaryDirectory[str]] = []
TASK14_CLOSEOUT = "5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d"
TASK14_EVIDENCE = "3482882c864f1471ec1dd682631544d0c404c542"
TASK15_PLAN = "807ac28ac5616fb837c1ccea1e2bc47572ae3984"
TASK15_EVIDENCE = "afce8e889aa645cd9e48e90631698b87866f287f"
IMPLEMENTATION_CHECKPOINT = "e655152ab729b3e1e530f1bc044febca3509a6fd"
IMPLEMENTATION_EVIDENCE = "6da307a0d4756c61ddcabdf01f00d828afb55d7e"
IMPLEMENTATION_DOCUMENT = "d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111"


class _CausalBarInput(Protocol):
    timestamp: datetime
    bar_close: datetime
    symbol: str
    target_timeframe: str
    segment_id: int
    source_row_count: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _slot(
    family: str,
    detector: str,
    *,
    timeframe: str = "1h",
    direction: str = "long",
    role: str | None = None,
    horizon: int | None = None,
    lookback: int | None = None,
) -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        parameters = dict(slot.parameters)
        if (
            slot.kind is ValidationSlotKind.CORE
            and slot.family == family
            and parameters["detector"] == detector
            and slot.timeframe == timeframe
            and slot.direction == direction
            and (role is None or slot.role == role)
            and (horizon is None or slot.horizon_hours == horizon)
            and (
                lookback is None
                or parameters.get("lookback_hours") == str(lookback)
                or parameters.get("momentum_hours") == str(lookback)
                or parameters.get("profile_hours") == str(lookback)
                or parameters.get("donchian_hours") == str(lookback)
                or parameters.get("level_hours") == str(lookback)
            )
        ):
            return slot
    raise AssertionError("frozen validation slot not found")


def _bar(
    index: int,
    *,
    timeframe: str = "1h",
    symbol: str = "BTCUSDT",
    segment: int = 7,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    volume: float = 10.0,
) -> CanonicalAggregateBar:
    minutes = {"1h": 60, "4h": 240}[timeframe]
    timestamp = START + index * timedelta(minutes=minutes)
    source_ids = tuple(
        canonical_source_row_identity(row)
        for row in _minute_rows(
            timestamp=timestamp,
            minutes=minutes,
            symbol=symbol,
            segment=segment,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
    )
    return CanonicalAggregateBar(
        schema_version=1,
        timestamp=timestamp,
        bar_close=timestamp + timedelta(minutes=minutes),
        symbol=symbol,
        source_timeframe="1m",
        target_timeframe=timeframe,
        segment_id=segment,
        continuity=CONTINUITY_ID,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source_row_count=minutes,
        source_row_ids=source_ids,
        source_sha256=source_rows_sha256(source_ids, identities=True),
        parent_snapshot_sha256=PARENT,
    )


def _minute_rows(
    *,
    timestamp: datetime,
    minutes: int,
    symbol: str,
    segment: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> list[dict[str, object]]:
    ordinary_volume = volume / minutes
    rows: list[dict[str, object]] = []
    for minute in range(minutes):
        rows.append(
            {
                "timestamp": timestamp + timedelta(minutes=minute),
                "symbol": symbol,
                "timeframe": "1m",
                "open": open_ if minute == 0 else close,
                "high": high,
                "low": low,
                "close": close,
                "volume": (
                    volume - ordinary_volume * (minutes - 1)
                    if minute == minutes - 1
                    else ordinary_volume
                ),
                "segment_id": segment,
            }
        )
    return rows


def _bars(
    closes: list[float],
    *,
    timeframe: str = "1h",
    volumes: list[float] | None = None,
    segment: int = 7,
) -> tuple[CanonicalAggregateBar, ...]:
    return tuple(
        _bar(
            index,
            timeframe=timeframe,
            segment=segment,
            open_=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=volumes[index] if volumes else 10.0,
        )
        for index, close in enumerate(closes)
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _independent_canonical_bar(bar: _CausalBarInput) -> CanonicalAggregateBar:
    minute_rows = _minute_rows(
        timestamp=bar.timestamp,
        minutes=bar.source_row_count,
        symbol=bar.symbol,
        segment=bar.segment_id,
        open_=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )
    source_row_ids = tuple(canonical_source_row_identity(row) for row in minute_rows)
    return CanonicalAggregateBar(
        schema_version=1,
        timestamp=bar.timestamp,
        bar_close=bar.bar_close,
        symbol=bar.symbol,
        source_timeframe="1m",
        target_timeframe=bar.target_timeframe,
        segment_id=bar.segment_id,
        continuity=CONTINUITY_ID,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        source_row_count=bar.source_row_count,
        source_row_ids=source_row_ids,
        source_sha256=source_rows_sha256(source_row_ids, identities=True),
        parent_snapshot_sha256=PARENT,
    )


def _series(bars: tuple[_CausalBarInput, ...]) -> VerifiedAggregateSeries:
    if not bars:
        raise ValueError("test publication requires bars")
    bars = tuple(_independent_canonical_bar(bar) for bar in bars)
    temporary = TemporaryDirectory(prefix="candidate-series-test-")
    _SERIES_TEMPORARIES.append(temporary)
    base = Path(temporary.name)
    parent_root = base / "parent"
    parent_root.mkdir()
    minute_rows = [
        row
        for bar in bars
        for row in _minute_rows(
            timestamp=bar.timestamp,
            minutes=bar.source_row_count,
            symbol=bar.symbol,
            segment=bar.segment_id,
            open_=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
    ]
    minute_frame = pl.DataFrame(
        minute_rows,
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
    parent_artifacts: list[tuple[PartitionRecord, bytes]] = []
    for date_value in minute_frame["timestamp"].dt.date().unique(maintain_order=True):
        daily = minute_frame.filter(pl.col("timestamp").dt.date() == date_value)
        parent_buffer = BytesIO()
        daily.write_parquet(parent_buffer, compression="zstd", statistics=True)
        parent_bytes = parent_buffer.getvalue()
        parent_artifacts.append(
            (
                PartitionRecord(
                    path=(
                        f"symbol=BTCUSDT/timeframe=1m/date={date_value.isoformat()}/part.parquet"
                    ),
                    sha256=hashlib.sha256(parent_bytes).hexdigest(),
                    row_count=daily.height,
                    min_timestamp=_iso(daily["timestamp"][0]),
                    max_timestamp=_iso(daily["timestamp"][-1]),
                ),
                parent_bytes,
            )
        )
    parent_partitions = tuple(record for record, _ in parent_artifacts)
    identity = SnapshotIdentity(
        dataset_version="DS-CANDIDATE-TEST",
        dump_sha256="1" * 64,
        recovery_sha256="2" * 64,
        mapping_version="canonical-test-v1",
        config_version="canonical-test-v1",
        code_commit="3" * 40,
    )
    parent_payload = {
        "schema_version": 1,
        "identity": identity.to_dict(),
        "row_count": len(minute_rows),
        "min_timestamp": parent_partitions[0].min_timestamp,
        "max_timestamp": parent_partitions[-1].max_timestamp,
        "partitions": [asdict(item) for item in parent_partitions],
    }
    parent_sha = hashlib.sha256(
        json.dumps(parent_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    parent_manifest = SnapshotManifest(
        schema_version=1,
        identity=identity,
        row_count=len(minute_rows),
        min_timestamp=parent_partitions[0].min_timestamp,
        max_timestamp=parent_partitions[-1].max_timestamp,
        partitions=parent_partitions,
        snapshot_sha256=parent_sha,
    )
    for parent_partition, parent_bytes in parent_artifacts:
        parent_file = parent_root / parent_partition.path
        parent_file.parent.mkdir(parents=True)
        parent_file.write_bytes(parent_bytes)
    (parent_root / "manifest.json").write_text(parent_manifest.to_json(), encoding="utf-8")
    (parent_root / "_SUCCESS").write_text(f"{parent_sha}\n", encoding="utf-8")

    normalized_bars = tuple(
        replace(
            bar,
            source_row_ids=tuple(
                canonical_source_row_identity(row)
                for row in minute_rows[
                    index * bar.source_row_count : (index + 1) * bar.source_row_count
                ]
            ),
            source_sha256=source_rows_sha256(
                minute_rows[index * bar.source_row_count : (index + 1) * bar.source_row_count]
            ),
            parent_snapshot_sha256=parent_sha,
            row_sha256="",
        )
        for index, bar in enumerate(bars)
    )
    rows = [bar.to_dict() for bar in normalized_bars]
    frame = pl.DataFrame(rows)
    buffer = BytesIO()
    frame.write_parquet(buffer, compression="zstd", statistics=True)
    partition_bytes = buffer.getvalue()
    partition_sha = hashlib.sha256(partition_bytes).hexdigest()
    partition = PartitionRecord(
        path="part.parquet",
        sha256=partition_sha,
        row_count=len(normalized_bars),
        min_timestamp=_iso(normalized_bars[0].timestamp),
        max_timestamp=_iso(normalized_bars[-1].timestamp),
    )
    source_sha = source_rows_sha256(
        (source_id for bar in normalized_bars for source_id in bar.source_row_ids), identities=True
    )
    source_count = sum(bar.source_row_count for bar in normalized_bars)
    source_minimum = normalized_bars[0].timestamp
    source_maximum = normalized_bars[-1].bar_close - timedelta(minutes=1)
    parent_bindings = tuple((item.path, item.sha256) for item in parent_partitions)
    selection = AggregateSourceSelectionReceipt(
        parent_snapshot_sha256=parent_sha,
        symbol=normalized_bars[0].symbol,
        source_timeframe="1m",
        segment_id=normalized_bars[0].segment_id,
        source_row_count=source_count,
        source_bytes=1,
        source_sha256=source_sha,
        source_min_timestamp=_iso(source_minimum),
        source_max_timestamp=_iso(source_maximum),
        parent_partition_bindings=parent_bindings,
    )
    manifest = AggregatePublicationManifest(
        schema_version=1,
        parent_snapshot_identity=identity,
        parent_snapshot_sha256=parent_sha,
        symbol=normalized_bars[0].symbol,
        source_timeframe="1m",
        target_timeframe=normalized_bars[0].target_timeframe,
        segment_id=normalized_bars[0].segment_id,
        continuity=CONTINUITY_ID,
        config_version="aggregate-config-v1",
        work_budget_sha256=ValidationWorkBudget().sha256,
        work_demand_sha256="5" * 64,
        parent_source_selection_sha256=selection.sha256,
        parent_partition_bindings=parent_bindings,
        source_row_count=source_count,
        source_bytes=1,
        source_sha256=source_sha,
        source_min_timestamp=_iso(source_minimum),
        source_max_timestamp=_iso(source_maximum),
        aggregate_bar_count=len(normalized_bars),
        min_timestamp=_iso(normalized_bars[0].timestamp),
        max_timestamp=_iso(normalized_bars[-1].timestamp),
        artifact_scope="aggregate-parquet-partitions-v1",
        max_rows_per_partition=256,
        artifact_count_limit=1,
        artifact_byte_limit=16 * 1024 * 1024,
        declared_artifact_count=1,
        declared_artifact_bytes=16 * 1024 * 1024,
        actual_artifact_count=1,
        actual_artifact_bytes=len(partition_bytes),
        partitions=(partition,),
    )
    root = base / "aggregate"
    root.mkdir()
    (root / partition.path).write_bytes(partition_bytes)
    (root / AGGREGATE_MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
    (root / AGGREGATE_SUCCESS_NAME).write_text(f"{manifest.publication_sha256}\n", encoding="utf-8")
    return read_verified_aggregate_series(
        root,
        expected_publication_sha256=manifest.publication_sha256,
        parent_snapshot_directory=parent_root,
        expected_parent_snapshot_sha256=parent_sha,
        budget=ValidationWorkBudget(),
    )


def _effective_bars(slot: ValidationSlot, parameter: str) -> int:
    values = dict(slot.parameters)
    if (
        slot.kind is ValidationSlotKind.PERTURBATION
        and values.get("perturbed_parameter") == parameter
    ):
        return int(values["candidate_bars"])
    return target_bars(int(values[parameter]), slot.timeframe)


def _parent_a_slot(slot: ValidationSlot) -> ValidationSlot:
    if slot.family == "B":
        return _slot(
            "A", "moving_average_crossover", timeframe=slot.timeframe, direction=slot.direction
        )
    required = _effective_bars(slot, "donchian_hours")
    for candidate in VALIDATION_SLOT_ROSTER:
        if (
            candidate.family == "A"
            and candidate.timeframe == slot.timeframe
            and candidate.direction == slot.direction
            and dict(candidate.parameters).get("detector") == "donchian_breakout"
            and candidate.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
            and _effective_bars(candidate, "lookback_hours") == required
        ):
            return candidate
    raise AssertionError("matching parent A Donchian slot not found")


def _programme(
    series: VerifiedAggregateSeries,
    profile_config: bytes,
    *,
    budget: ValidationWorkBudget | None = None,
    source_price_precision_sha256: str = "8" * 64,
) -> ValidationProgrammeConfig:
    return ValidationProgrammeConfig(
        task14_closeout_commit=TASK14_CLOSEOUT,
        task14_evidence_commit=TASK14_EVIDENCE,
        task15_plan_commit=TASK15_PLAN,
        task15_evidence_commit=TASK15_EVIDENCE,
        implementation_plan_checkpoint=IMPLEMENTATION_CHECKPOINT,
        implementation_plan_evidence_commit=IMPLEMENTATION_EVIDENCE,
        implementation_plan_document_sha256=IMPLEMENTATION_DOCUMENT,
        code_commit="1" * 40,
        lockfile_sha256="2" * 64,
        dataset_sha256=series.parent_snapshot_manifest.snapshot_sha256,
        cost_policy_sha256="4" * 64,
        control_policy_sha256="5" * 64,
        split_sha256="6" * 64,
        profile_config_sha256=hashlib.sha256(profile_config).hexdigest(),
        source_price_precision_sha256=source_price_precision_sha256,
        families=EXPECTED_FAMILIES,
        roster=VALIDATION_SLOT_ROSTER,
        work_budget=budget or ValidationWorkBudget(),
    )


def _definition(
    slot: ValidationSlot,
    series: VerifiedAggregateSeries,
    **kwargs: object,
) -> CandidateDefinition:
    if slot.family in ("B", "E") and "parent_a_candidate" not in kwargs:
        kwargs["parent_a_candidate"] = _definition(_parent_a_slot(slot), series)
    return candidate_definition_for_slot(slot, series, **kwargs)  # type: ignore[arg-type]


def test_series_helper_rebuilds_independent_v1_identities_from_causal_inputs() -> None:
    source = _bar(0)
    caller_identity = "f" * 64
    causal = SimpleNamespace(
        timestamp=source.timestamp,
        bar_close=source.bar_close,
        symbol=source.symbol,
        target_timeframe=source.target_timeframe,
        segment_id=source.segment_id,
        source_row_count=source.source_row_count,
        open=source.open,
        high=source.high,
        low=source.low,
        close=source.close,
        volume=source.volume,
        source_row_ids=(caller_identity,) * source.source_row_count,
        source_sha256=caller_identity,
        parent_snapshot_sha256=caller_identity,
        row_sha256=caller_identity,
    )

    [rebuilt] = _series((causal,)).bars

    assert (rebuilt.timestamp, rebuilt.bar_close) == (source.timestamp, source.bar_close)
    assert (rebuilt.open, rebuilt.high, rebuilt.low, rebuilt.close, rebuilt.volume) == (
        source.open,
        source.high,
        source.low,
        source.close,
        source.volume,
    )
    expected_rows = _minute_rows(
        timestamp=source.timestamp,
        minutes=source.source_row_count,
        symbol=source.symbol,
        segment=source.segment_id,
        open_=source.open,
        high=source.high,
        low=source.low,
        close=source.close,
        volume=source.volume,
    )
    assert rebuilt.source_row_ids == tuple(
        canonical_source_row_identity(row) for row in expected_rows
    )
    assert caller_identity not in rebuilt.source_row_ids


def _detect(
    slot: ValidationSlot,
    bars: tuple[CanonicalAggregateBar, ...],
    **kwargs: object,
) -> tuple[CandidateSignal, ...]:
    series = _series(bars)
    return detect_candidate_signals(_definition(slot, series), series, **kwargs)


def test_contract_is_immutable_outcome_free_content_addressed_and_human_origin() -> None:
    bars = _bars([100.0] * 24 + [102.1])
    series = _series(bars)
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)
    [signal] = detect_candidate_signals(definition, series)

    assert definition.origin == "human_origin"
    assert definition.candidate_id.startswith("HC-")
    assert signal.signal_id.startswith("CS-")
    assert signal.family == "A"
    assert signal.segment_id == 7
    assert signal.information_cutoff == bars[-1].bar_close
    assert signal.legal_entry == bars[-1].bar_close
    assert signal.feature_start == bars[0].timestamp
    assert signal.source_publication_sha256 == series.publication_sha256
    assert signal.source_series_sha256 == series.series_sha256
    assert not (
        {"outcome", "return", "mfe", "mae", "label"} & {item.name for item in fields(signal)}
    )
    assert not hasattr(definition, "behaviour_id")
    with pytest.raises((TypeError, ValueError), match="issuance|token"):
        replace(signal, direction=-1)
    with pytest.raises(Exception):
        signal.direction = -1  # type: ignore[misc]


def test_candidate_signal_verifier_rejects_copy_lookalike_and_coherent_rehash() -> None:
    series = _series(_bars([100.0] * 24 + [102.1]))
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)
    [signal] = detect_candidate_signals(definition, series)
    verifier = getattr(research_candidates, "verify_candidate_signal", None)
    assert verifier is not None, "missing persistent detector signal verifier"
    assert verifier(signal) is signal

    copied = copy.copy(signal)
    coherent = copy.copy(signal)
    object.__setattr__(coherent, "direction", -signal.direction)
    object.__setattr__(
        coherent,
        "signal_id",
        f"CS-{hash_json('candidate-signal-v1', coherent.to_dict())}",
    )

    class Lookalike:
        def __getattr__(self, name: str):
            return getattr(signal, name)

    for forged in (copied, coherent, Lookalike()):
        with pytest.raises((TypeError, ValueError), match="signal.*registered|original|factory"):
            verifier(forged)


def test_subordinate_detection_rejects_copied_parent_opportunity() -> None:
    bars = _bars([100.0] * 24 + [102.0], volumes=[10.0] * 24 + [11.0])
    series = _series(bars)
    parent_definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)
    [opportunity] = detect_candidate_signals(parent_definition, series)
    subordinate = _definition(
        _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24),
        series,
        parent_a_candidate=parent_definition,
    )

    with pytest.raises((TypeError, ValueError), match="signal.*registered|original|factory"):
        detect_candidate_signals(
            subordinate,
            series,
            a_opportunities=(copy.copy(opportunity),),
        )


def test_arbitrary_time_signal_issuer_is_not_public() -> None:
    assert "emit_candidate_signal" not in research_models.__all__
    assert not hasattr(research_models, "emit_candidate_signal")


def test_flat_donchian_cannot_direct_mint_through_models_index_helper() -> None:
    series = _series(_bars([100.0] * 25))
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)

    assert detect_candidate_signals(definition, series) == ()
    assert not hasattr(research_models, "candidate_signal_from_indices")
    with pytest.raises(TypeError, match="detector-owned.*issuance token"):
        CandidateSignal(
            candidate_id=definition.candidate_id,
            family=definition.family,
            symbol=series.symbol,
            timeframe=definition.timeframe,
            direction=definition.direction,
            feature_start=series.bars[0].timestamp,
            information_cutoff=series.bars[-1].bar_close,
            legal_entry=series.bars[-1].bar_close,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            segment_id=series.segment_id,
            candidate_slot_id=definition.slot.slot_id,
            issuance_token=object(),
        )


def test_reviewer_fake_token_cannot_reverse_a_detector_direction() -> None:
    series = _series(_bars([100.0] * 24 + [102.0]))
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)

    class FakeToken:
        def consume(self, _observed: str) -> bool:
            return True

    FakeToken.__module__ = "market_structure_lab.research.candidates"
    FakeToken.__qualname__ = (
        "detect_candidate_signals.<locals>.detector_signal_issuer.<locals>.IssuanceToken"
    )
    with pytest.raises(TypeError, match="issuance"):
        CandidateSignal(
            candidate_id=definition.candidate_id,
            family=definition.family,
            symbol=series.symbol,
            timeframe=definition.timeframe,
            direction=-definition.direction,
            feature_start=series.bars[0].timestamp,
            information_cutoff=series.bars[-1].bar_close,
            legal_entry=series.bars[-1].bar_close,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            segment_id=series.segment_id,
            candidate_slot_id=definition.slot.slot_id,
            issuance_token=FakeToken(),
        )


@pytest.mark.parametrize(
    ("feature_start", "cutoff_offset", "entry_offset"),
    (
        (datetime(1900, 1, 1, tzinfo=UTC), timedelta(0), timedelta(0)),
        (START, timedelta(minutes=17), timedelta(minutes=17)),
        (START, timedelta(0), timedelta(days=365)),
    ),
)
def test_arbitrary_signal_clocks_cannot_bypass_detector_index_issuance(
    feature_start: datetime,
    cutoff_offset: timedelta,
    entry_offset: timedelta,
) -> None:
    series = _series(_bars([100.0] * 25))
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)
    cutoff = series.bars[-1].bar_close

    with pytest.raises(TypeError, match="issuance|token"):
        CandidateSignal(  # type: ignore[call-arg]
            candidate_id=definition.candidate_id,
            family=definition.family,
            symbol=series.symbol,
            timeframe=definition.timeframe,
            direction=definition.direction,
            feature_start=feature_start,
            information_cutoff=cutoff + cutoff_offset,
            legal_entry=cutoff + entry_offset,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            segment_id=series.segment_id,
            candidate_slot_id=definition.slot.slot_id,
        )


@pytest.mark.parametrize(
    "kind",
    (
        ValidationSlotKind.BASELINE,
        ValidationSlotKind.NEGATIVE_CONTROL,
        ValidationSlotKind.ROBUSTNESS,
        ValidationSlotKind.EXPOSURE,
        ValidationSlotKind.CAPACITY,
    ),
)
def test_non_detector_evaluation_slots_cannot_issue_candidate_definitions(
    kind: ValidationSlotKind,
) -> None:
    series = _series(_bars([100.0] * 2))
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.kind is kind)

    with pytest.raises(ValueError, match="CORE or PERTURBATION"):
        _definition(slot, series)


def test_candidate_identity_binds_publication_segment_role_parameters_and_full_a_grid() -> None:
    bars = _bars([100.0] * 25)
    series = _series(bars)
    slot = _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24)
    original = _definition(slot, series)

    assert original.a_selector_grid == A_SELECTOR_GRID
    changed_series = _series((*bars[:-1], replace(bars[-1], close=100.5, row_sha256="")))
    assert _definition(slot, changed_series).candidate_id != original.candidate_id
    segment_series = _series(_bars([100.0] * 25, segment=8))
    assert _definition(slot, segment_series).candidate_id != original.candidate_id
    changed_parent = _definition(_slot("A", "donchian_breakout", lookback=72), series)
    with pytest.raises(ValueError, match="parent Donchian lookback"):
        _definition(slot, series, parent_a_candidate=changed_parent)
    with pytest.raises(ValueError, match="A selector grid"):
        _definition(slot, series, a_selector_grid=A_SELECTOR_GRID[:-1])
    with pytest.raises(TypeError):
        candidate_definition_for_slot(slot, series, behaviour_id="DR-INVENTED")  # type: ignore[call-arg]


def test_available_adjacent_lookback_slots_have_unique_bound_candidate_identities() -> None:
    series_by_timeframe = {
        timeframe: _series(_bars([100.0] * 2, timeframe=timeframe)) for timeframe in ("1h", "4h")
    }
    perturbed = tuple(
        slot for slot in VALIDATION_SLOT_ROSTER if slot.kind is ValidationSlotKind.PERTURBATION
    )
    identities: set[str] = set()
    roles: Counter[tuple[str, str]] = Counter()
    unavailable_b = 0
    for slot in perturbed:
        series = series_by_timeframe[slot.timeframe]
        if slot.family == "B":
            with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
                _definition(slot, series)
            unavailable_b += 1
            continue
        definition = _definition(slot, series)
        identities.add(definition.candidate_id)
        roles[(slot.family, definition.detector_role)] += 1
        assert definition.evaluation_role == "adjacent_lookback"
        assert (
            detect_candidate_signals(
                definition,
                series,
            )
            == ()
        )

    assert len(perturbed) == 184
    assert unavailable_b == 16
    assert len(identities) == 168
    assert roles == Counter(
        {
            ("A", "moving_average_crossover"): 16,
            ("A", "donchian_breakout"): 16,
            ("A", "atr_breakout"): 8,
            ("A", "time_series_momentum"): 16,
            ("G", "candidate_primary"): 48,
            ("E", "volume_filtered_primary"): 32,
            ("D", "candidate_primary"): 32,
        }
    )


@pytest.mark.parametrize(
    ("timeframe", "hours", "expected"),
    (("1h", 8, 8), ("1h", 24, 24), ("1h", 72, 72), ("4h", 8, 2), ("4h", 24, 6), ("4h", 72, 18)),
)
def test_hour_windows_convert_to_exact_integral_target_bars(
    timeframe: str, hours: int, expected: int
) -> None:
    assert target_bars(hours, timeframe) == expected


def test_a_donchian_uses_prior_bars_strict_ties_rearm_and_frozen_horizon_overlap() -> None:
    closes = [100.0] * 24 + [102.0, 103.0, 100.0, 103.0] + [100.0] * 22 + [105.0]
    signals = _detect(_slot("A", "donchian_breakout", lookback=24), _bars(closes))

    assert [signal.information_cutoff for signal in signals] == [
        START + timedelta(hours=25),
        START + timedelta(hours=51),
    ]
    assert all(signal.direction == 1 for signal in signals)
    assert _detect(_slot("A", "donchian_breakout", lookback=24), _bars([100.0] * 25)) == ()


def test_a_4h_donchian_converts_24_hours_to_six_completed_reference_bars() -> None:
    bars = _bars([100.0] * 6 + [102.0], timeframe="4h")
    [signal] = _detect(_slot("A", "donchian_breakout", timeframe="4h", lookback=24), bars)

    assert signal.feature_start == START
    assert signal.information_cutoff == START + timedelta(hours=28)
    assert signal.legal_entry == signal.information_cutoff


@pytest.mark.parametrize(("direction", "expansion"), (("long", 103.0), ("short", 97.0)))
def test_a_atr24_uses_prior_exact_true_ranges_and_is_long_short_symmetric(
    direction: str, expansion: float
) -> None:
    bars = list(_bars([100.0] * 25))
    bars.append(
        _bar(
            25, open_=100.0, high=max(100.0, expansion), low=min(100.0, expansion), close=expansion
        )
    )
    signals = _detect(_slot("A", "atr_breakout", direction=direction), tuple(bars))

    assert len(signals) == 1
    assert signals[0].direction == (1 if direction == "long" else -1)
    assert signals[0].feature_start == bars[0].timestamp
    tied = replace(bars[-1], close=102.0 if direction == "long" else 98.0, row_sha256="")
    assert _detect(_slot("A", "atr_breakout", direction=direction), (*bars[:-1], tied)) == ()


def test_a_sma_and_tsmom_emit_only_nonzero_sign_transitions_after_warmup() -> None:
    sma_closes = [100.0] * 72 + [90.0] + [110.0] * 25
    sma = _detect(_slot("A", "moving_average_crossover"), _bars(sma_closes))
    assert len(sma) == 1
    assert sma[0].direction == 1

    momentum_closes = [100.0] * 25 + [99.0] + [101.0]
    momentum = _detect(_slot("A", "time_series_momentum", lookback=24), _bars(momentum_closes))
    assert len(momentum) == 1
    assert momentum[0].direction == 1


def test_a_transition_feature_start_includes_the_prior_state_input() -> None:
    sma_shared = [100.0] * 48 + [110.0] * 24 + [100.0]
    sma_without_transition = _bars([100.0, *sma_shared])
    sma_with_transition = _bars([10_000.0, *sma_shared])
    sma_slot = _slot("A", "moving_average_crossover")

    assert _detect(sma_slot, sma_without_transition) == ()
    [sma_signal] = _detect(sma_slot, sma_with_transition)
    assert sma_signal.feature_start == sma_with_transition[0].timestamp

    momentum_shared = [100.0] * 24 + [110.0, 100.0]
    momentum_without_transition = _bars([90.0, *momentum_shared])
    momentum_with_transition = _bars([110.0, *momentum_shared])
    momentum_slot = _slot("A", "time_series_momentum", lookback=24)

    assert _detect(momentum_slot, momentum_without_transition) == ()
    [momentum_signal] = _detect(momentum_slot, momentum_with_transition)
    assert momentum_signal.feature_start == momentum_with_transition[0].timestamp


def test_warmup_gap_and_series_changes_do_not_leak_state_across_boundaries() -> None:
    slot = _slot("A", "donchian_breakout", lookback=24)
    assert _detect(slot, _bars([100.0] * 23 + [102.0])) == ()

    bars = list(_bars([100.0] * 24 + [102.0]))
    bars[-1] = replace(
        bars[-1],
        timestamp=bars[-1].timestamp + timedelta(hours=1),
        bar_close=bars[-1].bar_close + timedelta(hours=1),
        row_sha256="",
    )
    with pytest.raises(ValueError, match="contiguous|range"):
        _series(tuple(bars))


@pytest.mark.parametrize(
    ("direction", "previous", "current", "prior_close", "close"),
    (
        (
            1,
            ProfileValueReferences(100, 98, 102, 100.0),
            ProfileValueReferences(102, 100, 104, 102.0),
            101,
            103,
        ),
        (
            -1,
            ProfileValueReferences(102, 100, 104, 102.0),
            ProfileValueReferences(100, 98, 102, 100.0),
            101,
            99,
        ),
    ),
)
def test_b_pure_formula_accepts_symmetric_migration_without_issuing_signals(
    direction: int,
    previous: ProfileValueReferences,
    current: ProfileValueReferences,
    prior_close: int,
    close: int,
) -> None:
    assert value_migration_acceptance(
        previous,
        current,
        prior_close_bin=prior_close,
        current_close_bin=close,
        direction=direction,
    )


def test_b_pure_formula_rejects_missing_flat_and_outside_value_references() -> None:
    previous = ProfileValueReferences(100, 98, 102, 100.0)
    current = ProfileValueReferences(102, 100, 104, 102.0)

    assert not value_migration_acceptance(
        ProfileValueReferences(None, None, None, None),
        current,
        prior_close_bin=101,
        current_close_bin=103,
        direction=1,
    )
    assert not value_migration_acceptance(
        previous,
        ProfileValueReferences(100, 98, 102, 100.0),
        prior_close_bin=100,
        current_close_bin=100,
        direction=1,
    )
    assert not value_migration_acceptance(
        previous,
        current,
        prior_close_bin=99,
        current_close_bin=103,
        direction=1,
    )


def test_family_b_fails_closed_without_independent_snapshot_precision_authority() -> None:
    series = _series(_bars([100.0] * 25))
    slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    profile_config = profile_config_artifact_bytes()
    programme = _programme(series, profile_config)

    assert not hasattr(research_candidates, "price_precision_artifact_bytes")
    with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
        read_source_price_precision_manifest(series)
    with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
        build_verified_profile_stream(
            series,
            programme,
            window_hours=24,
            profile_config_bytes=profile_config,
        )
    with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
        _definition(slot, series)


def test_new_validation_programme_cannot_self_authorize_source_precision() -> None:
    series = _series(_bars([100.0] * 25))
    profile_config = profile_config_artifact_bytes()

    for caller_chosen_hash in ("8" * 64, "9" * 64):
        programme = _programme(
            series,
            profile_config,
            source_price_precision_sha256=caller_chosen_hash,
        )
        with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
            build_verified_profile_stream(
                series,
                programme,
                window_hours=24,
                profile_config_bytes=profile_config,
            )


def test_e_filters_a_donchian_opportunities_with_strict_prior_volume_median() -> None:
    volumes = [10.0] * 24 + [11.0]
    bars = _bars([100.0] * 24 + [102.0], volumes=volumes)
    series = _series(bars)
    a_slot = _slot("A", "donchian_breakout", lookback=24)
    a_definition = _definition(a_slot, series)
    [opportunity] = detect_candidate_signals(a_definition, series)
    filtered = _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24)
    e_definition = _definition(filtered, series, parent_a_candidate=a_definition)

    assert len(detect_candidate_signals(e_definition, series, a_opportunities=(opportunity,))) == 1
    tied = (*bars[:-1], replace(bars[-1], volume=10.0, row_sha256=""))
    tied_series = _series(tied)
    tied_a_definition = _definition(a_slot, tied_series)
    [tied_opportunity] = detect_candidate_signals(tied_a_definition, tied_series)
    tied_e_definition = _definition(filtered, tied_series, parent_a_candidate=tied_a_definition)
    assert (
        detect_candidate_signals(
            tied_e_definition,
            tied_series,
            a_opportunities=(tied_opportunity,),
        )
        == ()
    )


def test_subordinate_candidates_reject_flat_or_wrong_registered_a_opportunities() -> None:
    bars = _bars([100.0] * 25 + [103.0])
    series = _series(bars)
    donchian_slot = _slot("A", "donchian_breakout", lookback=24)
    atr_slot = _slot("A", "atr_breakout")
    donchian_definition = _definition(donchian_slot, series)
    atr_definition = _definition(atr_slot, series)
    [atr_signal] = detect_candidate_signals(atr_definition, series)
    with pytest.raises(TypeError, match="issuance|token"):
        CandidateSignal(  # type: ignore[call-arg]
            candidate_id=donchian_definition.candidate_id,
            family="A",
            symbol=series.symbol,
            timeframe="1h",
            direction=1,
            feature_start=bars[0].timestamp,
            information_cutoff=bars[-1].bar_close,
            legal_entry=bars[-1].bar_close,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            segment_id=series.segment_id,
            candidate_slot_id=donchian_definition.slot.slot_id,
        )

    e_slot = _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24)
    e_definition = _definition(e_slot, series, parent_a_candidate=donchian_definition)
    with pytest.raises(ValueError, match="exact registered parent"):
        detect_candidate_signals(e_definition, series, a_opportunities=(atr_signal,))


def test_e_definition_cannot_bypass_exact_donchian_parent_factory_gate() -> None:
    series = _series(_bars([100.0] * 26))
    e_slot = _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24)
    atr_definition = _definition(_slot("A", "atr_breakout"), series)
    donchian_definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)
    issued = candidate_definition_for_slot(
        e_slot,
        series,
        parent_a_candidate=donchian_definition,
    )

    assert issued == candidate_definition_for_slot(
        e_slot,
        series,
        parent_a_candidate=donchian_definition,
    )
    assert issued.parent_a_candidate_id == donchian_definition.candidate_id
    assert issued.parent_a_slot_id == donchian_definition.slot.slot_id

    with pytest.raises(ValueError, match="exact A Donchian"):
        candidate_definition_for_slot(
            e_slot,
            series,
            parent_a_candidate=atr_definition,
        )
    with pytest.raises(TypeError, match="factory"):
        CandidateDefinition(
            slot=e_slot,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            source_segment_id=series.segment_id,
            aggregate_config_version=series.manifest.config_version,
            a_selector_grid=A_SELECTOR_GRID,
            parent_a_candidate_id=atr_definition.candidate_id,
            parent_a_slot_id=atr_definition.slot.slot_id,
        )
    with pytest.raises(TypeError, match="factory"):
        replace(
            issued,
            parent_a_candidate_id=atr_definition.candidate_id,
            parent_a_slot_id=atr_definition.slot.slot_id,
        )


def test_b_definition_cannot_bypass_missing_precision_authority_with_any_parent() -> None:
    series = _series(_bars([100.0] * 26))
    b_slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    wrong_parent_slot = next(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == "A"
        and slot.kind is ValidationSlotKind.PERTURBATION
        and slot.timeframe == b_slot.timeframe
        and slot.direction == b_slot.direction
    )
    wrong_parent = _definition(wrong_parent_slot, series)
    allowed_parent = _definition(_parent_a_slot(b_slot), series)
    for parent in (wrong_parent, allowed_parent):
        with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
            candidate_definition_for_slot(
                b_slot,
                series,
                parent_a_candidate=parent,
            )
    with pytest.raises(TypeError, match="factory"):
        CandidateDefinition(
            slot=b_slot,
            source_publication_sha256=series.publication_sha256,
            source_series_sha256=series.series_sha256,
            source_segment_id=series.segment_id,
            aggregate_config_version=series.manifest.config_version,
            a_selector_grid=A_SELECTOR_GRID,
            profile_bin_step=1.0,
            profile_definition_id=(
                "rolling-1m:uniform-touched-v1:value-area=0.70:"
                "window-hours=24:fixed-step=1.0:"
                f"source-config={series.manifest.config_version}"
            ),
            profile_stream_sha256="8" * 64,
            parent_a_candidate_id=wrong_parent.candidate_id,
            parent_a_slot_id=wrong_parent.slot.slot_id,
        )
    with pytest.raises(SourcePricePrecisionUnavailable, match="independently tracked"):
        candidate_definition_for_slot(
            b_slot,
            series,
            parent_a_candidate=wrong_parent,
            a_selector_grid=A_SELECTOR_GRID,
        )


def test_g_exact_candidate_and_controls_use_same_next_bar_clock() -> None:
    # Constant TR=2 makes ATR8/ATR24 equal; lower the final three pre-expansion ranges to compress.
    bars = list(_bars([100.0] * 28))
    for index in (24, 25, 26):
        bars[index] = replace(bars[index], high=100.25, low=99.75, row_sha256="")
    bars[27] = replace(bars[27], high=104.0, close=103.0, row_sha256="")
    bars_tuple = tuple(bars)
    candidate = _slot("G", "compression_expansion", role="candidate_primary", horizon=8)
    atr = _slot("G", "compression_expansion", role="atr_only_control", horizon=8)
    donchian = _slot("G", "compression_expansion", role="donchian_only_control", horizon=8)

    candidate_signals = _detect(candidate, bars_tuple)
    assert len(candidate_signals) == 1
    atr_signal = _detect(atr, bars_tuple)[0]
    donchian_signal = _detect(donchian, bars_tuple)[0]
    assert candidate_signals[0].feature_start == bars[0].timestamp
    assert atr_signal.feature_start == bars[2].timestamp
    assert donchian_signal.feature_start == bars[3].timestamp
    assert atr_signal.legal_entry == candidate_signals[0].legal_entry
    assert donchian_signal.legal_entry == candidate_signals[0].legal_entry


def test_g_controls_enumerate_every_legal_event_before_frozen_horizon_suppression() -> None:
    closes = [100.0] * 24 + [102.0 + 2.0 * index for index in range(10)]
    control = _slot("G", "compression_expansion", role="donchian_only_control", horizon=8)

    signals = _detect(control, _bars(closes))

    assert [signal.information_cutoff for signal in signals] == [
        START + timedelta(hours=25),
        START + timedelta(hours=33),
    ]


def test_d_requires_separate_confirmation_and_uses_t_plus_2_for_candidate_and_control() -> None:
    bars = list(_bars([100.0] * 24))
    bars.append(_bar(24, high=102.0, low=99.0, close=100.0))
    bars.append(_bar(25, high=101.0, low=98.0, close=99.0))
    bars_tuple = tuple(bars)
    candidate = _slot(
        "D",
        "failed_break_reclaim",
        role="candidate_primary",
        direction="short",
        horizon=8,
        lookback=24,
    )
    control = _slot(
        "D",
        "failed_break_reclaim",
        role="failed_donchian_control",
        direction="short",
        horizon=8,
        lookback=24,
    )
    [signal] = _detect(candidate, bars_tuple)
    [control_signal] = _detect(control, bars_tuple)

    assert signal.information_cutoff == bars[-1].bar_close
    assert signal.legal_entry == START + timedelta(hours=26)
    assert signal.feature_start == bars[0].timestamp
    assert control_signal.legal_entry == signal.legal_entry
    no_confirmation = (*bars_tuple[:-1], replace(bars_tuple[-1], close=101.0, row_sha256=""))
    assert _detect(candidate, no_confirmation) == ()
    assert len(_detect(control, no_confirmation)) == 1


def test_d_pseudo_level_is_frozen_at_exact_prior_utc_week_and_never_crosses_missing_history() -> (
    None
):
    bars = list(_bars([100.0] * (168 + 24)))
    # At current t=192, the pseudo reference is the Donchian boundary frozen at t-168=24.
    bars.append(_bar(192, high=102.0, low=99.0, close=100.0))
    bars.append(_bar(193, high=101.0, low=98.0, close=99.0))
    slot = _slot(
        "D",
        "failed_break_reclaim",
        role="pseudo_level_control",
        direction="short",
        horizon=8,
        lookback=24,
    )

    assert len(_detect(slot, tuple(bars))) == 1
    assert _detect(slot, tuple(bars[-26:])) == ()


def test_wrong_series_or_arbitrary_rows_are_rejected_before_signal_emission() -> None:
    bars = _bars([100.0] * 24 + [102.0])
    series = _series(bars)
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), series)

    with pytest.raises(TypeError, match="VerifiedAggregateSeries"):
        detect_candidate_signals(definition, bars)  # type: ignore[arg-type]
    changed = (*bars[:-1], replace(bars[-1], high=104.0, close=103.0, row_sha256=""))
    changed_series = _series(changed)
    with pytest.raises(ValueError, match="does not own"):
        detect_candidate_signals(definition, changed_series)
    with pytest.raises((TypeError, ValueError), match="seal"):
        replace(series, bars=changed)
