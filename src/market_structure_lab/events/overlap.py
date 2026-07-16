"""Deterministic half-open interval-overlap accounting for market events."""

from __future__ import annotations

import heapq
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping

from market_structure_lab.events.models import EventKind, MarketEvent


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


def _overlap_group(event: MarketEvent) -> tuple[object, ...]:
    return (
        event.dataset_version,
        event.feature_set_id,
        event.registry_id,
        event.symbol,
        event.timeframe,
        event.segment_id,
    )


def build_overlap_report(events: Iterable[MarketEvent]) -> EventOverlapReport:
    """Count overlaps within identical dataset, feature, stream, and segment identity."""

    ordered: list[tuple[int, MarketEvent]] = []
    seen_ids: set[str] = set()
    by_kind: Counter[str] = Counter()
    for index, event in enumerate(events):
        if not isinstance(event, MarketEvent):
            raise TypeError("events must contain only MarketEvent values")
        if event.event_id in seen_ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        seen_ids.add(event.event_id)
        by_kind[event.kind.value] += 1
        ordered.append((index, event))

    ordered.sort(
        key=lambda item: (
            _overlap_group(item[1]),
            item[1].start,
            item[1].end,
            item[1].event_id,
        )
    )

    pair_count = 0
    max_concurrency = 0
    by_kind_pair: Counter[str] = Counter()
    affected: set[int] = set()
    group: tuple[object, ...] | None = None
    active_heap: list[tuple[datetime, int]] = []
    active: dict[int, MarketEvent] = {}
    active_kinds: Counter[EventKind] = Counter()
    not_yet_affected: set[int] = set()

    for index, event in ordered:
        current_group = _overlap_group(event)
        if group != current_group:
            group = current_group
            active_heap.clear()
            active.clear()
            active_kinds.clear()
            not_yet_affected.clear()

        while active_heap and active_heap[0][0] <= event.start:
            _, expired_index = heapq.heappop(active_heap)
            expired = active.pop(expired_index)
            active_kinds[expired.kind] -= 1
            if active_kinds[expired.kind] == 0:
                del active_kinds[expired.kind]
            not_yet_affected.discard(expired_index)

        active_count = len(active)
        pair_count += active_count
        if active_count:
            affected.add(index)
            affected.update(not_yet_affected)
            not_yet_affected.clear()
            for active_kind, count in active_kinds.items():
                by_kind_pair[kind_pair_key(active_kind, event.kind)] += count
        else:
            not_yet_affected.add(index)

        active[index] = event
        active_kinds[event.kind] += 1
        heapq.heappush(active_heap, (event.end, index))
        max_concurrency = max(max_concurrency, len(active))

    total = len(ordered)
    affected_count = len(affected)
    return EventOverlapReport(
        total_events=total,
        by_kind=by_kind,
        interval_pair_count=pair_count,
        affected_event_count=affected_count,
        affected_event_ratio=affected_count / total if total else 0.0,
        max_concurrency=max_concurrency,
        by_kind_pair=by_kind_pair,
    )
