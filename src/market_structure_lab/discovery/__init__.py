"""Outcome-blind discovery boundaries."""

from market_structure_lab.discovery.behaviours import (
    FeatureDistribution,
    FrozenBehaviour,
    freeze_behaviours,
)
from market_structure_lab.discovery.kmeans import KMeansResult, fit_kmeans
from market_structure_lab.discovery.matrix import FeatureMatrix, build_feature_matrix
from market_structure_lab.discovery.pca import PCAProjection, fit_pca
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

__all__ = [
    "DiscoveryInput",
    "FeatureDistribution",
    "FeatureMatrix",
    "FrozenBehaviour",
    "FrozenDiscoverySplit",
    "KMeansResult",
    "PCAProjection",
    "PartitionRole",
    "StabilityPolicy",
    "StabilityReport",
    "TimePartition",
    "adjusted_rand_index",
    "build_feature_matrix",
    "evaluate_cluster_stability",
    "fit_kmeans",
    "fit_pca",
    "freeze_behaviours",
    "freeze_split",
    "make_discovery_input",
]
