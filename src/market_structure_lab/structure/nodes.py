"""Deterministic profile-node zones and cross-snapshot persistence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from math import fsum, isfinite

from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.profiles.volume import VolumeProfile


class NodeKind(StrEnum):
    HVN = "hvn"
    LVN = "lvn"


@dataclass(frozen=True)
class ProfileNode:
    """A contiguous high- or low-volume zone.

    The first four fields retain the original point-node API. Integer zone
    fields are populated when detection consumes a :class:`ProfileSnapshot`.
    """

    kind: NodeKind
    price: float
    volume: float
    prominence: float
    start_index: int | None = None
    end_index: int | None = None
    representative_index: int | None = None
    price_low: float | None = None
    price_high: float | None = None
    persistence: int = 1
    binning_id: str | None = None

    def __post_init__(self) -> None:
        indices = (self.start_index, self.end_index, self.representative_index)
        if any(index is None for index in indices) and not all(index is None for index in indices):
            raise ValueError("node zone indices must either all be set or all be omitted")
        if self.start_index is not None:
            if self.end_index is None or self.representative_index is None:
                raise ValueError("complete node zone indices are required")
            if self.end_index < self.start_index:
                raise ValueError("node zone end_index must not precede start_index")
            if not self.start_index <= self.representative_index <= self.end_index:
                raise ValueError("representative_index must be inside the node zone")
        if self.persistence < 1:
            raise ValueError("persistence must be at least 1")

    @property
    def width_bins(self) -> int:
        if self.start_index is None or self.end_index is None:
            return 1
        return self.end_index - self.start_index + 1


class NodePersistenceTracker:
    """Track consecutive overlap of same-kind node zones.

    Persistence is deliberately stateful and must be reset at stream, segment,
    or window boundaries where continuity is not valid.
    """

    def __init__(self) -> None:
        self._previous: tuple[ProfileNode, ...] = ()

    def transaction_checkpoint(self) -> tuple[ProfileNode, ...]:
        """Capture the immutable persistence tuple in constant time."""

        return self._previous

    def restore_transaction(self, checkpoint: tuple[ProfileNode, ...]) -> None:
        """Restore an earlier persistence tuple without replacing this tracker."""

        self._previous = checkpoint

    def update(self, nodes: Sequence[ProfileNode]) -> tuple[ProfileNode, ...]:
        persisted = tuple(self._with_persistence(node) for node in nodes)
        self._previous = persisted
        return persisted

    def reset(self) -> None:
        self._previous = ()

    def _with_persistence(self, node: ProfileNode) -> ProfileNode:
        prior = [
            candidate.persistence
            for candidate in self._previous
            if candidate.kind is node.kind and _zones_overlap(candidate, node)
        ]
        return replace(node, persistence=max(prior, default=0) + 1)


@dataclass(frozen=True)
class _ProfileBins:
    volumes: Mapping[int, float]
    price_for_index: Callable[[int], float]
    plateau_zones: bool
    binning_id: str | None


def detect_profile_nodes(
    profile: ProfileSnapshot | VolumeProfile,
    *,
    min_prominence: float = 0.0,
    smoothing_radius: int = 0,
) -> list[ProfileNode]:
    """Detect local HVN/LVN zones without crossing absent integer bins.

    ``ProfileSnapshot`` inputs use their canonical integer bins and collapse a
    contiguous equal-volume plateau into one zone. The price-keyed
    ``VolumeProfile`` compatibility facade retains its original strict-point
    behavior, including ignoring flat ties.
    """
    if not isfinite(min_prominence) or min_prominence < 0:
        raise ValueError("min_prominence must be a finite number greater than or equal to 0")
    if isinstance(smoothing_radius, bool) or not isinstance(smoothing_radius, int):
        raise TypeError("smoothing_radius must be an integer")
    if smoothing_radius < 0:
        raise ValueError("smoothing_radius must be a non-negative integer")

    prepared = _profile_bins(profile)
    if any(not isfinite(volume) for volume in prepared.volumes.values()):
        raise ValueError("profile volumes must be finite")

    nodes: list[ProfileNode] = []
    for segment in _contiguous_segments(prepared.volumes):
        smoothed = _smooth_segment(segment, prepared.volumes, radius=smoothing_radius)
        nodes.extend(
            _segment_nodes(
                segment,
                smoothed,
                prepared,
                min_prominence=min_prominence,
            )
        )
    return nodes


def _profile_bins(profile: ProfileSnapshot | VolumeProfile) -> _ProfileBins:
    if isinstance(profile, ProfileSnapshot):
        return _ProfileBins(
            volumes=profile.bin_volumes,
            price_for_index=profile.binning.price_for_index,
            plateau_zones=True,
            binning_id=profile.binning_id,
        )

    prices = sorted(profile.price_volumes)
    volumes: dict[int, float] = {}
    compatibility_index = 0
    previous: float | None = None
    remapped_prices: dict[int, float] = {}
    for price in prices:
        if previous is not None:
            compatibility_index += 1 if _is_adjacent(price, previous, profile.tick_size) else 2
        volumes[compatibility_index] = profile.price_volumes[price]
        remapped_prices[compatibility_index] = price
        previous = price
    return _ProfileBins(
        volumes=volumes,
        price_for_index=remapped_prices.__getitem__,
        plateau_zones=False,
        binning_id=None,
    )


def _contiguous_segments(volumes: Mapping[int, float]) -> tuple[tuple[int, ...], ...]:
    segments: list[list[int]] = []
    for index in sorted(volumes):
        if not segments or index != segments[-1][-1] + 1:
            segments.append([index])
        else:
            segments[-1].append(index)
    return tuple(tuple(segment) for segment in segments)


def _smooth_segment(
    segment: tuple[int, ...], volumes: Mapping[int, float], *, radius: int
) -> dict[int, float]:
    if radius == 0:
        return {index: volumes[index] for index in segment}
    smoothed: dict[int, float] = {}
    for position, index in enumerate(segment):
        start = max(0, position - radius)
        end = min(len(segment), position + radius + 1)
        values = [volumes[neighbor] for neighbor in segment[start:end]]
        smoothed[index] = fsum(values) / len(values)
    return smoothed


def _segment_nodes(
    segment: tuple[int, ...],
    volumes: Mapping[int, float],
    profile: _ProfileBins,
    *,
    min_prominence: float,
) -> list[ProfileNode]:
    if len(segment) < 3:
        return []
    zones = _plateaus(segment, volumes) if profile.plateau_zones else tuple((i, i) for i in segment)
    nodes: list[ProfileNode] = []
    segment_start = segment[0]
    segment_end = segment[-1]
    for start, end in zones:
        if start == segment_start or end == segment_end:
            continue
        level = volumes[start]
        left = volumes[start - 1]
        right = volumes[end + 1]
        if level > left and level > right:
            kind = NodeKind.HVN
            prominence = level - max(left, right)
        elif level < left and level < right:
            kind = NodeKind.LVN
            prominence = min(left, right) - level
        else:
            continue
        if prominence < min_prominence:
            continue
        representative = (start + end) // 2
        representative_price = profile.price_for_index(representative)
        if profile.binning_id is None:
            nodes.append(
                ProfileNode(
                    kind=kind,
                    price=representative_price,
                    volume=level,
                    prominence=prominence,
                )
            )
        else:
            nodes.append(
                ProfileNode(
                    kind=kind,
                    price=representative_price,
                    volume=level,
                    prominence=prominence,
                    start_index=start,
                    end_index=end,
                    representative_index=representative,
                    price_low=profile.price_for_index(start),
                    price_high=profile.price_for_index(end),
                    binning_id=profile.binning_id,
                )
            )
    return nodes


def _plateaus(
    segment: tuple[int, ...], volumes: Mapping[int, float]
) -> tuple[tuple[int, int], ...]:
    zones: list[tuple[int, int]] = []
    start = segment[0]
    previous = start
    for index in segment[1:]:
        if volumes[index] != volumes[previous]:
            zones.append((start, previous))
            start = index
        previous = index
    zones.append((start, previous))
    return tuple(zones)


def _zones_overlap(previous: ProfileNode, current: ProfileNode) -> bool:
    if (
        previous.binning_id is not None
        and current.binning_id is not None
        and previous.binning_id != current.binning_id
    ):
        return False
    if previous.start_index is None or current.start_index is None:
        return previous.price == current.price
    if previous.end_index is None or current.end_index is None:
        return False
    return max(previous.start_index, current.start_index) <= min(
        previous.end_index, current.end_index
    )


def _is_adjacent(price: float, neighbor: float, tick_size: float) -> bool:
    if not isfinite(price) or not isfinite(neighbor) or not isfinite(tick_size) or tick_size <= 0:
        return False
    return abs(price - neighbor - tick_size) <= max(1e-12, abs(tick_size) * 1e-12)
