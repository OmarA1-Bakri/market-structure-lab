"""Immutable Phase 5 no-edge terminal-decision publication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import weakref

from market_structure_lab.core.artifact_io import (
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.fs_durability import durable_move_no_replace, fsync_directory_posix
from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
from market_structure_lab.research import receipts as receipts_module
from market_structure_lab.research import (
    validation_programme_config as validation_programme_config_module,
)
from market_structure_lab.research.receipts import (
    verify_evaluation_receipt,
    verify_programme_receipt,
)
from market_structure_lab.research.validation_programme_config import (
    load_validation_programme_config,
)

_PUBLICATION_SCHEMA = "phase5-terminal-closure-v1"
_PUBLICATION_FILE = "publication.json"
_SUCCESS_FILE = "_SUCCESS"
_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_MAX_PUBLICATION_BYTES = 8 * 1024 * 1024
_MAX_TREE_FILES = 20_000
_MAX_TREE_BYTES = 256 * 1024 * 1024
_FROZEN_PROGRAMME_ID = "VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d"
_FROZEN_CONFIG_FILE_SHA256 = "376eac4c05df279fdeba35b63abebef670409ec566577981fd35b5861cfdd1b7"
_FROZEN_PROGRAMME_RECEIPT_SHA256 = (
    "2b1629261a8b2559daff4fe6fc6a9c31f48ae6432fdc114f3ecb5020ab3d8ed7"
)
_FACTORY_ISSUANCE: dict[int, object] = {}
_VERIFIED: dict[
    int,
    tuple[
        weakref.ReferenceType[Phase5TerminalClosure],
        tuple[object, ...],
        Path,
        Path,
        tuple[tuple[str, str, int], ...],
        bytes,
    ],
] = {}


@dataclass(frozen=True, slots=True)
class _TerminalSnapshot:
    programme_id: str
    runner_version: str
    receipt_parent_sha256: str
    terminal_payloads: tuple[Mapping[str, object], ...]
    receipt_count: int
    unavailable_prerequisites: tuple[str, ...] = ()


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _read_object(path: Path, maximum: int, label: str) -> tuple[bytes, dict[str, object]]:
    content = read_bounded_regular(path, maximum)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return content, payload


def _tree_snapshot(root: Path) -> tuple[tuple[tuple[str, str, int], ...], str]:
    require_regular_directory(root)
    entries: list[tuple[str, str, int]] = []
    total_bytes = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names.sort()
        files.sort()
        directory_path = Path(directory)
        for name in names:
            candidate = directory_path / name
            if candidate.is_symlink():
                raise ValueError("programme tree contains a symbolic-link directory")
        for name in files:
            candidate = directory_path / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("programme tree contains a non-regular artifact")
            content = read_bounded_regular(candidate, _MAX_TREE_BYTES)
            total_bytes += len(content)
            if total_bytes > _MAX_TREE_BYTES:
                raise ValueError("programme tree exceeds its authenticated byte bound")
            entries.append((candidate.relative_to(root).as_posix(), _sha256(content), len(content)))
            if len(entries) > _MAX_TREE_FILES:
                raise ValueError("programme tree exceeds its authenticated file bound")
    if not entries:
        raise ValueError("programme tree is empty")
    frozen = tuple(entries)
    identity = _sha256(
        _canonical(
            {
                "domain": "phase5-terminal-programme-tree-v1",
                "files": [
                    {"relative_path": path, "sha256": digest, "bytes": size}
                    for path, digest, size in frozen
                ],
            }
        )
    )
    return frozen, identity


def _holm_payload(snapshot: _TerminalSnapshot) -> list[dict[str, object]]:
    payload_by_slot = {str(item["slot_id"]): item for item in snapshot.terminal_payloads}
    primary = [slot for slot in VALIDATION_SLOT_ROSTER if slot.primary]
    if len(primary) != 64:
        raise ValueError("frozen primary roster cardinality differs")
    output: list[dict[str, object]] = []
    for slot in primary:
        terminal = payload_by_slot[slot.slot_id]
        # The empty-batch terminal publication uses the frozen conservative rule:
        # every failed, incomplete, or inconclusive primary receives effective p=1.
        output.append(
            {
                "slot_id": slot.slot_id,
                "family": slot.family,
                "effective_p_value": 1.0,
                "adjusted_holm_p_value": 1.0,
                "holm_reject": False,
                "scientific_status": terminal["decision"],
            }
        )
    return output


def _implementation_snapshot() -> tuple[dict[str, str], str]:
    source_paths = {
        "research/phase5_terminal.py": Path(__file__),
        "research/receipts.py": Path(str(receipts_module.__file__)),
        "research/validation_programme_config.py": Path(
            str(validation_programme_config_module.__file__)
        ),
    }

    def source_hashes() -> dict[str, str]:
        return {
            name: _sha256(read_bounded_regular(path, 4 * 1024 * 1024))
            for name, path in source_paths.items()
        }

    hashes = source_hashes()
    repeated = source_hashes()
    if repeated != hashes:
        raise ValueError("terminal implementation sources changed during verification")
    identity = _sha256(
        _canonical({"domain": "phase5-terminal-implementation-v1", "sources": hashes})
    )
    return hashes, identity


def _publication_payload(
    *,
    config_bytes: bytes,
    config: Mapping[str, object],
    tree_sha256: str,
    snapshot: _TerminalSnapshot,
) -> dict[str, object]:
    if len(snapshot.terminal_payloads) != len(VALIDATION_SLOT_ROSTER):
        raise ValueError("terminal receipt ledger has missing roster terminals")
    if any(
        payload["decision"] != "not_evaluated"
        or payload["execution_status"] != "failed"
        or payload["computation_completed"] is not False
        or payload["p_value"] is not None
        for payload in snapshot.terminal_payloads
    ):
        raise ValueError("frozen Terminal A requires every slot to be failed/not_evaluated")
    implementation_sources, implementation_sha256 = _implementation_snapshot()
    empty_batch_sha256 = _sha256(
        _canonical(
            {
                "domain": "phase5-empty-eligible-batch-v1",
                "programme_id": snapshot.programme_id,
                "eligible_candidate_ids": [],
            }
        )
    )
    payload: dict[str, object] = {
        "schema_version": _PUBLICATION_SCHEMA,
        "programme_id": snapshot.programme_id,
        "runner_version": snapshot.runner_version,
        "planned_slot_count": len(VALIDATION_SLOT_ROSTER),
        "terminal_slot_count": len(snapshot.terminal_payloads),
        "eligible_candidate_ids": [],
        "eligible_batch_sha256": empty_batch_sha256,
        "holm": _holm_payload(snapshot),
        "final_holdout_marker": "final_holdout_not_opened_empty_batch",
        "final_access_attempts": config["final_access_attempts"],
        "final_rows": config["final_rows"],
        "final_access_records": config["final_access_records"],
        "phase6_status": "scientifically_gated_no_validated_edge",
        "phase7_status": "scientifically_gated_no_validated_edge",
        "config_sha256": _sha256(config_bytes),
        "programme_tree_sha256": tree_sha256,
        "receipt_parent_sha256": snapshot.receipt_parent_sha256,
        "receipt_count": snapshot.receipt_count,
        "not_evaluated_slots": sum(
            item["decision"] == "not_evaluated" for item in snapshot.terminal_payloads
        ),
        "inconclusive_slots": sum(
            item["decision"] == "inconclusive" for item in snapshot.terminal_payloads
        ),
        "failed_slots": sum(
            item["execution_status"] == "failed" for item in snapshot.terminal_payloads
        ),
        "unavailable_prerequisites": list(snapshot.unavailable_prerequisites),
        "implementation_sources": implementation_sources,
        "implementation_sha256": implementation_sha256,
    }
    payload["closure_sha256"] = _sha256(
        _canonical(
            {
                "domain": "phase5-terminal-closure-identity-v1",
                "payload": payload,
            }
        )
    )
    return payload


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Phase5TerminalClosure:
    publication_root: Path
    programme_id: str
    runner_version: str
    closure_sha256: str
    canonical_bytes: bytes = field(repr=False, compare=False)
    _payload: Mapping[str, object] = field(repr=False, compare=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        token = _FACTORY_ISSUANCE.pop(id(_factory_token), None)
        if _factory_token is None or token is not _factory_token:
            raise TypeError("Phase5TerminalClosure requires its publisher factory")
        object.__setattr__(self, "_payload", MappingProxyType(dict(self._payload)))

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return json.loads(self.canonical_bytes)


def _issue_closure(
    *,
    publication_root: Path,
    payload: Mapping[str, object],
    canonical_bytes: bytes,
    programme_root: Path,
    config_path: Path,
    tree_snapshot: tuple[tuple[str, str, int], ...],
    config_bytes: bytes,
) -> Phase5TerminalClosure:
    token = object()
    _FACTORY_ISSUANCE[id(token)] = token
    closure = Phase5TerminalClosure(
        publication_root=publication_root,
        programme_id=str(payload["programme_id"]),
        runner_version=str(payload["runner_version"]),
        closure_sha256=str(payload["closure_sha256"]),
        canonical_bytes=canonical_bytes,
        _payload=payload,
        _factory_token=token,
    )
    snapshot = (
        closure.publication_root,
        closure.programme_id,
        closure.runner_version,
        closure.closure_sha256,
        closure.canonical_bytes,
        closure.to_dict(),
    )
    _VERIFIED[id(closure)] = (
        weakref.ref(closure),
        snapshot,
        programme_root,
        config_path,
        tree_snapshot,
        config_bytes,
    )
    return closure


def _decode_typed_evidence(value: object) -> object:
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise ValueError("programme terminal evidence shape is invalid")
    kind = value["type"]
    raw = value["value"]
    if kind == "string":
        if not isinstance(raw, str):
            raise ValueError("programme terminal string evidence is invalid")
        return raw
    if kind == "integer":
        if not isinstance(raw, str) or not raw.isdigit():
            raise ValueError("programme terminal integer evidence is invalid")
        return int(raw)
    if kind == "sequence":
        if not isinstance(raw, list):
            raise ValueError("programme terminal sequence evidence is invalid")
        return [_decode_typed_evidence(item) for item in raw]
    if kind == "mapping":
        if not isinstance(raw, list):
            raise ValueError("programme terminal mapping evidence is invalid")
        output: dict[str, object] = {}
        for item in raw:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise ValueError("programme terminal mapping member is invalid")
            if item[0] in output:
                raise ValueError("programme terminal mapping contains a duplicate key")
            output[item[0]] = _decode_typed_evidence(item[1])
        return output
    raise ValueError("programme terminal evidence type is unsupported")


def _legacy_terminal_snapshot(
    programme_root: Path,
    config_path: Path,
) -> tuple[_TerminalSnapshot, dict[str, object]]:
    config = load_validation_programme_config(config_path)
    if config.programme_id != _FROZEN_PROGRAMME_ID:
        raise ValueError("terminal config is not the frozen Phase 5 programme parent")
    vp_root = programme_root / config.programme_id
    programme_receipt = verify_programme_receipt(vp_root)
    if programme_receipt.programme_id != config.programme_id:
        raise ValueError("programme receipt parent differs from frozen config")
    if programme_receipt.hashes["config_sha256"] != config.sha256:
        raise ValueError("programme receipt config parent differs")
    if programme_receipt.receipt_sha256 != _FROZEN_PROGRAMME_RECEIPT_SHA256:
        raise ValueError("programme receipt is not the frozen Terminal A parent")
    if set(programme_receipt.artifact_sha256) != {
        "human-summary.txt",
        "programme-evidence.json",
    }:
        raise ValueError("programme receipt has missing or extra terminal evidence")
    if tuple(item["slot_id"] for item in programme_receipt.ledger) != tuple(
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    ):
        raise ValueError("programme receipt violates the exact canonical 1,104-slot roster")

    actual_names = {item.name for item in programme_root.iterdir()}
    expected_names = {config.programme_id}
    by_slot: dict[str, Mapping[str, object]] = {}
    receipt_count = 0
    for child in programme_root.iterdir():
        if child.name == config.programme_id:
            continue
        if not child.name.startswith("VR-"):
            raise ValueError("legacy programme tree contains an extra receipt artifact")
        receipt = verify_evaluation_receipt(child, config=config)
        expected_names.add(child.name)
        slot_id = str(receipt.slot["slot_id"])
        if slot_id in by_slot or receipt.attempt != 1:
            raise ValueError("legacy programme has duplicate or noncanonical slot attempts")
        terminal_state = {
            "slot_id": slot_id,
            "decision": receipt.terminal_state["decision"],
            "execution_status": receipt.terminal_state["execution_status"],
            "computation_completed": receipt.terminal_state["execution_status"] == "completed",
            "p_value": None,
        }
        if set(receipt.artifact_sha256) != {"human-summary.txt", "slot-evidence.json"}:
            raise ValueError("evaluation receipt has missing or extra terminal evidence")
        slot_evidence_bytes, slot_evidence = _read_object(
            child / "slot-evidence.json",
            _MAX_PUBLICATION_BYTES,
            "slot terminal evidence",
        )
        if _sha256(slot_evidence_bytes) != receipt.artifact_sha256["slot-evidence.json"]:
            raise ValueError("slot terminal evidence original bytes changed")
        if slot_evidence.get("domain") != "validation-slot-terminal-evidence-v1":
            raise ValueError("slot terminal evidence domain is invalid")
        decoded_slot = _decode_typed_evidence(slot_evidence.get("payload"))
        if not isinstance(decoded_slot, dict) or decoded_slot != {
            "programme_id": config.programme_id,
            "reason": "missing_prerequisites",
            "slot_id": slot_id,
            "terminal_state": {
                "decision": receipt.terminal_state["decision"],
                "execution_status": receipt.terminal_state["execution_status"],
            },
        }:
            raise ValueError("slot terminal evidence differs from its receipt parent")
        by_slot[slot_id] = MappingProxyType(terminal_state)
        receipt_count += 1
    if actual_names != expected_names or receipt_count != len(VALIDATION_SLOT_ROSTER):
        raise ValueError("legacy programme has missing or extra 1,104-slot receipts")
    terminal_payloads = tuple(by_slot[slot.slot_id] for slot in VALIDATION_SLOT_ROSTER)
    for ledger_item, terminal in zip(programme_receipt.ledger, terminal_payloads, strict=True):
        if ledger_item["terminal_state"] != {
            "execution_status": terminal["execution_status"],
            "decision": terminal["decision"],
        }:
            raise ValueError("evaluation receipt terminal differs from programme ledger")

    evidence_bytes, evidence = _read_object(
        vp_root / "programme-evidence.json",
        _MAX_PUBLICATION_BYTES,
        "programme terminal evidence",
    )
    if _sha256(evidence_bytes) != programme_receipt.artifact_sha256["programme-evidence.json"]:
        raise ValueError("programme terminal evidence original bytes changed")
    if evidence.get("domain") != "validation-programme-terminal-evidence-v1":
        raise ValueError("programme terminal evidence domain is invalid")
    decoded = _decode_typed_evidence(evidence.get("payload"))
    if not isinstance(decoded, dict):
        raise ValueError("programme terminal evidence payload is invalid")
    preflight = decoded.get("preflight_context")
    if (
        decoded.get("programme_id") != config.programme_id
        or decoded.get("reason") != "missing_prerequisites"
        or decoded.get("final_holdout_access_count") != 0
        or not isinstance(preflight, dict)
        or preflight.get("final_holdout_access_count") != 0
        or preflight.get("reason_code") != "missing_prerequisites"
    ):
        raise ValueError("programme terminal evidence does not prove zero-access refusal")
    missing = preflight.get("missing_prerequisites")
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        raise ValueError("programme terminal prerequisite evidence is invalid")
    closure_config: dict[str, object] = {
        "final_access_attempts": 0,
        "final_rows": 0,
        "final_access_records": 0,
    }
    return (
        _TerminalSnapshot(
            programme_id=config.programme_id,
            runner_version="phase5-validation-programme-v1",
            receipt_parent_sha256=programme_receipt.receipt_sha256,
            terminal_payloads=terminal_payloads,
            receipt_count=receipt_count,
            unavailable_prerequisites=tuple(missing),
        ),
        closure_config,
    )


def _derive(
    programme_root: Path,
    config_path: Path,
) -> tuple[dict[str, object], bytes, tuple[tuple[str, str, int], ...], bytes]:
    config_bytes = read_bounded_regular(config_path, _MAX_CONFIG_BYTES)
    if _sha256(config_bytes) != _FROZEN_CONFIG_FILE_SHA256:
        raise ValueError("terminal config original bytes differ from the frozen parent")
    tree_before, _tree_before_sha256 = _tree_snapshot(programme_root)
    snapshot, config = _legacy_terminal_snapshot(programme_root, config_path)
    tree_snapshot, tree_sha256 = _tree_snapshot(programme_root)
    if tree_snapshot != tree_before:
        raise ValueError("programme tree changed during terminal verification")
    if read_bounded_regular(config_path, _MAX_CONFIG_BYTES) != config_bytes:
        raise ValueError("terminal config changed during terminal verification")
    payload = _publication_payload(
        config_bytes=config_bytes,
        config=config,
        tree_sha256=tree_sha256,
        snapshot=snapshot,
    )
    return payload, _canonical(payload), tree_snapshot, config_bytes


def publish_phase5_terminal_closure(
    programme_root: Path,
    config_path: Path,
    output_root: Path,
) -> Phase5TerminalClosure:
    """Publish an atomic no-clobber empty-batch terminal decision."""

    programme = Path(programme_root).resolve(strict=True)
    config = Path(config_path).resolve(strict=True)
    output = Path(output_root).absolute()
    if path_exists_no_follow(output):
        raise FileExistsError(output)
    payload, canonical_bytes, tree_snapshot, config_bytes = _derive(programme, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:

        def write_synced(path: Path, content: bytes) -> None:
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())

        write_synced(stage / _PUBLICATION_FILE, canonical_bytes)
        success_bytes = (str(payload["closure_sha256"]) + "\n").encode()
        write_synced(stage / _SUCCESS_FILE, success_bytes)
        fsync_directory_posix(stage)
        try:
            durable_move_no_replace(stage, output)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
            # WSL DrvFS does not implement renameat2(RENAME_NOREPLACE).  Claim the
            # final directory with atomic mkdir and publish the success marker last;
            # readers reject the directory until both exact artifacts exist.
            output.mkdir()
            try:
                os.replace(stage / _PUBLICATION_FILE, output / _PUBLICATION_FILE)
                fsync_directory_posix(output)
                os.replace(stage / _SUCCESS_FILE, output / _SUCCESS_FILE)
                fsync_directory_posix(output)
                stage.rmdir()
            except BaseException:
                shutil.rmtree(output, ignore_errors=True)
                raise
        fsync_directory_posix(output.parent)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    post_tree, _post_tree_sha256 = _tree_snapshot(programme)
    post_config = read_bounded_regular(config, _MAX_CONFIG_BYTES)
    post_sources, post_implementation_sha256 = _implementation_snapshot()
    if (
        post_tree != tree_snapshot
        or post_config != config_bytes
        or post_sources != payload["implementation_sources"]
        or post_implementation_sha256 != payload["implementation_sha256"]
    ):
        shutil.rmtree(output, ignore_errors=True)
        raise ValueError("terminal parents changed before factory issuance")
    return _issue_closure(
        publication_root=output,
        payload=payload,
        canonical_bytes=canonical_bytes,
        programme_root=programme,
        config_path=config,
        tree_snapshot=tree_snapshot,
        config_bytes=config_bytes,
    )


def load_phase5_terminal_closure(
    publication_root: Path,
    *,
    expected_closure_sha256: str,
    programme_root: Path,
    config_path: Path,
) -> Phase5TerminalClosure:
    """Load a closure only while all original parent bytes still verify."""

    expected = _require_sha256(expected_closure_sha256, "expected_closure_sha256")
    publication = Path(publication_root).resolve(strict=True)
    require_regular_directory(publication)
    names = {item.name for item in publication.iterdir()}
    if names != {_PUBLICATION_FILE, _SUCCESS_FILE}:
        raise ValueError("terminal closure publication has missing or extra artifacts")
    canonical_bytes, stored = _read_object(
        publication / _PUBLICATION_FILE,
        _MAX_PUBLICATION_BYTES,
        "terminal closure publication",
    )
    payload, replay_bytes, tree_snapshot, config_bytes = _derive(
        Path(programme_root).resolve(strict=True),
        Path(config_path).resolve(strict=True),
    )
    if canonical_bytes != replay_bytes or stored != payload:
        raise ValueError("terminal closure publication differs from original parent bytes")
    if stored.get("closure_sha256") != expected:
        raise ValueError("terminal closure identity differs")
    if read_bounded_regular(publication / _SUCCESS_FILE, 256) != (expected + "\n").encode():
        raise ValueError("terminal closure success marker differs")
    return _issue_closure(
        publication_root=publication,
        payload=payload,
        canonical_bytes=canonical_bytes,
        programme_root=Path(programme_root).resolve(strict=True),
        config_path=Path(config_path).resolve(strict=True),
        tree_snapshot=tree_snapshot,
        config_bytes=config_bytes,
    )


def verify_phase5_terminal_closure(
    closure: Phase5TerminalClosure,
) -> Phase5TerminalClosure:
    """Reverify the exact factory object, publication, and original parents."""

    if type(closure) is not Phase5TerminalClosure:
        raise TypeError("terminal closure must be the exact factory-issued type")
    registered = _VERIFIED.get(id(closure))
    if registered is None or registered[0]() is not closure:
        raise ValueError("terminal closure is not the registered original")
    current = (
        closure.publication_root,
        closure.programme_id,
        closure.runner_version,
        closure.closure_sha256,
        closure.canonical_bytes,
        closure.to_dict(),
    )
    if current != registered[1]:
        raise ValueError("terminal closure object changed")
    payload, canonical_bytes, tree_snapshot, config_bytes = _derive(registered[2], registered[3])
    if tree_snapshot != registered[4] or config_bytes != registered[5]:
        raise ValueError("terminal closure original parent bytes changed")
    stored = read_bounded_regular(
        closure.publication_root / _PUBLICATION_FILE,
        _MAX_PUBLICATION_BYTES,
    )
    success = read_bounded_regular(closure.publication_root / _SUCCESS_FILE, 256)
    if (
        stored != canonical_bytes
        or stored != closure.canonical_bytes
        or payload != closure.to_dict()
        or success != (closure.closure_sha256 + "\n").encode()
    ):
        raise ValueError("terminal closure publication bytes changed")
    return closure


__all__ = [
    "Phase5TerminalClosure",
    "load_phase5_terminal_closure",
    "publish_phase5_terminal_closure",
    "verify_phase5_terminal_closure",
]
