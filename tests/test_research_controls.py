from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.controls import (
    ControlKind,
    ControlOpportunity,
    ControlSelection,
    ControlSelectionStatus,
    LegalBoundaryInterval,
    LegalDonorPoolEvidence,
    SelectorInput,
    SelectorEvidence,
    build_common_population_evidence,
    build_naive_zero_controls,
    build_selector_evidence,
    build_persistence_controls,
    build_legal_donor_pool_evidence,
    match_prior_week_pseudo_level_controls,
    persistence_direction,
    random_feature_selection,
    select_stratified_controls,
    shift_opportunities_one_utc_week,
    shuffle_labels_within_legal_strata,
)
from market_structure_lab.research.models import ValidationWorkBudget

SHA = "a" * 64
PROGRAMME = f"VP-{'1' * 64}"
START = datetime(2024, 1, 1, tzinfo=UTC)
WEEK = datetime(2024, 1, 1, tzinfo=UTC)


class OversizedControlPool:
    def __len__(self) -> int:
        return 385

    def __getitem__(self, index: int) -> object:
        raise AssertionError("control pool was iterated before the budget gate")


def _opp(
    suffix: str,
    *,
    candidate_id: str = "HC-" + "1" * 64,
    family: str = "A",
    control_role: str = "candidate_primary",
    symbol: str = "SOLUSDT",
    timeframe: str = "1h",
    fold_id: str = "outer-1",
    week: datetime = WEEK,
    direction: int = 1,
    horizon: int = 24,
    segment: int = 7,
    feature_time: datetime = START,
    entry_offset_hours: int = 1,
    gross_return: float = 0.01,
    cost_return: float = -0.001,
    prior_return: float | None = 0.02,
    lookback_hours: int = 24,
    level_reference: str = "donchian-24h",
    programme_id: str = PROGRAMME,
    publication_sha256: str = SHA,
    component: str = "development",
    block_id: str = "block-1",
) -> ControlOpportunity:
    signal_time = feature_time + timedelta(hours=entry_offset_hours)
    label_end = signal_time + timedelta(hours=horizon)
    return ControlOpportunity(
        row_identity=f"ROW-{suffix}",
        event_id=f"EV-{suffix}",
        candidate_id=candidate_id,
        family=family,
        control_role=control_role,
        symbol=symbol,
        timeframe=timeframe,
        fold_id=fold_id,
        utc_week_start=week,
        direction=direction,
        horizon_hours=horizon,
        segment_id=segment,
        feature_time=feature_time,
        signal_time=signal_time,
        legal_entry_time=signal_time,
        label_start=signal_time,
        label_end=label_end,
        programme_id=programme_id,
        publication_sha256=publication_sha256,
        component=component,
        block_id=block_id,
        gross_return=gross_return,
        cost_return=cost_return,
        prior_completed_close_return=prior_return,
        lookback_hours=lookback_hours,
        level_reference=level_reference,
    )


def _boundary(
    row: ControlOpportunity,
    *,
    start: datetime = START,
    end: datetime = START + timedelta(days=14),
    fold_id: str | None = None,
    component: str | None = None,
    block_id: str | None = None,
    segment_id: int | None = None,
) -> LegalBoundaryInterval:
    return LegalBoundaryInterval(
        programme_id=row.programme_id,
        publication_sha256=row.publication_sha256,
        symbol=row.symbol,
        timeframe=row.timeframe,
        segment_id=row.segment_id if segment_id is None else segment_id,
        fold_id=row.fold_id if fold_id is None else fold_id,
        component=row.component if component is None else component,
        block_id=row.block_id if block_id is None else block_id,
        interval_start=start,
        interval_end=end,
    )


def _selector_inputs(rows: tuple[ControlOpportunity, ...]) -> tuple[SelectorInput, ...]:
    return tuple(
        SelectorInput(
            row_identity=row.row_identity,
            source_publication_sha256=row.publication_sha256,
            feature_time=row.feature_time,
        )
        for row in rows
    )


