from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
from market_structure_lab.research.validation_v2 import (
    AttachedDevelopmentOutcomeV2,
    ValidationV2SourceBundle,
    derive_slot_evidence_v2,
)

SHA = "a" * 64
START = datetime(2025, 1, 6, tzinfo=UTC)


def _outcome(
    slot_id: str, day: int, value: float, *, fold: str = "outer-1"
) -> AttachedDevelopmentOutcomeV2:
    return AttachedDevelopmentOutcomeV2(
        event_id=f"event-{slot_id}-{day}",
        slot_id=slot_id,
        fold_id=fold,
        symbol="SOLUSDT",
        timeframe="1h",
        timestamp=START + timedelta(days=day),
        net_return=value,
        source_partition_sha256=SHA,
        aggregate_row_sha256="b" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
    )


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
    }
    assert not names & forbidden


def test_weekly_and_selector_evidence_are_derived_and_slot_bound() -> None:
    slot = VALIDATION_SLOT_ROSTER[0]
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcomes=tuple(_outcome(slot.slot_id, day, 0.01 + day / 1000) for day in range(15)),
        seed=7,
        runner_version="slot-runner-v2",
    )
    assert tuple(week.week_start.weekday() for week in evidence.weekly_vectors) == (0, 0, 0)
    assert evidence.slot_id == slot.slot_id
    assert evidence.selector_evidence.inner_fold_event_ids
    assert not set(evidence.selector_evidence.inner_fold_event_ids) & set(
        evidence.selector_evidence.outer_diagnostic_event_ids
    )


def test_derived_artifact_rejects_slot_swapping() -> None:
    slot = VALIDATION_SLOT_ROSTER[0]
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcomes=(_outcome(slot.slot_id, 0, 0.01), _outcome(slot.slot_id, 7, 0.02)),
        seed=7,
        runner_version="slot-runner-v2",
    )
    with pytest.raises(ValueError, match="slot"):
        evidence.verify_for(
            programme_id=evidence.programme_id,
            slot=VALIDATION_SLOT_ROSTER[1],
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            runner_version="slot-runner-v2",
        )


def test_exposure_rows_and_robustness_are_rerun_from_slot_outcomes() -> None:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.kind.value == "robustness")
    rows = tuple(
        AttachedDevelopmentOutcomeV2(
            event_id=f"event-{slot.slot_id}-{day}",
            slot_id=slot.slot_id,
            fold_id="outer-2",
            symbol="SOLUSDT",
            timeframe=slot.timeframe,
            timestamp=START + timedelta(days=day),
            net_return=0.01 + day / 1000,
            source_partition_sha256=SHA,
            aggregate_row_sha256="b" * 64,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
        )
        for day in (0, 7)
    )
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcomes=rows,
        seed=7,
        runner_version="slot-runner-v2",
    )
    assert tuple(row.event_id for row in evidence.exposure_rows) == tuple(
        row.event_id for row in rows
    )
    assert evidence.robustness_rerun is not None
    assert evidence.robustness_rerun.event_ids == tuple(row.event_id for row in rows)
