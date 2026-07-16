"""Causal event projection from auction structure and prior node zones."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from market_structure_lab.auction.engine import (
    AuctionSnapshot,
    StructuralEvent,
    StructuralEventKind,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry
from market_structure_lab.structure.nodes import ProfileNode

from .change_points import observation_resets_continuity, validate_observation_pair
from .models import EventKind, MarketEvent, make_event

_LIFTED_KINDS = {
    StructuralEventKind.VALUE_BREAKOUT: EventKind.VALUE_EXIT,
    StructuralEventKind.VALUE_REENTRY: EventKind.VALUE_REENTRY,
    StructuralEventKind.POC_MIGRATION: EventKind.POC_MIGRATION,
}
_RESET_KINDS = frozenset(
    {
        StructuralEventKind.GAP_RESET,
        StructuralEventKind.SEGMENT_RESET,
        StructuralEventKind.WINDOW_RESET,
    }
)


@dataclass(frozen=True, slots=True)
class StructuralEventConfig:
    """Version identities for projected Phase 2 and prior-node triggers."""

    phase2_trigger_version: str = "phase2-structural-lift-v1"
    node_trigger_version: str = "prior-node-zone-v1"

    def __post_init__(self) -> None:
        if not self.phase2_trigger_version.strip():
            raise ValueError("phase2_trigger_version must be non-empty")
        if not self.node_trigger_version.strip():
            raise ValueError("node_trigger_version must be non-empty")


class StructuralEventDetector:
    """Project structural events without consulting current or future node zones."""

    def __init__(
        self, config: StructuralEventConfig | None = None, *, registry: FeatureRegistry
    ) -> None:
        self.config = StructuralEventConfig() if config is None else config
        if not isinstance(self.config, StructuralEventConfig):
            raise TypeError("config must be a StructuralEventConfig")
        if not isinstance(registry, FeatureRegistry):
            raise TypeError("registry must be a FeatureRegistry")
        self.registry = registry
        self._latest_snapshot: AuctionSnapshot | None = None
        self._latest_row: FeatureRow | None = None

    def update(
        self, snapshot: AuctionSnapshot, row: FeatureRow
    ) -> tuple[MarketEvent, ...]:
        validate_observation_pair(snapshot, row)
        self.registry.validate_row(row)
        reset = observation_resets_continuity(
            self._latest_snapshot,
            snapshot,
            previous_row=self._latest_row,
            current_row=row,
        )
        previous_snapshot = None if reset else self._latest_snapshot
        previous_row = None if reset else self._latest_row

        projected = list(self._lift_phase2_events(snapshot, row))
        if previous_snapshot is not None and previous_row is not None:
            projected.extend(self._node_events(previous_snapshot, snapshot, previous_row, row))

        self._latest_snapshot = snapshot
        self._latest_row = row
        return tuple(projected)

    def reset(self) -> None:
        self._latest_snapshot = None
        self._latest_row = None

    def _lift_phase2_events(
        self, snapshot: AuctionSnapshot, row: FeatureRow
    ) -> tuple[MarketEvent, ...]:
        events: list[MarketEvent] = []
        for structural in snapshot.events:
            if structural.kind in _RESET_KINDS:
                continue
            kind = _LIFTED_KINDS.get(structural.kind)
            if kind is None:
                continue
            _validate_structural_timestamp(structural, snapshot)
            events.append(
                make_event(
                    kind,
                    row.timestamp,
                    row.information_cutoff,
                    row,
                    self.config.phase2_trigger_version,
                    registry=self.registry,
                    metadata={
                        "phase2_event_id": structural.event_id,
                        "phase2_kind": structural.kind.value,
                        "source_candle_open_timestamp": _iso_utc(structural.timestamp),
                        "observable_trigger_timestamp": _iso_utc(row.information_cutoff),
                        "phase2_payload_json": json.dumps(
                            dict(structural.payload),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                )
            )
        return tuple(events)

    def _node_events(
        self,
        previous: AuctionSnapshot,
        current: AuctionSnapshot,
        previous_row: FeatureRow,
        current_row: FeatureRow,
    ) -> tuple[MarketEvent, ...]:
        previous_close = previous.latest_candle.close
        current_close = current.latest_candle.close
        start = previous_row.information_cutoff
        events: list[MarketEvent] = []
        for position, node in enumerate(previous.nodes):
            low, high = _node_zone(node)
            kind = _node_event_kind(previous_close, current_close, low, high)
            if kind is None:
                continue
            events.append(
                make_event(
                    kind,
                    start,
                    current_row.information_cutoff,
                    current_row,
                    self.config.node_trigger_version,
                    registry=self.registry,
                    metadata={
                        "node_source": "prior_snapshot",
                        "prior_snapshot_timestamp": _iso_utc(previous.timestamp),
                        "prior_node_position": position,
                        "prior_node_kind": node.kind.value,
                        "prior_node_low": low,
                        "prior_node_high": high,
                        "prior_node_binning_id": node.binning_id,
                        "previous_close": previous_close,
                        "current_close": current_close,
                    },
                )
            )
        return tuple(events)


def _node_event_kind(
    previous_close: float, current_close: float, low: float, high: float
) -> EventKind | None:
    traversed = (previous_close < low and current_close > high) or (
        previous_close > high and current_close < low
    )
    if traversed:
        return EventKind.NODE_TRAVERSAL
    previous_inside = low <= previous_close <= high
    current_inside = low <= current_close <= high
    if not previous_inside and current_inside:
        return EventKind.NODE_TEST
    return None


def _node_zone(node: ProfileNode) -> tuple[float, float]:
    low = node.price if node.price_low is None else node.price_low
    high = node.price if node.price_high is None else node.price_high
    if high < low:
        raise ValueError("prior node zone high must not precede low")
    return float(low), float(high)


def _validate_structural_timestamp(
    event: StructuralEvent, snapshot: AuctionSnapshot
) -> None:
    if event.timestamp != snapshot.timestamp:
        raise ValueError("Phase 2 structural event timestamp must match its snapshot")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
