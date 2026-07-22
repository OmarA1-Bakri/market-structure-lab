"""Exact outcome-blind Phase 5 candidate and comparator detectors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timedelta
from math import fsum
from statistics import median
from typing import cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import CanonicalAggregateBar
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.research.models import (
    FROZEN_A_SELECTOR_GRID,
    CandidateDefinition,
    CandidateSignal,
    ValidationSlot,
    ValidationSlotKind,
    emit_candidate_signal,
)
from market_structure_lab.structure.value_migration import (
    ValueMigrationDirection,
    compare_value_migration,
)

_SHA256_LENGTH = 64
_TIMEFRAME_HOURS = {"1h": 1, "4h": 4}
_VERIFIED_PROFILE_STREAM_SEAL = object()


A_SELECTOR_GRID = FROZEN_A_SELECTOR_GRID


@dataclass(frozen=True, slots=True)
class FrozenProfile:
    """A rolling one-minute profile reference frozen at a completed cutoff."""

    information_cutoff: datetime
    window_hours: int
    source_minute_publication_sha256: str
    profile_config_sha256: str
    bin_metadata_sha256: str
    segment_id: int
    policy: str
    snapshot: ProfileSnapshot
    source_timeframe: str = "1m"
    profile_id: str = field(init=False)

    def __post_init__(self) -> None:
        offset = self.information_cutoff.utcoffset()
        if self.information_cutoff.tzinfo is None or offset is None or offset != timedelta(0):
            raise ValueError("profile cutoff must be UTC-aware")
        if isinstance(self.window_hours, bool) or self.window_hours < 1:
            raise ValueError("profile window_hours must be positive")
        _require_sha256(self.source_minute_publication_sha256, "profile source minute publication")
        _require_sha256(self.profile_config_sha256, "profile config")
        _require_sha256(self.bin_metadata_sha256, "profile bin metadata")
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
        if not isinstance(self.snapshot.binning, FixedStepBins):
            raise ValueError("candidate profiles require fixed-step integer bins")
        if self.snapshot.binning.provenance != (
            f"verified-price-precision:{self.bin_metadata_sha256}"
        ):
            raise ValueError("candidate profile binning lacks exact verified precision metadata")
        object.__setattr__(
            self,
            "profile_id",
            hash_json(
                "frozen-candidate-profile-v1",
                {
                    "information_cutoff": self.information_cutoff,
                    "feature_start": self.feature_start,
                    "window_hours": self.window_hours,
                    "source_minute_publication_sha256": self.source_minute_publication_sha256,
                    "profile_config_sha256": self.profile_config_sha256,
                    "bin_metadata_sha256": self.bin_metadata_sha256,
                    "segment_id": self.segment_id,
                    "policy": self.policy,
                    "source_timeframe": self.source_timeframe,
                    "snapshot": _profile_payload(self.snapshot),
                },
            ),
        )

    @property
    def feature_start(self) -> datetime:
        return self.information_cutoff - timedelta(hours=self.window_hours)


@dataclass(frozen=True, slots=True)
class VerifiedProfileStream:
    """Unique ordered content receipt for deterministic rolling one-minute profiles."""

    aggregate_series_sha256: str
    source_minute_publication_sha256: str
    profile_config_sha256: str
    bin_metadata_sha256: str
    bin_step: float
    profiles: tuple[FrozenProfile, ...]
    ordered_profile_ids: tuple[str, ...]
    stream_sha256: str
    seal: InitVar[object]

    def __post_init__(self, seal: object) -> None:
        if seal is not _VERIFIED_PROFILE_STREAM_SEAL:
            raise TypeError("VerifiedProfileStream requires its verifier capability seal")
        for name in (
            "aggregate_series_sha256",
            "source_minute_publication_sha256",
            "profile_config_sha256",
            "bin_metadata_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        cutoffs = tuple(profile.information_cutoff for profile in self.profiles)
        if cutoffs != tuple(sorted(cutoffs)) or len(cutoffs) != len(set(cutoffs)):
            raise ValueError("verified profile stream cutoffs must be unique and ordered")
        if self.ordered_profile_ids != tuple(profile.profile_id for profile in self.profiles):
            raise ValueError("verified profile stream identities differ from profile content")
        for profile in self.profiles:
            if (
                profile.source_minute_publication_sha256 != self.source_minute_publication_sha256
                or profile.profile_config_sha256 != self.profile_config_sha256
                or profile.bin_metadata_sha256 != self.bin_metadata_sha256
                or getattr(profile.snapshot.binning, "step", None) != self.bin_step
            ):
                raise ValueError("profile content differs from the verified stream receipt")
        expected = hash_json(
            "verified-candidate-profile-stream-v1",
            {
                "aggregate_series_sha256": self.aggregate_series_sha256,
                "source_minute_publication_sha256": self.source_minute_publication_sha256,
                "profile_config_sha256": self.profile_config_sha256,
                "bin_metadata_sha256": self.bin_metadata_sha256,
                "bin_step": self.bin_step,
                "ordered_profile_ids": self.ordered_profile_ids,
            },
        )
        if self.stream_sha256 != expected:
            raise ValueError("verified profile stream identity mismatch")


def verify_profile_stream(
    series: VerifiedAggregateSeries,
    profiles: Sequence[FrozenProfile],
    *,
    source_minute_publication_sha256: str,
    profile_config_sha256: str,
    bin_metadata_sha256: str,
    bin_step: float,
) -> VerifiedProfileStream:
    """Verify unique profile content and bind it to source/config/bin metadata."""

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("profile verification requires a VerifiedAggregateSeries capability")
    if source_minute_publication_sha256 != series.manifest.parent_snapshot_sha256:
        raise ValueError("profile source publication differs from aggregate parent minute lineage")
    frozen = tuple(profiles)
    legal_cutoffs = {bar.bar_close for bar in series.bars}
    if any(
        profile.information_cutoff not in legal_cutoffs or profile.segment_id != series.segment_id
        for profile in frozen
    ):
        raise ValueError("profile cutoff or segment differs from the verified aggregate series")
    ordered = tuple(profile.profile_id for profile in frozen)
    stream_sha256 = hash_json(
        "verified-candidate-profile-stream-v1",
        {
            "aggregate_series_sha256": series.series_sha256,
            "source_minute_publication_sha256": source_minute_publication_sha256,
            "profile_config_sha256": profile_config_sha256,
            "bin_metadata_sha256": bin_metadata_sha256,
            "bin_step": bin_step,
            "ordered_profile_ids": ordered,
        },
    )
    return VerifiedProfileStream(
        aggregate_series_sha256=series.series_sha256,
        source_minute_publication_sha256=source_minute_publication_sha256,
        profile_config_sha256=profile_config_sha256,
        bin_metadata_sha256=bin_metadata_sha256,
        bin_step=bin_step,
        profiles=frozen,
        ordered_profile_ids=ordered,
        stream_sha256=stream_sha256,
        seal=_VERIFIED_PROFILE_STREAM_SEAL,
    )


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
    series: VerifiedAggregateSeries,
    *,
    parent_a_candidate: CandidateDefinition | None = None,
    profile_stream: VerifiedProfileStream | None = None,
    a_selector_grid: tuple[str, ...] = A_SELECTOR_GRID,
) -> CandidateDefinition:
    """Freeze a roster slot against one authenticated aggregate publication."""

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("candidate definition requires a VerifiedAggregateSeries capability")
    selector_grid = () if slot.family == "A" else a_selector_grid
    if slot.family != "A" and selector_grid != A_SELECTOR_GRID:
        raise ValueError(
            "subordinate candidate A selector grid does not match the frozen full grid"
        )
    profile_definition_id = None
    profile_bin_step = None
    profile_stream_sha256 = None
    if slot.family == "B":
        if profile_stream is None or profile_stream.aggregate_series_sha256 != series.series_sha256:
            raise ValueError("family B requires a matching verified profile stream")
        profile_bin_step = profile_stream.bin_step
        profile_stream_sha256 = profile_stream.stream_sha256
        profile_bars = _parameter_bars(slot, "profile_hours")
        window_hours = profile_bars * _TIMEFRAME_HOURS[slot.timeframe]
        profile_definition_id = (
            "rolling-1m:uniform-touched-v1:value-area=0.70:"
            f"window-hours={window_hours}:fixed-step={profile_bin_step}:"
            f"source-config={series.manifest.config_version}"
        )
    elif profile_stream is not None:
        raise ValueError("profile stream is valid only for family B")
    parent_id = None
    parent_slot_id = None
    if slot.family in ("B", "E"):
        _validate_parent_definition(slot, series, parent_a_candidate)
        assert parent_a_candidate is not None
        parent_id = parent_a_candidate.candidate_id
        parent_slot_id = parent_a_candidate.slot.slot_id
    return CandidateDefinition(
        slot=slot,
        source_publication_sha256=series.publication_sha256,
        source_series_sha256=series.series_sha256,
        source_segment_id=series.segment_id,
        aggregate_config_version=series.manifest.config_version,
        a_selector_grid=selector_grid,
        profile_bin_step=profile_bin_step,
        profile_definition_id=profile_definition_id,
        profile_stream_sha256=profile_stream_sha256,
        parent_a_candidate_id=parent_id,
        parent_a_slot_id=parent_slot_id,
    )


def detect_candidate_signals(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    *,
    a_opportunities: Sequence[CandidateSignal] = (),
    profile_stream: VerifiedProfileStream | None = None,
) -> tuple[CandidateSignal, ...]:
    """Detect one frozen candidate/comparator over authenticated complete aggregate bars."""

    _validate_series(definition, series)
    bars = series.bars
    if not bars:
        return ()
    if definition.family == "A":
        return _detect_a(definition, series)
    if definition.family == "B":
        if (
            profile_stream is None
            or profile_stream.stream_sha256 != definition.profile_stream_sha256
        ):
            raise ValueError("family B detection requires its exact verified profile stream")
        return _detect_b(definition, series, a_opportunities, profile_stream)
    if definition.family == "G":
        return _detect_g(definition, series)
    if definition.family == "E":
        return _detect_e(definition, series, a_opportunities)
    return _detect_d(definition, series)


def _detect_a(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    detector = dict(definition.parameters)["detector"]
    if detector == "moving_average_crossover":
        return _detect_a_sma(definition, series)
    if detector == "donchian_breakout":
        return _detect_a_donchian(definition, series)
    if detector == "atr_breakout":
        return _detect_a_atr(definition, series)
    if detector == "time_series_momentum":
        return _detect_a_momentum(definition, series)
    raise ValueError("unsupported family A detector")


def _detect_a_sma(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
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
    return _signals(definition, series, events)


def _detect_a_donchian(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
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
    return _signals(definition, series, events)


def _detect_a_atr(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
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
    return _signals(definition, series, events)


def _detect_a_momentum(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    lookback = _parameter_bars(definition.slot, "momentum_hours")
    states: list[int | None] = [None] * len(bars)
    for index in range(lookback, len(bars)):
        states[index] = _strict_sign(bars[index].close - bars[index - lookback].close)
    events: list[tuple[int, int, int]] = []
    for index in range(lookback + 1, len(bars)):
        state = states[index]
        if state == definition.direction and state != 0 and state != states[index - 1]:
            events.append((index, index - lookback, index))
    return _signals(definition, series, events)


def _detect_b(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    opportunities: Sequence[CandidateSignal],
    profile_stream: VerifiedProfileStream,
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    by_cutoff = {profile.information_cutoff: profile for profile in profile_stream.profiles}
    selected: list[tuple[CandidateSignal, int]] = []
    window_bars = _parameter_bars(definition.slot, "profile_hours")
    window_hours = window_bars * _TIMEFRAME_HOURS[definition.timeframe]
    by_bar_cutoff = {bar.bar_close: index for index, bar in enumerate(bars)}
    for opportunity in opportunities:
        _validate_opportunity(definition, series, opportunity)
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
            series,
            opportunity,
            bars[index],
            feature_start=(
                opportunity.feature_start
                if role == "price_baseline"
                else min(
                    opportunity.feature_start, by_cutoff[bars[index - 2].bar_close].feature_start
                )
            ),
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_e(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    opportunities: Sequence[CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    count = _parameter_bars(definition.slot, "volume_median_hours")
    by_cutoff = {bar.bar_close: index for index, bar in enumerate(bars)}
    eligible: list[tuple[CandidateSignal, int]] = []
    selected: list[tuple[CandidateSignal, int]] = []
    for opportunity in opportunities:
        _validate_opportunity(definition, series, opportunity)
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
            series,
            opportunity,
            bars[index],
            feature_start=min(opportunity.feature_start, bars[index - count].timestamp),
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_g(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
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
        emit = condition and (armed if definition.role == "candidate_primary" else True)
        if emit:
            feature_start = {
                "candidate_primary": index - n24 - 3,
                "atr_only_control": index - n24 - 1,
                "donchian_only_control": index - donchian,
            }[definition.role]
            events.append((index, feature_start, index))
            if definition.role == "candidate_primary":
                armed = False
        elif definition.role == "candidate_primary" and not condition:
            armed = True
    return _signals(definition, series, events)


def _detect_d(
    definition: CandidateDefinition, series: VerifiedAggregateSeries
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
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
    return _signals(definition, series, events)


def _signals(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    events: Sequence[tuple[int, int, int]],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    output: list[CandidateSignal] = []
    frozen_until: datetime | None = None
    for signal_index, start_index, cutoff_index in events:
        cutoff = bars[cutoff_index].bar_close
        legal_entry = cutoff
        if frozen_until is not None and legal_entry < frozen_until:
            continue
        output.append(
            emit_candidate_signal(
                definition,
                series,
                symbol=bars[signal_index].symbol,
                feature_start=bars[start_index].timestamp,
                information_cutoff=cutoff,
                legal_entry=legal_entry,
            )
        )
        frozen_until = legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _signal_from_opportunity(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    opportunity: CandidateSignal,
    bar: CanonicalAggregateBar,
    *,
    feature_start: datetime,
) -> CandidateSignal:
    return emit_candidate_signal(
        definition,
        series,
        symbol=bar.symbol,
        feature_start=feature_start,
        information_cutoff=opportunity.information_cutoff,
        legal_entry=opportunity.legal_entry,
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
        or profile.segment_id != definition.source_segment_id
        or getattr(profile.snapshot.binning, "step", None) != definition.profile_bin_step
    ):
        return False
    return isinstance(profile.snapshot.binning, FixedStepBins)


def _validate_opportunity(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    signal: CandidateSignal,
) -> None:
    if signal.family != "A":
        raise ValueError("subordinate candidates require family A opportunities")
    if (
        signal.candidate_id != definition.parent_a_candidate_id
        or signal.candidate_slot_id != definition.parent_a_slot_id
        or signal.source_series_sha256 != series.series_sha256
        or signal.source_publication_sha256 != definition.source_publication_sha256
        or signal.segment_id != definition.source_segment_id
        or signal.timeframe != definition.timeframe
        or signal.direction != definition.direction
    ):
        raise ValueError("A opportunity is not the exact registered parent of this candidate")


def _validate_parent_definition(
    slot: ValidationSlot,
    series: VerifiedAggregateSeries,
    parent: CandidateDefinition | None,
) -> None:
    if parent is None or parent.family != "A":
        raise ValueError("B/E candidates require a registered parent family A candidate")
    if (
        parent.source_series_sha256 != series.series_sha256
        or parent.source_publication_sha256 != series.publication_sha256
        or parent.source_segment_id != series.segment_id
        or parent.timeframe != slot.timeframe
        or parent.slot.direction != slot.direction
    ):
        raise ValueError("parent A candidate does not match the subordinate source series/grid")
    if slot.family == "E":
        parameters = dict(parent.parameters)
        if parameters.get("detector") != "donchian_breakout":
            raise ValueError("family E requires an exact A Donchian opportunity parent")
        if _parameter_bars(parent.slot, "lookback_hours") != _parameter_bars(
            slot, "donchian_hours"
        ):
            raise ValueError(
                "family E parent Donchian lookback does not match its opportunity grid"
            )
    elif hash_json("A-selector-grid-entry-v1", parent.slot.to_dict()) not in A_SELECTOR_GRID:
        raise ValueError("family B parent must be one exact registered A selector-grid candidate")


def _validate_series(definition: CandidateDefinition, series: VerifiedAggregateSeries) -> None:
    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("candidate detection requires a VerifiedAggregateSeries capability")
    if (
        definition.source_series_sha256 != series.series_sha256
        or definition.source_publication_sha256 != series.publication_sha256
        or definition.source_segment_id != series.segment_id
        or definition.timeframe != series.target_timeframe
    ):
        raise ValueError("candidate definition does not own this verified aggregate series")


def _profile_payload(snapshot: ProfileSnapshot) -> dict[str, object]:
    return {
        "bin_volumes": [[index, volume] for index, volume in sorted(snapshot.bin_volumes.items())],
        "total_volume": snapshot.total_volume,
        "poc_index": snapshot.poc_index,
        "value_area_low_index": snapshot.value_area_low_index,
        "value_area_high_index": snapshot.value_area_high_index,
        "vwap": snapshot.vwap,
        "binning_id": snapshot.binning_id,
        "allocation_id": snapshot.allocation_id,
        "value_area_fraction": snapshot.value_area_fraction,
        "work_budget_id": snapshot.work_budget_id,
    }


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


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


__all__ = [
    "A_SELECTOR_GRID",
    "FrozenProfile",
    "VerifiedProfileStream",
    "candidate_definition_for_slot",
    "detect_candidate_signals",
    "target_bars",
    "verify_profile_stream",
]
