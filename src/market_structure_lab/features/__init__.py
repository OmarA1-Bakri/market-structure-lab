"""Versioned, cutoff-aware discovery feature construction."""

from market_structure_lab.features.builder import (
    FeatureBuildBatch,
    FeatureBuildBatchLease,
    FeatureBuildBatchMetadata,
    FeatureBuilder,
)
from market_structure_lab.features.builtin import (
    BUILTIN_DEFINITIONS,
    BUILTIN_FEATURE_SET_ID,
    builtin_feature_registry,
)
from market_structure_lab.features.models import FeatureRow, FeatureValue, timeframe_duration
from market_structure_lab.features.normalization import (
    PartitionRole,
    RobustNormalizer,
    TrainingPartition,
    fit_robust_normalizer,
)
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureRegistrySnapshot,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
    NormalizationRequirement,
    ObservableCutoffRule,
)

__all__ = [
    "BUILTIN_DEFINITIONS",
    "BUILTIN_FEATURE_BUILDER_ID",
    "BUILTIN_FEATURE_BUILDER_VERSION",
    "BUILTIN_FEATURE_SET_ID",
    "FeatureBuilder",
    "FeatureBuildBatch",
    "FeatureBuildBatchLease",
    "FeatureBuildBatchMetadata",
    "FeatureDefinition",
    "FeatureFamily",
    "FeatureRegistry",
    "FeatureRegistrySnapshot",
    "FeatureRow",
    "FeatureValue",
    "FeatureValueKind",
    "LeakageClass",
    "MissingPolicy",
    "NormalizationRequirement",
    "ObservableCutoffRule",
    "PartitionRole",
    "RobustNormalizer",
    "TrainingPartition",
    "builtin_feature_registry",
    "fit_robust_normalizer",
    "timeframe_duration",
]
