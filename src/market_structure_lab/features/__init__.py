"""Versioned, cutoff-aware discovery feature construction."""

from market_structure_lab.features.builder import FeatureBuilder
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
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)

__all__ = [
    "BUILTIN_DEFINITIONS",
    "BUILTIN_FEATURE_SET_ID",
    "FeatureBuilder",
    "FeatureDefinition",
    "FeatureFamily",
    "FeatureRegistry",
    "FeatureRow",
    "FeatureValue",
    "FeatureValueKind",
    "LeakageClass",
    "MissingPolicy",
    "PartitionRole",
    "RobustNormalizer",
    "TrainingPartition",
    "builtin_feature_registry",
    "fit_robust_normalizer",
    "timeframe_duration",
]
