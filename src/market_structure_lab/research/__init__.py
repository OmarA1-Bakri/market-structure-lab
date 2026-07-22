"""Narrow public Phase 5 validation contracts."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market_structure_lab.research.candidates import CandidateSignal

from market_structure_lab.research.models import (
    CandidateDefinition,
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationSlot,
    ValidationSlotKind,
    ValidationTerminalState,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
    evaluation_id_for_slot,
    freeze_validation_slot_roster,
    validation_roster_sha256,
)


def __getattr__(name: str) -> object:
    """Load the signal contract lazily without coupling data imports to detectors."""

    if name == "CandidateSignal":
        from market_structure_lab.research.candidates import CandidateSignal

        return CandidateSignal
    raise AttributeError(name)


__all__ = [
    "CandidateDefinition",
    "CandidateSignal",
    "EXPECTED_FAMILIES",
    "VALIDATION_SLOT_ROSTER",
    "ExecutionStatus",
    "ScientificDecision",
    "ValidationProgrammeConfig",
    "ValidationSlot",
    "ValidationSlotKind",
    "ValidationTerminalState",
    "ValidationWorkBudget",
    "ValidationWorkBudgetViolation",
    "ValidationWorkDemand",
    "evaluation_id_for_slot",
    "freeze_validation_slot_roster",
    "validation_roster_sha256",
]
