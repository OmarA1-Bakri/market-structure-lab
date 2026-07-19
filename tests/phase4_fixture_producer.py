"""Truthful causal producer for the synthetic Phase 4 discovery golden fixture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
    NormalizationRequirement,
    ObservableCutoffRule,
)

PHASE4_FIXTURE_BUILDER_ID = "phase4_fixture_producer.Phase4FixtureFeatureProducer"
PHASE4_FIXTURE_BUILDER_VERSION = "phase4-fixture-producer-v1"
PHASE4_FIXTURE_FEATURE_SET_ID = "FS-000601"


@dataclass(frozen=True, slots=True)
class Phase4FixtureSource:
    """Raw causal inputs frozen for one synthetic fixture observation."""

    timestamp: datetime
    symbol: str
    segment_id: int
    auction_location_ratio: Decimal
    previous_volume: Decimal
    current_volume: Decimal
    volume_scale: Decimal

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        timestamp: datetime,
    ) -> Phase4FixtureSource:
        source = payload["source"]
        return cls(
            timestamp=timestamp,
            symbol=str(payload["symbol"]),
            segment_id=int(payload["segment_id"]),
            auction_location_ratio=Decimal(str(source["auction_location_ratio"])),
            previous_volume=Decimal(str(source["previous_volume"])),
            current_volume=Decimal(str(source["current_volume"])),
            volume_scale=Decimal(str(source["volume_scale"])),
        )

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("fixture timestamp must be timezone-aware")
        if not self.symbol:
            raise ValueError("fixture symbol must be non-empty")
        if self.segment_id < 0:
            raise ValueError("fixture segment_id must be non-negative")
        if self.volume_scale <= 0:
            raise ValueError("fixture volume_scale must be positive")
        if any(
            not value.is_finite()
            for value in (
                self.auction_location_ratio,
                self.previous_volume,
                self.current_volume,
                self.volume_scale,
            )
        ):
            raise ValueError("fixture numeric sources must be finite")


class Phase4FixtureFeatureRegistry(FeatureRegistry):
    """Test-only registry accepting only the exact fixture producer contract."""

    def audit_discovery(self) -> None:
        expected = Phase4FixtureFeatureProducer.definitions()
        if self.feature_set_id != PHASE4_FIXTURE_FEATURE_SET_ID:
            raise ValueError("Phase 4 fixture registry uses an unexpected feature-set ID")
        if self.definitions != expected:
            raise ValueError("Phase 4 fixture registry must match its exact producer contracts")


class Phase4FixtureFeatureProducer:
    """Build the two FS-000601 software-fixture fields from explicit causal inputs."""

    builder_id = PHASE4_FIXTURE_BUILDER_ID
    builder_version = PHASE4_FIXTURE_BUILDER_VERSION
    feature_names = ("auction_location", "volume_change")

    @classmethod
    def definitions(cls) -> tuple[FeatureDefinition, ...]:
        return (
            FeatureDefinition(
                name="auction_location",
                definition="Explicit causal fixture auction-location ratio at the cutoff.",
                family=FeatureFamily.AUCTION,
                value_kind=FeatureValueKind.FLOAT,
                units="ratio",
                required_prior_observations=0,
                missing_policy=MissingPolicy.ERROR,
                version="1.0.0",
                leakage_class=LeakageClass.AT_CUTOFF,
                source_fields=("fixture.auction_location_ratio",),
                trailing_window="current_observation",
                observable_cutoff_rule=ObservableCutoffRule.AT_INFORMATION_CUTOFF,
                normalization_requirement=NormalizationRequirement.NOT_REQUIRED,
                future_outcome_prohibited=True,
                builder_id=cls.builder_id,
                builder_version=cls.builder_version,
            ),
            FeatureDefinition(
                name="volume_change",
                definition=(
                    "Causal two-observation fixture volume delta divided by its explicit scale."
                ),
                family=FeatureFamily.SEQUENCE,
                value_kind=FeatureValueKind.FLOAT,
                units="ratio",
                required_prior_observations=1,
                missing_policy=MissingPolicy.ERROR,
                version="1.0.0",
                leakage_class=LeakageClass.TRAILING_ONLY,
                source_fields=(
                    "fixture.current_volume",
                    "fixture.previous_volume",
                    "fixture.volume_scale",
                ),
                trailing_window="trailing_2_observations",
                observable_cutoff_rule=(ObservableCutoffRule.TRAILING_THROUGH_INFORMATION_CUTOFF),
                normalization_requirement=NormalizationRequirement.NOT_REQUIRED,
                future_outcome_prohibited=True,
                builder_id=cls.builder_id,
                builder_version=cls.builder_version,
            ),
        )

    @classmethod
    def registry(cls, definitions: tuple[FeatureDefinition, ...]) -> FeatureRegistry:
        registry = Phase4FixtureFeatureRegistry(PHASE4_FIXTURE_FEATURE_SET_ID, definitions)
        cls.validate_registry(registry)
        return registry

    @classmethod
    def validate_registry(cls, registry: FeatureRegistry) -> None:
        if registry.feature_set_id != PHASE4_FIXTURE_FEATURE_SET_ID:
            raise ValueError("fixture producer received the wrong feature-set ID")
        if registry.definitions != cls.definitions() or registry.names != cls.feature_names:
            raise ValueError("fixture producer registry does not match its exact contracts")

    def build_row(
        self,
        source: Phase4FixtureSource,
        *,
        information_cutoff: datetime,
        dataset_version: str,
        config_version: str,
        profile_version: str,
        window_policy_id: str,
        registry: FeatureRegistry,
    ) -> FeatureRow:
        self.validate_registry(registry)
        values = {
            "auction_location": float(source.auction_location_ratio),
            "volume_change": float(
                (source.current_volume - source.previous_volume) / source.volume_scale
            ),
        }
        if tuple(values) != self.feature_names or any(
            type(value) is not float or not isfinite(value) for value in values.values()
        ):
            raise ValueError("fixture producer output does not match its float field contract")
        return FeatureRow(
            timestamp=source.timestamp,
            information_cutoff=information_cutoff,
            symbol=source.symbol,
            timeframe="1m",
            segment_id=source.segment_id,
            dataset_version=dataset_version,
            config_version=config_version,
            profile_version=profile_version,
            window_policy_id=window_policy_id,
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
            values=values,
        )


__all__ = [
    "PHASE4_FIXTURE_BUILDER_ID",
    "PHASE4_FIXTURE_BUILDER_VERSION",
    "PHASE4_FIXTURE_FEATURE_SET_ID",
    "Phase4FixtureFeatureProducer",
    "Phase4FixtureFeatureRegistry",
    "Phase4FixtureSource",
]
