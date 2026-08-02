"""Immutable policy-rooted Phase 5 cost authority and consumption boundary.

The historical V2 completeness authority remains unchanged.  This successor
authorizes only the conservative, preregistered model permitted by the frozen
Task 15 contract; it does not claim actual execution evidence.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
from math import isfinite
import os
from pathlib import Path
import re
from typing import Any, ClassVar, cast

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.costs import (
    CostApplication,
    CostCoverageStatus,
    CostEvidence,
    CostPolicy,
    CostSide,
    FundingPolicy,
    TradeCostResult,
    apply_cost_policy,
)
from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    ValidationTerminalState,
)
from market_structure_lab.research.validation_v2_models import publication_json_bytes

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FACTORY = object()
_FAILED = ValidationTerminalState(ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED)
_INCONCLUSIVE = ValidationTerminalState(ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE)
_MAX_PROVENANCE_BYTES = 4 * 1024 * 1024


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


class CostPolicyProvenanceKindV3(StrEnum):
    """Permitted non-outcome sources for conservative cost parameters."""

    EXTERNAL_EVIDENCE = "external_evidence"
    PREREGISTERED_POLICY = "preregistered_policy"


@dataclass(frozen=True, slots=True)
class CostPolicyProvenanceIdentityV3:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.startswith("CPPV3-"):
            raise ValueError("cost policy provenance identity is invalid")
        _require_sha256(self.value.removeprefix("CPPV3-"), "cost policy provenance digest")

    @classmethod
    def from_payload(cls, payload: object) -> CostPolicyProvenanceIdentityV3:
        return cls(f"CPPV3-{hash_json('cost-policy-provenance-publication-v3', payload)}")


@dataclass(frozen=True, slots=True)
class CostPolicyProvenanceV3:
    """Factory-issued capability binding reviewed preregistration to exact policy bytes."""

    kind: CostPolicyProvenanceKindV3
    programme_sha256: str
    preregistration_sha256: str
    review_sha256: str
    prior_freeze_evidence_sha256: str
    cost_policy_sha256: str
    outcome_access_record_count: int
    identity: CostPolicyProvenanceIdentityV3
    publication_root: Path
    canonical_bytes: bytes
    canonical_sha256: str
    _factory_token: InitVar[object]

    def __post_init__(self, _factory_token: object) -> None:
        if _factory_token is not _FACTORY:
            raise TypeError("cost policy provenance must be created by its factory")
        if not isinstance(self.kind, CostPolicyProvenanceKindV3):
            raise TypeError("kind must be CostPolicyProvenanceKindV3")
        _require_sha256(self.programme_sha256, "programme_sha256")
        _require_sha256(self.preregistration_sha256, "preregistration_sha256")
        _require_sha256(self.review_sha256, "review_sha256")
        _require_sha256(self.prior_freeze_evidence_sha256, "prior_freeze_evidence_sha256")
        _require_sha256(self.cost_policy_sha256, "cost_policy_sha256")
        _require_sha256(self.canonical_sha256, "canonical_sha256")
        if not isinstance(self.identity, CostPolicyProvenanceIdentityV3):
            raise TypeError("identity must be CostPolicyProvenanceIdentityV3")
        if (
            type(self.publication_root) is not type(Path())
            or type(self.canonical_bytes) is not bytes
        ):
            raise TypeError("provenance publication root and canonical bytes must be exact types")
        if (
            isinstance(self.outcome_access_record_count, bool)
            or not isinstance(self.outcome_access_record_count, int)
            or self.outcome_access_record_count != 0
        ):
            raise ValueError("outcome_access_record_count must be exactly zero")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "programme_sha256": self.programme_sha256,
            "preregistration_sha256": self.preregistration_sha256,
            "review_sha256": self.review_sha256,
            "prior_freeze_evidence_sha256": self.prior_freeze_evidence_sha256,
            "cost_policy_sha256": self.cost_policy_sha256,
            "outcome_access_record_count": self.outcome_access_record_count,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity_payload(),
            "identity": self.identity.value,
            "canonical_sha256": self.canonical_sha256,
        }


@dataclass(frozen=True, slots=True)
class CostPolicyRequestV3:
    """One exact event that must be covered by the sealed policy."""

    event_id: str
    event_timestamp: datetime
    venue: str
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.event_id, "event_id"),
            (self.venue, "venue"),
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
        ):
            _require_text(value, label)
        if (
            not isinstance(self.event_timestamp, datetime)
            or self.event_timestamp.tzinfo is None
            or self.event_timestamp.utcoffset() is None
        ):
            raise ValueError("event_timestamp must be timezone-aware")
        object.__setattr__(self, "event_timestamp", self.event_timestamp.astimezone(UTC))

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.event_id, self.venue, self.symbol, self.timeframe

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_timestamp": self.event_timestamp,
            "venue": self.venue,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }


@dataclass(frozen=True, slots=True)
class CostPolicyAuthorityIdentityV3:
    """Typed identity for the additive successor cost authority."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.startswith("CPAV3-"):
            raise ValueError("cost policy authority identity is invalid")
        _require_sha256(self.value.removeprefix("CPAV3-"), "cost policy authority digest")

    @classmethod
    def from_payload(cls, payload: object) -> CostPolicyAuthorityIdentityV3:
        return cls(f"CPAV3-{hash_json('cost-policy-authority-v3', payload)}")


