"""Immutable additive receipts for Phase 5 V2 slot attempts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType

from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.fs_durability import (
    durable_move_no_replace,
    fsync_directory_posix,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.core.secure_windows import (
    WindowsHandleFilesystem,
    WindowsOwnedTreeClaim,
)
from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER

_MAX_RECEIPT_BYTES = 256 * 1024
_SCHEMA = "validation-slot-receipt-v2"
_SLOT_ID = re.compile(r"^VS-[0-9]{4}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME = re.compile(r"^VPV2-[a-f0-9]{64}$")


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationReceiptV2:
    path: Path
    programme_id: str
    slot_id: str
    attempt_number: int
    runner_kind: str
    runner_version: str
    attempt_sha256: str
    input_sha256: str
    evidence_sha256: str
    result_sha256: str
    parent_slot_id: str | None
    parent_attempt_sha256: str | None
    parent_result_sha256: str | None
    execution_status: str
    decision: str
    computation_completed: bool
    reason: str
    p_value: float | None
    metrics: Mapping[str, object]
    receipt_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if _PROGRAMME.fullmatch(self.programme_id) is None:
            raise ValueError("receipt programme_id is invalid")
        if _SLOT_ID.fullmatch(self.slot_id) is None:
            raise ValueError("receipt slot_id is invalid")
        slot = next(
            (item for item in VALIDATION_SLOT_ROSTER if item.slot_id == self.slot_id),
            None,
        )
        if slot is None or self.runner_kind != slot.kind.value:
            raise ValueError("receipt runner kind differs from frozen slot")
        for value, label in (
            (self.attempt_sha256, "attempt_sha256"),
            (self.input_sha256, "input_sha256"),
            (self.evidence_sha256, "evidence_sha256"),
            (self.result_sha256, "result_sha256"),
            (self.receipt_sha256, "receipt_sha256"),
        ):
            if _SHA256.fullmatch(value) is None:
                raise ValueError(f"receipt {label} is invalid")
        if self.parent_slot_id != slot.parent_slot_id:
            raise ValueError("receipt parent slot differs from frozen roster")
        for parent in (self.parent_attempt_sha256, self.parent_result_sha256):
            if parent is not None and _SHA256.fullmatch(parent) is None:
                raise ValueError("receipt parent identity is invalid")
        if slot.parent_slot_id is None and (
            self.parent_attempt_sha256 is not None or self.parent_result_sha256 is not None
        ):
            raise ValueError("root receipt cannot have parent attempt/result identities")
        if slot.parent_slot_id is not None and (
            self.parent_attempt_sha256 is None or self.parent_result_sha256 is None
        ):
            raise ValueError("child receipt requires parent attempt/result identities")
        if self.attempt_number < 1:
            raise ValueError("receipt attempt_number must be positive")
        if self.computation_completed and not self.metrics:
            raise ValueError("completed computation receipt requires metrics")
        if self.execution_status == "completed":
            if self.decision == "not_evaluated":
                raise ValueError("completed receipt cannot be not_evaluated")
        elif self.execution_status in {"failed", "abandoned"}:
            if self.decision != "not_evaluated" or self.computation_completed:
                raise ValueError("failed/abandoned receipt must be not_evaluated")
        else:
            raise ValueError("receipt execution status is invalid")
        if self.p_value is not None and (
            not isinstance(self.p_value, float)
            or not self.p_value == self.p_value
            or not 0.0 <= self.p_value <= 1.0
        ):
            raise ValueError("receipt p_value must be finite and in [0, 1]")
        if self.path.parent.name != self.slot_id:
            raise ValueError("receipt path does not match its slot")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True, slots=True)
class ValidationV2ReceiptReport:
    planned_slots: int
    attempted_slots: int
    completed_slot_computations: int
    not_evaluated_slots: int
    inconclusive_slot_computations: int
    failed_slots: int
    abandoned_slots: int
    receipt_count: int


def publish_validation_v2_receipt(
    root: Path,
    result: Mapping[str, object] | object,
) -> ValidationReceiptV2:
    """Atomically create one no-clobber receipt for one additive attempt."""

    root = Path(root)
    require_regular_directory(root)
    if isinstance(result, Mapping):
        payload = dict(result)
    else:
        to_dict = getattr(result, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("receipt result must be a mapping or expose to_dict")
        raw_payload = to_dict()
        if not isinstance(raw_payload, dict):
            raise TypeError("receipt result to_dict must return an object")
        payload = raw_payload
    required = {
        "schema_version",
        "programme_id",
        "slot_id",
        "attempt_number",
        "runner_kind",
        "runner_version",
        "attempt_sha256",
        "input_sha256",
        "evidence_sha256",
        "result_sha256",
        "parent_slot_id",
        "parent_attempt_sha256",
        "parent_result_sha256",
        "execution_status",
        "decision",
        "computation_completed",
        "reason",
        "p_value",
        "metrics",
    }
    if not required <= set(payload):
        raise ValueError("slot result is missing receipt identity or metrics fields")
    receipt_payload = {
        "schema_version": _SCHEMA,
        "result_schema_version": payload["schema_version"],
        **{name: payload[name] for name in sorted(required - {"schema_version"})},
    }
    preimage = _canonical(receipt_payload)
    receipt_payload["receipt_sha256"] = _sha256(preimage)
    content = _canonical(receipt_payload)
    if len(content) > _MAX_RECEIPT_BYTES:
        raise ValueError("validation V2 receipt exceeds the byte ceiling")
    slot = str(payload["slot_id"])
    raw_attempt = payload["attempt_number"]
    if isinstance(raw_attempt, bool) or not isinstance(raw_attempt, int):
        raise TypeError("receipt attempt_number must be an integer")
    attempt = raw_attempt
    if _SLOT_ID.fullmatch(slot) is None:
        raise ValueError("receipt slot_id must be a canonical frozen slot identifier")
    if attempt < 1:
        raise ValueError("receipt attempt must start at one")
    expected_attempt = hash_json(
        "phase5-validation-slot-attempt-v2",
        {
            "slot_id": slot,
            "input_sha256": payload["input_sha256"],
            "attempt_number": attempt,
        },
    )
    if payload["attempt_sha256"] != expected_attempt:
        raise ValueError("receipt attempt identity differs")
    expected_result = hash_json(
        "phase5-validation-slot-computation-result-v2",
        {
            "programme_id": payload["programme_id"],
            "slot_id": slot,
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
    if payload["result_sha256"] != expected_result:
        raise ValueError("receipt result identity differs")
    slot_root = root / slot
    destination = slot_root / f"attempt-{attempt:04d}.json"
    if _is_windows_platform():
        if path_exists_no_follow(slot_root):
            _publish_windows_retry_receipt(
                root=root,
                slot_root=slot_root,
                destination=destination,
                attempt=attempt,
                content=content,
            )
            return _receipt_from_payload(destination, receipt_payload, content)
        if attempt > 1:
            raise ValueError("receipt attempts must be gap-free and start at one")
        _publish_first_windows_slot_directory(
            root=root,
            slot_root=slot_root,
            destination=destination,
            content=content,
        )
        return _receipt_from_payload(destination, receipt_payload, content)
    if attempt > 1 and not path_exists_no_follow(root / slot / f"attempt-{attempt - 1:04d}.json"):
        raise ValueError("receipt attempts must be gap-free and start at one")
    if path_exists_no_follow(destination):
        raise FileExistsError(f"refusing existing validation V2 receipt: {destination}")
    slot_root_created = False
    if path_exists_no_follow(slot_root):
        require_regular_directory(slot_root)
    else:
        slot_root.mkdir(mode=0o755)
        require_regular_directory(slot_root)
        slot_root_created = True
        if not _is_windows_platform():
            try:
                fsync_directory_posix(root)
            except Exception:
                slot_root.rmdir()
                raise
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        durable_move_no_replace(temporary, destination)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        if slot_root_created:
            try:
                slot_root.rmdir()
                if not _is_windows_platform():
                    fsync_directory_posix(root)
            except OSError as cleanup_error:
                error.add_note(f"failed to clean receipt slot directory: {cleanup_error}")
        raise
    return _receipt_from_payload(destination, receipt_payload, content)


def _publish_first_windows_slot_directory(
    *,
    root: Path,
    slot_root: Path,
    destination: Path,
    content: bytes,
) -> None:
    stage = Path(tempfile.mkdtemp(prefix=f".{slot_root.name}.", suffix=".tmp", dir=root))
    staged_receipt = stage / destination.name
    descriptor = os.open(
        staged_receipt,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with _claim_windows_owned_tree(stage) as claim:
            try:
                claim.move_to(slot_root)
            except Exception:
                claim.delete_exact()
                raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_windows_retry_receipt(
    *,
    root: Path,
    slot_root: Path,
    destination: Path,
    attempt: int,
    content: bytes,
) -> None:
    filesystem = _windows_handle_filesystem()
    with filesystem.pin_rename_directory(slot_root) as slot_handle:
        if attempt > 1 and not filesystem.regular_exists_relative(
            slot_handle, f"attempt-{attempt - 1:04d}.json"
        ):
            raise ValueError("receipt attempts must be gap-free and start at one")
        if filesystem.regular_exists_relative(slot_handle, destination.name):
            raise FileExistsError(f"refusing existing validation V2 receipt: {destination}")
        stage = Path(tempfile.mkdtemp(prefix=f".{slot_root.name}.", suffix=".tmp", dir=root))
        staged_receipt = stage / destination.name
        descriptor = os.open(
            staged_receipt,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            with _claim_windows_owned_tree(stage) as claim:
                try:
                    claim.move_member_to(
                        staged_receipt.name,
                        destination,
                        destination_directory_handle=slot_handle,
                    )
                finally:
                    claim.delete_exact()
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _claim_windows_owned_tree(path: Path) -> WindowsOwnedTreeClaim:
    return WindowsHandleFilesystem().claim_owned_tree(path)


def _windows_handle_filesystem() -> WindowsHandleFilesystem:
    return WindowsHandleFilesystem()


def publish_validation_v2_receipts(
    root: Path,
    results: Sequence[Mapping[str, object] | object],
) -> tuple[ValidationReceiptV2, ...]:
    """Publish one independent immutable receipt for each supplied attempt."""

    return tuple(publish_validation_v2_receipt(root, result) for result in results)


def _receipt_from_payload(
    path: Path, payload: Mapping[str, object], content: bytes
) -> ValidationReceiptV2:
    metrics = payload["metrics"]
    if not isinstance(metrics, dict):
        raise TypeError("receipt metrics must be an object")
    attempt_number = payload["attempt_number"]
    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
        raise TypeError("receipt attempt_number must be an integer")
    raw_p_value = payload["p_value"]
    if raw_p_value is not None and (
        isinstance(raw_p_value, bool) or not isinstance(raw_p_value, (int, float))
    ):
        raise TypeError("receipt p_value must be numeric or null")
    return ValidationReceiptV2(
        path=path,
        programme_id=str(payload["programme_id"]),
        slot_id=str(payload["slot_id"]),
        attempt_number=attempt_number,
        runner_kind=str(payload["runner_kind"]),
        runner_version=str(payload["runner_version"]),
        attempt_sha256=str(payload["attempt_sha256"]),
        input_sha256=str(payload["input_sha256"]),
        evidence_sha256=str(payload["evidence_sha256"]),
        result_sha256=str(payload["result_sha256"]),
        parent_slot_id=(
            str(payload["parent_slot_id"]) if payload["parent_slot_id"] is not None else None
        ),
        parent_attempt_sha256=(
            str(payload["parent_attempt_sha256"])
            if payload["parent_attempt_sha256"] is not None
            else None
        ),
        parent_result_sha256=(
            str(payload["parent_result_sha256"])
            if payload["parent_result_sha256"] is not None
            else None
        ),
        execution_status=str(payload["execution_status"]),
        decision=str(payload["decision"]),
        computation_completed=bool(payload["computation_completed"]),
        reason=str(payload["reason"]),
        p_value=(float(raw_p_value) if raw_p_value is not None else None),
        metrics=metrics,
        receipt_sha256=str(payload["receipt_sha256"]),
        canonical_bytes=content,
    )


def verify_validation_v2_receipt(receipt: ValidationReceiptV2) -> ValidationReceiptV2:
    """Reopen and validate one immutable receipt's original bytes."""

    current = read_bounded_regular(receipt.path, _MAX_RECEIPT_BYTES)
    if current != receipt.canonical_bytes:
        raise ValueError("validation V2 receipt bytes changed")
    payload = json.loads(current)
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA:
        raise ValueError("validation V2 receipt schema is invalid")
    digest = payload.pop("receipt_sha256", None)
    if digest != _sha256(_canonical(payload)) or digest != receipt.receipt_sha256:
        raise ValueError("validation V2 receipt identity differs")
    payload["receipt_sha256"] = digest
    recreated = _receipt_from_payload(receipt.path, payload, current)
    if recreated != receipt:
        raise ValueError("validation V2 receipt fields changed")
    return receipt


