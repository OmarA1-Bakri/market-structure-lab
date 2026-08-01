from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkDemand,
)
from market_structure_lab.research.validation_v2 import (
    SlotRunnerInputsV2,
    ValidationSlotComputationResultV2,
    _issue_fixture_outcome_reader_v2,
    run_slot_roster_v2,
)
from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipt,
    publish_validation_v2_receipts,
    select_terminal_attempts_v2,
    verify_validation_v2_receipt,
    verify_validation_v2_receipts,
    verify_validation_programme_v2,
)


class _PathReceiptClaim:
    def __init__(self, path: Path, move_hook=None) -> None:  # type: ignore[no-untyped-def]
        self.path = path
        self.identity = path.stat().st_ino
        self.move_hook = move_hook

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def move_to(self, destination: Path) -> None:
        if self.move_hook is not None:
            self.move_hook(self, destination)
        owned = self._locate_owned()
        if owned is None:
            raise RuntimeError("owned receipt stage was replaced")
        owned.rename(destination)
        self.path = destination

    def delete_exact(self) -> None:
        owned = self._locate_owned()
        if owned is not None:
            shutil.rmtree(owned)

    def _locate_owned(self) -> Path | None:
        for candidate in self.path.parent.iterdir():
            if candidate.is_dir() and candidate.stat().st_ino == self.identity:
                return candidate
        return None


@pytest.fixture(scope="module")
def issued_rosters() -> tuple[
    tuple[ValidationSlotComputationResultV2, ...],
    tuple[ValidationSlotComputationResultV2, ...],
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
        programme_id="VPV2-" + "1" * 64,
        split_sha256="c" * 64,
        cost_authority_sha256="d" * 64,
        source_publication_sha256="e" * 64,
        aggregate_publication_sha256="f" * 64,
        expected_slot_ids=slot_ids,
    )
    inputs = SlotRunnerInputsV2(
        programme_id="VPV2-" + "1" * 64,
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
    return first, retry


def test_receipts_are_immutable_and_retry_is_additive(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    first_results, retry_results = issued_rosters
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    second = publish_validation_v2_receipt(
        tmp_path,
        retry_results[0],
        predecessor_receipt=first,
    )
    assert first.path != second.path
    assert first.path.parents[1] != second.path.parents[1]
    with pytest.raises(FileExistsError):
        publish_validation_v2_receipt(tmp_path, first_results[0])
    selected = select_terminal_attempts_v2((first, second))
    assert selected["VS-0001"] is second


def test_verifier_reports_receipts_separately_from_computations(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    first_results, _ = issued_rosters
    receipt = publish_validation_v2_receipt(tmp_path, first_results[0])
    report = verify_validation_v2_receipts(
        (receipt,),
        expected_slot_ids=("VS-0001",),
        runner_version=first_results[0].runner_version,
    )
    assert report.planned_slots == 1
    assert report.attempted_slots == 1
    assert report.completed_slot_computations == 0
    assert report.not_evaluated_slots == 1
    assert report.failed_slots == 1
    assert report.receipt_count == 1
    assert report.attempted_attempts == 1
    assert report.failed_attempts == 1


def test_complete_frozen_roster_is_independently_receipted_and_accounted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _retry_results = issued_rosters

    receipts = publish_validation_v2_receipts(tmp_path, first_results)
    mutable_receipts = list(receipts)
    real_verify = module.verify_validation_v2_receipts

    def verify_then_race(receipt_snapshot, **kwargs):  # type: ignore[no-untyped-def]
        assert type(receipt_snapshot) is tuple
        report = real_verify(receipt_snapshot, **kwargs)
        mutable_receipts.clear()
        return report

    monkeypatch.setattr(module, "verify_validation_v2_receipts", verify_then_race)
    report = verify_validation_programme_v2(
        first_results,
        mutable_receipts,
        runner_version=first_results[0].runner_version,
    )

    assert report.planned_slots == 1_104
    assert report.attempted_slots == 1_104
    assert report.completed_slot_computations == 0
    assert report.not_evaluated_slots == 1_104
    assert report.failed_slots == 1_104
    assert report.receipt_count == 1_104
    assert report.attempted_attempts == 1_104
    assert report.failed_attempts == 1_104


def test_receipt_rejects_retry_gaps_and_wrong_predecessor(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    first_results, retry_results = issued_rosters
    first_receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])
    wrong = first_receipts[1]
    with pytest.raises(ValueError, match="predecessor"):
        publish_validation_v2_receipt(tmp_path, retry_results[0])
    with pytest.raises(ValueError, match="predecessor"):
        publish_validation_v2_receipt(
            tmp_path,
            retry_results[0],
            predecessor_receipt=wrong,
        )


def test_report_retains_all_failed_retry_attempts(
    tmp_path: Path,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    first_results, retry_results = issued_rosters
    first = publish_validation_v2_receipt(tmp_path, first_results[0])
    second = publish_validation_v2_receipt(
        tmp_path,
        retry_results[0],
        predecessor_receipt=first,
    )
    report = verify_validation_v2_receipts(
        (first, second),
        expected_slot_ids=("VS-0001",),
        runner_version=first_results[0].runner_version,
    )

    assert report.attempted_slots == 1
    assert report.failed_slots == 1
    assert report.attempted_attempts == 2
    assert report.failed_attempts == 2
    assert report.receipt_count == 2


def test_receipt_batch_failure_leaves_no_receipt_or_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters
    monkeypatch.setattr(
        module,
        "_commit_staged_receipt_batch_v2",
        lambda *_args: (_ for _ in ()).throw(OSError("durable publication failed")),
    )

    with pytest.raises(OSError, match="durable publication failed"):
        publish_validation_v2_receipts(tmp_path, first_results[:2])

    assert tuple(tmp_path.iterdir()) == ()


def test_posix_receipt_commits_complete_stage_before_one_durable_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters
    calls: list[tuple[str, Path]] = []

    def move(source: Path, destination: Path) -> None:
        calls.append(("move", destination))
        assert (source / "manifest.json").is_file()
        assert len(tuple(source.glob("VS-*/attempt-*.json"))) == 2
        source.rename(destination)

    monkeypatch.setattr(module, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(
        module,
        "fsync_directory_posix",
        lambda path: calls.append(("fsync", path)),
    )
    monkeypatch.setattr(module, "durable_move_no_replace", move)

    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])

    assert len(receipts) == 2
    assert [kind for kind, _ in calls].count("move") == 1
    assert calls[-1][0] == "move"


def test_windows_receipt_uses_one_owned_tree_move_without_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters
    moves: list[tuple[Path, Path, tuple[str, ...]]] = []

    def move(claim: _PathReceiptClaim, destination: Path) -> None:
        moves.append(
            (
                claim.path,
                destination,
                tuple(sorted(path.name for path in claim.path.iterdir())),
            )
        )

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "fsync_directory_posix",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("Windows receipt publication must not fsync a directory descriptor")
        ),
    )
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathReceiptClaim(path, move),
    )

    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])

    assert len(moves) == 1
    source, destination, members = moves[0]
    assert destination == receipts[0].path.parents[1]
    assert source.parent == tmp_path
    assert members == ("VS-0001", "VS-0002", "manifest.json")
    assert not source.exists()


