from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import fields, replace
import json
from pathlib import Path
from threading import Barrier

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
    _issue_validation_slot_result_v2,
    run_slot_roster_v2,
    validation_slot_result_sha256_v2,
    verify_original_validation_slot_result_v2,
)
from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipt,
    publish_validation_v2_receipts,
    select_terminal_attempts_v2,
    verify_validation_v2_receipt,
    verify_validation_programme_v2,
    verify_validation_v2_receipts,
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
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    results = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
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


@pytest.fixture(scope="module")
def issued_rosters() -> tuple[
    tuple[ValidationSlotComputationResultV2, ...],
    tuple[ValidationSlotComputationResultV2, ...],
    VerifiedDevelopmentOutcomeReaderV2,
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
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    inputs = SlotRunnerInputsV2(
        programme_id="TVPV2-" + "1" * 64,
        outcome_reader=reader,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        promotion_grade_costs_complete=False,
        precision_available=True,
        source_available=False,
    )
    budget = ValidationWorkBudget()
    demand = ValidationWorkDemand()
    first = run_slot_roster_v2(inputs, budget=budget, demand=demand)
    retry = run_slot_roster_v2(
        inputs,
        budget=budget,
        demand=demand,
        attempt_numbers={slot_id: 2 for slot_id in slot_ids},
    )
    return first, retry, reader


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


def test_publisher_rejects_object_new_exact_type_forgery_before_field_or_filesystem_access(
    tmp_path: Path,
) -> None:
    forged = object.__new__(ValidationSlotComputationResultV2)
    before = _tree_snapshot(tmp_path)

    with pytest.raises((TypeError, ValueError), match="registered original|factory-issued"):
        publish_validation_v2_receipt(tmp_path, forged)

    assert _tree_snapshot(tmp_path) == before


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


def test_result_identity_binds_every_serialized_scientific_and_runner_field(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result
    payload = original.to_dict()

    assert original.result_sha256 == validation_slot_result_sha256_v2(payload)
    incomplete_legacy_identity = hash_json(
        "phase5-validation-slot-computation-result-v2",
        {
            "programme_id": original.programme_id,
            "slot_id": original.slot_id,
            "attempt_sha256": original.attempt_sha256,
            "input_sha256": original.input_sha256,
            "evidence_sha256": original.evidence_sha256,
            "execution_status": original.execution_status,
            "decision": original.decision,
            "computation_completed": original.computation_completed,
            "p_value": original.p_value,
            "metrics": dict(original.metrics),
        },
    )
    assert original.result_sha256 != incomplete_legacy_identity


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("runner_version", "attacker-runner-v2"),
        ("reason", "attacker rewrote the terminal reason"),
        ("attempt_number", 2),
        ("decision", "promoted"),
    ],
)
def test_exact_original_verifier_rejects_mutated_result_fields_before_evidence_execution(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
    field_name: str,
    replacement: object,
) -> None:
    original, _ = issued_result
    prior = getattr(original, field_name)
    object.__setattr__(original, field_name, replacement)
    try:
        with pytest.raises((TypeError, ValueError), match="immutable|identity|field|original"):
            verify_original_validation_slot_result_v2(original)
    finally:
        object.__setattr__(original, field_name, prior)


