from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.research.models import ScientificDecision
from market_structure_lab.research.robustness import (
    CapacityEvidence,
    ExclusionStrengthObservation,
    MarketFeatureObservation,
    OlsExposureEvent,
    PromotionEligibilityStatus,
    RegimeReturnEvidence,
    WeeklyRegimeObservation,
    RetirementRecord,
    AdjacentLookbackPerturbation,
    AdjacentLookbackPerturbationSet,
    RobustnessLowerBounds,
    evaluate_capacity_promotion,
    evaluate_exposure_ols,
    evaluate_regime_gate,
    evaluate_robustness_gates,
    fit_inner_training_regime_policy,
    select_ex_strongest_development_exclusions,
    target_excluded_market_features,
)

SHA = "a" * 64
PROGRAMME_ID = "VP-" + "1" * 64
CANDIDATE_ID = "candidate-A-001"
BASE_TIME = datetime(2026, 1, 5, tzinfo=UTC)


def test_ex_strongest_asset_and_year_use_development_only_ties_and_identity() -> None:
    observations = (
        ExclusionStrengthObservation("ETH", 2025, 0.40, True, 10, SHA),
        ExclusionStrengthObservation("BTC", 2025, 0.40, True, 10, SHA),
        ExclusionStrengthObservation("SOL", 2026, None, True, 0, SHA),
        ExclusionStrengthObservation("ADA", 2024, 0.30, True, 10, SHA),
        ExclusionStrengthObservation("BTC", 2024, 0.40, True, 10, SHA),
    )

    selection = select_ex_strongest_development_exclusions(
        observations, programme_id=PROGRAMME_ID, candidate_id=CANDIDATE_ID
    )

    assert selection.asset == "BTC"
    assert selection.year == 2024
    assert selection.asset_strength == pytest.approx(0.40)
    assert selection.year_strength == pytest.approx(0.40)
    assert selection.score_table_sha256 == selection.replay_sha256
    assert "SOL:2026" in selection.no_event_strata
    mutated = replace(selection, asset="ETH")
    with pytest.raises(ValueError, match="identity"):
        mutated.verify_identity()


def test_exclusion_selection_requires_multiple_assets_and_years() -> None:
    selection = select_ex_strongest_development_exclusions(
        (ExclusionStrengthObservation("BTC", 2025, 0.10, True, 3, SHA),),
        programme_id=PROGRAMME_ID,
        candidate_id=CANDIDATE_ID,
    )

    assert selection.terminal_state.decision is ScientificDecision.INCONCLUSIVE
    assert "one asset" in selection.reason


def test_robustness_gates_require_all_lower_bounds_perturbations_and_fold_four() -> None:
    passing = RobustnessLowerBounds(
        base=0.05,
        doubled_cost=0.03,
        one_bar_delay=0.02,
        ex_strongest_asset=0.01,
        ex_strongest_year=0.015,
        adjacent_lookback_perturbations=_perturbations("A", "ma_fast", 12, 0.02, 0.025),
        outer_fold_point_estimates=(0.1, -0.01, 0.2, 0.03),
    )

    evidence = evaluate_robustness_gates(passing, minimum_positive_lower_bound=0.0)

    assert evidence.terminal_state.decision is ScientificDecision.SUPPORTED_DEVELOPMENT
    assert evidence.base_positive
    assert evidence.stress_positive
    assert evidence.delay_positive
    assert evidence.perturbations_positive
    assert evidence.fold_signs_positive_3_of_4_with_fold4

    assert (
        evaluate_robustness_gates(replace(passing, one_bar_delay=0.0)).terminal_state.decision
        is ScientificDecision.REJECTED
    )
    assert (
        evaluate_robustness_gates(
            replace(
                passing,
                adjacent_lookback_perturbations=_perturbations("A", "ma_fast", 12, -0.001, 0.02),
            )
        ).terminal_state.decision
        is ScientificDecision.REJECTED
    )
    assert (
        evaluate_robustness_gates(
            replace(passing, outer_fold_point_estimates=(0.1, 0.2, 0.3, 0.0))
        ).terminal_state.decision
        is ScientificDecision.REJECTED
    )
    assert (
        evaluate_robustness_gates(
            passing, minimum_positive_lower_bound=0.02
        ).terminal_state.decision
        is ScientificDecision.REJECTED
    )


