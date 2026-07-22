"""Data truth, recovery, and canonical market-data boundaries."""

from market_structure_lab.data.aggregate_bars import (
    CanonicalAggregateBar,
    VerifiedAggregateBarSpool,
    iter_complete_aggregate_bars,
    spool_complete_aggregate_bars,
)
from market_structure_lab.data.aggregate_publication import (
    AggregatePublicationFailure,
    AggregatePublicationManifest,
    publish_aggregate_bars,
    verify_aggregate_publication,
    verify_failed_aggregate_publication,
)
from market_structure_lab.data.freshness import (
    CanonicalSeriesState,
    FreshnessManifest,
    FreshnessPlanningStatus,
    FreshnessSymbolPlan,
    build_freshness_manifest,
    read_freshness_manifest,
    resolve_freshness_cutoff,
    write_freshness_manifest,
)
from market_structure_lab.data.freshness_snapshot import (
    SnapshotPublicationPolicy,
    publish_freshness_snapshot,
)
from market_structure_lab.data.gaps import GapRange, RecoveryManifest
from market_structure_lab.data.inspection import (
    CandidateMapping,
    CandleQuality,
    ColumnInspection,
    DatabaseInspectionError,
    InspectionMode,
    InspectionReport,
    TableInspection,
    detect_candle_mapping,
    inspect_configured_database,
    inspect_database,
    render_human,
    render_json,
    report_to_dict,
    write_json_report,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.export import (
    SnapshotIdentity,
    SnapshotManifest,
    export_partitioned_snapshot,
    verify_snapshot,
)
from market_structure_lab.data.loader import iter_candle_batches, load_candles
from market_structure_lab.data.price_precision import (
    SourcePricePrecisionManifest,
    SourcePricePrecisionUnavailable,
    read_source_price_precision_manifest,
)
from market_structure_lab.data.segments import (
    SegmentBoundary,
    assign_segment_ids,
    load_canonical_gap_boundaries,
)

__all__ = [
    "AggregatePublicationManifest",
    "AggregatePublicationFailure",
    "CandidateMapping",
    "CandleQuality",
    "CanonicalSeriesState",
    "CanonicalAggregateBar",
    "VerifiedAggregateBarSpool",
    "SourcePricePrecisionManifest",
    "SourcePricePrecisionUnavailable",
    "ColumnInspection",
    "DatabaseInspectionError",
    "FreshnessManifest",
    "FreshnessPlanningStatus",
    "FreshnessSymbolPlan",
    "GapRange",
    "InspectionMode",
    "InspectionReport",
    "RecoveryCandle",
    "RecoveryManifest",
    "SegmentBoundary",
    "SnapshotIdentity",
    "SnapshotManifest",
    "SnapshotPublicationPolicy",
    "TableInspection",
    "detect_candle_mapping",
    "export_partitioned_snapshot",
    "inspect_configured_database",
    "inspect_database",
    "iter_candle_batches",
    "iter_complete_aggregate_bars",
    "spool_complete_aggregate_bars",
    "load_candles",
    "load_canonical_gap_boundaries",
    "publish_freshness_snapshot",
    "publish_aggregate_bars",
    "render_human",
    "render_json",
    "report_to_dict",
    "write_json_report",
    "assign_segment_ids",
    "build_freshness_manifest",
    "read_freshness_manifest",
    "resolve_freshness_cutoff",
    "verify_snapshot",
    "read_source_price_precision_manifest",
    "verify_aggregate_publication",
    "verify_failed_aggregate_publication",
    "write_freshness_manifest",
]
