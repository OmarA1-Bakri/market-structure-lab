from __future__ import annotations

from collections import Counter
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import (
    CONTINUITY_ID,
    CanonicalAggregateBar,
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
from market_structure_lab.data.export import PartitionRecord, SnapshotIdentity
from market_structure_lab.profiles import (
    Candle,
    FixedStepBins,
    UniformAllocation,
    calculate_profile,
)
from market_structure_lab.research.candidates import (
    A_SELECTOR_GRID,
    FrozenProfile,
    VerifiedProfileStream,
    candidate_definition_for_slot,
    detect_candidate_signals,
    target_bars,
    verify_profile_stream,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    CandidateDefinition,
    CandidateSignal,
    ValidationSlot,
    ValidationSlotKind,
)

PARENT = "b" * 64
PROFILE_CONFIG = "c" * 64
BIN_METADATA = "d" * 64
START = datetime(2025, 1, 1, tzinfo=UTC)


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
        hash_json("test-candidate-source-row", {"bar": index, "minute": minute})
        for minute in range(minutes)
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


def _series(bars: tuple[CanonicalAggregateBar, ...]) -> VerifiedAggregateSeries:
    if not bars:
        raise ValueError("test publication requires bars")
    rows = [bar.to_dict() for bar in bars]
    frame = pl.DataFrame(rows)
    buffer = BytesIO()
    frame.write_parquet(buffer, compression="zstd", statistics=True)
    partition_bytes = buffer.getvalue()
    partition_sha = hashlib.sha256(partition_bytes).hexdigest()
    partition = PartitionRecord(
        path="part.parquet",
        sha256=partition_sha,
        row_count=len(bars),
        min_timestamp=_iso(bars[0].timestamp),
        max_timestamp=_iso(bars[-1].timestamp),
    )
    source_sha = source_rows_sha256(
        (source_id for bar in bars for source_id in bar.source_row_ids), identities=True
    )
    source_count = sum(bar.source_row_count for bar in bars)
    source_minimum = bars[0].timestamp
    source_maximum = bars[-1].bar_close - timedelta(minutes=1)
    parent_bindings = (("source-partition.parquet", "e" * 64),)
    selection = AggregateSourceSelectionReceipt(
        parent_snapshot_sha256=PARENT,
        symbol=bars[0].symbol,
        source_timeframe="1m",
        segment_id=bars[0].segment_id,
        source_row_count=source_count,
        source_bytes=1,
        source_sha256=source_sha,
        source_min_timestamp=_iso(source_minimum),
        source_max_timestamp=_iso(source_maximum),
        parent_partition_bindings=parent_bindings,
    )
    identity = SnapshotIdentity(
        dataset_version="DS-CANDIDATE-TEST",
        dump_sha256="1" * 64,
        recovery_sha256="2" * 64,
        mapping_version="canonical-test-v1",
        config_version="canonical-test-v1",
        code_commit="3" * 40,
    )
    manifest = AggregatePublicationManifest(
        schema_version=1,
        parent_snapshot_identity=identity,
        parent_snapshot_sha256=PARENT,
        symbol=bars[0].symbol,
        source_timeframe="1m",
        target_timeframe=bars[0].target_timeframe,
        segment_id=bars[0].segment_id,
        continuity=CONTINUITY_ID,
        config_version="aggregate-config-v1",
        work_budget_sha256="4" * 64,
        work_demand_sha256="5" * 64,
        parent_source_selection_sha256=selection.sha256,
        parent_partition_bindings=parent_bindings,
        source_row_count=source_count,
        source_bytes=1,
        source_sha256=source_sha,
        source_min_timestamp=_iso(source_minimum),
        source_max_timestamp=_iso(source_maximum),
        aggregate_bar_count=len(bars),
        min_timestamp=_iso(bars[0].timestamp),
        max_timestamp=_iso(bars[-1].timestamp),
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
    with TemporaryDirectory(prefix="candidate-series-test-") as temporary:
        root = Path(temporary)
        (root / partition.path).write_bytes(partition_bytes)
        (root / AGGREGATE_MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
        (root / AGGREGATE_SUCCESS_NAME).write_text(
            f"{manifest.publication_sha256}\n", encoding="utf-8"
        )
        return read_verified_aggregate_series(root, manifest)


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


def _empty_profile_stream(series: VerifiedAggregateSeries) -> VerifiedProfileStream:
    return verify_profile_stream(
        series,
        (),
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        bin_step=1.0,
    )


def _definition(
    slot: ValidationSlot,
    series: VerifiedAggregateSeries,
    **kwargs: object,
) -> CandidateDefinition:
    if slot.family in ("B", "E") and "parent_a_candidate" not in kwargs:
        kwargs["parent_a_candidate"] = _definition(_parent_a_slot(slot), series)
    if slot.family == "B" and "profile_stream" not in kwargs:
        kwargs["profile_stream"] = _empty_profile_stream(series)
    return candidate_definition_for_slot(slot, series, **kwargs)  # type: ignore[arg-type]


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
    with pytest.raises((TypeError, ValueError), match="seal"):
        replace(signal, direction=-1)
    with pytest.raises(Exception):
        signal.direction = -1  # type: ignore[misc]


def test_candidate_identity_binds_publication_segment_role_parameters_and_full_a_grid() -> None:
    bars = _bars([100.0] * 25)
    series = _series(bars)
    slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    original = _definition(slot, series)

    assert original.a_selector_grid == A_SELECTOR_GRID
    changed_series = _series((*bars[:-1], replace(bars[-1], close=100.5, row_sha256="")))
    assert _definition(slot, changed_series).candidate_id != original.candidate_id
    segment_series = _series(_bars([100.0] * 25, segment=8))
    assert _definition(slot, segment_series).candidate_id != original.candidate_id
    structure_slot = _slot("B", "value_migration_acceptance", role="structure_only", lookback=24)
    assert _definition(structure_slot, series).candidate_id != (original.candidate_id)
    half_step = verify_profile_stream(
        series,
        (),
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        bin_step=0.5,
    )
    assert _definition(slot, series, profile_stream=half_step).candidate_id != original.candidate_id
    with pytest.raises(ValueError, match="A selector grid"):
        _definition(slot, series, a_selector_grid=A_SELECTOR_GRID[:-1])
    with pytest.raises(TypeError):
        candidate_definition_for_slot(slot, series, behaviour_id="DR-INVENTED")  # type: ignore[call-arg]


def test_all_184_adjacent_lookback_slots_have_unique_bound_candidate_identities() -> None:
    series_by_timeframe = {
        timeframe: _series(_bars([100.0] * 2, timeframe=timeframe)) for timeframe in ("1h", "4h")
    }
    perturbed = tuple(
        slot for slot in VALIDATION_SLOT_ROSTER if slot.kind is ValidationSlotKind.PERTURBATION
    )
    identities: set[str] = set()
    roles: Counter[tuple[str, str]] = Counter()
    for slot in perturbed:
        series = series_by_timeframe[slot.timeframe]
        profile_stream = _empty_profile_stream(series) if slot.family == "B" else None
        definition = _definition(slot, series, profile_stream=profile_stream)
        identities.add(definition.candidate_id)
        roles[(slot.family, definition.detector_role)] += 1
        assert definition.evaluation_role == "adjacent_lookback"
        assert (
            detect_candidate_signals(
                definition,
                series,
                profile_stream=profile_stream,
            )
            == ()
        )

    assert len(perturbed) == 184
    assert len(identities) == 184
    assert roles == Counter(
        {
            ("A", "moving_average_crossover"): 16,
            ("A", "donchian_breakout"): 16,
            ("A", "atr_breakout"): 8,
            ("A", "time_series_momentum"): 16,
            ("B", "combined_primary"): 16,
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


def _profile(cutoff: datetime, prices: list[float], *, window: int = 24) -> FrozenProfile:
    binning = FixedStepBins(step=1.0, provenance=f"verified-price-precision:{BIN_METADATA}")
    allocation = UniformAllocation()
    snapshot = calculate_profile(
        [
            allocation.allocate(
                Candle(open=price, high=price, low=price, close=price, volume=1.0), binning
            )
            for price in prices
        ],
        binning=binning,
        allocation_id=allocation.model_id,
        value_area_fraction=0.70,
    )
    return FrozenProfile(
        information_cutoff=cutoff,
        window_hours=window,
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        segment_id=7,
        policy="rolling",
        snapshot=snapshot,
    )


def test_b_uses_t_minus_2_t_minus_1_profiles_and_same_a_cutoff_and_entry() -> None:
    bars = _bars([100.0] * 24 + [102.0])
    series = _series(bars)
    a_slot = _slot("A", "donchian_breakout", lookback=24)
    a_definition = _definition(a_slot, series)
    [a_signal] = detect_candidate_signals(a_definition, series)
    profiles = (
        _profile(bars[-3].bar_close, [98.0, 99.0, 100.0]),
        _profile(bars[-2].bar_close, [100.0, 101.0, 102.0]),
    )
    profile_stream = verify_profile_stream(
        series,
        profiles,
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        bin_step=1.0,
    )
    b_slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    b_definition = _definition(
        b_slot,
        series,
        parent_a_candidate=a_definition,
        profile_stream=profile_stream,
    )
    [b_signal] = detect_candidate_signals(
        b_definition,
        series,
        a_opportunities=(a_signal,),
        profile_stream=profile_stream,
    )

    assert b_signal.information_cutoff == a_signal.information_cutoff
    assert b_signal.legal_entry == a_signal.legal_entry
    assert b_signal.direction == a_signal.direction
    assert b_signal.feature_start == profiles[0].feature_start
    partial_stream = verify_profile_stream(
        series,
        profiles[:1],
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        bin_step=1.0,
    )
    partial_definition = _definition(
        b_slot,
        series,
        parent_a_candidate=a_definition,
        profile_stream=partial_stream,
    )
    assert (
        detect_candidate_signals(
            partial_definition,
            series,
            a_opportunities=(a_signal,),
            profile_stream=partial_stream,
        )
        == ()
    )


def test_profile_stream_rejects_duplicate_cutoffs_arbitrary_provenance_and_tamper() -> None:
    series = _series(_bars([100.0] * 25))
    profile = _profile(series.bars[-2].bar_close, [100.0, 101.0, 102.0])
    with pytest.raises(ValueError, match="unique"):
        verify_profile_stream(
            series,
            (profile, profile),
            source_minute_publication_sha256=PARENT,
            profile_config_sha256=PROFILE_CONFIG,
            bin_metadata_sha256=BIN_METADATA,
            bin_step=1.0,
        )
    with pytest.raises(ValueError, match="precision metadata"):
        replace(
            profile,
            snapshot=replace(
                profile.snapshot,
                binning=FixedStepBins(step=1.0, provenance="source=arbitrary"),
            ),
        )
    stream = verify_profile_stream(
        series,
        (profile,),
        source_minute_publication_sha256=PARENT,
        profile_config_sha256=PROFILE_CONFIG,
        bin_metadata_sha256=BIN_METADATA,
        bin_step=1.0,
    )
    with pytest.raises((TypeError, ValueError), match="seal"):
        replace(stream, ordered_profile_ids=("f" * 64,))


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
    with pytest.raises(TypeError, match="seal"):
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

    profile_stream = _empty_profile_stream(series)
    b_slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    b_definition = _definition(
        b_slot,
        series,
        parent_a_candidate=donchian_definition,
        profile_stream=profile_stream,
    )
    with pytest.raises(ValueError, match="exact registered parent"):
        detect_candidate_signals(
            b_definition,
            series,
            a_opportunities=(atr_signal,),
            profile_stream=profile_stream,
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
    assert _detect(atr, bars_tuple)[0].legal_entry == candidate_signals[0].legal_entry
    assert _detect(donchian, bars_tuple)[0].legal_entry == candidate_signals[0].legal_entry


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