def test_exact_original_verifier_rejects_deep_copy_and_nested_metric_shape(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result
    try:
        copied = deepcopy(original)
    except (TypeError, ValueError):
        copied = None
    if copied is not None:
        with pytest.raises((TypeError, ValueError), match="registered original|factory-issued"):
            verify_original_validation_slot_result_v2(copied)

    prior_metrics = original.metrics
    object.__setattr__(original, "metrics", {"nested": {"fabricated": 1}})
    try:
        with pytest.raises(TypeError, match="sealed exact mapping|invalid exact shape"):
            verify_original_validation_slot_result_v2(original)
    finally:
        object.__setattr__(original, "metrics", prior_metrics)


@pytest.mark.parametrize(
    ("parent_name", "field_name", "replacement"),
    [
        ("budget", "max_source_rows", 1),
        ("demand", "source_rows", 1),
    ],
)
def test_receipt_publication_revalidates_exact_admission_parents_before_filesystem(
    tmp_path: Path,
    parent_name: str,
    field_name: str,
    replacement: int,
) -> None:
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
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    inputs = SlotRunnerInputsV2(
        programme_id="TVPV2-" + "1" * 64,
        outcome_reader=reader,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        promotion_grade_costs_complete=False,
        precision_available=True,
        source_available=False,
    )
    budget = ValidationWorkBudget()
    demand = ValidationWorkDemand()
    original = run_slot_roster_v2(inputs, budget=budget, demand=demand)[0]
    parent = budget if parent_name == "budget" else demand
    prior = getattr(parent, field_name)
    before = _tree_snapshot(tmp_path)
    object.__setattr__(parent, field_name, replacement)
    try:
        with pytest.raises(ValueError, match="budget|demand|admission|computation evidence"):
            publish_validation_v2_receipt(tmp_path, original)
    finally:
        object.__setattr__(parent, field_name, prior)

    assert _tree_snapshot(tmp_path) == before


def test_fixture_reader_verifier_rejects_scalar_and_nested_outcome_mutation() -> None:
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    rows = {
        slot.slot_id: [
            {
                "event_id": f"event-{slot.slot_id}",
                "timestamp": "2025-01-06T00:00:00+00:00",
                "symbol": "SOLUSDT",
                "timeframe": slot.timeframe,
                "entry_price": 100.0,
                "exit_price": 101.0,
                "volume": 10.0,
                "detector_metric": 1.0,
                "partition_role": "inner_train",
            }
        ]
        for slot in VALIDATION_SLOT_ROSTER
    }
    reader = _issue_fixture_outcome_reader_v2(
        json.dumps(
            {"schema_version": "validation-v2-outcome-fixture-v1", "slots": rows},
            sort_keys=True,
        ).encode(),
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )

    original_programme = reader.programme_id
    object.__setattr__(reader, "programme_id", "TVPV2-" + "2" * 64)
    try:
        with pytest.raises(ValueError, match="registered original|changed|differs"):
            reader.verify_original()
    finally:
        object.__setattr__(reader, "programme_id", original_programme)

    outcome = reader._outcomes_by_slot["VS-0001"]  # noqa: SLF001
    original_return = outcome[0].net_return
    object.__setattr__(outcome[0], "net_return", 0.75)
    try:
        with pytest.raises(ValueError, match="registered original|changed|differs"):
            reader.verify_original()
    finally:
        object.__setattr__(outcome[0], "net_return", original_return)

    original_timestamp = outcome[0].timestamp
    object.__setattr__(outcome[0], "timestamp", "not-a-datetime")
    try:
        with pytest.raises(TypeError, match="timestamp.*exact datetime"):
            reader.verify_original()
    finally:
        object.__setattr__(outcome[0], "timestamp", original_timestamp)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("programme_id", "not-a-programme"),
        ("slot_id", "VS-9999"),
        ("runner_kind", "capacity"),
        ("parent_result_sha256", "a" * 64),
    ],
)
def test_result_factory_rejects_noncanonical_individual_contract(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
    field_name: str,
    replacement: object,
) -> None:
    original, _ = issued_result
    fields_by_name = {
        item.name: getattr(original, item.name)
        for item in fields(ValidationSlotComputationResultV2)
    }
    fields_by_name[field_name] = replacement

    with pytest.raises(TypeError, match="concrete computation factory"):
        _issue_validation_slot_result_v2(
            evidence_verifier=lambda: None,
            retained_evidence=None,
            **fields_by_name,
        )


@pytest.mark.parametrize(
    "metrics",
    [
        {f"metric-{index}": index for index in range(65)},
        {"oversized": "x" * 4_097},
        {"huge_integer": 2**63},
    ],
)
def test_result_factory_rejects_unbounded_metrics_before_hashing(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
    metrics: object,
) -> None:
    original, _ = issued_result
    fields_by_name = {
        item.name: getattr(original, item.name)
        for item in fields(ValidationSlotComputationResultV2)
    }
    fields_by_name["metrics"] = metrics

    with pytest.raises(TypeError, match="concrete computation factory"):
        _issue_validation_slot_result_v2(
            evidence_verifier=lambda: None,
            retained_evidence=None,
            **fields_by_name,
        )


