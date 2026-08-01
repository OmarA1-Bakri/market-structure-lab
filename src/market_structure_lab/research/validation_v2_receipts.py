"""Issuer-bound, immutable batch receipts for Phase 5 V2 slot attempts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import cast
import weakref

from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.fs_durability import (
    durable_move_no_replace,
    fsync_directory_posix,
)
from market_structure_lab.core.secure_windows import WindowsHandleFilesystem, WindowsOwnedTreeClaim
from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
from market_structure_lab.research.validation_v2 import (
    ValidationSlotComputationResultV2,
    verified_receiptable_validation_slot_result_payload_v2,
    verified_receiptable_validation_slot_result_payloads_v2,
)

_MAX_RECEIPT_BYTES = 256 * 1024
_MAX_BATCH_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_BATCH_RECEIPTS = len(VALIDATION_SLOT_ROSTER)
_MAX_LEDGER_RECEIPTS = 20_000
_MAX_LEDGER_COMMITS = 20_000
_MAX_LEDGER_ROOT_ENTRIES = _MAX_LEDGER_COMMITS
_SCHEMA = "validation-slot-receipt-v2"
_BATCH_SCHEMA = "validation-receipt-batch-manifest-v2"
_SLOT_ID = re.compile(r"^VS-[0-9]{4}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROGRAMME = re.compile(r"^(?:VPV2|TVPV2)-[a-f0-9]{64}$")
_RELATIVE_RECEIPT = re.compile(r"^(VS-[0-9]{4})/attempt-([0-9]{4,8})\.json$")
_CANONICAL_SLOT_IDS = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
_CANONICAL_SLOT_INDEX = {slot_id: index for index, slot_id in enumerate(_CANONICAL_SLOT_IDS)}
_RECEIPT_ISSUANCE: dict[int, object] = {}
_VERIFIED_RECEIPTS: dict[
    int,
    tuple[
        weakref.ReferenceType[ValidationReceiptV2],
        tuple[object, ...],
        ValidationSlotComputationResultV2,
        ValidationReceiptV2 | None,
        bytes,
        bytes,
    ],
] = {}


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_sha256_v2(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _receipt_payload_from_bytes_v2(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("validation V2 receipt bytes are invalid JSON") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA:
        raise ValueError("validation V2 receipt schema is invalid")
    digest = payload.get("receipt_sha256")
    preimage = dict(payload)
    preimage.pop("receipt_sha256", None)
    if digest != _sha256(_canonical(preimage)):
        raise ValueError("validation V2 receipt identity differs")
    return payload


def _bounded_directory_names_v2(
    root: Path,
    *,
    maximum: int,
    label: str,
) -> tuple[str, ...]:
    names: list[str] = []
    for entry in root.iterdir():
        names.append(entry.name)
        if len(names) > maximum:
            raise ValueError(f"{label} exceeds its authenticated directory bound")
    return tuple(names)


def _require_canonical_slot_order_v2(slot_ids: Sequence[str], *, label: str) -> None:
    if any(type(slot_id) is not str for slot_id in slot_ids):
        raise TypeError(f"{label} must contain exact slot ID strings")
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError(f"{label} must contain unique slot IDs")
    try:
        canonical = tuple(sorted(slot_ids, key=_CANONICAL_SLOT_INDEX.__getitem__))
    except KeyError as error:
        raise ValueError(f"{label} contains a slot outside the frozen roster") from error
    if tuple(slot_ids) != canonical:
        raise ValueError(f"{label} must follow frozen-roster canonical order")


def _verify_complete_batch_v2(
    commit_root: Path,
    *,
    expected_manifest_bytes: bytes | None = None,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    require_regular_directory(commit_root)
    manifest_path = commit_root / "manifest.json"
    manifest_bytes = read_bounded_regular(manifest_path, _MAX_BATCH_MANIFEST_BYTES)
    if expected_manifest_bytes is not None and manifest_bytes != expected_manifest_bytes:
        raise ValueError("validation V2 receipt batch manifest changed")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("validation V2 receipt batch manifest is invalid JSON") from error
    required_manifest = {
        "schema_version",
        "programme_id",
        "runner_version",
        "commit_index",
        "predecessor_commit_sha256",
        "receipt_count",
        "receipts",
        "batch_manifest_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != required_manifest:
        raise ValueError("validation V2 receipt batch manifest schema is invalid")
    if manifest["schema_version"] != _BATCH_SCHEMA:
        raise ValueError("validation V2 receipt batch manifest schema is invalid")
    programme_id = manifest["programme_id"]
    if type(programme_id) is not str or _PROGRAMME.fullmatch(programme_id) is None:
        raise ValueError("validation V2 receipt batch programme is invalid")
    if type(manifest["runner_version"]) is not str or not manifest["runner_version"]:
        raise ValueError("validation V2 receipt batch runner is invalid")
    commit_index = manifest["commit_index"]
    if type(commit_index) is not int or not 1 <= commit_index <= _MAX_LEDGER_COMMITS:
        raise ValueError("validation V2 receipt batch commit index is invalid")
    predecessor = manifest["predecessor_commit_sha256"]
    if predecessor is not None:
        _require_sha256_v2(predecessor, "predecessor_commit_sha256")
    receipts = manifest["receipts"]
    receipt_count = manifest["receipt_count"]
    if (
        type(receipts) is not list
        or type(receipt_count) is not int
        or receipt_count != len(receipts)
        or not 1 <= receipt_count <= _MAX_BATCH_RECEIPTS
    ):
        raise ValueError("validation V2 receipt batch cardinality is invalid")
    digest = manifest["batch_manifest_sha256"]
    preimage = dict(manifest)
    preimage.pop("batch_manifest_sha256")
    if digest != _sha256(_canonical(preimage)):
        raise ValueError("validation V2 receipt batch manifest identity differs")
    expected_root_name = f"{programme_id}-commit-{commit_index:08d}"
    if commit_root.name != expected_root_name:
        raise ValueError("validation V2 receipt batch commit path differs")

    entry_fields = {
        "relative_path",
        "slot_id",
        "attempt_number",
        "attempt_sha256",
        "result_sha256",
        "receipt_sha256",
        "receipt_file_sha256",
        "predecessor_receipt_sha256",
    }
    relative_paths: set[str] = set()
    payloads: list[dict[str, object]] = []
    expected_by_slot: dict[str, set[str]] = {}
    for entry in receipts:
        if not isinstance(entry, dict) or set(entry) != entry_fields:
            raise ValueError("validation V2 receipt batch member schema is invalid")
        relative = entry["relative_path"]
        match = _RELATIVE_RECEIPT.fullmatch(relative) if type(relative) is str else None
        if match is None or relative in relative_paths:
            raise ValueError("validation V2 receipt batch member path is invalid or duplicated")
        relative_paths.add(relative)
        slot_id, attempt_text = match.groups()
        attempt_number = entry["attempt_number"]
        if (
            entry["slot_id"] != slot_id
            or type(attempt_number) is not int
            or attempt_number != int(attempt_text)
        ):
            raise ValueError("validation V2 receipt batch member identity differs from its path")
        for name in (
            "attempt_sha256",
            "result_sha256",
            "receipt_sha256",
            "receipt_file_sha256",
        ):
            _require_sha256_v2(entry[name], name)
        if entry["predecessor_receipt_sha256"] is not None:
            _require_sha256_v2(
                entry["predecessor_receipt_sha256"],
                "predecessor_receipt_sha256",
            )
        try:
            content = read_bounded_regular(commit_root / relative, _MAX_RECEIPT_BYTES)
        except FileNotFoundError as exc:
            raise ValueError("validation V2 receipt batch member is missing") from exc
        if _sha256(content) != entry["receipt_file_sha256"]:
            raise ValueError("validation V2 receipt batch member bytes changed")
        payload = _receipt_payload_from_bytes_v2(content)
        if any(
            payload.get(name) != entry[name]
            for name in (
                "slot_id",
                "attempt_number",
                "attempt_sha256",
                "result_sha256",
                "receipt_sha256",
                "predecessor_receipt_sha256",
            )
        ):
            raise ValueError("validation V2 receipt batch member payload differs")
        if (
            payload.get("programme_id") != programme_id
            or payload.get("runner_version") != manifest["runner_version"]
        ):
            raise ValueError(
                "validation V2 receipt batch member programme or runner differs from manifest"
            )
        payloads.append(payload)
        expected_by_slot.setdefault(slot_id, set()).add(Path(relative).name)

    expected_top_names = {"manifest.json", *expected_by_slot}
    actual_top_names = _bounded_directory_names_v2(
        commit_root,
        maximum=len(expected_top_names),
        label="validation V2 receipt batch",
    )
    if set(actual_top_names) != expected_top_names:
        raise ValueError("validation V2 receipt batch has missing or extra members")
    for slot_id, expected_names in expected_by_slot.items():
        slot_root = commit_root / slot_id
        require_regular_directory(slot_root)
        actual_slot_names = _bounded_directory_names_v2(
            slot_root,
            maximum=len(expected_names),
            label="validation V2 receipt slot batch",
        )
        if set(actual_slot_names) != expected_names:
            raise ValueError("validation V2 receipt batch has missing or extra slot members")
    return manifest, tuple(payloads)


def _load_programme_ledger_v2(root: Path, programme_id: str) -> _ProgrammeLedgerV2:
    require_regular_directory(root)
    prefix = f"{programme_id}-commit-"
    commits: list[tuple[int, Path]] = []
    for candidate_name in _bounded_directory_names_v2(
        root,
        maximum=_MAX_LEDGER_ROOT_ENTRIES,
        label="validation V2 programme ledger root",
    ):
        candidate = root / candidate_name
        if not candidate.name.startswith(prefix):
            continue
        suffix = candidate.name.removeprefix(prefix)
        if len(suffix) != 8 or not suffix.isdigit():
            raise ValueError("validation V2 programme ledger commit path is invalid")
        commits.append((int(suffix), candidate))
        if len(commits) > _MAX_LEDGER_COMMITS:
            raise ValueError("validation V2 programme ledger exceeds the commit bound")
    commits.sort(key=lambda item: item[0])
    if tuple(index for index, _path in commits) != tuple(range(1, len(commits) + 1)):
        raise ValueError("validation V2 programme ledger commit sequence has a gap")
    heads: dict[str, _LedgerHeadV2] = {}
    attempts: list[Mapping[str, object]] = []
    roots: list[Path] = []
    predecessor_commit: str | None = None
    ledger_runner_version: str | None = None
    first_attempt_count = 0
    for index, commit_root in commits:
        manifest, payloads = _verify_complete_batch_v2(commit_root)
        if manifest["programme_id"] != programme_id or manifest["commit_index"] != index:
            raise ValueError("validation V2 programme ledger commit identity differs")
        if manifest["predecessor_commit_sha256"] != predecessor_commit:
            raise ValueError("validation V2 programme ledger predecessor chain differs")
        manifest_runner_version = manifest["runner_version"]
        if type(manifest_runner_version) is not str:
            raise TypeError("validation V2 programme ledger runner shape is invalid")
        if ledger_runner_version is None:
            ledger_runner_version = manifest_runner_version
        elif manifest_runner_version != ledger_runner_version:
            raise ValueError("validation V2 programme ledger runner version drifted")
        for payload in payloads:
            slot_id = payload["slot_id"]
            attempt_number = payload["attempt_number"]
            if type(slot_id) is not str or type(attempt_number) is not int:
                raise TypeError("validation V2 programme ledger receipt shape is invalid")
            current = heads.get(slot_id)
            predecessor_receipt = payload["predecessor_receipt_sha256"]
            if current is None:
                if attempt_number != 1 or predecessor_receipt is not None:
                    raise ValueError("validation V2 programme ledger slot chain must start at one")
                if (
                    first_attempt_count >= len(_CANONICAL_SLOT_IDS)
                    or slot_id != _CANONICAL_SLOT_IDS[first_attempt_count]
                ):
                    raise ValueError(
                        "validation V2 programme ledger first attempts violate the canonical prefix"
                    )
                first_attempt_count += 1
            elif (
                attempt_number != current.attempt_number + 1
                or predecessor_receipt != current.receipt_sha256
            ):
                raise ValueError("validation V2 programme ledger slot head forked or became stale")
            heads[slot_id] = _LedgerHeadV2(
                attempt_number=attempt_number,
                receipt_sha256=_require_sha256_v2(payload["receipt_sha256"], "receipt_sha256"),
                result_sha256=_require_sha256_v2(payload["result_sha256"], "result_sha256"),
                payload=MappingProxyType(dict(payload)),
            )
            attempts.append(MappingProxyType(dict(payload)))
            if len(attempts) > _MAX_LEDGER_RECEIPTS:
                raise ValueError("validation V2 programme ledger exceeds the receipt bound")
        predecessor_commit = _require_sha256_v2(
            manifest["batch_manifest_sha256"], "batch_manifest_sha256"
        )
        roots.append(commit_root)
    return _ProgrammeLedgerV2(
        commit_count=len(commits),
        last_commit_sha256=predecessor_commit,
        runner_version=ledger_runner_version,
        first_attempt_count=first_attempt_count,
        heads=MappingProxyType(heads),
        attempt_payloads=tuple(attempts),
        commit_roots=tuple(roots),
    )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationReceiptV2:
    path: Path
    batch_manifest_path: Path
    batch_manifest_sha256: str
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
    predecessor_receipt_sha256: str | None
    execution_status: str
    decision: str
    computation_completed: bool
    reason: str
    p_value: float | None
    metrics: Mapping[str, object]
    receipt_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        token = _RECEIPT_ISSUANCE.pop(id(_factory_token), None)
        if _factory_token is None or token is not _factory_token:
            raise TypeError("ValidationReceiptV2 requires its issuer factory")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        _validate_receipt_shape_v2(self)


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
    attempted_attempts: int
    completed_attempt_computations: int
    not_evaluated_attempts: int
    inconclusive_attempt_computations: int
    failed_attempts: int
    abandoned_attempts: int


@dataclass(frozen=True, slots=True)
class _LedgerHeadV2:
    attempt_number: int
    receipt_sha256: str
    result_sha256: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _ProgrammeLedgerV2:
    commit_count: int
    last_commit_sha256: str | None
    runner_version: str | None
    first_attempt_count: int
    heads: Mapping[str, _LedgerHeadV2]
    attempt_payloads: tuple[Mapping[str, object], ...]
    commit_roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _LocalReceiptVerificationV2:
    receipt: ValidationReceiptV2
    result_payload: Mapping[str, object]
    receipt_payload: Mapping[str, object]
    manifest_bytes: bytes
    commit_root: Path


def _slot_for_receipt_v2(receipt: ValidationReceiptV2):  # type: ignore[no-untyped-def]
    slot = next((item for item in VALIDATION_SLOT_ROSTER if item.slot_id == receipt.slot_id), None)
    if slot is None:
        raise ValueError("receipt slot is outside the frozen roster")
    return slot


def _validate_receipt_shape_v2(receipt: ValidationReceiptV2) -> None:
    if not isinstance(receipt.path, Path) or not isinstance(receipt.batch_manifest_path, Path):
        raise TypeError("receipt paths must be concrete Path values")
    for value, label in (
        (receipt.programme_id, "programme_id"),
        (receipt.slot_id, "slot_id"),
        (receipt.runner_kind, "runner_kind"),
        (receipt.runner_version, "runner_version"),
        (receipt.attempt_sha256, "attempt_sha256"),
        (receipt.input_sha256, "input_sha256"),
        (receipt.evidence_sha256, "evidence_sha256"),
        (receipt.result_sha256, "result_sha256"),
        (receipt.execution_status, "execution_status"),
        (receipt.decision, "decision"),
        (receipt.reason, "reason"),
        (receipt.receipt_sha256, "receipt_sha256"),
        (receipt.batch_manifest_sha256, "batch_manifest_sha256"),
    ):
        if type(value) is not str:
            raise TypeError(f"receipt {label} must be an exact string")
    if _PROGRAMME.fullmatch(receipt.programme_id) is None:
        raise ValueError("receipt programme_id is invalid")
    if _SLOT_ID.fullmatch(receipt.slot_id) is None:
        raise ValueError("receipt slot_id is invalid")
    slot = _slot_for_receipt_v2(receipt)
    if receipt.runner_kind != slot.kind.value:
        raise ValueError("receipt runner kind differs from frozen slot")
    for value, label in (
        (receipt.attempt_sha256, "attempt_sha256"),
        (receipt.input_sha256, "input_sha256"),
        (receipt.evidence_sha256, "evidence_sha256"),
        (receipt.result_sha256, "result_sha256"),
        (receipt.receipt_sha256, "receipt_sha256"),
        (receipt.batch_manifest_sha256, "batch_manifest_sha256"),
    ):
        if _SHA256.fullmatch(value) is None:
            raise ValueError(f"receipt {label} is invalid")
    for parent in (
        receipt.parent_attempt_sha256,
        receipt.parent_result_sha256,
        receipt.predecessor_receipt_sha256,
    ):
        if parent is not None and (type(parent) is not str or _SHA256.fullmatch(parent) is None):
            raise ValueError("receipt parent/predecessor identity is invalid")
    if receipt.parent_slot_id != slot.parent_slot_id:
        raise ValueError("receipt parent slot differs from frozen roster")
    if slot.parent_slot_id is None and (
        receipt.parent_attempt_sha256 is not None or receipt.parent_result_sha256 is not None
    ):
        raise ValueError("root receipt cannot have parent attempt/result identities")
    if slot.parent_slot_id is not None and (
        receipt.parent_attempt_sha256 is None or receipt.parent_result_sha256 is None
    ):
        raise ValueError("child receipt requires parent attempt/result identities")
    if type(receipt.attempt_number) is not int or receipt.attempt_number < 1:
        raise ValueError("receipt attempt_number must be a positive exact integer")
    if receipt.attempt_number == 1 and receipt.predecessor_receipt_sha256 is not None:
        raise ValueError("first receipt attempt cannot bind a predecessor")
    if receipt.attempt_number > 1 and receipt.predecessor_receipt_sha256 is None:
        raise ValueError("retry receipt requires a predecessor")
    if type(receipt.computation_completed) is not bool:
        raise TypeError("receipt completion flag must be an exact bool")
    if type(receipt.metrics) is not MappingProxyType:
        raise TypeError("receipt metrics must be a sealed exact mapping")
    if receipt.computation_completed and not receipt.metrics:
        raise ValueError("completed computation receipt requires metrics")
    if receipt.execution_status == "completed":
        if receipt.decision == "not_evaluated":
            raise ValueError("completed receipt cannot be not_evaluated")
    elif receipt.execution_status in {"failed", "abandoned"}:
        if receipt.decision != "not_evaluated" or receipt.computation_completed:
            raise ValueError("failed/abandoned receipt must be not_evaluated")
    else:
        raise ValueError("receipt execution status is invalid")
    if receipt.p_value is not None and (
        type(receipt.p_value) is not float
        or not receipt.p_value == receipt.p_value
        or not 0.0 <= receipt.p_value <= 1.0
    ):
        raise ValueError("receipt p_value must be finite and in [0, 1]")
    if receipt.path.parent.name != receipt.slot_id:
        raise ValueError("receipt path does not match its slot")
    if receipt.batch_manifest_path != receipt.path.parent.parent / "manifest.json":
        raise ValueError("receipt manifest path differs from its immutable batch")
    expected_commit_prefix = f"{receipt.programme_id}-commit-"
    if not receipt.path.parent.parent.name.startswith(expected_commit_prefix):
        raise ValueError("receipt path is outside its canonical programme ledger")


def _receipt_snapshot_v2(receipt: ValidationReceiptV2) -> tuple[object, ...]:
    _validate_receipt_shape_v2(receipt)
    return (
        receipt.path,
        receipt.batch_manifest_path,
        receipt.batch_manifest_sha256,
        receipt.programme_id,
        receipt.slot_id,
        receipt.attempt_number,
        receipt.runner_kind,
        receipt.runner_version,
        receipt.attempt_sha256,
        receipt.input_sha256,
        receipt.evidence_sha256,
        receipt.result_sha256,
        receipt.parent_slot_id,
        receipt.parent_attempt_sha256,
        receipt.parent_result_sha256,
        receipt.predecessor_receipt_sha256,
        receipt.execution_status,
        receipt.decision,
        receipt.computation_completed,
        receipt.reason,
        receipt.p_value,
        tuple(sorted(receipt.metrics.items())),
        receipt.receipt_sha256,
        receipt.canonical_bytes,
    )


def _receipt_payload_v2(
    result_payload: Mapping[str, object],
    predecessor: ValidationReceiptV2 | None,
) -> tuple[dict[str, object], bytes]:
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
    if not required <= set(result_payload):
        raise ValueError("slot result is missing receipt identity or metrics fields")
    receipt_payload = {
        "schema_version": _SCHEMA,
        "result_schema_version": result_payload["schema_version"],
        **{
            name: (
                dict(cast(Mapping[str, object], result_payload[name]))
                if name == "metrics"
                else result_payload[name]
            )
            for name in sorted(required - {"schema_version"})
        },
        "predecessor_receipt_sha256": (
            predecessor.receipt_sha256 if predecessor is not None else None
        ),
    }
    preimage = _canonical(receipt_payload)
    receipt_payload["receipt_sha256"] = _sha256(preimage)
    content = _canonical(receipt_payload)
    if len(content) > _MAX_RECEIPT_BYTES:
        raise ValueError("validation V2 receipt exceeds the byte ceiling")
    return receipt_payload, content


def _verify_predecessor_v2(
    *,
    result_payload: Mapping[str, object],
    predecessor: ValidationReceiptV2 | None,
) -> None:
    attempt_number = result_payload["attempt_number"]
    if type(attempt_number) is not int:
        raise TypeError("result attempt_number must be an exact integer")
    if attempt_number == 1:
        if predecessor is not None:
            raise ValueError("first receipt attempt cannot bind a predecessor")
        return
    if predecessor is None:
        raise ValueError("retry receipt requires its exact registered predecessor")
    verify_validation_v2_receipt(predecessor)
    if (
        predecessor.programme_id != result_payload["programme_id"]
        or predecessor.slot_id != result_payload["slot_id"]
        or predecessor.attempt_number != attempt_number - 1
    ):
        raise ValueError("retry receipt predecessor programme, slot, or attempt differs")


def publish_validation_v2_receipt(
    root: Path,
    result: ValidationSlotComputationResultV2,
    *,
    predecessor_receipt: ValidationReceiptV2 | None = None,
) -> ValidationReceiptV2:
    """Publish one result as a one-member atomic authenticated batch."""

    receipts = publish_validation_v2_receipts(
        root,
        (result,),
        predecessor_receipts=(predecessor_receipt,),
    )
    return receipts[0]


def publish_validation_v2_receipts(
    root: Path,
    results: Sequence[ValidationSlotComputationResultV2],
    *,
    predecessor_receipts: Sequence[ValidationReceiptV2 | None] | None = None,
) -> tuple[ValidationReceiptV2, ...]:
    """Atomically publish one independently verified receipt batch."""

    if type(results) not in {tuple, list}:
        raise TypeError("receipt batch results must be an exact tuple or list")
    result_count = len(results)
    if not 1 <= result_count <= _MAX_BATCH_RECEIPTS:
        raise ValueError("receipt batch cardinality is outside the frozen bound")
    frozen_results = tuple(results)
    for result in frozen_results:
        if type(result) is not ValidationSlotComputationResultV2:
            raise TypeError("receipt result must be the exact factory-issued registered result")
    verified_payloads = verified_receiptable_validation_slot_result_payloads_v2(frozen_results)
    verified_results = tuple(zip(frozen_results, verified_payloads, strict=True))
    if predecessor_receipts is None:
        predecessors: tuple[ValidationReceiptV2 | None, ...] = (None,) * len(verified_results)
    else:
        if type(predecessor_receipts) not in {tuple, list}:
            raise TypeError("receipt predecessors must be an exact tuple or list")
        if len(predecessor_receipts) != len(verified_results):
            raise ValueError("receipt predecessor sequence differs from batch cardinality")
        predecessors = tuple(predecessor_receipts)
    seen: set[tuple[str, int]] = set()
    prepared: list[
        tuple[
            ValidationSlotComputationResultV2,
            ValidationReceiptV2 | None,
            dict[str, object],
            bytes,
        ]
    ] = []
    programmes: set[str] = set()
    runners: set[str] = set()
    for (result, result_payload), predecessor in zip(verified_results, predecessors, strict=True):
        slot_id = result_payload["slot_id"]
        attempt_number = result_payload["attempt_number"]
        if type(slot_id) is not str or type(attempt_number) is not int:
            raise TypeError("registered result slot/attempt shape is invalid")
        key = (slot_id, attempt_number)
        if key in seen:
            raise ValueError("receipt batch contains a duplicate slot attempt")
        seen.add(key)
        if predecessor is not None and type(predecessor) is not ValidationReceiptV2:
            raise TypeError("receipt predecessor must be the exact factory-issued type")
        _verify_predecessor_v2(result_payload=result_payload, predecessor=predecessor)
        payload, content = _receipt_payload_v2(result_payload, predecessor)
        prepared.append((result, predecessor, payload, content))
        programmes.add(str(result_payload["programme_id"]))
        runners.add(str(result_payload["runner_version"]))
    _require_canonical_slot_order_v2(
        tuple(str(payload["slot_id"]) for _result, _predecessor, payload, _content in prepared),
        label="receipt batch slots",
    )
    if len(programmes) != 1 or len(runners) != 1:
        raise ValueError("receipt batch crosses programme or runner identities")
    programme_id = next(iter(programmes))
    root = Path(root)
    require_regular_directory(root)
    ledger = _load_programme_ledger_v2(root, programme_id)
    batch_runner_version = next(iter(runners))
    if ledger.runner_version is not None and batch_runner_version != ledger.runner_version:
        raise ValueError("validation V2 programme ledger runner version drifted")
    next_first_attempt = ledger.first_attempt_count
    for _result, _predecessor, payload, _content in prepared:
        slot_id = str(payload["slot_id"])
        attempt_number = payload["attempt_number"]
        if type(attempt_number) is not int:
            raise TypeError("receipt attempt_number must be an exact integer")
        current = ledger.heads.get(slot_id)
        existing_attempt = next(
            (
                attempt
                for attempt in ledger.attempt_payloads
                if attempt["slot_id"] == slot_id and attempt["attempt_number"] == attempt_number
            ),
            None,
        )
        if existing_attempt is not None:
            if existing_attempt["receipt_sha256"] == payload["receipt_sha256"]:
                raise FileExistsError("canonical receipt attempt is already published")
            raise ValueError("canonical slot attempt already exists with different evidence")
        if attempt_number == 1:
            if current is not None:
                raise ValueError("canonical slot head already exists for attempt one")
            if (
                next_first_attempt >= len(_CANONICAL_SLOT_IDS)
                or slot_id != _CANONICAL_SLOT_IDS[next_first_attempt]
            ):
                raise ValueError(
                    "validation V2 programme ledger first attempts must extend the canonical prefix"
                )
            next_first_attempt += 1
        elif current is None:
            raise ValueError("retry receipt has no canonical slot head")
        elif (
            attempt_number != current.attempt_number + 1
            or payload["predecessor_receipt_sha256"] != current.receipt_sha256
        ):
            raise ValueError("retry receipt binds a stale canonical slot head")
    if ledger.commit_count >= _MAX_LEDGER_COMMITS:
        raise ValueError("validation V2 programme ledger commit bound is exhausted")
    if len(ledger.attempt_payloads) + len(prepared) > _MAX_LEDGER_RECEIPTS:
        raise ValueError("validation V2 programme ledger receipt bound is exhausted")

    entries = [
        {
            "relative_path": f"{payload['slot_id']}/attempt-{payload['attempt_number']:04d}.json",
            "slot_id": payload["slot_id"],
            "attempt_number": payload["attempt_number"],
            "attempt_sha256": payload["attempt_sha256"],
            "result_sha256": payload["result_sha256"],
            "receipt_sha256": payload["receipt_sha256"],
            "receipt_file_sha256": _sha256(content),
            "predecessor_receipt_sha256": payload["predecessor_receipt_sha256"],
        }
        for _result, _predecessor, payload, content in prepared
    ]
    commit_index = ledger.commit_count + 1
    manifest_payload: dict[str, object] = {
        "schema_version": _BATCH_SCHEMA,
        "programme_id": programme_id,
        "runner_version": batch_runner_version,
        "commit_index": commit_index,
        "predecessor_commit_sha256": ledger.last_commit_sha256,
        "receipt_count": len(entries),
        "receipts": entries,
    }
    manifest_sha = _sha256(_canonical(manifest_payload))
    manifest_payload["batch_manifest_sha256"] = manifest_sha
    manifest_content = _canonical(manifest_payload)
    if len(manifest_content) > _MAX_BATCH_MANIFEST_BYTES:
        raise ValueError("validation V2 receipt batch manifest exceeds the byte ceiling")

    destination = root / f"{programme_id}-commit-{commit_index:08d}"
    if path_exists_no_follow(destination):
        raise FileExistsError(f"refusing existing validation V2 receipt batch: {destination}")
    stage = Path(tempfile.mkdtemp(prefix=".receipt-batch-", suffix=".tmp", dir=root))
    moved = False
    try:
        for _result, _predecessor, payload, content in prepared:
            relative = (
                Path(str(payload["slot_id"])) / f"attempt-{payload['attempt_number']:04d}.json"
            )
            _write_staged_receipt_v2(stage, relative, content)
        _write_staged_receipt_v2(stage, Path("manifest.json"), manifest_content)
        try:
            _commit_staged_receipt_batch_v2(stage, destination)
            moved = True
        except Exception:
            if (
                _is_windows_platform()
                or path_exists_no_follow(stage)
                or not path_exists_no_follow(destination)
            ):
                raise
            _verify_complete_batch_v2(
                destination,
                expected_manifest_bytes=manifest_content,
            )
            _recover_posix_committed_receipt_batch_v2(destination)
            moved = True
    finally:
        if not moved and path_exists_no_follow(stage):
            shutil.rmtree(stage)

    receipts: list[ValidationReceiptV2] = []
    for result, predecessor, payload, content in prepared:
        path = (
            destination / str(payload["slot_id"]) / f"attempt-{payload['attempt_number']:04d}.json"
        )
        receipt = _issue_validation_receipt_v2(
            path=path,
            manifest_path=destination / "manifest.json",
            manifest_sha256=manifest_sha,
            payload=payload,
            content=content,
            manifest_content=manifest_content,
            result=result,
            predecessor=predecessor,
        )
        receipts.append(receipt)
    return tuple(receipts)


def _write_staged_receipt_v2(stage: Path, relative: Path, content: bytes) -> Path:
    destination = stage / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return destination


def _commit_staged_receipt_batch_v2(stage: Path, destination: Path) -> None:
    if _is_windows_platform():
        with _claim_windows_owned_tree(stage) as claim:
            try:
                claim.move_to(destination)
            except Exception:
                claim.delete_exact()
                raise
        return
    for directory in sorted(
        (path for path in stage.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        fsync_directory_posix(directory)
    fsync_directory_posix(stage)
    durable_move_no_replace(stage, destination)


def _recover_posix_committed_receipt_batch_v2(destination: Path) -> None:
    """Complete the durability barrier after an authenticated post-rename failure."""

    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        fsync_directory_posix(directory)
    fsync_directory_posix(destination)
    fsync_directory_posix(destination.parent)


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _claim_windows_owned_tree(path: Path) -> WindowsOwnedTreeClaim:
    return WindowsHandleFilesystem().claim_owned_tree(path)


def _issue_validation_receipt_v2(
    *,
    path: Path,
    manifest_path: Path,
    manifest_sha256: str,
    payload: Mapping[str, object],
    content: bytes,
    manifest_content: bytes,
    result: ValidationSlotComputationResultV2,
    predecessor: ValidationReceiptV2 | None,
) -> ValidationReceiptV2:
    metrics = payload["metrics"]
    if type(metrics) is not dict:
        raise TypeError("receipt metrics must be an exact object")
    attempt_number = payload["attempt_number"]
    if type(attempt_number) is not int:
        raise TypeError("receipt attempt_number must be an exact integer")
    raw_p_value = payload["p_value"]
    if raw_p_value is not None and type(raw_p_value) is not float:
        raise TypeError("receipt p_value must be an exact float or null")
    token = object()
    _RECEIPT_ISSUANCE[id(token)] = token
    try:
        receipt = ValidationReceiptV2(
            path=path,
            batch_manifest_path=manifest_path,
            batch_manifest_sha256=manifest_sha256,
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
            predecessor_receipt_sha256=(
                str(payload["predecessor_receipt_sha256"])
                if payload["predecessor_receipt_sha256"] is not None
                else None
            ),
            execution_status=str(payload["execution_status"]),
            decision=str(payload["decision"]),
            computation_completed=bool(payload["computation_completed"]),
            reason=str(payload["reason"]),
            p_value=raw_p_value,
            metrics=metrics,
            receipt_sha256=str(payload["receipt_sha256"]),
            canonical_bytes=content,
            _factory_token=token,
        )
    finally:
        _RECEIPT_ISSUANCE.pop(id(token), None)
    identifier = id(receipt)

    def cleanup(reference: weakref.ReferenceType[ValidationReceiptV2]) -> None:
        current = _VERIFIED_RECEIPTS.get(identifier)
        if current is not None and current[0] is reference:
            _VERIFIED_RECEIPTS.pop(identifier, None)

    _VERIFIED_RECEIPTS[identifier] = (
        weakref.ref(receipt, cleanup),
        _receipt_snapshot_v2(receipt),
        result,
        predecessor,
        content,
        manifest_content,
    )
    return receipt


def _registered_receipt_result_v2(
    receipt: ValidationReceiptV2,
) -> ValidationSlotComputationResultV2:
    if type(receipt) is not ValidationReceiptV2:
        raise TypeError("receipt must be the exact factory-issued type")
    registered = _VERIFIED_RECEIPTS.get(id(receipt))
    if registered is None or registered[0]() is not receipt:
        raise ValueError("receipt is not the registered original factory-issued receipt")
    if _receipt_snapshot_v2(receipt) != registered[1]:
        raise ValueError("receipt differs from its registered original")
    return registered[2]


def _verify_local_receipt_v2(
    receipt: ValidationReceiptV2,
    *,
    registered_result_payload: Mapping[str, object] | None = None,
) -> _LocalReceiptVerificationV2:
    registered_result = _registered_receipt_result_v2(receipt)
    registered = _VERIFIED_RECEIPTS[id(receipt)]
    if registered_result_payload is None:
        registered_result_payload = verified_receiptable_validation_slot_result_payload_v2(
            registered_result
        )
    current = read_bounded_regular(receipt.path, _MAX_RECEIPT_BYTES)
    if current != registered[4] or current != receipt.canonical_bytes:
        raise ValueError("validation V2 receipt bytes changed")
    payload = _receipt_payload_from_bytes_v2(current)
    if payload.get("receipt_sha256") != receipt.receipt_sha256:
        raise ValueError("validation V2 receipt identity differs")
    receipt_result_fields = {
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
    if payload.get("result_schema_version") != registered_result_payload["schema_version"] or any(
        payload.get(name) != registered_result_payload[name] for name in receipt_result_fields
    ):
        raise ValueError("validation V2 receipt result provenance differs")
    commit_root = receipt.batch_manifest_path.parent
    return _LocalReceiptVerificationV2(
        receipt=receipt,
        result_payload=registered_result_payload,
        receipt_payload=MappingProxyType(payload),
        manifest_bytes=registered[5],
        commit_root=commit_root,
    )


def _verify_receipt_manifest_membership_v2(
    local: _LocalReceiptVerificationV2,
    manifest: Mapping[str, object],
) -> None:
    receipt = local.receipt
    if manifest["batch_manifest_sha256"] != receipt.batch_manifest_sha256:
        raise ValueError("validation V2 receipt batch manifest identity differs")
    relative = receipt.path.relative_to(receipt.batch_manifest_path.parent).as_posix()
    manifest_receipts = manifest.get("receipts")
    if not isinstance(manifest_receipts, list):
        raise ValueError("validation V2 receipt batch manifest receipts are invalid")
    matching = [
        entry
        for entry in manifest_receipts
        if isinstance(entry, dict) and entry.get("relative_path") == relative
    ]
    if len(matching) != 1 or matching[0].get("receipt_sha256") != receipt.receipt_sha256:
        raise ValueError("receipt is not uniquely bound into its authenticated batch manifest")


def verify_validation_v2_receipt(receipt: ValidationReceiptV2) -> ValidationReceiptV2:
    """Reopen and validate one exact issuer-bound receipt and batch manifest."""

    local = _verify_local_receipt_v2(receipt)
    registered = _VERIFIED_RECEIPTS[id(receipt)]
    _verify_predecessor_v2(
        result_payload=local.result_payload,
        predecessor=registered[3],
    )
    manifest, _batch_payloads = _verify_complete_batch_v2(
        local.commit_root,
        expected_manifest_bytes=local.manifest_bytes,
    )
    _verify_receipt_manifest_membership_v2(local, manifest)
    ledger = _load_programme_ledger_v2(local.commit_root.parent, receipt.programme_id)
    if not any(
        payload["receipt_sha256"] == receipt.receipt_sha256 for payload in ledger.attempt_payloads
    ):
        raise ValueError("receipt is outside the canonical programme ledger")
    return receipt


def select_terminal_attempts_v2(
    receipts: Sequence[ValidationReceiptV2],
) -> Mapping[str, ValidationReceiptV2]:
    """Select highest attempts only after validating complete predecessor chains."""

    if type(receipts) not in {tuple, list}:
        raise TypeError("receipt selection requires an exact tuple or list")
    if len(receipts) > _MAX_LEDGER_RECEIPTS:
        raise ValueError("receipt selection exceeds the frozen receipt bound")
    frozen_receipts = tuple(receipts)
    registered_results = tuple(
        _registered_receipt_result_v2(receipt) for receipt in frozen_receipts
    )
    registered_payloads = verified_receiptable_validation_slot_result_payloads_v2(
        registered_results
    )
    locals_by_receipt = tuple(
        _verify_local_receipt_v2(
            receipt,
            registered_result_payload=result_payload,
        )
        for receipt, result_payload in zip(
            frozen_receipts,
            registered_payloads,
            strict=True,
        )
    )
    manifests: dict[Path, Mapping[str, object]] = {}
    manifest_bytes_by_root: dict[Path, bytes] = {}
    for local in locals_by_receipt:
        existing = manifest_bytes_by_root.get(local.commit_root)
        if existing is not None and existing != local.manifest_bytes:
            raise ValueError("receipts disagree about their immutable batch manifest")
        manifest_bytes_by_root[local.commit_root] = local.manifest_bytes
    for commit_root, manifest_bytes in manifest_bytes_by_root.items():
        manifest, _payloads = _verify_complete_batch_v2(
            commit_root,
            expected_manifest_bytes=manifest_bytes,
        )
        manifests[commit_root] = manifest
    ledgers: dict[tuple[Path, str], _ProgrammeLedgerV2] = {}
    for local in locals_by_receipt:
        _verify_receipt_manifest_membership_v2(local, manifests[local.commit_root])
        ledger_key = (local.commit_root.parent, local.receipt.programme_id)
        if ledger_key not in ledgers:
            ledgers[ledger_key] = _load_programme_ledger_v2(*ledger_key)
        if not any(
            payload["receipt_sha256"] == local.receipt.receipt_sha256
            for payload in ledgers[ledger_key].attempt_payloads
        ):
            raise ValueError("receipt is outside the canonical programme ledger")
    return _select_terminal_attempts_verified_v2(frozen_receipts)


def _select_terminal_attempts_verified_v2(
    receipts: Sequence[ValidationReceiptV2],
) -> Mapping[str, ValidationReceiptV2]:
    selected: dict[str, ValidationReceiptV2] = {}
    seen_attempts: set[tuple[str, int]] = set()
    by_receipt_sha: dict[str, ValidationReceiptV2] = {}
    for receipt in receipts:
        key = (receipt.slot_id, receipt.attempt_number)
        if key in seen_attempts:
            raise ValueError("duplicate validation V2 receipt attempt")
        seen_attempts.add(key)
        by_receipt_sha[receipt.receipt_sha256] = receipt
        current = selected.get(receipt.slot_id)
        if current is None or receipt.attempt_number > current.attempt_number:
            selected[receipt.slot_id] = receipt
    for receipt in receipts:
        if receipt.attempt_number == 1:
            continue
        predecessor = by_receipt_sha.get(receipt.predecessor_receipt_sha256 or "")
        if (
            predecessor is None
            or predecessor.slot_id != receipt.slot_id
            or predecessor.attempt_number != receipt.attempt_number - 1
        ):
            raise ValueError("receipt predecessor chain is incomplete")
    return MappingProxyType(selected)


def verify_validation_v2_receipts(
    receipts: Sequence[ValidationReceiptV2],
    *,
    expected_slot_ids: Sequence[str],
    runner_version: str,
) -> ValidationV2ReceiptReport:
    """Verify terminal selection and reconcile receipt/computation counts."""

    if type(expected_slot_ids) not in {tuple, list}:
        raise TypeError("planned slot IDs must be an exact tuple or list")
    if len(expected_slot_ids) > len(VALIDATION_SLOT_ROSTER):
        raise ValueError("planned slot IDs exceed the frozen roster bound")
    expected = tuple(expected_slot_ids)
    _require_canonical_slot_order_v2(expected, label="planned slot IDs")
    selected = select_terminal_attempts_v2(receipts)
    if set(selected) != set(expected):
        raise ValueError("programme receipts have missing or extra terminal slots")
    attempts: set[str] = set()
    inputs: set[str] = set()
    results: set[str] = set()
    programmes = {receipt.programme_id for receipt in selected.values()}
    if len(programmes) != 1:
        raise ValueError("selected receipts cross programme identities")
    roots = {receipt.path.parent.parent.parent for receipt in selected.values()}
    if len(roots) != 1:
        raise ValueError("selected receipts cross canonical programme roots")
    programme_id = next(iter(programmes))
    ledger = _load_programme_ledger_v2(next(iter(roots)), programme_id)
    if ledger.runner_version != runner_version:
        raise ValueError("canonical programme ledger uses the wrong runner version")
    if set(ledger.heads) != set(expected):
        raise ValueError("canonical programme ledger has missing or extra terminal slots")
    for slot_id in expected:
        if selected[slot_id].receipt_sha256 != ledger.heads[slot_id].receipt_sha256:
            raise ValueError("selected receipt is not the canonical programme slot head")

    frozen_runners = {slot.slot_id: slot.kind.value for slot in VALIDATION_SLOT_ROSTER}
    for payload in ledger.attempt_payloads:
        slot_id = str(payload["slot_id"])
        if payload["runner_version"] != runner_version:
            raise ValueError("historical receipt uses the wrong runner version")
        if payload["runner_kind"] != frozen_runners.get(slot_id):
            raise ValueError("historical receipt uses the wrong runner kind")
    completed = not_evaluated = inconclusive = failed = abandoned = 0
    for slot_id in expected:
        payload = ledger.heads[slot_id].payload
        if payload["runner_version"] != runner_version:
            raise ValueError("receipt uses the wrong runner version")
        if payload["runner_kind"] != frozen_runners.get(slot_id):
            raise ValueError("receipt uses the wrong runner kind")
        attempt_sha = str(payload["attempt_sha256"])
        input_sha = str(payload["input_sha256"])
        result_sha = str(payload["result_sha256"])
        if attempt_sha in attempts or input_sha in inputs:
            raise ValueError("receipt attempt/input fanout is forbidden")
        if result_sha in results:
            raise ValueError("receipt result fanout is forbidden")
        attempts.add(attempt_sha)
        inputs.add(input_sha)
        results.add(result_sha)
        if payload["computation_completed"] is True:
            completed += 1
            if payload["decision"] == "inconclusive":
                inconclusive += 1
        if payload["decision"] == "not_evaluated":
            not_evaluated += 1
        failed += payload["execution_status"] == "failed"
        abandoned += payload["execution_status"] == "abandoned"
    for slot_id in expected:
        payload = ledger.heads[slot_id].payload
        parent_slot_id = payload["parent_slot_id"]
        if parent_slot_id is None:
            continue
        parent = ledger.heads.get(str(parent_slot_id))
        if parent is None:
            raise ValueError("selected child receipt is missing its selected parent")
        if (
            payload["parent_attempt_sha256"] != parent.payload["attempt_sha256"]
            or payload["parent_result_sha256"] != parent.result_sha256
        ):
            raise ValueError("selected child receipt binds a stale parent retry")
    completed_attempts = sum(
        payload["computation_completed"] is True for payload in ledger.attempt_payloads
    )
    not_evaluated_attempts = sum(
        payload["decision"] == "not_evaluated" for payload in ledger.attempt_payloads
    )
    inconclusive_attempts = sum(
        payload["decision"] == "inconclusive" for payload in ledger.attempt_payloads
    )
    failed_attempts = sum(
        payload["execution_status"] == "failed" for payload in ledger.attempt_payloads
    )
    abandoned_attempts = sum(
        payload["execution_status"] == "abandoned" for payload in ledger.attempt_payloads
    )
    return ValidationV2ReceiptReport(
        planned_slots=len(expected),
        attempted_slots=len(selected),
        completed_slot_computations=completed,
        not_evaluated_slots=not_evaluated,
        inconclusive_slot_computations=inconclusive,
        failed_slots=failed,
        abandoned_slots=abandoned,
        receipt_count=len(ledger.attempt_payloads),
        attempted_attempts=len(ledger.attempt_payloads),
        completed_attempt_computations=completed_attempts,
        not_evaluated_attempts=not_evaluated_attempts,
        inconclusive_attempt_computations=inconclusive_attempts,
        failed_attempts=failed_attempts,
        abandoned_attempts=abandoned_attempts,
    )


def verify_validation_programme_v2(
    results: Sequence[object],
    receipts: Sequence[ValidationReceiptV2],
    *,
    runner_version: str,
) -> ValidationV2ReceiptReport:
    """Reconcile exact slot computations with their selected receipts."""

    from market_structure_lab.research.validation_v2 import verify_slot_results_v2

    if type(results) not in {tuple, list}:
        raise TypeError("programme results must be an exact tuple or list")
    if len(results) > len(VALIDATION_SLOT_ROSTER):
        raise ValueError("programme results exceed the frozen roster bound")
    if type(receipts) not in {tuple, list}:
        raise TypeError("programme receipts must be an exact tuple or list")
    if len(receipts) > _MAX_LEDGER_RECEIPTS:
        raise ValueError("programme receipts exceed the frozen ledger bound")
    frozen_receipts = tuple(receipts)
    if any(type(item) is not ValidationSlotComputationResultV2 for item in results):
        raise TypeError("programme results must be exact V2 slot computation results")
    typed_results = cast(tuple[ValidationSlotComputationResultV2, ...], tuple(results))
    verify_slot_results_v2(typed_results, runner_version=runner_version)
    expected = tuple(item.slot_id for item in typed_results)
    report = verify_validation_v2_receipts(
        frozen_receipts,
        expected_slot_ids=expected,
        runner_version=runner_version,
    )
    selected = _select_terminal_attempts_verified_v2(frozen_receipts)
    for result in typed_results:
        receipt = selected[result.slot_id]
        if (
            receipt.attempt_number != result.attempt_number
            or receipt.attempt_sha256 != result.attempt_sha256
            or receipt.input_sha256 != result.input_sha256
            or receipt.evidence_sha256 != result.evidence_sha256
            or receipt.result_sha256 != result.result_sha256
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