def test_target_excluded_market_features_and_prior_24h_ranks() -> None:
    rows = (
        MarketFeatureObservation("BTC", BASE_TIME, 0.99, 9.0, 5.0, 500.0),
        MarketFeatureObservation("ETH", BASE_TIME, 0.03, 0.20, 2.0, 200.0),
        MarketFeatureObservation("SOL", BASE_TIME, 0.01, 0.10, 4.0, 400.0),
        MarketFeatureObservation("ADA", BASE_TIME, 0.02, 0.30, 6.0, 300.0),
    )

    features = target_excluded_market_features("BTC", BASE_TIME, rows)

    assert features.market_return == pytest.approx(0.02)
    assert features.market_trend == pytest.approx(0.20)
    assert features.non_target_asset_count == 3
    assert features.target_volatility_rank == pytest.approx(2 / 3)
    assert features.target_turnover_rank == pytest.approx(1.0)
    assert "BTC" not in features.non_target_assets

    with pytest.raises(ValueError, match="at least three non-target"):
        target_excluded_market_features("BTC", BASE_TIME, rows[:3])


def test_regime_policy_uses_inner_training_median_excludes_zero_trend_and_classifies_gate() -> None:
    policy = fit_inner_training_regime_policy((0.2, 0.8, 0.6))
    assert policy.volatility_rank_median == pytest.approx(0.6)
    assert policy.classify(market_trend=0.0, volatility_rank=0.9) is None
    assert policy.classify(market_trend=0.01, volatility_rank=0.6) == "up_low_vol"
    assert policy.classify(market_trend=-0.01, volatility_rank=0.61) == "down_high_vol"

    one_positive = evaluate_regime_gate(
        (
            _regime("up_low_vol", (0.01, 0.02)),
            _regime("up_high_vol", (-0.01,)),
            _regime("down_low_vol", ()),
            _regime("down_high_vol", ()),
        )
    )
    assert one_positive.terminal_state.decision is ScientificDecision.INCONCLUSIVE
    assert "exactly one" in one_positive.reason

    passing = evaluate_regime_gate(
        (
            _regime("up_low_vol", (0.01, 0.02)),
            _regime("up_high_vol", (0.03, 0.01)),
        )
    )
    assert passing.terminal_state.decision is ScientificDecision.SUPPORTED_DEVELOPMENT

    rejected = evaluate_regime_gate((_regime("down_high_vol", (-0.02, -0.03)),))
    assert rejected.terminal_state.decision is ScientificDecision.REJECTED


def test_ols_exposure_uses_one_intercept_train_only_scaling_references_and_unseen_zero_encoding() -> (
    None
):
    train = tuple(
        OlsExposureEvent(
            row_id=f"tr-{index}",
            asset=asset,
            year=year,
            target_return=1.0 + 0.5 * market + residual,
            market_return=market,
            market_trend=trend,
            volatility_rank=vol_rank,
            turnover_rank=turn_rank,
        )
        for index, (asset, year, market, trend, vol_rank, turn_rank, residual) in enumerate(
            (
                ("BTC", 2024, -2.0, -1.0, 0.1, 0.7, 0.00),
                ("ETH", 2024, -1.0, 0.7, 0.6, 0.3, 0.01),
                ("BTC", 2025, 0.0, -0.2, 0.3, 0.9, -0.01),
                ("ETH", 2025, 1.0, 1.1, 0.8, 0.5, 0.02),
                ("SOL", 2025, 2.0, -0.8, 0.5, 0.2, -0.02),
                ("SOL", 2024, 3.0, 0.4, 0.2, 0.8, 0.00),
                ("BTC", 2024, 4.0, 1.6, 0.9, 0.4, 0.01),
                ("ETH", 2025, 5.0, -1.4, 0.4, 0.6, -0.01),
            )
        )
    )
    validation = _positive_ols_validation()

    evidence = evaluate_exposure_ols(train, validation)

    assert evidence.terminal_state.decision is ScientificDecision.SUPPORTED_DEVELOPMENT
    assert evidence.design.intercept_count == 1
    assert evidence.design.reference_asset == "BTC"
    assert evidence.design.reference_year == 2024
    assert "asset_SOL" in evidence.design.column_names
    assert "year_2025" in evidence.design.column_names
    encoded = evidence.validation_design_rows[0]
    assert encoded[evidence.design.column_names.index("asset_ETH")] == 0.0
    assert encoded[evidence.design.column_names.index("asset_SOL")] == 0.0
    assert encoded[evidence.design.column_names.index("year_2025")] == 0.0
    assert evidence.design.scaling_sha256 == evidence.replay_scaling_sha256


