"""Canonical serialization for deterministic auction replay evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from market_structure_lab.auction.engine import AuctionSnapshot


def snapshot_to_dict(snapshot: AuctionSnapshot) -> dict[str, Any]:
    """Return a stable, JSON-compatible representation of one frozen snapshot."""
    profile = snapshot.profile
    migration = snapshot.migration
    return {
        "active_timestamps": [_timestamp(value) for value in snapshot.active_timestamps],
        "candle_count": snapshot.candle_count,
        "config_version": snapshot.config_version,
        "dataset_version": snapshot.dataset_version,
        "events": [
            {
                "event_id": event.event_id,
                "kind": event.kind.value,
                "payload": [list(item) for item in event.payload],
                "timestamp": _timestamp(event.timestamp),
            }
            for event in snapshot.events
        ],
        "latest_candle": {
            "close": snapshot.latest_candle.close,
            "high": snapshot.latest_candle.high,
            "low": snapshot.latest_candle.low,
            "open": snapshot.latest_candle.open,
            "segment_id": snapshot.latest_candle.segment_id,
            "timestamp": _timestamp(snapshot.latest_candle.timestamp),
            "volume": snapshot.latest_candle.volume,
        },
        "location": snapshot.location.value,
        "migration": (
            None
            if migration is None
            else {
                "direction": migration.direction.value,
                "point_of_control_change": migration.point_of_control_change,
                "value_area_midpoint_change": migration.value_area_midpoint_change,
                "value_area_overlap": migration.value_area_overlap,
            }
        ),
        "nodes": [
            {
                "binning_id": node.binning_id,
                "end_index": node.end_index,
                "kind": node.kind.value,
                "persistence": node.persistence,
                "price": node.price,
                "price_high": node.price_high,
                "price_low": node.price_low,
                "prominence": node.prominence,
                "representative_index": node.representative_index,
                "start_index": node.start_index,
                "volume": node.volume,
                "width_bins": node.width_bins,
            }
            for node in snapshot.nodes
        ],
        "profile": {
            "allocation_id": profile.allocation_id,
            "bin_volumes": [
                [index, volume] for index, volume in sorted(profile.bin_volumes.items())
            ],
            "binning_id": profile.binning_id,
            "poc_index": profile.poc_index,
            "total_volume": profile.total_volume,
            "value_area_fraction": profile.value_area_fraction,
            "value_area_high_index": profile.value_area_high_index,
            "value_area_low_index": profile.value_area_low_index,
            "vwap": profile.vwap,
            "work_budget_id": profile.work_budget_id,
        },
        "profile_definition_id": snapshot.profile_definition_id,
        "segment_id": snapshot.segment_id,
        "symbol": snapshot.symbol,
        "timeframe": snapshot.timeframe,
        "timestamp": _timestamp(snapshot.timestamp),
        "window_id": snapshot.window_id,
        "window_version": snapshot.window_version,
    }


def canonical_snapshot_json(snapshot: AuctionSnapshot) -> str:
    """Serialize one snapshot without platform-dependent whitespace or key order."""
    return json.dumps(
        snapshot_to_dict(snapshot),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def snapshot_stream_sha256(snapshots: Iterable[AuctionSnapshot]) -> str:
    """Hash an ordered snapshot stream with unambiguous record boundaries."""
    digest = hashlib.sha256()
    for snapshot in snapshots:
        payload = canonical_snapshot_json(snapshot).encode("utf-8")
        digest.update(len(payload).to_bytes(8, byteorder="big", signed=False))
        digest.update(payload)
    return digest.hexdigest()


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