def test_naive_zero_controls_match_identical_opportunities_and_reject_tamper() -> None:
    opportunities = (_opp("001", gross_return=0.08), _opp("002", gross_return=-0.03))

    selection = build_naive_zero_controls(opportunities, budget=ValidationWorkBudget())

    assert selection.status is ControlSelectionStatus.COMPLETE
    assert selection.kind is ControlKind.NAIVE_ZERO
    assert len(selection.records) == 2
    assert {record.control_return for record in selection.records} == {0.0}
    assert {record.control_cost for record in selection.records} == {0.0}
    assert all(
        record.candidate_row_identity == record.control_row_identity for record in selection.records
    )
    assert ControlSelection.from_dict(selection.to_dict()) == selection

    tampered = selection.to_dict()
    tampered_records = cast(list[dict[str, object]], tampered["records"])
    tampered["records"] = [
        {**tampered_records[0], "control_return": 0.01},
        *tampered_records[1:],
    ]
    with pytest.raises(ValueError, match="sha256"):
        ControlSelection.from_dict({**tampered, "sha256": selection.sha256})


def test_unconditional_controls_are_exact_stratified_hash_ordered_and_count_shortage_is_explicit() -> (
    None
):
    candidate = _opp("C01")
    pool = (
        _opp("C01", control_role="unconditional"),  # overlapping row, ineligible
        _opp(
            "SAME-CLOCK",
            control_role="unconditional",
        ),  # distinct identity at the same legal opportunity, still ineligible
        _opp("U01", control_role="unconditional", entry_offset_hours=2, gross_return=0.02),
        _opp("U02", control_role="unconditional", entry_offset_hours=3, gross_return=0.03),
        _opp(
            "BAD",
            control_role="unconditional",
            symbol="ADAUSDT",
            entry_offset_hours=2,
        ),
    )
    complete = select_stratified_controls(
        (candidate,),
        pool,
        kind=ControlKind.UNCONDITIONAL,
        required_control_role="unconditional",
        budget=ValidationWorkBudget(),
    )

    expected = min(
        (pool[2], pool[3]),
        key=lambda row: hash_json(
            "unconditional-control-order-v1",
            {"candidate_id": candidate.candidate_id, "row_identity": row.row_identity},
        ),
    )
    assert complete.status is ControlSelectionStatus.COMPLETE
    assert complete.records[0].control_row_identity == expected.row_identity
    assert complete.records[0].control_return == expected.gross_return
    assert complete.records[0].control_row_identity != candidate.row_identity
    assert complete.records[0].control_row_identity != pool[1].row_identity

    same_clock_only = select_stratified_controls(
        (candidate,),
        pool[1:2],
        kind=ControlKind.UNCONDITIONAL,
        required_control_role="unconditional",
        budget=ValidationWorkBudget(),
    )
    assert same_clock_only.status is ControlSelectionStatus.INCONCLUSIVE
    assert same_clock_only.shortage_count == 1

    shortage = select_stratified_controls(
        (candidate, replace(candidate, row_identity="ROW-C02", event_id="EV-C02")),
        pool[2:3],
        kind=ControlKind.UNCONDITIONAL,
        required_control_role="unconditional",
        budget=ValidationWorkBudget(),
    )
    assert shortage.status is ControlSelectionStatus.INCONCLUSIVE
    assert shortage.shortage_count == 1
    assert "shortage" in shortage.reason


def test_persistence_direction_is_strict_prior_completed_bar_sign() -> None:
    assert persistence_direction(_opp("P1", prior_return=0.001)) == 1
    assert persistence_direction(_opp("P2", prior_return=-0.001)) == -1
    assert persistence_direction(_opp("P3", prior_return=0.0)) is None
    assert persistence_direction(_opp("P4", prior_return=None)) is None