@dataclass(frozen=True, slots=True)
class VerifiedCostPolicyAuthorityV3:
    """Factory-sealed immutable authority for one exact required-event population."""

    identity: CostPolicyAuthorityIdentityV3
    policy: CostPolicy | None
    provenance: CostPolicyProvenanceV3 | None
    required_events: tuple[CostPolicyRequestV3, ...]
    terminal_state: ValidationTerminalState | None
    reason: str
    cost_policy_sha256: str | None
    event_coverage_sha256: str | None
    canonical_sha256: str
    _factory_token: InitVar[object]
    _schema: ClassVar[str] = "verified-cost-policy-authority-v3"

    def __post_init__(self, _factory_token: object) -> None:
        if _factory_token is not _FACTORY:
            raise TypeError("cost policy authorities must be created by the successor publisher")
        if not isinstance(self.identity, CostPolicyAuthorityIdentityV3):
            raise TypeError("identity must be CostPolicyAuthorityIdentityV3")
        if self.policy is not None and type(self.policy) is not CostPolicy:
            raise TypeError("policy must be the exact CostPolicy type or None")
        if self.provenance is not None and type(self.provenance) is not CostPolicyProvenanceV3:
            raise TypeError("provenance must be the exact CostPolicyProvenanceV3 type or None")
        if type(self.required_events) is not tuple or any(
            type(item) is not CostPolicyRequestV3 for item in self.required_events
        ):
            raise TypeError("required_events must be an exact tuple of CostPolicyRequestV3")
        if (
            self.terminal_state is not None
            and type(self.terminal_state) is not ValidationTerminalState
        ):
            raise TypeError("terminal_state must be the exact ValidationTerminalState type or None")
        _require_text(self.reason, "reason")
        if self.cost_policy_sha256 is not None:
            _require_sha256(self.cost_policy_sha256, "cost_policy_sha256")
        if self.event_coverage_sha256 is not None:
            _require_sha256(self.event_coverage_sha256, "event_coverage_sha256")
        _require_sha256(self.canonical_sha256, "canonical_sha256")

    @property
    def required_event_count(self) -> int:
        return len(self.required_events)

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self._schema,
            "policy": None if self.policy is None else self.policy.to_dict(),
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
            "required_events": [item.to_dict() for item in self.required_events],
            "terminal_state": None
            if self.terminal_state is None
            else {
                "execution_status": self.terminal_state.execution_status.value,
                "scientific_decision": self.terminal_state.decision.value,
            },
            "reason": self.reason,
            "cost_policy_sha256": self.cost_policy_sha256,
            "event_coverage_sha256": self.event_coverage_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity_payload(),
            "identity": self.identity.value,
            "canonical_sha256": self.canonical_sha256,
        }


