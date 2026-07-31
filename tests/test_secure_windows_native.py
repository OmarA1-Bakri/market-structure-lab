from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows")


def _payload(slot: str, attempt: int) -> dict[str, object]:
    from market_structure_lab.core.identity import hash_json

    input_sha256 = "b" * 64
    attempt_sha256 = hash_json(
        "phase5-validation-slot-attempt-v2",
        {"slot_id": slot, "input_sha256": input_sha256, "attempt_number": attempt},
    )
    payload: dict[str, object] = {
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
        "reason": "native Windows integration fixture",
        "p_value": None,
        "metrics": {"estimate": 0.0},
    }
    payload["result_sha256"] = hash_json(
        "phase5-validation-slot-computation-result-v2",
        {
            "programme_id": payload["programme_id"],
            "slot_id": payload["slot_id"],
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


def test_native_windows_moves_and_deletes_nonempty_owned_tree(tmp_path: Path) -> None:
    from market_structure_lab.core.secure_windows import WindowsHandleFilesystem

    stage = tmp_path / "stage"
    (stage / "nested").mkdir(parents=True)
    (stage / "root.json").write_bytes(b"root")
    (stage / "nested" / "child.json").write_bytes(b"child")
    destination = tmp_path / "published"

    with WindowsHandleFilesystem().claim_owned_tree(stage) as claim:
        claim.move_to(destination)

    assert (destination / "root.json").read_bytes() == b"root"
    assert (destination / "nested" / "child.json").read_bytes() == b"child"
    competing_stage = tmp_path / "competing-stage"
    competing_stage.mkdir()
    (competing_stage / "candidate.json").write_bytes(b"candidate")
    with WindowsHandleFilesystem().claim_owned_tree(competing_stage) as claim:
        with pytest.raises(FileExistsError):
            claim.move_to(destination)
        claim.delete_exact()
    assert (destination / "root.json").read_bytes() == b"root"
    with WindowsHandleFilesystem().claim_owned_tree(destination) as claim:
        claim.delete_exact()
    assert tuple(tmp_path.iterdir()) == ()


def test_native_windows_publishes_first_and_retry_receipts(tmp_path: Path) -> None:
    from market_structure_lab.research.validation_v2_receipts import (
        publish_validation_v2_receipt,
    )

    first = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    retry = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 2))

    assert first.path.is_file()
    assert retry.path.is_file()
    assert first.path != retry.path


def test_native_windows_publishes_paired_source_and_audit_trees(tmp_path: Path) -> None:
    from market_structure_lab.data.validation_source_v2 import (
        _commit_paired_directories_windows,
    )

    source_stage = tmp_path / "source-stage"
    audit_stage = tmp_path / "audit-stage"
    (source_stage / "nested").mkdir(parents=True)
    audit_stage.mkdir()
    (source_stage / "nested" / "source.json").write_bytes(b"source")
    (audit_stage / "audit.json").write_bytes(b"audit")

    _commit_paired_directories_windows(
        first_stage=source_stage,
        first_destination=tmp_path / "source",
        second_stage=audit_stage,
        second_destination=tmp_path / "audit",
    )

    assert (tmp_path / "source" / "nested" / "source.json").read_bytes() == b"source"
    assert (tmp_path / "audit" / "audit.json").read_bytes() == b"audit"


def test_native_windows_failed_first_receipt_cleanup_leaves_zero_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.core.secure_windows import CtypesWindowsApi
    from market_structure_lab.research.validation_v2_receipts import (
        publish_validation_v2_receipt,
    )

    def fail_rename(
        _self: CtypesWindowsApi,
        _handle: int,
        _destination_directory: int,
        _destination_name: str,
    ) -> None:
        raise OSError("injected native rename failure")

    monkeypatch.setattr(CtypesWindowsApi, "rename_handle", fail_rename)

    with pytest.raises(OSError, match="injected native rename failure"):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))

    assert tuple(tmp_path.iterdir()) == ()


def test_native_windows_retry_rejects_destination_directory_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.core.secure_windows import CtypesWindowsApi
    from market_structure_lab.research.validation_v2_receipts import (
        publish_validation_v2_receipt,
    )

    first = publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 1))
    slot_root = first.path.parent
    displaced = tmp_path / "displaced-slot"
    original_rename = CtypesWindowsApi.rename_handle
    substituted = False

    def substitute_then_rename(
        self: CtypesWindowsApi,
        handle: int,
        destination_directory: int,
        destination_name: str,
    ) -> None:
        nonlocal substituted
        if not substituted:
            slot_root.rename(displaced)
            slot_root.mkdir()
            (slot_root / "foreign-sentinel").write_bytes(b"preserve")
            substituted = True
        original_rename(self, handle, destination_directory, destination_name)

    monkeypatch.setattr(CtypesWindowsApi, "rename_handle", substitute_then_rename)

    with pytest.raises(RuntimeError, match="substituted"):
        publish_validation_v2_receipt(tmp_path, _payload("VS-0001", 2))

    assert substituted is True
    assert (slot_root / "foreign-sentinel").read_bytes() == b"preserve"
    assert tuple(path.name for path in displaced.iterdir()) == (first.path.name,)
    assert not any(path.name.startswith(".VS-0001.") for path in tmp_path.iterdir())
