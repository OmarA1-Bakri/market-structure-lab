from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import (
    CONTINUITY_ID,
    CanonicalAggregateBar,
    source_rows_sha256,
)
from market_structure_lab.profiles import (
    Candle,
    FixedStepBins,
    UniformAllocation,
    calculate_profile,
)
from market_structure_lab.research.candidates import (
    A_SELECTOR_GRID,
    FrozenProfile,
    candidate_definition_for_slot,
    detect_candidate_signals,
    target_bars,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    CandidateDefinition,
    CandidateSignal,
    ValidationSlot,
    ValidationSlotKind,
)

PUBLICATION = "a" * 64
PARENT = "b" * 64
START = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Receipt:
    publication_sha256: str
    symbol: str
    target_timeframe: str
    segment_id: int
    aggregate_bar_count: int
    min_timestamp: str
    max_timestamp: str
    source_sha256: str
    parent_snapshot_sha256: str


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
) -> tuple[CanonicalAggregateBar, ...]:
    return tuple(
        _bar(
            index,
            timeframe=timeframe,
            open_=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=volumes[index] if volumes else 10.0,
        )
        for index, close in enumerate(closes)
    )


def _receipt(bars: tuple[CanonicalAggregateBar, ...], sha: str = PUBLICATION) -> Receipt:
    return Receipt(
        publication_sha256=sha,
        symbol=bars[0].symbol,
        target_timeframe=bars[0].target_timeframe,
        segment_id=bars[0].segment_id,
        aggregate_bar_count=len(bars),
        min_timestamp=bars[0].timestamp.isoformat().replace("+00:00", "Z"),
        max_timestamp=bars[-1].timestamp.isoformat().replace("+00:00", "Z"),
        source_sha256=source_rows_sha256(
            (source_id for bar in bars for source_id in bar.source_row_ids), identities=True
        ),
        parent_snapshot_sha256=PARENT,
    )


def _definition(slot: ValidationSlot, receipt: Receipt, **kwargs: object) -> CandidateDefinition:
    if slot.family == "B" and "profile_bin_step" not in kwargs:
        kwargs["profile_bin_step"] = 1.0
    return candidate_definition_for_slot(slot, receipt, **kwargs)  # type: ignore[arg-type]


def _detect(
    slot: ValidationSlot,
    bars: tuple[CanonicalAggregateBar, ...],
    **kwargs: object,
) -> tuple[CandidateSignal, ...]:
    receipt = _receipt(bars)
    return detect_candidate_signals(_definition(slot, receipt), bars, receipt, **kwargs)


def test_contract_is_immutable_outcome_free_content_addressed_and_human_origin() -> None:
    bars = _bars([100.0] * 24 + [102.1])
    receipt = _receipt(bars)
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), receipt)
    [signal] = detect_candidate_signals(definition, bars, receipt)

    assert definition.origin == "human_origin"
    assert definition.candidate_id.startswith("HC-")
    assert signal.signal_id.startswith("CS-")
    assert signal.family == "A"
    assert signal.segment_id == 7
    assert signal.information_cutoff == bars[-1].bar_close
    assert signal.legal_entry == bars[-1].bar_close
    assert signal.feature_start == bars[0].timestamp
    assert signal.source_publication_sha256 == PUBLICATION
    assert not (
        {"outcome", "return", "mfe", "mae", "label"} & {item.name for item in fields(signal)}
    )
    assert not hasattr(definition, "behaviour_id")
    assert replace(signal, direction=-1).signal_id != signal.signal_id
    with pytest.raises(Exception):
        signal.direction = -1  # type: ignore[misc]


def test_candidate_identity_binds_publication_segment_role_parameters_and_full_a_grid() -> None:
    bars = _bars([100.0] * 25)
    receipt = _receipt(bars)
    slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    original = _definition(slot, receipt)

    assert original.a_selector_grid == A_SELECTOR_GRID
    assert _definition(slot, replace(receipt, publication_sha256="c" * 64)).candidate_id != (
        original.candidate_id
    )
    assert _definition(slot, replace(receipt, segment_id=8)).candidate_id != original.candidate_id
    structure_slot = _slot("B", "value_migration_acceptance", role="structure_only", lookback=24)
    assert _definition(structure_slot, receipt).candidate_id != (original.candidate_id)
    assert _definition(slot, receipt, profile_bin_step=0.5).candidate_id != original.candidate_id
    with pytest.raises(ValueError, match="A selector grid"):
        _definition(slot, receipt, a_selector_grid=A_SELECTOR_GRID[:-1])
    with pytest.raises(TypeError):
        candidate_definition_for_slot(slot, receipt, behaviour_id="DR-INVENTED")  # type: ignore[call-arg]


