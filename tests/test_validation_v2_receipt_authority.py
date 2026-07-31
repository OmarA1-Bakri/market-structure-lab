from __future__ import annotations

from copy import copy
from dataclasses import fields, replace
import json
from pathlib import Path

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2 import (
    SlotRunnerInputsV2,
    ValidationSlotComputationResultV2,
    VerifiedDevelopmentOutcomeReaderV2,
    _issue_fixture_outcome_reader_v2,
    run_slot_roster_v2,
)
from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipt,
)


def _forged_promoted_payload() -> dict[str, object]:
    input_sha256 = "b" * 64
    attempt_sha256 = hash_json(
        "phase5-validation-slot-attempt-v2",
        {"slot_id": "VS-0001", "input_sha256": input_sha256, "attempt_number": 1},
    )
    payload: dict[str, object] = {
        "schema_version": "validation-slot-computation-result-v2",
        "programme_id": "VPV2-" + "1" * 64,
        "slot_id": "VS-0001",
        "attempt_number": 1,
        "runner_kind": "core",
        "runner_version": "slot-runner-v2",
        "attempt_sha256": attempt_sha256,
        "input_sha256": input_sha256,
        "evidence_sha256": "c" * 64,
        "result_sha256": "",
        "parent_slot_id": None,
        "parent_attempt_sha256": None,
        "parent_result_sha256": None,
        "execution_status": "completed",
        "decision": "promoted",
        "computation_completed": True,
        "reason": "caller claims promotion",
        "p_value": 0.0,
        "metrics": {"fabricated": 1},
    }
    payload["result_sha256"] = hash_json(
        "phase5-validation-slot-computation-result-v2",
        {
            "programme_id": payload["programme_id"],
            "slot_id": payload["slot_id"],
            "attempt_sha256": payload["attempt_sha256"],
            "input_sha256": payload["input_sha256"],
            "evidence_sha256": payload["evidence_sha256"],
            "execution_status": payload["execution_status"],
            "decision": payload["decision"],
            "computation_completed": payload["computation_completed"],
            "p_value": payload["p_value"],
            "metrics": payload["metrics"],
        },
    )
    return payload


class _ForgedResultLookalike:
    def to_dict(self) -> dict[str, object]:
        return _forged_promoted_payload()


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            "directory" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    )


def _issued_result() -> tuple[
    ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2
]:
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    fixture_bytes = json.dumps(
        {
            "schema_version": "validation-v2-outcome-fixture-v1",
            "slots": {slot_id: [] for slot_id in slot_ids},
        },
        sort_keys=True,
    ).encode()
    reader = _issue_fixture_outcome_reader_v2(
        fixture_bytes,
        programme_id="VPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    results = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="VPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=True,
            source_available=False,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )
    return results[0], reader


@pytest.fixture(scope="module")
def issued_result() -> tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2]:
    return _issued_result()


def test_publisher_rejects_self_hashed_forged_mapping_without_filesystem_mutation(
    tmp_path: Path,
) -> None:
    before = _tree_snapshot(tmp_path)

    with pytest.raises(TypeError, match="factory-issued|registered result"):
        publish_validation_v2_receipt(tmp_path, _forged_promoted_payload())

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "VS-0001").exists()


def test_publisher_rejects_generic_to_dict_lookalike_without_filesystem_mutation(
    tmp_path: Path,
) -> None:
    before = _tree_snapshot(tmp_path)

    with pytest.raises(TypeError, match="factory-issued|registered result"):
        publish_validation_v2_receipt(tmp_path, _ForgedResultLookalike())

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "VS-0001").exists()


def test_validation_result_rejects_direct_construction(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result
    constructor_fields = {
        item.name: getattr(original, item.name)
        for item in fields(ValidationSlotComputationResultV2)
    }

    with pytest.raises(TypeError, match="factory"):
        ValidationSlotComputationResultV2(**constructor_fields)


def test_validation_result_rejects_dataclass_replacement(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result

    with pytest.raises(TypeError, match="factory"):
        replace(original)


def test_publisher_rejects_shallow_copy_of_factory_issued_result(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result
    try:
        copied = copy(original)
    except (TypeError, ValueError) as error:
        assert "factory" in str(error) or "registered" in str(error)
        return
    assert copied is not original
    before = _tree_snapshot(tmp_path)

    with pytest.raises((TypeError, ValueError), match="registered original|factory-issued"):
        publish_validation_v2_receipt(tmp_path, copied)

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / original.slot_id).exists()


def test_publisher_accepts_exact_registered_factory_issued_result(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result

    receipt = publish_validation_v2_receipt(tmp_path, original)

    assert receipt.slot_id == original.slot_id
    assert receipt.result_sha256 == original.result_sha256


def test_publisher_revalidates_original_computation_evidence_before_writing(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, reader = issued_result
    original_bytes = reader.canonical_bytes
    before = _tree_snapshot(tmp_path)
    object.__setattr__(reader, "canonical_bytes", b'{"mutated":true}')
    try:
        with pytest.raises(ValueError, match="computation evidence|registered original"):
            publish_validation_v2_receipt(tmp_path, original)
    finally:
        object.__setattr__(reader, "canonical_bytes", original_bytes)

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / original.slot_id).exists()