_REGISTERED: dict[int, tuple[VerifiedCostPolicyAuthorityV3, str]] = {}
_REGISTERED_PROVENANCE: dict[int, tuple[CostPolicyProvenanceV3, str]] = {}


def _snapshot_policy(policy: object) -> CostPolicy | None:
    if type(policy) is not CostPolicy:
        return None
    try:
        return CostPolicy.from_dict(policy.to_dict())
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _published_policy(policy: CostPolicy) -> dict[str, object]:
    payload = policy.to_dict()
    evidence = cast(dict[str, Any], payload["evidence"])
    publication = cast(dict[str, Any] | None, evidence["event_coverage_publication"])
    if publication is not None:
        for entry in cast(list[dict[str, Any]], publication["entries"]):
            entry["effective_start"] = cast(datetime, entry["effective_start"]).isoformat()
            entry["effective_end"] = cast(datetime, entry["effective_end"]).isoformat()
    return payload


def _loaded_policy(payload: object) -> CostPolicy:
    raw = cast(dict[str, Any], payload)
    evidence = cast(dict[str, Any], raw["evidence"])
    publication = cast(dict[str, Any] | None, evidence["event_coverage_publication"])
    if publication is not None:
        for entry in cast(list[dict[str, Any]], publication["entries"]):
            entry["effective_start"] = datetime.fromisoformat(entry["effective_start"])
            entry["effective_end"] = datetime.fromisoformat(entry["effective_end"])
    return CostPolicy.from_dict(raw)


def publish_cost_policy_provenance_v3(
    *,
    policy: CostPolicy,
    kind: CostPolicyProvenanceKindV3,
    programme_sha256: str,
    preregistration_sha256: str,
    review_sha256: str,
    prior_freeze_evidence_sha256: str,
    output_root: Path,
    outcome_access_record_count: int = 0,
) -> CostPolicyProvenanceIdentityV3:
    """Persist canonical preregistration bytes for workflow-enforced prior freeze.

    Git/remote chronology remains a workflow gate; this publication makes the
    exact policy and evidence identities durable and independently reloadable.
    """

    snapshot = _snapshot_policy(policy)
    if snapshot is None:
        raise ValueError("cannot bind invalid cost policy bytes")
    if not isinstance(kind, CostPolicyProvenanceKindV3):
        raise TypeError("kind must be CostPolicyProvenanceKindV3")
    if preregistration_sha256 != snapshot.base_policy_sha256:
        raise ValueError("preregistration identity must match the cost policy base identity")
    if outcome_access_record_count != 0:
        raise ValueError("outcome_access_record_count must be exactly zero")
    identity_payload = {
        "schema_version": "cost-policy-provenance-publication-v3",
        "kind": kind.value,
        "programme_sha256": _require_sha256(programme_sha256, "programme_sha256"),
        "preregistration_sha256": _require_sha256(preregistration_sha256, "preregistration_sha256"),
        "review_sha256": _require_sha256(review_sha256, "review_sha256"),
        "prior_freeze_evidence_sha256": _require_sha256(
            prior_freeze_evidence_sha256, "prior_freeze_evidence_sha256"
        ),
        "cost_policy_sha256": snapshot.sha256,
        "cost_policy": _published_policy(snapshot),
        "outcome_access_record_count": 0,
    }
    identity = CostPolicyProvenanceIdentityV3.from_payload(identity_payload)
    content = publication_json_bytes({**identity_payload, "identity": identity.value})
    root = Path(output_root)
    require_regular_directory(root.parent)
    if path_exists_no_follow(root):
        raise FileExistsError(f"refusing existing cost provenance publication: {root}")
    os.mkdir(root, mode=0o700)
    try:
        (root / "publication.json").write_bytes(content)
        (root / "_SUCCESS").write_text(f"{identity.value}\n", encoding="ascii")
    except Exception:
        for child in root.iterdir():
            child.unlink()
        root.rmdir()
        raise
    return identity