def test_common_population_evidence_keeps_zero_for_unselected_and_placebo_is_domain_tagged() -> (
    None
):
    rows = (
        _opp("B1", family="B", gross_return=0.10, cost_return=-0.01),
        _opp("B2", family="B", gross_return=0.20, cost_return=-0.02),
        _opp("B3", family="B", gross_return=-0.05, cost_return=-0.01),
        _opp("B4", family="B", gross_return=0.03, cost_return=-0.01),
    )

    selector_evidence = build_selector_evidence(
        _selector_inputs(rows),
        selected_row_identities=("ROW-B1", "ROW-B3"),
        source_publication_sha256=SHA,
        selector_id="selector:family-b:v1",
        selector_cutoff=START + timedelta(hours=1),
    )

    evidence = build_common_population_evidence(
        rows,
        selector_evidence=selector_evidence,
        baseline_role="price_baseline",
        augmented_role="combined_primary",
        placebo_role="rate_matched_placebo",
        seed="common-population-test",
        budget=ValidationWorkBudget(),
    )

    assert tuple(record.control_return for record in evidence.baseline.records) == (
        0.10,
        0.20,
        -0.05,
        0.03,
    )
    augmented = {record.candidate_row_identity: record for record in evidence.augmented.records}
    assert augmented["ROW-B1"].control_return == 0.10
    assert augmented["ROW-B3"].control_return == -0.05
    assert augmented["ROW-B2"].control_return == 0.0
    assert augmented["ROW-B2"].control_cost == 0.0
    assert evidence.selected_count == 2
    assert evidence.complement_count == 2
    assert evidence.selector_evidence_sha256 == selector_evidence.selector_sha256
    assert evidence.selector_cutoff == START + timedelta(hours=1)
    assert evidence.complement_row_identities == ("ROW-B2", "ROW-B4")
    assert len(evidence.placebo.records) == 2
    assert evidence.placebo.kind is ControlKind.RATE_MATCHED_PLACEBO
    assert all(
        record.control_family == "placebo:rate_matched_placebo"
        for record in evidence.placebo.records
    )
    assert {record.control_row_identity for record in evidence.placebo.records}.isdisjoint(
        {"ROW-B1", "ROW-B3"}
    )

    oracle_mutated = tuple(
        replace(row, gross_return=99.0 if row.row_identity == "ROW-B2" else row.gross_return)
        for row in rows
    )
    mutated_evidence = build_common_population_evidence(
        oracle_mutated,
        selector_evidence=selector_evidence,
        baseline_role="price_baseline",
        augmented_role="combined_primary",
        placebo_role="rate_matched_placebo",
        seed="common-population-test",
        budget=ValidationWorkBudget(),
    )
    assert tuple(
        record.candidate_row_identity
        for record in mutated_evidence.augmented.records
        if record.control_return
    ) == (
        "ROW-B1",
        "ROW-B3",
    )


def test_selector_evidence_is_canonical_sealed_and_common_population_rejects_attestation() -> None:
    rows = (
        _opp("S1", family="B", gross_return=0.30),
        _opp("S2", family="B", gross_return=-0.10),
        _opp("S3", family="B", gross_return=0.20),
    )

    evidence = build_selector_evidence(
        _selector_inputs(rows),
        selected_row_identities=("ROW-S2", "ROW-S1"),
        source_publication_sha256=SHA,
        selector_id="selector:ordered:v1",
        selector_cutoff=START + timedelta(hours=1),
    )

    assert isinstance(evidence, SelectorEvidence)
    assert evidence.selected_row_identities == ("ROW-S2", "ROW-S1")
    assert "gross_return" not in evidence.to_dict()
    assert "cost_return" not in evidence.to_dict()
    assert SelectorEvidence.from_dict(evidence.to_dict()) == evidence
    with pytest.raises(TypeError, match="builder"):
        SelectorEvidence(
            selected_row_identities=("ROW-S1",),
            source_publication_sha256=SHA,
            selector_id="selector:self-attested",
            selector_cutoff=START + timedelta(hours=1),
            source_population_sha256=evidence.source_population_sha256,
        )
    with pytest.raises(TypeError, match="SelectorInput"):
        build_selector_evidence(
            cast(tuple[SelectorInput, ...], rows),
            selected_row_identities=("ROW-S1", "ROW-S3", "ROW-S2"),
            source_publication_sha256=SHA,
            selector_id="selector:ordered:v1",
            selector_cutoff=START + timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="selector evidence"):
        build_common_population_evidence(
            rows,
            selector_evidence=evidence,
            selected_row_identities=("ROW-S1", "ROW-S2"),
            baseline_role="price_baseline",
            augmented_role="combined_primary",
            placebo_role="rate_matched_placebo",
            seed="common-population-test",
            budget=ValidationWorkBudget(),
        )