def select_terminal_attempts_v2(
    receipts: Sequence[ValidationReceiptV2],
) -> Mapping[str, ValidationReceiptV2]:
    """Select the highest verified terminal attempt deterministically per slot."""

    selected: dict[str, ValidationReceiptV2] = {}
    seen_attempts: set[tuple[str, int]] = set()
    for receipt in receipts:
        verify_validation_v2_receipt(receipt)
        key = (receipt.slot_id, receipt.attempt_number)
        if key in seen_attempts:
            raise ValueError("duplicate validation V2 receipt attempt")
        seen_attempts.add(key)
        current = selected.get(receipt.slot_id)
        if current is None or receipt.attempt_number > current.attempt_number:
            selected[receipt.slot_id] = receipt
    return MappingProxyType(selected)


def verify_validation_v2_receipts(
    receipts: Sequence[ValidationReceiptV2],
    *,
    expected_slot_ids: Sequence[str],
    runner_version: str,
) -> ValidationV2ReceiptReport:
    """Verify terminal selection and report receipts separately from computation."""

    expected = tuple(expected_slot_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("planned slot IDs must be unique")
    selected = select_terminal_attempts_v2(receipts)
    if set(selected) != set(expected):
        raise ValueError("programme receipts have missing or extra terminal slots")
    attempts: set[str] = set()
    inputs: set[str] = set()
    results: set[str] = set()
    programmes = {receipt.programme_id for receipt in selected.values()}
    if len(programmes) != 1:
        raise ValueError("selected receipts cross programme identities")
    completed = not_evaluated = inconclusive = failed = abandoned = 0
    frozen_runners = {slot.slot_id: slot.kind.value for slot in VALIDATION_SLOT_ROSTER}
    for slot_id in expected:
        receipt = selected[slot_id]
        if receipt.runner_version != runner_version:
            raise ValueError("receipt uses the wrong runner version")
        expected_runner = frozen_runners.get(slot_id)
        if expected_runner is not None and receipt.runner_kind != expected_runner:
            raise ValueError("receipt uses the wrong runner kind")
        if not receipt.attempt_sha256 or not receipt.input_sha256 or not receipt.result_sha256:
            raise ValueError("receipt lacks attempt/input/result identities")
        if receipt.attempt_sha256 in attempts or receipt.input_sha256 in inputs:
            raise ValueError("receipt attempt/input fanout is forbidden")
        if receipt.result_sha256 in results:
            raise ValueError("receipt result fanout is forbidden")
        attempts.add(receipt.attempt_sha256)
        inputs.add(receipt.input_sha256)
        results.add(receipt.result_sha256)
        if receipt.computation_completed:
            if not receipt.metrics:
                raise ValueError("completed receipt omits slot metrics")
            completed += 1
            if receipt.decision == "inconclusive":
                inconclusive += 1
        if receipt.decision == "not_evaluated":
            not_evaluated += 1
        if receipt.execution_status == "failed":
            failed += 1
        if receipt.execution_status == "abandoned":
            abandoned += 1
    for slot_id in expected:
        receipt = selected[slot_id]
        if receipt.parent_slot_id is None:
            continue
        parent = selected.get(receipt.parent_slot_id)
        if parent is None:
            raise ValueError("selected child receipt is missing its selected parent")
        if (
            receipt.parent_attempt_sha256 != parent.attempt_sha256
            or receipt.parent_result_sha256 != parent.result_sha256
        ):
            raise ValueError("selected child receipt binds a stale parent retry")
    return ValidationV2ReceiptReport(
        planned_slots=len(expected),
        attempted_slots=len(selected),
        completed_slot_computations=completed,
        not_evaluated_slots=not_evaluated,
        inconclusive_slot_computations=inconclusive,
        failed_slots=failed,
        abandoned_slots=abandoned,
        receipt_count=len(receipts),
    )


def verify_validation_programme_v2(
    results: Sequence[object],
    receipts: Sequence[ValidationReceiptV2],
    *,
    runner_version: str,
) -> ValidationV2ReceiptReport:
    """Reconcile complete slot computations with their selected receipts."""

    from market_structure_lab.research.validation_v2 import (  # local: avoids import cycle
        ValidationSlotComputationResultV2,
        verify_slot_results_v2,
    )

    if any(not isinstance(item, ValidationSlotComputationResultV2) for item in results):
        raise TypeError("programme results must be V2 slot computation results")
    typed_results = tuple(
        item for item in results if isinstance(item, ValidationSlotComputationResultV2)
    )
    verify_slot_results_v2(typed_results, runner_version=runner_version)
    expected = tuple(item.slot_id for item in typed_results)
    report = verify_validation_v2_receipts(
        receipts,
        expected_slot_ids=expected,
        runner_version=runner_version,
    )
    selected = select_terminal_attempts_v2(receipts)
    for result in typed_results:
        receipt = selected[result.slot_id]
        if (
            receipt.attempt_number != result.attempt_number
            or receipt.attempt_sha256 != result.attempt_sha256
            or receipt.input_sha256 != result.input_sha256
            or receipt.evidence_sha256 != result.evidence_sha256
            or receipt.result_sha256 != result.result_sha256
            or receipt.parent_slot_id
            != next(
                (
                    slot.parent_slot_id
                    for slot in VALIDATION_SLOT_ROSTER
                    if slot.slot_id == result.slot_id
                ),
                None,
            )
            or receipt.parent_attempt_sha256 != result.parent_attempt_sha256
            or receipt.parent_result_sha256 != result.parent_result_sha256
            or receipt.runner_kind != result.runner_kind
            or receipt.execution_status != result.execution_status
            or receipt.decision != result.decision
            or receipt.computation_completed != result.computation_completed
            or receipt.reason != result.reason
            or receipt.p_value != result.p_value
            or dict(receipt.metrics) != dict(result.metrics)
        ):
            raise ValueError("selected receipt differs from its slot computation result")
    return report


__all__ = [
    "ValidationReceiptV2",
    "ValidationV2ReceiptReport",
    "publish_validation_v2_receipt",
    "publish_validation_v2_receipts",
    "select_terminal_attempts_v2",
    "verify_validation_v2_receipt",
    "verify_validation_v2_receipts",
    "verify_validation_programme_v2",
]
