from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, replace
from pathlib import Path
from typing import cast
from threading import Barrier, Thread

import pytest

from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationSlotKind,
    ValidationTerminalState,
    ValidationWorkBudget,
)
from market_structure_lab.research.splits import (
    CandidateEligibility,
    FinalHoldoutBatch,
    SymbolCoverage,
    freeze_common_grid_split,
    freeze_final_batch,
)
from market_structure_lab.research.receipts import (
    AccessState,
    EvaluationReceipt,
    ProgrammeReceipt,
    build_evaluation_receipt,
    build_final_access_authority,
    build_programme_receipt,
    issue_final_candidate_eligibility,
    open_final_holdout_rows,
    publish_evaluation_receipt,
    publish_programme_receipt,
    verify_evaluation_receipt,
    verify_programme_receipt,
)

TASK14_CLOSEOUT = "5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d"
TASK14_EVIDENCE = "3482882c864f1471ec1dd682631544d0c404c542"
TASK15_PLAN = "807ac28ac5616fb837c1ccea1e2bc47572ae3984"
TASK15_EVIDENCE = "afce8e889aa645cd9e48e90631698b87866f287f"
IMPLEMENTATION_CHECKPOINT = "e655152ab729b3e1e530f1bc044febca3509a6fd"
IMPLEMENTATION_EVIDENCE = "6da307a0d4756c61ddcabdf01f00d828afb55d7e"
IMPLEMENTATION_DOCUMENT = "d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111"


def _config(**changes: object) -> ValidationProgrammeConfig:
    values: dict[str, object] = {
        "task14_closeout_commit": TASK14_CLOSEOUT,
        "task14_evidence_commit": TASK14_EVIDENCE,
        "task15_plan_commit": TASK15_PLAN,
        "task15_evidence_commit": TASK15_EVIDENCE,
        "implementation_plan_checkpoint": IMPLEMENTATION_CHECKPOINT,
        "implementation_plan_evidence_commit": IMPLEMENTATION_EVIDENCE,
        "implementation_plan_document_sha256": IMPLEMENTATION_DOCUMENT,
        "code_commit": "1" * 40,
        "lockfile_sha256": "2" * 64,
        "dataset_sha256": "3" * 64,
        "cost_policy_sha256": "4" * 64,
        "control_policy_sha256": "5" * 64,
        "split_sha256": "6" * 64,
        "profile_config_sha256": "7" * 64,
        "source_price_precision_sha256": "8" * 64,
        "families": EXPECTED_FAMILIES,
        "roster": VALIDATION_SLOT_ROSTER,
        "work_budget": ValidationWorkBudget(),
    }
    values.update(changes)
    return ValidationProgrammeConfig(**values)  # type: ignore[arg-type]


def _terminal_by_slot(
    state: ValidationTerminalState | None = None,
) -> dict[str, ValidationTerminalState]:
    terminal = state or ValidationTerminalState(
        ExecutionStatus.COMPLETED,
        ScientificDecision.REJECTED,
    )
    return {slot.slot_id: terminal for slot in VALIDATION_SLOT_ROSTER}


def _candidate(
    programme_id: str, family: str, suffix: str, *, eligible: bool = True
) -> CandidateEligibility:
    return CandidateEligibility(
        programme_id=programme_id,
        candidate_id=f"HC-{suffix * 64}",
        family=family,
        eligible=eligible,
        eligibility_receipt_sha256=suffix * 64,
    )


def _issued_candidate(
    config: ValidationProgrammeConfig, family: str, suffix: str, *, eligible: bool = True
):
    return issue_final_candidate_eligibility(
        config,
        candidate_id=f"HC-{suffix * 64}",
        family=family,
        eligible=eligible,
    )


def _authority(config: ValidationProgrammeConfig, batch: FinalHoldoutBatch, *issued: object):
    return build_final_access_authority(
        config,
        programme_receipt=build_programme_receipt(config, terminal_by_slot=_terminal_by_slot()),
        batch=batch,
        eligibility_receipts=issued,  # type: ignore[arg-type]
    )