def test_result_factory_rejects_hostile_mapping_before_iteration(
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    class ExplodingMapping:
        def __iter__(self):  # type: ignore[no-untyped-def]
            pytest.fail("hostile metrics mapping was iterated")

    original, _ = issued_result
    fields_by_name = {
        item.name: getattr(original, item.name)
        for item in fields(ValidationSlotComputationResultV2)
    }
    fields_by_name["metrics"] = ExplodingMapping()

    with pytest.raises(TypeError, match="concrete computation factory"):
        _issue_validation_slot_result_v2(
            evidence_verifier=lambda: None,
            retained_evidence=None,
            **fields_by_name,
        )


def test_child_verifier_replays_exact_parent_evidence_chain() -> None:
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    rows = {
        slot.slot_id: [
            {
                "event_id": f"event-{slot.slot_id}",
                "timestamp": "2025-01-06T00:00:00+00:00",
                "symbol": "SOLUSDT",
                "timeframe": slot.timeframe,
                "entry_price": 100.0,
                "exit_price": 101.0,
                "volume": 10.0,
                "detector_metric": 1.0,
                "partition_role": "inner_train",
            }
        ]
        for slot in VALIDATION_SLOT_ROSTER
    }
    reader = _issue_fixture_outcome_reader_v2(
        json.dumps(
            {"schema_version": "validation-v2-outcome-fixture-v1", "slots": rows},
            sort_keys=True,
        ).encode(),
        programme_id="TVPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    results = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=True,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )
    child_slot = next(slot for slot in VALIDATION_SLOT_ROSTER if slot.parent_slot_id == "VS-0001")
    child = next(result for result in results if result.slot_id == child_slot.slot_id)
    parent_outcome = reader._outcomes_by_slot["VS-0001"][0]  # noqa: SLF001
    original_return = parent_outcome.net_return
    object.__setattr__(parent_outcome, "net_return", 0.75)
    try:
        with pytest.raises(ValueError, match="slot rows differ|parent|registered original"):
            verify_original_validation_slot_result_v2(child)
    finally:
        object.__setattr__(parent_outcome, "net_return", original_return)


def test_receipt_is_factory_issued_and_replacement_is_rejected(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    original, _ = issued_result
    receipt = publish_validation_v2_receipt(tmp_path, original)

    with pytest.raises(TypeError, match="factory|issuer"):
        replace(receipt)
    forged = object.__new__(type(receipt))
    with pytest.raises((TypeError, ValueError), match="registered original|factory-issued"):
        verify_validation_v2_receipt(forged)


def test_retry_receipt_requires_exact_registered_predecessor_and_complete_chain(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, retry_results, _reader = issued_rosters
    first = publish_validation_v2_receipt(tmp_path, first_results[0])

    with pytest.raises(ValueError, match="predecessor"):
        publish_validation_v2_receipt(tmp_path, retry_results[0])
    with pytest.raises(ValueError, match="predecessor|slot"):
        publish_validation_v2_receipt(
            tmp_path,
            retry_results[0],
            predecessor_receipt=publish_validation_v2_receipt(tmp_path, first_results[1]),
        )

    second = publish_validation_v2_receipt(
        tmp_path,
        retry_results[0],
        predecessor_receipt=first,
    )
    assert second.predecessor_receipt_sha256 == first.receipt_sha256
    with pytest.raises(ValueError, match="predecessor|chain"):
        select_terminal_attempts_v2((second,))
    assert select_terminal_attempts_v2((first, second))["VS-0001"] is second


def test_batch_publication_is_atomic_and_manifest_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _retry_results, _reader = issued_rosters
    calls = 0
    real_writer = module._write_staged_receipt_v2

    def fail_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second receipt failure")
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(module, "_write_staged_receipt_v2", fail_second)
    before = _tree_snapshot(tmp_path)
    with pytest.raises(OSError, match="second receipt"):
        publish_validation_v2_receipts(tmp_path, first_results[:2])
    assert _tree_snapshot(tmp_path) == before

    monkeypatch.setattr(module, "_write_staged_receipt_v2", real_writer)
    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])
    assert len(receipts) == 2
    batch_roots = {receipt.path.parents[1] for receipt in receipts}
    assert len(batch_roots) == 1
    batch_root = next(iter(batch_roots))
    manifest = batch_root / "manifest.json"
    assert manifest.is_file()
    original = manifest.read_bytes()
    manifest.write_bytes(original + b"\n")
    try:
        with pytest.raises(ValueError, match="manifest.*changed|batch.*changed"):
            verify_validation_v2_receipt(receipts[0])
    finally:
        manifest.write_bytes(original)


def test_surviving_receipt_rejects_missing_sibling_from_authenticated_batch(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])
    receipts[1].path.unlink()

    with pytest.raises(ValueError, match="batch.*missing|member.*missing|complete"):
        verify_validation_v2_receipt(receipts[0])


def test_surviving_receipt_rejects_mutated_sibling_from_authenticated_batch(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])
    receipts[1].path.write_bytes(receipts[1].canonical_bytes + b"\n")

    with pytest.raises(ValueError, match="batch.*changed|member.*changed|bytes changed"):
        verify_validation_v2_receipt(receipts[0])


