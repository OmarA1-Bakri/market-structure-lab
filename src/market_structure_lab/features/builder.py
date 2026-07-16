"""Bounded, causal construction of audited discovery feature rows."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from math import fsum, log, sqrt
from statistics import median

from market_structure_lab.auction.engine import (
    AuctionLocation,
    AuctionSnapshot,
    StructuralEventKind,
)
from market_structure_lab.features.builtin import builtin_feature_registry
from market_structure_lab.features.models import FeatureRow, FeatureValue, timeframe_duration
from market_structure_lab.features.registry import FeatureRegistry
from market_structure_lab.structure.nodes import NodeKind, ProfileNode

_HISTORY_LIMIT = 21
_RESET_EVENTS = frozenset(
    {
        StructuralEventKind.WINDOW_RESET,
        StructuralEventKind.GAP_RESET,
        StructuralEventKind.SEGMENT_RESET,
    }
)
_INSIDE_VALUE = frozenset(
    {
        AuctionLocation.LOWER_VALUE,
        AuctionLocation.POINT_OF_CONTROL,
        AuctionLocation.UPPER_VALUE,
    }
)


class FeatureBuilder:
    """Build one registered feature row per ordered immutable snapshot."""

    def __init__(self, registry: FeatureRegistry | None = None) -> None:
        expected = builtin_feature_registry()
        selected = expected if registry is None else registry
        if not isinstance(selected, FeatureRegistry):
            raise TypeError("registry must be a FeatureRegistry")
        if selected.registry_id != expected.registry_id:
            raise ValueError("registry must exactly match the audited built-in feature registry")
        self._registry = selected
        self._history: deque[AuctionSnapshot] = deque(maxlen=_HISTORY_LIMIT)
        self._latest: AuctionSnapshot | None = None
        self._last_location: AuctionLocation | None = None
        self._location_dwell = 0

    @property
    def registry(self) -> FeatureRegistry:
        return self._registry

    @property
    def history_size(self) -> int:
        return len(self._history)

    def update(self, snapshot: AuctionSnapshot) -> FeatureRow:
        """Validate and transform a snapshot without observing any future row."""

        if not isinstance(snapshot, AuctionSnapshot):
            raise TypeError("snapshot must be an AuctionSnapshot")
        reset = self._continuity_reset(snapshot)
        prior = [] if reset else list(self._history)
        previous = prior[-1] if prior else None
        dwell = (
            1 if reset or self._last_location is not snapshot.location else self._location_dwell + 1
        )
        values = _feature_values(snapshot, previous, prior, location_dwell=dwell)
        row = FeatureRow(
            timestamp=snapshot.timestamp,
            information_cutoff=snapshot.timestamp + timeframe_duration(snapshot.timeframe),
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            segment_id=snapshot.segment_id,
            dataset_version=snapshot.dataset_version,
            config_version=snapshot.config_version,
            profile_version=snapshot.profile_definition_id,
            window_policy_id=snapshot.window_version,
            feature_set_id=self._registry.feature_set_id,
            registry_id=self._registry.registry_id,
            values=values,
        )
        self._registry.validate_row(row)

        if reset:
            self._history.clear()
        self._history.append(snapshot)
        self._latest = snapshot
        self._last_location = snapshot.location
        self._location_dwell = dwell
        return row

    def build(self, snapshots: Iterable[AuctionSnapshot]) -> tuple[FeatureRow, ...]:
        return tuple(self.update(snapshot) for snapshot in snapshots)

    def reset(self) -> None:
        self._history.clear()
        self._latest = None
        self._last_location = None
        self._location_dwell = 0

    def _continuity_reset(self, snapshot: AuctionSnapshot) -> bool:
        previous = self._latest
        if previous is None:
            return True
        if snapshot.symbol != previous.symbol:
            raise ValueError("feature stream symbol changed")
        if snapshot.timeframe != previous.timeframe:
            raise ValueError("feature stream timeframe changed")
        for field in (
            "dataset_version",
            "config_version",
            "profile_definition_id",
            "window_version",
        ):
            if getattr(snapshot, field) != getattr(previous, field):
                raise ValueError(f"feature stream {field} changed")
        if snapshot.timestamp == previous.timestamp:
            raise ValueError("duplicate feature snapshot timestamp")
        if snapshot.timestamp < previous.timestamp:
            raise ValueError("out-of-order feature snapshot timestamp")

        reset_event = any(event.kind in _RESET_EVENTS for event in snapshot.events)
        segment_changed = snapshot.segment_id != previous.segment_id
        expected = timeframe_duration(snapshot.timeframe)
        gap = snapshot.timestamp - previous.timestamp != expected
        if gap and not (reset_event or segment_changed):
            raise ValueError("unexplained material gap in feature stream")
        return reset_event or segment_changed


def _feature_values(
    current: AuctionSnapshot,
    previous: AuctionSnapshot | None,
    prior: Sequence[AuctionSnapshot],
    *,
    location_dwell: int,
) -> dict[str, FeatureValue]:
    candle = current.latest_candle
    profile = current.profile
    poc = profile.point_of_control
    val = profile.value_area_low
    vah = profile.value_area_high
    previous_candle = None if previous is None else previous.latest_candle
    previous_profile = None if previous is None else previous.profile
    previous_poc = None if previous_profile is None else previous_profile.point_of_control
    previous_val = None if previous_profile is None else previous_profile.value_area_low
    previous_vah = None if previous_profile is None else previous_profile.value_area_high

    snapshots_20 = (*prior, current)[-20:]
    snapshots_21 = (*prior, current)[-21:]
    returns_20 = _trailing_returns(snapshots_21) if len(snapshots_21) == 21 else None
    realized = _realized_volatility(returns_20)
    current_return = _log_ratio(
        candle.close,
        None if previous_candle is None else previous_candle.close,
    )
    price_range = candle.high - candle.low

    values: dict[str, FeatureValue] = {
        "auction_location": current.location.value,
        "poc_distance_close": _distance(candle.close, poc),
        "poc_velocity_close_1": _change_over_previous_close(
            poc,
            previous_poc,
            None if previous_candle is None else previous_candle.close,
        ),
        "value_width_close": _ratio(
            None if val is None or vah is None else vah - val,
            candle.close,
        ),
        "value_midpoint_velocity_close_1": _change_over_previous_close(
            _midpoint(val, vah),
            _midpoint(previous_val, previous_vah),
            None if previous_candle is None else previous_candle.close,
        ),
        "poc_volume_share": _ratio(
            None if profile.poc_index is None else profile.bin_volumes.get(profile.poc_index),
            profile.total_volume,
        ),
        "vwap_distance_close": _distance(candle.close, profile.vwap),
        "vwap_slope_close_1": _change_over_previous_close(
            profile.vwap,
            None if previous_profile is None else previous_profile.vwap,
            None if previous_candle is None else previous_candle.close,
        ),
        "close_value_position": _value_position(candle.close, val, vah),
        "value_area_jaccard_1": _value_area_jaccard(current, previous),
        "nearest_hvn_distance_close": _nearest_node_distance(
            candle.close, current.nodes, NodeKind.HVN
        ),
        "nearest_lvn_distance_close": _nearest_node_distance(
            candle.close, current.nodes, NodeKind.LVN
        ),
        "max_node_persistence_bars": max((node.persistence for node in current.nodes), default=0),
        "inside_value_rate_20": _inside_value_rate(snapshots_20),
        "value_reentry_rate_20": _value_reentry_rate(snapshots_20),
        "location_dwell_bars": location_dwell,
        "log_return_1": current_return,
        "range_close_fraction": _ratio(price_range, candle.close),
        "body_range_ratio": _ratio(candle.close - candle.open, price_range),
        "upper_wick_range_ratio": _ratio(candle.high - max(candle.open, candle.close), price_range),
        "lower_wick_range_ratio": _ratio(min(candle.open, candle.close) - candle.low, price_range),
        "log_volume_ratio_1": _log_ratio(
            candle.volume,
            None if previous_candle is None else previous_candle.volume,
        ),
        "realized_volatility_20": realized,
        "volatility_normalized_return_20": _ratio(current_return, realized),
        "return_autocorrelation_1_20": _lag_one_correlation(returns_20),
        "volume_relative_median_20": _volume_relative_median(snapshots_20),
    }
    return values


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator / denominator)


def _distance(close: float, reference: float | None) -> float | None:
    if reference is None:
        return None
    return _ratio(close - reference, close)


def _change_over_previous_close(
    current: float | None,
    previous: float | None,
    previous_close: float | None,
) -> float | None:
    if current is None or previous is None:
        return None
    return _ratio(current - previous, previous_close)


def _midpoint(low: float | None, high: float | None) -> float | None:
    if low is None or high is None:
        return None
    return (low + high) / 2.0


def _value_position(close: float, low: float | None, high: float | None) -> float | None:
    if low is None or high is None:
        return None
    return _ratio(close - low, high - low)


def _value_area_jaccard(current: AuctionSnapshot, previous: AuctionSnapshot | None) -> float | None:
    if previous is None:
        return None
    current_low = current.profile.value_area_low_index
    current_high = current.profile.value_area_high_index
    previous_low = previous.profile.value_area_low_index
    previous_high = previous.profile.value_area_high_index
    if None in (current_low, current_high, previous_low, previous_high):
        return None
    assert current_low is not None
    assert current_high is not None
    assert previous_low is not None
    assert previous_high is not None
    intersection = max(0, min(current_high, previous_high) - max(current_low, previous_low) + 1)
    union = (current_high - current_low + 1) + (previous_high - previous_low + 1) - intersection
    return float(intersection / union)


def _nearest_node_distance(
    close: float, nodes: Sequence[ProfileNode], kind: NodeKind
) -> float | None:
    if close == 0:
        return None
    eligible = [node for node in nodes if node.kind is kind]
    if not eligible:
        return None
    node = min(
        eligible,
        key=lambda item: (
            abs(close - item.price),
            item.representative_index if item.representative_index is not None else 0,
            item.price,
        ),
    )
    return float((close - node.price) / close)


def _inside_value_rate(snapshots: Sequence[AuctionSnapshot]) -> float | None:
    if len(snapshots) != 20 or any(
        snapshot.location is AuctionLocation.NO_VALUE for snapshot in snapshots
    ):
        return None
    return sum(snapshot.location in _INSIDE_VALUE for snapshot in snapshots) / 20.0


def _value_reentry_rate(snapshots: Sequence[AuctionSnapshot]) -> float | None:
    if len(snapshots) != 20:
        return None
    count = sum(
        event.kind is StructuralEventKind.VALUE_REENTRY
        for snapshot in snapshots
        for event in snapshot.events
    )
    return count / 20.0


def _log_ratio(current: float, previous: float | None) -> float | None:
    if previous is None or current <= 0 or previous <= 0:
        return None
    return float(log(current / previous))


def _trailing_returns(snapshots: Sequence[AuctionSnapshot]) -> tuple[float, ...] | None:
    returns: list[float] = []
    for previous, current in zip(snapshots, snapshots[1:]):
        value = _log_ratio(current.latest_candle.close, previous.latest_candle.close)
        if value is None:
            return None
        returns.append(value)
    return tuple(returns)


def _realized_volatility(returns: Sequence[float] | None) -> float | None:
    if returns is None or len(returns) != 20:
        return None
    return float(sqrt(fsum(value * value for value in returns) / 20.0))


def _lag_one_correlation(returns: Sequence[float] | None) -> float | None:
    if returns is None or len(returns) != 20:
        return None
    left = returns[:-1]
    right = returns[1:]
    left_mean = fsum(left) / len(left)
    right_mean = fsum(right) / len(right)
    left_squared = fsum((value - left_mean) ** 2 for value in left)
    right_squared = fsum((value - right_mean) ** 2 for value in right)
    if left_squared == 0 or right_squared == 0:
        return None
    covariance = fsum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    return float(covariance / sqrt(left_squared * right_squared))


def _volume_relative_median(snapshots: Sequence[AuctionSnapshot]) -> float | None:
    if len(snapshots) != 20:
        return None
    baseline = float(median(snapshot.latest_candle.volume for snapshot in snapshots))
    ratio = _ratio(snapshots[-1].latest_candle.volume, baseline)
    return None if ratio is None else ratio - 1.0
