"""Canonical boundary-aware transition evidence."""

from market_structure_lab.discovery.transitions import (
    ClusterObservation,
    ClusterTransitionEstimate,
    ClusterTransitionMatrix,
    ClusterTransitionRow,
    TransitionBoundaryEvidence,
    compress_dwell_runs,
    estimate_cluster_transitions,
)

__all__ = [
    "ClusterObservation",
    "ClusterTransitionEstimate",
    "ClusterTransitionMatrix",
    "ClusterTransitionRow",
    "TransitionBoundaryEvidence",
    "compress_dwell_runs",
    "estimate_cluster_transitions",
]