def test_terminal_selection_verifies_each_batch_and_ledger_once_per_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _retry_results, _reader = issued_rosters
    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])
    batch_calls = 0
    ledger_calls = 0
    real_batch = module._verify_complete_batch_v2
    real_ledger = module._load_programme_ledger_v2

    def counted_batch(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal batch_calls
        batch_calls += 1
        return real_batch(*args, **kwargs)

    def counted_ledger(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal ledger_calls
        ledger_calls += 1
        return real_ledger(*args, **kwargs)

    monkeypatch.setattr(module, "_verify_complete_batch_v2", counted_batch)
    monkeypatch.setattr(module, "_load_programme_ledger_v2", counted_ledger)

    selected = select_terminal_attempts_v2(receipts)

    assert tuple(selected) == ("VS-0001", "VS-0002")
    assert ledger_calls == 1
    assert batch_calls == 2  # one direct batch pass plus one ledger-chain pass


def test_canonical_programme_ledger_rejects_retry_fork_from_stale_head(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, retry_a, reader = issued_rosters
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    retry_b = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=False,
            source_available=False,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
        attempt_numbers={slot_id: 2 for slot_id in slot_ids},
    )
    assert retry_a[0].result_sha256 != retry_b[0].result_sha256
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    winner = publish_validation_v2_receipt(
        tmp_path,
        retry_a[0],
        predecessor_receipt=first,
    )
    before = _tree_snapshot(tmp_path)

    with pytest.raises((FileExistsError, ValueError), match="canonical|stale|head|commit"):
        publish_validation_v2_receipt(
            tmp_path,
            retry_b[0],
            predecessor_receipt=first,
        )

    assert _tree_snapshot(tmp_path) == before
    assert verify_validation_v2_receipt(winner) is winner


def test_canonical_programme_ledger_rejects_runner_version_drift_before_filesystem(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, reader = issued_rosters
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    drifted_retry = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=True,
            runner_version="slot-runner-v2-drift",
            source_available=False,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
        attempt_numbers={slot_id: 2 for slot_id in slot_ids},
    )
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="runner.*(drift|differs|immutable)"):
        publish_validation_v2_receipt(
            tmp_path,
            drifted_retry[0],
            predecessor_receipt=first,
        )

    assert _tree_snapshot(tmp_path) == before


def test_concurrent_distinct_retries_from_one_head_have_one_canonical_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, retry_a, reader = issued_rosters
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    retry_b = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id="TVPV2-" + "1" * 64,
            outcome_reader=reader,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            promotion_grade_costs_complete=False,
            precision_available=False,
            source_available=False,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
        attempt_numbers={slot_id: 2 for slot_id in slot_ids},
    )
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    barrier = Barrier(2)
    real_commit = module._commit_staged_receipt_batch_v2

    def simultaneous_commit(stage: Path, destination: Path) -> None:
        barrier.wait(timeout=10)
        real_commit(stage, destination)

    monkeypatch.setattr(module, "_commit_staged_receipt_batch_v2", simultaneous_commit)

    def publish_retry(result: ValidationSlotComputationResultV2) -> object:
        try:
            return publish_validation_v2_receipt(
                tmp_path,
                result,
                predecessor_receipt=first,
            )
        except Exception as error:  # noqa: BLE001 - capture competing CAS outcome
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish_retry, (retry_a[0], retry_b[0])))

    winners = [item for item in outcomes if not isinstance(item, Exception)]
    losers = [item for item in outcomes if isinstance(item, Exception)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], (FileExistsError, ValueError))
    assert verify_validation_v2_receipt(winners[0]) is winners[0]


