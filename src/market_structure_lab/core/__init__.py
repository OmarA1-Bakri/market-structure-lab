"""Cross-cutting, dependency-light configuration primitives."""

from market_structure_lab.core.config import (
    CandleSourceMapping,
    DatabaseSettings,
    MarketDataSettings,
    TimestampUnit,
    load_settings,
)
from market_structure_lab.core.identity import (
    CanonicalIdentityError,
    canonical_json,
    evaluation_id,
    hash_canonical_json,
    hash_json,
    programme_id,
)

__all__ = [
    "CandleSourceMapping",
    "CanonicalIdentityError",
    "DatabaseSettings",
    "MarketDataSettings",
    "TimestampUnit",
    "canonical_json",
    "evaluation_id",
    "hash_canonical_json",
    "hash_json",
    "load_settings",
    "programme_id",
]
