"""Immutable Phase 5 VP/VR receipts and programme-scoped final access."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from types import MappingProxyType
import uuid
import weakref
from typing import cast

from market_structure_lab.core.identity import hash_json as canonical_hash_json
from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationSlot,
    ValidationTerminalState,
    evaluation_id_for_slot,
    validation_roster_sha256,
)
from market_structure_lab.research.splits import (
    CandidateEligibility,
    FinalHoldoutBatch,
    VerifiedFinalAccess,
    attempt_final_access,
)

_SCHEMA_PROGRAMME = "validation-programme-receipt-v1"
_SCHEMA_EVALUATION = "validation-evaluation-receipt-v1"
_HUMAN_SUMMARY = "human-summary.txt"
_MACHINE_RECEIPT = "machine-receipt.json"
_MAX_FILES = 20_000
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SAFE_ARTIFACT = "artifact"
_PROGRAMME_RECEIPT_FACTORY = object()
_EVALUATION_RECEIPT_FACTORY = object()
_FINAL_ELIGIBILITY_FACTORY = object()
_ACCESS_AUTHORITY_FACTORY = object()
_ACCESS_STATE_FACTORY = object()
_OPENED_FINAL_ROWS_FACTORY = object()
_VERIFIED_PROGRAMME_RECEIPT_OBJECTS: dict[
    int, tuple[weakref.ReferenceType[ProgrammeReceipt], str, bytes]
] = {}
_ISSUED_FINAL_ELIGIBILITY_OBJECTS: dict[
    int, tuple[weakref.ReferenceType[FinalCandidateEligibilityReceipt], str, bytes]
] = {}
_VALIDATION_SLOT_BY_ID: dict[str, ValidationSlot] = {
    slot.slot_id: slot for slot in VALIDATION_SLOT_ROSTER
}
_VALIDATION_ROSTER_SHA256 = validation_roster_sha256(VALIDATION_SLOT_ROSTER)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ProgrammeReceipt:
    """Verified immutable programme receipt for one VP ledger."""

    programme_id: str
    receipt_sha256: str
    hashes: Mapping[str, str]
    ledger: tuple[Mapping[str, object], ...]
    artifact_sha256: Mapping[str, str]
    canonical_bytes: bytes = field(repr=False, compare=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _PROGRAMME_RECEIPT_FACTORY:
            raise TypeError("ProgrammeReceipt requires the receipt factory")
        object.__setattr__(self, "hashes", _freeze_mapping(self.hashes))
        object.__setattr__(self, "ledger", tuple(_freeze_mapping(item) for item in self.ledger))
        object.__setattr__(self, "artifact_sha256", _freeze_mapping(self.artifact_sha256))
        if not self.programme_id.startswith("VP-"):
            raise ValueError("programme receipt must belong to a VP identity")
        _require_sha256(self.receipt_sha256, "receipt_sha256")
        _require_exact_hashes(self.hashes)
        _validate_ledger(self.ledger)
        for name, digest in self.artifact_sha256.items():
            _artifact_name(name)
            _require_sha256(digest, "artifact sha256")
        if self.canonical_bytes != _json_file(self.to_dict()):
            raise ValueError("programme receipt bytes are not canonical")
        payload = dict(self.to_dict())
        payload.pop("receipt_sha256")
        expected = _sha256(_json_file(payload))
        if self.receipt_sha256 != expected:
            raise ValueError("programme receipt checksum differs from canonical payload")
        _register_programme_receipt_object(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_PROGRAMME,
            "programme_id": self.programme_id,
            "hashes": _to_plain(self.hashes),
            "ledger": [_to_plain(item) for item in self.ledger],
            "artifact_sha256": _to_plain(self.artifact_sha256),
            "receipt_sha256": self.receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReceipt:
    """Verified immutable VR receipt for one slot evaluation attempt."""

    programme_id: str
    evaluation_id: str
    attempt: int
    slot: Mapping[str, object]
    terminal_state: Mapping[str, str]
    hashes: Mapping[str, str]
    artifact_sha256: Mapping[str, str]
    receipt_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _EVALUATION_RECEIPT_FACTORY:
            raise TypeError("EvaluationReceipt requires the receipt factory")
        object.__setattr__(self, "slot", _freeze_mapping(self.slot))
        object.__setattr__(self, "terminal_state", _freeze_mapping(self.terminal_state))
        object.__setattr__(self, "hashes", _freeze_mapping(self.hashes))
        object.__setattr__(self, "artifact_sha256", _freeze_mapping(self.artifact_sha256))
        if not self.programme_id.startswith("VP-"):
            raise ValueError("evaluation receipt must belong to a VP identity")
        if not self.evaluation_id.startswith("VR-"):
            raise ValueError("evaluation receipt must own a VR identity")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("evaluation attempt must be a positive integer")
        _validate_terminal_mapping(self.terminal_state)
        _require_exact_hashes(self.hashes)
        for name, digest in self.artifact_sha256.items():
            _artifact_name(name)
            _require_sha256(digest, "artifact sha256")
        if self.canonical_bytes != _json_file(self.to_dict()):
            raise ValueError("evaluation receipt bytes are not canonical")
        payload = dict(self.to_dict())
        payload.pop("receipt_sha256")
        if self.receipt_sha256 != _sha256(_json_file(payload)):
            raise ValueError("evaluation receipt checksum differs from canonical payload")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_EVALUATION,
            "programme_id": self.programme_id,
            "evaluation_id": self.evaluation_id,
            "attempt": self.attempt,
            "slot": _to_plain(self.slot),
            "terminal_state": _to_plain(self.terminal_state),
            "hashes": _to_plain(self.hashes),
            "artifact_sha256": _to_plain(self.artifact_sha256),
            "receipt_sha256": self.receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReceiptPublication:
    """Path plus verified receipt returned from an immutable publication."""

    path: Path
    receipt: ProgrammeReceipt | EvaluationReceipt


@dataclass(frozen=True, slots=True)
class EvaluationReceiptRequest:
    """Bounded request to publish one immutable VR attempt in a batch."""

    slot: ValidationSlot
    terminal_state: ValidationTerminalState
    attempt: int
    artifacts: Mapping[str, bytes | str] | None = None


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FinalCandidateEligibilityReceipt:
    """Factory-issued eligibility evidence for one final-holdout candidate decision."""

    programme_id: str
    eligibility: CandidateEligibility
    receipt_sha256: str
    hashes: Mapping[str, str]
    canonical_bytes: bytes = field(repr=False, compare=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _FINAL_ELIGIBILITY_FACTORY:
            raise TypeError("FinalCandidateEligibilityReceipt requires the eligibility factory")
        object.__setattr__(self, "hashes", _freeze_mapping(self.hashes))
        if self.programme_id != self.eligibility.programme_id:
            raise ValueError("eligibility receipt programme differs from candidate metadata")
        _require_exact_hashes(self.hashes)
        if self.receipt_sha256 != self.eligibility.eligibility_receipt_sha256:
            raise ValueError("eligibility receipt SHA differs from candidate metadata")
        if self.canonical_bytes != canonical_json_bytes(self.to_payload()):
            raise ValueError("eligibility receipt bytes are not canonical")
        if self.receipt_sha256 != _sha256(self.canonical_bytes):
            raise ValueError("eligibility receipt checksum differs from canonical payload")
        _register_final_eligibility_receipt_object(self)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": "final-candidate-eligibility-receipt-v1",
            "programme_id": self.programme_id,
            "candidate": {
                "candidate_id": self.eligibility.candidate_id,
                "family": self.eligibility.family,
                "eligible": self.eligibility.eligible,
            },
            "hashes": _to_plain(self.hashes),
        }


@dataclass(frozen=True, slots=True)
class FinalAccessAuthority:
    """Sealed programme-scoped authority required before final-row access."""

    programme_id: str
    programme_receipt_sha256: str
    batch: FinalHoldoutBatch
    eligibility_receipts: tuple[FinalCandidateEligibilityReceipt, ...]
    authority_sha256: str
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _ACCESS_AUTHORITY_FACTORY:
            raise TypeError("FinalAccessAuthority requires the access-authority factory")
        if self.programme_id != self.batch.programme_id:
            raise ValueError("access authority programme differs from batch")
        _require_sha256(self.programme_receipt_sha256, "programme_receipt_sha256")
        _require_sha256(self.authority_sha256, "authority_sha256")
        if self.authority_sha256 != hash_json_local(
            "final-access-authority-v1",
            {
                "programme_id": self.programme_id,
                "programme_receipt_sha256": self.programme_receipt_sha256,
                "batch_sha256": self.batch.batch_sha256,
                "eligibility_receipt_sha256s": [
                    item.receipt_sha256 for item in self.eligibility_receipts
                ],
            },
        ):
            raise ValueError("access authority SHA differs from canonical payload")


@dataclass(frozen=True, slots=True)
class AccessState:
    """Programme-scoped final-access attempt committed before row iteration."""

    programme_id: str
    batch_sha256: str
    access_receipt_sha256: str
    access_record_path: Path
    verified_access: VerifiedFinalAccess = field(repr=False, compare=False)
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _ACCESS_STATE_FACTORY:
            raise TypeError("AccessState requires the final-access factory")
        if self.programme_id != self.verified_access.programme_id:
            raise ValueError("access state programme differs from verified access")
        if self.batch_sha256 != self.verified_access.batch_sha256:
            raise ValueError("access state batch differs from verified access")
        if self.access_receipt_sha256 != self.verified_access.access_receipt_sha256:
            raise ValueError("access state receipt differs from verified access")
        if self.access_record_path != self.verified_access.access_record_path:
            raise ValueError("access state path differs from verified access")


@dataclass(frozen=True, slots=True)
class OpenedFinalRows:
    """Synthetic final-row iterable paired with its committed access attempt."""

    access: AccessState
    rows: Iterable[Mapping[str, object]]
    factory_token: InitVar[object] = None

    def __post_init__(self, factory_token: object) -> None:
        if factory_token is not _OPENED_FINAL_ROWS_FACTORY:
            raise TypeError("OpenedFinalRows requires the final-access factory")


def build_programme_receipt(
    config: ValidationProgrammeConfig,
    *,
    terminal_by_slot: Mapping[str, ValidationTerminalState],
    artifact_sha256: Mapping[str, str] | None = None,
) -> ProgrammeReceipt:
    """Build canonical immutable VP bytes after proving exact terminal ledger coverage."""

    _require_config(config)
    ledger = _ledger_from_terminal_map(terminal_by_slot)
    hashes = _hashes(config)
    artifacts = dict(sorted((artifact_sha256 or {}).items()))
    values: dict[str, object] = {
        "schema_version": _SCHEMA_PROGRAMME,
        "programme_id": config.programme_id,
        "hashes": hashes,
        "ledger": [dict(item) for item in ledger],
        "artifact_sha256": artifacts,
    }
    values["receipt_sha256"] = _sha256(_json_file(values))
    canonical = _json_file(values)
    return ProgrammeReceipt(
        programme_id=config.programme_id,
        receipt_sha256=str(values["receipt_sha256"]),
        hashes=hashes,
        ledger=ledger,
        artifact_sha256=artifacts,
        canonical_bytes=canonical,
        factory_token=_PROGRAMME_RECEIPT_FACTORY,
    )


def build_evaluation_receipt(
    config: ValidationProgrammeConfig,
    *,
    slot: ValidationSlot,
    terminal_state: ValidationTerminalState,
    attempt: int,
    artifact_sha256: Mapping[str, str] | None = None,
) -> EvaluationReceipt:
    """Build canonical immutable VR bytes for one slot attempt."""

    _require_config(config)
    if slot not in config.roster:
        raise ValueError("evaluation slot is not owned by the validation programme")
    if not isinstance(terminal_state, ValidationTerminalState):
        raise ValueError("evaluation receipt terminal state must be a ValidationTerminalState")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("evaluation attempt must be a positive integer")
    terminal = _terminal_to_dict(terminal_state)
    artifacts = dict(sorted((artifact_sha256 or {}).items()))
    values: dict[str, object] = {
        "schema_version": _SCHEMA_EVALUATION,
        "programme_id": config.programme_id,
        "evaluation_id": evaluation_id_for_slot(config, slot),
        "attempt": attempt,
        "slot": slot.to_dict(),
        "terminal_state": terminal,
        "hashes": _hashes(config),
        "artifact_sha256": artifacts,
    }
    values["receipt_sha256"] = _sha256(_json_file(values))
    canonical = _json_file(values)
    return EvaluationReceipt(
        programme_id=config.programme_id,
        evaluation_id=str(values["evaluation_id"]),
        attempt=attempt,
        slot=slot.to_dict(),
        terminal_state=terminal,
        hashes=_hashes(config),
        artifact_sha256=artifacts,
        receipt_sha256=str(values["receipt_sha256"]),
        canonical_bytes=canonical,
        factory_token=_EVALUATION_RECEIPT_FACTORY,
    )


def publish_programme_receipt(
    config: ValidationProgrammeConfig,
    *,
    terminal_by_slot: Mapping[str, ValidationTerminalState],
    output_root: Path,
    artifacts: Mapping[str, bytes | str] | None = None,
    maximum_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    maximum_entries: int = _MAX_FILES,
) -> ReceiptPublication:
    """Atomically publish a VP human summary plus machine receipt and exact artifacts."""

    payloads = _artifact_payloads(artifacts or {})
    receipt = build_programme_receipt(
        config,
        terminal_by_slot=terminal_by_slot,
        artifact_sha256=_payload_hashes(payloads),
    )
    payloads[_MACHINE_RECEIPT] = receipt.canonical_bytes
    payloads[_HUMAN_SUMMARY] = _programme_summary(receipt).encode("utf-8")
    receipt = build_programme_receipt(
        config,
        terminal_by_slot=terminal_by_slot,
        artifact_sha256=_payload_hashes(
            {k: v for k, v in payloads.items() if k != _MACHINE_RECEIPT}
        ),
    )
    payloads[_MACHINE_RECEIPT] = receipt.canonical_bytes
    path = _publish_payloads(
        output_root,
        config.programme_id,
        payloads,
        expected_receipt=receipt,
        config=config,
        maximum_total_bytes=maximum_total_bytes,
        maximum_entries=maximum_entries,
    )
    verified = verify_programme_receipt(path)
    return ReceiptPublication(path=path, receipt=verified)


def publish_evaluation_receipt(
    config: ValidationProgrammeConfig,
    *,
    slot: ValidationSlot,
    terminal_state: ValidationTerminalState,
    attempt: int,
    output_root: Path,
    artifacts: Mapping[str, bytes | str] | None = None,
    maximum_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    maximum_entries: int = _MAX_FILES,
) -> ReceiptPublication:
    """Atomically publish one immutable VR attempt receipt."""

    directory_name, payloads, receipt = _evaluation_publication_payloads(
        config,
        slot=slot,
        terminal_state=terminal_state,
        attempt=attempt,
        artifacts=artifacts or {},
    )
    path = _publish_payloads(
        output_root,
        directory_name,
        payloads,
        expected_receipt=receipt,
        config=config,
        maximum_total_bytes=maximum_total_bytes,
        maximum_entries=maximum_entries,
    )
    verified = verify_evaluation_receipt(path, config=config)
    return ReceiptPublication(path=path, receipt=verified)


def publish_evaluation_receipt_batch(
    config: ValidationProgrammeConfig,
    *,
    requests: Sequence[EvaluationReceiptRequest],
    output_root: Path,
    maximum_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    maximum_entries: int = _MAX_FILES,
) -> tuple[ReceiptPublication, ...]:
    """Publish VR attempt receipts as one bounded staging batch."""

    _require_config(config)
    if isinstance(maximum_total_bytes, bool) or maximum_total_bytes < 1:
        raise ValueError("maximum_total_bytes must be positive")
    if isinstance(maximum_entries, bool) or maximum_entries < 1:
        raise ValueError("maximum_entries must be positive")
    if not isinstance(requests, Sequence):
        raise TypeError("evaluation receipt batch requests must be a Sequence")
    request_count = len(requests)
    if request_count > config.work_budget.max_evaluations:
        raise ValueError("evaluation receipt batch exceeds the config evaluation budget")
    if request_count == 0:
        return ()
    request_tuple = tuple(requests)

    prepared: list[tuple[str, dict[str, bytes], EvaluationReceipt]] = []
    seen_directories: set[str] = set()
    for request in request_tuple:
        if not isinstance(request, EvaluationReceiptRequest):
            raise TypeError("evaluation receipt batch requires EvaluationReceiptRequest items")
        directory_name, payloads, receipt = _evaluation_publication_payloads(
            config,
            slot=request.slot,
            terminal_state=request.terminal_state,
            attempt=request.attempt,
            artifacts=request.artifacts or {},
        )
        if directory_name in seen_directories:
            raise ValueError("duplicate evaluation receipt path in batch")
        seen_directories.add(directory_name)
        prepared.append((directory_name, payloads, receipt))

    _validate_batch_payload_budget(
        config,
        tuple(payloads for _directory_name, payloads, _receipt in prepared),
        maximum_total_bytes=maximum_total_bytes,
        maximum_entries=maximum_entries,
    )

    root = Path(output_root)
    if not path_exists_no_follow(root):
        try:
            root.mkdir(parents=True)
        except FileExistsError:
            pass
    require_regular_directory(root)

    results: list[ReceiptPublication | None] = [None] * len(prepared)
    pending: list[tuple[int, str, dict[str, bytes], EvaluationReceipt]] = []
    for index, (directory_name, payloads, receipt) in enumerate(prepared):
        final = root / directory_name
        if path_exists_no_follow(final):
            existing = verify_evaluation_receipt(final, config=config)
            if existing.receipt_sha256 != receipt.receipt_sha256:
                raise RuntimeError("receipt identity conflict")
            _require_identical_payloads(final, payloads)
            results[index] = ReceiptPublication(path=final, receipt=existing)
        else:
            pending.append((index, directory_name, payloads, receipt))

    if not pending:
        return tuple(cast(ReceiptPublication, item) for item in results)

    staging = root / f".evaluation-batch.staging.{os.getpid()}.{uuid.uuid4().hex}"
    committed: list[Path] = []
    try:
        staging.mkdir()
        verified_pending: dict[int, EvaluationReceipt] = {}
        for index, directory_name, payloads, receipt in pending:
            staged_directory = staging / directory_name
            staged_directory.mkdir()
            for name, content in payloads.items():
                _secure_write_payload(staged_directory, name, content, sync=False)
            _require_identical_payloads(staged_directory, payloads)
            verified_pending[index] = receipt

        for index, directory_name, payloads, receipt in pending:
            final = root / directory_name
            staged_directory = staging / directory_name
            try:
                staged_directory.rename(final)
                committed.append(final)
            except OSError:
                if path_exists_no_follow(final):
                    existing = verify_evaluation_receipt(final, config=config)
                    if existing.receipt_sha256 == receipt.receipt_sha256:
                        _require_identical_payloads(final, payloads)
                        shutil.rmtree(staged_directory)
                        results[index] = ReceiptPublication(path=final, receipt=existing)
                        continue
                raise
            results[index] = ReceiptPublication(path=final, receipt=verified_pending[index])
        _fsync_parent_directory(root)
    except Exception:
        for final in reversed(committed):
            if path_exists_no_follow(final):
                try:
                    require_regular_directory(final)
                except RuntimeError:
                    pass
                else:
                    shutil.rmtree(final)
        raise
    finally:
        if path_exists_no_follow(staging):
            try:
                require_regular_directory(staging)
            except RuntimeError:
                pass
            else:
                shutil.rmtree(staging)

    return tuple(cast(ReceiptPublication, item) for item in results)


def _evaluation_publication_payloads(
    config: ValidationProgrammeConfig,
    *,
    slot: ValidationSlot,
    terminal_state: ValidationTerminalState,
    attempt: int,
    artifacts: Mapping[str, bytes | str],
) -> tuple[str, dict[str, bytes], EvaluationReceipt]:
    payloads = _artifact_payloads(artifacts)
    evaluation = evaluation_id_for_slot(config, slot)
    directory_name = f"{evaluation}-attempt-{attempt:03d}"
    receipt = build_evaluation_receipt(
        config,
        slot=slot,
        terminal_state=terminal_state,
        attempt=attempt,
        artifact_sha256=_payload_hashes(payloads),
    )
    payloads[_MACHINE_RECEIPT] = receipt.canonical_bytes
    payloads[_HUMAN_SUMMARY] = _evaluation_summary(receipt).encode("utf-8")
    receipt = build_evaluation_receipt(
        config,
        slot=slot,
        terminal_state=terminal_state,
        attempt=attempt,
        artifact_sha256=_payload_hashes(
            {name: content for name, content in payloads.items() if name != _MACHINE_RECEIPT}
        ),
    )
    payloads[_MACHINE_RECEIPT] = receipt.canonical_bytes
    return directory_name, payloads, receipt


def _validate_batch_payload_budget(
    config: ValidationProgrammeConfig,
    all_payloads: Sequence[Mapping[str, bytes]],
    *,
    maximum_total_bytes: int,
    maximum_entries: int,
) -> None:
    effective_entries = min(maximum_entries, config.work_budget.max_artifacts)
    effective_bytes = min(maximum_total_bytes, config.work_budget.max_artifact_bytes)
    total_entries = 0
    total_bytes = 0
    for payloads in all_payloads:
        if len(payloads) > effective_entries:
            raise ValueError("receipt artifact entries exceed the config work-budget limit")
        receipt_bytes = sum(len(value) for value in payloads.values())
        if receipt_bytes > effective_bytes:
            raise ValueError("receipt artifact bytes exceed the config work-budget limit")
        total_entries += len(payloads)
        total_bytes += receipt_bytes
    if total_entries > effective_entries:
        raise ValueError("receipt batch artifact entries exceed the config work-budget limit")
    if total_bytes > effective_bytes:
        raise ValueError("receipt batch artifact bytes exceed the config work-budget limit")


def verify_programme_receipt(directory: Path) -> ProgrammeReceipt:
    """Verify exact file set, canonical bytes, and artifact checksums for a VP receipt."""

    path = Path(directory)
    raw = _read_receipt(path, schema=_SCHEMA_PROGRAMME)
    receipt = _programme_from_raw(raw)
    if path.name != receipt.programme_id:
        raise RuntimeError("programme receipt path identity does not match programme_id")
    _verify_files(path, receipt.artifact_sha256, receipt.canonical_bytes)
    return receipt


def verify_evaluation_receipt(
    directory: Path, *, config: ValidationProgrammeConfig
) -> EvaluationReceipt:
    """Verify exact file set, canonical bytes, and artifact checksums for a VR receipt."""

    path = Path(directory)
    raw = _read_receipt(path, schema=_SCHEMA_EVALUATION)
    receipt = _evaluation_from_raw(raw)
    try:
        _validate_evaluation_against_config(receipt, config)
    except ValueError as error:
        raise RuntimeError("evaluation receipt contract is malformed") from error
    if path.name != f"{receipt.evaluation_id}-attempt-{receipt.attempt:03d}":
        raise RuntimeError("evaluation receipt path identity does not match VR attempt")
    _verify_files(path, receipt.artifact_sha256, receipt.canonical_bytes)
    return receipt


def open_final_holdout_rows(
    authority: FinalAccessAuthority,
    *,
    access_root: Path,
    row_source_factory: Callable[[AccessState], Iterable[Mapping[str, object]]],
) -> OpenedFinalRows:
    """Commit programme access record before obtaining any synthetic final-row iterable."""

    if not isinstance(authority, FinalAccessAuthority):
        raise TypeError("final access requires a verified programme-scoped access authority")
    if not callable(row_source_factory):
        raise TypeError("row_source_factory must be callable")
    try:
        access = attempt_final_access(authority.batch, access_root=access_root)
    except FileExistsError as error:
        raise RuntimeError("final access attempt already exists; replay rejected") from error
    state = AccessState(
        programme_id=access.programme_id,
        batch_sha256=access.batch_sha256,
        access_receipt_sha256=access.access_receipt_sha256,
        access_record_path=access.access_record_path
        if access.access_record_path is not None
        else Path(),
        verified_access=access,
        factory_token=_ACCESS_STATE_FACTORY,
    )
    rows = row_source_factory(state)
    return OpenedFinalRows(access=state, rows=rows, factory_token=_OPENED_FINAL_ROWS_FACTORY)


def issue_final_candidate_eligibility(
    config: ValidationProgrammeConfig,
    *,
    candidate_id: str,
    family: str,
    eligible: bool,
) -> FinalCandidateEligibilityReceipt:
    """Issue one factory-bound candidate eligibility receipt for final-access preflight."""

    _require_config(config)
    if family not in EXPECTED_FAMILIES:
        raise ValueError("candidate eligibility family is unsupported")
    if not isinstance(eligible, bool):
        raise TypeError("candidate eligibility flag must be boolean")
    draft = {
        "schema_version": "final-candidate-eligibility-receipt-v1",
        "programme_id": config.programme_id,
        "candidate": {
            "candidate_id": candidate_id,
            "family": family,
            "eligible": eligible,
        },
        "hashes": _hashes(config),
    }
    canonical = canonical_json_bytes(draft)
    receipt_sha256 = _sha256(canonical)
    eligibility = CandidateEligibility(
        programme_id=config.programme_id,
        candidate_id=candidate_id,
        family=family,
        eligible=eligible,
        eligibility_receipt_sha256=receipt_sha256,
    )
    return FinalCandidateEligibilityReceipt(
        programme_id=config.programme_id,
        eligibility=eligibility,
        receipt_sha256=receipt_sha256,
        hashes=_hashes(config),
        canonical_bytes=canonical,
        factory_token=_FINAL_ELIGIBILITY_FACTORY,
    )


def build_final_access_authority(
    config: ValidationProgrammeConfig,
    *,
    programme_receipt: ProgrammeReceipt,
    batch: FinalHoldoutBatch,
    eligibility_receipts: Sequence[FinalCandidateEligibilityReceipt],
) -> FinalAccessAuthority:
    """Verify programme ledger and candidate eligibility before any final access record."""

    _require_config(config)
    if not isinstance(programme_receipt, ProgrammeReceipt):
        raise TypeError("final access authority requires a verified programme receipt")
    _reverify_programme_receipt_object(programme_receipt)
    if programme_receipt.programme_id != config.programme_id or dict(
        programme_receipt.hashes
    ) != _hashes(config):
        raise ValueError("programme receipt does not match validation config")
    _validate_ledger(programme_receipt.ledger)
    for item in programme_receipt.ledger:
        terminal = item.get("terminal_state")
        if (
            not isinstance(terminal, Mapping)
            or terminal.get("execution_status") != ExecutionStatus.COMPLETED.value
        ):
            raise ValueError("programme preflight has unresolved terminal blockers")
    if not isinstance(batch, FinalHoldoutBatch):
        raise TypeError("final access authority requires a canonical final batch")
    if batch.programme_id != config.programme_id:
        raise ValueError("final batch programme differs from validation config")
    receipts = tuple(eligibility_receipts)
    if any(not isinstance(item, FinalCandidateEligibilityReceipt) for item in receipts):
        raise TypeError("final access requires verified eligibility receipts")
    for receipt in receipts:
        _reverify_final_eligibility_receipt_object(receipt)
        if receipt.programme_id != config.programme_id or dict(receipt.hashes) != _hashes(config):
            raise ValueError("eligibility receipt does not match validation config")
        if receipt.receipt_sha256 != receipt.eligibility.eligibility_receipt_sha256:
            raise ValueError("eligibility receipt SHA mismatch")
    _validate_batch_against_eligibility_receipts(batch, receipts)
    if batch.batch_sha256 != _expected_final_batch_sha256(batch):
        raise ValueError("final batch canonical metadata differs from its SHA")
    authority_sha256 = hash_json_local(
        "final-access-authority-v1",
        {
            "programme_id": config.programme_id,
            "programme_receipt_sha256": programme_receipt.receipt_sha256,
            "batch_sha256": batch.batch_sha256,
            "eligibility_receipt_sha256s": [item.receipt_sha256 for item in receipts],
        },
    )
    return FinalAccessAuthority(
        programme_id=config.programme_id,
        programme_receipt_sha256=programme_receipt.receipt_sha256,
        batch=batch,
        eligibility_receipts=receipts,
        authority_sha256=authority_sha256,
        factory_token=_ACCESS_AUTHORITY_FACTORY,
    )


def _validate_batch_against_eligibility_receipts(
    batch: FinalHoldoutBatch,
    receipts: tuple[FinalCandidateEligibilityReceipt, ...],
) -> None:
    if not receipts:
        raise ValueError("final access requires verified eligibility receipts")
    eligibilities = tuple(item.eligibility for item in receipts)
    canonical = tuple(
        sorted(
            eligibilities,
            key=lambda item: (EXPECTED_FAMILIES.index(item.family), item.candidate_id),
        )
    )
    if eligibilities != canonical:
        raise ValueError("eligibility receipts are not in canonical family/candidate order")
    if batch.eligibility_decisions != canonical:
        raise ValueError(
            "final batch eligibility decisions differ from verified eligibility receipts"
        )
    eligible = tuple(item for item in canonical if item.eligible)
    if batch.candidate_ids != tuple(item.candidate_id for item in eligible):
        raise ValueError("final batch candidate set differs from verified eligibility receipts")
    if batch.family_order != tuple(item.family for item in eligible):
        raise ValueError("final batch family order differs from verified eligibility receipts")
    if batch.eligibility_receipt_sha256s != tuple(
        item.eligibility_receipt_sha256 for item in eligible
    ):
        raise ValueError("final batch eligibility hashes differ from verified eligibility receipts")
    if not batch.candidate_ids:
        raise ValueError("empty final batch cannot create an access authority")


def _hashes(config: ValidationProgrammeConfig) -> dict[str, str]:
    return {
        "code_commit": config.code_commit,
        "lockfile_sha256": config.lockfile_sha256,
        "dataset_sha256": config.dataset_sha256,
        "config_sha256": config.sha256,
        "cost_policy_sha256": config.cost_policy_sha256,
        "control_policy_sha256": config.control_policy_sha256,
        "work_budget_sha256": config.work_budget.sha256,
    }


def _ledger_from_terminal_map(
    terminal_by_slot: Mapping[str, ValidationTerminalState],
) -> tuple[Mapping[str, object], ...]:
    expected_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    if tuple(terminal_by_slot) != expected_ids:
        raise ValueError("programme receipt requires the exact frozen 1,104-slot ledger")
    ledger: list[Mapping[str, object]] = []
    for slot in VALIDATION_SLOT_ROSTER:
        state = terminal_by_slot.get(slot.slot_id)
        if not isinstance(state, ValidationTerminalState):
            raise ValueError("programme ledger terminal state must be a ValidationTerminalState")
        payload = slot.to_dict()
        payload["terminal_state"] = _terminal_to_dict(state)
        ledger.append(payload)
    return tuple(ledger)


def _validate_ledger(ledger: Sequence[Mapping[str, object]]) -> None:
    if len(ledger) != 1_104:
        raise ValueError("programme receipt requires the exact frozen 1,104-slot ledger")
    for item, slot in zip(ledger, VALIDATION_SLOT_ROSTER, strict=True):
        terminal = item.get("terminal_state")
        raw_slot = _to_plain(item)
        assert isinstance(raw_slot, dict)
        raw_slot.pop("terminal_state", None)
        if raw_slot != slot.to_dict():
            raise ValueError("programme receipt ledger slot payload differs from frozen roster")
        _validate_terminal_mapping(terminal)


def _expected_final_batch_sha256(batch: FinalHoldoutBatch) -> str:
    return canonical_hash_json(
        "final-holdout-batch-v1",
        {
            "programme_id": batch.programme_id,
            "split_sha256": batch.split_sha256,
            "timeframe": batch.timeframe,
            "common_start": batch.common_start,
            "temporal_holdout_start": batch.temporal_holdout_start,
            "temporal_holdout_end": batch.temporal_holdout_end,
            "temporal_holdout_sha256": batch.temporal_holdout_sha256,
            "development_symbols": list(batch.development_symbols),
            "asset_holdout_symbols": list(batch.asset_holdout_symbols),
            "candidate_ids": list(batch.candidate_ids),
            "family_order": list(batch.family_order),
            "eligibility_receipt_sha256s": list(batch.eligibility_receipt_sha256s),
            "eligibility_decisions": [item.to_dict() for item in batch.eligibility_decisions],
        },
    )


def _require_exact_hashes(hashes: Mapping[str, str]) -> None:
    required = {
        "code_commit",
        "lockfile_sha256",
        "dataset_sha256",
        "config_sha256",
        "cost_policy_sha256",
        "control_policy_sha256",
        "work_budget_sha256",
    }
    if set(hashes) != required:
        raise ValueError("receipt must bind exact code/lock/data/config/cost/control/budget hashes")
    for name, value in hashes.items():
        if name == "code_commit":
            if (
                not isinstance(value, str)
                or len(value) != 40
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("code_commit must be a lower-case Git SHA")
        else:
            _require_sha256(value, name)


def _validate_evaluation_against_config(
    receipt: EvaluationReceipt,
    config: ValidationProgrammeConfig,
) -> None:
    _require_config(config)
    if receipt.programme_id != config.programme_id:
        raise ValueError("evaluation receipt programme differs from validation config")
    if dict(receipt.hashes) != _hashes(config):
        raise ValueError("evaluation receipt hashes differ from validation config")
    slot_payload = cast(dict[str, object], _to_plain(receipt.slot))
    raw_slot_id = slot_payload.get("slot_id")
    if not isinstance(raw_slot_id, str):
        raise ValueError("evaluation receipt slot payload missing a valid slot_id")
    slot = _VALIDATION_SLOT_BY_ID.get(raw_slot_id)
    if slot is None or slot.to_dict() != slot_payload:
        raise ValueError("evaluation receipt slot is not owned by the frozen programme")
    expected = evaluation_id_for_slot(config, slot)
    if receipt.evaluation_id != expected:
        raise ValueError("evaluation receipt VR identity differs from programme and slot")


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return _json_file(payload)


def hash_json_local(domain: str, payload: Mapping[str, object]) -> str:
    return _sha256(_json_file({"domain": domain, "payload": payload, "schema_version": 1}))


def _terminal_to_dict(state: ValidationTerminalState) -> dict[str, str]:
    return {
        "execution_status": state.execution_status.value,
        "decision": state.decision.value,
    }


def _validate_terminal_mapping(raw: object) -> None:
    if not isinstance(raw, Mapping):
        raise ValueError("receipt terminal state must be a mapping")
    state = ValidationTerminalState(
        ExecutionStatus(str(raw.get("execution_status"))),
        ScientificDecision(str(raw.get("decision"))),
    )
    _ = state


def _verify_programme_receipt_payload(path: Path) -> ProgrammeReceipt:
    raw = _read_receipt(path, schema=_SCHEMA_PROGRAMME)
    receipt = _programme_from_raw(raw)
    _verify_files(path, receipt.artifact_sha256, receipt.canonical_bytes)
    return receipt


def _verify_evaluation_receipt_payload(path: Path) -> EvaluationReceipt:
    raw = _read_receipt(path, schema=_SCHEMA_EVALUATION)
    receipt = _evaluation_from_raw(raw)
    _verify_files(path, receipt.artifact_sha256, receipt.canonical_bytes)
    return receipt


def _read_receipt(path: Path, *, schema: str) -> Mapping[str, object]:
    require_regular_directory(path)
    try:
        raw = json.loads(
            read_bounded_regular(path / _MACHINE_RECEIPT, _MAX_RECEIPT_BYTES).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("receipt is missing or malformed") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != schema:
        raise RuntimeError("receipt schema is malformed")
    try:
        receipt_sha256 = raw.pop("receipt_sha256")
        if not isinstance(receipt_sha256, str) or _sha256(_json_file(raw)) != receipt_sha256:
            raise RuntimeError("receipt checksum tamper detected")
        raw["receipt_sha256"] = receipt_sha256
    except KeyError as error:
        raise RuntimeError("receipt checksum is missing") from error
    return raw


def _programme_from_raw(raw: Mapping[str, object]) -> ProgrammeReceipt:
    try:
        return ProgrammeReceipt(
            programme_id=str(raw["programme_id"]),
            receipt_sha256=str(raw["receipt_sha256"]),
            hashes=_string_mapping(raw["hashes"]),
            ledger=_mapping_tuple(raw["ledger"]),
            artifact_sha256=_string_mapping(raw["artifact_sha256"]),
            canonical_bytes=_json_file(raw),
            factory_token=_PROGRAMME_RECEIPT_FACTORY,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("programme receipt contract is malformed") from error


def _evaluation_from_raw(raw: Mapping[str, object]) -> EvaluationReceipt:
    try:
        return EvaluationReceipt(
            programme_id=str(raw["programme_id"]),
            evaluation_id=str(raw["evaluation_id"]),
            attempt=_required_int(raw["attempt"], "attempt"),
            slot=_mapping(raw["slot"]),
            terminal_state=_string_mapping(raw["terminal_state"]),
            hashes=_string_mapping(raw["hashes"]),
            artifact_sha256=_string_mapping(raw["artifact_sha256"]),
            receipt_sha256=str(raw["receipt_sha256"]),
            canonical_bytes=_json_file(raw),
            factory_token=_EVALUATION_RECEIPT_FACTORY,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("evaluation receipt contract is malformed") from error


def _verify_files(path: Path, artifact_sha256: Mapping[str, str], receipt_bytes: bytes) -> None:
    expected = {_MACHINE_RECEIPT, *artifact_sha256}
    actual = set(bounded_regular_files(path, maximum=_MAX_FILES))
    if actual != expected:
        raise RuntimeError("receipt contains missing or unexpected files")
    observed_receipt = read_bounded_regular(path / _MACHINE_RECEIPT, len(receipt_bytes))
    if observed_receipt != receipt_bytes:
        raise RuntimeError("receipt is not canonical JSON")
    for name, digest in artifact_sha256.items():
        if sha256_regular(path / PurePosixPath(name)) != digest:
            raise RuntimeError("receipt artifact tamper detected")


def _publish_payloads(
    root: Path,
    directory_name: str,
    payloads: Mapping[str, bytes],
    *,
    expected_receipt: ProgrammeReceipt | EvaluationReceipt,
    config: ValidationProgrammeConfig,
    maximum_total_bytes: int,
    maximum_entries: int,
) -> Path:
    if isinstance(maximum_total_bytes, bool) or maximum_total_bytes < 1:
        raise ValueError("maximum_total_bytes must be positive")
    if isinstance(maximum_entries, bool) or maximum_entries < 1:
        raise ValueError("maximum_entries must be positive")
    effective_entries = min(maximum_entries, config.work_budget.max_artifacts)
    effective_bytes = min(maximum_total_bytes, config.work_budget.max_artifact_bytes)
    if len(payloads) > effective_entries:
        raise ValueError("receipt artifact entries exceed the config work-budget limit")
    total = sum(len(value) for value in payloads.values())
    if total > effective_bytes:
        raise ValueError("receipt artifact bytes exceed the config work-budget limit")
    if not path_exists_no_follow(root):
        try:
            root.mkdir(parents=True)
        except FileExistsError:
            pass
    require_regular_directory(root)
    final = root / directory_name
    staging = root / f".{directory_name}.staging.{os.getpid()}.{uuid.uuid4().hex}"
    if path_exists_no_follow(final):
        existing = (
            verify_programme_receipt(final)
            if isinstance(expected_receipt, ProgrammeReceipt)
            else verify_evaluation_receipt(final, config=config)
        )
        if existing.receipt_sha256 != expected_receipt.receipt_sha256:
            raise RuntimeError("receipt identity conflict")
        _require_identical_payloads(final, payloads)
        return final
    staging.mkdir()
    try:
        for name, content in payloads.items():
            _secure_write_payload(staging, name, content)
        verified = (
            _verify_programme_receipt_payload(staging)
            if isinstance(expected_receipt, ProgrammeReceipt)
            else _verify_evaluation_receipt_payload(staging)
        )
        if verified.receipt_sha256 != expected_receipt.receipt_sha256:
            raise RuntimeError("staged receipt differs from expected receipt")
        try:
            staging.rename(final)
        except OSError:
            if path_exists_no_follow(final):
                existing = (
                    verify_programme_receipt(final)
                    if isinstance(expected_receipt, ProgrammeReceipt)
                    else verify_evaluation_receipt(final, config=config)
                )
                if existing.receipt_sha256 == expected_receipt.receipt_sha256:
                    shutil.rmtree(staging)
                    _require_identical_payloads(final, payloads)
                    return final
            raise
        _fsync_parent_directory(final)
    except Exception:
        if path_exists_no_follow(staging):
            try:
                require_regular_directory(staging)
            except RuntimeError:
                pass
            else:
                shutil.rmtree(staging)
        raise
    return final


def _register_programme_receipt_object(receipt: ProgrammeReceipt) -> None:
    key = id(receipt)

    def cleanup(
        reference: weakref.ReferenceType[ProgrammeReceipt], *, object_id: int = key
    ) -> None:
        current = _VERIFIED_PROGRAMME_RECEIPT_OBJECTS.get(object_id)
        if current is not None and current[0] is reference:
            _VERIFIED_PROGRAMME_RECEIPT_OBJECTS.pop(object_id, None)

    reference = weakref.ref(receipt, cleanup)
    _VERIFIED_PROGRAMME_RECEIPT_OBJECTS[key] = (
        reference,
        receipt.receipt_sha256,
        receipt.canonical_bytes,
    )


def _register_final_eligibility_receipt_object(receipt: FinalCandidateEligibilityReceipt) -> None:
    key = id(receipt)

    def cleanup(
        reference: weakref.ReferenceType[FinalCandidateEligibilityReceipt],
        *,
        object_id: int = key,
    ) -> None:
        current = _ISSUED_FINAL_ELIGIBILITY_OBJECTS.get(object_id)
        if current is not None and current[0] is reference:
            _ISSUED_FINAL_ELIGIBILITY_OBJECTS.pop(object_id, None)

    reference = weakref.ref(receipt, cleanup)
    _ISSUED_FINAL_ELIGIBILITY_OBJECTS[key] = (
        reference,
        receipt.receipt_sha256,
        receipt.canonical_bytes,
    )


def _reverify_programme_receipt_object(receipt: ProgrammeReceipt) -> None:
    current = _VERIFIED_PROGRAMME_RECEIPT_OBJECTS.get(id(receipt))
    if current is None or current[0]() is not receipt:
        raise ValueError("programme receipt is not a verified registered receipt object")
    _reference, original_sha, original_bytes = current
    expected_bytes = _json_file(receipt.to_dict())
    if receipt.canonical_bytes != expected_bytes:
        raise ValueError("programme receipt canonical bytes changed after verification")
    payload = receipt.to_dict()
    payload.pop("receipt_sha256")
    if receipt.receipt_sha256 != _sha256(_json_file(payload)):
        raise ValueError("programme receipt checksum changed after verification")
    if receipt.receipt_sha256 != original_sha or receipt.canonical_bytes != original_bytes:
        raise ValueError("programme receipt original registered identity was mutated")
    _validate_ledger(receipt.ledger)


def _reverify_final_eligibility_receipt_object(receipt: FinalCandidateEligibilityReceipt) -> None:
    current = _ISSUED_FINAL_ELIGIBILITY_OBJECTS.get(id(receipt))
    if current is None or current[0]() is not receipt:
        raise ValueError("eligibility receipt is not a verified registered issued receipt object")
    _reference, original_sha, original_bytes = current
    if receipt.canonical_bytes != canonical_json_bytes(receipt.to_payload()):
        raise ValueError("eligibility receipt canonical bytes changed after issue")
    if receipt.receipt_sha256 != _sha256(receipt.canonical_bytes):
        raise ValueError("eligibility receipt checksum changed after issue")
    if receipt.receipt_sha256 != original_sha or receipt.canonical_bytes != original_bytes:
        raise ValueError("eligibility receipt original registered identity was mutated")


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = _freeze_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("expected mapping to freeze")
    return frozen


def _to_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _secure_write_payload(
    root: Path, relative_name: str, content: bytes, *, sync: bool = True
) -> None:
    parts = PurePosixPath(relative_name).parts
    if not parts:
        raise ValueError("receipt artifact path must be non-empty")
    root_fd = os.open(
        root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    opened_fds: list[int] = [root_fd]
    current_fd = root_fd
    try:
        for directory in parts[:-1]:
            try:
                os.mkdir(directory, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            metadata = os.fstat(next_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError("receipt artifact directory must be regular")
            opened_fds.append(next_fd)
            current_fd = next_fd
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(parts[-1], file_flags, 0o600, dir_fd=current_fd)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("receipt artifact file must be regular")
            _write_all(fd, content)
            if sync:
                os.fsync(fd)
        finally:
            os.close(fd)
        if sync:
            os.fsync(current_fd)
    finally:
        for descriptor in reversed(opened_fds):
            os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("receipt artifact write made no progress")
        view = view[written:]


def _fsync_parent_directory(path: Path) -> None:
    try:
        descriptor = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_identical_payloads(directory: Path, payloads: Mapping[str, bytes]) -> None:
    actual = set(bounded_regular_files(directory, maximum=_MAX_FILES))
    if actual != set(payloads):
        raise RuntimeError("receipt contains missing or unexpected files")
    for name, expected in payloads.items():
        if read_bounded_regular(directory / PurePosixPath(name), len(expected)) != expected:
            raise RuntimeError("receipt identity conflict")


def _artifact_payloads(artifacts: Mapping[str, bytes | str]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for name, content in artifacts.items():
        clean = _artifact_name(name)
        if clean in {_MACHINE_RECEIPT, _HUMAN_SUMMARY} or clean in payloads:
            raise ValueError("duplicate or reserved receipt artifact path")
        if isinstance(content, str):
            payloads[clean] = content.encode("utf-8")
        elif isinstance(content, bytes):
            payloads[clean] = content
        else:
            raise TypeError("receipt artifact content must be str or bytes")
    return dict(sorted(payloads.items()))


def _payload_hashes(payloads: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha256(content) for name, content in sorted(payloads.items())}


def _artifact_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("receipt artifact path must be non-empty")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or any(part == "" for part in pure.parts):
        raise ValueError("receipt artifact path is unsafe")
    if any(part.startswith(".") for part in pure.parts):
        raise ValueError("receipt artifact path cannot contain hidden path parts")
    return pure.as_posix()


def _programme_summary(receipt: ProgrammeReceipt) -> str:
    return (
        f"# {receipt.programme_id}\n"
        "Phase 5 validation programme receipt.\n"
        f"Ledger entries: {len(receipt.ledger)}.\n"
        "Final holdout rows accessed: 0 unless a separate access-attempt record exists.\n"
    )


def _evaluation_summary(receipt: EvaluationReceipt) -> str:
    return (
        f"# {receipt.evaluation_id}\n"
        f"Attempt: {receipt.attempt}.\n"
        f"Execution: {receipt.terminal_state['execution_status']}.\n"
        f"Decision: {receipt.terminal_state['decision']}.\n"
    )


def _json_file(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_config(config: ValidationProgrammeConfig) -> None:
    if not isinstance(config, ValidationProgrammeConfig):
        raise TypeError("receipt requires a ValidationProgrammeConfig")
    if config.roster != VALIDATION_SLOT_ROSTER:
        raise ValueError("receipt requires the exact frozen validation roster")
    if config.roster_sha256 != _VALIDATION_ROSTER_SHA256:
        raise ValueError("receipt roster SHA differs from frozen roster")


def _required_int(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"{label} must be an integer")
    return raw


def _string_mapping(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise TypeError("expected a mapping")
    return {str(key): str(value) for key, value in raw.items()}


def _mapping(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError("expected a mapping")
    return dict(raw)


def _mapping_tuple(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise TypeError("expected a sequence")
    return tuple(_mapping(item) for item in raw)


__all__ = [
    "AccessState",
    "EvaluationReceipt",
    "EvaluationReceiptRequest",
    "FinalAccessAuthority",
    "FinalCandidateEligibilityReceipt",
    "OpenedFinalRows",
    "ProgrammeReceipt",
    "ReceiptPublication",
    "build_evaluation_receipt",
    "build_final_access_authority",
    "build_programme_receipt",
    "issue_final_candidate_eligibility",
    "open_final_holdout_rows",
    "publish_evaluation_receipt_batch",
    "publish_evaluation_receipt",
    "publish_programme_receipt",
    "verify_evaluation_receipt",
    "verify_programme_receipt",
]
