from __future__ import annotations

from dataclasses import replace

import pytest

from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationSlotKind,
    ValidationTerminalState,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
    evaluation_id_for_slot,
    freeze_validation_slot_roster,
    validation_roster_sha256,
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
        "families": EXPECTED_FAMILIES,
        "roster": VALIDATION_SLOT_ROSTER,
        "work_budget": ValidationWorkBudget(),
    }
    values.update(changes)
    return ValidationProgrammeConfig(**values)  # type: ignore[arg-type]


def test_statuses_use_exact_values_and_validate_orthogonal_terminal_state() -> None:
    assert tuple(status.value for status in ExecutionStatus) == (
        "completed",
        "failed",
        "abandoned",
    )
    assert tuple(decision.value for decision in ScientificDecision) == (
        "not_evaluated",
        "rejected",
        "inconclusive",
        "supported_development",
        "validated",
        "promoted",
    )
    for decision in tuple(ScientificDecision)[1:]:
        assert ValidationTerminalState(ExecutionStatus.COMPLETED, decision).decision is decision
    for status in (ExecutionStatus.FAILED, ExecutionStatus.ABANDONED):
        assert (
            ValidationTerminalState(status, ScientificDecision.NOT_EVALUATED).execution_status
            is status
        )

    with pytest.raises(ValueError, match="completed.*not_evaluated"):
        ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.NOT_EVALUATED)
    with pytest.raises(ValueError, match="failed.*not_evaluated"):
        ValidationTerminalState(ExecutionStatus.FAILED, ScientificDecision.PROMOTED)


def test_default_work_budget_freezes_exact_counts_and_draws() -> None:
    budget = ValidationWorkBudget()

    assert budget.evaluation_count == 1_104
    assert budget.core_count == 152
    assert budget.primary_count == 64
    assert budget.baseline_count == 192
    assert budget.negative_control_count == 192
    assert budget.robustness_count == 256
    assert budget.perturbation_count == 184
    assert budget.exposure_count == 64
    assert budget.capacity_count == 64
    assert budget.bootstrap_draws == 4_096
    assert (
        budget.core_count
        + budget.baseline_count
        + budget.negative_control_count
        + budget.robustness_count
        + budget.perturbation_count
        + budget.exposure_count
        + budget.capacity_count
        == budget.evaluation_count
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("evaluation_count", 1_103),
        ("core_count", 151),
        ("primary_count", 63),
        ("baseline_count", 191),
        ("negative_control_count", 191),
        ("robustness_count", 255),
        ("perturbation_count", 183),
        ("exposure_count", 63),
        ("capacity_count", 63),
        ("bootstrap_draws", 4_095),
        ("max_source_rows", -1),
        ("max_source_bytes", 1.5),
    ),
)
def test_work_budget_rejects_changed_exact_counts_or_invalid_limits(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        replace(ValidationWorkBudget(), **{field: value})


def test_slot_roster_has_exact_counts_family_order_roles_and_unique_identity() -> None:
    roster = VALIDATION_SLOT_ROSTER

    assert len(roster) == 1_104
    assert tuple(dict.fromkeys(slot.family for slot in roster)) == EXPECTED_FAMILIES
    assert len({slot.slot_id for slot in roster}) == 1_104
    assert sum(slot.kind is ValidationSlotKind.CORE for slot in roster) == 152
    assert sum(slot.primary for slot in roster) == 64
    assert sum(slot.kind is ValidationSlotKind.BASELINE for slot in roster) == 192
    assert sum(slot.kind is ValidationSlotKind.NEGATIVE_CONTROL for slot in roster) == 192
    assert sum(slot.kind is ValidationSlotKind.ROBUSTNESS for slot in roster) == 256
    assert sum(slot.kind is ValidationSlotKind.PERTURBATION for slot in roster) == 184
    assert sum(slot.kind is ValidationSlotKind.EXPOSURE for slot in roster) == 64
    assert sum(slot.kind is ValidationSlotKind.CAPACITY for slot in roster) == 64
    assert len(validation_roster_sha256(roster)) == 64


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate",
        "missing",
        "substituted",
        "misclassified",
        "reordered",
        "wrong_family",
        "wrong_role",
        "wrong_timeframe",
        "wrong_horizon",
        "wrong_parameter",
    ),
)
def test_roster_rejects_every_shape_mutation_even_when_total_is_1104(mutation: str) -> None:
    changed = list(VALIDATION_SLOT_ROSTER)
    if mutation == "duplicate":
        changed[-1] = changed[0]
    elif mutation == "missing":
        changed[-1] = replace(changed[-1], slot_id=changed[0].slot_id)
    elif mutation == "substituted":
        changed[20] = replace(changed[20], slot_id="VS-SUBSTITUTED")
    elif mutation == "misclassified":
        changed[160] = replace(changed[160], kind=ValidationSlotKind.CORE)
    elif mutation == "reordered":
        changed[0], changed[1] = changed[1], changed[0]
    elif mutation == "wrong_family":
        changed[0] = replace(changed[0], family="D")
    elif mutation == "wrong_role":
        changed[0] = replace(changed[0], role="invented")
    elif mutation == "wrong_timeframe":
        changed[0] = replace(changed[0], timeframe="15m")
    elif mutation == "wrong_horizon":
        changed[0] = replace(changed[0], horizon_hours=72)
    elif mutation == "wrong_parameter":
        changed[0] = replace(changed[0], parameters=(("fast_hours", "23"),))

    assert len(changed) == 1_104
    with pytest.raises(ValueError, match="exact frozen 1,104-slot roster"):
        freeze_validation_slot_roster(changed)