def test_rank_deficient_ols_is_inconclusive() -> None:
    train = tuple(
        OlsExposureEvent(f"tr-{index}", "BTC", 2024, 0.01 * index, 1.0, 1.0, 1.0, 1.0)
        for index in range(6)
    )

    validation = tuple(replace(event, row_id=f"va-{index}") for index, event in enumerate(train))

    evidence = evaluate_exposure_ols(train, validation)

    assert evidence.terminal_state.decision is ScientificDecision.INCONCLUSIVE
    assert "rank deficient" in evidence.reason


def test_capacity_absence_or_nonfinite_blocks_promotion_without_changing_validation_decision() -> (
    None
):
    assert (
        evaluate_capacity_promotion(ScientificDecision.VALIDATED, None).status
        is PromotionEligibilityStatus.INCONCLUSIVE_CAPACITY
    )
    assert (
        evaluate_capacity_promotion(ScientificDecision.SUPPORTED_DEVELOPMENT, None).status
        is PromotionEligibilityStatus.INCONCLUSIVE_CAPACITY
    )
    promotable = evaluate_capacity_promotion(
        ScientificDecision.VALIDATED,
        CapacityEvidence(CANDIDATE_ID, 0.02, 1.5, SHA),
    )
    assert promotable.status is PromotionEligibilityStatus.PROMOTABLE
    capacity_evidence = promotable.capacity_evidence
    assert capacity_evidence is not None
    mutated = replace(capacity_evidence, market_impact_bps=2.0)
    with pytest.raises(ValueError, match="identity"):
        mutated.verify_identity()


def test_retirement_record_binds_reason_effective_time_superseding_identity_and_invalidation() -> (
    None
):
    record = RetirementRecord(
        candidate_id=CANDIDATE_ID,
        reason="drift_monitor_invalidated",
        effective_time=BASE_TIME + timedelta(days=1),
        superseding_identity="VR-" + "2" * 64,
        invalidation_evidence_sha256=SHA,
    )

    assert len(record.sha256) == 64
    record.verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(record, reason="manual_override").verify_identity()


def test_selector_rejects_non_development_observations_before_identity_hashing() -> None:
    with pytest.raises(ValueError, match="development-only"):
        select_ex_strongest_development_exclusions(
            (
                ExclusionStrengthObservation("BTC", 2025, 99.0, False, 1, SHA),
                ExclusionStrengthObservation("ETH", 2025, 0.1, True, 1, SHA),
                ExclusionStrengthObservation("SOL", 2026, 0.2, True, 1, SHA),
            ),
            programme_id=PROGRAMME_ID,
            candidate_id=CANDIDATE_ID,
        )


