"""Outcome-blind event representations and deterministic segmentation."""

from market_structure_lab.events.fixed_windows import (
    UTCSessionUnit,
    segment_fixed_windows,
    segment_rolling_windows,
    segment_utc_sessions,
)
from market_structure_lab.events.models import (
    EventKind,
    JsonScalar,
    MarketEvent,
    make_event,
)
from market_structure_lab.events.overlap import (
    EventOverlapReport,
    build_overlap_report,
    kind_pair_key,
)

__all__ = [
    "EventKind",
    "EventOverlapReport",
    "JsonScalar",
    "MarketEvent",
    "UTCSessionUnit",
    "build_overlap_report",
    "kind_pair_key",
    "make_event",
    "segment_fixed_windows",
    "segment_rolling_windows",
    "segment_utc_sessions",
]