def test_programme_config_binds_lineage_code_data_policies_roster_and_budget() -> None:
    config = _config()

    assert config.programme_id.startswith("VP-")
    assert len(config.sha256) == 64
    assert config.to_dict()["implementation_plan_document_sha256"] == IMPLEMENTATION_DOCUMENT
    assert config.to_dict()["roster_sha256"] == validation_roster_sha256(VALIDATION_SLOT_ROSTER)
    assert config.to_dict()["work_budget_sha256"] == config.work_budget.sha256
    assert evaluation_id_for_slot(config, config.roster[0]).startswith("VR-")

    for field, value in (
        ("code_commit", "7" * 40),
        ("lockfile_sha256", "8" * 64),
        ("dataset_sha256", "9" * 64),
        ("cost_policy_sha256", "a" * 64),
        ("control_policy_sha256", "b" * 64),
        ("split_sha256", "c" * 64),
    ):
        assert _config(**{field: value}).programme_id != config.programme_id


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("task14_closeout_commit", "0" * 40),
        ("task14_evidence_commit", "0" * 40),
        ("task15_plan_commit", "0" * 40),
        ("task15_evidence_commit", "0" * 40),
        ("implementation_plan_checkpoint", "0" * 40),
        ("implementation_plan_evidence_commit", "0" * 40),
        ("implementation_plan_document_sha256", "0" * 64),
        ("families", ("A", "B", "G", "D", "E")),
    ),
)
def test_programme_config_rejects_changed_authoritative_lineage_or_family_order(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        _config(**{field: value})


class _ExplodingIterable:
    iterated = False

    def __iter__(self):
        self.iterated = True
        raise AssertionError("preflight iterated work")


class _AllocationSpy:
    called = False

    def __call__(self) -> None:
        self.called = True
        raise AssertionError("preflight allocated work")


_LIMIT_CASES = (
    ("source_rows", "max_source_rows"),
    ("source_bytes", "max_source_bytes"),
    ("aggregate_bars", "max_aggregate_bars"),
    ("symbols", "max_symbols"),
    ("ranges", "max_ranges"),
    ("candidates", "max_candidates"),
    ("trials", "max_trials"),
    ("events", "max_events"),
    ("outcomes", "max_outcomes"),
    ("path_cells", "max_path_cells"),
    ("outer_folds", "max_outer_folds"),
    ("inner_folds", "max_inner_folds"),
    ("bootstrap_cells", "max_bootstrap_cells"),
    ("bootstrap_blocks", "max_bootstrap_blocks"),
    ("controls", "max_controls"),
    ("placebos", "max_placebos"),
    ("perturbations", "max_perturbations"),
    ("artifacts", "max_artifacts"),
    ("artifact_bytes", "max_artifact_bytes"),
    ("dashboard_bytes", "max_dashboard_bytes"),
    ("final_holdout_candidates", "max_final_holdout_candidates"),
    ("final_batch_candidates", "max_final_batch_candidates"),
)


@pytest.mark.parametrize(("demand_field", "limit_field"), _LIMIT_CASES)
def test_each_independent_work_limit_rejects_before_iteration_or_allocation(
    demand_field: str, limit_field: str
) -> None:
    budget = ValidationWorkBudget()
    demand = replace(
        ValidationWorkDemand(),
        **{demand_field: getattr(budget, limit_field) + 1},
    )
    exploding = _ExplodingIterable()
    allocation = _AllocationSpy()

    with pytest.raises(ValidationWorkBudgetViolation, match=demand_field):
        budget.preflight(demand, deferred_work=exploding, allocation=allocation)

    assert exploding.iterated is False
    assert allocation.called is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("evaluations", 1_103),
        ("evaluations", 1_105),
        ("bootstrap_draws", 4_095),
        ("bootstrap_draws", 4_097),
    ),
)
def test_exact_evaluation_and_bootstrap_counts_reject_before_work(field: str, value: int) -> None:
    budget = ValidationWorkBudget()
    exploding = _ExplodingIterable()
    allocation = _AllocationSpy()

    with pytest.raises(ValidationWorkBudgetViolation, match=field):
        budget.preflight(
            replace(ValidationWorkDemand(), **{field: value}),
            deferred_work=exploding,
            allocation=allocation,
        )

    assert exploding.iterated is False
    assert allocation.called is False


def test_valid_work_preflight_returns_without_consuming_or_allocating() -> None:
    exploding = _ExplodingIterable()
    allocation = _AllocationSpy()

    ValidationWorkBudget().preflight(
        ValidationWorkDemand(), deferred_work=exploding, allocation=allocation
    )

    assert exploding.iterated is False
    assert allocation.called is False