def load_cost_policy_provenance_v3(
    *,
    publication_root: Path,
    expected_identity: CostPolicyProvenanceIdentityV3,
    expected_programme_sha256: str,
) -> CostPolicyProvenanceV3:
    """Load only canonical bytes matching externally frozen identities."""

    root = Path(publication_root)
    content = read_bounded_regular(root / "publication.json", _MAX_PROVENANCE_BYTES)
    payload = json.loads(content)
    if not isinstance(payload, dict) or publication_json_bytes(payload) != content:
        raise ValueError("cost policy provenance publication is not canonical")
    identity_value = payload.pop("identity", None)
    derived = CostPolicyProvenanceIdentityV3.from_payload(payload)
    if identity_value != expected_identity.value or derived != expected_identity:
        raise ValueError("cost policy provenance differs from expected frozen identity")
    if payload.get("programme_sha256") != _require_sha256(
        expected_programme_sha256, "expected_programme_sha256"
    ):
        raise ValueError("cost policy provenance programme identity differs")
    if payload.get("outcome_access_record_count") != 0:
        raise ValueError("cost policy provenance contains outcome access")
    policy = _loaded_policy(payload["cost_policy"])
    if payload.get("cost_policy_sha256") != policy.sha256:
        raise ValueError("cost policy provenance policy bytes differ")
    canonical_sha256 = hash_json(
        "cost-policy-provenance-v3",
        {key: payload[key] for key in payload if key != "cost_policy" and key != "schema_version"},
    )
    provenance = CostPolicyProvenanceV3(
        kind=CostPolicyProvenanceKindV3(payload["kind"]),
        programme_sha256=payload["programme_sha256"],
        preregistration_sha256=payload["preregistration_sha256"],
        review_sha256=payload["review_sha256"],
        prior_freeze_evidence_sha256=payload["prior_freeze_evidence_sha256"],
        cost_policy_sha256=payload["cost_policy_sha256"],
        outcome_access_record_count=0,
        identity=expected_identity,
        publication_root=root,
        canonical_bytes=content,
        canonical_sha256=canonical_sha256,
        _factory_token=_FACTORY,
    )
    _REGISTERED_PROVENANCE[id(provenance)] = (provenance, canonical_sha256)
    return verify_cost_policy_provenance_v3(provenance, policy=policy)


def verify_cost_policy_provenance_v3(
    provenance: CostPolicyProvenanceV3,
    *,
    policy: CostPolicy,
) -> CostPolicyProvenanceV3:
    """Require the registered original capability and its exact policy snapshot."""

    if type(provenance) is not CostPolicyProvenanceV3:
        raise TypeError("cost policy provenance must be the exact factory-issued type")
    registered = _REGISTERED_PROVENANCE.get(id(provenance))
    if registered is None or registered[0] is not provenance:
        raise ValueError("cost policy provenance is not the registered original")
    snapshot = _snapshot_policy(policy)
    if snapshot is None:
        raise ValueError("cost policy provenance received invalid policy bytes")
    content = read_bounded_regular(
        provenance.publication_root / "publication.json", _MAX_PROVENANCE_BYTES
    )
    publication_payload = json.loads(content)
    if (
        not isinstance(publication_payload, dict)
        or publication_json_bytes(publication_payload) != content
    ):
        raise ValueError("cost policy provenance publication changed or is not canonical")
    publication_identity = publication_payload.pop("identity", None)
    derived_identity = CostPolicyProvenanceIdentityV3.from_payload(publication_payload)
    canonical_sha256 = hash_json("cost-policy-provenance-v3", provenance._identity_payload())
    if (
        provenance.canonical_sha256 != canonical_sha256
        or registered[1] != canonical_sha256
        or provenance.cost_policy_sha256 != snapshot.sha256
        or provenance.preregistration_sha256 != snapshot.base_policy_sha256
        or provenance.outcome_access_record_count != 0
        or publication_identity != provenance.identity.value
        or derived_identity != provenance.identity
        or any(
            publication_payload.get(key) != value
            for key, value in provenance._identity_payload().items()
        )
        or provenance.canonical_bytes != content
        or set(bounded_regular_files(provenance.publication_root, maximum=4))
        != {"publication.json", "_SUCCESS"}
        or read_bounded_regular(provenance.publication_root / "_SUCCESS", 128)
        != f"{provenance.identity.value}\n".encode("ascii")
    ):
        raise ValueError(
            "cost policy provenance identity, policy bytes, or freeze evidence changed"
        )
    return provenance