def test_adjacent_lookback_perturbations_are_exact_typed_and_identity_bound() -> None:
    perturbations = AdjacentLookbackPerturbationSet(
        candidate_id=CANDIDATE_ID,
        family="A",
        slot_id="A-primary-001",
        original_lookbacks={"ma_fast": 12},
        perturbations=(
            AdjacentLookbackPerturbation("ma_fast", 12, 11, "n_L-1", 0.01),
            AdjacentLookbackPerturbation("ma_fast", 12, 13, "n_L+1", 0.02),
        ),
    )
    passing = RobustnessLowerBounds(
        base=0.05,
        doubled_cost=0.03,
        one_bar_delay=0.02,
        ex_strongest_asset=0.01,
        ex_strongest_year=0.015,
        adjacent_lookback_perturbations=perturbations,
        outer_fold_point_estimates=(0.1, -0.01, 0.2, 0.03),
    )

    evidence = evaluate_robustness_gates(passing)

    assert evidence.terminal_state.decision is ScientificDecision.SUPPORTED_DEVELOPMENT
    perturbations.verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(perturbations, slot_id="A-primary-002").verify_identity()
    with pytest.raises(ValueError, match=r"missing.*n_L\+1"):
        AdjacentLookbackPerturbationSet(
            candidate_id=CANDIDATE_ID,
            family="A",
            slot_id="A-primary-001",
            original_lookbacks={"ma_fast": 12},
            perturbations=(AdjacentLookbackPerturbation("ma_fast", 12, 11, "n_L-1", 0.01),),
        )
    with pytest.raises(ValueError, match="unknown lookback parameter"):
        AdjacentLookbackPerturbationSet(
            candidate_id=CANDIDATE_ID,
            family="A",
            slot_id="A-primary-001",
            original_lookbacks={"mystery": 12},
            perturbations=(
                AdjacentLookbackPerturbation("mystery", 12, 11, "n_L-1", 0.01),
                AdjacentLookbackPerturbation("mystery", 12, 13, "n_L+1", 0.02),
            ),
        )
    with pytest.raises(ValueError, match="unexpected adjacent-lookback perturbation"):
        AdjacentLookbackPerturbationSet(
            candidate_id=CANDIDATE_ID,
            family="A",
            slot_id="A-primary-001",
            original_lookbacks={"ma_fast": 12},
            perturbations=(
                AdjacentLookbackPerturbation("ma_fast", 12, 11, "n_L-1", 0.01),
                AdjacentLookbackPerturbation("ma_fast", 12, 13, "n_L+1", 0.02),
                AdjacentLookbackPerturbation("ma_slow", 24, 23, "n_L-1", 0.01),
            ),
        )


def test_ols_residual_lower_bound_is_computed_and_cannot_be_supplied_by_caller() -> None:
    train = _full_rank_ols_train()
    negative_validation = (
        OlsExposureEvent("va-neg-1", "XRP", 2026, -5.0, 6.0, 3.0, 0.9, 1.0),
        OlsExposureEvent("va-neg-2", "ADA", 2027, -5.0, 7.0, 3.5, 1.0, 1.1),
    )

    with pytest.raises(TypeError, match="residual_lower_bound"):
        evaluate_exposure_ols(  # type: ignore[call-arg]
            train, negative_validation, residual_lower_bound=1.0
        )
    evidence = evaluate_exposure_ols(train, negative_validation)

    assert evidence.terminal_state.decision is ScientificDecision.REJECTED
    assert evidence.residual_lower_bound is not None
    assert evidence.residual_lower_bound <= 0.0
    assert evidence.interval_method == "normal-95-validation-residual-mean-v1"
    evidence.verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(evidence, residual_lower_bound=1.0).verify_identity()


