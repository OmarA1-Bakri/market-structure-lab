"""Immutable additive receipts for Phase 5 V2 slot attempts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType

from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER

_MAX_RECEIPT_BYTES = 256 * 1024
_SCHEMA = "validation-slot-receipt-v2"


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
    result_sha256: str
    execution_status: str
    decision: str
    computation_completed: bool
    metrics: Mapping[str, object]
    receipt_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("receipt attempt_number must be positive")
        if self.computation_completed and not self.metrics:
            raise ValueError("completed computation receipt requires metrics")
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
        "result_sha256",
        "execution_status",
        "decision",
        "computation_completed",
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
    attempt = int(payload["attempt_number"])
    destination = root / slot / f"attempt-{attempt:04d}.json"
    if path_exists_no_follow(destination):
        raise FileExistsError(f"refusing existing validation V2 receipt: {destination}")
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError:
            raise
        finally:
            Path(temporary_name).unlink(missing_ok=True)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return _receipt_from_payload(destination, receipt_payload, content)


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
    return ValidationReceiptV2(
        path=path,
        programme_id=str(payload["programme_id"]),
        slot_id=str(payload["slot_id"]),
        attempt_number=attempt_number,
        runner_kind=str(payload["runner_kind"]),
        runner_version=str(payload["runner_version"]),
        attempt_sha256=str(payload["attempt_sha256"]),
        input_sha256=str(payload["input_sha256"]),
        result_sha256=str(payload["result_sha256"]),
        execution_status=str(payload["execution_status"]),
        decision=str(payload["decision"]),
        computation_completed=bool(payload["computation_completed"]),
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
            or receipt.result_sha256 != result.result_sha256
            or receipt.runner_kind != result.runner_kind
            or receipt.execution_status != result.execution_status
            or receipt.decision != result.decision
            or receipt.computation_completed != result.computation_completed
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
