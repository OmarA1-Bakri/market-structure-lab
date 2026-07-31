from pathlib import Path

import pytest

from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipt,
    select_terminal_attempts_v2,
    verify_validation_v2_receipts,
)


def _payload(slot: str, attempt: int) -> dict[str, object]:
    return {
        "schema_version": "validation-slot-computation-result-v2",
        "programme_id": "VPV2-" + "1" * 64,
        "slot_id": slot,
        "attempt_number": attempt,
        "runner_kind": "core",
        "runner_version": "slot-runner-v2",
        "attempt_sha256": (f"{attempt:x}" * 64)[:64],
        "input_sha256": "b" * 64,
        "result_sha256": (f"{attempt + 8:x}" * 64)[:64],
        "execution_status": "completed",
        "decision": "inconclusive",
        "computation_completed": True,
        "metrics": {"estimate": 0.0},
    }


def test_receipts_are_immutable_and_retry_is_additive(tmp_path: Path) -> None:
    first = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    second = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 2))
    assert first.path != second.path
    with pytest.raises(FileExistsError):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    selected = select_terminal_attempts_v2((first, second))
    assert selected["VS-0001"].attempt_number == 2


def test_verifier_reports_receipts_separately_from_computations(tmp_path: Path) -> None:
    receipt = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    report = verify_validation_v2_receipts(
        (receipt,), expected_slot_ids=("VS-0001",), runner_version="slot-runner-v2"
    )
    assert report.planned_slots == 1
    assert report.attempted_slots == 1
    assert report.completed_slot_computations == 1
    assert report.inconclusive_slot_computations == 1
    assert report.receipt_count == 1
