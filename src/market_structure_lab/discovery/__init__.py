"""Outcome-blind discovery boundaries."""

from market_structure_lab.discovery.behaviours import (
    FeatureDistribution,
    FrozenBehaviour,
    freeze_behaviours,
)
from market_structure_lab.discovery.evidence import (
    AIInterpretation,
    BehaviourEvidencePack,
)
from market_structure_lab.discovery.kmeans import KMeansResult, fit_kmeans, fit_projected_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix, build_feature_matrix
from market_structure_lab.discovery.motifs import MotifMatch, discover_motifs
from market_structure_lab.discovery.pca import PCAProjection, fit_pca, pca_projection_sha256
from market_structure_lab.discovery.runs import (
    DiscoveryRunConfig,
    DiscoveryRunManifest,
    InterpretationManifest,
    publish_ai_interpretations,
    run_discovery,
)
from market_structure_lab.discovery.splits import (
    DiscoveryInput,
    FrozenDiscoverySplit,
    PartitionRole,
    TimePartition,
    freeze_split,
    make_discovery_input,
)
from market_structure_lab.discovery.stability import (
    StabilityPolicy,
    StabilityReport,
    adjusted_rand_index,
    evaluate_cluster_stability,
)
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
    "DiscoveryInput",
    "AIInterpretation",
    "BehaviourEvidencePack",
    "ClusterObservation",
    "ClusterTransitionEstimate",
    "ClusterTransitionMatrix",
    "ClusterTransitionRow",
    "TransitionBoundaryEvidence",
    "FeatureDistribution",
    "FeatureMatrix",
    "FrozenBehaviour",
    "FrozenDiscoverySplit",
    "DiscoveryRunConfig",
    "DiscoveryRunManifest",
    "InterpretationManifest",
    "KMeansResult",
    "MotifMatch",
    "PCAProjection",
    "PartitionRole",
    "StabilityPolicy",
    "StabilityReport",
    "TimePartition",
    "adjusted_rand_index",
    "build_feature_matrix",
    "compress_dwell_runs",
    "discover_motifs",
    "estimate_cluster_transitions",
    "evaluate_cluster_stability",
    "fit_kmeans",
    "fit_pca",
    "fit_projected_kmeans",
    "freeze_behaviours",
    "freeze_split",
    "make_discovery_input",
    "pca_projection_sha256",
    "publish_ai_interpretations",
    "run_discovery",
]
