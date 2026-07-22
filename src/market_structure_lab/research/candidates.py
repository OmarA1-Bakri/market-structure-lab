"""Exact outcome-blind Phase 5 candidate and comparator detectors."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import json
from math import fsum
from statistics import median
from typing import Any, cast

import polars as pl

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import (
    CanonicalAggregateBar,
    canonical_source_row_identity,
    source_rows_sha256,
)
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
from market_structure_lab.data.canonical import CANONICAL_SCHEMA, validate_candle_frame
from market_structure_lab.data.export import read_snapshot_manifest, verify_snapshot
from market_structure_lab.profiles import BinContribution, ProfileAccumulator, UniformAllocation
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle, ProfileSnapshot
from market_structure_lab.research.models import (
    FROZEN_A_SELECTOR_GRID,
    CandidateDefinition,
    CandidateSignal,
    ValidationSlot,
    ValidationSlotKind,
    candidate_definition_for_slot,
    candidate_signal_from_indices,
)
from market_structure_lab.structure.value_migration import (
    ValueMigrationDirection,
    compare_value_migration,
)

_SHA256_LENGTH = 64
_TIMEFRAME_HOURS = {"1h": 1, "4h": 4}
_MAX_PROFILE_PARENT_PARTITION_BYTES = 64 * 1024 * 1024
_MAX_PROFILE_PARENT_PARTITION_ROWS = 2_000
_FROZEN_PROFILE_SEAL = object()
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
    source_start_index: int
    source_end_index: int
    source_row_count: int
    source_start_timestamp: datetime
    source_end_timestamp: datetime
    source_window_sha256: str
    source_timeframe: str = "1m"
    profile_id: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _FROZEN_PROFILE_SEAL:
            raise TypeError("FrozenProfile requires the verified profile factory seal")
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
        if (
            isinstance(self.source_start_index, bool)
            or isinstance(self.source_end_index, bool)
            or self.source_start_index < 0
            or self.source_end_index <= self.source_start_index
            or self.source_row_count != self.source_end_index - self.source_start_index
            or self.source_row_count != self.window_hours * 60
        ):
            raise ValueError("candidate profile source window indices are invalid")
        if self.source_end_timestamp != self.information_cutoff - timedelta(minutes=1):
            raise ValueError("candidate profile source window does not end at its cutoff")
        if self.source_start_timestamp != self.information_cutoff - timedelta(
            hours=self.window_hours
        ):
            raise ValueError("candidate profile source window does not start at its cutoff")
        _require_sha256(self.source_window_sha256, "profile source window")
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
                    "source_start_index": self.source_start_index,
                    "source_end_index": self.source_end_index,
                    "source_row_count": self.source_row_count,
                    "source_start_timestamp": self.source_start_timestamp,
                    "source_end_timestamp": self.source_end_timestamp,
                    "source_window_sha256": self.source_window_sha256,
                    "snapshot": _profile_payload(self.snapshot),
                },
            ),
        )

    @property
    def feature_start(self) -> datetime:
        return self.source_start_timestamp


@dataclass(frozen=True, slots=True)
class VerifiedProfileStream:
    """Unique ordered content receipt for deterministic rolling one-minute profiles."""

    aggregate_series_sha256: str
    source_minute_publication_sha256: str
    profile_config_sha256: str
    bin_metadata_sha256: str
    bin_step: float
    bin_origin: float
    bin_definition_id: str
    window_hours: int
    source_row_ids: tuple[str, ...]
    source_row_count: int
    source_sha256: str
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
            "source_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        cutoffs = tuple(profile.information_cutoff for profile in self.profiles)
        if cutoffs != tuple(sorted(cutoffs)) or len(cutoffs) != len(set(cutoffs)):
            raise ValueError("verified profile stream cutoffs must be unique and ordered")
        if self.ordered_profile_ids != tuple(profile.profile_id for profile in self.profiles):
            raise ValueError("verified profile stream identities differ from profile content")
        if self.source_row_count != len(self.source_row_ids):
            raise ValueError("verified profile stream source row count is invalid")
        if source_rows_sha256(self.source_row_ids, identities=True) != self.source_sha256:
            raise ValueError("verified profile stream ordered source identity is invalid")
        for profile in self.profiles:
            if (
                profile.source_minute_publication_sha256 != self.source_minute_publication_sha256
                or profile.profile_config_sha256 != self.profile_config_sha256
                or profile.bin_metadata_sha256 != self.bin_metadata_sha256
                or profile.window_hours != self.window_hours
                or profile.snapshot.binning.definition_id != self.bin_definition_id
            ):
                raise ValueError("profile content differs from the verified stream receipt")
            expected_window = _profile_window_sha256(
                source_sha256=self.source_sha256,
                source_row_ids=self.source_row_ids,
                start_index=profile.source_start_index,
                end_index=profile.source_end_index,
            )
            if profile.source_window_sha256 != expected_window:
                raise ValueError("profile source window differs from exact ordered parent rows")
        expected = hash_json(
            "verified-candidate-profile-stream-v1",
            {
                "aggregate_series_sha256": self.aggregate_series_sha256,
                "source_minute_publication_sha256": self.source_minute_publication_sha256,
                "profile_config_sha256": self.profile_config_sha256,
                "bin_metadata_sha256": self.bin_metadata_sha256,
                "bin_step": self.bin_step,
                "bin_origin": self.bin_origin,
                "bin_definition_id": self.bin_definition_id,
                "window_hours": self.window_hours,
                "source_row_count": self.source_row_count,
                "source_sha256": self.source_sha256,
                "ordered_profile_ids": self.ordered_profile_ids,
            },
        )
        if self.stream_sha256 != expected:
            raise ValueError("verified profile stream identity mismatch")


def profile_config_artifact_bytes(window_hours: int) -> bytes:
    """Return the exact frozen rolling-profile configuration artifact bytes."""

    return _artifact_bytes(
        {
            "schema_version": 1,
            "policy": "rolling",
            "source_timeframe": "1m",
            "allocation_id": "uniform-touched-v1",
            "value_area_fraction": 0.70,
            "window_hours": window_hours,
        }
    )


def price_precision_artifact_bytes(symbol: str, *, step: float, origin: float = 0.0) -> bytes:
    """Return exact fixed-step price-precision artifact bytes for one symbol."""

    return _artifact_bytes(
        {
            "schema_version": 1,
            "symbol": symbol,
            "binning_version": "fixed-step-v1",
            "step": step,
            "origin": origin,
        }
    )


def build_verified_profile_stream(
    series: VerifiedAggregateSeries,
    *,
    profile_config_bytes: bytes,
    expected_profile_config_sha256: str,
    price_precision_bytes: bytes,
    expected_price_precision_sha256: str,
) -> VerifiedProfileStream:
    """Build rolling profiles only from exact verified parent one-minute publication rows."""

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("profile builder requires a VerifiedAggregateSeries capability")
    _require_sha256(expected_profile_config_sha256, "expected profile config")
    _require_sha256(expected_price_precision_sha256, "expected price precision")
    if hashlib.sha256(profile_config_bytes).hexdigest() != expected_profile_config_sha256:
        raise ValueError("profile config artifact bytes differ from the frozen expectation")
    if hashlib.sha256(price_precision_bytes).hexdigest() != expected_price_precision_sha256:
        raise ValueError("price precision artifact bytes differ from the frozen expectation")
    config = _parse_artifact(profile_config_bytes, "profile config")
    precision = _parse_artifact(price_precision_bytes, "price precision")
    expected_config_fields = {
        "schema_version",
        "policy",
        "source_timeframe",
        "allocation_id",
        "value_area_fraction",
        "window_hours",
    }
    if set(config) != expected_config_fields or profile_config_bytes != _artifact_bytes(config):
        raise ValueError("profile config artifact bytes are not exact canonical JSON")
    if (
        config["schema_version"] != 1
        or config["policy"] != "rolling"
        or config["source_timeframe"] != "1m"
        or config["allocation_id"] != "uniform-touched-v1"
        or config["value_area_fraction"] != 0.70
        or isinstance(config["window_hours"], bool)
        or not isinstance(config["window_hours"], int)
        or config["window_hours"] < 1
    ):
        raise ValueError("profile config artifact differs from the frozen rolling policy")
    expected_precision_fields = {
        "schema_version",
        "symbol",
        "binning_version",
        "step",
        "origin",
    }
    if set(precision) != expected_precision_fields or price_precision_bytes != _artifact_bytes(
        precision
    ):
        raise ValueError("price precision artifact bytes are not exact canonical JSON")
    if (
        precision["schema_version"] != 1
        or precision["symbol"] != series.symbol
        or precision["binning_version"] != "fixed-step-v1"
        or isinstance(precision["step"], bool)
        or not isinstance(precision["step"], (int, float))
        or isinstance(precision["origin"], bool)
        or not isinstance(precision["origin"], (int, float))
    ):
        raise ValueError("price precision artifact differs from the verified aggregate symbol")
    window_hours = cast(int, config["window_hours"])
    profile_config_sha256 = hashlib.sha256(profile_config_bytes).hexdigest()
    bin_metadata_sha256 = hashlib.sha256(price_precision_bytes).hexdigest()
    bin_step = float(cast(float, precision["step"]))
    bin_origin = float(cast(float, precision["origin"]))
    binning = FixedStepBins(
        step=bin_step,
        origin=bin_origin,
        provenance=f"verified-price-precision:{bin_metadata_sha256}",
    )
    source_row_ids = tuple(source_id for bar in series.bars for source_id in bar.source_row_ids)
    if source_rows_sha256(source_row_ids, identities=True) != series.manifest.source_sha256:
        raise ValueError("aggregate source identity differs before profile construction")
    verify_snapshot(series.parent_snapshot_directory, series.parent_snapshot_manifest)
    if (
        read_snapshot_manifest(series.parent_snapshot_directory / "manifest.json")
        != series.parent_snapshot_manifest
    ):
        raise ValueError("profile parent snapshot manifest changed after verification")
    accumulator = ProfileAccumulator(
        binning=binning,
        allocation=UniformAllocation(),
        value_area_fraction=0.70,
    )
    active: deque[BinContribution] = deque()
    profiles: list[FrozenProfile] = []
    cutoff_set = {bar.bar_close for bar in series.bars}
    expected_timestamp = series.bars[0].timestamp
    source_index = 0
    expected_schema = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))
    for path, expected_sha256 in series.manifest.parent_partition_bindings:
        content = read_bounded_regular(
            series.parent_snapshot_directory / path,
            _MAX_PROFILE_PARENT_PARTITION_BYTES,
        )
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("profile parent partition differs from aggregate lineage")
        frame = pl.read_parquet(BytesIO(content))
        if frame.schema != expected_schema or frame.height > _MAX_PROFILE_PARENT_PARTITION_ROWS:
            raise ValueError("profile parent partition schema or row bound is invalid")
        validate_candle_frame(frame)
        selected = frame.filter(
            (pl.col("symbol") == series.symbol)
            & (pl.col("timeframe") == "1m")
            & (pl.col("segment_id") == series.segment_id)
        )
        for row in selected.iter_rows(named=True):
            timestamp = cast(datetime, row["timestamp"])
            if source_index >= len(source_row_ids):
                raise ValueError("profile parent contains extra selected source rows")
            if timestamp != expected_timestamp:
                raise ValueError("profile parent minute rows are missing, duplicated, or reordered")
            observed_id = canonical_source_row_identity(row)
            if observed_id != source_row_ids[source_index]:
                raise ValueError("profile parent row differs from aggregate source identity")
            contribution = accumulator.add(
                Candle(
                    open=float(cast(float, row["open"])),
                    high=float(cast(float, row["high"])),
                    low=float(cast(float, row["low"])),
                    close=float(cast(float, row["close"])),
                    volume=float(cast(float, row["volume"])),
                )
            )
            active.append(contribution)
            window_rows = window_hours * 60
            if len(active) > window_rows:
                accumulator.remove(active.popleft())
            cutoff = timestamp + timedelta(minutes=1)
            if cutoff in cutoff_set and len(active) == window_rows:
                start_index = source_index - window_rows + 1
                end_index = source_index + 1
                profiles.append(
                    FrozenProfile(
                        information_cutoff=cutoff,
                        window_hours=window_hours,
                        source_minute_publication_sha256=series.manifest.parent_snapshot_sha256,
                        profile_config_sha256=profile_config_sha256,
                        bin_metadata_sha256=bin_metadata_sha256,
                        segment_id=series.segment_id,
                        policy="rolling",
                        snapshot=accumulator.snapshot(),
                        source_start_index=start_index,
                        source_end_index=end_index,
                        source_row_count=window_rows,
                        source_start_timestamp=timestamp - timedelta(minutes=window_rows - 1),
                        source_end_timestamp=timestamp,
                        source_window_sha256=_profile_window_sha256(
                            source_sha256=series.manifest.source_sha256,
                            source_row_ids=source_row_ids,
                            start_index=start_index,
                            end_index=end_index,
                        ),
                        seal=_FROZEN_PROFILE_SEAL,
                    )
                )
            source_index += 1
            expected_timestamp += timedelta(minutes=1)
    if source_index != len(source_row_ids):
        raise ValueError("profile parent is missing exact aggregate source rows")
    frozen = tuple(profiles)
    expected_cutoffs = tuple(
        bar.bar_close
        for bar in series.bars
        if bar.bar_close - series.bars[0].timestamp >= timedelta(hours=window_hours)
    )
    if tuple(profile.information_cutoff for profile in frozen) != expected_cutoffs:
        raise ValueError("verified profile stream contains duplicate or missing rolling cutoffs")
    ordered = tuple(profile.profile_id for profile in frozen)
    source_minute_publication_sha256 = series.manifest.parent_snapshot_sha256
    stream_sha256 = hash_json(
        "verified-candidate-profile-stream-v1",
        {
            "aggregate_series_sha256": series.series_sha256,
            "source_minute_publication_sha256": source_minute_publication_sha256,
            "profile_config_sha256": profile_config_sha256,
            "bin_metadata_sha256": bin_metadata_sha256,
            "bin_step": bin_step,
            "bin_origin": bin_origin,
            "bin_definition_id": binning.definition_id,
            "window_hours": window_hours,
            "source_row_count": len(source_row_ids),
            "source_sha256": series.manifest.source_sha256,
            "ordered_profile_ids": ordered,
        },
    )
    return VerifiedProfileStream(
        aggregate_series_sha256=series.series_sha256,
        source_minute_publication_sha256=source_minute_publication_sha256,
        profile_config_sha256=profile_config_sha256,
        bin_metadata_sha256=bin_metadata_sha256,
        bin_step=bin_step,
        bin_origin=bin_origin,
        bin_definition_id=binning.definition_id,
        window_hours=window_hours,
        source_row_ids=source_row_ids,
        source_row_count=len(source_row_ids),
        source_sha256=series.manifest.source_sha256,
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
            events.append((index, index - slow, index))
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
            events.append((index, index - lookback - 1, index))
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
        classified_migration = compare_value_migration(prior_snapshot, snapshot)
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
            cutoff_index=index,
            feature_start_index=(
                _timestamp_index(series, opportunity.feature_start)
                if role == "price_baseline"
                else min(
                    _timestamp_index(series, opportunity.feature_start),
                    _timestamp_index(series, by_cutoff[bars[index - 2].bar_close].feature_start),
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
            cutoff_index=index,
            feature_start_index=min(
                _timestamp_index(series, opportunity.feature_start), index - count
            ),
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
            events.append((index, reference - lookback, index + 1))
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
            candidate_signal_from_indices(
                definition,
                series,
                event_index=signal_index,
                feature_start_index=start_index,
                cutoff_index=cutoff_index,
            )
        )
        frozen_until = legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _signal_from_opportunity(
    definition: CandidateDefinition,
    series: VerifiedAggregateSeries,
    opportunity: CandidateSignal,
    *,
    feature_start_index: int,
    cutoff_index: int,
) -> CandidateSignal:
    if (
        opportunity.information_cutoff != series.bars[cutoff_index].bar_close
        or opportunity.legal_entry != opportunity.information_cutoff
    ):
        raise ValueError("subordinate signal bar does not match its verified cutoff index")
    return candidate_signal_from_indices(
        definition,
        series,
        event_index=cutoff_index,
        feature_start_index=feature_start_index,
        cutoff_index=cutoff_index,
    )


def _timestamp_index(series: VerifiedAggregateSeries, timestamp: datetime) -> int:
    first = series.bars[0]
    width = first.bar_close - first.timestamp
    offset = timestamp - first.timestamp
    index = offset // width
    if (
        offset < timedelta(0)
        or offset % width != timedelta(0)
        or index >= len(series.bars)
        or series.bars[index].timestamp != timestamp
    ):
        raise ValueError("subordinate feature start is outside the verified aggregate series")
    return index


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


def _artifact_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_artifact(content: bytes, label: str) -> dict[str, object]:
    if not isinstance(content, bytes):
        raise TypeError(f"{label} artifact must be supplied as bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} artifact is invalid JSON") from error
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise ValueError(f"{label} artifact must contain one JSON object")
    return cast(dict[str, object], payload)


def _profile_window_sha256(
    *,
    source_sha256: str,
    source_row_ids: Sequence[str],
    start_index: int,
    end_index: int,
) -> str:
    return hash_json(
        "candidate-profile-source-window-v1",
        {
            "source_sha256": source_sha256,
            "start_index": start_index,
            "end_index": end_index,
            "row_count": end_index - start_index,
            "first_source_row_id": source_row_ids[start_index],
            "last_source_row_id": source_row_ids[end_index - 1],
        },
    )


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
    "build_verified_profile_stream",
    "candidate_definition_for_slot",
    "detect_candidate_signals",
    "price_precision_artifact_bytes",
    "profile_config_artifact_bytes",
    "target_bars",
]
