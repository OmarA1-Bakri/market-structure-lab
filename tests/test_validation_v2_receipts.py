from pathlib import Path

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipt,
    select_terminal_attempts_v2,
    verify_validation_v2_receipts,
)


def _payload(slot: str, attempt: int) -> dict[str, object]:
    input_sha256 = "b" * 64
    attempt_sha256 = hash_json(
        "phase5-validation-slot-attempt-v2",
        {"slot_id": slot, "input_sha256": input_sha256, "attempt_number": attempt},
    )
    payload = {
        "schema_version": "validation-slot-computation-result-v2",
        "programme_id": "VPV2-" + "1" * 64,
        "slot_id": slot,
        "attempt_number": attempt,
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
        "decision": "inconclusive",
        "computation_completed": True,
        "reason": "fixture result",
        "p_value": None,
        "metrics": {"estimate": 0.0},
    }
    payload["result_sha256"] = hash_json(
        "phase5-validation-slot-computation-result-v2",
        {
            "programme_id": payload["programme_id"],
            "slot_id": slot,
            "attempt_sha256": attempt_sha256,
            "input_sha256": input_sha256,
            "evidence_sha256": payload["evidence_sha256"],
            "execution_status": payload["execution_status"],
            "decision": payload["decision"],
            "computation_completed": payload["computation_completed"],
            "p_value": payload["p_value"],
            "metrics": payload["metrics"],
        },
    )
    return payload


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


def test_receipt_rejects_retry_gaps_and_unsafe_slot_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="slot"):
        publish_validation_v2_receipt(tmp_path, _payload("../escape", 1))
    with pytest.raises(ValueError, match="attempt"):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 2))


def test_receipt_publication_failure_leaves_no_receipt_or_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    def fail_move(_source: Path, _destination: Path) -> None:
        raise OSError("durable publication failed")

    monkeypatch.setattr(module, "durable_move_no_replace", fail_move)

    with pytest.raises(OSError, match="durable publication failed"):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))

    assert not (tmp_path / "VS-0001").exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_receipt_commits_new_slot_directory_before_durable_file_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    calls: list[tuple[str, Path]] = []

    def move(source: Path, destination: Path) -> None:
        calls.append(("move", destination))
        source.rename(destination)

    monkeypatch.setattr(
        module,
        "fsync_directory_posix",
        lambda path: calls.append(("fsync", path)),
    )
    monkeypatch.setattr(module, "durable_move_no_replace", move)

    receipt = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))

    assert receipt.path.is_file()
    assert calls == [
        ("fsync", tmp_path),
        ("move", receipt.path),
    ]


def test_windows_receipt_uses_write_through_move_without_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    moves: list[tuple[Path, Path, bool, tuple[str, ...]]] = []

    def move(source: Path, destination: Path) -> None:
        assert not destination.exists()
        moves.append(
            (
                source,
                destination,
                source.is_dir(),
                tuple(sorted(path.name for path in source.iterdir())),
            )
        )
        source.rename(destination)

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "fsync_directory_posix",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("Windows receipt publication must not fsync a directory descriptor")
        ),
    )
    monkeypatch.setattr(module, "durable_move_no_replace", move)

    receipt = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))

    assert len(moves) == 1
    source, destination, source_was_directory, members = moves[0]
    assert destination == receipt.path.parent
    assert source.parent == tmp_path
    assert source_was_directory is True
    assert members == (receipt.path.name,)
    assert not source.exists()


def test_windows_first_receipt_move_failure_leaves_no_slot_or_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    def fail_move(source: Path, destination: Path) -> None:
        assert source.is_dir()
        assert not destination.exists()
        raise OSError("Windows write-through move failed")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "durable_move_no_replace", fail_move)

    with pytest.raises(OSError, match="write-through"):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))

    assert tuple(tmp_path.iterdir()) == ()


def test_windows_receipt_first_slot_directory_and_retry_file_are_additive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    moves: list[tuple[Path, Path, bool]] = []

    def move(source: Path, destination: Path) -> None:
        moves.append((source, destination, source.is_dir()))
        source.rename(destination)

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "durable_move_no_replace", move)

    first = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    second = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 2))

    assert first.path.is_file()
    assert second.path.is_file()
    assert [(destination, was_directory) for _, destination, was_directory in moves] == [
        (tmp_path / "VS-0001", True),
        (second.path, False),
    ]