def _batch(
    config: ValidationProgrammeConfig, *eligibilities: CandidateEligibility
) -> FinalHoldoutBatch:
    from datetime import UTC, datetime

    coverages = tuple(
        SymbolCoverage(
            symbol=symbol,
            complete_start=datetime(2022, 1, 1, tzinfo=UTC),
            complete_end=datetime(2024, 1, 9, tzinfo=UTC),
            source_conflict=False,
            mapping_compatible=True,
            coverage_sha256=hash_json("receipt-test-coverage", symbol),
        )
        for symbol in ("ADAUSDT", "APTUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT")
    )
    split = freeze_common_grid_split(
        programme_id=config.programme_id,
        coverages=coverages,
        timeframe="1h",
        budget=config.work_budget,
    )
    if not eligibilities:
        eligibilities = (_candidate(config.programme_id, "A", "a"),)
    return freeze_final_batch(
        programme_id=config.programme_id,
        split=split,
        eligibilities=eligibilities,
        budget=config.work_budget,
    )


def _forged_batch(batch: FinalHoldoutBatch, **changes: object) -> FinalHoldoutBatch:
    forged = object.__new__(FinalHoldoutBatch)
    for item in fields(FinalHoldoutBatch):
        if item.name == "factory_token":
            continue
        object.__setattr__(forged, item.name, changes.get(item.name, getattr(batch, item.name)))
    return forged


def _receipt_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_programme_receipt_has_stable_canonical_identity_and_exact_ledger() -> None:
    config = _config()

    receipt = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    same = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())

    assert isinstance(receipt, ProgrammeReceipt)
    assert receipt.programme_id == config.programme_id
    assert receipt.receipt_sha256 == same.receipt_sha256
    assert receipt.canonical_bytes == same.canonical_bytes
    assert receipt.hashes == {
        "code_commit": config.code_commit,
        "lockfile_sha256": config.lockfile_sha256,
        "dataset_sha256": config.dataset_sha256,
        "config_sha256": config.sha256,
        "cost_policy_sha256": config.cost_policy_sha256,
        "control_policy_sha256": config.control_policy_sha256,
        "work_budget_sha256": config.work_budget.sha256,
    }
    assert len(receipt.ledger) == 1_104
    assert sum(item["kind"] == ValidationSlotKind.CORE.value for item in receipt.ledger) == 152
    assert sum(item["primary"] is True for item in receipt.ledger) == 64
    assert sum(item["kind"] == ValidationSlotKind.BASELINE.value for item in receipt.ledger) == 192
    assert (
        sum(item["kind"] == ValidationSlotKind.NEGATIVE_CONTROL.value for item in receipt.ledger)
        == 192
    )
    assert (
        sum(item["kind"] == ValidationSlotKind.ROBUSTNESS.value for item in receipt.ledger) == 256
    )
    assert (
        sum(item["kind"] == ValidationSlotKind.PERTURBATION.value for item in receipt.ledger) == 184
    )
    assert sum(item["kind"] == ValidationSlotKind.EXPOSURE.value for item in receipt.ledger) == 64
    assert sum(item["kind"] == ValidationSlotKind.CAPACITY.value for item in receipt.ledger) == 64
    assert [item["slot_id"] for item in receipt.ledger] == [
        slot.slot_id for slot in VALIDATION_SLOT_ROSTER
    ]
    assert [item["family"] for item in receipt.ledger] == [
        slot.family for slot in VALIDATION_SLOT_ROSTER
    ]
    assert [item["role"] for item in receipt.ledger] == [
        slot.role for slot in VALIDATION_SLOT_ROSTER
    ]
    assert [item["timeframe"] for item in receipt.ledger] == [
        slot.timeframe for slot in VALIDATION_SLOT_ROSTER
    ]
    assert [item["horizon_hours"] for item in receipt.ledger] == [
        slot.horizon_hours for slot in VALIDATION_SLOT_ROSTER
    ]
    ledger_payload = cast(list[dict[str, object]], receipt.to_dict()["ledger"])
    assert [item["parameters"] for item in ledger_payload] == [
        slot.to_dict()["parameters"] for slot in VALIDATION_SLOT_ROSTER
    ]


