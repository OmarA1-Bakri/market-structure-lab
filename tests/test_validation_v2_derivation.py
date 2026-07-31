from dataclasses import fields
from datetime import UTC, datetime, timedelta
import json

import pytest

from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
from market_structure_lab.research.validation_v2 import (
    AttachedDevelopmentOutcomeV2,
    ValidationV2SourceBundle,
    _issue_fixture_outcome_reader_v2,
    derive_slot_evidence_v2,
)

SHA = "a" * 64
START = datetime(2025, 1, 6, tzinfo=UTC)


def _rows(slot, days=(0,), values=(101.0,), metrics=(1.0,), symbols=("SOLUSDT",)):
    if len(symbols) == 1 and len(days) > 1:
        symbols = symbols * len(days)
    return [
        {
            "event_id": f"event-{slot.slot_id}-{day}-{symbol}",
            "timestamp": (START + timedelta(days=day)).isoformat(),
            "symbol": symbol,
            "timeframe": slot.timeframe,
            "entry_price": 100.0,
            "exit_price": value,
            "volume": 10.0,
            "detector_metric": metric,
            "partition_role": "inner_train",
        }
        for day, value, metric, symbol in zip(days, values, metrics, symbols, strict=True)
    ]


def _reader(slot, rows):
    content = json.dumps(
        {"schema_version": "validation-v2-outcome-fixture-v1", "slots": {slot.slot_id: rows}},
        sort_keys=True,
    ).encode()
    return _issue_fixture_outcome_reader_v2(
        content,
        programme_id="VPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=(slot.slot_id,),
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
            timestamp=START,
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


def test_weekly_and_selector_evidence_are_derived_and_slot_bound() -> None:
    slot = VALIDATION_SLOT_ROSTER[0]
    reader = _reader(
        slot,
        _rows(
            slot,
            days=tuple(range(15)),
            values=tuple(101 + d / 10 for d in range(15)),
            metrics=tuple(float(d) for d in range(15)),
            symbols=("SOLUSDT",) * 15,
        ),
    )
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcome_reader=reader,
        seed=7,
        runner_version="slot-runner-v2",
    )
    assert tuple(week.week_start.weekday() for week in evidence.weekly_vectors) == (0, 0, 0)
    assert evidence.selector_evidence.inner_fold_event_ids
    assert not set(evidence.selector_evidence.inner_fold_event_ids) & set(
        evidence.selector_evidence.outer_diagnostic_event_ids
    )


def test_derived_artifact_rejects_slot_swapping() -> None:
    slot = VALIDATION_SLOT_ROSTER[0]
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcome_reader=_reader(
            slot, _rows(slot, days=(0, 7), values=(101.0, 102.0), metrics=(1.0, 2.0))
        ),
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


def test_exposure_and_robustness_rerun_are_derived() -> None:
    slot = next(item for item in VALIDATION_SLOT_ROSTER if item.kind.value == "robustness")
    rows = _rows(
        slot, days=(0, 7), values=(101.0, 102.0), metrics=(1.0, 2.0), symbols=("SOLUSDT", "SOLUSDT")
    )
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcome_reader=_reader(slot, rows),
        seed=7,
        runner_version="slot-runner-v2",
    )
    assert tuple(row.event_id for row in evidence.exposure_rows) == tuple(
        row["event_id"] for row in rows
    )
    assert evidence.robustness_rerun is not None
    assert evidence.robustness_rerun.event_ids == tuple(row["event_id"] for row in rows)


def test_same_week_assets_are_one_week_and_selector_ignores_realized_returns() -> None:
    slot = VALIDATION_SLOT_ROSTER[0]
    reader = _reader(
        slot,
        _rows(
            slot,
            days=(0, 0),
            values=(50.0, 200.0),
            metrics=(1.0, 2.0),
            symbols=("SOLUSDT", "BNBUSDT"),
        ),
    )
    evidence = derive_slot_evidence_v2(
        programme_id="VPV2-" + "1" * 64,
        slot=slot,
        outcome_reader=reader,
        seed=7,
        runner_version="slot-runner-v2",
    )
    assert len(evidence.weekly_vectors) == 1
    assert evidence.weekly_vectors[0].symbols == ("BNBUSDT", "SOLUSDT")
    assert evidence.selector_evidence.selected_event_ids == (f"event-{slot.slot_id}-0-BNBUSDT",)