def test_admissibility_changing_evidence_contracts_are_identity_bound() -> None:
    perturbations = AdjacentLookbackPerturbationSet(
        candidate_id=CANDIDATE_ID,
        family="D",
        slot_id="D-primary-001",
        original_lookbacks={"level": 20},
        perturbations=(
            AdjacentLookbackPerturbation("level", 20, 19, "n_L-1", 0.01),
            AdjacentLookbackPerturbation("level", 20, 21, "n_L+1", 0.02),
        ),
    )
    lower_bounds = RobustnessLowerBounds(
        base=0.05,
        doubled_cost=0.03,
        one_bar_delay=0.02,
        ex_strongest_asset=0.01,
        ex_strongest_year=0.015,
        adjacent_lookback_perturbations=perturbations,
        outer_fold_point_estimates=(0.1, -0.01, 0.2, 0.03),
    )
    robustness = evaluate_robustness_gates(lower_bounds)
    regime = evaluate_regime_gate(
        (
            _regime("up_low_vol", (0.01, 0.02)),
            _regime("up_high_vol", (0.03, 0.01)),
        )
    )
    ols = evaluate_exposure_ols(_full_rank_ols_train(), _positive_ols_validation())
    promotion = evaluate_capacity_promotion(
        ScientificDecision.VALIDATED, CapacityEvidence(CANDIDATE_ID, 0.02, 1.5, SHA)
    )

    for contract in (lower_bounds, robustness, regime, ols.design, ols, promotion):
        contract.verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(lower_bounds, base=0.0).verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(robustness, base_positive=False).verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(regime, supported_positive_regimes=("up_low_vol",)).verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(ols.design, reference_asset="ETH").verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(promotion, status=PromotionEligibilityStatus.NOT_ELIGIBLE).verify_identity()


def test_promotion_decision_hierarchy_distinguishes_not_eligible_from_capacity_inconclusive() -> (
    None
):
    finite_capacity = CapacityEvidence(CANDIDATE_ID, 0.02, 1.5, SHA)

    for decision in (
        ScientificDecision.REJECTED,
        ScientificDecision.INCONCLUSIVE,
        ScientificDecision.NOT_EVALUATED,
    ):
        assert (
            evaluate_capacity_promotion(decision, None).status
            is PromotionEligibilityStatus.NOT_ELIGIBLE
        )
    assert (
        evaluate_capacity_promotion(ScientificDecision.SUPPORTED_DEVELOPMENT, None).status
        is PromotionEligibilityStatus.INCONCLUSIVE_CAPACITY
    )
    assert (
        evaluate_capacity_promotion(
            ScientificDecision.SUPPORTED_DEVELOPMENT, finite_capacity
        ).status
        is PromotionEligibilityStatus.NOT_ELIGIBLE
    )


def test_ols_rejects_duplicate_or_overlapping_row_ids_before_fit() -> None:
    train = _full_rank_ols_train()
    validation = _positive_ols_validation()

    duplicate_validation = (validation[0], replace(validation[1], row_id=validation[0].row_id))
    with pytest.raises(ValueError, match="duplicate validation row_id"):
        evaluate_exposure_ols(train, duplicate_validation)

    duplicate_train = (train[0], replace(train[1], row_id=train[0].row_id), *train[2:])
    with pytest.raises(ValueError, match="duplicate train row_id"):
        evaluate_exposure_ols(duplicate_train, validation)

    overlapping_validation = (replace(validation[0], row_id=train[0].row_id), validation[1])
    with pytest.raises(ValueError, match="disjoint"):
        evaluate_exposure_ols(train, overlapping_validation)


def test_regime_evidence_rejects_duplicate_utc_weeks_that_inflate_support() -> None:
    duplicated_week = BASE_TIME

    with pytest.raises(ValueError, match="duplicate UTC week"):
        RegimeReturnEvidence(
            "up_low_vol",
            (
                WeeklyRegimeObservation(duplicated_week, 0.01, SHA),
                WeeklyRegimeObservation(duplicated_week, 0.02, SHA),
            ),
        )


def _perturbations(
    family: str, parameter: str, original: int, lower_minus: float, lower_plus: float
) -> AdjacentLookbackPerturbationSet:
    return AdjacentLookbackPerturbationSet(
        candidate_id=CANDIDATE_ID,
        family=family,
        slot_id=f"{family}-primary-001",
        original_lookbacks={parameter: original},
        perturbations=(
            AdjacentLookbackPerturbation(parameter, original, original - 1, "n_L-1", lower_minus),
            AdjacentLookbackPerturbation(parameter, original, original + 1, "n_L+1", lower_plus),
        ),
    )