def test_named_controls_and_prior_week_pseudo_levels_require_exact_causal_strata() -> None:
    candidate = _opp(
        "D0", family="D", feature_time=START + timedelta(days=14), week=WEEK + timedelta(days=14)
    )
    donchian = _opp(
        "FD",
        family="D",
        control_role="failed_donchian_control",
        gross_return=0.04,
        week=WEEK + timedelta(days=14),
    )
    atr = _opp(
        "ATR",
        family="G",
        control_role="atr_only_control",
        gross_return=0.07,
        week=WEEK + timedelta(days=14),
    )

    named = select_stratified_controls(
        (candidate,),
        (donchian, atr),
        kind=ControlKind.FAILED_DONCHIAN,
        required_control_role="failed_donchian_control",
        budget=ValidationWorkBudget(),
    )
    assert named.status is ControlSelectionStatus.COMPLETE
    assert named.records[0].control_row_identity == donchian.row_identity

    prior_week = match_prior_week_pseudo_level_controls(
        (candidate,),
        (
            _opp(
                "PW",
                family="D",
                control_role="prior_week_pseudo_level",
                feature_time=candidate.feature_time - timedelta(days=7),
                week=WEEK + timedelta(days=7),
            ),
            _opp(
                "BADSEG",
                family="D",
                control_role="prior_week_pseudo_level",
                feature_time=candidate.feature_time - timedelta(days=7),
                week=WEEK + timedelta(days=7),
                segment=8,
            ),
        ),
        budget=ValidationWorkBudget(),
    )
    assert prior_week.status is ControlSelectionStatus.COMPLETE
    assert prior_week.records[0].control_row_identity == "ROW-PW"

    missing = match_prior_week_pseudo_level_controls(
        (candidate,),
        (
            _opp(
                "BADSEG",
                family="D",
                control_role="prior_week_pseudo_level",
                feature_time=candidate.feature_time - timedelta(days=7),
                week=WEEK + timedelta(days=7),
                segment=8,
            ),
        ),
        budget=ValidationWorkBudget(),
    )
    assert missing.status is ControlSelectionStatus.INCONCLUSIVE
    assert "prior-week" in missing.reason


def test_negative_controls_use_separate_donors_and_never_copy_time_shift_outcomes() -> None:
    candidates = (
        _opp("N1", direction=1, gross_return=0.11),
        _opp("N2", direction=-1, gross_return=-0.03),
        _opp("N3", direction=1, gross_return=0.04),
    )
    donors = (
        _opp("D1", direction=1, gross_return=-0.50),
        _opp("D2", direction=-1, gross_return=0.70),
        _opp("D3", direction=1, gross_return=0.90),
    )

    donor_evidence = build_legal_donor_pool_evidence(
        donors, source_publication_sha256=SHA, pool_identity="shuffle-donors:v1"
    )
    assert sorted(count for _, count in donor_evidence.per_stratum_counts) == [1, 2]
    shuffled = shuffle_labels_within_legal_strata(
        candidates,
        donors,
        donor_pool_evidence=donor_evidence,
        seed="shuffle-test",
        budget=ValidationWorkBudget(),
    )
    assert shuffled.status is ControlSelectionStatus.COMPLETE
    assert {record.event_id for record in shuffled.records} == {row.event_id for row in candidates}
    assert {record.label_source_row_identity for record in shuffled.records} <= {
        row.row_identity for row in donors
    }
    assert tuple(record.gross_return for record in shuffled.records) == tuple(
        record.gross_return
        for record in shuffle_labels_within_legal_strata(
            candidates,
            donors,
            donor_pool_evidence=donor_evidence,
            seed="shuffle-test",
            budget=ValidationWorkBudget(),
        ).records
    )

    singleton = shuffle_labels_within_legal_strata(
        (candidates[0],),
        (),
        donor_pool_evidence=build_legal_donor_pool_evidence(
            (), source_publication_sha256=SHA, pool_identity="empty-donors:v1"
        ),
        seed="shuffle-test",
        budget=ValidationWorkBudget(),
    )
    assert singleton.status is ControlSelectionStatus.INCONCLUSIVE
    assert singleton.shortage_count == 1

    shifted = shift_opportunities_one_utc_week(
        candidates,
        legal_boundaries=tuple(_boundary(row) for row in candidates),
        budget=ValidationWorkBudget(),
    )
    assert shifted.status is ControlSelectionStatus.COMPLETE
    assert all(
        item.shifted_feature_time == item.source_feature_time + timedelta(days=7)
        for item in shifted.records
    )
    assert not hasattr(shifted.records[0], "gross_return")
    assert not hasattr(shifted.records[0], "cost_return")

    selected = random_feature_selection(
        candidates,
        donors,
        seed="feature-seed",
        direction_seed="direction-seed",
        budget=ValidationWorkBudget(),
    )
    assert selected.status is ControlSelectionStatus.COMPLETE
    assert len(selected.records) == len(candidates)
    assert sum(1 for record in selected.records if record.control_direction == 1) == 2
    assert sum(1 for record in selected.records if record.control_direction == -1) == 1
    assert {record.control_row_identity for record in selected.records} <= {
        row.row_identity for row in donors
    }

    assert isinstance(donor_evidence, LegalDonorPoolEvidence)
    assert donor_evidence.total_count == len(donors)
    subset_evidence = build_legal_donor_pool_evidence(
        donors[:2], source_publication_sha256=SHA, pool_identity="shuffle-donors:v1"
    )
    biased_subset = shuffle_labels_within_legal_strata(
        candidates,
        donors,
        donor_pool_evidence=subset_evidence,
        seed="shuffle-test",
        budget=ValidationWorkBudget(),
    )
    assert biased_subset.status is ControlSelectionStatus.INCONCLUSIVE
    assert "donor pool evidence" in biased_subset.reason