def test_all_184_adjacent_lookback_slots_have_unique_bound_candidate_identities() -> None:
    bars = _bars([100.0] * 2)
    receipt = _receipt(bars)
    perturbed = tuple(
        slot for slot in VALIDATION_SLOT_ROSTER if slot.kind is ValidationSlotKind.PERTURBATION
    )
    identities = {
        _definition(
            slot, receipt, profile_bin_step=1.0 if slot.family == "B" else None
        ).candidate_id
        for slot in perturbed
    }

    assert len(perturbed) == 184
    assert len(identities) == 184


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
    receipt = _receipt(tuple(bars))
    assert detect_candidate_signals(_definition(slot, receipt), tuple(bars), receipt) == ()


def _profile(cutoff: datetime, prices: list[float], *, window: int = 24) -> FrozenProfile:
    binning = FixedStepBins(step=1.0, provenance="verified-price-precision-v1")
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
        source_publication_sha256=PUBLICATION,
        segment_id=7,
        policy="rolling",
        snapshot=snapshot,
    )


def test_b_uses_t_minus_2_t_minus_1_profiles_and_same_a_cutoff_and_entry() -> None:
    bars = _bars([100.0] * 24 + [102.0])
    receipt = _receipt(bars)
    a_slot = _slot("A", "donchian_breakout", lookback=24)
    [a_signal] = detect_candidate_signals(_definition(a_slot, receipt), bars, receipt)
    profiles = (
        _profile(bars[-3].bar_close, [98.0, 99.0, 100.0]),
        _profile(bars[-2].bar_close, [100.0, 101.0, 102.0]),
    )
    b_slot = _slot("B", "value_migration_acceptance", role="combined_primary", lookback=24)
    [b_signal] = detect_candidate_signals(
        _definition(b_slot, receipt), bars, receipt, a_opportunities=(a_signal,), profiles=profiles
    )

    assert b_signal.information_cutoff == a_signal.information_cutoff
    assert b_signal.legal_entry == a_signal.legal_entry
    assert b_signal.direction == a_signal.direction
    assert (
        detect_candidate_signals(
            _definition(b_slot, receipt),
            bars,
            receipt,
            a_opportunities=(a_signal,),
            profiles=profiles[:1],
        )
        == ()
    )


def test_e_filters_a_donchian_opportunities_with_strict_prior_volume_median() -> None:
    volumes = [10.0] * 24 + [11.0]
    bars = _bars([100.0] * 24 + [102.0], volumes=volumes)
    receipt = _receipt(bars)
    a_slot = _slot("A", "donchian_breakout", lookback=24)
    [opportunity] = detect_candidate_signals(_definition(a_slot, receipt), bars, receipt)
    filtered = _slot("E", "volume_confirmation", role="volume_filtered_primary", lookback=24)

    assert (
        len(
            detect_candidate_signals(
                _definition(filtered, receipt), bars, receipt, a_opportunities=(opportunity,)
            )
        )
        == 1
    )
    tied = (*bars[:-1], replace(bars[-1], volume=10.0, row_sha256=""))
    tied_receipt = _receipt(tied)
    tied_opportunity = replace(
        opportunity,
        candidate_id=_definition(a_slot, tied_receipt).candidate_id,
        source_publication_sha256=PUBLICATION,
    )
    assert (
        detect_candidate_signals(
            _definition(filtered, tied_receipt),
            tied,
            tied_receipt,
            a_opportunities=(tied_opportunity,),
        )
        == ()
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


def test_wrong_publication_or_receipt_shape_is_rejected_before_signal_emission() -> None:
    bars = _bars([100.0] * 24 + [102.0])
    receipt = _receipt(bars)
    definition = _definition(_slot("A", "donchian_breakout", lookback=24), receipt)

    with pytest.raises(ValueError, match="publication"):
        detect_candidate_signals(definition, bars, replace(receipt, publication_sha256="f" * 64))
    with pytest.raises(ValueError, match="count|range"):
        detect_candidate_signals(definition, bars, replace(receipt, aggregate_bar_count=24))
    with pytest.raises(ValueError, match="source digest"):
        detect_candidate_signals(definition, bars, replace(receipt, source_sha256="e" * 64))