def _verified_provenance(
    provenance: object,
    policy: CostPolicy | None,
) -> CostPolicyProvenanceV3 | None:
    if policy is None or type(provenance) is not CostPolicyProvenanceV3:
        return None
    try:
        return verify_cost_policy_provenance_v3(provenance, policy=policy)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _classify(
    policy: CostPolicy | None,
    provenance: CostPolicyProvenanceV3 | None,
    required_events: tuple[CostPolicyRequestV3, ...],
) -> tuple[ValidationTerminalState | None, str, str | None, str | None]:
    if policy is None or provenance is None:
        return _FAILED, "missing or invalid preregistered cost policy", None, None
    if (
        type(policy.evidence) is not CostEvidence
        or policy.funding is not FundingPolicy.SPOT_NOT_APPLICABLE
    ):
        return _FAILED, "cost policy has invalid nested evidence or funding", None, None
    if not isfinite(policy.evidence.total_rate_sum) or policy.evidence.total_rate_sum <= 0.0:
        return (
            _FAILED,
            "configured aggregate cost must be positive and finite",
            policy.sha256,
            None,
        )
    if (
        policy.stress_multiplier != 2.0
        or policy.stress_fill_probability_power != 2
        or policy.delay_model_sha256 is None
    ):
        return (
            _FAILED,
            "cost policy must freeze doubled rates, squared fill, and a delay identity",
            policy.sha256,
            None,
        )
    if len({item.key for item in required_events}) != len(required_events):
        return _FAILED, "required cost event identities must be unique", policy.sha256, None
    if len(required_events) > policy.max_applications:
        return _FAILED, "required event population exceeds cost policy budget", policy.sha256, None
    publication = policy.evidence.event_coverage_publication
    if (
        policy.evidence.coverage_status is not CostCoverageStatus.COMPLETE
        or publication is None
        or publication.source_publication_sha256 != policy.evidence.source_publication_sha256
    ):
        return _INCONCLUSIVE, "exact event cost coverage is missing", policy.sha256, None
    matched = []
    for request in required_events:
        rows = tuple(
            item
            for item in publication.entries
            if item.key == request.key and item.covers_timestamp(request.event_timestamp)
        )
        if len(rows) != 1:
            return (
                _INCONCLUSIVE,
                "exact event cost coverage is missing or ambiguous",
                policy.sha256,
                publication.sha256,
            )
        matched.append(rows[0].sha256)
    if len(publication.entries) != len(required_events) or len(set(matched)) != len(matched):
        return (
            _INCONCLUSIVE,
            "event cost coverage contains missing or extra rows",
            policy.sha256,
            publication.sha256,
        )
    return (
        None,
        "cost policy and exact event coverage are admissible",
        policy.sha256,
        publication.sha256,
    )


