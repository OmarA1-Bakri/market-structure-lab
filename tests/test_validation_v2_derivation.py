from dataclasses import fields
from datetime import UTC, datetime

import pytest

from market_structure_lab.research import validation_v2 as module
from market_structure_lab.research.validation_v2 import (
    AttachedDevelopmentOutcomeV2,
    ValidationV2SourceBundle,
)

SHA = "a" * 64


def test_public_source_bundle_has_no_caller_minted_decision_fields() -> None:
    names = {item.name for item in fields(ValidationV2SourceBundle)}
    forbidden = {
        "weekly_vectors",
        "robustness_lower_bounds",
        "exposures",
        "pvalues",
        "selector_scores",
        "decisions",
        "eligibility",
        "outcomes_by_slot",
    }
    assert not names & forbidden


def test_attached_outcomes_reject_direct_construction() -> None:
    with pytest.raises(TypeError, match="factory"):
        AttachedDevelopmentOutcomeV2(
            event_id="forged",
            slot_id="VS-0001",
            fold_id="outer-1",
            symbol="SOLUSDT",
            timeframe="1h",
            timestamp=datetime(2025, 1, 6, tzinfo=UTC),
            net_return=0.1,
            source_partition_sha256=SHA,
            aggregate_row_sha256="b" * 64,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            source_publication_sha256="e" * 64,
            aggregate_publication_sha256="f" * 64,
            detector_metric=1.0,
            volume=10.0,
        )


def test_fixture_reader_has_no_quantitative_evidence_derivation_surface() -> None:
    for name in (
        "derive_slot_evidence_v2",
        "DerivedSlotEvidenceV2",
        "ExposureRowV2",
        "RobustnessRerunEvidenceV2",
        "SelectorEvidenceV2",
        "WeeklySlotVectorV2",
    ):
        assert not hasattr(module, name)