def test_concurrent_identical_first_attempts_have_one_canonical_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    result, _reader = issued_result
    barrier = Barrier(2)
    real_commit = module._commit_staged_receipt_batch_v2

    def simultaneous_commit(stage: Path, destination: Path) -> None:
        barrier.wait(timeout=10)
        real_commit(stage, destination)

    monkeypatch.setattr(module, "_commit_staged_receipt_batch_v2", simultaneous_commit)

    def publish_once() -> object:
        try:
            return publish_validation_v2_receipt(tmp_path, result)
        except Exception as error:  # noqa: BLE001 - capture competing CAS outcome
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _index: publish_once(), range(2)))

    winners = [item for item in outcomes if not isinstance(item, Exception)]
    losers = [item for item in outcomes if isinstance(item, Exception)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], FileExistsError)
    assert verify_validation_v2_receipt(winners[0]) is winners[0]


def test_first_attempts_must_extend_the_frozen_roster_prefix_across_commits(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="canonical.*(prefix|order)|frozen.*prefix"):
        publish_validation_v2_receipt(tmp_path, first_results[2])

    assert _tree_snapshot(tmp_path) == before
    assert verify_validation_v2_receipt(first) is first


def test_receipt_batch_bounds_exact_sequences_before_copy_or_iteration(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    result, _reader = issued_result
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="cardinality|bound"):
        publish_validation_v2_receipts(tmp_path, [result] * 1_105)

    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("ceiling_name", ["_MAX_LEDGER_COMMITS", "_MAX_LEDGER_RECEIPTS"])
def test_receipt_publication_rejects_aggregate_ledger_ceiling_before_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
    ceiling_name: str,
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    result, _reader = issued_result
    monkeypatch.setattr(module, ceiling_name, 0)
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="ledger.*(commit|receipt).*bound|ceiling"):
        publish_validation_v2_receipt(tmp_path, result)

    assert _tree_snapshot(tmp_path) == before


def test_posix_post_rename_failure_recovers_authenticated_committed_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    result, _reader = issued_result

    def move_then_fail(stage: Path, destination: Path) -> None:
        stage.rename(destination)
        raise OSError("injected post-rename durability failure")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(module, "_commit_staged_receipt_batch_v2", move_then_fail)

    receipt = publish_validation_v2_receipt(tmp_path, result)

    assert verify_validation_v2_receipt(receipt) is receipt
    assert not tuple(tmp_path.glob(".receipt-batch-*.tmp"))


def test_receipt_batch_rejects_noncanonical_frozen_roster_order_before_filesystem(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="canonical.*order|frozen.*order"):
        publish_validation_v2_receipts(tmp_path, (first_results[1], first_results[0]))

    assert _tree_snapshot(tmp_path) == before