def seal_cost_policy_authority_v3(
    *,
    policy: object,
    provenance: object,
    required_events: tuple[CostPolicyRequestV3, ...],
) -> VerifiedCostPolicyAuthorityV3:
    """Seal a new authority without reading outcomes or requiring capacity evidence."""

    if type(required_events) is not tuple or any(
        type(item) is not CostPolicyRequestV3 for item in required_events
    ):
        raise TypeError("required_events must be an exact tuple of CostPolicyRequestV3")
    typed_policy = _snapshot_policy(policy)
    typed_provenance = _verified_provenance(provenance, typed_policy)
    terminal, reason, policy_sha, coverage_sha = _classify(
        typed_policy, typed_provenance, required_events
    )
    payload = {
        "schema_version": VerifiedCostPolicyAuthorityV3._schema,
        "policy": None if typed_policy is None else typed_policy.to_dict(),
        "provenance": None if typed_provenance is None else typed_provenance.to_dict(),
        "required_events": [item.to_dict() for item in required_events],
        "terminal_state": None
        if terminal is None
        else {
            "execution_status": terminal.execution_status.value,
            "scientific_decision": terminal.decision.value,
        },
        "reason": reason,
        "cost_policy_sha256": policy_sha,
        "event_coverage_sha256": coverage_sha,
    }
    identity = CostPolicyAuthorityIdentityV3.from_payload(payload)
    canonical_sha256 = hash_json(
        "verified-cost-policy-authority-v3-canonical", {**payload, "identity": identity.value}
    )
    authority = VerifiedCostPolicyAuthorityV3(
        identity=identity,
        policy=typed_policy,
        provenance=typed_provenance,
        required_events=required_events,
        terminal_state=terminal,
        reason=reason,
        cost_policy_sha256=policy_sha,
        event_coverage_sha256=coverage_sha,
        canonical_sha256=canonical_sha256,
        _factory_token=_FACTORY,
    )
    _REGISTERED[id(authority)] = (authority, canonical_sha256)
    return verify_cost_policy_authority_v3(authority)


def verify_cost_policy_authority_v3(
    authority: VerifiedCostPolicyAuthorityV3,
) -> VerifiedCostPolicyAuthorityV3:
    """Revalidate the factory-issued original and every identity-changing field."""

    if type(authority) is not VerifiedCostPolicyAuthorityV3:
        raise TypeError("cost policy authority must be the exact successor type")
    registered = _REGISTERED.get(id(authority))
    if registered is None or registered[0] is not authority:
        raise ValueError("cost policy authority is not the registered original")
    if authority.policy is not None and authority.provenance is not None:
        verify_cost_policy_provenance_v3(authority.provenance, policy=authority.policy)
    payload = authority._identity_payload()
    identity = CostPolicyAuthorityIdentityV3.from_payload(payload)
    canonical_sha256 = hash_json(
        "verified-cost-policy-authority-v3-canonical", {**payload, "identity": identity.value}
    )
    if (
        authority.identity != identity
        or authority.canonical_sha256 != canonical_sha256
        or registered[1] != canonical_sha256
    ):
        raise ValueError("cost policy authority original identity or fields changed")
    return authority


def apply_verified_cost_policy_v3(
    authority: VerifiedCostPolicyAuthorityV3,
    *,
    request: CostPolicyRequestV3,
    side: CostSide,
    entry_price: float,
    exit_price: float,
    application: CostApplication = CostApplication.BASE,
    filled: bool = True,
    delay_evidence_sha256: str | None = None,
) -> TradeCostResult:
    """Consume only an admissible original authority through accepted cost primitives."""

    verify_cost_policy_authority_v3(authority)
    if authority.terminal_state is not None or authority.policy is None:
        raise ValueError(f"cost policy authority is not admissible: {authority.reason}")
    if type(request) is not CostPolicyRequestV3 or request not in authority.required_events:
        raise ValueError("cost request is outside the authority event population")
    if application is CostApplication.DELAYED and (
        authority.policy.delay_model_sha256 is None
        or delay_evidence_sha256 != authority.policy.delay_model_sha256
    ):
        raise ValueError("delay evidence must match the sealed delay policy identity")
    replayed_policy = CostPolicy.from_dict(authority.policy.to_dict())
    return apply_cost_policy(
        replayed_policy,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        event_id=request.event_id,
        event_timestamp=request.event_timestamp,
        venue=request.venue,
        symbol=request.symbol,
        timeframe=request.timeframe,
        application=application,
        filled=filled,
        delay_evidence_sha256=delay_evidence_sha256,
    )