def test_time_shift_requires_explicit_legal_boundaries_and_rejects_all_crossings() -> None:
    row = _opp("TS", feature_time=START + timedelta(days=1))

    missing = shift_opportunities_one_utc_week(
        (row,), legal_boundaries=(), budget=ValidationWorkBudget()
    )
    assert missing.status is ControlSelectionStatus.INCONCLUSIVE
    assert "legal boundary" in missing.reason

    complete = shift_opportunities_one_utc_week(
        (row,), legal_boundaries=(_boundary(row),), budget=ValidationWorkBudget()
    )
    assert complete.status is ControlSelectionStatus.COMPLETE
    assert complete.records[0].legal_boundary_sha256 == _boundary(row).boundary_sha256
    assert complete.records[0].boundary_interval_start == START
    assert complete.records[0].boundary_interval_end == START + timedelta(days=14)

    duplicate = shift_opportunities_one_utc_week(
        (row,), legal_boundaries=(_boundary(row), _boundary(row)), budget=ValidationWorkBudget()
    )
    assert duplicate.status is ControlSelectionStatus.COMPLETE

    with pytest.raises(ValueError, match="conflicting legal boundary"):
        shift_opportunities_one_utc_week(
            (row,),
            legal_boundaries=(
                _boundary(row),
                _boundary(row, end=START + timedelta(days=15)),
            ),
            budget=ValidationWorkBudget(),
        )

    crossing_cases = (
        _boundary(row, fold_id="inner-1"),
        _boundary(row, component="final_asset"),
        _boundary(row, segment_id=8),
        _boundary(row, block_id="block-2"),
        _boundary(row, end=row.label_end + timedelta(days=7) - timedelta(minutes=1)),
    )
    for boundary in crossing_cases:
        shifted = shift_opportunities_one_utc_week(
            (row,), legal_boundaries=(boundary,), budget=ValidationWorkBudget()
        )
        assert shifted.status is ControlSelectionStatus.INCONCLUSIVE
        assert shifted.shortage_count == 1