def test_historical_receipt_remains_verifiable_but_cannot_mix_with_amended_programme(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    historical_results, _retry_results, historical_reader = issued_rosters
    historical = historical_results[0]
    receipt = publish_validation_v2_receipt(tmp_path, historical)
    original_bytes = receipt.path.read_bytes()
    programme_id = "TVPV2-" + "2" * 64
    amended_reader = _issue_fixture_outcome_reader_v2(
        historical_reader.canonical_bytes,
        programme_id=programme_id,
        split_sha256=historical_reader.split_sha256,
        cost_authority_sha256=historical_reader.cost_authority_sha256,
        source_publication_sha256=historical_reader.source_publication_sha256,
        aggregate_publication_sha256=historical_reader.aggregate_publication_sha256,
        expected_slot_ids=historical_reader.slot_ids,
    )
    amended = run_slot_roster_v2(
        SlotRunnerInputsV2(
            programme_id=programme_id,
            outcome_reader=amended_reader,
            split_sha256=amended_reader.split_sha256,
            cost_authority_sha256=amended_reader.cost_authority_sha256,
            promotion_grade_costs_complete=False,
            precision_available=True,
        ),
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )[1]

    with pytest.raises(ValueError, match="crosses programme"):
        publish_validation_v2_receipts(
            tmp_path / "mixed",
            (historical, amended),
        )

    assert verify_validation_v2_receipt(receipt) is receipt
    assert receipt.path.read_bytes() == original_bytes


def test_programme_verifier_rejects_hostile_sequence_before_iteration() -> None:
    class HostileSequence:
        def __len__(self) -> int:
            return 1

        def __iter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("programme verifier iterated before exact-sequence admission")

    with pytest.raises(TypeError, match="exact tuple or list"):
        verify_validation_programme_v2(
            HostileSequence(),  # type: ignore[arg-type]
            (),
            runner_version="slot-runner-v2",
        )


def test_receipt_verifier_bounds_extra_batch_members(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    receipt = publish_validation_v2_receipt(tmp_path, first_results[0])
    (receipt.path.parents[1] / "extra-1").mkdir()

    with pytest.raises(ValueError, match="bound"):
        verify_validation_v2_receipt(receipt)


def test_receipt_report_rejects_reordered_expected_frozen_slots(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    first_results, _retry_results, _reader = issued_rosters
    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])

    with pytest.raises(ValueError, match="canonical.*order|frozen.*order"):
        verify_validation_v2_receipts(
            receipts,
            expected_slot_ids=("VS-0002", "VS-0001"),
            runner_version=first_results[0].runner_version,
        )


def test_batch_rejects_uninitialized_result_before_field_or_filesystem_access(
    tmp_path: Path,
) -> None:
    forged = object.__new__(ValidationSlotComputationResultV2)
    before = _tree_snapshot(tmp_path)

    with pytest.raises((TypeError, ValueError), match="registered original|factory-issued"):
        publish_validation_v2_receipts(tmp_path, (forged,))

    assert _tree_snapshot(tmp_path) == before


def test_registered_result_without_publication_config_authority_cannot_publish_as_production(
    tmp_path: Path,
    issued_result: tuple[ValidationSlotComputationResultV2, VerifiedDevelopmentOutcomeReaderV2],
) -> None:
    template, _reader = issued_result
    fields = template.to_dict()
    for key in (
        "parent_slot_id",
        "result_sha256",
        "slot_computation_result_sha256",
    ):
        fields.pop(key)
    fields["programme_id"] = "VPV2-" + "2" * 64
    evidence = object()
    before = _tree_snapshot(tmp_path)

    with pytest.raises(TypeError, match="concrete computation factory"):
        _issue_validation_slot_result_v2(
            evidence_verifier=lambda: evidence,
            retained_evidence=evidence,
            **fields,  # pyright: ignore[reportArgumentType]
        )

    assert _tree_snapshot(tmp_path) == before


def test_result_rejects_equal_but_distinct_outcome_reader_substitution_before_filesystem(
    tmp_path: Path,
) -> None:
    slot_ids = tuple(slot.slot_id for slot in VALIDATION_SLOT_ROSTER)
    fixture_bytes = json.dumps(
        {
            "schema_version": "validation-v2-outcome-fixture-v1",
            "slots": {slot_id: [] for slot_id in slot_ids},
        },
        sort_keys=True,
    ).encode()

    def issue_reader() -> VerifiedDevelopmentOutcomeReaderV2:
        return _issue_fixture_outcome_reader_v2(
            fixture_bytes,
            programme_id="TVPV2-" + "1" * 64,
            split_sha256="c" * 64,
            cost_authority_sha256="d" * 64,
            source_publication_sha256="e" * 64,
            aggregate_publication_sha256="f" * 64,
            expected_slot_ids=slot_ids,
        )

    original_reader = issue_reader()
    lookalike_reader = issue_reader()
    assert original_reader == lookalike_reader and original_reader is not lookalike_reader
    inputs = SlotRunnerInputsV2(
        programme_id="TVPV2-" + "1" * 64,
        outcome_reader=original_reader,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        promotion_grade_costs_complete=False,
        precision_available=True,
        source_available=False,
    )
    result = run_slot_roster_v2(
        inputs,
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(),
    )[0]
    object.__setattr__(inputs, "outcome_reader", lookalike_reader)
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="input parent changed"):
        publish_validation_v2_receipt(tmp_path, result)

    assert _tree_snapshot(tmp_path) == before


def test_mutated_child_rejects_before_corrupt_parent_evidence_is_read(
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
        VerifiedDevelopmentOutcomeReaderV2,
    ],
) -> None:
    results, _retry_results, reader = issued_rosters
    child_index = next(
        index
        for index, slot in enumerate(VALIDATION_SLOT_ROSTER)
        if slot.parent_slot_id is not None
    )
    child = results[child_index]
    prior_reason = child.reason
    prior_bytes = reader.canonical_bytes
    object.__setattr__(child, "reason", "mutated child")
    object.__setattr__(reader, "canonical_bytes", b'{"corrupt-parent":true}')
    try:
        with pytest.raises(ValueError, match="immutable computation"):
            verify_original_validation_slot_result_v2(child)
    finally:
        object.__setattr__(child, "reason", prior_reason)
        object.__setattr__(reader, "canonical_bytes", prior_bytes)
