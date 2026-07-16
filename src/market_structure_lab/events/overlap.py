"""Deterministic half-open interval-overlap accounting for market events."""

from __future__ import annotations

import heapq
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping, TypeAlias

from market_structure_lab.events.models import EventKind, MarketEvent

OverlapGroup: TypeAlias = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
]
OverlapOrderKey: TypeAlias = tuple[OverlapGroup, datetime, datetime, str]


def kind_pair_key(first: EventKind, second: EventKind) -> str:
    """Return the canonical key for an unordered pair of event kinds."""

    left, right = sorted((first.value, second.value))
    return f"{left}|{right}"


@dataclass(frozen=True, slots=True)
class EventOverlapReport:
    """Outcome-free aggregate overlap diagnostics."""

    total_events: int
    by_kind: Mapping[str, int]
    interval_pair_count: int
    affected_event_count: int
    affected_event_ratio: float
    max_concurrency: int
    max_active_events: int
    by_kind_pair: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.total_events < 0 or self.interval_pair_count < 0:
            raise ValueError("overlap counts must be non-negative")
        if not 0 <= self.affected_event_count <= self.total_events:
            raise ValueError("affected_event_count must be within total_events")
        if not 0.0 <= self.affected_event_ratio <= 1.0:
            raise ValueError("affected_event_ratio must be between zero and one")
        if self.max_concurrency < 0:
            raise ValueError("max_concurrency must be non-negative")
        if self.max_active_events < 0:
            raise ValueError("max_active_events must be non-negative")
        if self.max_active_events != self.max_concurrency:
            raise ValueError("max_active_events must equal max_concurrency")
        object.__setattr__(
            self,
            "by_kind",
            MappingProxyType(dict(sorted(self.by_kind.items()))),
        )
        object.__setattr__(
            self,
            "by_kind_pair",
            MappingProxyType(dict(sorted(self.by_kind_pair.items()))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_events": self.total_events,
            "by_kind": dict(self.by_kind),
            "interval_pair_count": self.interval_pair_count,
            "affected_event_count": self.affected_event_count,
            "affected_event_ratio": self.affected_event_ratio,
            "max_concurrency": self.max_concurrency,
            "max_active_events": self.max_active_events,
            "by_kind_pair": dict(self.by_kind_pair),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _overlap_group(event: MarketEvent) -> OverlapGroup:
    return (
        event.dataset_version,
        event.config_version,
        event.profile_version,
        event.window_policy_id,
        event.feature_set_id,
        event.registry_id,
        event.registry_sha256,
        event.symbol,
        event.timeframe,
        event.segment_id,
    )


def build_overlap_report(events: Iterable[MarketEvent]) -> EventOverlapReport:
    """Count overlaps from events in canonical group and interval order.

    The sweep retains only intervals that overlap a future event. Rejecting
    unordered input keeps memory proportional to maximum concurrency instead
    of the total event count.
    """

    by_kind: Counter[str] = Counter()
    pair_count = 0
    max_concurrency = 0
    by_kind_pair: Counter[str] = Counter()
    affected_count = 0
    total = 0
    group: OverlapGroup | None = None
    active_heap: list[tuple[datetime, int]] = []
    active: dict[int, EventKind] = {}
    active_kinds: Counter[EventKind] = Counter()
    unaffected_active: set[int] = set()
    previous_order_key: OverlapOrderKey | None = None

    for index, event in enumerate(events):
        if not isinstance(event, MarketEvent):
            raise TypeError("events must contain only MarketEvent values")
        current_group = _overlap_group(event)
        order_key = (current_group, event.start, event.end, event.event_id)
        if previous_order_key is not None:
            if order_key == previous_order_key:
                raise ValueError(f"duplicate event_id: {event.event_id}")
            if order_key < previous_order_key:
                raise ValueError("events must use canonical overlap order")
        previous_order_key = order_key
        total += 1
        by_kind[event.kind.value] += 1

        if group != current_group:
            group = current_group
            active_heap.clear()
            active.clear()
            active_kinds.clear()
            unaffected_active.clear()

        while active_heap and active_heap[0][0] <= event.start:
            _, expired_index = heapq.heappop(active_heap)
            expired_kind = active.pop(expired_index)
            active_kinds[expired_kind] -= 1
            if active_kinds[expired_kind] == 0:
                del active_kinds[expired_kind]
            unaffected_active.discard(expired_index)

        active_count = len(active)
        pair_count += active_count
        if active_count:
            affected_count += 1 + len(unaffected_active)
            unaffected_active.clear()
            for active_kind, count in active_kinds.items():
                by_kind_pair[kind_pair_key(active_kind, event.kind)] += count
        else:
            unaffected_active.add(index)

        active[index] = event.kind
        active_kinds[event.kind] += 1
        heapq.heappush(active_heap, (event.end, index))
        max_concurrency = max(max_concurrency, len(active))

    return EventOverlapReport(
        total_events=total,
        by_kind=by_kind,
        interval_pair_count=pair_count,
        affected_event_count=affected_count,
        affected_event_ratio=affected_count / total if total else 0.0,
        max_concurrency=max_concurrency,
        max_active_events=max_concurrency,
        by_kind_pair=by_kind_pair,
    )