def test_programme_receipt_rejects_missing_extra_reordered_or_incompatible_terminal_slots() -> None:
    config = _config()
    terminal = _terminal_by_slot()

    missing = dict(terminal)
    missing.pop(VALIDATION_SLOT_ROSTER[0].slot_id)
    with pytest.raises(ValueError, match="exact frozen 1,104-slot ledger"):
        build_programme_receipt(config, terminal_by_slot=missing)

    extra = dict(terminal)
    extra["VS-9999"] = ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.REJECTED
    )
    with pytest.raises(ValueError, match="exact frozen 1,104-slot ledger"):
        build_programme_receipt(config, terminal_by_slot=extra)

    with pytest.raises(ValueError, match="terminal state"):
        build_programme_receipt(
            config,
            terminal_by_slot={
                slot.slot_id: ("completed", "rejected")  # type: ignore[misc]
                for slot in VALIDATION_SLOT_ROSTER
            },  # type: ignore[misc, dict-item]
        )

    with pytest.raises(ValueError, match="completed.*not_evaluated"):
        ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.NOT_EVALUATED)


def test_evaluation_receipt_is_one_vr_per_slot_and_retry_retains_distinct_attempts() -> None:
    config = _config()
    slot = VALIDATION_SLOT_ROSTER[0]
    terminal = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.REJECTED)

    first = build_evaluation_receipt(config, slot=slot, terminal_state=terminal, attempt=1)
    retry = build_evaluation_receipt(config, slot=slot, terminal_state=terminal, attempt=2)

    assert isinstance(first, EvaluationReceipt)
    assert first.evaluation_id.startswith("VR-")
    assert first.evaluation_id == retry.evaluation_id
    assert first.receipt_sha256 != retry.receipt_sha256
    assert first.attempt == 1
    assert retry.attempt == 2
    assert first.slot["slot_id"] == slot.slot_id

    foreign_slot = replace(slot, slot_id="VS-9999")
    with pytest.raises(ValueError, match="owned by the validation programme"):
        build_evaluation_receipt(config, slot=foreign_slot, terminal_state=terminal, attempt=1)


