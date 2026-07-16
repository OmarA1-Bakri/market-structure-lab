"""Data truth, recovery, and canonical market-data boundaries."""

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
from market_structure_lab.data.derived import (
    DerivedEvidence,
    DerivedPartitionRecord,
    DerivedPublicationIdentity,
    DerivedPublicationManifest,
    publish_feature_rows,
    publish_market_events,
    read_derived_manifest,
    verify_derived_publication,
)
from market_structure_lab.data.export import (
    SnapshotIdentity,
    SnapshotManifest,
    export_partitioned_snapshot,
    verify_snapshot,
)
from market_structure_lab.data.loader import iter_candle_batches, load_candles
from market_structure_lab.data.segments import (
    SegmentBoundary,
    assign_segment_ids,
    load_canonical_gap_boundaries,
)

__all__ = [
    "CandidateMapping",
    "CandleQuality",
    "ColumnInspection",
    "DatabaseInspectionError",
    "DerivedEvidence",
    "DerivedPartitionRecord",
    "DerivedPublicationIdentity",
    "DerivedPublicationManifest",
    "GapRange",
    "InspectionMode",
    "InspectionReport",
    "RecoveryCandle",
    "RecoveryManifest",
    "SegmentBoundary",
    "SnapshotIdentity",
    "SnapshotManifest",
    "TableInspection",
    "detect_candle_mapping",
    "export_partitioned_snapshot",
    "inspect_configured_database",
    "inspect_database",
    "iter_candle_batches",
    "load_candles",
    "load_canonical_gap_boundaries",
    "publish_feature_rows",
    "publish_market_events",
    "render_human",
    "render_json",
    "report_to_dict",
    "read_derived_manifest",
    "write_json_report",
    "assign_segment_ids",
    "verify_snapshot",
    "verify_derived_publication",
]
