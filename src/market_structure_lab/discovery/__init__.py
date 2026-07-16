"""Outcome-blind discovery boundaries."""

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

__all__ = [
    "DiscoveryInput",
    "FeatureMatrix",
    "FrozenDiscoverySplit",
    "KMeansResult",
    "PCAProjection",
    "PartitionRole",
    "TimePartition",
    "build_feature_matrix",
    "fit_kmeans",
    "fit_pca",
    "freeze_split",
    "make_discovery_input",
]
