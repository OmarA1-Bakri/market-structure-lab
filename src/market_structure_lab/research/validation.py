"""Primitive-input Phase 5 validation-programme orchestration.

This module is deliberately thin: callers may provide only preflight metadata plus
raw observations or factory-issued primitive capabilities.  The programme derives
all family decisions internally by calling the existing split, outcome, control,
cost, statistics, robustness, and receipt primitives in a fixed non-final order.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass
from datetime import timedelta
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
from market_structure_lab.research.candidates import (
    CandidateSignal,
    VerifiedProfileStream,
    detect_candidate_signals,
)
from market_structure_lab.research.controls import (
    ControlKind,
    ControlOpportunity,
    LegalBoundaryInterval,
    SelectorInput,
    ControlSelectionStatus,
    build_naive_zero_controls,
    build_persistence_controls,
    build_selector_evidence,
    select_stratified_controls,
)
from market_structure_lab.data.price_precision import SourcePricePrecisionUnavailable
from market_structure_lab.research.costs import (
    CostAdmissionStatus,
    CostPolicy,
    CostSide,
    apply_cost_batch,
    assess_cost_policy,
    verify_cost_policy,
)
from market_structure_lab.research.models import (
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
    CandidateDefinition,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
    candidate_definition_for_slot,
    evaluation_id_for_slot,
    freeze_validation_slot_roster,
)
from market_structure_lab.research.outcomes import AttachedOutcome, attach_outcome
from market_structure_lab.research.receipts import (
    EvaluationReceiptRequest,
    ProgrammeReceipt,
    ReceiptPublication,
    publish_evaluation_receipt_batch,
    publish_programme_receipt,
    verify_evaluation_receipt,
    verify_programme_receipt,
)
from market_structure_lab.research.robustness import (
    OlsExposureEvent,
    RobustnessLowerBounds,
    evaluate_exposure_ols,
    evaluate_robustness_gates,
)
from market_structure_lab.research.splits import (
    EventInterval,
    SymbolCoverage,
    freeze_common_grid_split,
    purge_and_embargo,
)
from market_structure_lab.research.statistics import (
    ControlStatistic,
    CostOpportunity,
    EvaluationStatistic,
    FamilyDecisionEvidence,
    FamilyDecisionInput,
    FamilyInferenceRole,
    WeeklyVectorObservation,
    aggregate_weekly_vectors,
    bootstrap_weekly_mean,
    classify_family_decision,
    derive_mde_evidence,
    deterministic_validation_seed,
    holm_family_correction,
)

_MIN_SYMBOLS = 5
_MIN_COMMON_COMPLETE_DAYS = 730
_FAILED_PREFLIGHT_STATE = ValidationTerminalState(
    ExecutionStatus.FAILED,
    ScientificDecision.NOT_EVALUATED,
)
_STAGE_ORDER = (
    "aggregate_capability",
    "human_candidate",
    "A_candidate_definition",
    "A_signal",
    "B_candidate_definition",
    "B_signal",
    "G_candidate_definition",
    "G_signal",
    "E_candidate_definition",
    "E_signal",
    "D_candidate_definition",
    "D_signal",
    "development_outcomes",
    "common_grid_split",
    "purge_embargo",
    "controls",
    "costs",
    "statistics",
    "robustness",
    "terminalize",
)
_REQUIRED_CONTROL_ROLES = {
    "A": ("naive", "unconditional", "persistence"),
    "B": ("price_baseline", "structure_only"),
    "G": ("atr_only", "donchian_only"),
    "E": ("price_only", "rate_matched_placebo"),
    "D": ("failed_donchian", "pseudo_level"),
}
_PRIMARY_COUNTS = {"A": 24, "B": 8, "G": 8, "E": 8, "D": 16}
_DEVELOPMENT_EVIDENCE_SEAL = object()


@dataclass(frozen=True, slots=True)
class ProgrammePreflightMetadata:
    """Bounded metadata admitted before source iteration or lifecycle work."""

    code_commit: str
    lockfile_sha256: str
    repository_clean: bool
    cost_policy_sha256: str | None
    work_demand: ValidationWorkDemand

    def __post_init__(self) -> None:
        if not isinstance(self.repository_clean, bool):
            raise TypeError("repository_clean must be bool")
        if self.cost_policy_sha256 is not None:
            _require_sha256(self.cost_policy_sha256, "cost_policy_sha256")
        if not isinstance(self.work_demand, ValidationWorkDemand):
            raise TypeError("work_demand must be a ValidationWorkDemand")


@dataclass(frozen=True, slots=True)
class CandidatePrimitiveCase:
    """Representative human candidate primitive inputs for one family.

    Family B may be represented by a verified profile capability when independent
    source precision exists; without that capability the orchestrator records B as
    a profile-capability diagnostic and never fabricates a subordinate definition.
    """

    family: str
    slot: ValidationSlot
    definition: CandidateDefinition | None = None
    signals: tuple[CandidateSignal, ...] = ()
    profile_stream: VerifiedProfileStream | None = None

    def __post_init__(self) -> None:
        if self.family not in EXPECTED_FAMILIES:
            raise ValueError("candidate case family must be A, B, G, E, or D")
        if self.slot.family != self.family or self.slot not in VALIDATION_SLOT_ROSTER:
            raise ValueError("candidate case slot must be in the frozen roster for its family")
        if self.definition is not None:
            if not isinstance(self.definition, CandidateDefinition):
                raise TypeError("candidate definition must be CandidateDefinition")
            if self.definition.slot != self.slot or self.definition.family != self.family:
                raise ValueError("candidate definition does not match its case slot")
        _require_tuple(self.signals, CandidateSignal, "candidate case signals")
        if any(signal.family != self.family for signal in self.signals):
            raise ValueError("candidate signals do not match candidate case family")
        if self.profile_stream is not None and not isinstance(
            self.profile_stream, VerifiedProfileStream
        ):
            raise TypeError("profile_stream must be VerifiedProfileStream")


@dataclass(frozen=True, slots=True)
class DevelopmentOutcomeCase:
    """Raw non-final outcome inputs consumed by attach_outcome."""

    signal: CandidateSignal
    minute_path: Iterable[Mapping[str, object]]
    policy: OutcomePolicy


@dataclass(frozen=True, slots=True)
class CostApplicationCase:
    """Raw trade-cost inputs consumed by assess/verify/apply cost primitives."""

    side: CostSide
    entry_price: float
    exit_price: float
    event_id: str
    event_timestamp: object
    venue: str
    symbol: str
    timeframe: str


@dataclass(frozen=True, slots=True)
class VerifiedDevelopmentEvidence:
    """Factory-issued, non-final statistical and robustness source evidence."""

    programme_id: str
    config_sha256: str
    source_publication_sha256: str
    component: str
    fold_id: str
    candidate_lineage_sha256: str
    weekly_observations: Mapping[str, tuple[WeeklyVectorObservation, ...]]
    robustness_lower_bounds: Mapping[str, RobustnessLowerBounds]
    exposure_train_events: Mapping[str, tuple[OlsExposureEvent, ...]]
    exposure_validation_events: Mapping[str, tuple[OlsExposureEvent, ...]]
    evidence_sha256: str
    seal: InitVar[object | None] = None

    def __post_init__(self, seal: object | None) -> None:
        if seal is not _DEVELOPMENT_EVIDENCE_SEAL:
            raise TypeError("VerifiedDevelopmentEvidence must be issued by its factory")
        if self.component != "development":
            raise ValueError("development evidence cannot carry final-holdout components")
        _require_sha256(self.config_sha256, "config_sha256")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_sha256(self.candidate_lineage_sha256, "candidate_lineage_sha256")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        object.__setattr__(
            self,
            "weekly_observations",
            _freeze_mapping_tuple(
                self.weekly_observations, WeeklyVectorObservation, "weekly_observations"
            ),
        )
        object.__setattr__(
            self,
            "robustness_lower_bounds",
            MappingProxyType(dict(self.robustness_lower_bounds)),
        )
        object.__setattr__(
            self,
            "exposure_train_events",
            _freeze_mapping_tuple(
                self.exposure_train_events, OlsExposureEvent, "exposure_train_events"
            ),
        )
        object.__setattr__(
            self,
            "exposure_validation_events",
            _freeze_mapping_tuple(
                self.exposure_validation_events,
                OlsExposureEvent,
                "exposure_validation_events",
            ),
        )

    def verify(
        self,
        config: ValidationProgrammeConfig,
        aggregate_series: VerifiedAggregateSeries,
        candidate_cases: Mapping[str, CandidatePrimitiveCase],
    ) -> None:
        """Reject evidence detached from its programme, source, or candidate lineage."""

        if (
            self.programme_id != config.programme_id
            or self.config_sha256 != config.sha256
            or self.source_publication_sha256 != aggregate_series.publication_sha256
        ):
            raise ValueError("development evidence crosses programme or source identity")
        candidate_lineage = _candidate_lineage_sha256(candidate_cases)
        if self.candidate_lineage_sha256 != candidate_lineage:
            raise ValueError("development evidence candidate lineage does not match")
        if self.evidence_sha256 != _development_evidence_sha256(
            programme_id=self.programme_id,
            config_sha256=self.config_sha256,
            source_publication_sha256=self.source_publication_sha256,
            component=self.component,
            fold_id=self.fold_id,
            candidate_lineage_sha256=self.candidate_lineage_sha256,
            weekly_observations=self.weekly_observations,
            robustness_lower_bounds=self.robustness_lower_bounds,
            exposure_train_events=self.exposure_train_events,
            exposure_validation_events=self.exposure_validation_events,
        ):
            raise ValueError("development evidence identity does not match its payload")


def freeze_development_evidence(
    *,
    config: ValidationProgrammeConfig,
    aggregate_series: VerifiedAggregateSeries,
    candidate_cases: Mapping[str, CandidatePrimitiveCase],
    weekly_observations: Mapping[str, tuple[WeeklyVectorObservation, ...]],
    robustness_lower_bounds: Mapping[str, RobustnessLowerBounds],
    exposure_train_events: Mapping[str, tuple[OlsExposureEvent, ...]],
    exposure_validation_events: Mapping[str, tuple[OlsExposureEvent, ...]],
    fold_id: str = "outer-1/inner-1",
) -> VerifiedDevelopmentEvidence:
    """Validate and bind non-final inputs before they can influence family decisions."""

    if not isinstance(config, ValidationProgrammeConfig):
        raise TypeError("config must be ValidationProgrammeConfig")
    if not isinstance(aggregate_series, VerifiedAggregateSeries):
        raise TypeError("aggregate_series must be VerifiedAggregateSeries")
    cases = _freeze_candidate_cases(candidate_cases)
    weekly = _freeze_mapping_tuple(
        weekly_observations, WeeklyVectorObservation, "weekly_observations"
    )
    robustness = MappingProxyType(dict(robustness_lower_bounds))
    train = _freeze_mapping_tuple(exposure_train_events, OlsExposureEvent, "exposure_train_events")
    validation = _freeze_mapping_tuple(
        exposure_validation_events, OlsExposureEvent, "exposure_validation_events"
    )
    if set(robustness) != set(EXPECTED_FAMILIES):
        raise ValueError("robustness_lower_bounds must cover A, B, G, E, D exactly")
    publication = aggregate_series.publication_sha256
    seen_weekly_rows: set[str] = set()
    for family in EXPECTED_FAMILIES:
        if not weekly[family]:
            raise ValueError(f"{family} weekly observations are required")
        for row in weekly[family]:
            if row.source_publication_sha256 != publication:
                raise ValueError("weekly observation crosses source publication")
            if row.row_id in seen_weekly_rows:
                raise ValueError("duplicate weekly observation row identity")
            seen_weekly_rows.add(row.row_id)
        bounds = robustness[family]
        if not isinstance(bounds, RobustnessLowerBounds):
            raise TypeError("robustness_lower_bounds must contain RobustnessLowerBounds")
        bounds.verify_identity()
        perturbations = bounds.adjacent_lookback_perturbations
        perturbations.verify_identity()
        definition = cases[family].definition or cases["A"].definition
        if definition is None:
            raise ValueError("A candidate definition is required for development evidence")
        if (
            perturbations.family != family
            or perturbations.candidate_id != definition.candidate_id
            or perturbations.slot_id != cases[family].slot.slot_id
        ):
            raise ValueError("robustness evidence crosses candidate family or slot lineage")
        train_ids = {row.row_id for row in train[family]}
        validation_ids = {row.row_id for row in validation[family]}
        if not train_ids or not validation_ids or train_ids & validation_ids:
            raise ValueError("exposure train and validation rows must be non-empty and disjoint")
        evaluate_exposure_ols(train[family], validation[family])
    lineage = _candidate_lineage_sha256(cases)
    evidence_sha256 = _development_evidence_sha256(
        programme_id=config.programme_id,
        config_sha256=config.sha256,
        source_publication_sha256=publication,
        component="development",
        fold_id=fold_id,
        candidate_lineage_sha256=lineage,
        weekly_observations=weekly,
        robustness_lower_bounds=robustness,
        exposure_train_events=train,
        exposure_validation_events=validation,
    )
    return VerifiedDevelopmentEvidence(
        programme_id=config.programme_id,
        config_sha256=config.sha256,
        source_publication_sha256=publication,
        component="development",
        fold_id=fold_id,
        candidate_lineage_sha256=lineage,
        weekly_observations=weekly,
        robustness_lower_bounds=robustness,
        exposure_train_events=train,
        exposure_validation_events=validation,
        evidence_sha256=evidence_sha256,
        seal=_DEVELOPMENT_EVIDENCE_SEAL,
    )


@dataclass(frozen=True, slots=True)
class ValidationProgrammeSources:
    """Primitive inputs for one non-final validation programme.

    No field carries final-holdout rows, source/stage hashes, precomputed p-values,
    caller terminal decisions, family decisions, or selection booleans.
    """

    metadata: ProgrammePreflightMetadata
    symbol_coverages: tuple[SymbolCoverage, ...]
    aggregate_series: VerifiedAggregateSeries
    candidate_cases: Mapping[str, CandidatePrimitiveCase]
    outcome_cases: tuple[DevelopmentOutcomeCase, ...]
    purge_training: tuple[EventInterval, ...]
    purge_test: tuple[EventInterval, ...]
    legal_boundaries: tuple[LegalBoundaryInterval, ...]
    control_candidates: tuple[ControlOpportunity, ...]
    control_pool: tuple[ControlOpportunity, ...]
    selector_inputs: tuple[SelectorInput, ...]
    inner_selector_scores: Mapping[str, float]
    outer_diagnostic_scores: Mapping[str, float]
    cost_policy: CostPolicy
    cost_cases: tuple[CostApplicationCase, ...]
    cost_opportunities: tuple[CostOpportunity, ...]
    development_evidence: VerifiedDevelopmentEvidence
    deferred_source_rows: Iterable[Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, ProgrammePreflightMetadata):
            raise TypeError("metadata must be ProgrammePreflightMetadata")
        if not isinstance(self.aggregate_series, VerifiedAggregateSeries):
            raise TypeError("aggregate_series must be VerifiedAggregateSeries")
        object.__setattr__(self, "candidate_cases", _freeze_candidate_cases(self.candidate_cases))
        if not isinstance(self.development_evidence, VerifiedDevelopmentEvidence):
            raise TypeError("development_evidence must be VerifiedDevelopmentEvidence")
        _require_tuple(self.symbol_coverages, SymbolCoverage, "symbol_coverages")
        _require_tuple(self.outcome_cases, DevelopmentOutcomeCase, "outcome_cases")
        _require_tuple(self.purge_training, EventInterval, "purge_training")
        _require_tuple(self.purge_test, EventInterval, "purge_test")
        _require_tuple(self.legal_boundaries, LegalBoundaryInterval, "legal_boundaries")
        _require_tuple(self.control_candidates, ControlOpportunity, "control_candidates")
        _require_tuple(self.control_pool, ControlOpportunity, "control_pool")
        _require_tuple(self.selector_inputs, SelectorInput, "selector_inputs")
        selector_ids = {item.row_identity for item in self.selector_inputs}
        object.__setattr__(
            self,
            "inner_selector_scores",
            _freeze_selector_scores(
                self.inner_selector_scores, selector_ids, "inner_selector_scores"
            ),
        )
        object.__setattr__(
            self,
            "outer_diagnostic_scores",
            _freeze_selector_scores(
                self.outer_diagnostic_scores, selector_ids, "outer_diagnostic_scores"
            ),
        )
        if not isinstance(self.cost_policy, CostPolicy):
            raise TypeError("cost_policy must be CostPolicy")
        _require_tuple(self.cost_cases, CostApplicationCase, "cost_cases")
        _require_tuple(self.cost_opportunities, CostOpportunity, "cost_opportunities")


@dataclass(frozen=True, slots=True)
class SlotLifecycleEvidence:
    """Derived slot evidence after primitive-backed classification."""

    slot_id: str
    family: str
    selected_by: str
    terminal_state: ValidationTerminalState
    family_decision_sha256: str
    primitive_evidence_sha256: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "slot_id": self.slot_id,
            "family": self.family,
            "selected_by": self.selected_by,
            "terminal_state": {
                "execution_status": self.terminal_state.execution_status.value,
                "decision": self.terminal_state.decision.value,
            },
            "family_decision_sha256": self.family_decision_sha256,
            "primitive_evidence_sha256": self.primitive_evidence_sha256,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _FamilyPrimitiveStatistics:
    family: str
    weekly_sha256: str
    bootstrap_reason: str
    bootstrap_p_value: float
    bootstrap_ci_lower: float
    support_sufficient: bool
    interval_width_ok: bool
    holm_adjusted_p_value: float
    holm_rejected: bool
    mde_sha256: str


@dataclass(frozen=True, slots=True)
class ValidationProgrammeResult:
    """Verified output of one primitive-backed validation programme run."""

    output_root: Path
    programme_receipt_path: Path
    programme_receipt: ProgrammeReceipt
    evaluation_receipts: tuple[ReceiptPublication, ...]
    terminal_by_slot: Mapping[str, ValidationTerminalState]
    family_decisions: Mapping[str, ScientificDecision]
    slot_evidence: tuple[SlotLifecycleEvidence, ...]
    stage_order: tuple[str, ...]
    primitive_evidence: Mapping[str, str]
    final_holdout_access_count: int = 0
    selection_policy: str = "inner_folds_only"
    inner_selected_row_identity: str = ""
    outer_diagnostic_best_row_identity: str = ""


@dataclass(frozen=True, slots=True)
class ValidationPreflightFailureResult:
    """Immutable failed/not-evaluated publication created before source construction."""

    output_root: Path
    programme_receipt_path: Path
    programme_receipt: ProgrammeReceipt
    evaluation_receipts: tuple[ReceiptPublication, ...]
    reason_code: str
    missing_prerequisites: tuple[str, ...]
    source_manifest_sha256: str
    final_holdout_access_count: int = 0


def publish_validation_preflight_failure(
    config: ValidationProgrammeConfig,
    *,
    output_root: Path,
    reason_code: str,
    missing_prerequisites: tuple[str, ...],
    source_manifest_sha256: str,
) -> ValidationPreflightFailureResult:
    """Publish the complete terminal ledger when real primitive sources do not exist."""

    if not isinstance(config, ValidationProgrammeConfig):
        raise TypeError("config must be ValidationProgrammeConfig")
    if reason_code != "missing_prerequisites":
        raise ValueError("preflight reason_code must be missing_prerequisites")
    if (
        not isinstance(missing_prerequisites, tuple)
        or not missing_prerequisites
        or missing_prerequisites != tuple(sorted(set(missing_prerequisites)))
        or any(
            not item or len(item) > 64 or set(item) - set("abcdefghijklmnopqrstuvwxyz0123456789_")
            for item in missing_prerequisites
        )
    ):
        raise ValueError("missing_prerequisites must be a non-empty sorted tuple of safe tokens")
    _require_sha256(source_manifest_sha256, "source_manifest_sha256")
    root = Path(output_root)
    _reject_existing_output_root(root)
    publication, evaluations = _publish_terminal_receipts(
        config,
        root,
        _failed_terminal_map(config),
        reason=reason_code,
        primitive_evidence={"source_preflight_manifest": source_manifest_sha256},
    )
    return ValidationPreflightFailureResult(
        output_root=root,
        programme_receipt_path=publication.path,
        programme_receipt=verify_programme_receipt(publication.path),
        evaluation_receipts=evaluations,
        reason_code=reason_code,
        missing_prerequisites=missing_prerequisites,
        source_manifest_sha256=source_manifest_sha256,
    )


def run_validation_programme(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
    output_root: Path,
) -> ValidationProgrammeResult:
    """Run the fixed non-final validation lifecycle and publish VP plus 1104 VRs."""

    root = Path(output_root)
    _reject_existing_output_root(root)
    try:
        _preflight(config, sources)
        primitive_evidence = _run_primitive_lifecycle(config, sources)
        family_evidence = _derive_family_decisions(config, sources, primitive_evidence)
        slot_evidence, terminal_by_slot = _terminalize_slots(
            config, primitive_evidence, family_evidence
        )
    except (ValueError, TypeError, ValidationWorkBudgetViolation) as error:
        _publish_terminal_receipts(
            config,
            root,
            _failed_terminal_map(config),
            reason=str(error),
            primitive_evidence={},
        )
        raise
    publication, evaluation_publications = _publish_terminal_receipts(
        config,
        root,
        terminal_by_slot,
        reason="primitive validation lifecycle completed",
        slot_evidence=slot_evidence,
        primitive_evidence=primitive_evidence,
    )

    programme = verify_programme_receipt(publication.path)
    inner_selected, outer_best = _selector_identities(sources)
    verified_evaluations = tuple(
        ReceiptPublication(
            path=item.path, receipt=verify_evaluation_receipt(item.path, config=config)
        )
        for item in evaluation_publications
    )
    return ValidationProgrammeResult(
        output_root=root,
        programme_receipt_path=publication.path,
        programme_receipt=programme,
        evaluation_receipts=verified_evaluations,
        terminal_by_slot=MappingProxyType(dict(terminal_by_slot)),
        family_decisions=MappingProxyType(
            {
                family: evidence.terminal_state.decision
                for family, evidence in family_evidence.items()
            }
        ),
        slot_evidence=slot_evidence,
        stage_order=tuple(primitive_evidence),
        primitive_evidence=MappingProxyType(dict(primitive_evidence)),
        inner_selected_row_identity=inner_selected,
        outer_diagnostic_best_row_identity=outer_best,
    )


def _preflight(config: ValidationProgrammeConfig, sources: ValidationProgrammeSources) -> None:
    if not isinstance(config, ValidationProgrammeConfig):
        raise TypeError("config must be ValidationProgrammeConfig")
    if not isinstance(sources, ValidationProgrammeSources):
        raise TypeError("sources must be ValidationProgrammeSources")
    roster = freeze_validation_slot_roster(config.roster)
    if len(roster) != 1_104:
        raise ValueError("validation roster must contain exactly 1104 slots")
    metadata = sources.metadata
    if metadata.code_commit != config.code_commit:
        raise ValueError("code commit metadata does not match programme config")
    if metadata.lockfile_sha256 != config.lockfile_sha256:
        raise ValueError("lockfile metadata does not match programme config")
    if not metadata.repository_clean:
        raise ValueError("dirty or uncommitted code is not admissible")
    if metadata.cost_policy_sha256 != config.cost_policy_sha256:
        raise ValueError("cost policy metadata does not match programme config")
    if sources.cost_policy.sha256 != config.cost_policy_sha256:
        raise ValueError("cost policy capability does not match programme config")
    if sources.aggregate_series.publication_sha256 != config.dataset_sha256:
        raise ValueError("aggregate publication does not match programme dataset identity")
    sources.development_evidence.verify(
        config,
        sources.aggregate_series,
        sources.candidate_cases,
    )
    for coverage in sources.symbol_coverages:
        if coverage.symbol in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError(f"primary source conflict symbol is not admissible: {coverage.symbol}")
        if coverage.source_conflict:
            raise ValueError(f"source conflict is not admissible: {coverage.symbol}")
        if not coverage.mapping_compatible:
            raise ValueError(f"source mapping is incompatible: {coverage.symbol}")
    symbols = tuple(coverage.symbol for coverage in sources.symbol_coverages)
    if len(symbols) != len(set(symbols)):
        raise ValueError("validation source coverage contains duplicate symbols")
    if len(symbols) < _MIN_SYMBOLS:
        raise ValueError("validation requires at least five eligible symbols")
    common_start = max(coverage.complete_start for coverage in sources.symbol_coverages)
    common_end = min(coverage.complete_end for coverage in sources.symbol_coverages)
    if (common_end - common_start).days < _MIN_COMMON_COMPLETE_DAYS:
        raise ValueError("validation requires at least 730 common complete days")
    config.work_budget.preflight(metadata.work_demand, deferred_work=sources.deferred_source_rows)


def _run_primitive_lifecycle(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
) -> dict[str, str]:
    evidence: dict[str, str] = {}
    _verify_aggregate_capability(config, sources.aggregate_series)
    evidence["aggregate_capability"] = sources.aggregate_series.series_sha256

    candidates_sha = _verify_candidate_stage_order_and_parentage(sources)
    evidence.update(candidates_sha)

    outcomes = _attach_development_outcomes(config, sources)
    evidence["development_outcomes"] = hash_json(
        "validation-development-outcomes-v1",
        [outcome.outcome_id for outcome in outcomes],
    )

    split = freeze_common_grid_split(
        programme_id=config.programme_id,
        coverages=sources.symbol_coverages,
        timeframe=sources.aggregate_series.target_timeframe,
        budget=config.work_budget,
    )
    evidence["common_grid_split"] = split.split_sha256

    purge = purge_and_embargo(
        sources.purge_training,
        sources.purge_test,
        test_start=split.outer_folds[0].test.start,
        test_end=split.outer_folds[0].test.end,
        horizon=timedelta(hours=24),
        budget=config.work_budget,
    )
    evidence["purge_embargo"] = purge.result_sha256

    control_sha = _run_control_primitives(config, sources)
    evidence["controls"] = control_sha
    cost_sha = _run_cost_primitives(config, sources)
    evidence["costs"] = cost_sha
    statistics_sha = _run_statistics_primitives(config, sources)
    evidence["statistics"] = statistics_sha
    robustness_sha = _run_robustness_primitives(sources)
    evidence["robustness"] = robustness_sha
    evidence["terminalize"] = hash_json("validation-terminalize-inputs-v1", evidence)
    if tuple(evidence) != _STAGE_ORDER:
        raise RuntimeError("validation primitive execution order differs from the frozen lifecycle")
    return evidence


def _verify_aggregate_capability(
    config: ValidationProgrammeConfig, series: VerifiedAggregateSeries
) -> None:
    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("aggregate evidence must be a VerifiedAggregateSeries capability")
    if series.publication_sha256 != config.dataset_sha256:
        raise ValueError("aggregate publication does not match programme dataset identity")


def _verify_candidate_stage_order_and_parentage(
    sources: ValidationProgrammeSources,
) -> dict[str, str]:
    cases = sources.candidate_cases
    a_case = cases["A"]
    if a_case.definition is None:
        raise ValueError("A human candidate definition is required before comparators")
    a_definition = candidate_definition_for_slot(a_case.slot, sources.aggregate_series)
    if a_definition != a_case.definition:
        raise ValueError("A candidate definition does not replay from aggregate capability")
    a_signals = detect_candidate_signals(a_definition, sources.aggregate_series)
    if a_signals != a_case.signals:
        raise ValueError("A signals do not replay from the frozen candidate definition")
    if not a_signals:
        raise ValueError("A evidence must contain at least one issued signal")
    a_signal = a_signals[0]
    evidence = {
        "human_candidate": hash_json(
            "validation-human-candidate-roster-v1",
            {family: cases[family].slot.to_dict() for family in EXPECTED_FAMILIES},
        ),
        "A_candidate_definition": a_definition.candidate_id,
        "A_signal": a_signal.signal_id,
    }
    for family in ("B", "G", "E", "D"):
        case = cases[family]
        if family == "B" and case.definition is None:
            try:
                candidate_definition_for_slot(
                    case.slot, sources.aggregate_series, parent_a_candidate=a_definition
                )
            except SourcePricePrecisionUnavailable as error:
                evidence["B_candidate_definition"] = (
                    f"profile-capability-unavailable:{type(error).__name__}"
                )
                evidence["B_signal"] = "profile-capability-unavailable"
                continue
            raise ValueError(
                "B candidate definition replay unexpectedly succeeded without a supplied definition"
            )
        if case.definition is None:
            raise ValueError(f"{family} human candidate definition is required")
        parent = a_definition if family in {"B", "E"} else None
        replay = candidate_definition_for_slot(
            case.slot,
            sources.aggregate_series,
            parent_a_candidate=parent,
            profile_stream=case.profile_stream,
        )
        if replay != case.definition:
            raise ValueError(f"{family} candidate definition does not replay from primitive inputs")
        replay_signals = detect_candidate_signals(
            replay,
            sources.aggregate_series,
            a_opportunities=(a_signal,) if family in {"B", "E"} else (),
            profile_stream=case.profile_stream,
        )
        if replay_signals != case.signals:
            raise ValueError(f"{family} signals do not replay from the frozen candidate definition")
        if not replay_signals:
            raise ValueError(f"{family} evidence must contain at least one issued signal")
        signal = replay_signals[0]
        if (
            signal.source_series_sha256 != a_signal.source_series_sha256
            or signal.source_publication_sha256 != a_signal.source_publication_sha256
            or signal.segment_id != a_signal.segment_id
            or signal.timeframe != a_signal.timeframe
            or signal.direction != a_signal.direction
        ):
            raise ValueError(f"{family} comparator does not bind the frozen A comparator evidence")
        evidence[f"{family}_candidate_definition"] = replay.candidate_id
        evidence[f"{family}_signal"] = signal.signal_id
    return evidence


def _attach_development_outcomes(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
) -> tuple[AttachedOutcome, ...]:
    outcomes: list[AttachedOutcome] = []
    for case in sources.outcome_cases:
        if case.policy.component is not OutcomeComponent.DEVELOPMENT:
            raise ValueError("validation programme cannot attach final-holdout outcomes")
        outcomes.append(
            attach_outcome(
                case.signal,
                sources.aggregate_series,
                case.minute_path,
                case.policy,
                config.work_budget,
            )
        )
    if not outcomes:
        raise ValueError("at least one development outcome case is required")
    return tuple(outcomes)


def _run_control_primitives(
    config: ValidationProgrammeConfig, sources: ValidationProgrammeSources
) -> str:
    _validate_control_boundaries(config, sources)
    if not sources.legal_boundaries:
        raise ValueError("legal boundary intervals are required before controls")
    if any(not boundary.is_non_final for boundary in sources.legal_boundaries):
        raise ValueError("control legal boundaries must be non-final")
    a_candidates, a_pool = _family_control_rows(sources, "A")
    naive = build_naive_zero_controls(a_candidates, budget=config.work_budget)
    unconditional = select_stratified_controls(
        a_candidates,
        a_pool,
        kind=ControlKind.UNCONDITIONAL,
        required_control_role="unconditional",
        budget=config.work_budget,
    )
    persistence = build_persistence_controls(
        a_candidates,
        a_pool,
        budget=config.work_budget,
    )
    inner_selected, outer_best = _selector_identities(sources)
    selector = build_selector_evidence(
        sources.selector_inputs,
        selected_row_identities=(inner_selected,),
        source_publication_sha256=sources.aggregate_series.publication_sha256,
        selector_id="inner-fold-selector-v1",
        selector_cutoff=max(row.feature_time for row in sources.selector_inputs),
    )
    return hash_json(
        "validation-control-primitives-v1",
        {
            "naive": naive.sha256,
            "unconditional": unconditional.sha256,
            "persistence": persistence.sha256,
            "role_complete": {
                "naive": naive.status is ControlSelectionStatus.COMPLETE,
                "unconditional": unconditional.status is ControlSelectionStatus.COMPLETE,
                "persistence": persistence.status is ControlSelectionStatus.COMPLETE,
            },
            "selector": selector.selector_sha256,
            "inner_selected_row_identity": inner_selected,
            "outer_diagnostic_best_row_identity": outer_best,
            "legal_boundaries": [item.boundary_sha256 for item in sources.legal_boundaries],
        },
    )


def _validate_control_boundaries(
    config: ValidationProgrammeConfig, sources: ValidationProgrammeSources
) -> None:
    if not sources.legal_boundaries:
        raise ValueError("legal boundary intervals are required before controls")
    expected_publication = sources.aggregate_series.publication_sha256
    boundary_by_key = {boundary.key: boundary for boundary in sources.legal_boundaries}
    if len(boundary_by_key) != len(sources.legal_boundaries):
        raise ValueError("duplicate legal boundary identity")
    opportunities = (*sources.control_candidates, *sources.control_pool)
    by_row_id: dict[str, ControlOpportunity] = {}
    for row in opportunities:
        if row.row_identity in by_row_id:
            raise ValueError("duplicate control opportunity row identity")
        by_row_id[row.row_identity] = row
        if (
            row.programme_id != config.programme_id
            or row.publication_sha256 != expected_publication
            or not row.is_non_final
        ):
            raise ValueError(
                "control opportunity crosses programme, publication, or final boundary"
            )
        definition = (
            sources.candidate_cases[row.family].definition
            or sources.candidate_cases["A"].definition
        )
        if definition is None or row.candidate_id != definition.candidate_id:
            raise ValueError("control opportunity crosses candidate lineage")
        boundary = boundary_by_key.get(
            (
                row.programme_id,
                row.publication_sha256,
                row.symbol,
                row.timeframe,
                row.segment_id,
                row.fold_id,
                row.component,
                row.block_id,
            )
        )
        if boundary is None or not boundary.contains_path(
            feature_time=row.feature_time,
            signal_time=row.signal_time,
            entry_time=row.legal_entry_time,
            label_start=row.label_start,
            label_end=row.label_end,
        ):
            raise ValueError("control opportunity is outside its exact legal boundary")
    selector_by_id = {row.row_identity: row for row in sources.selector_inputs}
    if set(selector_by_id) != set(by_row_id):
        raise ValueError("selector inputs must exactly cover admitted control opportunities")
    for row_id, selector in selector_by_id.items():
        opportunity = by_row_id[row_id]
        if (
            selector.source_publication_sha256 != expected_publication
            or selector.feature_time != opportunity.feature_time
        ):
            raise ValueError("selector input differs from its admitted control opportunity")


def _run_cost_primitives(
    config: ValidationProgrammeConfig, sources: ValidationProgrammeSources
) -> str:
    assessment = assess_cost_policy(sources.cost_policy)
    if assessment.admission_status is not CostAdmissionStatus.ADMISSIBLE:
        raise ValueError(f"cost policy is not admissible: {assessment.reason}")
    coverage = verify_cost_policy(sources.cost_policy)
    trades = tuple(
        {
            "side": case.side,
            "entry_price": case.entry_price,
            "exit_price": case.exit_price,
            "event_id": case.event_id,
            "event_timestamp": case.event_timestamp,
            "venue": case.venue,
            "symbol": case.symbol,
            "timeframe": case.timeframe,
        }
        for case in sources.cost_cases
    )
    results = apply_cost_batch(
        sources.cost_policy,
        trades,
        demand=ValidationWorkDemand(outcomes=len(trades)),
        budget=config.work_budget,
    )
    return hash_json(
        "validation-cost-primitives-v1",
        {
            "policy": sources.cost_policy.sha256,
            "coverage": coverage.value,
            "applications": [item.evidence_sha256 for item in results],
        },
    )


def _run_statistics_primitives(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
) -> str:
    payload = {
        family: _family_statistics_payload(_family_primitive_statistics(config, sources, family))
        for family in EXPECTED_FAMILIES
    }
    return hash_json("validation-statistics-primitives-v1", payload)


def _family_primitive_statistics(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
    family: str,
) -> _FamilyPrimitiveStatistics:
    a_definition = sources.candidate_cases["A"].definition
    if a_definition is None:
        raise ValueError("A definition is required for statistical MDE identity")
    mde = derive_mde_evidence(
        opportunities=tuple(sources.cost_opportunities),
        programme_id=config.programme_id,
        candidate_id=a_definition.candidate_id,
        fold_id="outer-1/inner-1",
    )
    if mde.mde is None:
        raise ValueError("cost MDE evidence is inconclusive")
    observations = sources.development_evidence.weekly_observations[family]
    if not observations:
        raise ValueError(f"{family} weekly observations are required")
    weekly = aggregate_weekly_vectors(
        observations,
        vector_names=("net_return",),
        expected_assets=tuple(sorted({row.asset for row in observations})),
        fold_start=min(row.timestamp for row in observations) - timedelta(days=7),
        fold_end=max(row.timestamp for row in observations) + timedelta(days=7),
        source_publication_sha256=sources.aggregate_series.publication_sha256,
    )
    case_definition = sources.candidate_cases[family].definition or a_definition
    first_slot = _primary_slots(family)[0]
    seed = deterministic_validation_seed(
        programme_id=config.programme_id,
        candidate_id=case_definition.candidate_id,
        fold_id="outer-1/inner-1",
        purpose="weekly-bootstrap",
    )
    bootstrap = bootstrap_weekly_mean(
        weekly_values=tuple(week.values["net_return"] for week in weekly.weeks),
        seed=seed,
        budget=config.work_budget,
        mde=mde.mde,
        family=family,
        slot_id=first_slot.slot_id,
    )
    raw_p = bootstrap.p_value if bootstrap.p_value is not None else 1.0
    stats = tuple(
        EvaluationStatistic(
            slot_id=slot.slot_id,
            family=family,
            p_value=raw_p,
            status=ExecutionStatus.COMPLETED,
            decision=ScientificDecision.REJECTED,
        )
        for slot in _primary_slots(family)
    )
    holm = holm_family_correction(stats, family=family)
    first = holm[0]
    ci_lower = bootstrap.ci_lower if bootstrap.ci_lower is not None else -1.0
    support_sufficient = (
        bootstrap.required_support is not None and bootstrap.support >= bootstrap.required_support
    )
    interval_width_ok = (
        bootstrap.ci_lower is not None
        and bootstrap.ci_upper is not None
        and mde.mde is not None
        and (bootstrap.ci_upper - bootstrap.ci_lower) <= 2.0 * mde.mde
    )
    return _FamilyPrimitiveStatistics(
        family=family,
        weekly_sha256=weekly.sha256,
        bootstrap_reason=bootstrap.reason,
        bootstrap_p_value=raw_p,
        bootstrap_ci_lower=ci_lower,
        support_sufficient=support_sufficient,
        interval_width_ok=interval_width_ok,
        holm_adjusted_p_value=first.adjusted_p_value,
        holm_rejected=first.rejected,
        mde_sha256=mde.max_identity_sha256,
    )


def _family_statistics_payload(statistical: _FamilyPrimitiveStatistics) -> dict[str, object]:
    return {
        "family": statistical.family,
        "weekly_sha256": statistical.weekly_sha256,
        "bootstrap_reason": statistical.bootstrap_reason,
        "bootstrap_p_value": statistical.bootstrap_p_value,
        "bootstrap_ci_lower": statistical.bootstrap_ci_lower,
        "support_sufficient": statistical.support_sufficient,
        "interval_width_ok": statistical.interval_width_ok,
        "holm_adjusted_p_value": statistical.holm_adjusted_p_value,
        "holm_rejected": statistical.holm_rejected,
        "mde_sha256": statistical.mde_sha256,
    }


def _run_robustness_primitives(sources: ValidationProgrammeSources) -> str:
    payload: dict[str, object] = {}
    for family in EXPECTED_FAMILIES:
        robustness = evaluate_robustness_gates(
            sources.development_evidence.robustness_lower_bounds[family]
        )
        exposure = evaluate_exposure_ols(
            sources.development_evidence.exposure_train_events[family],
            sources.development_evidence.exposure_validation_events[family],
        )
        payload[family] = {"robustness": robustness.sha256, "exposure": exposure.sha256}
    return hash_json("validation-robustness-primitives-v1", payload)


def _derive_family_decisions(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
    primitive_evidence: Mapping[str, str],
) -> dict[str, FamilyDecisionEvidence]:
    decisions: dict[str, FamilyDecisionEvidence] = {}
    for family in EXPECTED_FAMILIES:
        statistical = _family_primitive_statistics(config, sources, family)
        controls = _control_statistics_from_statistics(config, sources, family, statistical)
        robustness = evaluate_robustness_gates(
            sources.development_evidence.robustness_lower_bounds[family]
        )
        exposure = evaluate_exposure_ols(
            sources.development_evidence.exposure_train_events[family],
            sources.development_evidence.exposure_validation_events[family],
        )
        family_input = FamilyDecisionInput(
            family=family,
            controls=controls,
            policy_valid=sources.candidate_cases[family].definition is not None,
            cost_coverage_complete=assess_cost_policy(sources.cost_policy).admission_status
            is CostAdmissionStatus.ADMISSIBLE,
            support_sufficient=statistical.support_sufficient,
            interval_width_ok=statistical.interval_width_ok,
            robustness_gate=robustness.terminal_state.decision
            is ScientificDecision.SUPPORTED_DEVELOPMENT,
            exposure_gate=exposure.terminal_state.decision
            is ScientificDecision.SUPPORTED_DEVELOPMENT,
            diagnostic_only=False,
        )
        evidence = classify_family_decision(family_input)
        if evidence.terminal_state.decision in (
            ScientificDecision.VALIDATED,
            ScientificDecision.PROMOTED,
        ):
            raise ValueError("caller terminal state cannot be injected into development validation")
        decisions[family] = evidence
    return decisions


def _control_statistics_from_statistics(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
    family: str,
    statistical: _FamilyPrimitiveStatistics,
) -> tuple[ControlStatistic, ...]:
    completeness = _control_role_completeness(config, sources, family)
    return tuple(
        ControlStatistic(
            role=role,
            corrected_p_value=statistical.holm_adjusted_p_value,
            corrected_lower_bound=statistical.bootstrap_ci_lower,
            support_sufficient=(
                statistical.support_sufficient
                and statistical.interval_width_ok
                and completeness[role]
                and statistical.holm_rejected
            ),
            inference_role=FamilyInferenceRole.CONTROL,
        )
        for role in _REQUIRED_CONTROL_ROLES[family]
    )


def _control_role_completeness(
    config: ValidationProgrammeConfig, sources: ValidationProgrammeSources, family: str
) -> dict[str, bool]:
    candidates, pool = _family_control_rows(sources, family)
    complete: dict[str, bool] = {}
    for role in _REQUIRED_CONTROL_ROLES[family]:
        if role == "naive":
            selection = build_naive_zero_controls(candidates, budget=config.work_budget)
        elif role == "persistence":
            selection = build_persistence_controls(candidates, pool, budget=config.work_budget)
        else:
            kind = {
                "unconditional": ControlKind.UNCONDITIONAL,
                "price_baseline": ControlKind.PRICE_BASELINE,
                "structure_only": ControlKind.AUGMENTED_SELECTED,
                "atr_only": ControlKind.ATR,
                "donchian_only": ControlKind.DONCHIAN,
                "price_only": ControlKind.PRICE_BASELINE,
                "rate_matched_placebo": ControlKind.RATE_MATCHED_PLACEBO,
                "failed_donchian": ControlKind.FAILED_DONCHIAN,
                "pseudo_level": ControlKind.PRIOR_WEEK_PSEUDO_LEVEL,
            }[role]
            selection = select_stratified_controls(
                candidates,
                pool,
                kind=kind,
                required_control_role=role,
                budget=config.work_budget,
            )
        complete[role] = selection.status is ControlSelectionStatus.COMPLETE
    return complete


def _family_control_rows(
    sources: ValidationProgrammeSources, family: str
) -> tuple[tuple[ControlOpportunity, ...], tuple[ControlOpportunity, ...]]:
    candidates = tuple(row for row in sources.control_candidates if row.family == family)
    pool = tuple(row for row in sources.control_pool if row.family == family)
    if not candidates or not pool:
        raise ValueError(f"{family} control rows are required")
    return candidates, pool


def _terminalize_slots(
    config: ValidationProgrammeConfig,
    primitive_evidence: Mapping[str, str],
    family_evidence: Mapping[str, FamilyDecisionEvidence],
) -> tuple[tuple[SlotLifecycleEvidence, ...], dict[str, ValidationTerminalState]]:
    primitive_sha = hash_json("validation-primitive-evidence-chain-v1", dict(primitive_evidence))
    slot_evidence: list[SlotLifecycleEvidence] = []
    terminal_by_slot: dict[str, ValidationTerminalState] = {}
    for slot in freeze_validation_slot_roster(config.roster):
        family_decision = family_evidence[slot.family]
        terminal = family_decision.terminal_state
        selected_by = "inner"
        if slot.family != "A" and slot.primary:
            selected_by = "inner:a_comparator_bound"
        if not slot.primary:
            selected_by = "diagnostic_only"
            if terminal.decision is ScientificDecision.SUPPORTED_DEVELOPMENT:
                terminal = ValidationTerminalState(
                    ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
                )
        evidence = SlotLifecycleEvidence(
            slot_id=slot.slot_id,
            family=slot.family,
            selected_by=selected_by,
            terminal_state=terminal,
            family_decision_sha256=hash_json(
                "validation-family-decision-v1",
                {
                    "family": slot.family,
                    "decision": family_decision.terminal_state.decision.value,
                    "reason": family_decision.reason,
                    "primitive_evidence_sha256": primitive_sha,
                },
            ),
            primitive_evidence_sha256=primitive_sha,
            reason=family_decision.reason,
        )
        slot_evidence.append(evidence)
        terminal_by_slot[slot.slot_id] = terminal
    return tuple(slot_evidence), terminal_by_slot


def _publish_terminal_receipts(
    config: ValidationProgrammeConfig,
    output_root: Path,
    terminal_by_slot: Mapping[str, ValidationTerminalState],
    *,
    reason: str,
    slot_evidence: tuple[SlotLifecycleEvidence, ...] = (),
    primitive_evidence: Mapping[str, str] | None = None,
) -> tuple[ReceiptPublication, tuple[ReceiptPublication, ...]]:
    root = Path(output_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = root.parent / f".{root.name}.staging.{os.getpid()}.{uuid.uuid4().hex}"
    evidence_by_slot = {item.slot_id: item for item in slot_evidence}
    requests = []
    for slot in freeze_validation_slot_roster(config.roster):
        terminal = terminal_by_slot[slot.slot_id]
        slot_payload: dict[str, object] = {
            "programme_id": config.programme_id,
            "slot_id": slot.slot_id,
            "terminal_state": {
                "execution_status": terminal.execution_status.value,
                "decision": terminal.decision.value,
            },
            "reason": reason,
        }
        if slot.slot_id in evidence_by_slot:
            slot_payload["slot_evidence"] = evidence_by_slot[slot.slot_id].to_dict()
        requests.append(
            EvaluationReceiptRequest(
                slot=slot,
                terminal_state=terminal,
                attempt=1,
                artifacts={
                    "slot-evidence.json": canonical_json(
                        "validation-slot-terminal-evidence-v1", slot_payload
                    )
                },
            )
        )
    try:
        evaluation_publications = publish_evaluation_receipt_batch(
            config,
            requests=tuple(requests),
            output_root=staging,
        )
        actual_ids = tuple(
            verify_evaluation_receipt(item.path, config=config).evaluation_id
            for item in evaluation_publications
        )
        expected_ids = tuple(
            evaluation_id_for_slot(config, slot)
            for slot in freeze_validation_slot_roster(config.roster)
        )
        if actual_ids != expected_ids:
            raise RuntimeError("evaluation receipt batch does not cover the frozen roster")
        programme_publication = publish_programme_receipt(
            config,
            terminal_by_slot=terminal_by_slot,
            output_root=staging,
            artifacts={
                "programme-evidence.json": canonical_json(
                    "validation-programme-terminal-evidence-v1",
                    {
                        "programme_id": config.programme_id,
                        "reason": reason,
                        "primitive_evidence": dict(primitive_evidence or {}),
                        "slot_evidence": [item.to_dict() for item in slot_evidence],
                        "final_holdout_access_count": 0,
                    },
                )
            },
        )
        verify_programme_receipt(programme_publication.path)
        for item in evaluation_publications:
            verify_evaluation_receipt(item.path, config=config)
        if os.path.lexists(root):
            raise FileExistsError("validation output root appeared during publication")
        staging.rename(root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    final_evaluations = tuple(
        ReceiptPublication(
            path=root / item.path.name,
            receipt=verify_evaluation_receipt(root / item.path.name, config=config),
        )
        for item in evaluation_publications
    )
    final_programme_path = root / programme_publication.path.name
    final_programme = ReceiptPublication(
        path=final_programme_path,
        receipt=verify_programme_receipt(final_programme_path),
    )
    return final_programme, final_evaluations


def _failed_terminal_map(config: ValidationProgrammeConfig) -> dict[str, ValidationTerminalState]:
    return {
        slot.slot_id: _FAILED_PREFLIGHT_STATE
        for slot in freeze_validation_slot_roster(config.roster)
    }


def _primary_slots(family: str) -> tuple[ValidationSlot, ...]:
    return tuple(
        slot
        for slot in VALIDATION_SLOT_ROSTER
        if slot.family == family and slot.kind is ValidationSlotKind.CORE and slot.primary
    )


def _slot_for_signal(signal: CandidateSignal) -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        if slot.slot_id == signal.candidate_slot_id:
            return slot
    raise ValueError("candidate signal slot is not in the frozen roster")


def _reject_existing_output_root(output_root: Path) -> None:
    if os.path.lexists(output_root):
        raise FileExistsError("validation output root already exists; refusing to overwrite")


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_tuple(value: tuple[Any, ...], item_type: type[Any], label: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, item_type) for item in value):
        raise TypeError(f"{label} must be a tuple of {item_type.__name__}")


def _freeze_candidate_cases(
    raw: Mapping[str, CandidatePrimitiveCase],
) -> Mapping[str, CandidatePrimitiveCase]:
    if set(raw) != set(EXPECTED_FAMILIES):
        raise ValueError("candidate_cases must cover A, B, G, E, D exactly")
    frozen: dict[str, CandidatePrimitiveCase] = {}
    for family, case in raw.items():
        if not isinstance(case, CandidatePrimitiveCase):
            raise TypeError("candidate_cases must contain CandidatePrimitiveCase")
        if case.family != family:
            raise ValueError("candidate case family differs from mapping key")
        frozen[family] = case
    return MappingProxyType(frozen)


def _candidate_lineage_sha256(
    candidate_cases: Mapping[str, CandidatePrimitiveCase],
) -> str:
    def candidate_id(case: CandidatePrimitiveCase) -> str | None:
        return case.definition.candidate_id if case.definition is not None else None

    return hash_json(
        "validation-development-candidate-lineage-v1",
        {
            family: {
                "slot_id": candidate_cases[family].slot.slot_id,
                "candidate_id": candidate_id(candidate_cases[family]),
                "signal_ids": [signal.signal_id for signal in candidate_cases[family].signals],
            }
            for family in EXPECTED_FAMILIES
        },
    )


def _development_evidence_sha256(
    *,
    programme_id: str,
    config_sha256: str,
    source_publication_sha256: str,
    component: str,
    fold_id: str,
    candidate_lineage_sha256: str,
    weekly_observations: Mapping[str, tuple[WeeklyVectorObservation, ...]],
    robustness_lower_bounds: Mapping[str, RobustnessLowerBounds],
    exposure_train_events: Mapping[str, tuple[OlsExposureEvent, ...]],
    exposure_validation_events: Mapping[str, tuple[OlsExposureEvent, ...]],
) -> str:
    def exposure_payload(row: OlsExposureEvent) -> dict[str, object]:
        return {
            "row_id": row.row_id,
            "asset": row.asset,
            "year": row.year,
            "target_return": row.target_return,
            "market_return": row.market_return,
            "market_trend": row.market_trend,
            "volatility_rank": row.volatility_rank,
            "turnover_rank": row.turnover_rank,
        }

    return hash_json(
        "verified-development-evidence-v1",
        {
            "programme_id": programme_id,
            "config_sha256": config_sha256,
            "source_publication_sha256": source_publication_sha256,
            "component": component,
            "fold_id": fold_id,
            "candidate_lineage_sha256": candidate_lineage_sha256,
            "weekly_observations": {
                family: [
                    {
                        "row_id": row.row_id,
                        "asset": row.asset,
                        "timestamp": row.timestamp,
                        "values": dict(sorted(row.values.items())),
                        "source_publication_sha256": row.source_publication_sha256,
                    }
                    for row in weekly_observations[family]
                ]
                for family in EXPECTED_FAMILIES
            },
            "robustness_lower_bounds": {
                family: robustness_lower_bounds[family].sha256 for family in EXPECTED_FAMILIES
            },
            "exposure_train_events": {
                family: [exposure_payload(row) for row in exposure_train_events[family]]
                for family in EXPECTED_FAMILIES
            },
            "exposure_validation_events": {
                family: [exposure_payload(row) for row in exposure_validation_events[family]]
                for family in EXPECTED_FAMILIES
            },
        },
    )


def _freeze_mapping_tuple(
    raw: Mapping[str, tuple[Any, ...]], item_type: type[Any], label: str
) -> Mapping[str, tuple[Any, ...]]:
    if set(raw) != set(EXPECTED_FAMILIES):
        raise ValueError(f"{label} must cover A, B, G, E, D exactly")
    frozen: dict[str, tuple[Any, ...]] = {}
    for family, values in raw.items():
        _require_tuple(values, item_type, f"{label}[{family}]")
        frozen[family] = values
    return MappingProxyType(frozen)


def _freeze_selector_scores(
    raw: Mapping[str, float], expected_ids: set[str], label: str
) -> Mapping[str, float]:
    if set(raw) != expected_ids or not raw:
        raise ValueError(f"{label} must exactly cover selector input row identities")
    values: dict[str, float] = {}
    for row_id, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{label} values must be finite numbers")
        values[row_id] = float(value)
    return MappingProxyType(values)


def _selector_identities(sources: ValidationProgrammeSources) -> tuple[str, str]:
    inner = max(
        sources.inner_selector_scores,
        key=lambda row_id: (sources.inner_selector_scores[row_id], row_id),
    )
    outer = max(
        sources.outer_diagnostic_scores,
        key=lambda row_id: (sources.outer_diagnostic_scores[row_id], row_id),
    )
    return inner, outer


__all__ = [
    "CandidatePrimitiveCase",
    "CostApplicationCase",
    "DevelopmentOutcomeCase",
    "ProgrammePreflightMetadata",
    "SlotLifecycleEvidence",
    "ValidationProgrammeResult",
    "ValidationProgrammeSources",
    "VerifiedDevelopmentEvidence",
    "freeze_development_evidence",
    "run_validation_programme",
]
