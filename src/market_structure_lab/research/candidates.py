"""Exact outcome-blind Phase 5 candidate and comparator detectors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import fsum
from statistics import median
from typing import Protocol, cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import CanonicalAggregateBar, source_rows_sha256
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.research.models import (
    FROZEN_A_SELECTOR_GRID,
    CandidateDefinition,
    CandidateSignal,
    ValidationSlot,
    ValidationSlotKind,
)
from market_structure_lab.structure.value_migration import (
    ValueMigrationDirection,
    compare_value_migration,
)

_SHA256_LENGTH = 64
_TIMEFRAME_HOURS = {"1h": 1, "4h": 4}


class AggregatePublicationReceipt(Protocol):
    publication_sha256: str
    symbol: str
    target_timeframe: str
    segment_id: int
    aggregate_bar_count: int
    min_timestamp: str
    max_timestamp: str
    source_sha256: str
    parent_snapshot_sha256: str


A_SELECTOR_GRID = FROZEN_A_SELECTOR_GRID


@dataclass(frozen=True, slots=True)
class FrozenProfile:
    """A rolling one-minute profile reference frozen at a completed cutoff."""

    information_cutoff: datetime
    window_hours: int
    source_publication_sha256: str
    segment_id: int
    policy: str
    snapshot: ProfileSnapshot
    source_timeframe: str = "1m"

    def __post_init__(self) -> None:
        offset = self.information_cutoff.utcoffset()
        if self.information_cutoff.tzinfo is None or offset is None or offset != timedelta(0):
            raise ValueError("profile cutoff must be UTC-aware")
        if isinstance(self.window_hours, bool) or self.window_hours < 1:
            raise ValueError("profile window_hours must be positive")
        _require_sha256(self.source_publication_sha256, "profile source publication")
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("profile segment_id must be non-negative")
        if self.policy != "rolling":
            raise ValueError("candidate profiles require the rolling policy")
        if self.source_timeframe != "1m":
            raise ValueError("candidate profiles must be constructed from one-minute bars")
        if self.snapshot.allocation_id != "uniform-touched-v1":
            raise ValueError("candidate profiles require uniform-touched-v1 allocation")
        if self.snapshot.value_area_fraction != 0.70:
            raise ValueError("candidate profiles require the frozen 70% value area")


def target_bars(hours: int, timeframe: str) -> int:
    """Convert a civil-hour window to an exact integral target-bar count."""

    if timeframe not in _TIMEFRAME_HOURS:
        raise ValueError("candidate timeframe must be 1h or 4h")
    width = _TIMEFRAME_HOURS[timeframe]
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1 or hours % width:
        raise ValueError("candidate hour window must convert to integral target bars")
    return hours // width


def candidate_definition_for_slot(
    slot: ValidationSlot,
    receipt: AggregatePublicationReceipt,
    *,
    profile_bin_step: float | None = None,
    a_selector_grid: tuple[str, ...] = A_SELECTOR_GRID,
) -> CandidateDefinition:
    """Freeze a roster slot against one authenticated aggregate publication."""

    _validate_receipt_shape(receipt)
    selector_grid = () if slot.family == "A" else a_selector_grid
    if slot.family != "A" and selector_grid != A_SELECTOR_GRID:
        raise ValueError(
            "subordinate candidate A selector grid does not match the frozen full grid"
        )
    profile_definition_id = None
    if slot.family == "B":
        if profile_bin_step is None or profile_bin_step <= 0:
            raise ValueError("family B requires a verified positive profile_bin_step")
        profile_bars = _parameter_bars(slot, "profile_hours")
        window_hours = profile_bars * _TIMEFRAME_HOURS[slot.timeframe]
        profile_definition_id = (
            "rolling-1m:uniform-touched-v1:value-area=0.70:"
            f"window-hours={window_hours}:fixed-step={profile_bin_step}:"
            f"source-config={getattr(receipt, 'config_version', 'aggregate-config-v1')}"
        )
    elif profile_bin_step is not None:
        raise ValueError("profile_bin_step is valid only for family B")
    return CandidateDefinition(
        slot=slot,
        source_publication_sha256=receipt.publication_sha256,
        source_segment_id=receipt.segment_id,
        aggregate_config_version=getattr(receipt, "config_version", "aggregate-config-v1"),
        a_selector_grid=selector_grid,
        profile_bin_step=profile_bin_step,
        profile_definition_id=profile_definition_id,
    )


def detect_candidate_signals(
    definition: CandidateDefinition,
    bars: Sequence[CanonicalAggregateBar],
    receipt: AggregatePublicationReceipt,
    *,
    a_opportunities: Sequence[CandidateSignal] = (),
    profiles: Sequence[FrozenProfile] = (),
) -> tuple[CandidateSignal, ...]:
    """Detect one frozen candidate/comparator over authenticated complete aggregate bars."""

    _validate_inputs(definition, bars, receipt)
    if not bars:
        return ()
    emitted: list[CandidateSignal] = []
    offset = 0
    for run in _contiguous_runs(bars):
        opportunities = tuple(
            signal
            for signal in a_opportunities
            if run[0].timestamp < signal.information_cutoff <= run[-1].bar_close
        )
        if definition.family == "A":
            detected = _detect_a(definition, run)
        elif definition.family == "B":
            detected = _detect_b(definition, run, opportunities, profiles)
        elif definition.family == "G":
            detected = _detect_g(definition, run)
        elif definition.family == "E":
            detected = _detect_e(definition, run, opportunities)
        else:
            detected = _detect_d(definition, run)
        emitted.extend(detected)
        offset += len(run)
    del offset
    return tuple(emitted)


def _detect_a(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    detector = dict(definition.parameters)["detector"]
    if detector == "moving_average_crossover":
        return _detect_a_sma(definition, bars)
    if detector == "donchian_breakout":
        return _detect_a_donchian(definition, bars)
    if detector == "atr_breakout":
        return _detect_a_atr(definition, bars)
    if detector == "time_series_momentum":
        return _detect_a_momentum(definition, bars)
    raise ValueError("unsupported family A detector")


def _detect_a_sma(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    fast = _parameter_bars(definition.slot, "fast_hours")
    slow = _parameter_bars(definition.slot, "slow_hours")
    states: list[int | None] = [None] * len(bars)
    for index in range(slow - 1, len(bars)):
        fast_mean = fsum(bar.close for bar in bars[index - fast + 1 : index + 1]) / fast
        slow_mean = fsum(bar.close for bar in bars[index - slow + 1 : index + 1]) / slow
        states[index] = _strict_sign(fast_mean - slow_mean)
    events: list[tuple[int, int, int]] = []
    for index in range(slow, len(bars)):
        state = states[index]
        previous = states[index - 1]
        if state == definition.direction and state != 0 and state != previous:
            events.append((index, index - slow + 1, index))
    return _signals(definition, bars, events)


def _detect_a_donchian(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    lookback = _parameter_bars(definition.slot, "lookback_hours")
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(lookback, len(bars)):
        upper, lower = _donchian(bars, index, lookback)
        condition = (
            bars[index].close > upper if definition.direction == 1 else bars[index].close < lower
        )
        if condition and armed:
            events.append((index, index - lookback, index))
            armed = False
        elif not armed and lower <= bars[index].close <= upper:
            armed = True
    return _signals(definition, bars, events)


def _detect_a_atr(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    count = _parameter_bars(definition.slot, "atr_hours")
    true_ranges = _true_ranges(bars)
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(count + 1, len(bars)):
        prior_atr = _mean_true_range(true_ranges[index - count : index])
        if prior_atr is None:
            continue
        threshold = bars[index - 1].close + definition.direction * prior_atr
        condition = (
            bars[index].close > threshold
            if definition.direction == 1
            else bars[index].close < threshold
        )
        if condition and armed:
            events.append((index, index - count - 1, index))
            armed = False
        elif not condition:
            armed = True
    return _signals(definition, bars, events)


def _detect_a_momentum(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    lookback = _parameter_bars(definition.slot, "momentum_hours")
    states: list[int | None] = [None] * len(bars)
    for index in range(lookback, len(bars)):
        states[index] = _strict_sign(bars[index].close - bars[index - lookback].close)
    events: list[tuple[int, int, int]] = []
    for index in range(lookback + 1, len(bars)):
        state = states[index]
        if state == definition.direction and state != 0 and state != states[index - 1]:
            events.append((index, index - lookback, index))
    return _signals(definition, bars, events)


def _detect_b(
    definition: CandidateDefinition,
    bars: Sequence[CanonicalAggregateBar],
    opportunities: Sequence[CandidateSignal],
    profiles: Sequence[FrozenProfile],
) -> tuple[CandidateSignal, ...]:
    by_cutoff = {profile.information_cutoff: profile for profile in profiles}
    selected: list[tuple[CandidateSignal, int]] = []
    window_bars = _parameter_bars(definition.slot, "profile_hours")
    window_hours = window_bars * _TIMEFRAME_HOURS[definition.timeframe]
    by_bar_cutoff = {bar.bar_close: index for index, bar in enumerate(bars)}
    for opportunity in opportunities:
        _validate_opportunity(definition, opportunity)
        index = by_bar_cutoff.get(opportunity.information_cutoff)
        if index is None or index < 2:
            continue
        previous = by_cutoff.get(bars[index - 2].bar_close)
        current = by_cutoff.get(bars[index - 1].bar_close)
        if previous is None or current is None:
            continue
        if not _valid_profile(definition, previous, window_hours) or not _valid_profile(
            definition, current, window_hours
        ):
            continue
        prior_snapshot = previous.snapshot
        snapshot = current.snapshot
        classified_migration = compare_value_migration(prior_snapshot, snapshot)
        references = (
            prior_snapshot.poc_index,
            prior_snapshot.value_area_low_index,
            prior_snapshot.value_area_high_index,
            snapshot.poc_index,
            snapshot.value_area_low_index,
            snapshot.value_area_high_index,
        )
        if any(value is None for value in references):
            continue
        prior_poc, prior_low, prior_high, poc, low, high = cast(tuple[int, ...], references)
        migration = (
            poc > prior_poc
            and low + high > prior_low + prior_high
            and classified_migration.direction
            in (ValueMigrationDirection.HIGHER, ValueMigrationDirection.OVERLAPPING_HIGHER)
            if definition.direction == 1
            else poc < prior_poc
            and low + high < prior_low + prior_high
            and classified_migration.direction
            in (ValueMigrationDirection.LOWER, ValueMigrationDirection.OVERLAPPING_LOWER)
        )
        accepted = (
            low <= snapshot.binning.bin_index(bars[index - 1].close) <= high
            and low <= (snapshot.binning.bin_index(bars[index].close)) <= high
        )
        if migration and accepted:
            selected.append((opportunity, index))
    role = definition.role
    all_opportunities = [
        (opportunity, by_bar_cutoff[opportunity.information_cutoff])
        for opportunity in opportunities
        if opportunity.information_cutoff in by_bar_cutoff
    ]
    if role == "price_baseline":
        chosen = all_opportunities
    elif role in ("structure_only", "combined_primary"):
        chosen = selected
    elif role == "rate_matched_placebo":
        chosen = _rate_matched(definition, all_opportunities, len(selected))
    else:
        raise ValueError("unsupported family B role")
    signals = tuple(
        _signal_from_opportunity(
            definition,
            opportunity,
            bars[index],
            feature_start=(
                opportunity.feature_start
                if role == "price_baseline"
                else min(
                    opportunity.feature_start, bars[index].bar_close - timedelta(hours=window_hours)
                )
            ),
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_e(
    definition: CandidateDefinition,
    bars: Sequence[CanonicalAggregateBar],
    opportunities: Sequence[CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    count = _parameter_bars(definition.slot, "volume_median_hours")
    by_cutoff = {bar.bar_close: index for index, bar in enumerate(bars)}
    eligible: list[tuple[CandidateSignal, int]] = []
    selected: list[tuple[CandidateSignal, int]] = []
    for opportunity in opportunities:
        _validate_opportunity(definition, opportunity)
        index = by_cutoff.get(opportunity.information_cutoff)
        if index is None or index < count:
            continue
        item = (opportunity, index)
        eligible.append(item)
        if bars[index].volume > median(bar.volume for bar in bars[index - count : index]):
            selected.append(item)
    if definition.role == "price_only":
        chosen = eligible
    elif definition.role == "volume_filtered_primary":
        chosen = selected
    elif definition.role == "rate_matched_placebo":
        chosen = _rate_matched(definition, eligible, len(selected))
    else:
        raise ValueError("unsupported family E role")
    signals = tuple(
        _signal_from_opportunity(
            definition,
            opportunity,
            bars[index],
            feature_start=min(opportunity.feature_start, bars[index - count].timestamp),
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_g(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    n8 = _parameter_bars(definition.slot, "atr_short_hours")
    n24 = _parameter_bars(definition.slot, "atr_long_hours")
    donchian = _parameter_bars(definition.slot, "donchian_hours")
    true_ranges = _true_ranges(bars)
    armed = True
    events: list[tuple[int, int, int]] = []
    first = {
        "candidate_primary": max(n24 + 3, donchian),
        "atr_only_control": n24 + 1,
        "donchian_only_control": donchian,
    }.get(definition.role)
    if first is None:
        raise ValueError("unsupported family G role")
    for index in range(first, len(bars)):
        upper, lower = _donchian(bars, index, donchian)
        breakout = (
            bars[index].close > upper if definition.direction == 1 else bars[index].close < lower
        )
        prior_atr = _mean_true_range(true_ranges[index - n24 : index])
        current_true_range = true_ranges[index]
        expansion = (
            prior_atr is not None
            and current_true_range is not None
            and current_true_range > prior_atr
        )
        if definition.role == "candidate_primary":
            compression = all(
                (short := _mean_true_range(true_ranges[j - n8 + 1 : j + 1])) is not None
                and (long := _mean_true_range(true_ranges[j - n24 + 1 : j + 1])) is not None
                and short < long
                for j in (index - 3, index - 2, index - 1)
            )
            condition = compression and expansion and breakout
        elif definition.role == "atr_only_control":
            condition = expansion and _strict_sign(bars[index].close - bars[index - 1].close) == (
                definition.direction
            )
        elif definition.role == "donchian_only_control":
            condition = breakout
        else:
            condition = breakout
        if condition and armed:
            feature_start = {
                "candidate_primary": index - n24 - 3,
                "atr_only_control": index - n24 - 1,
                "donchian_only_control": index - donchian,
            }[definition.role]
            events.append((index, feature_start, index))
            armed = False
        elif not condition:
            armed = True
    return _signals(definition, bars, events)


def _detect_d(
    definition: CandidateDefinition, bars: Sequence[CanonicalAggregateBar]
) -> tuple[CandidateSignal, ...]:
    lookback = _parameter_bars(definition.slot, "level_hours")
    week = target_bars(24 * 7, definition.timeframe)
    pseudo = definition.role == "pseudo_level_control"
    first = lookback + week if pseudo else lookback
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(first, len(bars) - 1):
        reference = index - week if pseudo else index
        upper, lower = _donchian(bars, reference, lookback)
        if definition.direction == -1:
            breach = bars[index].high > upper and bars[index].close <= upper
            confirmed = bars[index + 1].close < upper
        else:
            breach = bars[index].low < lower and bars[index].close >= lower
            confirmed = bars[index + 1].close > lower
        condition = breach and (confirmed or definition.role == "failed_donchian_control")
        if condition and armed:
            events.append((index + 1, reference - lookback, index + 1))
            armed = False
        elif not breach:
            armed = True
    return _signals(definition, bars, events)


def _signals(
    definition: CandidateDefinition,
    bars: Sequence[CanonicalAggregateBar],
    events: Sequence[tuple[int, int, int]],
) -> tuple[CandidateSignal, ...]:
    output: list[CandidateSignal] = []
    frozen_until: datetime | None = None
    for signal_index, start_index, cutoff_index in events:
        cutoff = bars[cutoff_index].bar_close
        legal_entry = cutoff
        if frozen_until is not None and legal_entry < frozen_until:
            continue
        output.append(
            CandidateSignal(
                candidate_id=definition.candidate_id,
                family=definition.family,
                symbol=bars[signal_index].symbol,
                timeframe=definition.timeframe,
                direction=definition.direction,
                feature_start=bars[start_index].timestamp,
                information_cutoff=cutoff,
                legal_entry=legal_entry,
                source_publication_sha256=definition.source_publication_sha256,
                segment_id=definition.source_segment_id,
            )
        )
        frozen_until = legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _signal_from_opportunity(
    definition: CandidateDefinition,
    opportunity: CandidateSignal,
    bar: CanonicalAggregateBar,
    *,
    feature_start: datetime,
) -> CandidateSignal:
    return CandidateSignal(
        candidate_id=definition.candidate_id,
        family=definition.family,
        symbol=bar.symbol,
        timeframe=definition.timeframe,
        direction=definition.direction,
        feature_start=feature_start,
        information_cutoff=opportunity.information_cutoff,
        legal_entry=opportunity.legal_entry,
        source_publication_sha256=definition.source_publication_sha256,
        segment_id=definition.source_segment_id,
    )


def _suppress_overlap(
    definition: CandidateDefinition, signals: Sequence[CandidateSignal]
) -> tuple[CandidateSignal, ...]:
    output: list[CandidateSignal] = []
    frozen_until: datetime | None = None
    for signal in sorted(signals, key=lambda item: (item.legal_entry, item.signal_id)):
        if frozen_until is not None and signal.legal_entry < frozen_until:
            continue
        output.append(signal)
        frozen_until = signal.legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _rate_matched(
    definition: CandidateDefinition,
    population: Sequence[tuple[CandidateSignal, int]],
    count: int,
) -> list[tuple[CandidateSignal, int]]:
    ranked = sorted(
        population,
        key=lambda item: hash_json(
            "outcome-blind-rate-matched-placebo-v1",
            {"candidate_id": definition.candidate_id, "opportunity_id": item[0].signal_id},
        ),
    )
    return ranked[:count]


def _valid_profile(
    definition: CandidateDefinition, profile: FrozenProfile, window_hours: int
) -> bool:
    if (
        profile.window_hours != window_hours
        or profile.source_publication_sha256 != definition.source_publication_sha256
        or profile.segment_id != definition.source_segment_id
        or getattr(profile.snapshot.binning, "step", None) != definition.profile_bin_step
    ):
        return False
    return "source=" in profile.snapshot.binning.definition_id


def _validate_opportunity(definition: CandidateDefinition, signal: CandidateSignal) -> None:
    if signal.family != "A":
        raise ValueError("subordinate candidates require family A opportunities")
    if (
        signal.source_publication_sha256 != definition.source_publication_sha256
        or signal.segment_id != definition.source_segment_id
        or signal.timeframe != definition.timeframe
        or signal.direction != definition.direction
    ):
        raise ValueError("A opportunity lineage does not match the subordinate candidate")


def _parameter_bars(slot: ValidationSlot, name: str) -> int:
    parameters = dict(slot.parameters)
    base = target_bars(int(parameters[name]), slot.timeframe)
    if (
        slot.kind is ValidationSlotKind.PERTURBATION
        and parameters.get("perturbed_parameter") == name
    ):
        return int(parameters["candidate_bars"])
    return base


def _true_ranges(bars: Sequence[CanonicalAggregateBar]) -> tuple[float | None, ...]:
    output: list[float | None] = []
    for index, bar in enumerate(bars):
        if not index:
            output.append(None)
            continue
        output.append(
            max(
                bar.high - bar.low,
                abs(bar.high - bars[index - 1].close),
                abs(bar.low - bars[index - 1].close),
            )
        )
    return tuple(output)


def _mean_true_range(values: Sequence[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return fsum(cast(float, value) for value in values) / len(values)


def _donchian(
    bars: Sequence[CanonicalAggregateBar], index: int, lookback: int
) -> tuple[float, float]:
    reference = bars[index - lookback : index]
    return max(bar.high for bar in reference), min(bar.low for bar in reference)


def _strict_sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _contiguous_runs(
    bars: Sequence[CanonicalAggregateBar],
) -> tuple[tuple[CanonicalAggregateBar, ...], ...]:
    if not bars:
        return ()
    runs: list[list[CanonicalAggregateBar]] = [[bars[0]]]
    for bar in bars[1:]:
        previous = runs[-1][-1]
        if (
            bar.timestamp != previous.bar_close
            or bar.symbol != previous.symbol
            or bar.target_timeframe != previous.target_timeframe
            or bar.segment_id != previous.segment_id
        ):
            runs.append([bar])
        else:
            runs[-1].append(bar)
    return tuple(tuple(run) for run in runs)


def _validate_inputs(
    definition: CandidateDefinition,
    bars: Sequence[CanonicalAggregateBar],
    receipt: AggregatePublicationReceipt,
) -> None:
    _validate_receipt_shape(receipt)
    if definition.source_publication_sha256 != receipt.publication_sha256:
        raise ValueError("candidate source publication does not match the receipt")
    if definition.source_segment_id != receipt.segment_id:
        raise ValueError("candidate source segment does not match the receipt")
    if definition.timeframe != receipt.target_timeframe:
        raise ValueError("candidate timeframe does not match the receipt")
    if len(bars) != receipt.aggregate_bar_count:
        raise ValueError("aggregate receipt count does not match supplied bars")
    if not bars:
        return
    expected_min = _parse_utc(receipt.min_timestamp)
    expected_max = _parse_utc(receipt.max_timestamp)
    if bars[0].timestamp != expected_min or bars[-1].timestamp != expected_max:
        raise ValueError("aggregate receipt range does not match supplied bars")
    for bar in bars:
        if not isinstance(bar, CanonicalAggregateBar):
            raise TypeError("candidate inputs must be authenticated CanonicalAggregateBar rows")
        if (
            bar.symbol != receipt.symbol
            or bar.target_timeframe != receipt.target_timeframe
            or bar.segment_id != receipt.segment_id
        ):
            raise ValueError("aggregate row identity does not match publication receipt")
        if bar.parent_snapshot_sha256 != receipt.parent_snapshot_sha256:
            raise ValueError("aggregate row parent snapshot does not match publication receipt")
    observed_source = source_rows_sha256(
        (source_row_id for bar in bars for source_row_id in bar.source_row_ids),
        identities=True,
    )
    if observed_source != receipt.source_sha256:
        raise ValueError("aggregate rows do not match the authenticated publication source digest")


def _validate_receipt_shape(receipt: AggregatePublicationReceipt) -> None:
    _require_sha256(receipt.publication_sha256, "aggregate publication")
    _require_sha256(receipt.source_sha256, "aggregate publication source")
    _require_sha256(receipt.parent_snapshot_sha256, "aggregate parent snapshot")
    if receipt.target_timeframe not in _TIMEFRAME_HOURS:
        raise ValueError("aggregate receipt timeframe must be 1h or 4h")
    if isinstance(receipt.segment_id, bool) or receipt.segment_id < 0:
        raise ValueError("aggregate receipt segment_id must be non-negative")
    if isinstance(receipt.aggregate_bar_count, bool) or receipt.aggregate_bar_count < 0:
        raise ValueError("aggregate receipt count must be non-negative")
    _parse_utc(receipt.min_timestamp)
    _parse_utc(receipt.max_timestamp)


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _parse_utc(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("aggregate receipt timestamp must be UTC-aware")
    return timestamp.astimezone(UTC)


__all__ = [
    "A_SELECTOR_GRID",
    "FrozenProfile",
    "candidate_definition_for_slot",
    "detect_candidate_signals",
    "target_bars",
]