def _regime(regime: str, values: tuple[float, ...]) -> RegimeReturnEvidence:
    return RegimeReturnEvidence(
        regime,
        tuple(
            WeeklyRegimeObservation(BASE_TIME + timedelta(days=7 * index), value, SHA)
            for index, value in enumerate(values)
        ),
    )


def _full_rank_ols_train() -> tuple[OlsExposureEvent, ...]:
    return tuple(
        OlsExposureEvent(
            row_id=f"tr-{index}",
            asset=asset,
            year=year,
            target_return=1.0 + 0.5 * market + residual,
            market_return=market,
            market_trend=trend,
            volatility_rank=vol_rank,
            turnover_rank=turn_rank,
        )
        for index, (asset, year, market, trend, vol_rank, turn_rank, residual) in enumerate(
            (
                ("BTC", 2024, -2.0, -1.0, 0.1, 0.7, 0.00),
                ("ETH", 2024, -1.0, 0.7, 0.6, 0.3, 0.01),
                ("BTC", 2025, 0.0, -0.2, 0.3, 0.9, -0.01),
                ("ETH", 2025, 1.0, 1.1, 0.8, 0.5, 0.02),
                ("SOL", 2025, 2.0, -0.8, 0.5, 0.2, -0.02),
                ("SOL", 2024, 3.0, 0.4, 0.2, 0.8, 0.00),
                ("BTC", 2024, 4.0, 1.6, 0.9, 0.4, 0.01),
                ("ETH", 2025, 5.0, -1.4, 0.4, 0.6, -0.01),
            )
        )
    )


def _positive_ols_validation() -> tuple[OlsExposureEvent, ...]:
    return (
        OlsExposureEvent("va-1", "XRP", 2026, 4.50, 6.0, 3.0, 0.9, 1.0),
        OlsExposureEvent("va-2", "ADA", 2027, 5.00, 7.0, 3.5, 1.0, 1.1),
    )


def test_regime_gate_identity_binds_non_default_support_and_negative_threshold() -> None:
    evidence = evaluate_regime_gate(
        (
            _regime("up_low_vol", (0.01, 0.02, 0.03)),
            _regime("up_high_vol", (0.03, 0.02, 0.01)),
        ),
        minimum_weeks=3,
        negative_threshold=-0.01,
    )

    assert evidence.minimum_weeks == 3
    assert evidence.negative_threshold == pytest.approx(-0.01)
    evidence.verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(evidence, minimum_weeks=2).verify_identity()
    with pytest.raises(ValueError, match="identity"):
        replace(evidence, negative_threshold=0.0).verify_identity()


def test_target_excluded_market_features_require_three_unique_non_target_assets() -> None:
    rows = (
        MarketFeatureObservation("BTC", BASE_TIME, 0.99, 9.0, 5.0, 500.0),
        MarketFeatureObservation("ETH", BASE_TIME, 0.03, 0.20, 2.0, 200.0),
        MarketFeatureObservation("ETH", BASE_TIME, 0.04, 0.25, 3.0, 250.0),
        MarketFeatureObservation("SOL", BASE_TIME, 0.01, 0.10, 4.0, 400.0),
    )

    with pytest.raises(ValueError, match="duplicate asset"):
        target_excluded_market_features("BTC", BASE_TIME, rows)


def test_regime_positive_support_requires_mean_strictly_above_zero_not_threshold() -> None:
    evidence = evaluate_regime_gate(
        (
            _regime("up_low_vol", (-0.005, -0.005)),
            _regime("up_high_vol", (0.02, 0.03)),
        ),
        negative_threshold=-0.01,
    )

    assert evidence.terminal_state.decision is ScientificDecision.INCONCLUSIVE
    assert evidence.supported_positive_regimes == ("up_high_vol",)
    assert evidence.supported_negative_regimes == ()
    assert "exactly one" in evidence.reason
