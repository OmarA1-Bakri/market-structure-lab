"""Bounded causal detectors for feature changes and market expansion."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from statistics import median

from market_structure_lab.auction.engine import (
    AuctionSnapshot,
    StructuralEventKind,
)
from market_structure_lab.features.models import FeatureRow, timeframe_duration

from .models import EventKind, MarketEvent, make_event

_RESET_EVENTS = frozenset(
    {
        StructuralEventKind.GAP_RESET,
        StructuralEventKind.SEGMENT_RESET,
        StructuralEventKind.WINDOW_RESET,
    }
)


@dataclass(frozen=True, slots=True)
class ChangePointConfig:
    """Configuration for a trailing-median/MAD change detector."""

    feature_name: str
    history_window: int = 60
    min_history: int = 20
    robust_threshold: float = 6.0
    trigger_version: str = "change-point-mad-v1"

    def __post_init__(self) -> None:
        _validate_history_config(self.history_window, self.min_history)
        if not isinstance(self.feature_name, str) or not self.feature_name.strip():
            raise ValueError("feature_name must be non-empty")
        if not _is_finite_number(self.robust_threshold) or self.robust_threshold <= 0:
            raise ValueError("robust_threshold must be finite and positive")
        if not isinstance(self.trigger_version, str) or not self.trigger_version.strip():
            raise ValueError("trigger_version must be non-empty")


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    """Configuration for causal volatility and volume expansion events."""

    volatility_feature: str = "realized_volatility_20"
    history_window: int = 60
    min_history: int = 20
    volatility_multiplier: float = 2.0
    volume_multiplier: float = 2.0
    trigger_version: str = "expansion-median-v1"

    def __post_init__(self) -> None:
        _validate_history_config(self.history_window, self.min_history)
        if not isinstance(self.volatility_feature, str) or not self.volatility_feature.strip():
            raise ValueError("volatility_feature must be non-empty")
        for value, field in (
            (self.volatility_multiplier, "volatility_multiplier"),
            (self.volume_multiplier, "volume_multiplier"),
        ):
            if not _is_finite_number(value) or value <= 1:
                raise ValueError(f"{field} must be finite and greater than 1")
        if not isinstance(self.trigger_version, str) or not self.trigger_version.strip():
            raise ValueError("trigger_version must be non-empty")


def validate_observation_pair(snapshot: AuctionSnapshot, row: FeatureRow) -> None:
    """Fail closed unless a feature row describes exactly the supplied snapshot."""

    if not isinstance(snapshot, AuctionSnapshot):
        raise TypeError("snapshot must be an AuctionSnapshot")
    if not isinstance(row, FeatureRow):
        raise TypeError("row must be a FeatureRow")
    candle = snapshot.latest_candle
    snapshot_identities = (
        (snapshot.timestamp, candle.timestamp, "snapshot candle timestamp"),
        (snapshot.symbol, candle.symbol, "snapshot candle symbol"),
        (snapshot.timeframe, candle.timeframe, "snapshot candle timeframe"),
        (snapshot.segment_id, candle.segment_id, "snapshot candle segment"),
    )
    for expected, actual, field in snapshot_identities:
        if expected != actual:
            raise ValueError(f"{field} mismatch")
    expected_cutoff = snapshot.timestamp + timeframe_duration(snapshot.timeframe)
    identities = (
        (snapshot.timestamp, row.timestamp, "timestamp"),
        (expected_cutoff, row.information_cutoff, "information cutoff"),
        (snapshot.symbol, row.symbol, "symbol"),
        (snapshot.timeframe, row.timeframe, "timeframe"),
        (snapshot.segment_id, row.segment_id, "segment"),
        (snapshot.dataset_version, row.dataset_version, "dataset version"),
        (snapshot.config_version, row.config_version, "config version"),
        (snapshot.profile_definition_id, row.profile_version, "profile version"),
        (snapshot.window_version, row.window_policy_id, "window policy"),
    )
    for expected, actual, field in identities:
        if expected != actual:
            raise ValueError(f"snapshot/feature {field} mismatch")


class CausalChangePointDetector:
    """Detect robust deviations using completed prior observations only."""

    def __init__(self, config: ChangePointConfig) -> None:
        if not isinstance(config, ChangePointConfig):
            raise TypeError("config must be a ChangePointConfig")
        self.config = config
        self._history: deque[float] = deque(maxlen=config.history_window)
        self._latest: AuctionSnapshot | None = None

    @property
    def history_size(self) -> int:
        return len(self._history)

    def update(self, snapshot: AuctionSnapshot, row: FeatureRow) -> MarketEvent | None:
        validate_observation_pair(snapshot, row)
        value = _numeric_feature(row, self.config.feature_name)
        reset = observation_resets_continuity(self._latest, snapshot)
        if reset:
            self._history.clear()

        event = None
        if value is not None and len(self._history) >= self.config.min_history:
            center = float(median(self._history))
            mad = float(median(abs(item - center) for item in self._history))
            deviation = abs(value - center)
            # No epsilon is injected: when the completed-prior MAD is zero, only an
            # exact departure from the prior median is considered a change.
            triggered = (
                deviation > 0
                if mad == 0
                else deviation / mad >= self.config.robust_threshold
            )
            if triggered:
                event = make_event(
                    EventKind.CHANGE_POINT,
                    row.timestamp,
                    row.information_cutoff,
                    row,
                    self.config.trigger_version,
                    metadata={
                        "feature_name": self.config.feature_name,
                        "current_value": value,
                        "prior_median": center,
                        "prior_mad": mad,
                        "absolute_deviation": deviation,
                        "robust_score": None if mad == 0 else deviation / mad,
                        "zero_scale_rule": "exact_departure" if mad == 0 else "not_applicable",
                        "prior_observation_count": len(self._history),
                    },
                )

        if value is not None:
            self._history.append(value)
        self._latest = snapshot
        return event

    def reset(self) -> None:
        self._history.clear()
        self._latest = None


class ExpansionDetector:
    """Detect volatility or volume expansion against completed prior medians."""

    def __init__(self, config: ExpansionConfig | None = None) -> None:
        self.config = ExpansionConfig() if config is None else config
        if not isinstance(self.config, ExpansionConfig):
            raise TypeError("config must be an ExpansionConfig")
        self._volatility: deque[float] = deque(maxlen=self.config.history_window)
        self._volume: deque[float] = deque(maxlen=self.config.history_window)
        self._latest: AuctionSnapshot | None = None

    @property
    def history_size(self) -> int:
        return max(len(self._volatility), len(self._volume))

    def update(self, snapshot: AuctionSnapshot, row: FeatureRow) -> MarketEvent | None:
        validate_observation_pair(snapshot, row)
        volatility = _numeric_feature(row, self.config.volatility_feature)
        volume = snapshot.latest_candle.volume
        reset = observation_resets_continuity(self._latest, snapshot)
        if reset:
            self._volatility.clear()
            self._volume.clear()

        event = None
        volatility_baseline = (
            float(median(self._volatility))
            if volatility is not None and len(self._volatility) >= self.config.min_history
            else None
        )
        volume_baseline = (
            float(median(self._volume))
            if len(self._volume) >= self.config.min_history
            else None
        )
        volatility_expanded = (
            volatility is not None
            and volatility_baseline is not None
            and _exceeds_baseline(
                volatility,
                volatility_baseline,
                self.config.volatility_multiplier,
            )
        )
        volume_expanded = volume_baseline is not None and _exceeds_baseline(
            volume, volume_baseline, self.config.volume_multiplier
        )
        if volatility_expanded or volume_expanded:
            event = make_event(
                EventKind.EXPANSION,
                row.timestamp,
                row.information_cutoff,
                row,
                self.config.trigger_version,
                metadata={
                    "volatility_feature": self.config.volatility_feature,
                    "current_volatility": volatility,
                    "prior_volatility_median": volatility_baseline,
                    "volatility_multiplier": self.config.volatility_multiplier,
                    "volatility_expanded": volatility_expanded,
                    "current_volume": volume,
                    "prior_volume_median": volume_baseline,
                    "volume_multiplier": self.config.volume_multiplier,
                    "volume_expanded": volume_expanded,
                    "zero_baseline_rule": "positive_current_is_expansion",
                    "prior_volatility_observation_count": len(self._volatility),
                    "prior_volume_observation_count": len(self._volume),
                },
            )

        if volatility is not None:
            self._volatility.append(volatility)
        self._volume.append(volume)
        self._latest = snapshot
        return event

    def reset(self) -> None:
        self._volatility.clear()
        self._volume.clear()
        self._latest = None


def observation_resets_continuity(
    previous: AuctionSnapshot | None, current: AuctionSnapshot
) -> bool:
    """Validate ordering and return whether current starts a new causal segment."""

    if previous is None:
        return True
    if current.symbol != previous.symbol:
        raise ValueError("event stream symbol changed")
    if current.timeframe != previous.timeframe:
        raise ValueError("event stream timeframe changed")
    if current.timestamp == previous.timestamp:
        raise ValueError("duplicate event observation timestamp")
    if current.timestamp < previous.timestamp:
        raise ValueError("out-of-order event observation timestamp")
    reset_event = any(event.kind in _RESET_EVENTS for event in current.events)
    segment_changed = current.segment_id != previous.segment_id
    expected = timeframe_duration(current.timeframe)
    gap = current.timestamp - previous.timestamp != expected
    if gap and not (reset_event or segment_changed):
        raise ValueError("unexplained material gap in event stream")
    return reset_event or segment_changed


def _numeric_feature(row: FeatureRow, name: str) -> float | None:
    if name not in row.values:
        raise ValueError(f"feature row does not contain {name!r}")
    value = row.values[name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"feature {name!r} must be numeric or null")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"feature {name!r} must be finite")
    return normalized


def _exceeds_baseline(current: float, baseline: float, multiplier: float) -> bool:
    if current < 0:
        raise ValueError("expansion observations must be non-negative")
    if baseline < 0:
        raise ValueError("expansion baselines must be non-negative")
    if baseline == 0:
        return current > 0
    return current >= baseline * multiplier


def _validate_history_config(history_window: int, min_history: int) -> None:
    for value, field in ((history_window, "history_window"), (min_history, "min_history")):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field} must be an integer")
        if value < 1:
            raise ValueError(f"{field} must be positive")
    if min_history > history_window:
        raise ValueError("min_history must not exceed history_window")


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )
