from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict, cast

import pytest

from market_structure_lab.research.costs import (
    CostAdmissionStatus,
    CostApplication,
    CostCoverageStatus,
    CostEvidence,
    CostEventCoverage,
    CostEventCoveragePublication,
    CostPolicy,
    CostRate,
    CostSide,
    FundingPolicy,
    TradeCostResult,
    apply_cost_batch,
    apply_cost_policy,
    assess_cost_application,
    assess_cost_policy,
    freeze_cost_event_coverage_publication,
    verify_cost_policy,
)
from market_structure_lab.research.models import (
    ExecutionStatus,
    ScientificDecision,
    ValidationTerminalState,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
EVENT_TS = datetime(2025, 1, 2, 12, tzinfo=UTC)


class CostRequest(TypedDict):
    side: CostSide
    entry_price: float
    exit_price: float
    event_id: str
    event_timestamp: datetime
    venue: str
    symbol: str
    timeframe: str
    application: NotRequired[CostApplication]
    filled: NotRequired[bool]
    delay_evidence_sha256: NotRequired[str | None]


class CostRequestChanges(TypedDict, total=False):
    side: CostSide
    entry_price: float
    exit_price: float
    event_id: str
    event_timestamp: datetime
    venue: str
    symbol: str
    timeframe: str
    application: CostApplication
    filled: bool
    delay_evidence_sha256: str | None


class ExplodingCoverageSource:
    def __iter__(self) -> Iterator[CostEventCoverage]:
        raise AssertionError("coverage source was iterated before bounded admission")


class ExplodingTrades:
    def __iter__(self) -> Iterator[CostRequest]:
        raise AssertionError("trades were iterated before cost budget preflight")


class UnderdeclaredTrades:
    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[CostRequest]:
        yield _trade()
        yield _trade(event_id="EV-000002")
        raise AssertionError("batch materialised beyond the declared overflow witness")


class AllocationSpy:
    called = False

    def __call__(self) -> None:
        self.called = True
        raise AssertionError("allocation happened before cost budget preflight")


def _rate(value: float, *, evidence: str = SHA_A) -> CostRate:
    return CostRate(
        rate=value,
        units="proportion_of_notional",
        provenance="fixture-derived-cost-v1",
        evidence_sha256=evidence,
    )


def _event_coverage(
    *,
    event_id: str = "EV-000001",
    venue: str = "binance_spot",
    symbol: str = "SOLUSDT",
    timeframe: str = "1h",
    effective_start: datetime = EVENT_TS - timedelta(hours=1),
    effective_end: datetime = EVENT_TS + timedelta(hours=1),
) -> CostEventCoverage:
    return CostEventCoverage(
        event_id=event_id,
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        effective_start=effective_start,
        effective_end=effective_end,
        evidence_sha256=SHA_E,
    )


def _publication(*coverages: CostEventCoverage) -> CostEventCoveragePublication:
    rows = coverages or (_event_coverage(),)
    return freeze_cost_event_coverage_publication(
        rows,
        source_publication_sha256=SHA_B,
        budget=ValidationWorkBudget(max_events=8),
        demand=ValidationWorkDemand(events=len(rows)),
    )


def _evidence(*, missing: bool = False, all_zero: bool = False) -> CostEvidence:
    value = 0.0 if all_zero else None
    return CostEvidence(
        evidence_id="CE-TEST-000001",
        symbol="SOLUSDT",
        timeframe="1h",
        source_publication_sha256=SHA_B,
        entry_fee=_rate(value if value is not None else 0.0010, evidence=SHA_A),
        entry_half_spread=_rate(value if value is not None else 0.0005, evidence=SHA_B),
        entry_slippage=_rate(value if value is not None else 0.0002, evidence=SHA_C),
        exit_fee=_rate(value if value is not None else 0.0015, evidence=SHA_A),
        exit_half_spread=_rate(value if value is not None else 0.0007, evidence=SHA_B),
        exit_slippage=_rate(value if value is not None else 0.0003, evidence=SHA_C),
        fill_probability=0.8,
        coverage_status=CostCoverageStatus.MISSING_EVENT_COVERAGE
        if missing
        else CostCoverageStatus.COMPLETE,
        coverage_reason="event coverage absent" if missing else "complete event evidence",
        event_coverage_publication=None if missing else _publication(),
    )


def _policy(*, evidence: CostEvidence | None = None) -> CostPolicy:
    return CostPolicy(
        policy_id="CP-PHASE5-TEST-000001",
        base_policy_sha256=SHA_D,
        funding=FundingPolicy.SPOT_NOT_APPLICABLE,
        evidence=evidence if evidence is not None else _evidence(),
        stress_multiplier=2.0,
        stress_fill_probability_power=2,
        delay_model_sha256=SHA_E,
        max_applications=4,
    )


def _trade(**changes: object) -> CostRequest:
    request: CostRequest = {
        "side": CostSide.LONG,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "event_id": "EV-000001",
        "event_timestamp": EVENT_TS,
        "venue": "binance_spot",
        "symbol": "SOLUSDT",
        "timeframe": "1h",
    }
    request.update(cast(CostRequestChanges, changes))
    return request


def _apply(
    policy: CostPolicy,
    *,
    side: CostSide,
    entry_price: float,
    exit_price: float,
    application: CostApplication = CostApplication.BASE,
    filled: bool = True,
    event_id: str = "EV-000001",
    event_timestamp: datetime = EVENT_TS,
    venue: str = "binance_spot",
    delay_evidence_sha256: str | None = None,
) -> TradeCostResult:
    return apply_cost_policy(
        policy,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        application=application,
        filled=filled,
        event_id=event_id,
        event_timestamp=event_timestamp,
        venue=venue,
        symbol="SOLUSDT",
        timeframe="1h",
        delay_evidence_sha256=delay_evidence_sha256,
    )


def test_cost_policy_assessment_uses_terminal_states_only_for_blocking_outcomes() -> None:
    invalid = assess_cost_policy(None)
    assert invalid.terminal_state == ValidationTerminalState(
        ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
    )
    assert invalid.admission_status is CostAdmissionStatus.FAILED

    missing = assess_cost_policy(_policy(evidence=_evidence(missing=True)))
    assert missing.terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
    )
    assert missing.admission_status is CostAdmissionStatus.INCONCLUSIVE

    complete = assess_cost_policy(_policy())
    assert complete.terminal_state is None
    assert complete.admission_status is CostAdmissionStatus.ADMISSIBLE


def test_all_zero_configured_rates_are_failed_not_evaluated_not_zero_cost_complete() -> None:
    policy = _policy(evidence=_evidence(all_zero=True))
    assessment = assess_cost_policy(policy)

    assert assessment.terminal_state == ValidationTerminalState(
        ExecutionStatus.FAILED, ScientificDecision.NOT_EVALUATED
    )
    assert assessment.admission_status is CostAdmissionStatus.FAILED
    assert "zero" in assessment.reason
    with pytest.raises(ValueError, match="zero"):
        _apply(policy, side=CostSide.LONG, entry_price=100.0, exit_price=110.0)


def test_event_coverage_publication_is_bounded_and_rejects_exploding_sources_pre_materialisation() -> (
    None
):
    with pytest.raises(TypeError, match="Sized"):
        freeze_cost_event_coverage_publication(
            ExplodingCoverageSource(),
            source_publication_sha256=SHA_B,
            budget=ValidationWorkBudget(max_events=8),
            demand=ValidationWorkDemand(events=1),
        )

    with pytest.raises(ValidationWorkBudgetViolation, match="events"):
        freeze_cost_event_coverage_publication(
            (_event_coverage(), _event_coverage(event_id="EV-000002")),
            source_publication_sha256=SHA_B,
            budget=ValidationWorkBudget(max_events=1),
            demand=ValidationWorkDemand(events=2),
        )


def test_event_coverage_publication_binds_count_digest_source_and_uses_keyed_lookup() -> None:
    publication = _publication(_event_coverage(), _event_coverage(event_id="EV-000002"))

    assert publication.coverage_count == 2
    assert publication.source_publication_sha256 == SHA_B
    assert publication.coverage_digest_sha256 == publication.recompute_coverage_digest()
    found = publication.lookup(
        event_id="EV-000002",
        venue="binance_spot",
        symbol="SOLUSDT",
        timeframe="1h",
        event_timestamp=EVENT_TS,
    )
    assert found is not None
    assert found.event_id == "EV-000002"
    assert (
        publication.lookup(
            event_id="EV-MISSING",
            venue="binance_spot",
            symbol="SOLUSDT",
            timeframe="1h",
            event_timestamp=EVENT_TS,
        )
        is None
    )


def test_event_coverage_is_keyed_by_event_venue_symbol_timeframe_and_utc_interval() -> None:
    policy = _policy()
    complete = assess_cost_application(
        policy,
        event_id="EV-000001",
        event_timestamp=EVENT_TS,
        venue="binance_spot",
        symbol="SOLUSDT",
        timeframe="1h",
    )
    assert complete.terminal_state is None
    assert complete.admission_status is CostAdmissionStatus.ADMISSIBLE

    mutations: tuple[CostRequestChanges, ...] = (
        {"event_id": "EV-MISSING"},
        {"venue": "coinbase_spot"},
        {"symbol": "ADAUSDT"},
        {"timeframe": "4h"},
        {"event_timestamp": EVENT_TS + timedelta(hours=2)},
    )
    for changes in mutations:
        args = _trade(**changes)
        missing = assess_cost_application(
            policy,
            event_id=args["event_id"],
            event_timestamp=args["event_timestamp"],
            venue=args["venue"],
            symbol=args["symbol"],
            timeframe=args["timeframe"],
        )
        assert missing.terminal_state == ValidationTerminalState(
            ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
        )
        with pytest.raises(ValueError, match="event cost coverage"):
            apply_cost_policy(
                policy,
                side=CostSide.LONG,
                entry_price=100.0,
                exit_price=110.0,
                event_id=args["event_id"],
                event_timestamp=args["event_timestamp"],
                venue=args["venue"],
                symbol=args["symbol"],
                timeframe=args["timeframe"],
            )


def test_event_coverage_requires_utc_aware_half_open_effective_interval() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_event_coverage(), effective_start=datetime(2025, 1, 1))
    with pytest.raises(ValueError, match="effective_end"):
        replace(_event_coverage(), effective_end=EVENT_TS - timedelta(hours=2))
    edge_policy = _policy(
        evidence=replace(
            _evidence(),
            event_coverage_publication=_publication(
                _event_coverage(
                    effective_start=EVENT_TS - timedelta(hours=1),
                    effective_end=EVENT_TS,
                )
            ),
        )
    )
    assert assess_cost_application(
        edge_policy,
        event_id="EV-000001",
        event_timestamp=EVENT_TS,
        venue="binance_spot",
        symbol="SOLUSDT",
        timeframe="1h",
    ).terminal_state == ValidationTerminalState(
        ExecutionStatus.COMPLETED, ScientificDecision.INCONCLUSIVE
    )


def test_spot_costs_use_exact_long_formula_with_unequal_entry_and_exit_prices() -> None:
    result = _apply(
        _policy(),
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
        application=CostApplication.BASE,
    )

    ratio = 1.1
    entry_cost = 0.0010 + 0.0005 + 0.0002
    exit_cost = ratio * (0.0015 + 0.0007 + 0.0003)
    conditional = (ratio - 1.0) - entry_cost - exit_cost
    assert result.gross_return == pytest.approx(0.1)
    assert result.entry_cost == pytest.approx(entry_cost)
    assert result.exit_cost == pytest.approx(exit_cost)
    assert result.funding_cost == 0.0
    assert result.conditional_filled_net_return == pytest.approx(conditional)
    assert result.expected_net_return == pytest.approx(0.8 * conditional)
    assert result.missed_fill_return == 0.0
    assert result.cost_policy_sha256 == _policy().sha256
    assert result.event_coverage_sha256 == _event_coverage().sha256


def test_spot_costs_use_exact_short_formula_and_price_ratio_scales_exit_cost() -> None:
    result = _apply(
        _policy(),
        side=CostSide.SHORT,
        entry_price=100.0,
        exit_price=90.0,
    )

    ratio = 0.9
    entry_cost = 0.0010 + 0.0005 + 0.0002
    exit_cost = ratio * (0.0015 + 0.0007 + 0.0003)
    conditional = -(ratio - 1.0) - entry_cost - exit_cost
    assert result.gross_return == pytest.approx(0.1)
    assert result.exit_cost == pytest.approx(exit_cost)
    assert result.conditional_filled_net_return == pytest.approx(conditional)
    assert result.expected_net_return == pytest.approx(0.8 * conditional)


def test_missed_fill_has_exactly_zero_return_and_no_cost() -> None:
    result = _apply(
        _policy(),
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
        filled=False,
    )

    assert result == TradeCostResult.missed(
        cost_policy_sha256=_policy().sha256,
        evidence_sha256=_evidence().sha256,
        event_coverage_sha256=_event_coverage().sha256,
    )


def test_stress_doubles_applicable_rates_and_squares_fill_probability() -> None:
    base = _apply(_policy(), side=CostSide.LONG, entry_price=100.0, exit_price=110.0)
    stress = _apply(
        _policy(),
        side=CostSide.LONG,
        entry_price=100.0,
        exit_price=110.0,
        application=CostApplication.STRESS,
    )

    assert stress.entry_cost == pytest.approx(base.entry_cost * 2.0)
    assert stress.exit_cost == pytest.approx(base.exit_cost * 2.0)
    assert stress.fill_probability == pytest.approx(0.8**2)
    assert stress.expected_net_return == pytest.approx(
        (0.8**2) * stress.conditional_filled_net_return
    )


def test_delay_application_binds_separately_legal_price_path_evidence() -> None:
    result = _apply(
        _policy(),
        side=CostSide.LONG,
        entry_price=101.0,
        exit_price=111.0,
        application=CostApplication.DELAYED,
        delay_evidence_sha256="f" * 64,
    )

    assert result.application is CostApplication.DELAYED
    assert result.delay_evidence_sha256 == "f" * 64

    with pytest.raises(ValueError, match="delay_evidence_sha256"):
        _apply(
            _policy(),
            side=CostSide.LONG,
            entry_price=101.0,
            exit_price=111.0,
            application=CostApplication.DELAYED,
        )