def test_random_feature_direction_seed_changes_assignment_order_not_stratum_counts() -> None:
    candidates = (
        _opp("RL1", direction=1),
        _opp("RL2", direction=1),
        _opp("RS1", direction=-1),
        _opp("RS2", direction=-1),
    )
    donors = tuple(_opp(f"RD{index}", direction=1) for index in range(8))

    first = random_feature_selection(
        candidates,
        donors,
        seed="same-feature-seed",
        direction_seed="direction-seed-one",
        budget=ValidationWorkBudget(),
    )
    second = random_feature_selection(
        candidates,
        donors,
        seed="same-feature-seed",
        direction_seed="direction-seed-two",
        budget=ValidationWorkBudget(),
    )

    assert first.status is ControlSelectionStatus.COMPLETE
    assert second.status is ControlSelectionStatus.COMPLETE
    assert tuple(record.control_direction for record in first.records).count(1) == 2
    assert tuple(record.control_direction for record in first.records).count(-1) == 2
    assert tuple(record.control_direction for record in second.records).count(1) == 2
    assert tuple(record.control_direction for record in second.records).count(-1) == 2
    assert {record.control_row_identity for record in first.records} == {
        record.control_row_identity for record in second.records
    }
    assert tuple(
        (record.control_row_identity, record.control_direction) for record in first.records
    ) != tuple((record.control_row_identity, record.control_direction) for record in second.records)


def test_persistence_controls_match_exact_strata_and_shortage_explicit() -> None:
    candidates = (_opp("PA", prior_return=0.02), _opp("PB", prior_return=-0.02, direction=-1))
    pool = (
        _opp(
            "PL",
            control_role="persistence",
            direction=1,
            entry_offset_hours=2,
            gross_return=0.05,
        ),
        _opp(
            "PS",
            control_role="persistence",
            direction=-1,
            entry_offset_hours=2,
            gross_return=-0.05,
        ),
    )

    complete = build_persistence_controls(candidates, pool, budget=ValidationWorkBudget())
    assert complete.status is ControlSelectionStatus.COMPLETE
    assert tuple(record.control_direction for record in complete.records) == (1, -1)

    same_clock = build_persistence_controls(
        candidates[:1],
        (_opp("P-SAME", control_role="persistence", direction=1),),
        budget=ValidationWorkBudget(),
    )
    assert same_clock.status is ControlSelectionStatus.INCONCLUSIVE
    assert same_clock.shortage_count == 1

    shortage = build_persistence_controls(candidates, pool[:1], budget=ValidationWorkBudget())
    assert shortage.status is ControlSelectionStatus.INCONCLUSIVE
    assert shortage.shortage_count == 1


def test_non_final_boundary_crossings_are_inconclusive_not_silent() -> None:
    final_row = _opp("FINAL", component="final_temporal")

    shifted = shift_opportunities_one_utc_week(
        (final_row,),
        legal_boundaries=(_boundary(final_row, component="final_temporal"),),
        budget=ValidationWorkBudget(),
    )
    assert shifted.status is ControlSelectionStatus.INCONCLUSIVE
    assert shifted.shortage_count == 1

    incompatible_candidate = _opp(
        "D0X", family="D", feature_time=START + timedelta(days=14), week=WEEK + timedelta(days=14)
    )
    incompatible_pool = (
        _opp(
            "PWX",
            family="D",
            control_role="prior_week_pseudo_level",
            feature_time=incompatible_candidate.feature_time - timedelta(days=7),
            week=WEEK + timedelta(days=7),
            publication_sha256="c" * 64,
        ),
    )
    prior_week = match_prior_week_pseudo_level_controls(
        (incompatible_candidate,), incompatible_pool, budget=ValidationWorkBudget()
    )
    assert prior_week.status is ControlSelectionStatus.INCONCLUSIVE
    assert prior_week.shortage_count == 1


def test_invalid_inputs_and_work_budget_fail_closed_before_deferred_iteration() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        _opp("TZ", feature_time=datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="finite"):
        _opp("NAN", gross_return=float("nan"))
    with pytest.raises(ValueError, match="duplicate"):
        build_naive_zero_controls((_opp("DUP"), _opp("DUP")), budget=ValidationWorkBudget())
    with pytest.raises(ValueError, match="control work limit"):
        select_stratified_controls(
            (_opp("C"),),
            OversizedControlPool(),  # type: ignore[arg-type]
            kind=ControlKind.UNCONDITIONAL,
            required_control_role="unconditional",
            budget=ValidationWorkBudget(),
        )