def test_receipt_publication_is_immutable_no_follow_bounded_and_byte_replayable(
    tmp_path: Path,
) -> None:
    config = _config()
    terminal = _terminal_by_slot()
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    programme_a = publish_programme_receipt(
        config,
        terminal_by_slot=terminal,
        output_root=root_a,
        artifacts={"ledger-note.txt": b"synthetic no final rows\n"},
    )
    evaluation_a = publish_evaluation_receipt(
        config,
        slot=VALIDATION_SLOT_ROSTER[0],
        terminal_state=terminal[VALIDATION_SLOT_ROSTER[0].slot_id],
        attempt=1,
        output_root=root_a,
        artifacts={"metric.json": canonical_json("receipt-test-metric", {"value": 1})},
    )
    publish_programme_receipt(
        config,
        terminal_by_slot=terminal,
        output_root=root_b,
        artifacts={"ledger-note.txt": b"synthetic no final rows\n"},
    )
    publish_evaluation_receipt(
        config,
        slot=VALIDATION_SLOT_ROSTER[0],
        terminal_state=terminal[VALIDATION_SLOT_ROSTER[0].slot_id],
        attempt=1,
        output_root=root_b,
        artifacts={"metric.json": canonical_json("receipt-test-metric", {"value": 1})},
    )

    assert verify_programme_receipt(programme_a.path) == programme_a.receipt
    assert verify_evaluation_receipt(evaluation_a.path, config=config) == evaluation_a.receipt
    assert _receipt_tree(root_a) == _receipt_tree(root_b)

    same = publish_programme_receipt(
        config,
        terminal_by_slot=terminal,
        output_root=root_a,
        artifacts={"ledger-note.txt": b"synthetic no final rows\n"},
    )
    assert same.path == programme_a.path

    receipt_path = programme_a.path / "machine-receipt.json"
    original = receipt_path.read_bytes()
    receipt_path.write_bytes(original + b" ")
    with pytest.raises(RuntimeError, match="canonical|tamper|checksum|bounded|size"):
        verify_programme_receipt(programme_a.path)
    receipt_path.write_bytes(original)

    (programme_a.path / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or unexpected"):
        verify_programme_receipt(programme_a.path)
    (programme_a.path / "extra.txt").unlink()

    (programme_a.path / "human-summary.txt").unlink()
    with pytest.raises(RuntimeError, match="missing or unexpected"):
        verify_programme_receipt(programme_a.path)

    symlink_dir = tmp_path / "symlink-root"
    symlink_dir.symlink_to(programme_a.path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink|reparse"):
        verify_programme_receipt(symlink_dir)

    with pytest.raises(ValueError, match="artifact.*bytes"):
        publish_programme_receipt(
            config,
            terminal_by_slot=terminal,
            output_root=tmp_path / "small-budget",
            artifacts={"too-big.txt": b"x" * 128},
            maximum_total_bytes=32,
        )


def test_final_access_record_commits_before_iterator_and_replay_or_forgery_rejects(
    tmp_path: Path,
) -> None:
    config = _config()
    issued = _issued_candidate(config, "A", "a")
    batch = _batch(config, issued.eligibility)
    access_root = tmp_path / "access"
    access_root.mkdir()
    events: list[str] = []

    def rows(access: AccessState) -> Iterator[dict[str, str]]:
        events.append(f"factory:{access.access_record_path.exists()}")
        return iter(({"row": "synthetic"},))

    authority = _authority(config, batch, issued)
    opened = open_final_holdout_rows(
        authority,
        access_root=access_root,
        row_source_factory=rows,
    )

    assert events == ["factory:True"]
    assert list(opened.rows) == [{"row": "synthetic"}]
    assert opened.access.programme_id == config.programme_id

    with pytest.raises(RuntimeError, match="already exists|replay|access attempt"):
        open_final_holdout_rows(authority, access_root=access_root, row_source_factory=rows)

    forged = _forged_batch(batch, batch_sha256="f" * 64)
    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    with pytest.raises(ValueError, match="canonical metadata"):
        build_final_access_authority(
            config,
            programme_receipt=build_programme_receipt(config, terminal_by_slot=_terminal_by_slot()),
            batch=forged,
            eligibility_receipts=(issued,),
        )

    wrong_config = _config(dataset_sha256="9" * 64)
    wrong_root = tmp_path / "wrong"
    wrong_root.mkdir()
    with pytest.raises(ValueError, match="programme"):
        build_final_access_authority(
            wrong_config,
            programme_receipt=build_programme_receipt(
                wrong_config, terminal_by_slot=_terminal_by_slot()
            ),
            batch=batch,
            eligibility_receipts=(issued,),
        )


def test_empty_final_batch_touches_nothing_and_concurrent_access_allows_one_attempt(
    tmp_path: Path,
) -> None:
    config = _config()
    empty_issued = _issued_candidate(config, "A", "a", eligible=False)
    empty = _batch(config, empty_issued.eligibility)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    with pytest.raises(ValueError, match="empty final batch"):
        build_final_access_authority(
            config,
            programme_receipt=build_programme_receipt(config, terminal_by_slot=_terminal_by_slot()),
            batch=empty,
            eligibility_receipts=(empty_issued,),
        )
    assert list(empty_root.iterdir()) == []

    issued_b = _issued_candidate(config, "B", "b")
    issued_a = _issued_candidate(config, "A", "a")
    batch = _batch(config, issued_b.eligibility, issued_a.eligibility)
    authority = _authority(config, batch, issued_a, issued_b)
    assert batch.family_order == ("A", "B")
    assert batch.candidate_ids == tuple(sorted(batch.candidate_ids))
    access_root = tmp_path / "race"
    access_root.mkdir()
    barrier = Barrier(2)
    results: list[str] = []

    def row_factory(access: AccessState) -> Iterator[dict[str, str]]:
        yield {"record": access.access_receipt_sha256}

    def attempt() -> None:
        barrier.wait()
        try:
            opened = open_final_holdout_rows(
                authority,
                access_root=access_root,
                row_source_factory=row_factory,
            )
            list(opened.rows)
            results.append("opened")
        except RuntimeError:
            results.append("rejected")

    threads = [Thread(target=attempt), Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["opened", "rejected"]
    assert len(list(access_root.iterdir())) == 1


def test_access_attempt_is_consumed_when_row_factory_crashes(tmp_path: Path) -> None:
    config = _config()
    issued = _issued_candidate(config, "A", "a")
    batch = _batch(config, issued.eligibility)
    authority = _authority(config, batch, issued)
    access_root = tmp_path / "crash"
    access_root.mkdir()

    def crashing_rows(access: AccessState) -> Iterator[dict[str, str]]:
        raise RuntimeError("synthetic crash after access record")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        open_final_holdout_rows(
            authority,
            access_root=access_root,
            row_source_factory=crashing_rows,
        )
    assert len(list(access_root.iterdir())) == 1

    with pytest.raises(RuntimeError, match="already exists|replay|access attempt"):
        open_final_holdout_rows(
            authority,
            access_root=access_root,
            row_source_factory=lambda access: iter(()),
        )


def test_final_batch_rejects_stale_reordered_incomplete_extra_or_wrong_programme_before_iteration(
    tmp_path: Path,
) -> None:
    config = _config()
    issued = _issued_candidate(config, "A", "a")
    programme = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    batch = _batch(config, issued.eligibility)
    access_root = tmp_path / "stale"
    access_root.mkdir()
    called = False

    def should_not_iterate(access: AccessState) -> Iterator[dict[str, str]]:
        nonlocal called
        called = True
        yield {"bad": access.access_receipt_sha256}

    stale = _forged_batch(batch, eligibility_decisions=())
    with pytest.raises(ValueError, match="canonical metadata|eligibility"):
        build_final_access_authority(
            config, programme_receipt=programme, batch=stale, eligibility_receipts=(issued,)
        )
    assert called is False

    extra = _forged_batch(
        batch,
        candidate_ids=(*batch.candidate_ids, f"HC-{'c' * 64}"),
        family_order=(*batch.family_order, "C"),
        eligibility_receipt_sha256s=(*batch.eligibility_receipt_sha256s, "c" * 64),
    )
    with pytest.raises(ValueError, match="canonical metadata|unsupported|differ"):
        build_final_access_authority(
            config, programme_receipt=programme, batch=extra, eligibility_receipts=(issued,)
        )
    assert called is False

    wrong_programme = _config(dataset_sha256="9" * 64)
    wrong = _forged_batch(batch, programme_id=wrong_programme.programme_id)
    with pytest.raises(ValueError, match="programme|canonical metadata"):
        build_final_access_authority(
            config, programme_receipt=programme, batch=wrong, eligibility_receipts=(issued,)
        )
    assert called is False


def _rewrite_machine_receipt(path: Path, mutator: object) -> None:
    import json
    from typing import Callable, cast

    receipt_path = path / "machine-receipt.json"
    raw = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    cast(Callable[[dict[str, object]], None], mutator)(raw)
    raw.pop("receipt_sha256", None)
    raw["receipt_sha256"] = _plain_sha256(_plain_json(raw))
    receipt_path.write_bytes(_plain_json(raw))


def _plain_json(payload: dict[str, object]) -> bytes:
    import json

    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        + b"\n"
    )


def _plain_sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def test_programme_verification_rejects_full_slot_payload_tamper_even_with_recomputed_checksum(
    tmp_path: Path,
) -> None:
    config = _config()
    publication = publish_programme_receipt(
        config,
        terminal_by_slot=_terminal_by_slot(),
        output_root=tmp_path,
    )

    def mutate(raw: dict[str, object]) -> None:
        ledger = raw["ledger"]
        assert isinstance(ledger, list)
        first = ledger[0]
        assert isinstance(first, dict)
        first["timeframe"] = "4h" if first["timeframe"] == "1h" else "1h"

    _rewrite_machine_receipt(publication.path, mutate)

    with pytest.raises(RuntimeError, match="slot|ledger|canonical|frozen|contract"):
        verify_programme_receipt(publication.path)


def test_evaluation_verification_requires_config_hashes_slot_ownership_and_vr_derivation(
    tmp_path: Path,
) -> None:
    config = _config()
    terminal = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.REJECTED)
    publication = publish_evaluation_receipt(
        config,
        slot=VALIDATION_SLOT_ROSTER[0],
        terminal_state=terminal,
        attempt=1,
        output_root=tmp_path,
    )

    assert verify_evaluation_receipt(publication.path, config=config) == publication.receipt

    def empty_hashes(raw: dict[str, object]) -> None:
        raw["hashes"] = {}

    _rewrite_machine_receipt(publication.path, empty_hashes)
    with pytest.raises(RuntimeError, match="hash|contract|config"):
        verify_evaluation_receipt(publication.path, config=config)

    repaired = publish_evaluation_receipt(
        config,
        slot=VALIDATION_SLOT_ROSTER[0],
        terminal_state=terminal,
        attempt=2,
        output_root=tmp_path,
    )

    def vs9999(raw: dict[str, object]) -> None:
        slot = raw["slot"]
        assert isinstance(slot, dict)
        slot["slot_id"] = "VS-9999"
        raw["evaluation_id"] = "VR-" + "9" * 64

    _rewrite_machine_receipt(repaired.path, vs9999)
    with pytest.raises(RuntimeError, match="slot|owned|evaluation|VR"):
        verify_evaluation_receipt(repaired.path, config=config)


def test_final_access_requires_verified_programme_authority_and_eligibility_receipts_before_record(
    tmp_path: Path,
) -> None:
    config = _config()
    programme = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    issued = issue_final_candidate_eligibility(
        config,
        candidate_id=f"HC-{'a' * 64}",
        family="A",
        eligible=True,
    )
    batch = _batch(config, issued.eligibility)
    root = tmp_path / "authority"
    root.mkdir()
    called = False

    def rows(access: AccessState) -> Iterator[dict[str, str]]:
        nonlocal called
        called = True
        return iter(({"row": access.access_receipt_sha256},))

    public_only = _batch(config, _candidate(config.programme_id, "A", "a"))
    with pytest.raises(ValueError, match="eligibility|authority|verified"):
        build_final_access_authority(
            config,
            programme_receipt=programme,
            batch=public_only,
            eligibility_receipts=(),
        )
    assert list(root.iterdir()) == []
    assert called is False

    authority = build_final_access_authority(
        config,
        programme_receipt=programme,
        batch=batch,
        eligibility_receipts=(issued,),
    )
    opened = open_final_holdout_rows(
        authority,
        access_root=root,
        row_source_factory=rows,
    )
    assert list(opened.rows) == [{"row": opened.access.access_receipt_sha256}]
    assert called is True

    stale = _forged_batch(batch, candidate_ids=())
    with pytest.raises(ValueError, match="batch|eligibility|canonical"):
        build_final_access_authority(
            config,
            programme_receipt=programme,
            batch=stale,
            eligibility_receipts=(issued,),
        )


def test_publication_budgets_come_from_config_work_budget_before_staging(tmp_path: Path) -> None:
    config = _config(work_budget=replace(ValidationWorkBudget(), max_artifacts=1))
    output = tmp_path / "too-many"

    with pytest.raises(ValueError, match="artifact.*entries|budget"):
        publish_programme_receipt(
            config,
            terminal_by_slot=_terminal_by_slot(),
            output_root=output,
        )
    assert not output.exists()

    tiny_bytes = _config(work_budget=replace(ValidationWorkBudget(), max_artifact_bytes=16))
    output_bytes = tmp_path / "too-large"
    with pytest.raises(ValueError, match="artifact.*bytes|budget"):
        publish_evaluation_receipt(
            tiny_bytes,
            slot=VALIDATION_SLOT_ROSTER[0],
            terminal_state=ValidationTerminalState(
                ExecutionStatus.COMPLETED,
                ScientificDecision.REJECTED,
            ),
            attempt=1,
            output_root=output_bytes,
        )
    assert not output_bytes.exists()


def test_access_authority_rejects_mutated_or_forged_receipts_before_access_record(
    tmp_path: Path,
) -> None:
    config = _config()
    programme = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    issued = _issued_candidate(config, "A", "a")
    batch = _batch(config, issued.eligibility)
    access_root = tmp_path / "mutated-authority"
    access_root.mkdir()
    called = False

    def rows(access: AccessState) -> Iterator[dict[str, str]]:
        nonlocal called
        called = True
        return iter(({"row": access.access_receipt_sha256},))

    with pytest.raises(TypeError):
        programme.ledger[0]["timeframe"] = "4h"  # type: ignore[index]

    mutated_bytes = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    object.__setattr__(mutated_bytes, "canonical_bytes", mutated_bytes.canonical_bytes + b" ")
    with pytest.raises(ValueError, match="canonical|checksum|verified|receipt"):
        build_final_access_authority(
            config,
            programme_receipt=mutated_bytes,
            batch=batch,
            eligibility_receipts=(issued,),
        )
    assert list(access_root.iterdir()) == []
    assert called is False

    forged_programme = object.__new__(ProgrammeReceipt)
    for item in fields(ProgrammeReceipt):
        if item.name == "factory_token":
            continue
        object.__setattr__(forged_programme, item.name, getattr(programme, item.name))
    with pytest.raises(ValueError, match="verified|forged|receipt"):
        build_final_access_authority(
            config,
            programme_receipt=forged_programme,
            batch=batch,
            eligibility_receipts=(issued,),
        )

    forged_eligibility = object.__new__(type(issued))
    for item in fields(type(issued)):
        if item.name == "factory_token":
            continue
        object.__setattr__(forged_eligibility, item.name, getattr(issued, item.name))
    with pytest.raises(ValueError, match="verified|forged|eligibility"):
        build_final_access_authority(
            config,
            programme_receipt=programme,
            batch=batch,
            eligibility_receipts=(forged_eligibility,),
        )
    assert list(access_root.iterdir()) == []
    assert called is False


def test_publication_uses_secure_no_follow_writes_without_path_write_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()

    def forbidden_write_bytes(self: Path, data: bytes) -> int:
        raise AssertionError(f"Path.write_bytes bypassed secure writer for {self}")

    monkeypatch.setattr(Path, "write_bytes", forbidden_write_bytes)

    publication = publish_programme_receipt(
        config,
        terminal_by_slot=_terminal_by_slot(),
        output_root=tmp_path / "secure-write",
        artifacts={"nested/evidence.txt": b"bounded\n"},
    )

    assert (
        verify_programme_receipt(publication.path).receipt_sha256
        == publication.receipt.receipt_sha256
    )


def test_concurrent_identical_publication_converges_on_verified_final(tmp_path: Path) -> None:
    config = _config()
    terminal = _terminal_by_slot()
    barrier = Barrier(2)
    results: list[Path] = []
    errors: list[str] = []

    def publish() -> None:
        barrier.wait()
        try:
            result = publish_programme_receipt(
                config,
                terminal_by_slot=terminal,
                output_root=tmp_path / "concurrent",
                artifacts={"same.txt": b"same\n"},
            )
            results.append(result.path)
        except Exception as error:  # pragma: no cover - asserted below for thread diagnostics
            errors.append(f"{type(error).__name__}: {error}")

    threads = (Thread(target=publish), Thread(target=publish))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert verify_programme_receipt(results[0]).programme_id == config.programme_id


def test_access_authority_rejects_coherently_mutated_registered_receipts_before_access(
    tmp_path: Path,
) -> None:
    config = _config()
    programme = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    issued = _issued_candidate(config, "A", "a")
    batch = _batch(config, issued.eligibility)
    root = tmp_path / "coherent-mutation"
    root.mkdir()
    called = False

    def rows(access: AccessState) -> Iterator[dict[str, str]]:
        nonlocal called
        called = True
        return iter(({"row": access.access_receipt_sha256},))

    # Coherent mutation: all current programme receipt self-checks can pass, but
    # authority must compare against the identity registered at construction time.
    mutated_programme = build_programme_receipt(config, terminal_by_slot=_terminal_by_slot())
    mutated_programme_payload = mutated_programme.to_dict()
    ledger = mutated_programme_payload["ledger"]
    assert isinstance(ledger, list)
    first = ledger[0]
    assert isinstance(first, dict)
    terminal = first["terminal_state"]
    assert isinstance(terminal, dict)
    terminal["decision"] = ScientificDecision.INCONCLUSIVE.value
    mutated_programme_payload.pop("receipt_sha256")
    mutated_programme_payload["receipt_sha256"] = _plain_sha256(
        _plain_json(mutated_programme_payload)
    )
    mutated_programme_bytes = _plain_json(mutated_programme_payload)
    object.__setattr__(
        mutated_programme,
        "ledger",
        tuple(cast(list[dict[str, object]], mutated_programme_payload["ledger"])),
    )
    object.__setattr__(mutated_programme, "canonical_bytes", mutated_programme_bytes)
    object.__setattr__(
        mutated_programme, "receipt_sha256", mutated_programme_payload["receipt_sha256"]
    )

    with pytest.raises(ValueError, match="original|registered|mutated|identity"):
        build_final_access_authority(
            config,
            programme_receipt=mutated_programme,
            batch=batch,
            eligibility_receipts=(issued,),
        )

    # Same coherent-mutation bypass attempt for issued final eligibility receipt.
    mutated_issued = _issued_candidate(config, "A", "a")
    mutated_payload = mutated_issued.to_payload()
    candidate = mutated_payload["candidate"]
    assert isinstance(candidate, dict)
    candidate["candidate_id"] = f"HC-{'b' * 64}"
    mutated_payload.pop("receipt_sha256", None)
    mutated_bytes = _plain_json(mutated_payload)
    mutated_sha = _plain_sha256(mutated_bytes)
    mutated_eligibility = _candidate(config.programme_id, "A", "b", eligible=True)
    object.__setattr__(
        mutated_eligibility,
        "eligibility_receipt_sha256",
        mutated_sha,
    )
    object.__setattr__(mutated_issued, "eligibility", mutated_eligibility)
    object.__setattr__(mutated_issued, "canonical_bytes", mutated_bytes)
    object.__setattr__(mutated_issued, "receipt_sha256", mutated_sha)
    mutated_batch = _batch(config, mutated_eligibility)

    with pytest.raises(ValueError, match="original|registered|mutated|identity"):
        build_final_access_authority(
            config,
            programme_receipt=programme,
            batch=mutated_batch,
            eligibility_receipts=(mutated_issued,),
        )

    assert list(root.iterdir()) == []
    assert called is False