def test_funding_rows_are_rejected_and_spot_identity_is_exact() -> None:
    policy = _policy()
    assert policy.funding.value == "not_applicable_spot_v1"

    with pytest.raises(ValueError, match="funding"):
        CostPolicy(
            policy_id="CP-PHASE5-TEST-000002",
            base_policy_sha256=SHA_D,
            funding_rows_sha256=SHA_E,
            evidence=_evidence(),
        )


def test_invalid_rates_units_provenance_fill_and_prices_fail_closed() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _rate(-0.1)
    with pytest.raises(ValueError, match="finite"):
        _rate(float("nan"))
    with pytest.raises(ValueError, match="units"):
        replace(_rate(0.1), units="basis_points")
    with pytest.raises(ValueError, match="provenance"):
        replace(_rate(0.1), provenance="")
    with pytest.raises(ValueError, match="fill_probability"):
        replace(_evidence(), fill_probability=0.0)
    with pytest.raises(ValueError, match="entry_price"):
        _apply(_policy(), side=CostSide.LONG, entry_price=0.0, exit_price=110.0)
    with pytest.raises(ValueError, match="side"):
        _apply(_policy(), side=1, entry_price=100.0, exit_price=110.0)  # type: ignore[arg-type]


def test_missing_or_invalid_base_policy_and_missing_event_coverage_fail_closed() -> None:
    with pytest.raises(ValueError, match="base_policy_sha256"):
        CostPolicy(policy_id="CP-PHASE5-TEST-000003", base_policy_sha256="", evidence=_evidence())

    policy = replace(_policy(), evidence=_evidence(missing=True))
    assert verify_cost_policy(policy) is CostCoverageStatus.MISSING_EVENT_COVERAGE
    with pytest.raises(ValueError, match="inconclusive"):
        _apply(policy, side=CostSide.LONG, entry_price=100.0, exit_price=110.0)


def test_policy_identity_is_canonical_and_rejects_tamper_replay() -> None:
    policy = _policy()
    replay = CostPolicy.from_dict(policy.to_dict())
    assert replay == policy
    assert replay.sha256 == policy.sha256

    tampered = policy.to_dict()
    tampered_evidence = cast(dict[str, object], tampered["evidence"])
    tampered["evidence"] = {**tampered_evidence, "fill_probability": 1.0}
    with pytest.raises(ValueError, match="sha256"):
        CostPolicy.from_dict(tampered)

    replay_without_hashes = policy.to_dict(include_sha=False)
    replay_without_hashes["evidence"] = _evidence().to_dict(include_sha=False)
    replay_evidence = cast(dict[str, object], replay_without_hashes["evidence"])
    replay_without_hashes["evidence"] = {
        **replay_evidence,
        "fill_probability": 1.0,
    }
    assert CostPolicy.from_dict(replay_without_hashes).sha256 != policy.sha256

    with pytest.raises(ValueError, match="sha256"):
        CostPolicy.from_dict({**policy.to_dict(), "sha256": "0" * 64})


def test_cost_application_budget_preflights_before_iterating_trades() -> None:
    policy = replace(_policy(), max_applications=2)

    with pytest.raises(ValueError, match="applications"):
        policy.require_application_budget(3)

    policy.require_application_budget(2)


def test_batch_cost_preflight_uses_validation_budget_before_iteration_or_allocation() -> None:
    spy = AllocationSpy()
    over_budget = ValidationWorkDemand(
        outcomes=2, path_cells=ValidationWorkBudget().max_path_cells + 1
    )

    with pytest.raises(ValidationWorkBudgetViolation, match="path_cells"):
        apply_cost_batch(
            _policy(),
            ExplodingTrades(),
            demand=over_budget,
            budget=ValidationWorkBudget(),
            allocation=spy,
        )

    assert not spy.called


def test_batch_cost_preflight_rejects_underdeclared_sized_iterable_before_application() -> None:
    with pytest.raises(ValueError, match="underdeclared"):
        apply_cost_batch(
            _policy(),
            UnderdeclaredTrades(),
            demand=ValidationWorkDemand(outcomes=1, path_cells=1),
            budget=ValidationWorkBudget(),
        )


def test_batch_cost_application_accepts_bounded_requests_after_preflight() -> None:
    demand = ValidationWorkDemand(outcomes=1, path_cells=1)
    results = apply_cost_batch(
        _policy(),
        (_trade(),),
        demand=demand,
        budget=ValidationWorkBudget(),
    )

    assert len(results) == 1
    assert results[0].conditional_filled_net_return > 0.0
