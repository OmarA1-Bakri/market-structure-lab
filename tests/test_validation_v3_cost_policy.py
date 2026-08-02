from __future__ import annotations

from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile

import pytest

from market_structure_lab.research.costs import (
    CostApplication,
    CostCoverageStatus,
    CostEvidence,
    CostEventCoverage,
    CostPolicy,
    CostRate,
    CostSide,
    FundingPolicy,
)
from market_structure_lab.research.models import ExecutionStatus, ScientificDecision
from market_structure_lab.research.validation_v3_cost_policy import (
    CostPolicyProvenanceKindV3,
    CostPolicyProvenanceIdentityV3,
    CostPolicyProvenanceV3,
    CostPolicyRequestV3,
    apply_verified_cost_policy_v3,
    load_cost_policy_provenance_v3,
    publish_cost_policy_provenance_v3,
    seal_cost_policy_authority_v3,
    verify_cost_policy_authority_v3,
    verify_cost_policy_provenance_v3,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
EVENT_TS = datetime(2025, 1, 2, 12, tzinfo=UTC)
_PROVENANCE_DIRECTORIES: list[tempfile.TemporaryDirectory[str]] = []


def _rate(value: float, *, source: str = SHA_A) -> CostRate:
    return CostRate(
        rate=value,
        units="proportion_of_notional",
        provenance="preregistered_conservative_policy_v1",
        evidence_sha256=source,
    )


def _coverage(event_id: str = "EV-1") -> CostEventCoverage:
    return CostEventCoverage(
        event_id=event_id,
        venue="binance_spot",
        symbol="SOLUSDT",
        timeframe="1h",
        effective_start=EVENT_TS - timedelta(hours=1),
        effective_end=EVENT_TS + timedelta(hours=1),
        evidence_sha256=SHA_E,
    )


def _policy(*, coverages: tuple[CostEventCoverage, ...] | None = None) -> CostPolicy:
    from market_structure_lab.research.costs import CostEventCoveragePublication
    from market_structure_lab.core.identity import hash_json

    coverage_rows = (_coverage(),) if coverages is None else coverages
    publication = CostEventCoveragePublication(
        source_publication_sha256=SHA_B,
        coverage_count=len(coverage_rows),
        coverage_digest_sha256=hash_json(
            "cost-event-coverage-publication-entries-v1",
            [item.to_dict(include_sha=False) for item in coverage_rows],
        ),
        entries=coverage_rows,
    )
    evidence = CostEvidence(
        evidence_id="CE-1",
        symbol="SOLUSDT",
        timeframe="1h",
        source_publication_sha256=SHA_B,
        entry_fee=_rate(0.0010),
        entry_half_spread=_rate(0.0005, source=SHA_B),
        entry_slippage=_rate(0.0002, source=SHA_C),
        exit_fee=_rate(0.0015),
        exit_half_spread=_rate(0.0007, source=SHA_B),
        exit_slippage=_rate(0.0003, source=SHA_C),
        fill_probability=0.8,
        coverage_status=CostCoverageStatus.COMPLETE,
        coverage_reason="complete",
        event_coverage_publication=publication,
    )
    return CostPolicy(
        policy_id="CP-1",
        base_policy_sha256=SHA_D,
        evidence=evidence,
        funding=FundingPolicy.SPOT_NOT_APPLICABLE,
        stress_multiplier=2.0,
        stress_fill_probability_power=2,
        delay_model_sha256=SHA_E,
        max_applications=8,
    )


def _provenance(policy: CostPolicy | None = None) -> CostPolicyProvenanceV3:
    bound_policy = _policy() if policy is None else policy
    directory = tempfile.TemporaryDirectory(prefix="msl-cost-provenance-")
    _PROVENANCE_DIRECTORIES.append(directory)
    root = Path(directory.name) / "publication"
    identity = publish_cost_policy_provenance_v3(
        policy=bound_policy,
        kind=CostPolicyProvenanceKindV3.PREREGISTERED_POLICY,
        programme_sha256=SHA_A,
        preregistration_sha256=bound_policy.base_policy_sha256,
        review_sha256=SHA_C,
        prior_freeze_evidence_sha256=SHA_E,
        output_root=root,
    )
    return load_cost_policy_provenance_v3(
        publication_root=root,
        expected_identity=identity,
        expected_programme_sha256=SHA_A,
    )


def _request(event_id: str = "EV-1") -> CostPolicyRequestV3:
    return CostPolicyRequestV3(
        event_id=event_id,
        event_timestamp=EVENT_TS,
        venue="binance_spot",
        symbol="SOLUSDT",
        timeframe="1h",
    )


def test_policy_rooted_successor_seals_positive_preregistered_model_without_capacity() -> None:
    policy = _policy()
    authority = seal_cost_policy_authority_v3(
        policy=policy, provenance=_provenance(policy), required_events=(_request(),)
    )

    assert authority.identity.value.startswith("CPAV3-")
    assert authority.terminal_state is None
    assert authority.policy is not None
    assert authority.cost_policy_sha256 == authority.policy.sha256
    assert authority.required_event_count == 1
    assert "capacity" not in authority.to_dict()
    assert verify_cost_policy_authority_v3(authority) is authority
    assert authority.policy is not policy

    object.__setattr__(policy.evidence.entry_fee, "units", "mutated_after_seal")
    assert verify_cost_policy_authority_v3(authority) is authority


def test_zero_components_are_admissible_when_finite_aggregate_cost_is_positive() -> None:
    policy = _policy()
    evidence = replace(
        policy.evidence,
        entry_half_spread=_rate(0.0),
        entry_slippage=_rate(0.0),
        exit_half_spread=_rate(0.0),
        exit_slippage=_rate(0.0),
    )
    mixed_policy = replace(policy, evidence=evidence)
    authority = seal_cost_policy_authority_v3(
        policy=mixed_policy,
        provenance=_provenance(mixed_policy),
        required_events=(_request(),),
    )
    assert authority.terminal_state is None


def test_all_zero_preregistered_parameters_fail_not_evaluated() -> None:
    policy = _policy()
    zero = _rate(0.0)
    evidence = replace(
        policy.evidence,
        entry_fee=zero,
        entry_half_spread=zero,
        entry_slippage=zero,
        exit_fee=zero,
        exit_half_spread=zero,
        exit_slippage=zero,
    )
    zero_policy = replace(policy, evidence=evidence)
    authority = seal_cost_policy_authority_v3(
        policy=zero_policy,
        provenance=_provenance(zero_policy),
        required_events=(_request(),),
    )

    assert authority.terminal_state is not None
    assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
    assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED
    assert "positive" in authority.reason


def test_finite_components_with_overflowed_aggregate_fail_not_evaluated() -> None:
    policy = _policy()
    huge = _rate(1e308)
    overflow_evidence = replace(
        policy.evidence,
        entry_fee=huge,
        entry_half_spread=huge,
        entry_slippage=huge,
        exit_fee=huge,
        exit_half_spread=huge,
        exit_slippage=huge,
    )
    overflow_policy = replace(policy, evidence=overflow_evidence)
    authority = seal_cost_policy_authority_v3(
        policy=overflow_policy,
        provenance=_provenance(overflow_policy),
        required_events=(_request(),),
    )
    assert authority.terminal_state is not None
    assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
    assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED


def test_only_registered_original_provenance_can_authorize_policy() -> None:
    policy = _policy()
    provenance = _provenance(policy)
    assert verify_cost_policy_provenance_v3(provenance, policy=policy) is provenance
    assert (
        provenance.canonical_bytes
        == (provenance.publication_root / "publication.json").read_bytes()
    )

    wrong_identity = CostPolicyProvenanceIdentityV3(f"CPPV3-{'b' * 64}")
    with pytest.raises(ValueError, match="expected frozen identity"):
        load_cost_policy_provenance_v3(
            publication_root=provenance.publication_root,
            expected_identity=wrong_identity,
            expected_programme_sha256=SHA_A,
        )
    with pytest.raises(ValueError, match="programme identity"):
        load_cost_policy_provenance_v3(
            publication_root=provenance.publication_root,
            expected_identity=provenance.identity,
            expected_programme_sha256=SHA_B,
        )

    forged = object.__new__(CostPolicyProvenanceV3)
    object.__setattr__(forged, "kind", provenance.kind)
    object.__setattr__(forged, "programme_sha256", provenance.programme_sha256)
    object.__setattr__(forged, "preregistration_sha256", provenance.preregistration_sha256)
    object.__setattr__(forged, "review_sha256", provenance.review_sha256)
    object.__setattr__(
        forged, "prior_freeze_evidence_sha256", provenance.prior_freeze_evidence_sha256
    )
    object.__setattr__(forged, "cost_policy_sha256", provenance.cost_policy_sha256)
    object.__setattr__(forged, "outcome_access_record_count", 0)
    object.__setattr__(forged, "canonical_sha256", provenance.canonical_sha256)

    for unauthorized in (copy(provenance), forged):
        authority = seal_cost_policy_authority_v3(
            policy=policy, provenance=unauthorized, required_events=(_request(),)
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
        assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED

    with pytest.raises(ValueError, match="exactly zero"):
        publish_cost_policy_provenance_v3(
            policy=policy,
            kind=CostPolicyProvenanceKindV3.PREREGISTERED_POLICY,
            programme_sha256=SHA_A,
            preregistration_sha256=policy.base_policy_sha256,
            review_sha256=SHA_C,
            prior_freeze_evidence_sha256=SHA_E,
            output_root=Path("not-created"),
            outcome_access_record_count=1,
        )


def test_changed_policy_bytes_cannot_reuse_provenance_capability() -> None:
    policy = _policy()
    provenance = _provenance(policy)
    changed_fee_evidence = replace(policy.evidence, entry_fee=_rate(0.0020))
    changed_fill_evidence = replace(policy.evidence, fill_probability=0.7)
    changed_policies = (
        replace(policy, evidence=changed_fee_evidence),
        replace(policy, evidence=changed_fill_evidence),
        replace(policy, stress_multiplier=3.0),
        replace(policy, delay_model_sha256=SHA_A),
    )
    for changed_policy in changed_policies:
        authority = seal_cost_policy_authority_v3(
            policy=changed_policy,
            provenance=provenance,
            required_events=(_request(),),
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
        assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED


def test_missing_policy_and_tampered_nonfinite_parameter_fail_not_evaluated() -> None:
    missing = seal_cost_policy_authority_v3(
        policy=None, provenance=_provenance(), required_events=(_request(),)
    )
    assert missing.terminal_state is not None
    assert missing.terminal_state.execution_status is ExecutionStatus.FAILED
    assert missing.terminal_state.decision is ScientificDecision.NOT_EVALUATED

    policy = _policy()
    provenance = _provenance(policy)
    object.__setattr__(policy.evidence.entry_fee, "rate", float("inf"))
    invalid = seal_cost_policy_authority_v3(
        policy=policy, provenance=provenance, required_events=(_request(),)
    )
    assert invalid.terminal_state is not None
    assert invalid.terminal_state.execution_status is ExecutionStatus.FAILED
    assert invalid.terminal_state.decision is ScientificDecision.NOT_EVALUATED


def test_mutated_nested_policy_or_provenance_fails_at_seal_without_crashing() -> None:
    invalid_policies: list[tuple[CostPolicy, CostPolicyProvenanceV3]] = []

    invalid_units = _policy()
    provenance = _provenance(invalid_units)
    object.__setattr__(invalid_units.evidence.entry_fee, "units", "basis_points")
    invalid_policies.append((invalid_units, provenance))

    invalid_evidence_hash = _policy()
    provenance = _provenance(invalid_evidence_hash)
    object.__setattr__(invalid_evidence_hash.evidence.entry_fee, "evidence_sha256", "bad")
    invalid_policies.append((invalid_evidence_hash, provenance))

    invalid_fill = _policy()
    provenance = _provenance(invalid_fill)
    object.__setattr__(invalid_fill.evidence, "fill_probability", 0.0)
    invalid_policies.append((invalid_fill, provenance))

    invalid_coverage_count = _policy()
    provenance = _provenance(invalid_coverage_count)
    publication = invalid_coverage_count.evidence.event_coverage_publication
    assert publication is not None
    object.__setattr__(publication, "coverage_count", 2)
    invalid_policies.append((invalid_coverage_count, provenance))

    invalid_coverage_row = _policy()
    provenance = _provenance(invalid_coverage_row)
    publication = invalid_coverage_row.evidence.event_coverage_publication
    assert publication is not None
    object.__setattr__(publication.entries[0], "evidence_sha256", "bad")
    invalid_policies.append((invalid_coverage_row, provenance))

    for policy, provenance in invalid_policies:
        authority = seal_cost_policy_authority_v3(
            policy=policy, provenance=provenance, required_events=(_request(),)
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
        assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED

    provenance = _provenance()
    object.__setattr__(provenance, "review_sha256", "bad")
    authority = seal_cost_policy_authority_v3(
        policy=_policy(), provenance=provenance, required_events=(_request(),)
    )
    assert authority.terminal_state is not None
    assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
    assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED


def test_successor_requires_exact_doubled_squared_fill_and_delay_policy() -> None:
    invalid_policies = (
        replace(_policy(), stress_multiplier=3.0),
        replace(_policy(), stress_fill_probability_power=1),
        replace(_policy(), delay_model_sha256=None),
    )
    for policy in invalid_policies:
        authority = seal_cost_policy_authority_v3(
            policy=policy,
            provenance=_provenance(policy),
            required_events=(_request(),),
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.FAILED
        assert authority.terminal_state.decision is ScientificDecision.NOT_EVALUATED


def test_missing_or_extra_event_coverage_completes_inconclusive() -> None:
    for policy, required in (
        (_policy(coverages=()), (_request(),)),
        (_policy(coverages=(_coverage(), _coverage("EV-EXTRA"))), (_request(),)),
        (_policy(), (_request(), _request("EV-MISSING"))),
    ):
        authority = seal_cost_policy_authority_v3(
            policy=policy, provenance=_provenance(policy), required_events=required
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.COMPLETED
        assert authority.terminal_state.decision is ScientificDecision.INCONCLUSIVE
        assert "coverage" in authority.reason


def test_required_event_dimension_or_time_mismatch_is_missing_coverage_inconclusive() -> None:
    policy = _policy()
    provenance = _provenance(policy)
    mismatches = (
        replace(_request(), venue="coinbase_spot"),
        replace(_request(), symbol="ADAUSDT"),
        replace(_request(), timeframe="4h"),
        replace(_request(), event_timestamp=EVENT_TS + timedelta(hours=2)),
    )
    for request in mismatches:
        authority = seal_cost_policy_authority_v3(
            policy=policy,
            provenance=provenance,
            required_events=(request,),
        )
        assert authority.terminal_state is not None
        assert authority.terminal_state.execution_status is ExecutionStatus.COMPLETED
        assert authority.terminal_state.decision is ScientificDecision.INCONCLUSIVE


def test_verified_consumption_preserves_base_stress_delay_and_missed_fill_semantics() -> None:
    authority = seal_cost_policy_authority_v3(
        policy=_policy(), provenance=_provenance(), required_events=(_request(),)
    )
    request = _request()

    base = apply_verified_cost_policy_v3(
        authority,
        request=request,
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
    )
    stress = apply_verified_cost_policy_v3(
        authority,
        request=request,
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
        application=CostApplication.STRESS,
    )
    delayed = apply_verified_cost_policy_v3(
        authority,
        request=request,
        side=CostSide.LONG,
        entry_price=101.0,
        exit_price=109.0,
        application=CostApplication.DELAYED,
        delay_evidence_sha256=SHA_E,
    )
    missed = apply_verified_cost_policy_v3(
        authority,
        request=request,
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
        filled=False,
    )

    assert stress.entry_cost == pytest.approx(2.0 * base.entry_cost)
    assert stress.exit_cost == pytest.approx(2.0 * base.exit_cost)
    assert stress.fill_probability == pytest.approx(base.fill_probability**2)
    assert delayed.application is CostApplication.DELAYED
    assert delayed.delay_evidence_sha256 == SHA_E
    assert missed.filled is False
    assert missed.gross_return == missed.entry_cost == missed.exit_cost == 0.0
    assert missed.expected_net_return == missed.missed_fill_return == 0.0


def test_copied_or_mutated_authority_cannot_be_consumed() -> None:
    authority = seal_cost_policy_authority_v3(
        policy=_policy(), provenance=_provenance(), required_events=(_request(),)
    )
    copied = copy(authority)
    with pytest.raises(ValueError, match="registered original"):
        verify_cost_policy_authority_v3(copied)

    object.__setattr__(authority, "reason", "changed")
    with pytest.raises(ValueError, match="changed"):
        apply_verified_cost_policy_v3(
            authority,
            request=_request(),
            side=CostSide.LONG,
            entry_price=100.0,
            exit_price=110.0,
        )
