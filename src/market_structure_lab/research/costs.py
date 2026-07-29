"""Deterministic Phase 5 spot-cash cost policy contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sized
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Self, cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    ValidationTerminalState,
    ValidationWorkBudget,
    ValidationWorkDemand,
)

_SHA256_LENGTH = 64
_RATE_UNITS = "proportion_of_notional"
_FUNDING_SPOT = "not_applicable_spot_v1"
_FAILED_NOT_EVALUATED = ValidationTerminalState(
    ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
)
_INCONCLUSIVE = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE)


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


def _require_non_empty(value: object, label: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")


def _require_finite_positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    converted = float(value)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted


def _require_non_negative_rate(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    converted = float(value)
    if converted < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return converted


def _require_utc_aware(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


class CostSide(StrEnum):
    """Legal direction for a validation cost application."""

    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is CostSide.LONG else -1


class CostApplication(StrEnum):
    """Which deterministic cost variant is being applied."""

    BASE = "base"
    STRESS = "stress"
    DELAYED = "delayed"


class FundingPolicy(StrEnum):
    """Funding treatment for the current spot-only Phase 5 scope."""

    SPOT_NOT_APPLICABLE = _FUNDING_SPOT


class CostCoverageStatus(StrEnum):
    """Whether event-level cost evidence covers the requested application."""

    COMPLETE = "complete"
    MISSING_EVENT_COVERAGE = "missing_event_coverage"


class CostAdmissionStatus(StrEnum):
    """Cost-specific admission status that avoids scientific-success overclaiming."""

    ADMISSIBLE = "admissible"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CostRate:
    """One non-negative notional-rate component with provenance evidence."""

    rate: float
    units: str
    provenance: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", _require_non_negative_rate(self.rate, "rate"))
        if self.units != _RATE_UNITS:
            raise ValueError(f"units must be {_RATE_UNITS}")
        _require_non_empty(self.provenance, "provenance")
        _require_sha256(self.evidence_sha256, "evidence_sha256")

    @property
    def sha256(self) -> str:
        return hash_json("cost-rate-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "rate": self.rate,
            "units": self.units,
            "provenance": self.provenance,
            "evidence_sha256": self.evidence_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Self:
        return cls(
            rate=cast(float, raw["rate"]),
            units=cast(str, raw["units"]),
            provenance=cast(str, raw["provenance"]),
            evidence_sha256=cast(str, raw["evidence_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class CostEventCoverage:
    """Event-level cost coverage keyed to identity, venue, symbol, timeframe, and UTC interval."""

    event_id: str
    venue: str
    symbol: str
    timeframe: str
    effective_start: datetime
    effective_end: datetime
    evidence_sha256: str

    def __post_init__(self) -> None:
        _require_non_empty(self.event_id, "event_id")
        _require_non_empty(self.venue, "venue")
        _require_non_empty(self.symbol, "symbol")
        _require_non_empty(self.timeframe, "timeframe")
        start = _require_utc_aware(self.effective_start, "effective_start")
        end = _require_utc_aware(self.effective_end, "effective_end")
        if end <= start:
            raise ValueError("effective_end must be after effective_start")
        object.__setattr__(self, "effective_start", start)
        object.__setattr__(self, "effective_end", end)
        _require_sha256(self.evidence_sha256, "event coverage evidence_sha256")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.event_id, self.venue, self.symbol, self.timeframe)

    @property
    def sha256(self) -> str:
        return hash_json("cost-event-coverage-v1", self.to_dict(include_sha=False))

    def covers_timestamp(self, event_timestamp: datetime) -> bool:
        timestamp = _require_utc_aware(event_timestamp, "event_timestamp")
        return self.effective_start <= timestamp < self.effective_end

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": self.event_id,
            "venue": self.venue,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "effective_start": self.effective_start,
            "effective_end": self.effective_end,
            "evidence_sha256": self.evidence_sha256,
        }
        if include_sha:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Self:
        expected = raw.get("sha256")
        payload = {key: value for key, value in raw.items() if key != "sha256"}
        coverage = cls(
            event_id=cast(str, payload["event_id"]),
            venue=cast(str, payload["venue"]),
            symbol=cast(str, payload["symbol"]),
            timeframe=cast(str, payload["timeframe"]),
            effective_start=cast(datetime, payload["effective_start"]),
            effective_end=cast(datetime, payload["effective_end"]),
            evidence_sha256=cast(str, payload["evidence_sha256"]),
        )
        if expected is not None and expected != coverage.sha256:
            raise ValueError("cost event coverage sha256 does not match payload")
        return coverage


@dataclass(frozen=True, slots=True)
class CostEventCoveragePublication:
    """Bounded keyed publication of event-level cost coverage evidence."""

    source_publication_sha256: str
    coverage_count: int
    coverage_digest_sha256: str
    entries: tuple[CostEventCoverage, ...]
    _lookup: Mapping[tuple[str, str, str, str], tuple[CostEventCoverage, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _require_sha256(self.source_publication_sha256, "event coverage source_publication_sha256")
        if isinstance(self.coverage_count, bool) or not isinstance(self.coverage_count, int):
            raise ValueError("coverage_count must be an integer")
        entries = tuple(self.entries)
        if self.coverage_count != len(entries):
            raise ValueError("coverage_count must match event coverage entries")
        if any(not isinstance(item, CostEventCoverage) for item in entries):
            raise TypeError("entries must contain only CostEventCoverage")
        if len({item.sha256 for item in entries}) != len(entries):
            raise ValueError("event coverage entries must be unique")
        object.__setattr__(self, "entries", entries)
        digest = self.recompute_coverage_digest()
        if self.coverage_digest_sha256 != digest:
            raise ValueError("coverage_digest_sha256 does not match event coverage entries")
        _require_sha256(self.coverage_digest_sha256, "coverage_digest_sha256")
        lookup: dict[tuple[str, str, str, str], list[CostEventCoverage]] = {}
        for entry in entries:
            lookup.setdefault(entry.key, []).append(entry)
        object.__setattr__(
            self,
            "_lookup",
            {key: tuple(value) for key, value in lookup.items()},
        )

    @property
    def sha256(self) -> str:
        return hash_json("cost-event-coverage-publication-v1", self.to_dict(include_sha=False))

    def recompute_coverage_digest(self) -> str:
        return hash_json(
            "cost-event-coverage-publication-entries-v1",
            [entry.to_dict(include_sha=False) for entry in self.entries],
        )

    def lookup(
        self,
        *,
        event_id: str,
        venue: str,
        symbol: str,
        timeframe: str,
        event_timestamp: datetime,
    ) -> CostEventCoverage | None:
        entries = self._lookup.get((event_id, venue, symbol, timeframe), ())
        for entry in entries:
            if entry.covers_timestamp(event_timestamp):
                return entry
        return None

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_publication_sha256": self.source_publication_sha256,
            "coverage_count": self.coverage_count,
            "coverage_digest_sha256": self.coverage_digest_sha256,
            "entries": [entry.to_dict() for entry in self.entries],
        }
        if include_sha:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Self:
        expected = raw.get("sha256")
        entries = tuple(
            CostEventCoverage.from_dict(cast(Mapping[str, object], item))
            for item in cast(Iterable[object], raw["entries"])
        )
        publication = cls(
            source_publication_sha256=cast(str, raw["source_publication_sha256"]),
            coverage_count=cast(int, raw["coverage_count"]),
            coverage_digest_sha256=cast(str, raw["coverage_digest_sha256"]),
            entries=entries,
        )
        if expected is not None and expected != publication.sha256:
            raise ValueError("cost event coverage publication sha256 does not match payload")
        return publication


def freeze_cost_event_coverage_publication(
    coverages: object,
    *,
    source_publication_sha256: str,
    budget: ValidationWorkBudget,
    demand: ValidationWorkDemand,
) -> CostEventCoveragePublication:
    """Freeze bounded event coverage without accepting unbounded iterables."""

    if not isinstance(coverages, Sized) or not isinstance(coverages, Iterable):
        raise TypeError("event coverage source must be a Sized iterable")
    budget.preflight(demand, deferred_work=coverages)
    observed = len(coverages)
    if observed != demand.events:
        raise ValueError(
            f"event coverage count declaration mismatch: {observed} != {demand.events}"
        )
    entries = tuple(cast(Iterable[CostEventCoverage], coverages))
    digest = hash_json(
        "cost-event-coverage-publication-entries-v1",
        [entry.to_dict(include_sha=False) for entry in entries],
    )
    return CostEventCoveragePublication(
        source_publication_sha256=source_publication_sha256,
        coverage_count=observed,
        coverage_digest_sha256=digest,
        entries=entries,
    )


@dataclass(frozen=True, slots=True)
class CostEvidence:
    """Bounded source evidence for entry and exit fee/spread/slippage rates."""

    evidence_id: str
    symbol: str
    timeframe: str
    source_publication_sha256: str
    entry_fee: CostRate
    entry_half_spread: CostRate
    entry_slippage: CostRate
    exit_fee: CostRate
    exit_half_spread: CostRate
    exit_slippage: CostRate
    fill_probability: float
    coverage_status: CostCoverageStatus = CostCoverageStatus.COMPLETE
    coverage_reason: str = "complete"
    event_coverage_publication: CostEventCoveragePublication | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.evidence_id, "evidence_id")
        _require_non_empty(self.symbol, "symbol")
        _require_non_empty(self.timeframe, "timeframe")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        for label in (
            "entry_fee",
            "entry_half_spread",
            "entry_slippage",
            "exit_fee",
            "exit_half_spread",
            "exit_slippage",
        ):
            if not isinstance(getattr(self, label), CostRate):
                raise TypeError(f"{label} must be a CostRate")
        if (
            isinstance(self.fill_probability, bool)
            or not isinstance(self.fill_probability, (int, float))
            or not isfinite(self.fill_probability)
            or self.fill_probability <= 0.0
            or self.fill_probability > 1.0
        ):
            raise ValueError("fill_probability must be in (0, 1]")
        object.__setattr__(self, "fill_probability", float(self.fill_probability))
        if not isinstance(self.coverage_status, CostCoverageStatus):
            raise TypeError("coverage_status must be a CostCoverageStatus")
        _require_non_empty(self.coverage_reason, "coverage_reason")
        if self.event_coverage_publication is not None and not isinstance(
            self.event_coverage_publication, CostEventCoveragePublication
        ):
            raise TypeError("event_coverage_publication must be CostEventCoveragePublication")

    @property
    def entry_rate_sum(self) -> float:
        return self.entry_fee.rate + self.entry_half_spread.rate + self.entry_slippage.rate

    @property
    def exit_rate_sum(self) -> float:
        return self.exit_fee.rate + self.exit_half_spread.rate + self.exit_slippage.rate

    @property
    def total_rate_sum(self) -> float:
        return self.entry_rate_sum + self.exit_rate_sum

    @property
    def sha256(self) -> str:
        return hash_json("cost-evidence-v1", self.to_dict(include_sha=False))

    def matching_event_coverage(
        self,
        *,
        event_id: str,
        venue: str,
        symbol: str,
        timeframe: str,
        event_timestamp: datetime,
    ) -> CostEventCoverage | None:
        if self.event_coverage_publication is None:
            return None
        return self.event_coverage_publication.lookup(
            event_id=event_id,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            event_timestamp=event_timestamp,
        )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "evidence_id": self.evidence_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source_publication_sha256": self.source_publication_sha256,
            "entry_fee": self.entry_fee.to_dict(),
            "entry_half_spread": self.entry_half_spread.to_dict(),
            "entry_slippage": self.entry_slippage.to_dict(),
            "exit_fee": self.exit_fee.to_dict(),
            "exit_half_spread": self.exit_half_spread.to_dict(),
            "exit_slippage": self.exit_slippage.to_dict(),
            "fill_probability": self.fill_probability,
            "coverage_status": self.coverage_status.value,
            "coverage_reason": self.coverage_reason,
            "event_coverage_publication": None
            if self.event_coverage_publication is None
            else self.event_coverage_publication.to_dict(),
        }
        if include_sha:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Self:
        expected = raw.get("sha256")
        publication_payload = raw.get("event_coverage_publication")
        publication = (
            None
            if publication_payload is None
            else CostEventCoveragePublication.from_dict(
                cast(Mapping[str, object], publication_payload)
            )
        )
        evidence = cls(
            evidence_id=cast(str, raw["evidence_id"]),
            symbol=cast(str, raw["symbol"]),
            timeframe=cast(str, raw["timeframe"]),
            source_publication_sha256=cast(str, raw["source_publication_sha256"]),
            entry_fee=CostRate.from_dict(cast(Mapping[str, object], raw["entry_fee"])),
            entry_half_spread=CostRate.from_dict(
                cast(Mapping[str, object], raw["entry_half_spread"])
            ),
            entry_slippage=CostRate.from_dict(cast(Mapping[str, object], raw["entry_slippage"])),
            exit_fee=CostRate.from_dict(cast(Mapping[str, object], raw["exit_fee"])),
            exit_half_spread=CostRate.from_dict(
                cast(Mapping[str, object], raw["exit_half_spread"])
            ),
            exit_slippage=CostRate.from_dict(cast(Mapping[str, object], raw["exit_slippage"])),
            fill_probability=cast(float, raw["fill_probability"]),
            coverage_status=CostCoverageStatus(cast(str, raw["coverage_status"])),
            coverage_reason=cast(str, raw["coverage_reason"]),
            event_coverage_publication=publication,
        )
        if expected is not None and expected != evidence.sha256:
            raise ValueError("cost evidence sha256 does not match payload")
        return evidence


@dataclass(frozen=True, slots=True)
class CostPolicyAssessment:
    """Explicit terminal-state assessment for cost preflight/admission."""

    admission_status: CostAdmissionStatus
    reason: str
    terminal_state: ValidationTerminalState | None = None
    coverage_status: CostCoverageStatus = CostCoverageStatus.COMPLETE
    cost_policy_sha256: str | None = None
    event_coverage_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.admission_status, CostAdmissionStatus):
            raise TypeError("admission_status must be CostAdmissionStatus")
        if self.terminal_state is not None and not isinstance(
            self.terminal_state, ValidationTerminalState
        ):
            raise TypeError("terminal_state must be ValidationTerminalState or None")
        if not isinstance(self.coverage_status, CostCoverageStatus):
            raise TypeError("coverage_status must be CostCoverageStatus")
        _require_non_empty(self.reason, "reason")
        if self.cost_policy_sha256 is not None:
            _require_sha256(self.cost_policy_sha256, "cost_policy_sha256")
        if self.event_coverage_sha256 is not None:
            _require_sha256(self.event_coverage_sha256, "event_coverage_sha256")


@dataclass(frozen=True, slots=True)
class CostPolicy:
    """Frozen spot-cash cost policy for one validation programme."""

    policy_id: str
    base_policy_sha256: str
    evidence: CostEvidence
    funding: FundingPolicy = FundingPolicy.SPOT_NOT_APPLICABLE
    funding_rows_sha256: str | None = None
    stress_multiplier: float = 2.0
    stress_fill_probability_power: int = 2
    delay_model_sha256: str | None = None
    max_applications: int = 1_104

    def __post_init__(self) -> None:
        _require_non_empty(self.policy_id, "policy_id")
        _require_sha256(self.base_policy_sha256, "base_policy_sha256")
        if not isinstance(self.evidence, CostEvidence):
            raise TypeError("evidence must be CostEvidence")
        if self.funding is not FundingPolicy.SPOT_NOT_APPLICABLE:
            raise ValueError("funding must be not_applicable_spot_v1 for spot-cash validation")
        if self.funding_rows_sha256 is not None:
            raise ValueError("funding rows are rejected for spot-cash validation")
        object.__setattr__(
            self,
            "stress_multiplier",
            _require_finite_positive(self.stress_multiplier, "stress_multiplier"),
        )
        if (
            isinstance(self.stress_fill_probability_power, bool)
            or not isinstance(self.stress_fill_probability_power, int)
            or self.stress_fill_probability_power < 1
        ):
            raise ValueError("stress_fill_probability_power must be a positive integer")
        if self.delay_model_sha256 is not None:
            _require_sha256(self.delay_model_sha256, "delay_model_sha256")
        if (
            isinstance(self.max_applications, bool)
            or not isinstance(self.max_applications, int)
            or self.max_applications < 0
        ):
            raise ValueError("max_applications must be a non-negative integer")

    @property
    def sha256(self) -> str:
        return hash_json("cost-policy-v1", self.to_dict(include_sha=False))

    def require_application_budget(self, applications: int) -> None:
        if isinstance(applications, bool) or not isinstance(applications, int) or applications < 0:
            raise ValueError("applications must be a non-negative integer")
        if applications > self.max_applications:
            raise ValueError(
                f"applications exceeds cost application budget {self.max_applications}: {applications}"
            )

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "policy_id": self.policy_id,
            "base_policy_sha256": self.base_policy_sha256,
            "funding": self.funding.value,
            "funding_rows_sha256": self.funding_rows_sha256,
            "evidence": self.evidence.to_dict(),
            "stress_multiplier": self.stress_multiplier,
            "stress_fill_probability_power": self.stress_fill_probability_power,
            "delay_model_sha256": self.delay_model_sha256,
            "max_applications": self.max_applications,
        }
        if include_sha:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Self:
        expected = raw.get("sha256")
        policy = cls(
            policy_id=cast(str, raw["policy_id"]),
            base_policy_sha256=cast(str, raw["base_policy_sha256"]),
            funding=FundingPolicy(cast(str, raw["funding"])),
            funding_rows_sha256=cast(str | None, raw["funding_rows_sha256"]),
            evidence=CostEvidence.from_dict(cast(Mapping[str, object], raw["evidence"])),
            stress_multiplier=cast(float, raw["stress_multiplier"]),
            stress_fill_probability_power=cast(int, raw["stress_fill_probability_power"]),
            delay_model_sha256=cast(str | None, raw["delay_model_sha256"]),
            max_applications=cast(int, raw["max_applications"]),
        )
        if expected is not None and expected != policy.sha256:
            raise ValueError("cost policy sha256 does not match payload")
        return policy


@dataclass(frozen=True, slots=True)
class TradeCostResult:
    """Deterministic net-return decomposition for one trade after costs."""

    application: CostApplication
    filled: bool
    gross_return: float
    entry_cost: float
    exit_cost: float
    funding_cost: float
    conditional_filled_net_return: float
    fill_probability: float
    expected_net_return: float
    missed_fill_return: float
    cost_policy_sha256: str
    evidence_sha256: str
    event_coverage_sha256: str
    delay_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.application, CostApplication):
            raise TypeError("application must be CostApplication")
        if not isinstance(self.filled, bool):
            raise TypeError("filled must be bool")
        for label in (
            "gross_return",
            "entry_cost",
            "exit_cost",
            "funding_cost",
            "conditional_filled_net_return",
            "fill_probability",
            "expected_net_return",
            "missed_fill_return",
        ):
            value = getattr(self, label)
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{label} must be finite")
            object.__setattr__(self, label, float(value))
        _require_sha256(self.cost_policy_sha256, "cost_policy_sha256")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        _require_sha256(self.event_coverage_sha256, "event_coverage_sha256")
        if self.delay_evidence_sha256 is not None:
            _require_sha256(self.delay_evidence_sha256, "delay_evidence_sha256")

    @classmethod
    def missed(
        cls,
        *,
        cost_policy_sha256: str,
        evidence_sha256: str,
        event_coverage_sha256: str,
    ) -> Self:
        return cls(
            application=CostApplication.BASE,
            filled=False,
            gross_return=0.0,
            entry_cost=0.0,
            exit_cost=0.0,
            funding_cost=0.0,
            conditional_filled_net_return=0.0,
            fill_probability=0.0,
            expected_net_return=0.0,
            missed_fill_return=0.0,
            cost_policy_sha256=cost_policy_sha256,
            evidence_sha256=evidence_sha256,
            event_coverage_sha256=event_coverage_sha256,
        )


def assess_cost_policy(policy: object) -> CostPolicyAssessment:
    """Return explicit admission evidence for policy-level cost preflight."""

    if not isinstance(policy, CostPolicy):
        return CostPolicyAssessment(
            CostAdmissionStatus.FAILED,
            "invalid cost policy",
            terminal_state=_FAILED_NOT_EVALUATED,
            coverage_status=CostCoverageStatus.MISSING_EVENT_COVERAGE,
        )
    if policy.evidence.total_rate_sum == 0.0:
        return CostPolicyAssessment(
            CostAdmissionStatus.FAILED,
            "all configured aggregate cost rates are zero",
            terminal_state=_FAILED_NOT_EVALUATED,
            coverage_status=policy.evidence.coverage_status,
            cost_policy_sha256=policy.sha256,
        )
    if (
        policy.evidence.coverage_status is CostCoverageStatus.MISSING_EVENT_COVERAGE
        or policy.evidence.event_coverage_publication is None
    ):
        return CostPolicyAssessment(
            CostAdmissionStatus.INCONCLUSIVE,
            "missing event cost coverage",
            terminal_state=_INCONCLUSIVE,
            coverage_status=CostCoverageStatus.MISSING_EVENT_COVERAGE,
            cost_policy_sha256=policy.sha256,
        )
    return CostPolicyAssessment(
        CostAdmissionStatus.ADMISSIBLE,
        "cost policy complete",
        terminal_state=None,
        coverage_status=CostCoverageStatus.COMPLETE,
        cost_policy_sha256=policy.sha256,
    )


def assess_cost_application(
    policy: object,
    *,
    event_id: str,
    event_timestamp: datetime,
    venue: str,
    symbol: str,
    timeframe: str,
) -> CostPolicyAssessment:
    """Assess whether one event application has exact cost coverage."""

    policy_assessment = assess_cost_policy(policy)
    if not isinstance(policy, CostPolicy):
        return policy_assessment
    if policy_assessment.admission_status is not CostAdmissionStatus.ADMISSIBLE:
        return policy_assessment
    coverage = policy.evidence.matching_event_coverage(
        event_id=event_id,
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        event_timestamp=event_timestamp,
    )
    if coverage is None:
        return CostPolicyAssessment(
            CostAdmissionStatus.INCONCLUSIVE,
            "missing event cost coverage for bound event",
            terminal_state=_INCONCLUSIVE,
            coverage_status=CostCoverageStatus.MISSING_EVENT_COVERAGE,
            cost_policy_sha256=policy.sha256,
        )
    return CostPolicyAssessment(
        CostAdmissionStatus.ADMISSIBLE,
        "event cost coverage complete",
        terminal_state=None,
        coverage_status=CostCoverageStatus.COMPLETE,
        cost_policy_sha256=policy.sha256,
        event_coverage_sha256=coverage.sha256,
    )


def verify_cost_policy(policy: CostPolicy) -> CostCoverageStatus:
    """Fail closed on invalid policies and return explicit coverage status for valid ones."""

    assessment = assess_cost_policy(policy)
    if assessment.admission_status is CostAdmissionStatus.FAILED:
        raise ValueError(assessment.reason)
    return assessment.coverage_status


def apply_cost_policy(
    policy: CostPolicy,
    *,
    side: CostSide,
    entry_price: float,
    exit_price: float,
    event_id: str,
    event_timestamp: datetime,
    venue: str,
    symbol: str,
    timeframe: str,
    application: CostApplication = CostApplication.BASE,
    filled: bool = True,
    delay_evidence_sha256: str | None = None,
) -> TradeCostResult:
    """Apply deterministic spot-cash costs to one already legal validation path."""

    assessment = assess_cost_application(
        policy,
        event_id=event_id,
        event_timestamp=event_timestamp,
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
    )
    if assessment.admission_status is CostAdmissionStatus.FAILED:
        raise ValueError(assessment.reason)
    if assessment.admission_status is CostAdmissionStatus.INCONCLUSIVE:
        raise ValueError(f"event cost coverage is inconclusive: {assessment.reason}")
    if assessment.event_coverage_sha256 is None:
        raise ValueError("event cost coverage is missing")
    if not isinstance(side, CostSide):
        raise ValueError("side must be CostSide")
    if not isinstance(application, CostApplication):
        raise TypeError("application must be CostApplication")
    if not isinstance(filled, bool):
        raise TypeError("filled must be bool")
    if not filled:
        return TradeCostResult.missed(
            cost_policy_sha256=policy.sha256,
            evidence_sha256=policy.evidence.sha256,
            event_coverage_sha256=assessment.event_coverage_sha256,
        )
    entry = _require_finite_positive(entry_price, "entry_price")
    exit_ = _require_finite_positive(exit_price, "exit_price")
    if application is CostApplication.DELAYED:
        _require_sha256(delay_evidence_sha256, "delay_evidence_sha256")
    elif delay_evidence_sha256 is not None:
        raise ValueError("delay_evidence_sha256 is only valid for delayed applications")

    multiplier = policy.stress_multiplier if application is CostApplication.STRESS else 1.0
    fill_probability = policy.evidence.fill_probability
    if application is CostApplication.STRESS:
        fill_probability = fill_probability**policy.stress_fill_probability_power
    ratio = exit_ / entry
    gross = float(side.sign) * (ratio - 1.0)
    entry_cost = multiplier * policy.evidence.entry_rate_sum
    exit_cost = ratio * multiplier * policy.evidence.exit_rate_sum
    funding_cost = 0.0
    conditional = gross - entry_cost - exit_cost - funding_cost
    return TradeCostResult(
        application=application,
        filled=True,
        gross_return=gross,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        funding_cost=funding_cost,
        conditional_filled_net_return=conditional,
        fill_probability=fill_probability,
        expected_net_return=fill_probability * conditional,
        missed_fill_return=0.0,
        cost_policy_sha256=policy.sha256,
        evidence_sha256=policy.evidence.sha256,
        event_coverage_sha256=assessment.event_coverage_sha256,
        delay_evidence_sha256=delay_evidence_sha256,
    )


def _buffer_declared_trades(
    trades: Iterable[Mapping[str, object]], demand: ValidationWorkDemand
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(trades, Sized):
        raise TypeError("cost batch trades must be Sized or otherwise hard-bounded")
    declared_len = len(trades)
    if declared_len != demand.outcomes:
        raise ValueError(f"cost batch declaration mismatch: {declared_len} != {demand.outcomes}")
    buffer: list[Mapping[str, object]] = []
    for index, trade in enumerate(trades):
        if index >= demand.outcomes:
            raise ValueError(
                "cost batch underdeclared rows: iterable produced more rows than demand"
            )
        buffer.append(trade)
    if len(buffer) != demand.outcomes:
        raise ValueError(f"cost batch declaration mismatch: {len(buffer)} != {demand.outcomes}")
    return tuple(buffer)


def apply_cost_batch(
    policy: CostPolicy,
    trades: Iterable[Mapping[str, object]],
    *,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
    allocation: Callable[[], object] | None = None,
) -> tuple[TradeCostResult, ...]:
    """Preflight budget declarations before iterating, allocating, or applying costs."""

    budget.preflight(demand, deferred_work=trades, allocation=allocation)
    policy.require_application_budget(demand.outcomes)
    buffered = _buffer_declared_trades(trades, demand)
    if allocation is not None:
        _ = allocation()
    results: list[TradeCostResult] = []
    for trade in buffered:
        results.append(
            apply_cost_policy(
                policy,
                side=cast(CostSide, trade["side"]),
                entry_price=cast(float, trade["entry_price"]),
                exit_price=cast(float, trade["exit_price"]),
                event_id=cast(str, trade["event_id"]),
                event_timestamp=cast(datetime, trade["event_timestamp"]),
                venue=cast(str, trade["venue"]),
                symbol=cast(str, trade["symbol"]),
                timeframe=cast(str, trade["timeframe"]),
                application=cast(CostApplication, trade.get("application", CostApplication.BASE)),
                filled=cast(bool, trade.get("filled", True)),
                delay_evidence_sha256=cast(str | None, trade.get("delay_evidence_sha256")),
            )
        )
    return tuple(results)


__all__ = [
    "CostAdmissionStatus",
    "CostApplication",
    "CostCoverageStatus",
    "CostEvidence",
    "CostEventCoverage",
    "CostEventCoveragePublication",
    "CostPolicy",
    "CostPolicyAssessment",
    "CostRate",
    "CostSide",
    "FundingPolicy",
    "TradeCostResult",
    "apply_cost_batch",
    "apply_cost_policy",
    "assess_cost_application",
    "assess_cost_policy",
    "freeze_cost_event_coverage_publication",
    "verify_cost_policy",
]
