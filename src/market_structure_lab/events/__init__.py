"""Outcome-blind event representations and deterministic segmentation."""

from market_structure_lab.events.change_points import (
    CausalChangePointDetector,
    ChangePointConfig,
    ExpansionConfig,
    ExpansionDetector,
    observation_resets_continuity,
    validate_observation_pair,
)
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
from market_structure_lab.events.structural_events import (
    StructuralEventConfig,
    StructuralEventDetector,
)

__all__ = [
    "CausalChangePointDetector",
    "ChangePointConfig",
    "EventKind",
    "EventOverlapReport",
    "ExpansionConfig",
    "ExpansionDetector",
    "JsonScalar",
    "MarketEvent",
    "StructuralEventConfig",
    "StructuralEventDetector",
    "UTCSessionUnit",
    "build_overlap_report",
    "kind_pair_key",
    "make_event",
    "observation_resets_continuity",
    "segment_fixed_windows",
    "segment_rolling_windows",
    "segment_utc_sessions",
    "validate_observation_pair",
]
