"""Bounded, causal construction of audited discovery feature rows."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import fsum, log, sqrt
from pathlib import Path
from statistics import median
from threading import RLock
from weakref import WeakKeyDictionary

from market_structure_lab.core.artifact_io import (
    iter_bounded_regular_lines,
    path_exists_no_follow,
    require_regular_directory,
)
from market_structure_lab.auction.engine import (
    AuctionLocation,
    AuctionSnapshot,
    StructuralEventKind,
)
from market_structure_lab.features.builtin import builtin_feature_registry
from market_structure_lab.features.models import FeatureRow, FeatureValue, timeframe_duration
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    FeatureRegistry,
)
from market_structure_lab.structure.nodes import NodeKind, ProfileNode

_HISTORY_LIMIT = 21
MAX_FEATURE_BUILD_ROWS = 10_000_000
MAX_FEATURE_BUILD_ROW_BYTES = 1024 * 1024
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


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class FeatureBuildBatch:
    """Immutable replay envelope emitted by :class:`FeatureBuilder`."""

    artifact_path: Path
    artifact_sha256: str
    content_sha256: str
    row_count: int
    builder_id: str
    builder_version: str
    feature_registry_sha256: str
    dependency_contract_sha256: str
    registry_id: str
    maximum_row_bytes: int

    def __post_init__(self) -> None:
        if self.row_count < 0 or self.row_count > MAX_FEATURE_BUILD_ROWS:
            raise ValueError("feature build batch row count exceeds the bounded limit")
        if not 1 <= self.maximum_row_bytes <= MAX_FEATURE_BUILD_ROW_BYTES:
            raise ValueError("feature build batch row-byte limit is invalid")
        for value, label in (
            (self.artifact_sha256, "artifact_sha256"),
            (self.content_sha256, "content_sha256"),
            (self.feature_registry_sha256, "feature_registry_sha256"),
            (self.dependency_contract_sha256, "dependency_contract_sha256"),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")

    def iter_rows(
        self,
        lease: FeatureBuildBatchLease | None = None,
    ) -> Iterator[FeatureRow]:
        """Replay and revalidate the exact bounded ordered producer output."""

        active_lease = FeatureBuildBatch.verify_issued(self) if lease is None else lease
        authenticated = _resolve_feature_build_batch_lease(self, active_lease)
        digest = hashlib.sha256()
        count = 0
        for record in iter_bounded_regular_lines(
            authenticated.artifact_path,
            maximum_lines=authenticated.row_count,
            maximum_line_bytes=authenticated.maximum_row_bytes,
        ):
            row = _feature_row_from_json(record)
            canonical = row.canonical_json().encode("utf-8")
            if canonical != record:
                raise ValueError("feature build batch contains a non-canonical row")
            digest.update(record)
            digest.update(b"\n")
            count += 1
            yield row
        output_sha256 = digest.hexdigest()
        if output_sha256 != authenticated.artifact_sha256:
            raise ValueError("feature build batch artifact checksum mismatch")
        if count != authenticated.row_count or output_sha256 != authenticated.content_sha256:
            raise ValueError("feature build batch output count or digest mismatch")

    @staticmethod
    def verify_issued(batch: object) -> FeatureBuildBatchLease:
        """Issue a detached nonce-only lease for an exact sealed-builder batch."""

        return _verify_feature_build_batch_issuance(batch)

    @staticmethod
    def resolve_lease(
        batch: object,
        lease: FeatureBuildBatchLease,
    ) -> FeatureBuildBatchMetadata:
        """Return a detached metadata copy resolved from the private canonical record."""

        return _resolve_feature_build_batch_lease(batch, lease)

    def close(self) -> None:
        """Revoke this batch instance without deleting its immutable replay artifact."""

        _revoke_feature_build_batch(self)


@dataclass(frozen=True, slots=True)
class FeatureBuildBatchMetadata:
    artifact_path: Path
    artifact_sha256: str
    content_sha256: str
    row_count: int
    builder_id: str
    builder_version: str
    feature_registry_sha256: str
    dependency_contract_sha256: str
    registry_id: str
    maximum_row_bytes: int


@dataclass(frozen=True, slots=True)
class FeatureBuildBatchLease:
    lease_id: str


@dataclass(frozen=True, slots=True)
class _IssuedFeatureBuildBatch:
    lease_id: str
    artifact_path: Path
    artifact_sha256: str
    content_sha256: str
    row_count: int
    builder_id: str
    builder_version: str
    feature_registry_sha256: str
    dependency_contract_sha256: str
    registry_id: str
    maximum_row_bytes: int


_ISSUED_FEATURE_BUILD_BATCHES: WeakKeyDictionary[
    FeatureBuildBatch,
    _IssuedFeatureBuildBatch,
] = WeakKeyDictionary()
_ISSUED_FEATURE_BUILD_BATCHES_LOCK = RLock()


def _feature_build_batch_record(batch: FeatureBuildBatch) -> _IssuedFeatureBuildBatch:
    return _IssuedFeatureBuildBatch(
        lease_id=secrets.token_hex(32),
        artifact_path=batch.artifact_path,
        artifact_sha256=batch.artifact_sha256,
        content_sha256=batch.content_sha256,
        row_count=batch.row_count,
        builder_id=batch.builder_id,
        builder_version=batch.builder_version,
        feature_registry_sha256=batch.feature_registry_sha256,
        dependency_contract_sha256=batch.dependency_contract_sha256,
        registry_id=batch.registry_id,
        maximum_row_bytes=batch.maximum_row_bytes,
    )


def _record_metadata(record: _IssuedFeatureBuildBatch) -> FeatureBuildBatchMetadata:
    return FeatureBuildBatchMetadata(
        artifact_path=record.artifact_path,
        artifact_sha256=record.artifact_sha256,
        content_sha256=record.content_sha256,
        row_count=record.row_count,
        builder_id=record.builder_id,
        builder_version=record.builder_version,
        feature_registry_sha256=record.feature_registry_sha256,
        dependency_contract_sha256=record.dependency_contract_sha256,
        registry_id=record.registry_id,
        maximum_row_bytes=record.maximum_row_bytes,
    )


def _batch_matches_record(batch: FeatureBuildBatch, record: _IssuedFeatureBuildBatch) -> bool:
    return FeatureBuildBatchMetadata(
        artifact_path=batch.artifact_path,
        artifact_sha256=batch.artifact_sha256,
        content_sha256=batch.content_sha256,
        row_count=batch.row_count,
        builder_id=batch.builder_id,
        builder_version=batch.builder_version,
        feature_registry_sha256=batch.feature_registry_sha256,
        dependency_contract_sha256=batch.dependency_contract_sha256,
        registry_id=batch.registry_id,
        maximum_row_bytes=batch.maximum_row_bytes,
    ) == _record_metadata(record)


def _issue_feature_build_batch(batch: FeatureBuildBatch) -> FeatureBuildBatch:
    if type(batch) is not FeatureBuildBatch:
        raise TypeError("only exact FeatureBuildBatch instances can be issued")
    with _ISSUED_FEATURE_BUILD_BATCHES_LOCK:
        _ISSUED_FEATURE_BUILD_BATCHES[batch] = _feature_build_batch_record(batch)
    return batch


def _verify_feature_build_batch_issuance(batch: object) -> FeatureBuildBatchLease:
    if type(batch) is not FeatureBuildBatch:
        raise TypeError("feature publication requires an exact issued FeatureBuildBatch instance")
    with _ISSUED_FEATURE_BUILD_BATCHES_LOCK:
        expected = _ISSUED_FEATURE_BUILD_BATCHES.get(batch)
        if expected is None:
            raise ValueError(
                "feature publication requires an exact issued FeatureBuildBatch instance"
            )
        if not _batch_matches_record(batch, expected):
            raise ValueError("issued FeatureBuildBatch metadata changed after construction")
        return FeatureBuildBatchLease(expected.lease_id)


def _resolve_feature_build_batch_lease(
    batch: object,
    lease: FeatureBuildBatchLease,
) -> FeatureBuildBatchMetadata:
    if type(batch) is not FeatureBuildBatch:
        raise TypeError("feature publication requires an exact issued FeatureBuildBatch instance")
    if type(lease) is not FeatureBuildBatchLease:
        raise TypeError("feature replay requires an exact authenticated batch lease")
    with _ISSUED_FEATURE_BUILD_BATCHES_LOCK:
        expected = _ISSUED_FEATURE_BUILD_BATCHES.get(batch)
        if expected is None or not secrets.compare_digest(expected.lease_id, lease.lease_id):
            raise ValueError("feature replay lease is no longer issued for this FeatureBuildBatch")
        return _record_metadata(expected)


def _revoke_feature_build_batch(batch: FeatureBuildBatch) -> None:
    if type(batch) is not FeatureBuildBatch:
        raise TypeError("only exact FeatureBuildBatch instances can be revoked")
    with _ISSUED_FEATURE_BUILD_BATCHES_LOCK:
        _ISSUED_FEATURE_BUILD_BATCHES.pop(batch, None)


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
    def builder_id(self) -> str:
        return BUILTIN_FEATURE_BUILDER_ID

    @property
    def builder_version(self) -> str:
        return BUILTIN_FEATURE_BUILDER_VERSION

    @property
    def history_size(self) -> int:
        return len(self._history)

    def update(self, snapshot: AuctionSnapshot) -> FeatureRow:
        """Validate and transform a snapshot without observing any future row."""

        return _update_feature_builder(self, snapshot)

    def build(self, snapshots: Iterable[AuctionSnapshot]) -> tuple[FeatureRow, ...]:
        return tuple(self.update(snapshot) for snapshot in snapshots)

    def build_batch(
        self,
        snapshots: Iterable[AuctionSnapshot],
        *,
        artifact_path: str | Path,
        maximum_rows: int,
    ) -> FeatureBuildBatch:
        """Calculate and spool an immutable bounded output batch for publication."""

        if (
            isinstance(maximum_rows, bool)
            or not isinstance(maximum_rows, int)
            or not 0 <= maximum_rows <= MAX_FEATURE_BUILD_ROWS
        ):
            raise ValueError("maximum_rows must be within the bounded feature batch limit")
        destination = Path(artifact_path)
        if ".." in destination.parts:
            raise ValueError("feature batch artifact path cannot traverse parents")
        if destination.anchor and not destination.is_absolute():
            raise ValueError("feature batch artifact path cannot be drive-relative")
        if not destination.is_absolute():
            destination = Path.cwd() / destination
        require_regular_directory(destination.parent)
        temporary = destination.with_name(f".{destination.name}.tmp")
        if path_exists_no_follow(destination) or path_exists_no_follow(temporary):
            raise FileExistsError("feature batch artifact already exists")

        digest = hashlib.sha256()
        count = 0
        active_stream: tuple[str, str] | None = None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for snapshot in snapshots:
                    if not isinstance(snapshot, AuctionSnapshot):
                        raise TypeError("snapshot must be an AuctionSnapshot")
                    stream = (snapshot.symbol, snapshot.timeframe)
                    if active_stream is not None and stream != active_stream:
                        _reset_feature_builder(self)
                    active_stream = stream
                    if count >= maximum_rows:
                        raise ValueError("feature build batch exceeds maximum_rows")
                    row = _update_feature_builder(self, snapshot)
                    record = row.canonical_json().encode("utf-8")
                    if len(record) > MAX_FEATURE_BUILD_ROW_BYTES:
                        raise ValueError("feature build row exceeds the byte limit")
                    handle.write(record)
                    handle.write(b"\n")
                    digest.update(record)
                    digest.update(b"\n")
                    count += 1
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        content_sha256 = digest.hexdigest()
        return _issue_feature_build_batch(
            FeatureBuildBatch(
                artifact_path=destination,
                artifact_sha256=content_sha256,
                content_sha256=content_sha256,
                row_count=count,
                builder_id=BUILTIN_FEATURE_BUILDER_ID,
                builder_version=BUILTIN_FEATURE_BUILDER_VERSION,
                feature_registry_sha256=self._registry.sha256,
                dependency_contract_sha256=self._registry.dependency_contract_sha256,
                registry_id=self._registry.registry_id,
                maximum_row_bytes=MAX_FEATURE_BUILD_ROW_BYTES,
            )
        )

    def reset(self) -> None:
        _reset_feature_builder(self)


def _reset_feature_builder(builder: FeatureBuilder) -> None:
    builder._history.clear()
    builder._latest = None
    builder._last_location = None
    builder._location_dwell = 0


def _update_feature_builder(
    builder: FeatureBuilder,
    snapshot: AuctionSnapshot,
) -> FeatureRow:
    if not isinstance(snapshot, AuctionSnapshot):
        raise TypeError("snapshot must be an AuctionSnapshot")
    reset = _feature_continuity_reset(builder, snapshot)
    prior = [] if reset else list(builder._history)
    previous = prior[-1] if prior else None
    dwell = (
        1
        if reset or builder._last_location is not snapshot.location
        else builder._location_dwell + 1
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
        feature_set_id=builder._registry.feature_set_id,
        registry_id=builder._registry.registry_id,
        values=values,
    )
    builder._registry.validate_row(row)

    if reset:
        builder._history.clear()
    builder._history.append(snapshot)
    builder._latest = snapshot
    builder._last_location = snapshot.location
    builder._location_dwell = dwell
    return row


def _feature_continuity_reset(
    builder: FeatureBuilder,
    snapshot: AuctionSnapshot,
) -> bool:
    previous = builder._latest
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


def _feature_row_from_json(record: bytes) -> FeatureRow:
    try:
        payload = json.loads(record)
        values = payload.pop("values")
        for field_name in ("timestamp", "information_cutoff"):
            payload[field_name] = datetime.fromisoformat(
                str(payload[field_name]).replace("Z", "+00:00")
            )
        return FeatureRow(**payload, values=values)
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid feature row in producer batch") from error


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
