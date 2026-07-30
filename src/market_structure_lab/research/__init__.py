"""Narrow public Phase 5 validation contracts."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market_structure_lab.research.candidates import CandidateSignal
    from market_structure_lab.research.outcomes import AttachedOutcome, attach_outcome
    from market_structure_lab.research.splits import (
        CommonGridSplit,
        FinalHoldoutBatch,
        VerifiedFinalAccess,
        freeze_common_grid_split,
        freeze_final_batch,
    )

    from market_structure_lab.research.validation import (
        CandidatePrimitiveCase,
        CostApplicationCase,
        DevelopmentOutcomeCase,
        ProgrammePreflightMetadata,
        SlotLifecycleEvidence,
        ValidationProgrammeResult,
        ValidationProgrammeSources,
        VerifiedDevelopmentEvidence,
        freeze_development_evidence,
        run_validation_programme,
    )
from market_structure_lab.research.models import (
    CandidateDefinition,
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    OutcomeComponent,
    OutcomePolicy,
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
    """Load causal evidence contracts lazily without introducing data import cycles."""

    if name == "CandidateSignal":
        from market_structure_lab.research.candidates import CandidateSignal

        return CandidateSignal
    if name in {"AttachedOutcome", "attach_outcome"}:
        from market_structure_lab.research.outcomes import AttachedOutcome, attach_outcome

        return {
            "AttachedOutcome": AttachedOutcome,
            "attach_outcome": attach_outcome,
        }[name]
    if name in {
        "CommonGridSplit",
        "FinalHoldoutBatch",
        "VerifiedFinalAccess",
        "freeze_common_grid_split",
        "freeze_final_batch",
    }:
        from market_structure_lab.research.splits import (
            CommonGridSplit,
            FinalHoldoutBatch,
            VerifiedFinalAccess,
            freeze_common_grid_split,
            freeze_final_batch,
        )

        return {
            "CommonGridSplit": CommonGridSplit,
            "FinalHoldoutBatch": FinalHoldoutBatch,
            "VerifiedFinalAccess": VerifiedFinalAccess,
            "freeze_common_grid_split": freeze_common_grid_split,
            "freeze_final_batch": freeze_final_batch,
        }[name]

    if name in {
        "CostApplicationCase",
        "CandidatePrimitiveCase",
        "DevelopmentOutcomeCase",
        "ProgrammePreflightMetadata",
        "SlotLifecycleEvidence",
        "ValidationProgrammeResult",
        "ValidationProgrammeSources",
        "VerifiedDevelopmentEvidence",
        "freeze_development_evidence",
        "run_validation_programme",
    }:
        from market_structure_lab.research.validation import (
            CandidatePrimitiveCase,
            ProgrammePreflightMetadata,
            CostApplicationCase,
            DevelopmentOutcomeCase,
            SlotLifecycleEvidence,
            ValidationProgrammeResult,
            ValidationProgrammeSources,
            VerifiedDevelopmentEvidence,
            freeze_development_evidence,
            run_validation_programme,
        )

        return {
            "CandidatePrimitiveCase": CandidatePrimitiveCase,
            "CostApplicationCase": CostApplicationCase,
            "DevelopmentOutcomeCase": DevelopmentOutcomeCase,
            "ProgrammePreflightMetadata": ProgrammePreflightMetadata,
            "SlotLifecycleEvidence": SlotLifecycleEvidence,
            "ValidationProgrammeResult": ValidationProgrammeResult,
            "ValidationProgrammeSources": ValidationProgrammeSources,
            "VerifiedDevelopmentEvidence": VerifiedDevelopmentEvidence,
            "freeze_development_evidence": freeze_development_evidence,
            "run_validation_programme": run_validation_programme,
        }[name]
    raise AttributeError(name)


__all__ = [
    "AttachedOutcome",
    "CandidateDefinition",
    "CandidateSignal",
    "CommonGridSplit",
    "EXPECTED_FAMILIES",
    "VALIDATION_SLOT_ROSTER",
    "ExecutionStatus",
    "FinalHoldoutBatch",
    "OutcomeComponent",
    "OutcomePolicy",
    "ScientificDecision",
    "ValidationProgrammeConfig",
    "ValidationSlot",
    "ValidationSlotKind",
    "ValidationTerminalState",
    "ValidationWorkBudget",
    "ValidationWorkBudgetViolation",
    "ValidationWorkDemand",
    "VerifiedFinalAccess",
    "evaluation_id_for_slot",
    "freeze_validation_slot_roster",
    "freeze_common_grid_split",
    "freeze_final_batch",
    "validation_roster_sha256",
    "attach_outcome",
    "CostApplicationCase",
    "CandidatePrimitiveCase",
    "DevelopmentOutcomeCase",
    "ProgrammePreflightMetadata",
    "SlotLifecycleEvidence",
    "ValidationProgrammeResult",
    "ValidationProgrammeSources",
    "VerifiedDevelopmentEvidence",
    "freeze_development_evidence",
    "run_validation_programme",
]