def test_windows_batch_move_failure_leaves_no_owned_stage_or_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters

    def fail_move(_claim: _PathReceiptClaim, _destination: Path) -> None:
        raise OSError("Windows write-through move failed")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathReceiptClaim(path, fail_move),
    )

    with pytest.raises(OSError, match="write-through"):
        publish_validation_v2_receipts(tmp_path, first_results[:2])

    assert tuple(tmp_path.iterdir()) == ()


def test_windows_stage_swap_preserves_foreign_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters
    foreign_stage: Path | None = None

    def swap(claim: _PathReceiptClaim, _destination: Path) -> None:
        nonlocal foreign_stage
        owned_away = claim.path.with_name(claim.path.name + ".owned")
        claim.path.rename(owned_away)
        claim.path.mkdir()
        (claim.path / "foreign-sentinel").write_text("preserve", encoding="utf-8")
        foreign_stage = claim.path

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathReceiptClaim(path, swap),
    )

    receipts = publish_validation_v2_receipts(tmp_path, first_results[:2])

    assert all(verify_validation_v2_receipt(receipt) is receipt for receipt in receipts)
    assert foreign_stage is not None
    assert (foreign_stage / "foreign-sentinel").read_text(encoding="utf-8") == "preserve"


def test_concurrent_identical_batch_commit_has_one_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issued_rosters: tuple[
        tuple[ValidationSlotComputationResultV2, ...],
        tuple[ValidationSlotComputationResultV2, ...],
    ],
) -> None:
    from market_structure_lab.research import validation_v2_receipts as module

    first_results, _ = issued_rosters
    real_move = module.durable_move_no_replace
    destinations: set[Path] = set()

    def one_winner(source: Path, destination: Path) -> None:
        if destination in destinations:
            raise FileExistsError(destination)
        destinations.add(destination)
        real_move(source, destination)

    monkeypatch.setattr(module, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(module, "durable_move_no_replace", one_winner)
    first = publish_validation_v2_receipts(tmp_path, first_results[:2])
    with pytest.raises(FileExistsError):
        publish_validation_v2_receipts(tmp_path, first_results[:2])
    assert all(verify_validation_v2_receipt(receipt) is receipt for receipt in first)
